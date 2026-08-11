import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _user_ids(value: str) -> frozenset[int]:
    result: set[int] = set()
    for item in re.split(r"[,;\s]+", value.strip()):
        if not item:
            continue
        user_id = int(item)
        if user_id <= 0:
            raise ValueError("Telegram User ID должен быть положительным")
        result.add(user_id)
    return frozenset(result)


def _path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    admin_user_ids: frozenset[int]
    sbis_cookies: str
    database_path: Path
    whitelist_path: Path
    log_path: Path | None


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    cookies = os.environ.get("SBIS_COOKIES", "").strip()
    admins = _user_ids(os.environ.get("TELEGRAM_ADMIN_USER_IDS", ""))
    log_value = os.environ.get("KKT_BOT_LOG_PATH", "").strip()

    if not token:
        raise RuntimeError("В .env не задан TELEGRAM_BOT_TOKEN")
    if not admins:
        raise RuntimeError("В .env не заданы TELEGRAM_ADMIN_USER_IDS")
    if not cookies:
        raise RuntimeError("В .env не задан SBIS_COOKIES")

    return Settings(
        telegram_token=token,
        admin_user_ids=admins,
        sbis_cookies=cookies,
        database_path=_path("KKT_DATABASE", "data/kkt.db"),
        whitelist_path=_path(
            "TELEGRAM_WHITELIST_PATH", "data/whitelist.json"
        ),
        log_path=_path("KKT_BOT_LOG_PATH", log_value) if log_value else None,
    )

