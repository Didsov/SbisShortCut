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


@dataclass(frozen=True)
class KKTLookupResult:
    owner_inn: str
    accounts_count: int
    available_kkt_count: int
    foreign_kkt_count: int
    expired_kkt_count: int
    cash_registers: tuple[KKTInfo, ...]
    errors: tuple[str, ...]


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
    )


def find_all_kkt_by_owner_inn(
    owner_inn: str,
    *,
    status_callback=None,
) -> KKTLookupResult:
    owner_inn = normalize_inn(owner_inn)
    if status_callback:
        status_callback("Получаю аккаунты и реестр ККТ из СБИС…")
    raw = collect_kkt_by_inn(owner_inn)
    selected: list[KKTInfo] = []
    seen: set[str] = set()
    for item in raw.get("kkt") or []:
        parsed = _parse(item, owner_inn)
        if parsed is None or parsed.reg_number in seen:
            continue
        if not parsed.fn_end_date:
            continue
        seen.add(parsed.reg_number)
        selected.append(parsed)
    selected.sort(key=lambda item: (item.fn_end_date or "", item.reg_number))
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
    )
