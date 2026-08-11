import calendar
from dataclasses import dataclass
from datetime import date, datetime

from parsers.kkt import extract_ofd_end_date
from services.live_collector import collect_kkt_by_inn


@dataclass(frozen=True)
class KKTInfo:
    owner_inn: str
    owner_name: str | None
    model: str | None
    reg_number: str
    manufacturer_number: str | None
    fn_end_date: str | None
    ofd_end_date: str | None
    sales_point_address: str | None = None
    account_id: int | None = None
    account_name: str | None = None


@dataclass(frozen=True)
class KKTLookupResult:
    owner_inn: str
    accounts_count: int
    available_kkt_count: int
    foreign_kkt_count: int
    expired_kkt_count: int
    cash_registers: tuple[KKTInfo, ...]
    errors: tuple[str, ...]
    skip_metrics: tuple[tuple[str, int], ...] = ()


def normalize_inn(value: str) -> str:
    normalized = "".join(str(value).split())
    if not normalized.isdigit() or len(normalized) not in {10, 12}:
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    return normalized


def _text(value) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None


def _date_text(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text


def _owner_name(detail: dict) -> str | None:
    chief = detail.get("headChief")
    if not isinstance(chief, dict):
        return None
    name = " ".join(
        str(chief.get(field) or "").strip()
        for field in ("Фамилия", "Имя", "Отчество")
    ).strip()
    return name or _text(chief.get("Лицо.Название"))


def _parse(item: dict, owner_inn: str) -> KKTInfo | None:
    registry = item.get("registry") if isinstance(item.get("registry"), dict) else {}
    detail = item.get("kkt") if isinstance(item.get("kkt"), dict) else {}
    reg_number = _text(detail.get("НомерРегистрационный") or registry.get("KKTRegId"))
    if not reg_number:
        return None
    license_data = registry.get("LicenseData") or {}
    model = detail.get("kktModel") or {}
    return KKTInfo(
        owner_inn=owner_inn,
        owner_name=_owner_name(detail),
        model=_text(
            detail.get("ОборудованиеНазвание")
            or (model.get("Name") if isinstance(model, dict) else None)
            or registry.get("KKTName")
        ),
        reg_number=reg_number,
        manufacturer_number=_text(detail.get("НомерПроизводителя")),
        fn_end_date=_date_text(
            item.get("fn_end_date")
            or detail.get("FSEndDate")
            or (license_data.get("finish_fs_day") if isinstance(license_data, dict) else None)
        ),
        ofd_end_date=_date_text(
            item.get("ofd_end_date")
            or extract_ofd_end_date(registry=registry, detail=detail)
        ),
        sales_point_address=_text(
            item.get("sales_point_address")
            or detail.get("salespoint_address")
            or detail.get("Адрес")
            or registry.get("Address")
        ),
        account_id=item.get("account_id"),
        account_name=_text(item.get("account_name")),
    )


def replacement_sort_key(
    item: KKTInfo,
    *,
    today: date | None = None,
) -> tuple[int, str, str]:
    """Сортирует кассы по близости даты замены ФН к текущему дню."""
    report_date = today or date.today()
    try:
        fn_date = date.fromisoformat(str(item.fn_end_date)[:10])
    except (TypeError, ValueError):
        return (10**9, str(item.fn_end_date or ""), item.reg_number)
    return (abs((fn_date - report_date).days), fn_date.isoformat(), item.reg_number)


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def fn_replacement_status(
    item: KKTInfo,
    *,
    today: date | None = None,
) -> tuple[str, str]:
    """Возвращает цветной маркер и текст срочности замены ФН."""
    report_date = today or date.today()
    try:
        fn_date = date.fromisoformat(str(item.fn_end_date)[:10])
    except (TypeError, ValueError):
        return ("⚪", "срок не распознан")
    if fn_date < report_date:
        return ("⚫", "срок истёк")
    if fn_date < _add_months(report_date, 1):
        return ("🔴", "замена менее чем через месяц")
    if fn_date <= _add_months(report_date, 4):
        return ("🟡", "замена в течение четырёх месяцев")
    return ("🟢", "замена более чем через четыре месяца")


def display_sort_key(item: KKTInfo) -> tuple[str, str]:
    """Сортировка для выдачи: дальние сроки первыми, истёкшие последними."""
    try:
        normalized = date.fromisoformat(str(item.fn_end_date)[:10]).isoformat()
    except (TypeError, ValueError):
        normalized = "0001-01-01"
    return (normalized, item.reg_number)


def _candidate_quality(raw_item: dict, parsed: KKTInfo) -> tuple[int, str]:
    detail = raw_item.get("kkt") if isinstance(raw_item.get("kkt"), dict) else {}
    active_score = 1 if detail.get("Действующая") is True else 0
    return (active_score, str(parsed.fn_end_date or ""))


def find_all_kkt_by_owner_inn(
    owner_inn: str,
    *,
    status_callback=None,
) -> KKTLookupResult:
    owner_inn = normalize_inn(owner_inn)
    if status_callback:
        status_callback("Получаю аккаунты и реестр ККТ из СБИС…")
    raw = collect_kkt_by_inn(
        owner_inn,
        status_callback=status_callback,
    )
    selected_by_reg_number: dict[str, tuple[dict, KKTInfo]] = {}
    skip_metrics = {
        str(reason): int(count)
        for reason, count in (raw.get("skip_metrics") or {}).items()
    }

    def record_skip(reason: str) -> None:
        skip_metrics[reason] = int(skip_metrics.get(reason, 0)) + 1

    for item in raw.get("kkt") or []:
        parsed = _parse(item, owner_inn)
        if parsed is None:
            record_skip("parse_failed")
            continue
        if not parsed.fn_end_date:
            record_skip("missing_fn_end_date")
            continue
        previous = selected_by_reg_number.get(parsed.reg_number)
        if previous is None:
            selected_by_reg_number[parsed.reg_number] = (item, parsed)
            continue
        if _candidate_quality(item, parsed) > _candidate_quality(*previous):
            record_skip("duplicate_complete_replaced")
            selected_by_reg_number[parsed.reg_number] = (item, parsed)
        else:
            record_skip("duplicate_complete_kept")

    selected = [parsed for _raw_item, parsed in selected_by_reg_number.values()]
    selected.sort(key=display_sort_key, reverse=True)
    print(
        "[FILTER_SUMMARY] "
        f"INN={owner_inn} raw_kkt={len(raw.get('kkt') or [])} "
        f"selected={len(selected)} skip_metrics={skip_metrics}"
    )
    if status_callback:
        status_callback("Формирую результат…")
    errors = tuple(str(error) for error in (raw.get("errors") or []))
    return KKTLookupResult(
        owner_inn=owner_inn,
        accounts_count=len(raw.get("accounts") or []),
        available_kkt_count=len(selected),
        foreign_kkt_count=int(raw.get("skipped_foreign_owner") or 0),
        expired_kkt_count=0,
        cash_registers=tuple(selected),
        errors=errors,
        skip_metrics=tuple(sorted(skip_metrics.items())),
    )
