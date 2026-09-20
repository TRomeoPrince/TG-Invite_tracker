from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing. Copy .env.example to .env and set it.")

    database_url = os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./invite_tracker.db",
    ).strip()

    return Settings(bot_token=bot_token, database_url=database_url)
