import json
from contextlib import closing
from datetime import datetime

from storage.database import get_connection

from storage.database import get_connection


def contractor_exists(
    inn: str,
) -> bool:

    with closing(get_connection()) as conn:

        row = conn.execute(
            """
            SELECT 1
            FROM contractors
            WHERE inn = ?
            LIMIT 1
            """,
            (inn,),
        ).fetchone()

    return row is not None

def save_contractor(
    contractor: dict,
):
    """
    Сохраняет карточку организации.
    Один ИНН = одна запись.
    """
    inn = contractor.get("inn")

    if not inn:
        raise ValueError(
            "Нельзя сохранить contractor без ИНН"
        )
    with closing(get_connection()) as conn, conn:

        conn.execute(
            """
            INSERT INTO contractors (
                contractor_id,
                inn,
                kpp,
                name,
                legal_address,
                region,
                account_id,
                raw_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(inn)
            DO UPDATE SET

                contractor_id = excluded.contractor_id,
                kpp = excluded.kpp,
                name = excluded.name,
                legal_address = excluded.legal_address,
                region = excluded.region,
                account_id = excluded.account_id,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                contractor.get("contractor_id"),
                contractor.get("inn"),
                contractor.get("kpp"),
                contractor.get("name"),
                contractor.get("legal_address"),
                contractor.get("region"),
                contractor.get("account_id"),
                json.dumps(
                    contractor,
                    ensure_ascii=False
                ),
                datetime.now().isoformat(),
            ),
        )
