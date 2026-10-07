from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UnknownUser:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    first_seen_at: str
    last_seen_at: str
    message_count: int

    @property
    def name(self) -> str:
        parts = [
            str(value).strip()
            for value in (self.first_name, self.last_name)
            if value and str(value).strip()
        ]

        if parts:
            return " ".join(parts)

        if self.username:
            return f"@{self.username}"

        return str(self.user_id)

    @property
    def username_text(self) -> str:
        if not self.username:
            return ""
        return f"@{self.username}"


class AccessMonitor:
    """
    Независимый журнал пользователей, которые обращались к боту,
    не находясь в whitelist.

    Ошибки этого модуля не должны останавливать основной бот.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.enabled = True

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
        except Exception:
            self.enabled = False
            logger.exception(
                "Не удалось инициализировать AccessMonitor; "
                "мониторинг неизвестных пользователей отключён"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS unknown_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 1
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_unknown_users_last_seen
                ON unknown_users(last_seen_at DESC)
                """
            )
            connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                action TEXT NOT NULL,
                request_text TEXT,
                created_at TEXT NOT NULL,
                success INTEGER,
                duration_ms INTEGER,
                details TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_user_actions_user_created
            ON user_actions(user_id, created_at DESC)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_user_actions_created
            ON user_actions(created_at DESC)
            """
        )


    def record_action(
        self,
        message: Message,
        *,
        action: str = "message",
        success: bool | None = None,
        duration_ms: int | None = None,
        details: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        sender = message.from_user
        if sender is None:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_actions (
                        user_id,
                        username,
                        first_name,
                        last_name,
                        action,
                        request_text,
                        created_at,
                        success,
                        duration_ms,
                        details
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sender.id,
                        sender.username,
                        sender.first_name,
                        sender.last_name,
                        action,
                        message.text,
                        now,
                        None if success is None else int(success),
                        duration_ms,
                        details,
                    ),
                )
        except Exception:
            logger.exception(
                "Не удалось записать действие пользователя %s",
                sender.id,
            )
    def record(self, message: Message) -> None:
        """
        Зафиксировать обращение неизвестного пользователя.

        Fail-open: ошибка мониторинга не распространяется в основной бот.
        """
        if not self.enabled:
            return

        sender = message.from_user
        if sender is None:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO unknown_users (
                        user_id,
                        username,
                        first_name,
                        last_name,
                        first_seen_at,
                        last_seen_at,
                        message_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1)

                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        first_name = excluded.first_name,
                        last_name = excluded.last_name,
                        last_seen_at = excluded.last_seen_at,
                        message_count = unknown_users.message_count + 1
                    """,
                    (
                        sender.id,
                        sender.username,
                        sender.first_name,
                        sender.last_name,
                        now,
                        now,
                    ),
                )
        except Exception:
            logger.exception(
                "Не удалось записать обращение неизвестного пользователя %s",
                sender.id,
            )

    def recent_unknown(
        self,
        is_allowed: Callable[[int], bool],
        limit: int = 10,
    ) -> list[UnknownUser]:
        """
        Последние пользователи, которые всё ещё не находятся в whitelist.
        """
        if not self.enabled:
            return []

        try:
            # Берём с запасом, потому что часть ранее неизвестных
            # пользователей уже могла быть добавлена в whitelist.
            fetch_limit = max(limit * 10, 100)

            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        user_id,
                        username,
                        first_name,
                        last_name,
                        first_seen_at,
                        last_seen_at,
                        message_count
                    FROM unknown_users
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (fetch_limit,),
                ).fetchall()

            result: list[UnknownUser] = []

            for row in rows:
                user_id = int(row["user_id"])

                try:
                    if is_allowed(user_id):
                        continue
                except Exception:
                    # Ошибка whitelist не должна ломать /allow.
                    continue

                result.append(
                    UnknownUser(
                        user_id=user_id,
                        username=row["username"],
                        first_name=row["first_name"],
                        last_name=row["last_name"],
                        first_seen_at=row["first_seen_at"],
                        last_seen_at=row["last_seen_at"],
                        message_count=int(row["message_count"]),
                    )
                )

                if len(result) >= limit:
                    break

            return result

        except Exception:
            logger.exception(
                "Не удалось получить последних неизвестных пользователей"
            )
            return []

class UnknownUserMiddleware(BaseMiddleware):
    def __init__(
        self,
        monitor: AccessMonitor,
        is_allowed: Callable[[int], bool],
    ) -> None:
        self.monitor = monitor
        self.is_allowed = is_allowed

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:

        try:
            sender = event.from_user

            if sender is not None:
                if self.is_allowed(sender.id):
                    self.monitor.record_action(
                        event,
                        action=self._detect_action(event),
                    )
                else:
                    self.monitor.record(event)

        except Exception:
            logger.exception("Ошибка AccessMonitor middleware")

        return await handler(event, data)

    @staticmethod
    def _detect_action(message: Message) -> str:
        text = (message.text or "").strip()

        if not text:
            return "message"

        if text.startswith("/"):
            return text.split(maxsplit=1)[0].lower()

        return "search"
def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)

        # Показываем в локальном часовом поясе сервера.
        local = parsed.astimezone()

        return local.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return value


def format_recent_unknown(users: list[UnknownUser]) -> str:
    if not users:
        return "Нет последних пользователей вне белого списка."

    lines = [
        "Последние пользователи вне белого списка:",
        "",
    ]

    for index, user in enumerate(users, 1):
        username = (
            f" · @{user.username}"
            if user.username
            else ""
        )

        lines.extend(
            [
                f"{index}. {user.name}{username}",
                f"ID: <code>{user.user_id}</code>",
                f"Последний раз: {_format_time(user.last_seen_at)}",
                f"Обращений: {user.message_count}",
                "",
            ]
        )

    return "\n".join(lines).rstrip()


def recent_unknown_keyboard(
    users: list[UnknownUser],
) -> InlineKeyboardMarkup | None:

    if not users:
        return None

    rows: list[list[InlineKeyboardButton]] = []

    for user in users:
        label = user.name

        if user.username and f"@{user.username}" not in label:
            label = f"{label} (@{user.username})"

        # Telegram-кнопку не раздуваем слишком длинным именем.
        if len(label) > 36:
            label = label[:33] + "..."

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"➕ {label}",
                    callback_data=f"allow_recent:{user.user_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def record_result(
    self,
    user_id: int,
    *,
    action: str,
    success: bool,
    duration_ms: int | None = None,
    details: str | None = None,
) -> None:
    ...