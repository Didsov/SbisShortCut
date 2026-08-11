from decoder import SBISDecoder
from parsers.kkt import extract_ofd_end_date
from sbis_client import SBISClient
from services.accounts import get_contractor_accounts
from services.contractor import get_contractor_by_inn
from services.kkt import get_kkt
from services.registry import get_all_kkt_registry


def _account_id(account: dict) -> int | None:
    value = account.get("AccountId") or (account.get("AccountInfo") or {}).get(
        "AccountId"
    )
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def collect_kkt_by_inn(inn: str, kpp: str | None = None) -> dict:
    """Получает ККТ из СБИС без чтения и записи локальной базы."""
    result = {
        "inn": inn,
        "kpp": kpp,
        "accounts": [],
        "kkt": [],
        "errors": [],
        "skipped_foreign_owner": 0,
    }
    seen_reg_numbers: set[str] = set()
    decoder = SBISDecoder()

    with SBISClient() as client:
        contractor = get_contractor_by_inn(inn=inn, kpp=kpp, client=client)
        contractor_id = contractor.get("@Лицо")
        if not contractor_id:
            raise LookupError(f"Для ИНН {inn} не найден Billing ContractorId")
        accounts = get_contractor_accounts(
            contractor_id=contractor_id,
            only_active=True,
            client=client,
        )
        result["accounts"] = accounts

        for account in accounts:
            account_id = _account_id(account)
            if account_id is None:
                result["errors"].append(
                    {"stage": "AccountContractor.List", "error": "Нет AccountId"}
                )
                continue

            try:
                registry = get_all_kkt_registry(
                    account_id=account_id,
                    limit=25,
                    include_archived=False,
                    max_pages=200,
                    retry_attempts=3,
                    client=client,
                )
            except Exception as error:
                result["errors"].append(
                    {
                        "stage": "RegKKT.Registry",
                        "account_id": account_id,
                        "error": str(error),
                    }
                )
                continue

            for registry_item in registry:
                if not isinstance(registry_item, dict):
                    continue
                kkt_id = registry_item.get("KKTId")
                reg_number = str(registry_item.get("KKTRegId") or "").strip()
                if kkt_id is None or not reg_number or reg_number in seen_reg_numbers:
                    continue
                seen_reg_numbers.add(reg_number)

                try:
                    detail = get_kkt(
                        account_id=account_id,
                        kkt_id=int(kkt_id),
                        kkt_reg_id=reg_number,
                        client=client,
                    )
                    if not isinstance(detail, dict):
                        detail = decoder.decode(detail)
                    owner_inn = str(
                        detail.get("ИНН") or registry_item.get("INN") or ""
                    ).strip()
                    if owner_inn != inn:
                        result["skipped_foreign_owner"] += 1
                        continue
                    result["kkt"].append(
                        {
                            "account_id": account_id,
                            "sales_point_address": detail.get("salespoint_address")
                            or detail.get("АдресФактический")
                            or detail.get("Адрес")
                            or registry_item.get("Address"),
                            "kkt_inn": owner_inn,
                            "registry": registry_item,
                            "kkt": detail,
                            "fn_end_date": detail.get("FSEndDate")
                            or (registry_item.get("LicenseData") or {}).get(
                                "finish_fs_day"
                            ),
                            "ofd_end_date": extract_ofd_end_date(
                                registry=registry_item,
                                detail=detail,
                            ),
                        }
                    )
                except Exception as error:
                    result["errors"].append(
                        {
                            "stage": "KKT.Read",
                            "account_id": account_id,
                            "reg_number": reg_number,
                            "error": str(error),
                        }
                    )

    return result
