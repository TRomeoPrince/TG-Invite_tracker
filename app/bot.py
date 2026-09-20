import logging

from telegram import Update
from telegram.ext import Application, ChatMemberHandler, CommandHandler

from app.config import get_settings
from app.database import init_db
from app.handlers.commands import leaderboard, link, myinvites, start
from app.handlers.members import track_membership

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
    app.add_handler(ChatMemberHandler(track_membership, ChatMemberHandler.CHAT_MEMBER))

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
