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

logger = logging.getLogger()


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
    if not chat:
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
    return _api("getMe").get("username")


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
    return _select(app, CHATS)


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
        "Anyone joining through this link is credited to your Telegram user ID."
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
        text = title + "\n\n" + "\n".join(lines)

    markup = _keyboard(
        [
            [
                {"text": "✅ Active", "callback_data": f"leader:{chat_id}:active"},
                {"text": "📚 All Time", "callback_data": f"leader:{chat_id}:all"},
            ],
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
    )
    markup = _keyboard(
        [
            [{"text": "🏆 Leaderboard", "callback_data": f"leader:{chat_id}:active"}],
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
        return _mark_left(app, chat["id"], member["id"])
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
        _record_referral(
            app,
            chat["id"],
            member["id"],
            inviter_id,
            method,
        )


def _handle_my_chat_member(app, change: dict):
    _upsert_chat(app, change.get("chat"))
    _upsert_user(app, change.get("from"))


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
            return jsonify({"ok": True, "service": "TG Invite Tracker webhook"}), 200

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
