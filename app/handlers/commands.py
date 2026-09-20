from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from app.database import SessionLocal
from app.models import Group, User
from app.services.referrals import (
    all_tracked_chats,
    chat_stats,
    get_personal_link,
    leaderboard as get_leaderboard,
    referral_invitees,
    save_personal_link,
    upsert_group,
    upsert_user,
    user_stats,
)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}
ACTIVE_MEMBER_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}
ADMIN_STATUSES = {
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}


def _home_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📂 My Chats", callback_data="mychats"),
                InlineKeyboardButton("🛡 Admin Stats", callback_data="adminchats"),
            ],
            [
                InlineKeyboardButton(
                    "➕ Add to Group",
                    url=f"https://t.me/{bot_username}?startgroup=setup&admin=invite_users",
                ),
                InlineKeyboardButton(
                    "📢 Add to Channel",
                    url=f"https://t.me/{bot_username}?startchannel&admin=invite_users",
                ),
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data="help"),
            ],
        ]
    )


def _chat_keyboard(chat_id: int, *, include_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📊 My Stats", callback_data=f"mystats:{chat_id}"),
            InlineKeyboardButton("🔗 My Invite Link", callback_data=f"link:{chat_id}"),
        ],
        [
            InlineKeyboardButton("👥 My Invitees", callback_data=f"invitees:{chat_id}"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data=f"leader:{chat_id}:active"),
        ],
    ]
    if include_admin:
        rows.append([InlineKeyboardButton("🛡 Overall Admin Stats", callback_data=f"adminstats:{chat_id}")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def _stats_text(title: str, stats: dict[str, int], *, overall: bool = False) -> str:
    lines = [
        title,
        "",
        f"👥 Total: {stats['total']}",
        f"✅ Active: {stats['active']}",
        f"❌ Left: {stats['left']}",
        f"➕ Direct adds: {stats['direct']}",
        f"🔗 Invite-link joins: {stats['link']}",
    ]
    if overall:
        lines.append(f"🙋 Inviters: {stats['inviters']}")
    return "\n".join(lines)


async def _is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except (BadRequest, Forbidden):
        return False


async def _is_member(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ACTIVE_MEMBER_STATUSES
    except (BadRequest, Forbidden):
        return False


async def _discover_chats(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    *,
    admins_only: bool,
) -> list[Group]:
    async with SessionLocal() as session:
        chats = await all_tracked_chats(session)

    visible: list[Group] = []
    for chat in chats[:100]:
        try:
            member = await context.bot.get_chat_member(chat.group_id, user_id)
        except (BadRequest, Forbidden):
            continue

        if admins_only:
            if member.status in ADMIN_STATUSES:
                visible.append(chat)
        elif member.status in ACTIVE_MEMBER_STATUSES:
            visible.append(chat)

    return visible


async def _chat_title(chat_id: int) -> str:
    async with SessionLocal() as session:
        row = await session.get(Group, chat_id)
        return row.title if row and row.title else str(chat_id)


async def _send_or_edit(update: Update, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=text, reply_markup=keyboard)
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise
    elif update.effective_message:
        await update.effective_message.reply_text(text=text, reply_markup=keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    me = await context.bot.get_me()

    if chat and chat.type == ChatType.PRIVATE:
        if user:
            async with SessionLocal() as session:
                await upsert_user(session, user)
                await session.commit()
        await _send_or_edit(
            update,
            "🤖 TG Invite Tracker\n\n"
            "Track direct adds and personal invite links across your Telegram groups and channels.\n\n"
            "Choose what you want to do:",
            _home_keyboard(me.username),
        )
        return

    if not chat or chat.type not in GROUP_TYPES:
        return

    if user:
        async with SessionLocal() as session:
            await upsert_user(session, user)
            await upsert_group(session, chat)
            await session.commit()

        admin = await _is_admin(context, chat.id, user.id)
        await _send_or_edit(
            update,
            f"🤖 Invite Tracker — {chat.title or 'this chat'}\n\nChoose an option:",
            _chat_keyboard(chat.id, include_admin=admin),
        )
    else:
        await _send_or_edit(
            update,
            "🤖 Invite Tracker is active here.\n\nUse /stats for overall tracking statistics.",
        )


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP} or not user:
        await _send_or_edit(
            update,
            "🔗 Open the bot privately, choose **My Chats**, select a group/channel, then tap **My Invite Link**.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="home")]])
            if update.callback_query
            else None,
        )
        return

    await _show_link(update, context, chat.id, user.id)


async def _show_link(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    if not await _is_member(context, chat_id, user_id):
        await _send_or_edit(update, "You are not currently a member of that tracked chat.")
        return

    async with SessionLocal() as session:
        existing = await get_personal_link(session, chat_id, user_id)
        if existing:
            invite_link = existing.invite_link
        else:
            invite = await context.bot.create_chat_invite_link(
                chat_id=chat_id,
                name=f"ref:{user_id}",
            )
            await save_personal_link(session, chat_id, user_id, invite.invite_link)
            await session.commit()
            invite_link = invite.invite_link

    title = await _chat_title(chat_id)
    admin = await _is_admin(context, chat_id, user_id)
    await _send_or_edit(
        update,
        f"🔗 Your personal invite link\n\n📍 {title}\n{invite_link}\n\n"
        "Anyone joining through this link is credited to your Telegram user ID.",
        _chat_keyboard(chat_id, include_admin=admin),
    )


async def myinvites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in GROUP_TYPES or not user:
        await _send_or_edit(update, "Use the private dashboard → My Chats → select a chat → My Stats.")
        return
    await _show_my_stats(update, context, chat.id, user.id)


async def _show_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    async with SessionLocal() as session:
        stats = await user_stats(session, chat_id, user_id)
    title = await _chat_title(chat_id)
    admin = await _is_admin(context, chat_id, user_id)
    await _send_or_edit(
        update,
        _stats_text(f"📊 Your invite stats — {title}", stats),
        _chat_keyboard(chat_id, include_admin=admin),
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type not in GROUP_TYPES:
        await _send_or_edit(update, "Open the private dashboard and choose a tracked chat first.")
        return

    active_only = not (context.args and context.args[0].lower() == "all")
    await _show_leaderboard(update, context, chat.id, active_only)


async def _show_leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    active_only: bool,
) -> None:
    async with SessionLocal() as session:
        rows = await get_leaderboard(session, chat_id, active_only)
        if not rows:
            text = "🏆 No referrals recorded yet."
        else:
            lines = []
            medals = ["🥇", "🥈", "🥉"]
            for index, (user_id, count) in enumerate(rows, start=1):
                user = await session.get(User, user_id)
                if user:
                    display = f"@{user.username}" if user.username else (user.first_name or str(user_id))
                else:
                    display = str(user_id)
                prefix = medals[index - 1] if index <= 3 else f"{index}."
                lines.append(f"{prefix} {display} — {count}")

            title = "🏆 Active Invite Leaderboard" if active_only else "🏆 All-Time Invite Leaderboard"
            text = title + "\n\n" + "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Active", callback_data=f"leader:{chat_id}:active"),
                InlineKeyboardButton("📚 All Time", callback_data=f"leader:{chat_id}:all"),
            ],
            [InlineKeyboardButton("◀ Chat Menu", callback_data=f"chat:{chat_id}")],
        ]
    )
    await _send_or_edit(update, text, keyboard)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user

    if not chat:
        return

    if chat.type == ChatType.PRIVATE:
        await _send_or_edit(
            update,
            "🛡 Choose **Admin Stats** from the dashboard to view groups/channels you administer.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🛡 Admin Stats", callback_data="adminchats")]]),
        )
        return

    if chat.type not in GROUP_TYPES:
        return

    # In a normal group post we can verify the actual administrator.
    if user:
        if not await _is_admin(context, chat.id, user.id):
            await _send_or_edit(update, "🛡 /stats is available to chat administrators.")
            return
    else:
        # Channel posts may be sent as the channel identity rather than exposing
        # the human admin. Such a command can only originate from someone who
        # has permission to post in that channel, so show aggregate chat stats.
        sender_chat = update.effective_message.sender_chat if update.effective_message else None
        if not sender_chat or sender_chat.id != chat.id:
            return

    await _show_admin_stats(update, context, chat.id, user.id if user else None)


async def _show_admin_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int | None,
) -> None:
    if user_id is not None and not await _is_admin(context, chat_id, user_id):
        await _send_or_edit(update, "You are not an administrator of that chat.")
        return

    async with SessionLocal() as session:
        data = await chat_stats(session, chat_id)

    title = await _chat_title(chat_id)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏆 Leaderboard", callback_data=f"leader:{chat_id}:active")],
            [InlineKeyboardButton("◀ Chat Menu", callback_data=f"chat:{chat_id}")],
        ]
    )
    await _send_or_edit(
        update,
        _stats_text(f"🛡 Overall stats — {title}", data, overall=True),
        keyboard,
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    await query.answer()
    data = query.data or ""

    if data == "home":
        me = await context.bot.get_me()
        await _send_or_edit(
            update,
            "🤖 TG Invite Tracker\n\nChoose what you want to do:",
            _home_keyboard(me.username),
        )
        return

    if data == "help":
        await _send_or_edit(
            update,
            "❓ Help\n\n"
            "• My Chats: your tracked groups/channels\n"
            "• My Invite Link: one personal link per chat\n"
            "• My Stats: your direct + link referrals\n"
            "• My Invitees: latest people credited to you\n"
            "• Leaderboard: active or all-time rankings\n"
            "• Admin Stats: overall stats for chats you administer",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]),
        )
        return

    if data in {"mychats", "adminchats"}:
        admins_only = data == "adminchats"
        chats = await _discover_chats(context, user.id, admins_only=admins_only)
        if not chats:
            text = (
                "🛡 No tracked groups/channels where you are currently an admin were found."
                if admins_only
                else "📂 No tracked groups/channels were found for your account yet."
            )
            await _send_or_edit(
                update,
                text,
                InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]),
            )
            return

        rows = [
            [InlineKeyboardButton(
                ("🛡 " if admins_only else "📍 ") + (chat.title or str(chat.group_id)),
                callback_data=f"{'adminstats' if admins_only else 'chat'}:{chat.group_id}",
            )]
            for chat in chats[:25]
        ]
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
        await _send_or_edit(
            update,
            "🛡 Your admin chats:" if admins_only else "📂 Your tracked chats:",
            InlineKeyboardMarkup(rows),
        )
        return

    parts = data.split(":")
    action = parts[0]

    try:
        chat_id = int(parts[1])
    except (IndexError, ValueError):
        return

    if action == "chat":
        if not await _is_member(context, chat_id, user.id):
            await _send_or_edit(update, "You are no longer a member of that chat.")
            return
        title = await _chat_title(chat_id)
        admin = await _is_admin(context, chat_id, user.id)
        await _send_or_edit(
            update,
            f"📍 {title}\n\nChoose an option:",
            _chat_keyboard(chat_id, include_admin=admin),
        )
    elif action == "mystats":
        await _show_my_stats(update, context, chat_id, user.id)
    elif action == "link":
        await _show_link(update, context, chat_id, user.id)
    elif action == "leader":
        active_only = len(parts) < 3 or parts[2] != "all"
        await _show_leaderboard(update, context, chat_id, active_only)
    elif action == "adminstats":
        await _show_admin_stats(update, context, chat_id, user.id)
    elif action == "invitees":
        async with SessionLocal() as session:
            rows = await referral_invitees(session, chat_id, user.id)
        title = await _chat_title(chat_id)
        if not rows:
            text = f"👥 Your invitees — {title}\n\nNo referrals recorded yet."
        else:
            lines = [f"👥 Your latest invitees — {title}", ""]
            for idx, (referral, invitee) in enumerate(rows, start=1):
                if invitee and invitee.username:
                    name = f"@{invitee.username}"
                elif invitee and invitee.first_name:
                    name = invitee.first_name
                else:
                    name = str(referral.invitee_id)
                status = "✅ Active" if referral.active else "❌ Left"
                method = "🔗 Link" if referral.method.value == "INVITE_LINK" else "➕ Direct"
                lines.append(f"{idx}. {name} — {status} — {method}")
            text = "\n".join(lines)

        await _send_or_edit(
            update,
            text,
            InlineKeyboardMarkup([[InlineKeyboardButton("◀ Chat Menu", callback_data=f"chat:{chat_id}")]]),
        )
