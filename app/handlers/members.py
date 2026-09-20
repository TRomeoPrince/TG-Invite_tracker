from telegram import Update
from telegram.ext import ContextTypes

from app.database import SessionLocal
from app.models import ReferralMethod
from app.services.referrals import (
    get_link_owner,
    mark_left,
    record_referral,
    upsert_group,
    upsert_user,
)

JOINED_STATES = {"member", "administrator", "creator", "restricted"}
LEFT_STATES = {"left", "kicked"}


async def track_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            inviter_id = await get_link_owner(session, chat.id, change.invite_link.invite_link)
            if inviter_id is not None:
                method = ReferralMethod.INVITE_LINK

        if inviter_id is None and actor and actor.id != member.id and not actor.is_bot:
            inviter_id = actor.id
            method = ReferralMethod.DIRECT_ADD

        if inviter_id is None or inviter_id == member.id:
            await session.commit()
            return

        _, credited = await record_referral(
            session,
            group_id=chat.id,
            invitee_id=member.id,
            inviter_id=inviter_id,
            method=method,
        )
        await session.commit()

    if credited:
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text="✅ Invite recorded.",
                disable_notification=True,
            )
        except Exception:
            pass
