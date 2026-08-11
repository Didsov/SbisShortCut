import os
import re
import socket
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from storage import database
from storage.database import get_connection


HOST_ID = socket.gethostname().strip().lower() or "unknown-host"
WORKER_ID = f"{HOST_ID}-pid-{os.getpid()}-{uuid.uuid4().hex[:8]}"
WORKER_PATTERN = re.compile(
    r"^(?:(?P<host>.+)-)?pid-(?P<pid>\d+)-[0-9a-f]+$",
    re.IGNORECASE,
)

# При запуске источника с --force каждый клиент должен быть повторён, однако
# один AccountId может входить в десятки контрагентов. После успешного полного
# пересбора аккаунта не открываем его снова в том же процессе.
_FORCE_COMPLETED_ACCOUNTS: set[tuple[str, int]] = set()
_FORCE_IN_PROGRESS_ACCOUNTS: set[tuple[str, int]] = set()


def _account_scope_key(account_id: int) -> tuple[str, int]:
    return (str(database.DB_PATH.resolve()), account_id)


def worker_process_is_alive(worker_id: str | None) -> bool:
    """Проверяет локальный PID; чужой компьютер считается недоступным."""
    match = WORKER_PATTERN.match(str(worker_id or "").strip())
    if match is None:
        return True

    worker_host = (match.group("host") or HOST_ID).strip().lower()
    if worker_host != HOST_ID:
        return True

    try:
        os.kill(int(match.group("pid")), 0)
    except (OSError, ProcessLookupError, ValueError):
        return False
    return True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AccountClaim:
    state: str
    completed_at: str | None = None

    @property
    def claimed(self) -> bool:
        return self.state == "claimed"


def claim_account(
    *,
    account_id: int,
    billing_contractor_id: int,
    source_inn: str,
    account_name: str | None,
    lease_seconds: int,
    force_reprocess: bool = False,
    allow_error_retry: bool = False,
) -> AccountClaim:
    """Атомарно закрепляет аккаунт за текущим процессом."""
    now = utc_now()
    now_text = timestamp(now)
    lease_until = timestamp(now + timedelta(seconds=max(60, lease_seconds)))

    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT status, completed_at, worker_id, lease_until
            FROM account_scans
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

        if row is not None:
            if (
                force_reprocess
                and row["status"] == "done"
                and _account_scope_key(account_id)
                in _FORCE_COMPLETED_ACCOUNTS
            ):
                return AccountClaim("done", row["completed_at"])

            if row["status"] == "done" and not force_reprocess:
                return AccountClaim("done", row["completed_at"])

            if (
                row["status"] == "error"
                and row["worker_id"] == WORKER_ID
                and not allow_error_retry
                and not force_reprocess
            ):
                return AccountClaim("deferred")

            lease_is_active = bool(
                row["status"] == "processing"
                and row["worker_id"] != WORKER_ID
                and row["lease_until"]
                and row["lease_until"] > now_text
                and worker_process_is_alive(row["worker_id"])
            )

            if lease_is_active and not force_reprocess:
                return AccountClaim("busy")

        connection.execute(
            """
            INSERT INTO account_scans (
                account_id,
                billing_contractor_id,
                source_inn,
                account_name,
                status,
                started_at,
                completed_at,
                updated_at,
                worker_id,
                lease_until,
                registry_kkt_count,
                saved_kkt_count,
                error
            )
            VALUES (?, ?, ?, ?, 'processing', ?, NULL, ?, ?, ?, 0, 0, NULL)
            ON CONFLICT(account_id) DO UPDATE SET
                billing_contractor_id = excluded.billing_contractor_id,
                source_inn = excluded.source_inn,
                account_name = COALESCE(excluded.account_name, account_scans.account_name),
                status = 'processing',
                started_at = excluded.started_at,
                completed_at = NULL,
                updated_at = excluded.updated_at,
                worker_id = excluded.worker_id,
                lease_until = excluded.lease_until,
                registry_kkt_count = 0,
                saved_kkt_count = 0,
                error = NULL
            """,
            (
                account_id,
                billing_contractor_id,
                source_inn,
                account_name,
                now_text,
                now_text,
                WORKER_ID,
                lease_until,
            ),
        )

    if force_reprocess:
        _FORCE_IN_PROGRESS_ACCOUNTS.add(_account_scope_key(account_id))

    return AccountClaim("claimed")


def mark_account_done(
    *,
    account_id: int,
    registry_kkt_count: int,
    saved_kkt_count: int,
) -> None:
    now_text = timestamp()

    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE account_scans
            SET status = 'done',
                completed_at = ?,
                updated_at = ?,
                worker_id = NULL,
                lease_until = NULL,
                registry_kkt_count = ?,
                saved_kkt_count = ?,
                error = NULL
            WHERE account_id = ?
              AND worker_id = ?
            """,
            (
                now_text,
                now_text,
                max(0, registry_kkt_count),
                max(0, saved_kkt_count),
                account_id,
                WORKER_ID,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Не удалось подтвердить завершение AccountId={account_id}: "
                "аккаунт закреплён за другим процессом"
            )

    scope_key = _account_scope_key(account_id)
    if scope_key in _FORCE_IN_PROGRESS_ACCOUNTS:
        _FORCE_IN_PROGRESS_ACCOUNTS.discard(scope_key)
        _FORCE_COMPLETED_ACCOUNTS.add(scope_key)


def renew_account_claim(
    *,
    account_id: int,
    lease_seconds: int,
) -> None:
    now = utc_now()

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE account_scans
            SET updated_at = ?,
                lease_until = ?
            WHERE account_id = ?
              AND status = 'processing'
              AND worker_id = ?
            """,
            (
                timestamp(now),
                timestamp(now + timedelta(seconds=max(60, lease_seconds))),
                account_id,
                WORKER_ID,
            ),
        )


def mark_account_error(
    *,
    account_id: int,
    error: str,
    registry_kkt_count: int = 0,
    saved_kkt_count: int = 0,
) -> None:
    now_text = timestamp()

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE account_scans
            SET status = 'error',
                updated_at = ?,
                lease_until = NULL,
                registry_kkt_count = ?,
                saved_kkt_count = ?,
                error = ?
            WHERE account_id = ?
              AND worker_id = ?
            """,
            (
                now_text,
                max(0, registry_kkt_count),
                max(0, saved_kkt_count),
                str(error)[:2000],
                account_id,
                WORKER_ID,
            ),
        )


def get_account_scan_counts() -> dict[str, int]:
    result = {
        "processing": 0,
        "done": 0,
        "error": 0,
    }

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM account_scans
            GROUP BY status
            """
        ).fetchall()

    for row in rows:
        result[row["status"]] = row["count"]

    return result
