from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.database import SessionLocal
from app.services.referrals import (
    get_personal_link,
    leaderboard as get_leaderboard,
    save_personal_link,
    upsert_group,
    upsert_user,
    user_stats,
)


def is_group(update: Update) -> bool:
    return bool(
        update.effective_chat
        and update.effective_chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 TG Invite Tracker is running.\n\n"
        "Commands:\n"
        "/link - create/get your personal invite link\n"
        "/myinvites - view your invite stats\n"
        "/leaderboard - active referrals\n"
        "/leaderboard all - all-time referrals"
    )
    await update.effective_message.reply_text(text)


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_group(update):
        await update.effective_message.reply_text("Use /link inside the group you want to track.")
        return

    chat = update.effective_chat
    user = update.effective_user

    async with SessionLocal() as session:
        await upsert_user(session, user)
        await upsert_group(session, chat)

        existing = await get_personal_link(session, chat.id, user.id)
        if existing:
            await session.commit()
            await update.effective_message.reply_text(
                f"🔗 Your personal invite link:\n{existing.invite_link}"
            )
            return

        invite = await context.bot.create_chat_invite_link(
            chat_id=chat.id,
            name=f"ref:{user.id}",
        )
        await save_personal_link(session, chat.id, user.id, invite.invite_link)
        await session.commit()

    await update.effective_message.reply_text(
        f"🔗 Your personal invite link:\n{invite.invite_link}\n\n"
        "Anyone joining through this link will be credited to you."
    )


async def myinvites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_group(update):
        await update.effective_message.reply_text("Use /myinvites inside the tracked group.")
        return

    async with SessionLocal() as session:
        stats = await user_stats(session, update.effective_chat.id, update.effective_user.id)

    await update.effective_message.reply_text(
        "📊 Your invite stats\n\n"
        f"Total: {stats['total']}\n"
        f"✅ Active: {stats['active']}\n"
        f"❌ Left: {stats['left']}\n"
        f"➕ Direct adds: {stats['direct']}\n"
        f"🔗 Invite-link joins: {stats['link']}"
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_group(update):
        await update.effective_message.reply_text("Use /leaderboard inside the tracked group.")
        return

    active_only = not (context.args and context.args[0].lower() == "all")

    async with SessionLocal() as session:
        rows = await get_leaderboard(session, update.effective_chat.id, active_only)
        if not rows:
            await update.effective_message.reply_text("No referrals recorded yet.")
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for index, (user_id, count) in enumerate(rows, start=1):
            user = await session.get(__import__("app.models", fromlist=["User"]).User, user_id)
            if user:
                display = f"@{user.username}" if user.username else (user.first_name or str(user_id))
            else:
                display = str(user_id)

            prefix = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{prefix} {display} — {count}")

    title = "🏆 Active Invite Leaderboard" if active_only else "🏆 All-Time Invite Leaderboard"
    await update.effective_message.reply_text(title + "\n\n" + "\n".join(lines))
