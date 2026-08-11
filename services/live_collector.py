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


def _account_name(account: dict) -> str | None:
    account_info = account.get("AccountInfo") or {}
    return (
        account.get("AccountName")
        or account.get("Name")
        or account.get("Название")
        or account_info.get("Name")
        or account_info.get("Название")
    )


def _log_account(statistics: dict) -> None:
    print(
        "[CHECK_ACCOUNT] "
        f"AccountId={statistics.get('account_id')} "
        f"name={statistics.get('account_name') or '—'} "
        f"registry_kkt={statistics.get('registry_kkt_count', 0)} "
        f"loaded_kkt={statistics.get('loaded_kkt_count', 0)} "
        f"skipped={statistics.get('skipped_count', 0)} "
        f"errors={statistics.get('error_count', 0)}"
    )


def _record_skip(
    result: dict,
    statistics: dict,
    reason: str,
    *,
    account_id: int | None,
    reg_number: str | None = None,
) -> None:
    metrics = result["skip_metrics"]
    metrics[reason] = int(metrics.get(reason, 0)) + 1
    statistics["skipped_count"] += 1
    statistics["skip_reasons"][reason] = (
        int(statistics["skip_reasons"].get(reason, 0)) + 1
    )
    result["skipped_items"].append(
        {
            "stage": "live_collector",
            "reason": reason,
            "account_id": account_id,
            "reg_number": reg_number,
        }
    )


def collect_kkt_by_inn(
    inn: str,
    kpp: str | None = None,
    *,
    status_callback=None,
) -> dict:
    """Получает ККТ из СБИС без чтения и записи локальной базы."""
    result = {
        "inn": inn,
        "kpp": kpp,
        "accounts": [],
        "kkt": [],
        "errors": [],
        "account_statistics": [],
        "skip_metrics": {},
        "skipped_items": [],
        "skipped_foreign_owner": 0,
    }
    decoder = SBISDecoder()

    with SBISClient() as client:
        if status_callback:
            status_callback("Ищу контрагента в СБИС…")
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
        if status_callback:
            status_callback(
                f"Найдено активных аккаунтов: {len(accounts)}. Начинаю обход…"
            )

        for account_index, account in enumerate(accounts, 1):
            # Один РНМ может присутствовать в нескольких аккаунтах. Поэтому
            # дедупликация допустима только внутри текущего аккаунта: карточка
            # в другом аккаунте может содержать актуальные сроки ФН.
            seen_reg_numbers: set[str] = set()
            account_id = _account_id(account)
            statistics = {
                "account_id": account_id,
                "account_name": _account_name(account),
                "registry_kkt_count": 0,
                "loaded_kkt_count": 0,
                "skipped_count": 0,
                "skip_reasons": {},
                "error_count": 0,
            }
            result["account_statistics"].append(statistics)
            account_label = statistics["account_name"] or "Без названия"
            if status_callback:
                status_callback(
                    f"Аккаунт {account_index}/{len(accounts)} "
                    f"({account_id or 'без ID'}, {account_label}): "
                    "получаю Registry…"
                )
            if account_id is None:
                statistics["error_count"] += 1
                result["errors"].append(
                    {"stage": "AccountContractor.List", "error": "Нет AccountId"}
                )
                _log_account(statistics)
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
                statistics["error_count"] += 1
                result["errors"].append(
                    {
                        "stage": "RegKKT.Registry",
                        "account_id": account_id,
                        "error": str(error),
                    }
                )
                _log_account(statistics)
                continue

            account_reg_numbers = {
                str(item.get("KKTRegId") or "").strip()
                for item in registry
                if isinstance(item, dict)
                and item.get("KKTId") is not None
                and str(item.get("KKTRegId") or "").strip()
            }
            statistics["registry_kkt_count"] = len(account_reg_numbers)
            if status_callback:
                status_callback(
                    f"Аккаунт {account_index}/{len(accounts)} "
                    f"({account_id}): ККТ в Registry — "
                    f"{statistics['registry_kkt_count']}. Читаю карточки…"
                )

            valid_total = len(account_reg_numbers)
            processed_valid = 0
            for registry_item in registry:
                if not isinstance(registry_item, dict):
                    _record_skip(
                        result,
                        statistics,
                        "invalid_registry_item",
                        account_id=account_id,
                    )
                    continue
                kkt_id = registry_item.get("KKTId")
                reg_number = str(registry_item.get("KKTRegId") or "").strip()
                if kkt_id is None:
                    _record_skip(
                        result,
                        statistics,
                        "missing_kkt_id",
                        account_id=account_id,
                        reg_number=reg_number or None,
                    )
                    continue
                if not reg_number:
                    _record_skip(
                        result,
                        statistics,
                        "missing_reg_number",
                        account_id=account_id,
                    )
                    continue
                if reg_number in seen_reg_numbers:
                    _record_skip(
                        result,
                        statistics,
                        "duplicate_in_account",
                        account_id=account_id,
                        reg_number=reg_number,
                    )
                    continue
                seen_reg_numbers.add(reg_number)
                processed_valid += 1

                if status_callback and (
                    processed_valid == 1
                    or processed_valid == valid_total
                    or processed_valid % 5 == 0
                ):
                    status_callback(
                        f"Аккаунт {account_index}/{len(accounts)} "
                        f"({account_id}): KKT.Read "
                        f"{processed_valid}/{valid_total}…"
                    )

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
                        _record_skip(
                            result,
                            statistics,
                            "foreign_owner",
                            account_id=account_id,
                            reg_number=reg_number,
                        )
                        continue
                    result["kkt"].append(
                        {
                            "account_id": account_id,
                            "account_name": statistics["account_name"],
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
                    statistics["loaded_kkt_count"] += 1
                except Exception as error:
                    statistics["error_count"] += 1
                    _record_skip(
                        result,
                        statistics,
                        "kkt_read_error",
                        account_id=account_id,
                        reg_number=reg_number,
                    )
                    result["errors"].append(
                        {
                            "stage": "KKT.Read",
                            "account_id": account_id,
                            "reg_number": reg_number,
                            "error": str(error),
                        }
                    )

            _log_account(statistics)
            if status_callback:
                status_callback(
                    f"Аккаунт {account_index}/{len(accounts)} завершён: "
                    f"получено карточек {statistics['loaded_kkt_count']}, "
                    f"ошибок {statistics['error_count']}."
                )

    print(
        "[CHECK_SUMMARY] "
        f"INN={inn} accounts={len(result['accounts'])} "
        f"loaded_kkt={len(result['kkt'])} errors={len(result['errors'])} "
        f"skip_metrics={result['skip_metrics']}"
    )

    return result
