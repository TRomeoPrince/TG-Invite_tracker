import asyncio

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database import SessionLocal
from app.models import Referral, ReferralMethod, User
from app.services.referrals import (
    get_link_owner,
    mark_left,
    record_referral,
    upsert_group,
    upsert_user,
)

JOINED_STATES = {"member", "administrator", "creator", "restricted"}
LEFT_STATES = {"left", "kicked"}
CONFIRMATION_DELETE_SECONDS = 10


async def _delete_later(bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(CONFIRMATION_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # The message may already have been deleted or the bot may no longer
        # have permission to delete messages. Neither case should crash tracking.
        pass


async def track_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Store authoritative membership/referral information from chat_member updates.

    User-facing confirmations are intentionally handled separately by
    announce_join_event(), because Telegram's service-message update gives us
    the exact message_id to reply to.
    """
    change = update.chat_member
    if change is None:
        return

    old_status = change.old_chat_member.status
    new_status = change.new_chat_member.status
    member = change.new_chat_member.user
    actor = change.from_user
    chat = change.chat

    if member.is_bot:
        return

    joined = old_status in LEFT_STATES and new_status in JOINED_STATES
    left = old_status in JOINED_STATES and new_status in LEFT_STATES

    async with SessionLocal() as session:
        await upsert_group(session, chat)
        await upsert_user(session, member)

        if actor:
            await upsert_user(session, actor)

        if left:
            await mark_left(session, chat.id, member.id)
            await session.commit()
            return

        if not joined:
            await session.commit()
            return

        inviter_id = None
        method = None

        if change.invite_link:
            inviter_id = await get_link_owner(
                session,
                chat.id,
                change.invite_link.invite_link,
            )
            if inviter_id is not None:
                method = ReferralMethod.INVITE_LINK

        if inviter_id is None and actor and actor.id != member.id and not actor.is_bot:
            inviter_id = actor.id
            method = ReferralMethod.DIRECT_ADD

        if inviter_id is None or inviter_id == member.id:
            await session.commit()
            return

        await record_referral(
            session,
            group_id=chat.id,
            invitee_id=member.id,
            inviter_id=inviter_id,
            method=method,
        )
        await session.commit()


async def announce_join_event(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Reply directly to Telegram's native:
      - "X joined the group via invite link"
      - "X added Y"
    service message.

    Telegram bots do not receive per-user read receipts in group chats, so the
    confirmation cannot be deleted at the exact moment the invitee sees it.
    We delete it automatically after a short configurable delay instead.
    """
    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None or not message.new_chat_members:
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue

        # The chat_member update and the service-message update are separate.
        # Give the referral writer a moment to commit, then retry briefly in
        # case Telegram delivers the service message first.
        referral = None
        inviter = None

        for attempt in range(4):
            if attempt:
                await asyncio.sleep(0.5)

            async with SessionLocal() as session:
                referral = await session.scalar(
                    select(Referral).where(
                        Referral.group_id == chat.id,
                        Referral.invitee_id == member.id,
                    )
                )

                if referral:
                    inviter = await session.get(User, referral.inviter_id)
                    break

        if not referral:
            # Normal joins that were not attributable to a tracked personal
            # link or a direct add should not produce an "invite recorded"
            # confirmation.
            continue

        if inviter and inviter.username:
            inviter_name = f"@{inviter.username}"
        elif inviter and inviter.first_name:
            inviter_name = inviter.first_name
        else:
            inviter_name = str(referral.inviter_id)

        if referral.method == ReferralMethod.INVITE_LINK:
            text = f"✅ Invite recorded for {inviter_name} via personal link."
        else:
            text = f"✅ Direct add recorded for {inviter_name}."

        try:
            confirmation = await message.reply_text(
                text,
                disable_notification=True,
            )
            asyncio.create_task(
                _delete_later(
                    context.bot,
                    chat.id,
                    confirmation.message_id,
                )
            )
        except Exception:
            pass


async def track_bot_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Register the actual chat (including broadcast channels) when this bot is
    added/promoted there.

    This is separate from CHAT_MEMBER updates, which describe other users.
    Without MY_CHAT_MEMBER handling, a linked discussion group may be known to
    the dashboard while its parent channel is not.
    """
    change = update.my_chat_member
    if change is None:
        return

    chat = change.chat
    new_status = change.new_chat_member.status

    if new_status in LEFT_STATES:
        return

    async with SessionLocal() as session:
        await upsert_group(session, chat)
        if change.from_user:
            await upsert_user(session, change.from_user)
        await session.commit()
