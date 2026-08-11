from datetime import datetime
from contextlib import closing

from storage.database import get_connection


def save_error(
    *,
    stage: str,
    error: str,
    inn: str | None = None,
    account_id: int | None = None,
    kkt_id: int | None = None,
) -> None:
    """
    Сохраняет ошибку обработки в таблицу parse_errors.
    """

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            INSERT INTO parse_errors (
                inn,
                account_id,
                kkt_id,
                stage,
                error,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                inn,
                account_id,
                kkt_id,
                stage,
                error,
                datetime.now().isoformat(
                    timespec="seconds"
                ),
            ),
        )


def get_last_errors(
    limit: int = 100,
) -> list[dict]:
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                inn,
                account_id,
                kkt_id,
                stage,
                error,
                created_at
            FROM parse_errors
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]
