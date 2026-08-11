from datetime import datetime
from contextlib import closing

from storage.database import get_connection


def normalize_kpp(kpp: str | None) -> str:
    return (kpp or "").strip()


def save_client(client: dict) -> None:
    inn = (client.get("inn") or "").strip()
    kpp = normalize_kpp(client.get("kpp"))

    if not inn:
        raise ValueError("Нельзя сохранить клиента без ИНН")

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            INSERT INTO clients (
                crm_client_id,
                crm_person_id,
                name,
                inn,
                kpp,
                address,
                parse_status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(inn, kpp) DO UPDATE SET
                crm_client_id = COALESCE(excluded.crm_client_id, clients.crm_client_id),
                crm_person_id = COALESCE(excluded.crm_person_id, clients.crm_person_id),
                name = COALESCE(excluded.name, clients.name),
                address = COALESCE(excluded.address, clients.address)
            """,
            (
                client.get("id"),
                client.get("person_id"),
                client.get("name"),
                inn,
                kpp,
                client.get("address"),
            ),
        )


def is_client_done(
    inn: str,
    kpp: str | None,
) -> bool:
    kpp = normalize_kpp(kpp)

    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM clients
            WHERE inn = ?
              AND kpp = ?
              AND parse_status = 'done'
            LIMIT 1
            """,
            (inn, kpp),
        ).fetchone()

    return row is not None


def get_client_status(inn: str, kpp: str | None) -> str | None:
    kpp = normalize_kpp(kpp)
    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT parse_status
            FROM clients
            WHERE inn = ? AND kpp = ?
            LIMIT 1
            """,
            (inn, kpp),
        ).fetchone()
    return row["parse_status"] if row is not None else None


def mark_client_not_found(
    inn: str,
    kpp: str | None,
    error: str,
) -> None:
    kpp = normalize_kpp(kpp)
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE clients
            SET parse_status = 'not_found',
                parsed_at = ?,
                error = ?
            WHERE inn = ? AND kpp = ?
            """,
            (datetime.now().isoformat(timespec="seconds"), error, inn, kpp),
        )


def mark_client_done(
    inn: str,
    kpp: str | None,
    contractor_id: int,
) -> None:
    kpp = normalize_kpp(kpp)

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE clients
            SET
                billing_contractor_id = ?,
                parse_status = 'done',
                parsed_at = ?,
                error = NULL
            WHERE inn = ?
              AND kpp = ?
            """,
            (
                contractor_id,
                datetime.now().isoformat(
                    timespec="seconds"
                ),
                inn,
                kpp,
            ),
        )


def mark_client_error(
    inn: str,
    kpp: str | None,
    error: str,
) -> None:
    kpp = normalize_kpp(kpp)

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE clients
            SET
                parse_status = 'error',
                parsed_at = ?,
                error = ?
            WHERE inn = ?
              AND kpp = ?
            """,
            (
                datetime.now().isoformat(
                    timespec="seconds"
                ),
                error,
                inn,
                kpp,
            ),
        )


def get_client_counts() -> dict:
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                parse_status,
                COUNT(*) AS count
            FROM clients
            GROUP BY parse_status
            """
        ).fetchall()

    result = {
        "pending": 0,
        "done": 0,
        "error": 0,
    }

    for row in rows:
        status = row["parse_status"]

        if status:
            result[status] = row["count"]

    return result
