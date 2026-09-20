import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.config import get_settings
from app.database import init_db
from app.handlers.commands import callbacks, leaderboard, link, myinvites, start, stats
from app.handlers.members import announce_join_event, track_bot_membership, track_membership

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await init_db()
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Open the interactive dashboard"),
            BotCommand("link", "Get your personal invite link"),
            BotCommand("myinvites", "View your personal invite stats"),
            BotCommand("leaderboard", "View the invite leaderboard"),
            BotCommand("stats", "Admin: view overall chat stats"),
        ]
    )
    logger.info("Database initialized and bot commands registered.")


def build_application() -> Application:
    settings = get_settings()

    app = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CommandHandler("myinvites", myinvites))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(callbacks))

    # Register the exact group/channel where the bot itself is added.
    app.add_handler(
        ChatMemberHandler(
            track_bot_membership,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

    # Authoritative membership/referral attribution for other users.
    app.add_handler(
        ChatMemberHandler(
            track_membership,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    # Reply to Telegram's visible group join/add service event.
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            announce_join_event,
        )
    )

    return app


def main() -> None:
    application = build_application()
    logger.info("TG Invite Tracker starting...")
    application.run_polling(
        allowed_updates=[
            Update.MESSAGE,
            Update.CHANNEL_POST,
            Update.CALLBACK_QUERY,
            Update.CHAT_MEMBER,
            Update.MY_CHAT_MEMBER,
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
