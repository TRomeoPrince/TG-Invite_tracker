import logging

from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.config import get_settings
from app.database import init_db
from app.handlers.commands import leaderboard, link, myinvites, start
from app.handlers.members import announce_join_event, track_membership

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await init_db()
    logger.info("Database initialized.")


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

    # Authoritative referral attribution.
    app.add_handler(
        ChatMemberHandler(
            track_membership,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    # User-facing confirmation, attached directly to Telegram's native join/add
    # service message so it is obvious which event was recorded.
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
        allowed_updates=[Update.MESSAGE, Update.CHAT_MEMBER],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
