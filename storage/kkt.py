import json
from contextlib import closing
from datetime import datetime

from parsers.kkt import extract_ofd_end_date
from storage.database import get_connection


def get_missing_reg_numbers(reg_numbers: set[str]) -> set[str]:
    """Возвращает РНМ, для которых в базе ещё нет сохранённой ККТ."""
    normalized = {
        str(reg_number).strip()
        for reg_number in reg_numbers
        if str(reg_number).strip()
    }

    if not normalized:
        return set()

    found: set[str] = set()
    values = sorted(normalized)

    with closing(get_connection()) as connection:
        for offset in range(0, len(values), 500):
            chunk = values[offset:offset + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT reg_number
                FROM kkt
                WHERE reg_number IN ({placeholders})
                  AND raw_json IS NOT NULL
                  AND TRIM(raw_json) NOT IN ('', '{{}}')
                """,
                chunk,
            ).fetchall()
            found.update(
                str(row["reg_number"]).strip()
                for row in rows
                if row["reg_number"]
            )

    return normalized - found


def save_kkt(item: dict) -> None:
    registry = item.get("registry") or {}
    detail = item.get("kkt") or {}

    reg_number = (
        detail.get("НомерРегистрационный")
        or registry.get("KKTRegId")
    )

    # Кассы без РНМ не сохраняем.
    if not reg_number:
        return

    reg_number = str(reg_number).strip()

    if not reg_number:
        return

    license_data = (
        registry.get("LicenseData")
        or {}
    )

    used_for = detail.get("used_for") or {}

    parsed_at = datetime.now().isoformat(
        timespec="seconds"
    )

    values = {
        "source_client_inn": item.get(
            "source_client_inn"
        ),
        "source_client_kpp": item.get(
            "source_client_kpp"
        ),
        "source_contractor_id": item.get(
            "source_contractor_id"
        ),

        "account_id": item.get("account_id"),
        "account_name": item.get("account_name"),

        "kkt_contractor_id": (
            item.get("kkt_contractor_id")
            or registry.get("Contragent")
        ),

        "kkt_id": (
            detail.get("@ККМ")
            or registry.get("KKTId")
        ),

        "reg_number": reg_number,

        "manufacturer_number": detail.get(
            "НомерПроизводителя"
        ),

        "fn_number": (
            used_for.get("old_fn")
            or detail.get("number")
        ),

        "kkt_name": (
            detail.get("НазваниеККМ")
            or registry.get("KKTName")
        ),

        "model": (
            detail.get("ОборудованиеНазвание")
            or registry.get("KKTName")
        ),

        "organization": (
            detail.get("Название")
            or registry.get("Название")
        ),

        "inn": (
            item.get("kkt_inn")
            or registry.get("INN")
            or detail.get("ИНН")
        ),

        "kpp": (
            item.get("kkt_kpp")
            or registry.get("KPP")
            or detail.get("КПП")
        ),

        "address": (
            detail.get("salespoint_address")
            or detail.get("АдресФактический")
            or detail.get("Адрес")
            or registry.get("Address")
        ),

        "active": (
            detail.get("Действующая")
            if detail.get("Действующая") is not None
            else registry.get("Active")
        ),

        "status": (
            detail.get("СтатусРегистрацииФНС")
            if detail.get("СтатусРегистрацииФНС") is not None
            else registry.get("Status")
        ),

        "fn_end_date": (
            item.get("fn_end_date")
            or detail.get("FSEndDate")
            or license_data.get("finish_fs_day")
        ),

        "ofd_end_date": (
            item.get("ofd_end_date")
            or extract_ofd_end_date(
                registry=registry,
                detail=detail,
            )
        ),

        "raw_json": json.dumps(
            {
                "registry": registry,
                "detail": detail,
            },
            ensure_ascii=False,
            default=str,
        ),

        "parsed_at": parsed_at,
        "updated_at": parsed_at,
    }

    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            INSERT INTO kkt (
                source_client_inn,
                source_client_kpp,
                source_contractor_id,

                account_id,
                account_name,
                kkt_contractor_id,

                kkt_id,
                reg_number,
                manufacturer_number,
                fn_number,
                kkt_name,
                model,
                organization,
                inn,
                kpp,
                address,
                active,
                status,
                fn_end_date,
                ofd_end_date,
                raw_json,
                parsed_at,
                updated_at
            )
            VALUES (
                :source_client_inn,
                :source_client_kpp,
                :source_contractor_id,

                :account_id,
                :account_name,
                :kkt_contractor_id,

                :kkt_id,
                :reg_number,
                :manufacturer_number,
                :fn_number,
                :kkt_name,
                :model,
                :organization,
                :inn,
                :kpp,
                :address,
                :active,
                :status,
                :fn_end_date,
                :ofd_end_date,
                :raw_json,
                :parsed_at,
                :updated_at
            )
            ON CONFLICT(reg_number) DO UPDATE SET
                source_client_inn =
                    excluded.source_client_inn,
                source_client_kpp =
                    excluded.source_client_kpp,
                source_contractor_id =
                    excluded.source_contractor_id,

                account_id = excluded.account_id,
                account_name = excluded.account_name,
                kkt_contractor_id =
                    excluded.kkt_contractor_id,

                kkt_id = excluded.kkt_id,
                manufacturer_number =
                    excluded.manufacturer_number,
                fn_number = excluded.fn_number,
                kkt_name = excluded.kkt_name,
                model = excluded.model,
                organization = excluded.organization,
                inn = excluded.inn,
                kpp = excluded.kpp,
                address = excluded.address,
                active = excluded.active,
                status = excluded.status,
                fn_end_date = excluded.fn_end_date,
                ofd_end_date = CASE
                    WHEN excluded.ofd_end_date IS NOT NULL
                         AND TRIM(excluded.ofd_end_date) <> ''
                    THEN excluded.ofd_end_date
                    ELSE kkt.ofd_end_date
                END,
                raw_json = excluded.raw_json,
                parsed_at = excluded.parsed_at,
                updated_at = excluded.updated_at
            """,
            values,
        )
