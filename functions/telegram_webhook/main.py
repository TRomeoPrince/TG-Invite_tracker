from __future__ import annotations

import logging
import os
from typing import Any

import requests
import zcatalyst_sdk
from flask import Request, jsonify, make_response

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

USERS = "TG_Users"
CHATS = "TG_Chats"
LINKS = "TG_InviteLinks"
REFERRALS = "TG_Referrals"
SETTINGS = "TG_ChatSettings"
CONTESTS = "TG_Contests"
CONTEST_REFS = "TG_ContestReferrals"

DEFAULT_WELCOME = "👋 HI, {USER}! How are you?"
DEFAULT_CONTEST_NAME = "Invite Contest"

logger = logging.getLogger()
_BOT_USERNAME_CACHE = None


def _api(method: str, **payload):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result")


def _escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _zcql(app, query: str):
    return app.zcql().execute_query(query)


def _flatten(result_item: dict, table: str) -> dict:
    if table in result_item and isinstance(result_item[table], dict):
        return result_item[table]
    return result_item


def _select(app, table: str, where: str = "") -> list[dict]:
    query = f"SELECT * FROM {table}"
    if where:
        query += f" WHERE {where}"
    rows = _zcql(app, query)
    return [_flatten(row, table) for row in rows]


def _find_one(app, table: str, where: str) -> dict | None:
    rows = _select(app, table, where)
    return rows[0] if rows else None


def _upsert_user(app, user: dict | None) -> None:
    if not user:
        return
    uid = str(user["id"])
    table = app.datastore().table(USERS)
    existing = _find_one(app, USERS, f"TelegramUserID = '{_escape(uid)}'")
    data = {
        "TelegramUserID": uid,
        "Username": user.get("username") or "",
        "FirstName": user.get("first_name") or "",
        "LastName": user.get("last_name") or "",
    }
    if existing:
        data["ROWID"] = existing["ROWID"]
        table.update_row(data)
    else:
        table.insert_row(data)


def _upsert_chat(app, chat: dict | None) -> None:
    if not chat or chat.get("type") == "private":
        return
    cid = str(chat["id"])
    table = app.datastore().table(CHATS)
    existing = _find_one(app, CHATS, f"ChatID = '{_escape(cid)}'")
    data = {
        "ChatID": cid,
        "Title": chat.get("title") or chat.get("username") or "Private Chat",
        "ChatType": chat.get("type") or "",
    }
    if existing:
        data["ROWID"] = existing["ROWID"]
        table.update_row(data)
    else:
        table.insert_row(data)


def _find_link(app, chat_id: int | str, owner_id: int | str) -> dict | None:
    return _find_one(
        app,
        LINKS,
        f"ChatID = '{_escape(chat_id)}' AND OwnerUserID = '{_escape(owner_id)}' AND Revoked = false",
    )


def _find_link_owner(app, chat_id: int | str, invite_link: str) -> str | None:
    row = _find_one(
        app,
        LINKS,
        f"ChatID = '{_escape(chat_id)}' AND InviteLink = '{_escape(invite_link)}' AND Revoked = false",
    )
    return str(row["OwnerUserID"]) if row else None


def _save_link(app, chat_id: int | str, owner_id: int | str, invite_link: str) -> None:
    table = app.datastore().table(LINKS)
    existing = _find_link(app, chat_id, owner_id)
    data = {
        "ChatID": str(chat_id),
        "OwnerUserID": str(owner_id),
        "InviteLink": invite_link,
        "Revoked": False,
    }
    if existing:
        data["ROWID"] = existing["ROWID"]
        table.update_row(data)
    else:
        table.insert_row(data)


def _find_referral(app, chat_id: int | str, invitee_id: int | str) -> dict | None:
    return _find_one(
        app,
        REFERRALS,
        f"ChatID = '{_escape(chat_id)}' AND InviteeID = '{_escape(invitee_id)}'",
    )


def _record_referral(
    app,
    chat_id: int | str,
    invitee_id: int | str,
    inviter_id: int | str,
    method: str,
) -> bool:
    table = app.datastore().table(REFERRALS)
    existing = _find_referral(app, chat_id, invitee_id)
    if existing:
        table.update_row(
            {
                "ROWID": existing["ROWID"],
                "Active": True,
                "JoinCount": int(existing.get("JoinCount") or 1) + 1,
            }
        )
        return False

    table.insert_row(
        {
            "ChatID": str(chat_id),
            "InviteeID": str(invitee_id),
            "InviterID": str(inviter_id),
            "Method": method,
            "Active": True,
            "JoinCount": 1,
        }
    )
    return True


def _mark_left(app, chat_id: int | str, invitee_id: int | str) -> None:
    row = _find_referral(app, chat_id, invitee_id)
    if row:
        app.datastore().table(REFERRALS).update_row(
            {"ROWID": row["ROWID"], "Active": False}
        )


def _referrals_for(app, chat_id: int | str, inviter_id: int | str | None = None) -> list[dict]:
    where = f"ChatID = '{_escape(chat_id)}'"
    if inviter_id is not None:
        where += f" AND InviterID = '{_escape(inviter_id)}'"
    return _select(app, REFERRALS, where)


def _get_welcome_settings(app, chat_id: int | str) -> dict:
    row = _find_one(app, SETTINGS, f"ChatID = '{_escape(chat_id)}'")
    if not row:
        return {"Enabled": True, "Message": DEFAULT_WELCOME}
    return {
        "Enabled": bool(row.get("Enabled")),
        "Message": row.get("Message") or DEFAULT_WELCOME,
    }


def _save_welcome_settings(
    app,
    chat_id: int | str,
    *,
    enabled: bool | None = None,
    message: str | None = None,
) -> dict:
    table = app.datastore().table(SETTINGS)
    existing = _find_one(app, SETTINGS, f"ChatID = '{_escape(chat_id)}'")
    current = _get_welcome_settings(app, chat_id)
    data = {
        "ChatID": str(chat_id),
        "Enabled": current["Enabled"] if enabled is None else enabled,
        "Message": current["Message"] if message is None else message,
    }
    if existing:
        data["ROWID"] = existing["ROWID"]
        table.update_row(data)
    else:
        table.insert_row(data)
    return data


def _render_welcome(template: str, member: dict) -> str:
    name = member.get("first_name") or member.get("username") or "there"
    return template.replace("{USER}", name)


def _get_contest(app, chat_id: int | str) -> dict:
    row = _find_one(app, CONTESTS, f"ChatID = '{_escape(chat_id)}'")
    if not row:
        return {
            "Active": False,
            "Name": DEFAULT_CONTEST_NAME,
            "Prize": "",
            "Generation": 0,
        }
    return {
        "Active": bool(row.get("Active")),
        "Name": row.get("Name") or DEFAULT_CONTEST_NAME,
        "Prize": row.get("Prize") or "",
        "Generation": int(row.get("Generation") or 0),
    }


def _save_contest(
    app,
    chat_id: int | str,
    *,
    active: bool | None = None,
    name: str | None = None,
    prize: str | None = None,
    start_new: bool = False,
) -> dict:
    table = app.datastore().table(CONTESTS)
    existing = _find_one(app, CONTESTS, f"ChatID = '{_escape(chat_id)}'")
    current = _get_contest(app, chat_id)
    generation = current["Generation"] + 1 if start_new else current["Generation"]
    data = {
        "ChatID": str(chat_id),
        "Active": current["Active"] if active is None else active,
        "Name": current["Name"] if name is None else name,
        "Prize": current["Prize"] if prize is None else prize,
        "Generation": generation,
    }
    if existing:
        data["ROWID"] = existing["ROWID"]
        table.update_row(data)
    else:
        table.insert_row(data)
    return data


def _contest_record_referral(app, chat_id, invitee_id, inviter_id) -> None:
    contest = _get_contest(app, chat_id)
    if not contest["Active"]:
        return
    generation = contest["Generation"]
    existing = _find_one(
        app,
        CONTEST_REFS,
        f"ChatID = '{_escape(chat_id)}' AND Generation = {generation} "
        f"AND InviteeID = '{_escape(invitee_id)}'",
    )
    table = app.datastore().table(CONTEST_REFS)
    if existing:
        table.update_row({"ROWID": existing["ROWID"], "Active": True})
        return
    table.insert_row(
        {
            "ChatID": str(chat_id),
            "Generation": generation,
            "InviteeID": str(invitee_id),
            "InviterID": str(inviter_id),
            "Active": True,
        }
    )


def _contest_mark_left(app, chat_id, invitee_id) -> None:
    contest = _get_contest(app, chat_id)
    generation = contest["Generation"]
    if not generation:
        return
    row = _find_one(
        app,
        CONTEST_REFS,
        f"ChatID = '{_escape(chat_id)}' AND Generation = {generation} "
        f"AND InviteeID = '{_escape(invitee_id)}'",
    )
    if row:
        app.datastore().table(CONTEST_REFS).update_row(
            {"ROWID": row["ROWID"], "Active": False}
        )


def _contest_rows(app, chat_id) -> list[dict]:
    contest = _get_contest(app, chat_id)
    generation = contest["Generation"]
    if not generation:
        return []
    return _select(
        app,
        CONTEST_REFS,
        f"ChatID = '{_escape(chat_id)}' AND Generation = {generation}",
    )


def _contest_announcement(app, chat_id) -> str:
    contest = _get_contest(app, chat_id)
    title = _chat_title(app, chat_id)
    lines = [
        f"🏆 {contest['Name']} — {title}",
        "",
        "Invite friends using your personal invite link or direct adds.",
        "Every valid active referral counts toward the contest leaderboard.",
    ]
    if contest["Prize"]:
        lines += ["", f"🎁 Reward: {contest['Prize']}"]
    lines += ["", "Use /link to get your personal invite link.", "Use /leaderboard to check the standings."]
    return "\n".join(lines)


def _show_contest_leaderboard(app, chat_id, target_chat_id, edit_message_id=None):
    contest = _get_contest(app, chat_id)
    rows = _contest_rows(app, chat_id)
    counts: dict[str, int] = {}
    for row in rows:
        if not bool(row.get("Active")):
            continue
        inviter = str(row["InviterID"])
        counts[inviter] = counts.get(inviter, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:20]
    lines = [f"🏆 {contest['Name']} Leaderboard"]
    if contest["Prize"]:
        lines.append(f"🎁 Reward: {contest['Prize']}")
    lines.append("")
    if not ordered:
        lines.append("No active contest referrals yet.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for index, (uid, count) in enumerate(ordered, start=1):
            prefix = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{prefix} {_user_name(app, uid)} — {count}")
    lines.append(_growth_footer().strip())

    markup = _keyboard([
        [{"text": "🔄 Refresh", "callback_data": f"contestboard:{chat_id}"}],
        [_add_bot_button()],
        [{"text": "◀ Contest Settings", "callback_data": f"contestsettings:{chat_id}"}],
    ])
    text = "\n".join(lines)
    if edit_message_id:
        return _edit(target_chat_id, edit_message_id, text, markup)
    return _send(target_chat_id, text, markup)


def _user_name(app, user_id: int | str) -> str:
    row = _find_one(app, USERS, f"TelegramUserID = '{_escape(user_id)}'")
    if not row:
        return str(user_id)
    if row.get("Username"):
        return f"@{row['Username']}"
    return row.get("FirstName") or str(user_id)


def _stats(rows: list[dict]) -> dict[str, int]:
    return {
        "total": len(rows),
        "active": sum(bool(r.get("Active")) for r in rows),
        "left": sum(not bool(r.get("Active")) for r in rows),
        "direct": sum(r.get("Method") == "DIRECT_ADD" for r in rows),
        "link": sum(r.get("Method") == "INVITE_LINK" for r in rows),
    }


def _keyboard(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def _send(chat_id, text, reply_markup=None, reply_to_message_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    return _api("sendMessage", **payload)


def _delete_message(chat_id, message_id):
    try:
        return _api("deleteMessage", chat_id=chat_id, message_id=message_id)
    except Exception:
        # Ignore permission/race errors so cleanup never breaks tracking.
        return None


def _edit(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api("editMessageText", **payload)


def _answer_callback(callback_id: str):
    return _api("answerCallbackQuery", callback_query_id=callback_id)


def _is_member(chat_id, user_id) -> bool:
    try:
        result = _api("getChatMember", chat_id=chat_id, user_id=user_id)
        return result.get("status") in {"member", "administrator", "creator", "restricted"}
    except Exception:
        return False


def _is_admin(chat_id, user_id) -> bool:
    try:
        result = _api("getChatMember", chat_id=chat_id, user_id=user_id)
        return result.get("status") in {"administrator", "creator"}
    except Exception:
        return False


def _bot_username() -> str:
    global _BOT_USERNAME_CACHE
    if not _BOT_USERNAME_CACHE:
        _BOT_USERNAME_CACHE = _api("getMe").get("username")
    return _BOT_USERNAME_CACHE


def _growth_footer() -> str:
    return f"\n\n🤖 Powered by @{_bot_username()}"


def _add_bot_button() -> dict:
    username = _bot_username()
    return {
        "text": "➕ Add Inviter Tracker to Your Group",
        "url": f"https://t.me/{username}?startgroup=setup&admin=invite_users",
    }


def _chat_menu(chat_id, admin=False):
    rows = [
        [
            {"text": "📊 My Stats", "callback_data": f"mystats:{chat_id}"},
            {"text": "🔗 My Invite Link", "callback_data": f"link:{chat_id}"},
        ],
        [
            {"text": "👥 My Invitees", "callback_data": f"invitees:{chat_id}"},
            {"text": "🏆 Leaderboard", "callback_data": f"leader:{chat_id}:active"},
        ],
    ]
    if admin:
        rows.append(
            [{"text": "🛡 Overall Admin Stats", "callback_data": f"adminstats:{chat_id}"}]
        )
        rows.append(
            [
                {"text": "👋 Welcome Settings", "callback_data": f"welsettings:{chat_id}"},
                {"text": "🏆 Contest", "callback_data": f"contestsettings:{chat_id}"},
            ]
        )
    rows.append([_add_bot_button()])
    rows.append([{"text": "🏠 Home", "callback_data": "home"}])
    return _keyboard(rows)


def _home_menu():
    username = _bot_username()
    return _keyboard(
        [
            [
                {"text": "📂 My Chats", "callback_data": "mychats"},
                {"text": "🛡 Admin Stats", "callback_data": "adminchats"},
            ],
            [
                {
                    "text": "➕ Add to Group",
                    "url": f"https://t.me/{username}?startgroup=setup&admin=invite_users",
                },
                {
                    "text": "📢 Add to Channel",
                    "url": f"https://t.me/{username}?startchannel&admin=invite_users",
                },
            ],
            [{"text": "❓ Help", "callback_data": "help"}],
        ]
    )


def _tracked_chats(app) -> list[dict]:
    return [row for row in _select(app, CHATS) if row.get("ChatType") != "private"]


def _chat_title(app, chat_id) -> str:
    row = _find_one(app, CHATS, f"ChatID = '{_escape(chat_id)}'")
    return row.get("Title") if row else str(chat_id)


def _show_link(app, user_id, target_chat_id, private_chat_id, edit_message_id=None):
    if not _is_member(target_chat_id, user_id):
        text = "You are not currently a member of that tracked chat."
        if edit_message_id:
            return _edit(private_chat_id, edit_message_id, text)
        return _send(private_chat_id, text)

    existing = _find_link(app, target_chat_id, user_id)
    if existing:
        invite_link = existing["InviteLink"]
    else:
        invite = _api(
            "createChatInviteLink",
            chat_id=target_chat_id,
            name=f"ref:{user_id}",
        )
        invite_link = invite["invite_link"]
        _save_link(app, target_chat_id, user_id, invite_link)

    title = _chat_title(app, target_chat_id)
    text = (
        f"🔗 Your personal invite link\n\n📍 {title}\n{invite_link}\n\n"
        "Anyone joining through this link is credited to your Telegram user ID.\n\n"
        "Run invite tracking in your own community too — add Inviter Tracker to your group."
    )
    markup = _chat_menu(target_chat_id, _is_admin(target_chat_id, user_id))
    if edit_message_id:
        return _edit(private_chat_id, edit_message_id, text, markup)
    return _send(private_chat_id, text, markup)


def _show_my_stats(app, user_id, chat_id, private_chat_id, edit_message_id=None):
    data = _stats(_referrals_for(app, chat_id, user_id))
    title = _chat_title(app, chat_id)
    text = (
        f"📊 Your invite stats — {title}\n\n"
        f"👥 Total: {data['total']}\n"
        f"✅ Active: {data['active']}\n"
        f"❌ Left: {data['left']}\n"
        f"➕ Direct adds: {data['direct']}\n"
        f"🔗 Invite-link joins: {data['link']}"
        + _growth_footer()
    )
    markup = _chat_menu(chat_id, _is_admin(chat_id, user_id))
    if edit_message_id:
        return _edit(private_chat_id, edit_message_id, text, markup)
    return _send(private_chat_id, text, markup)


def _show_leaderboard(app, chat_id, target_chat_id, edit_message_id=None, active_only=True):
    rows = _referrals_for(app, chat_id)
    counts: dict[str, int] = {}
    for row in rows:
        if active_only and not bool(row.get("Active")):
            continue
        inviter = str(row["InviterID"])
        counts[inviter] = counts.get(inviter, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:20]
    if not ordered:
        text = "🏆 No referrals recorded yet."
    else:
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for index, (uid, count) in enumerate(ordered, start=1):
            prefix = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{prefix} {_user_name(app, uid)} — {count}")
        title = "🏆 Active Invite Leaderboard" if active_only else "🏆 All-Time Invite Leaderboard"
        text = title + "\n\n" + "\n".join(lines) + _growth_footer()

    markup = _keyboard(
        [
            [
                {"text": "✅ Active", "callback_data": f"leader:{chat_id}:active"},
                {"text": "📚 All Time", "callback_data": f"leader:{chat_id}:all"},
            ],
            [_add_bot_button()],
            [{"text": "◀ Chat Menu", "callback_data": f"chat:{chat_id}"}],
        ]
    )
    if edit_message_id:
        return _edit(target_chat_id, edit_message_id, text, markup)
    return _send(target_chat_id, text, markup)


def _show_admin_stats(app, chat_id, target_chat_id, user_id=None, edit_message_id=None):
    if user_id is not None and not _is_admin(chat_id, user_id):
        text = "You are not an administrator of that chat."
        if edit_message_id:
            return _edit(target_chat_id, edit_message_id, text)
        return _send(target_chat_id, text)

    rows = _referrals_for(app, chat_id)
    data = _stats(rows)
    inviters = len({str(r["InviterID"]) for r in rows})
    title = _chat_title(app, chat_id)
    text = (
        f"🛡 Overall stats — {title}\n\n"
        f"👥 Total: {data['total']}\n"
        f"✅ Active: {data['active']}\n"
        f"❌ Left: {data['left']}\n"
        f"➕ Direct adds: {data['direct']}\n"
        f"🔗 Invite-link joins: {data['link']}\n"
        f"🙋 Inviters: {inviters}"
        + _growth_footer()
    )
    markup = _keyboard(
        [
            [{"text": "🏆 Leaderboard", "callback_data": f"leader:{chat_id}:active"}],
            [_add_bot_button()],
            [{"text": "◀ Chat Menu", "callback_data": f"chat:{chat_id}"}],
        ]
    )
    if edit_message_id:
        return _edit(target_chat_id, edit_message_id, text, markup)
    return _send(target_chat_id, text, markup)


def _handle_message(app, message: dict):
    chat = message.get("chat") or {}
    user = message.get("from")
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    message_id = message.get("message_id")

    _upsert_chat(app, chat)
    _upsert_user(app, user)

    # Telegram service messages for group joins/leaves.
    new_members = message.get("new_chat_members") or []
    left_member = message.get("left_chat_member")

    if new_members and chat_type in {"group", "supergroup"}:
        welcome = _get_welcome_settings(app, chat_id)
        for new_member in new_members:
            if new_member.get("is_bot"):
                continue
            _upsert_user(app, new_member)
            if welcome["Enabled"]:
                _send(
                    chat_id,
                    _render_welcome(welcome["Message"], new_member),
                    reply_to_message_id=message_id,
                )

        # Remove Telegram's native "X joined..." service message after the
        # welcome has been sent so busy groups stay clean.
        if message_id:
            _delete_message(chat_id, message_id)

    if left_member and chat_type in {"group", "supergroup"}:
        # Membership state/referral tracking is handled by chat_member updates.
        # The visible native "X left the group" service message is just clutter.
        if message_id:
            _delete_message(chat_id, message_id)
        return

    # Admin custom welcome message via ForceReply in private chat.
    reply_to = message.get("reply_to_message") or {}
    reply_text = reply_to.get("text") or ""
    if chat_type == "private" and text and reply_text.startswith("✏️ Send the new welcome message for "):
        marker = "\nChat ID: "
        if marker in reply_text and user:
            try:
                target_chat_id = int(reply_text.split(marker, 1)[1].splitlines()[0].strip())
            except ValueError:
                target_chat_id = None
            if target_chat_id is not None:
                if not _is_admin(target_chat_id, user["id"]):
                    return _send(chat_id, "You are no longer an administrator of that chat.")
                if len(text) > 1000:
                    return _send(chat_id, "Welcome messages must be 1000 characters or fewer.")
                _save_welcome_settings(app, target_chat_id, message=text)
                return _send(
                    chat_id,
                    "✅ Custom welcome message saved.\n\nPreview:\n" + text.replace("{USER}", user.get("first_name") or "New Member"),
                    _keyboard([[{"text": "👋 Welcome Settings", "callback_data": f"welsettings:{target_chat_id}"}]]),
                )

    # Admin contest-name / reward setup via ForceReply in private chat.
    if chat_type == "private" and text and user:
        contest_name_prefix = "✏️ Send the contest name for "
        contest_prize_prefix = "🎁 Send the contest reward for "
        if reply_text.startswith(contest_name_prefix) or reply_text.startswith(contest_prize_prefix):
            marker = "\nChat ID: "
            if marker in reply_text:
                try:
                    target_chat_id = int(reply_text.split(marker, 1)[1].splitlines()[0].strip())
                except ValueError:
                    target_chat_id = None
                if target_chat_id is not None:
                    if not _is_admin(target_chat_id, user["id"]):
                        return _send(chat_id, "You are no longer an administrator of that chat.")
                    if len(text) > 500:
                        return _send(chat_id, "Please keep this to 500 characters or fewer.")
                    if reply_text.startswith(contest_name_prefix):
                        _save_contest(app, target_chat_id, name=text)
                        saved = f"✅ Contest name saved as:\n{text}"
                    else:
                        _save_contest(app, target_chat_id, prize=text)
                        saved = f"✅ Contest reward saved as:\n{text}"
                    return _send(
                        chat_id,
                        saved,
                        _keyboard([[{"text": "🏆 Contest Settings", "callback_data": f"contestsettings:{target_chat_id}"}]]),
                    )

    if not text.startswith("/"):
        return

    command, *args = text.split()
    command = command.split("@")[0].lower()

    if command == "/start":
        if chat_type == "private":
            if args and args[0].startswith("link_") and user:
                try:
                    target_chat_id = int(args[0][5:])
                except ValueError:
                    target_chat_id = None
                if target_chat_id is not None:
                    return _show_link(app, user["id"], target_chat_id, chat_id)

            if args and args[0].startswith("setup_") and user:
                try:
                    target_chat_id = int(args[0][6:])
                except ValueError:
                    target_chat_id = None
                if target_chat_id is not None:
                    if not _is_admin(target_chat_id, user["id"]):
                        return _send(chat_id, "You need to be an admin of that chat to complete setup.")
                    title = _chat_title(app, target_chat_id)
                    return _send(
                        chat_id,
                        f"✅ Inviter Tracker setup — {title}\n\n"
                        "Invite tracking is ready.\n"
                        "🔗 Personal invite links: ready\n"
                        "👋 Welcome messages: ON by default\n"
                        "🏆 Contest tools: ready\n\n"
                        "Use the buttons below to customize anything you want.",
                        _chat_menu(target_chat_id, True),
                    )

            return _send(
                chat_id,
                "🤖 TG Invite Tracker\n\n"
                "Track direct adds and personal invite links across your Telegram groups and channels.\n\n"
                "Choose what you want to do:",
                _home_menu(),
            )

        admin = bool(user and _is_admin(chat_id, user["id"]))
        return _send(
            chat_id,
            f"🤖 Invite Tracker — {chat.get('title') or 'this chat'}\n\nChoose an option:",
            _chat_menu(chat_id, admin),
            message_id,
        )

    if command == "/link":
        if chat_type in {"group", "supergroup"} and user:
            username = _bot_username()
            private_url = f"https://t.me/{username}?start=link_{chat_id}"
            return _send(
                chat_id,
                "📩 Your personal invite link will be sent privately so the group stays clean.",
                _keyboard([[{"text": "📩 Get My Invite Link", "url": private_url}]]),
                message_id,
            )
        if chat_type == "private":
            return _send(
                chat_id,
                "🔗 Open My Chats, select the group/channel, then tap My Invite Link.",
                _keyboard([[{"text": "📂 My Chats", "callback_data": "mychats"}]]),
            )

    if command == "/myinvites" and user:
        if chat_type == "private":
            return _send(
                chat_id,
                "📂 Choose My Chats, select a chat, then tap My Stats.",
                _keyboard([[{"text": "📂 My Chats", "callback_data": "mychats"}]]),
            )
        return _show_my_stats(app, user["id"], chat_id, chat_id)

    if command == "/leaderboard":
        active_only = not (args and args[0].lower() == "all")
        return _show_leaderboard(app, chat_id, chat_id, active_only=active_only)

    if command == "/stats":
        if chat_type == "private":
            return _send(
                chat_id,
                "🛡 Choose Admin Stats from the dashboard.",
                _keyboard([[{"text": "🛡 Admin Stats", "callback_data": "adminchats"}]]),
            )
        if user and not _is_admin(chat_id, user["id"]):
            return _send(chat_id, "🛡 /stats is available to chat administrators.")
        return _show_admin_stats(app, chat_id, chat_id, user["id"] if user else None)


def _handle_callback(app, callback: dict):
    callback_id = callback["id"]
    _answer_callback(callback_id)

    user = callback.get("from")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    data = callback.get("data") or ""

    _upsert_user(app, user)

    if data == "home":
        return _edit(
            chat_id,
            message_id,
            "🤖 TG Invite Tracker\n\nChoose what you want to do:",
            _home_menu(),
        )

    if data == "help":
        return _edit(
            chat_id,
            message_id,
            "❓ Help\n\n"
            "• My Chats: your tracked groups/channels\n"
            "• My Invite Link: one personal link per chat\n"
            "• My Stats: your direct + link referrals\n"
            "• My Invitees: latest people credited to you\n"
            "• Leaderboard: active or all-time rankings\n"
            "• Admin Stats: overall stats for chats you administer",
            _keyboard([[{"text": "🏠 Home", "callback_data": "home"}]]),
        )

    if data in {"mychats", "adminchats"}:
        admins_only = data == "adminchats"
        rows = []
        for tracked in _tracked_chats(app)[:100]:
            cid = int(tracked["ChatID"])
            if admins_only:
                visible = _is_admin(cid, user["id"])
            else:
                visible = _is_member(cid, user["id"])
            if visible:
                rows.append(
                    [
                        {
                            "text": ("🛡 " if admins_only else "📍 ")
                            + (tracked.get("Title") or str(cid)),
                            "callback_data": f"{'adminstats' if admins_only else 'chat'}:{cid}",
                        }
                    ]
                )
        rows.append([{"text": "🏠 Home", "callback_data": "home"}])
        return _edit(
            chat_id,
            message_id,
            "🛡 Your admin chats:" if admins_only else "📂 Your tracked chats:",
            _keyboard(rows),
        )

    if data.startswith("welsettings:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        settings = _get_welcome_settings(app, target_chat_id)
        status = "✅ ON" if settings["Enabled"] else "❌ OFF"
        title = _chat_title(app, target_chat_id)
        preview = settings["Message"].replace("{USER}", "New Member")
        return _edit(
            chat_id,
            message_id,
            f"👋 Welcome Settings — {title}\n\nStatus: {status}\n\nCurrent message:\n{settings['Message']}\n\nPreview:\n{preview}",
            _keyboard([
                [
                    {"text": "✅ Turn ON", "callback_data": f"weltoggle:{target_chat_id}:on"},
                    {"text": "❌ Turn OFF", "callback_data": f"weltoggle:{target_chat_id}:off"},
                ],
                [{"text": "✏️ Set Custom Message", "callback_data": f"welcustom:{target_chat_id}"}],
                [{"text": "♻️ Reset to Default", "callback_data": f"welreset:{target_chat_id}"}],
                [{"text": "◀ Chat Menu", "callback_data": f"chat:{target_chat_id}"}],
            ]),
        )

    if data.startswith("weltoggle:"):
        parts2 = data.split(":")
        if len(parts2) != 3:
            return
        try:
            target_chat_id = int(parts2[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        enabled = parts2[2] == "on"
        _save_welcome_settings(app, target_chat_id, enabled=enabled)
        return _edit(
            chat_id,
            message_id,
            f"👋 Welcome messages are now {'✅ ON' if enabled else '❌ OFF'}.",
            _keyboard([[{"text": "👋 Welcome Settings", "callback_data": f"welsettings:{target_chat_id}"}]]),
        )

    if data.startswith("welreset:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        _save_welcome_settings(app, target_chat_id, message=DEFAULT_WELCOME)
        return _edit(
            chat_id,
            message_id,
            "♻️ Welcome message reset to:\n\n" + DEFAULT_WELCOME,
            _keyboard([[{"text": "👋 Welcome Settings", "callback_data": f"welsettings:{target_chat_id}"}]]),
        )

    if data.startswith("welcustom:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        title = _chat_title(app, target_chat_id)
        return _send(
            chat_id,
            f"✏️ Send the new welcome message for {title}\nChat ID: {target_chat_id}\n\nReply to this message with the new welcome text. Use {{USER}} where the new member's name should appear.",
            {"force_reply": True, "selective": True},
        )

    if data.startswith("contestsettings:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        contest = _get_contest(app, target_chat_id)
        title = _chat_title(app, target_chat_id)
        status = "✅ RUNNING" if contest["Active"] else "⏸ OFF"
        reward = contest["Prize"] or "Not set"
        return _edit(
            chat_id,
            message_id,
            f"🏆 Contest Settings — {title}\n\n"
            f"Status: {status}\n"
            f"Contest: {contest['Name']}\n"
            f"Reward: {reward}\n\n"
            "No member-count threshold is required. Start it whenever you want.",
            _keyboard([
                [
                    {"text": "▶️ Start New Contest", "callback_data": f"conteststart:{target_chat_id}"},
                    {"text": "⏹ Stop", "callback_data": f"conteststop:{target_chat_id}"},
                ],
                [
                    {"text": "✏️ Set Name", "callback_data": f"contestname:{target_chat_id}"},
                    {"text": "🎁 Set Reward", "callback_data": f"contestprize:{target_chat_id}"},
                ],
                [{"text": "📣 Announce Contest", "callback_data": f"contestannounce:{target_chat_id}"}],
                [{"text": "🏆 Contest Leaderboard", "callback_data": f"contestboard:{target_chat_id}"}],
                [{"text": "◀ Chat Menu", "callback_data": f"chat:{target_chat_id}"}],
            ]),
        )

    if data.startswith("conteststart:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        contest = _save_contest(app, target_chat_id, active=True, start_new=True)
        try:
            _send(target_chat_id, _contest_announcement(app, target_chat_id))
        except Exception:
            pass
        return _edit(
            chat_id,
            message_id,
            f"✅ {contest['Name']} has started.\n\nOnly referrals recorded from this new contest onward count in its leaderboard.",
            _keyboard([[{"text": "🏆 Contest Settings", "callback_data": f"contestsettings:{target_chat_id}"}]]),
        )

    if data.startswith("conteststop:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        _save_contest(app, target_chat_id, active=False)
        return _edit(
            chat_id,
            message_id,
            "⏹ Contest stopped. The current contest leaderboard remains available for review.",
            _keyboard([[{"text": "🏆 Contest Settings", "callback_data": f"contestsettings:{target_chat_id}"}]]),
        )

    if data.startswith("contestname:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        title = _chat_title(app, target_chat_id)
        return _send(
            chat_id,
            f"✏️ Send the contest name for {title}\nChat ID: {target_chat_id}\n\nReply to this message with the contest name.",
            {"force_reply": True, "selective": True},
        )

    if data.startswith("contestprize:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        title = _chat_title(app, target_chat_id)
        return _send(
            chat_id,
            f"🎁 Send the contest reward for {title}\nChat ID: {target_chat_id}\n\nReply with the reward/prize text.",
            {"force_reply": True, "selective": True},
        )

    if data.startswith("contestannounce:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not _is_admin(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are not an administrator of that chat.")
        contest = _get_contest(app, target_chat_id)
        if not contest["Active"]:
            return _edit(
                chat_id,
                message_id,
                "Start the contest first, then announce it.",
                _keyboard([[{"text": "🏆 Contest Settings", "callback_data": f"contestsettings:{target_chat_id}"}]]),
            )
        try:
            _send(target_chat_id, _contest_announcement(app, target_chat_id))
            result = "📣 Contest announcement posted."
        except Exception:
            result = "I couldn't post in that chat. Check that the bot has permission to send messages."
        return _edit(
            chat_id,
            message_id,
            result,
            _keyboard([[{"text": "🏆 Contest Settings", "callback_data": f"contestsettings:{target_chat_id}"}]]),
        )

    if data.startswith("contestboard:"):
        try:
            target_chat_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        return _show_contest_leaderboard(app, target_chat_id, chat_id, message_id)

    parts = data.split(":")
    action = parts[0]
    if len(parts) < 2:
        return
    try:
        target_chat_id = int(parts[1])
    except ValueError:
        return

    if action == "chat":
        if not _is_member(target_chat_id, user["id"]):
            return _edit(chat_id, message_id, "You are no longer a member of that chat.")
        title = _chat_title(app, target_chat_id)
        return _edit(
            chat_id,
            message_id,
            f"📍 {title}\n\nChoose an option:",
            _chat_menu(target_chat_id, _is_admin(target_chat_id, user["id"])),
        )

    if action == "mystats":
        return _show_my_stats(app, user["id"], target_chat_id, chat_id, message_id)

    if action == "link":
        return _show_link(app, user["id"], target_chat_id, chat_id, message_id)

    if action == "leader":
        active_only = len(parts) < 3 or parts[2] != "all"
        return _show_leaderboard(
            app,
            target_chat_id,
            chat_id,
            edit_message_id=message_id,
            active_only=active_only,
        )

    if action == "adminstats":
        return _show_admin_stats(
            app,
            target_chat_id,
            chat_id,
            user["id"],
            message_id,
        )

    if action == "invitees":
        refs = _referrals_for(app, target_chat_id, user["id"])[:20]
        title = _chat_title(app, target_chat_id)
        if not refs:
            text = f"👥 Your invitees — {title}\n\nNo referrals recorded yet."
        else:
            lines = [f"👥 Your latest invitees — {title}", ""]
            for index, row in enumerate(refs, start=1):
                status = "✅ Active" if bool(row.get("Active")) else "❌ Left"
                method = "🔗 Link" if row.get("Method") == "INVITE_LINK" else "➕ Direct"
                lines.append(
                    f"{index}. {_user_name(app, row['InviteeID'])} — {status} — {method}"
                )
            text = "\n".join(lines)
        return _edit(
            chat_id,
            message_id,
            text,
            _keyboard(
                [[{"text": "◀ Chat Menu", "callback_data": f"chat:{target_chat_id}"}]]
            ),
        )


def _handle_chat_member(app, change: dict):
    chat = change.get("chat") or {}
    member_info = change.get("new_chat_member") or {}
    old_info = change.get("old_chat_member") or {}
    member = member_info.get("user") or {}
    actor = change.get("from")

    _upsert_chat(app, chat)
    _upsert_user(app, member)
    _upsert_user(app, actor)

    if member.get("is_bot"):
        return

    old_status = old_info.get("status")
    new_status = member_info.get("status")
    joined_states = {"member", "administrator", "creator", "restricted"}
    left_states = {"left", "kicked"}

    joined = old_status in left_states and new_status in joined_states
    left = old_status in joined_states and new_status in left_states

    if left:
        _mark_left(app, chat["id"], member["id"])
        _contest_mark_left(app, chat["id"], member["id"])
        return
    if not joined:
        return

    inviter_id = None
    method = None
    invite_link = change.get("invite_link")
    if invite_link and invite_link.get("invite_link"):
        inviter_id = _find_link_owner(app, chat["id"], invite_link["invite_link"])
        if inviter_id:
            method = "INVITE_LINK"

    if (
        not inviter_id
        and actor
        and actor.get("id") != member.get("id")
        and not actor.get("is_bot")
    ):
        inviter_id = str(actor["id"])
        method = "DIRECT_ADD"

    if inviter_id and str(inviter_id) != str(member["id"]):
        is_new = _record_referral(
            app,
            chat["id"],
            member["id"],
            inviter_id,
            method,
        )
        if is_new:
            _contest_record_referral(
                app,
                chat["id"],
                member["id"],
                inviter_id,
            )


def _handle_my_chat_member(app, change: dict):
    chat = change.get("chat") or {}
    actor = change.get("from")
    new_member = change.get("new_chat_member") or {}
    old_member = change.get("old_chat_member") or {}

    _upsert_chat(app, chat)
    _upsert_user(app, actor)

    new_status = new_member.get("status")
    old_status = old_member.get("status")
    if new_status not in {"member", "administrator", "creator"}:
        return
    if new_status == old_status:
        return

    chat_id = chat.get("id")
    if not chat_id:
        return

    username = _bot_username()
    setup_url = f"https://t.me/{username}?start=setup_{chat_id}"

    if new_status in {"administrator", "creator"}:
        can_invite = bool(new_member.get("can_invite_users", True))
        if can_invite:
            text = (
                "✅ Inviter Tracker is ready!\n\n"
                "I can now track direct adds and personal invite links.\n"
                "👋 Welcome messages are ON by default.\n"
                "🏆 Contest tools are ready whenever you need them.\n\n"
                "Admin: tap below to finish/customize setup."
            )
        else:
            text = (
                "⚠️ Almost ready.\n\n"
                "Please give me the Invite Users / Manage Invite Links permission so I can create personal invite links.\n\n"
                "Then tap below to finish setup."
            )
    else:
        text = (
            "👋 Inviter Tracker has been added.\n\n"
            "To enable invite tracking and personal invite links, promote me to admin and allow Invite Users / Manage Invite Links."
        )

    try:
        _send(
            chat_id,
            text,
            _keyboard([[{"text": "⚙️ Complete Setup", "url": setup_url}]]),
        )
    except Exception:
        pass


def _process_update(app, update: dict):
    if "message" in update:
        return _handle_message(app, update["message"])
    if "channel_post" in update:
        return _handle_message(app, update["channel_post"])
    if "callback_query" in update:
        return _handle_callback(app, update["callback_query"])
    if "chat_member" in update:
        return _handle_chat_member(app, update["chat_member"])
    if "my_chat_member" in update:
        return _handle_my_chat_member(app, update["my_chat_member"])


def handler(request: Request):
    try:
        if request.method == "GET":
            return jsonify({
                "ok": True,
                "service": "TG Invite Tracker webhook",
                "bot_token_configured": bool(BOT_TOKEN),
            }), 200

        if request.method != "POST":
            return make_response("Method not allowed", 405)

        if not BOT_TOKEN:
            logger.error("BOT_TOKEN environment variable is missing.")
            return make_response("Server configuration error", 500)

        app = zcatalyst_sdk.initialize()
        update = request.get_json(silent=True) or {}
        _process_update(app, update)
        return jsonify({"ok": True}), 200
    except Exception as exc:
        logger.exception("Webhook processing failed: %s", exc)
        # Return 200 so Telegram does not continuously replay a poison update.
        return jsonify({"ok": False, "error": str(exc)}), 200
