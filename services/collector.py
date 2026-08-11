from concurrent.futures import Executor, as_completed
from datetime import date, datetime
import threading
import time

from config.run_settings import ACCOUNT_LEASE_SECONDS
from decoder import SBISDecoder
from parsers.kkt import extract_ofd_end_date
from sbis_client import SBISClient
from services.accounts import get_contractor_accounts
from services.console_output import print_event, show_status
from services.contractor import (
    get_billing_contractor_id,
    get_contractor_card,
)
from services.kkt import get_kkt
from services.performance import (
    record_kkt_write,
    record_operation_retry,
)
from services.registry import (
    PartialRegistryError,
    get_all_kkt_registry,
)
from storage.contractors import (
    save_contractor,
    contractor_exists,
)
from storage.account_scans import (
    claim_account,
    mark_account_done,
    mark_account_error,
    renew_account_claim,
)
from storage.clients import (
    mark_client_done,
    mark_client_error,
    mark_client_not_found,
    save_client,
)
from storage.errors import save_error
from storage.kkt import get_missing_reg_numbers, save_kkt


_worker_state = threading.local()


def get_worker_client() -> SBISClient:
    client = getattr(_worker_state, "sbis_client", None)

    if client is None:
        client = SBISClient()
        _worker_state.sbis_client = client

    return client


def fetch_kkt_detail(
    account_id: int,
    kkt_id: int,
    kkt_reg_id: str,
) -> dict:
    last_error: Exception | None = None

    for attempt in range(1, 3):
        try:
            response = get_kkt(
                account_id=account_id,
                kkt_id=kkt_id,
                kkt_reg_id=kkt_reg_id,
                client=get_worker_client(),
            )

            return decode_kkt_response(
                response=response,
                decoder=SBISDecoder(),
            )
        except Exception as error:
            last_error = error

            if attempt < 2:
                record_operation_retry("kkt_read")
                time.sleep(0.5)

    raise RuntimeError(
        f"KKT.Read не выполнен для РНМ {kkt_reg_id}: {last_error}"
    ) from last_error


def parse_sbis_date(
    value: str | None,
) -> date | None:
    """
    Преобразует дату СБИС вида:

        YYYY-MM-DD
        YYYY-MM-DD HH:MM:SS

    в объект date.
    """

    if not isinstance(value, str):
        return None

    value = value.strip()

    if not value:
        return None

    try:
        return datetime.strptime(
            value[:10],
            "%Y-%m-%d",
        ).date()

    except ValueError:
        return None


def decode_kkt_response(
    response: dict,
    decoder: SBISDecoder,
) -> dict:
    """
    Проверяет и декодирует ответ KKT.Read.
    """

    if not isinstance(response, dict):
        raise TypeError(
            "KKT.Read вернул данные неизвестного формата"
        )

    if "error" in response:
        api_error = response["error"]

        raise RuntimeError(
            api_error.get("details")
            or api_error.get("message")
            or "Ошибка KKT.Read"
        )

    data = response.get(
        "result",
        response,
    )

    if (
        isinstance(data, dict)
        and (
            "_type" in data
            or (
                "d" in data
                and "s" in data
            )
        )
    ):
        data = decoder.decode(data)

    if not isinstance(data, dict):
        raise TypeError(
            "После декодирования KKT.Read "
            "получен не словарь"
        )

    return data


def get_account_id(
    account: dict,
) -> int | None:
    account_info = (
        account.get("AccountInfo")
        or {}
    )

    value = (
        account.get("AccountId")
        or account.get("AccountID")
        or account_info.get("AccountId")
        or account_info.get("AccountID")
    )

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_account_name(
    account: dict,
) -> str | None:
    account_info = (
        account.get("AccountInfo")
        or {}
    )

    return (
        account.get("AccountName")
        or account.get("Name")
        or account.get("Название")
        or account_info.get("Name")
        or account_info.get("Название")
    )


def get_account_console_label(
    account_id: int,
    inn: str,
    account_name: str | None,
) -> str:
    name = " ".join(str(account_name or "Без названия").split())
    return f"ID={account_id} | ИНН={inn} | {name}"


def get_accounts_by_inn(
    inn: str,
    kpp: str | None = None,
) -> list[dict]:
    """
    Получает аккаунты контрагента по ИНН и КПП.
    """

    contractor_id = get_billing_contractor_id(
        inn=inn,
        kpp=kpp,
    )

    return get_contractor_accounts(
        contractor_id=contractor_id,
    )


def _get_all_kkt_by_inn_legacy(
    inn: str,
    kpp: str | None = None,
) -> dict:
    """
    Получает все аккаунты клиента и все подходящие ККТ.

    ККТ сохраняется в результате, если:

    - это настоящая строка ККТ, а не папка;
    - присутствует РНМ;
    - KKT.Read успешно вернул подробности;
    - срок ФН известен;
    - срок ФН не раньше 2026-01-01.
    """

    decoder = SBISDecoder()

    contractor_id = get_billing_contractor_id(
        inn=inn,
        kpp=kpp,
    )

    accounts = get_contractor_accounts(
        contractor_id=contractor_id,
    )
    contractor_cache = {}

    result = {
        "inn": inn,
        "kpp": kpp,
        "contractor_id": contractor_id,
        "accounts": accounts,
        "kkt": [],
        "errors": [],
        "skipped_without_reg_id": 0,
        "skipped_without_fn_date": 0,
        "skipped_old_fn": 0,
    }

    # Основная дедупликация — по РНМ.
    # Один и тот же РНМ не должен сохраняться повторно,
    # даже если встретился в нескольких ветках Registry.
    seen_reg_numbers: set[str] = set()
    seen_contractors: set[str] = set()

    for account in accounts:
        account_id = get_account_id(account)
        account_name = get_account_name(account)

        if account_id is None:
            result["errors"].append({
                "stage": "AccountContractor.List",
                "account_id": None,
                "account_name": account_name,
                "error": "Не найден AccountId",
            })
            continue

        try:
            registry = get_all_kkt_registry(
                account_id=account_id,
                limit=25,
                include_archived=False,
                max_pages=200,
                retry_attempts=3,
            )

            if not isinstance(registry, list):
                raise TypeError(
                    "RegKKT.Registry вернул не список"
                )

        except Exception as error:
            result["errors"].append({
                "stage": "RegKKT.Registry",
                "account_id": account_id,
                "account_name": account_name,
                "error": str(error),
            })
            continue

        registry_kkt = [
            item
            for item in registry
            if (
                isinstance(item, dict)
                and item.get("KKTId") is not None
                and item.get("KKTRegId")
            )
        ]

        total_registry_kkt = len(registry_kkt)

        print(
            f"[KKT.Read] AccountId={account_id} "
            f"зарегистрированных ККТ={total_registry_kkt}"
        )

        for kkt_index, registry_item in enumerate(
            registry_kkt,
            start=1,
        ):
            if not isinstance(registry_item, dict):
                continue

            # Registry содержит папки, точки продаж
            # и настоящие строки ККТ.
            kkt_id = registry_item.get("KKTId")

            if kkt_id is None:
                continue

            try:
                kkt_id = int(kkt_id)
            except (TypeError, ValueError):
                result["errors"].append({
                    "stage": "RegKKT.Registry",
                    "account_id": account_id,
                    "account_name": account_name,
                    "kkt_id": kkt_id,
                    "error": "Некорректный KKTId",
                })
                continue

            kkt_reg_id = registry_item.get("KKTRegId")

            # Незавершённая регистрация без РНМ.
            # Считаем мусором и не вызываем KKT.Read.
            if not kkt_reg_id:
                result["skipped_without_reg_id"] += 1
                continue

            kkt_reg_id = str(kkt_reg_id).strip()

            if not kkt_reg_id:
                result["skipped_without_reg_id"] += 1
                continue

            # Один РНМ соответствует одной зарегистрированной ККТ.
            if kkt_reg_id in seen_reg_numbers:
                continue

            seen_reg_numbers.add(kkt_reg_id)

            try:
                print(
                f"\r[KKT.Read] AccountId={account_id} "
                f"{kkt_index}/{total_registry_kkt} | "
                f"РНМ={kkt_reg_id}",
                end="",
                flush=True,
                )
                print()

                kkt_response = get_kkt(
                    account_id=account_id,
                    kkt_id=kkt_id,
                    kkt_reg_id=kkt_reg_id,
                )

                kkt_data = decode_kkt_response(
                    response=kkt_response,
                    decoder=decoder,
                )

                license_data = (
                    registry_item.get("LicenseData")
                    or {}
                )

                fn_end_date_raw = (
                    kkt_data.get("FSEndDate")
                    or license_data.get(
                        "finish_fs_day"
                    )
                )

                fn_end_date = parse_sbis_date(
                    fn_end_date_raw
                )

                # Нет срока ФН — в рабочий отчёт
                # такую кассу не сохраняем.
                if fn_end_date is None:
                    result[
                        "skipped_without_fn_date"
                    ] += 1
                    continue

                # Оставляем только ККТ,
                # где ФН закончился не раньше 01.01.2026.

                ofd_end_date = extract_ofd_end_date(
                    registry=registry_item,
                    detail=kkt_data,
                )
                
                kkt_inn = (
                    registry_item.get("INN")
                    or kkt_data.get("ИНН")
                )

                if kkt_inn:
                    kkt_inn = str(kkt_inn).strip()

                    if (
                        kkt_inn not in seen_contractors
                        and not contractor_exists(kkt_inn)
                    ):

                        try:
                            contractor = get_contractor_card(
                                inn=kkt_inn
                            )

                            if contractor:
                                save_contractor(
                                    contractor
                                )

                            seen_contractors.add(
                                kkt_inn
                            )

                            print(
                                f"[CONTRACTOR] {kkt_inn} "
                                f"{contractor.get('name') if contractor else 'не найден'}"
                            )

                        except Exception as error:
                            result["errors"].append({
                                "stage": "BillingContractor.ReadCard",
                                "inn": kkt_inn,
                                "error": str(error),
                            })
                result["kkt"].append({
                    "source_client_inn": inn,
                    "source_client_kpp": kpp,
                    "source_contractor_id": contractor_id,

                    "account_id": account_id,
                    "account_name": account_name,

                    "kkt_contractor_id": (
                        registry_item.get("Contragent")
                    ),
                    "kkt_inn": (
                        registry_item.get("INN")
                        or kkt_data.get("ИНН")
                    ),
                    "kkt_kpp": (
                        registry_item.get("KPP")
                        or kkt_data.get("КПП")
                    ),

                    "registry": registry_item,
                    "kkt": kkt_data,

                    "fn_end_date": (
                        fn_end_date_raw
                    ),
                    "ofd_end_date": (
                        ofd_end_date
                    ),
                })

            except Exception as error:
                result["errors"].append({
                    "stage": "KKT.Read",
                    "account_id": account_id,
                    "account_name": account_name,
                    "kkt_id": kkt_id,
                    "kkt_reg_id": kkt_reg_id,
                    "error": str(error),
                })

    return result


def get_all_kkt_by_inn_optimized(
    inn: str,
    kpp: str | None = None,
    *,
    client: SBISClient | None = None,
    executor: Executor | None = None,
    force_reprocess: bool = False,
    track_account_scans: bool = False,
    allow_account_error_retry: bool = False,
    owner_inn_filter: str | None = None,
) -> dict:
    """Полный сбор ККТ без тихого принятия частичных результатов."""

    owner_inn_filter = (
        str(owner_inn_filter).strip()
        if owner_inn_filter is not None
        else None
    ) or None
    client = client or SBISClient()
    contractor_id = get_billing_contractor_id(
        inn=inn,
        kpp=kpp,
        client=client,
    )
    accounts = get_contractor_accounts(
        contractor_id=contractor_id,
        client=client,
    )

    result = {
        "inn": inn,
        "kpp": kpp,
        "contractor_id": contractor_id,
        "accounts": accounts,
        "kkt": [],
        "errors": [],
        "skipped_without_reg_id": 0,
        "skipped_foreign_owner": 0,
        "skipped_inactive": 0,
        "without_fn_date": 0,
        "critical_error_count": 0,
        "complete": True,
        "account_scans": [],
        "accounts_processed": 0,
        "accounts_skipped": 0,
        "accounts_busy": 0,
        "accounts_deferred": 0,
        "accounts_failed": 0,
        "accounts_checked": 0,
        "accounts_removed": 0,
    }
    seen_reg_numbers: set[str] = set()
    seen_contractors: set[str] = set()

    for account_index, account in enumerate(accounts, start=1):
        result["accounts_checked"] += 1
        account_id = get_account_id(account)
        account_name = get_account_name(account)

        # AccountContractor.List иногда возвращает удалённую связь с
        # AccountExtId=0. Такой аккаунт уже нельзя открыть, хотя его старый
        # AccountId и запись account_scans могут сохраниться.
        raw_account_ext_id = account.get("AccountExtId")
        if str(raw_account_ext_id).strip() == "0":
            result["accounts_removed"] += 1
            print_event(
                f"[АККАУНТ] ID={account_id or '—'} | ИНН={inn} | "
                f"{account_name or 'Без названия'} — удалён, пропуск"
            )
            continue

        if account_id is None:
            result["errors"].append({
                "stage": "AccountContractor.List",
                "account_id": None,
                "account_name": account_name,
                "error": "Не найден AccountId",
                "critical": True,
            })
            result["critical_error_count"] += 1
            continue

        account_label = get_account_console_label(
            account_id=account_id,
            inn=inn,
            account_name=account_name,
        )

        claim = (
            claim_account(
                account_id=account_id,
                billing_contractor_id=contractor_id,
                source_inn=inn,
                account_name=account_name,
                lease_seconds=ACCOUNT_LEASE_SECONDS,
                force_reprocess=force_reprocess,
                allow_error_retry=allow_account_error_retry,
            )
            if track_account_scans
            else None
        )

        if claim is not None and claim.state == "done":
            result["accounts_skipped"] += 1
            show_status(
                f"[АККАУНТ {account_index}/{len(accounts)}] "
                f"{account_label} — уже полностью собран, пропуск"
            )
            continue

        if claim is not None and claim.state == "busy":
            result["accounts_busy"] += 1
            result["errors"].append({
                "stage": "AccountScan.Busy",
                "account_id": account_id,
                "account_name": account_name,
                "error": (
                    "Аккаунт обрабатывается другим процессом. "
                    "Клиент оставлен для повторного запуска."
                ),
                "critical": True,
            })
            result["critical_error_count"] += 1
            print_event(
                f"[АККАУНТ] {account_label} — занят другой консолью; "
                "будет повторён позже"
            )
            continue

        if claim is not None and claim.state == "deferred":
            result["accounts_deferred"] += 1
            result["errors"].append({
                "stage": "AccountScan.Deferred",
                "account_id": account_id,
                "account_name": account_name,
                "error": (
                    "Аккаунт уже исчерпал повторы в текущем запуске. "
                    "Он будет повторён при следующем запуске программы."
                ),
                "critical": True,
            })
            result["critical_error_count"] += 1
            show_status(
                f"[АККАУНТ {account_index}/{len(accounts)}] "
                f"{account_label} — повторы этого запуска исчерпаны"
            )
            continue

        scan = {
            "account_id": account_id,
            "account_name": account_name,
            "source_inn": inn,
            "console_label": account_label,
            "state": "processing",
            "registry_complete": False,
            "expected_reg_numbers": set(),
            "read_failures": 0,
            "error": None,
            "tracked": track_account_scans,
        }
        result["account_scans"].append(scan)
        show_status(
            f"[АККАУНТ {account_index}/{len(accounts)}] "
            f"{account_label} — получаю полный реестр ККТ"
        )
        registry_is_partial = False

        try:
            registry = get_all_kkt_registry(
                account_id=account_id,
                limit=25,
                include_archived=False,
                max_pages=200,
                retry_attempts=3,
                client=client,
            )
            scan["registry_complete"] = True
        except PartialRegistryError as error:
            registry = error.items
            registry_is_partial = True
            scan["error"] = str(error)
            result["errors"].append({
                "stage": "RegKKT.Registry.Partial",
                "account_id": account_id,
                "account_name": account_name,
                "error": str(error),
                "critical": True,
            })
            result["critical_error_count"] += 1
            print_event(
                f"[АККАУНТ] {account_label} — Registry получен частично "
                f"({len(registry)} строк); данные сохраним, аккаунт повторим"
            )
        except Exception as error:
            scan["state"] = "error"
            scan["error"] = str(error)
            result["errors"].append({
                "stage": "RegKKT.Registry",
                "account_id": account_id,
                "account_name": account_name,
                "error": str(error),
                "critical": True,
            })
            result["critical_error_count"] += 1
            continue

        if track_account_scans:
            renew_account_claim(
                account_id=account_id,
                lease_seconds=ACCOUNT_LEASE_SECONDS,
            )

        if registry_is_partial and not registry:
            scan["state"] = "error"
            continue

        prepared: list[tuple[int, dict, int, str]] = []

        for index, registry_item in enumerate(registry, start=1):
            if not isinstance(registry_item, dict):
                continue

            raw_kkt_id = registry_item.get("KKTId")
            raw_reg_id = registry_item.get("KKTRegId")

            if raw_kkt_id is None:
                continue

            if not raw_reg_id or not str(raw_reg_id).strip():
                result["skipped_without_reg_id"] += 1
                continue

            kkt_reg_id = str(raw_reg_id).strip()

            # Деактивированные кассы остаются видимыми в Registry, но их
            # карточка KKT.Read больше не открывается. В реальном ответе СБИС
            # такая строка может иметь Active=true и Status=9, поэтому
            # учитываем оба признака.
            registry_status = str(
                registry_item.get("Status") or ""
            ).strip()
            if (
                registry_item.get("Active") is False
                or registry_status == "9"
            ):
                result["skipped_inactive"] += 1
                continue

            registry_owner_inn = str(
                registry_item.get("INN") or ""
            ).strip()

            # Общий аккаунт может содержать сотни касс разных владельцев.
            # Если Registry уже сообщил чужой ИНН, KKT.Read не требуется.
            if (
                owner_inn_filter
                and registry_owner_inn
                and registry_owner_inn != owner_inn_filter
            ):
                result["skipped_foreign_owner"] += 1
                continue

            scan["expected_reg_numbers"].add(kkt_reg_id)

            try:
                kkt_id = int(raw_kkt_id)
            except (TypeError, ValueError):
                result["errors"].append({
                    "stage": "RegKKT.Registry",
                    "account_id": account_id,
                    "account_name": account_name,
                    "kkt_id": raw_kkt_id,
                    "error": "Некорректный KKTId",
                    "critical": True,
                })
                result["critical_error_count"] += 1
                continue

            if kkt_reg_id in seen_reg_numbers:
                continue

            seen_reg_numbers.add(kkt_reg_id)
            prepared.append((index, registry_item, kkt_id, kkt_reg_id))

        show_status(
            f"[АККАУНТ {account_index}/{len(accounts)}] {account_label} — "
            f"реестр={len(scan['expected_reg_numbers'])}, "
            f"читаю карточки={len(prepared)}"
        )

        completed_details: list[
            tuple[dict, int, str, dict | None, Exception | None]
        ] = []

        if executor is None:
            for _, registry_item, kkt_id, kkt_reg_id in prepared:
                try:
                    detail = fetch_kkt_detail(
                        account_id,
                        kkt_id,
                        kkt_reg_id,
                    )
                    completed_details.append(
                        (registry_item, kkt_id, kkt_reg_id, detail, None)
                    )
                except Exception as error:
                    completed_details.append(
                        (registry_item, kkt_id, kkt_reg_id, None, error)
                    )
        else:
            future_map = {
                executor.submit(
                    fetch_kkt_detail,
                    account_id,
                    kkt_id,
                    kkt_reg_id,
                ): (registry_item, kkt_id, kkt_reg_id)
                for _, registry_item, kkt_id, kkt_reg_id in prepared
            }

            for completed, future in enumerate(
                as_completed(future_map),
                start=1,
            ):
                registry_item, kkt_id, kkt_reg_id = future_map[future]

                if completed == len(future_map) or completed % 25 == 0:
                    if track_account_scans:
                        renew_account_claim(
                            account_id=account_id,
                            lease_seconds=ACCOUNT_LEASE_SECONDS,
                        )
                    show_status(
                        f"[АККАУНТ {account_index}/{len(accounts)}] "
                        f"{account_label} — KKT.Read "
                        f"{completed}/{len(future_map)}"
                    )

                try:
                    detail = future.result()
                    completed_details.append(
                        (registry_item, kkt_id, kkt_reg_id, detail, None)
                    )
                except Exception as error:
                    completed_details.append(
                        (registry_item, kkt_id, kkt_reg_id, None, error)
                    )

        for registry_item, kkt_id, kkt_reg_id, kkt_data, error in (
            completed_details
        ):
            if error is not None or kkt_data is None:
                # Если тот же РНМ встретится в другом аккаунте клиента,
                # разрешаем повторить запрос после текущей ошибки.
                seen_reg_numbers.discard(kkt_reg_id)
                result["errors"].append({
                    "stage": "KKT.Read",
                    "account_id": account_id,
                    "account_name": account_name,
                    "kkt_id": kkt_id,
                    "kkt_reg_id": kkt_reg_id,
                    "error": str(error),
                    "critical": True,
                })
                result["critical_error_count"] += 1
                scan["read_failures"] += 1
                continue

            license_data = registry_item.get("LicenseData") or {}
            fn_end_date_raw = (
                kkt_data.get("FSEndDate")
                or license_data.get("finish_fs_day")
            )

            if parse_sbis_date(fn_end_date_raw) is None:
                result["without_fn_date"] += 1

            ofd_end_date = extract_ofd_end_date(
                registry=registry_item,
                detail=kkt_data,
            )
            # Полная карточка приоритетнее Registry. Это также защищает от
            # редкого рассогласования ИНН между двумя ответами СБИС.
            kkt_inn = kkt_data.get("ИНН") or registry_item.get("INN")

            if kkt_inn:
                kkt_inn = str(kkt_inn).strip()

            # Для записей без ИНН в Registry проверка выполняется после
            # KKT.Read. Не возвращаем кассу, если совпадение не подтверждено.
            if owner_inn_filter and kkt_inn != owner_inn_filter:
                result["skipped_foreign_owner"] += 1
                continue

            if kkt_inn:
                if kkt_inn not in seen_contractors:
                    seen_contractors.add(kkt_inn)

                    if not contractor_exists(kkt_inn):
                        try:
                            contractor = get_contractor_card(
                                inn=kkt_inn,
                                client=client,
                            )

                            if contractor:
                                save_contractor(contractor)
                        except Exception as contractor_error:
                            result["errors"].append({
                                "stage": "BillingContractor.ReadCard",
                                "inn": kkt_inn,
                                "error": str(contractor_error),
                                "critical": False,
                            })

            result["kkt"].append({
                "source_client_inn": inn,
                "source_client_kpp": kpp,
                "source_contractor_id": contractor_id,
                "account_id": account_id,
                "account_name": account_name,
                "kkt_contractor_id": registry_item.get("Contragent"),
                "kkt_inn": kkt_inn,
                "kkt_kpp": (
                    registry_item.get("KPP")
                    or kkt_data.get("КПП")
                ),
                "registry": registry_item,
                "kkt": kkt_data,
                "fn_end_date": fn_end_date_raw,
                "ofd_end_date": ofd_end_date,
            })

        scan["state"] = "error" if registry_is_partial else "collected"

    result["complete"] = result["critical_error_count"] == 0
    return result


def get_all_kkt_by_inn(
    inn: str,
    kpp: str | None = None,
    *,
    owner_inn_filter: str | None = None,
) -> dict:
    """Совместимый публичный вход в новый полный сбор."""

    with SBISClient() as client:
        return get_all_kkt_by_inn_optimized(
            inn=inn,
            kpp=kpp,
            client=client,
            owner_inn_filter=owner_inn_filter,
        )


def _mark_claimed_scans_error(result: dict | None, error: str) -> None:
    if not result:
        return

    for scan in result.get("account_scans", []):
        if scan.get("state") not in {"processing", "collected", "error"}:
            continue

        try:
            mark_account_error(
                account_id=scan["account_id"],
                error=error,
                registry_kkt_count=len(scan.get("expected_reg_numbers", ())),
            )
            scan["state"] = "error"
        except Exception:
            # Исходная ошибка важнее сбоя обновления служебного статуса.
            pass


def _finalize_account_scans(result: dict) -> None:
    """Сверяет Registry с БД и фиксирует итог каждого AccountId."""
    for scan in result["account_scans"]:
        account_id = scan["account_id"]
        account_label = scan["console_label"]
        expected = set(scan.get("expected_reg_numbers", ()))

        if scan["state"] == "error" or not scan["registry_complete"]:
            error_message = scan.get("error") or "Registry получен не полностью"
            mark_account_error(
                account_id=account_id,
                error=error_message,
                registry_kkt_count=len(expected),
            )
            result["accounts_failed"] += 1
            print_event(
                f"[АККАУНТ] {account_label} — не завершён: "
                "неполный Registry, будет повторён"
            )
            continue

        missing = get_missing_reg_numbers(expected)
        other_critical_errors = [
            error
            for error in result["errors"]
            if error.get("critical", False)
            and error.get("account_id") == account_id
            and error.get("stage") != "KKT.Read"
        ]

        if missing or other_critical_errors:
            if missing:
                error_message = (
                    f"После сохранения отсутствуют {len(missing)} "
                    f"из {len(expected)} РНМ"
                )
            else:
                error_message = str(other_critical_errors[0].get("error"))

            mark_account_error(
                account_id=account_id,
                error=error_message,
                registry_kkt_count=len(expected),
                saved_kkt_count=len(expected) - len(missing),
            )
            scan["state"] = "error"
            result["accounts_failed"] += 1
            print_event(
                f"[АККАУНТ] {account_label} — не завершён: "
                f"в БД {len(expected) - len(missing)}/{len(expected)} ККТ; "
                "будет повторён"
            )
            continue

        recovered_reads = scan.get("read_failures", 0)

        if recovered_reads:
            # Финальный KKT.Read завершился таймаутом, но соответствующий РНМ
            # уже есть в БД. Это предупреждение, а не потерянная касса.
            result["errors"] = [
                error
                for error in result["errors"]
                if not (
                    error.get("account_id") == account_id
                    and error.get("stage") == "KKT.Read"
                )
            ]

        mark_account_done(
            account_id=account_id,
            registry_kkt_count=len(expected),
            saved_kkt_count=len(expected),
        )
        scan["state"] = "done"
        result["accounts_processed"] += 1
        suffix = (
            f", таймаутов закрыто данными БД={recovered_reads}"
            if recovered_reads
            else ""
        )
        print_event(
            f"[АККАУНТ] {account_label} — готов: "
            f"ККТ={len(expected)}{suffix}"
        )

    result["critical_error_count"] = sum(
        1
        for error in result["errors"]
        if error.get("critical", False)
    )
    result["complete"] = result["critical_error_count"] == 0


def collect_and_save_client(
    client: dict,
    *,
    api_client: SBISClient | None = None,
    executor: Executor | None = None,
    force_reprocess: bool = False,
    allow_account_error_retry: bool = False,
    filter_kkt_by_client_inn: bool = False,
) -> dict | None:
    """
    Обрабатывает одного CRM-клиента:

    - сохраняет клиента;
    - получает все его аккаунты;
    - получает все зарегистрированные ККТ;
    - сохраняет ККТ аккаунтов клиента; при отключённом фильтре владельца
      общий AccountId собирается целиком для всех организаций;
    - сохраняет ошибки;
    - обновляет статус клиента.
    """

    save_client(client)

    inn = client.get("inn")
    kpp = client.get("kpp")

    if not inn:
        error_message = (
            "У клиента отсутствует ИНН"
        )

        mark_client_error(
            inn="",
            kpp=kpp,
            error=error_message,
        )

        save_error(
            stage="CRM",
            error=error_message,
        )

        return None

    result = None

    try:
        result = get_all_kkt_by_inn_optimized(
            inn=inn,
            kpp=kpp,
            client=api_client,
            executor=executor,
            force_reprocess=force_reprocess,
            track_account_scans=True,
            allow_account_error_retry=allow_account_error_retry,
            owner_inn_filter=inn if filter_kkt_by_client_inn else None,
        )

        total_to_save = len(result["kkt"])

        if total_to_save:
            show_status(f"[БД] Сохраняю ККТ: 0/{total_to_save}")

        for index, item in enumerate(
            result["kkt"],
            start=1,
        ):
            registry = item.get("registry") or {}
            detail = item.get("kkt") or {}

            reg_number = (
                detail.get("НомерРегистрационный")
                or registry.get("KKTRegId")
            )

            try:
                save_kkt(item)
                record_kkt_write()

            except Exception as error:
                raise RuntimeError(
                    f"Не удалось сохранить ККТ "
                    f"РНМ={reg_number}: {error}"
                ) from error

            if index == total_to_save or index % 100 == 0:
                show_status(f"[БД] Сохраняю ККТ: {index}/{total_to_save}")

        _finalize_account_scans(result)

        for error_data in result["errors"]:
            save_error(
                inn=inn,
                account_id=error_data.get(
                    "account_id"
                ),
                kkt_id=error_data.get(
                    "kkt_id"
                ),
                stage=error_data.get(
                    "stage",
                    "unknown",
                ),
                error=error_data.get(
                    "error",
                    "Неизвестная ошибка",
                ),
            )

        if result["complete"]:
            mark_client_done(
                inn=inn,
                kpp=kpp,
                contractor_id=result["contractor_id"],
            )
        else:
            mark_client_error(
                inn=inn,
                kpp=kpp,
                error=(
                    "Сбор завершён частично: "
                    f"критических ошибок={result['critical_error_count']}. "
                    "Успешно полученные ККТ сохранены; клиент будет повторён."
                ),
            )

        return result

    except Exception as error:
        error_message = str(error)
        _mark_claimed_scans_error(result, error_message)

        if isinstance(error, LookupError):
            print_event(
                f"[КЛИЕНТ] ИНН={inn} — контрагент не найден в СБИС"
            )
            mark_client_not_found(
                inn=inn,
                kpp=kpp,
                error=error_message,
            )
            save_error(
                inn=inn,
                stage="BillingContractor.NotFound",
                error=error_message,
            )
            return {
                "inn": inn,
                "kpp": kpp,
                "kkt": [],
                "errors": [],
                "complete": True,
                "not_found": True,
                "accounts_processed": 0,
                "accounts_skipped": 0,
                "accounts_failed": 0,
                "accounts_busy": 0,
                "accounts_deferred": 0,
            }

        print_event(
            f"[КЛИЕНТ] ИНН={inn} — ошибка "
            f"{type(error).__name__}: {error_message}"
        )

        try:
            mark_client_error(
                inn=inn,
                kpp=kpp,
                error=error_message,
            )
        except Exception as status_error:
            print_event(
                f"[БД] Не удалось установить статус клиента: {status_error}"
            )

        try:
            save_error(
                inn=inn,
                stage="client",
                error=error_message,
            )
        except Exception as save_error_exception:
            print_event(
                f"[БД] Не удалось сохранить ошибку: {save_error_exception}"
            )

        return None
