import calendar
import io
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing, nullcontext, redirect_stdout
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from threading import Lock


from parsers.kkt import extract_ofd_end_date
from services.collector import get_all_kkt_by_inn
from storage import database
from storage.kkt import save_kkt


_BACKED_UP_DATABASES: set[Path] = set()
_DATABASE_BACKUP_LOCK = Lock()


def normalize_inn(value: str) -> str:
    """Проверяет ИНН физического лица или организации."""
    normalized = "".join(str(value).split())
    if not normalized.isdigit() or len(normalized) not in {10, 12}:
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    return normalized


@dataclass(frozen=True)
class KKTInfo:
    owner_inn: str
    owner_name: str | None
    model: str | None
    reg_number: str
    manufacturer_number: str | None
    fn_end_date: str | None
    ofd_end_date: str | None


@dataclass(frozen=True)
class KKTLookupResult:
    owner_inn: str
    contractor_id: int | None
    accounts_count: int
    source_kkt_count: int
    available_kkt_count: int
    foreign_kkt_count: int
    expired_kkt_count: int
    cash_registers: tuple[KKTInfo, ...]
    errors: tuple[str, ...]
    saved_kkt_count: int = 0

    @property
    def cash_register(self) -> KKTInfo | None:
        return self.cash_registers[0] if self.cash_registers else None


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_sbis_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_text(value) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def normalize_reg_number(value: str) -> str:
    reg_number = "".join(str(value).split())

    if not reg_number.isdigit() or len(reg_number) != 16:
        raise ValueError("Регистрационный номер должен содержать 16 цифр")

    return reg_number


def normalize_manufacturer_number(value: str) -> str:
    number = str(value).strip()

    if not number or len(number) > 64:
        raise ValueError("Некорректный заводской номер")

    return number


def normalize_date_text(value) -> str | None:
    parsed = parse_sbis_date(value)

    if parsed is not None:
        return parsed.isoformat()

    return normalize_text(value)


def extract_owner_name(detail: dict) -> str | None:
    head_chief = detail.get("headChief")

    if not isinstance(head_chief, dict):
        return None

    full_name = " ".join(
        str(head_chief.get(field) or "").strip()
        for field in ("Фамилия", "Имя", "Отчество")
    ).strip()

    return full_name or normalize_text(
        head_chief.get("Лицо.Название")
    )


def extract_kkt_owner_inn(item: dict) -> str | None:
    registry = item.get("registry") or {}
    detail = item.get("kkt") or {}

    if not isinstance(registry, dict):
        registry = {}

    if not isinstance(detail, dict):
        detail = {}

    return normalize_text(
        detail.get("ИНН")
        or item.get("kkt_inn")
        or registry.get("INN")
    )


def parse_kkt_info(item: dict, owner_inn: str) -> KKTInfo | None:
    registry = item.get("registry") or {}
    detail = item.get("kkt") or {}

    if not isinstance(registry, dict):
        registry = {}

    if not isinstance(detail, dict):
        detail = {}

    reg_number = normalize_text(
        detail.get("НомерРегистрационный")
        or registry.get("KKTRegId")
    )

    if reg_number is None:
        return None

    license_data = registry.get("LicenseData") or {}

    if not isinstance(license_data, dict):
        license_data = {}

    model_info = detail.get("kktModel") or {}

    if not isinstance(model_info, dict):
        model_info = {}

    fn_end_date = (
        item.get("fn_end_date")
        or detail.get("FSEndDate")
        or license_data.get("finish_fs_day")
    )
    ofd_end_date = (
        item.get("ofd_end_date")
        or extract_ofd_end_date(
            registry=registry,
            detail=detail,
        )
    )

    return KKTInfo(
        owner_inn=owner_inn,
        owner_name=extract_owner_name(detail),
        model=normalize_text(
            detail.get("ОборудованиеНазвание")
            or model_info.get("Name")
            or registry.get("KKTName")
        ),
        reg_number=reg_number,
        manufacturer_number=normalize_text(
            detail.get("НомерПроизводителя")
        ),
        fn_end_date=normalize_date_text(fn_end_date),
        ofd_end_date=normalize_date_text(ofd_end_date),
    )


def normalize_collector_error(value) -> str:
    if not isinstance(value, dict):
        return str(value)

    stage = normalize_text(value.get("stage")) or "СБИС"
    message = normalize_text(value.get("error")) or "неизвестная ошибка"
    return f"{stage}: {message}"


def kkt_sort_key(item: KKTInfo) -> tuple:
    fn_date = parse_sbis_date(item.fn_end_date)
    return (
        fn_date is None,
        fn_date or date.max,
        item.reg_number,
    )


def ensure_database_backup(
    *,
    status_callback: Callable[[str], None] | None = None,
) -> Path:
    database_path = Path(database.DB_PATH).resolve()
    backup_dir = PROJECT_ROOT / "data" / "backups"
    backup_path = backup_dir / (
        f"{database_path.stem}_before_telegram"
        f"{database_path.suffix}"
    )

    with _DATABASE_BACKUP_LOCK:
        if database_path in _BACKED_UP_DATABASES:
            return backup_path

        if not database_path.is_file():
            raise FileNotFoundError(f"База не найдена: {database_path}")

        if status_callback is not None:
            status_callback("создаю резервную копию основной базы…")

        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        temporary_path = backup_dir / (
            f".{database_path.stem}_before_telegram_{timestamp}.tmp"
        )

        try:
            with (
                closing(sqlite3.connect(database_path)) as source,
                closing(sqlite3.connect(temporary_path)) as destination,
            ):
                source.backup(destination)

            temporary_path.replace(backup_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        legacy_pattern = (
            f"{database_path.stem}_before_telegram_*"
            f"{database_path.suffix}"
        )

        for legacy_path in backup_dir.glob(legacy_pattern):
            if legacy_path.resolve() != backup_path.resolve():
                legacy_path.unlink()

        _BACKED_UP_DATABASES.add(database_path)
    return backup_path


def parse_database_kkt_row(row) -> KKTInfo:
    detail: dict = {}
    raw_json = row["raw_json"]

    if raw_json:
        try:
            raw_data = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            raw_data = {}

        if isinstance(raw_data, dict):
            candidate = raw_data.get("detail") or raw_data.get("kkt") or {}

            if isinstance(candidate, dict):
                detail = candidate

    model_info = detail.get("kktModel") or {}

    if not isinstance(model_info, dict):
        model_info = {}

    return KKTInfo(
        owner_inn=normalize_text(row["inn"] or detail.get("ИНН")) or "—",
        owner_name=extract_owner_name(detail),
        model=normalize_text(
            row["model"]
            or detail.get("ОборудованиеНазвание")
            or model_info.get("Name")
        ),
        reg_number=normalize_text(row["reg_number"]) or "—",
        manufacturer_number=normalize_text(
            row["manufacturer_number"]
            or detail.get("НомерПроизводителя")
        ),
        fn_end_date=normalize_date_text(
            row["fn_end_date"] or detail.get("FSEndDate")
        ),
        ofd_end_date=normalize_date_text(
            row["ofd_end_date"]
            or extract_ofd_end_date(detail=detail)
        ),
    )


def find_all_kkt_by_owner_inn(
    owner_inn: str,
    *,
    kpp: str | None = None,
    today: date | None = None,
    verbose: bool = True,
    save_to_database: bool = False,
    backup_before_write: bool = True,
    status_callback: Callable[[str], None] | None = None,
) -> KKTLookupResult:
    """Возвращает ККТ только запрошенного владельца из live-ответов СБИС.

    ККТ с известным сроком ФН раньше даты ``today - 3 месяца`` исключаются.
    ККТ без срока ФН сохраняются в результате, поскольку нельзя доказать, что
    их ФН закончился более трёх месяцев назад.
    """
    owner_inn = normalize_inn(owner_inn)
    report_date = today or date.today()
    cutoff = subtract_months(report_date, 3)
    output_context = nullcontext() if verbose else redirect_stdout(io.StringIO())

    if save_to_database and backup_before_write:
        ensure_database_backup(status_callback=status_callback)

    if status_callback is not None:
        status_callback("получаю аккаунты и реестр ККТ из СБИС…")

    with output_context:
        raw_result = get_all_kkt_by_inn(
            inn=owner_inn,
            kpp=kpp,
            owner_inn_filter=owner_inn,
        )

    if status_callback is not None:
        status_callback("проверяю владельца и сроки найденных ККТ…")

    raw_items = raw_result.get("kkt") or []
    accounts = raw_result.get("accounts") or []
    errors = [
        normalize_collector_error(error)
        for error in (raw_result.get("errors") or [])
    ]
    selected: list[KKTInfo] = []
    seen_reg_numbers: set[str] = set()
    foreign_kkt_count = int(
        raw_result.get("skipped_foreign_owner") or 0
    )
    expired_kkt_count = 0
    saved_kkt_count = 0

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        actual_owner_inn = extract_kkt_owner_inn(item)

        # Аккаунт может быть общим для нескольких владельцев. При отсутствии
        # подтверждённого совпадения ИНН кассу также не возвращаем.
        if actual_owner_inn != owner_inn:
            foreign_kkt_count += 1
            continue

        kkt = parse_kkt_info(item, owner_inn)

        if kkt is None or kkt.reg_number in seen_reg_numbers:
            continue

        seen_reg_numbers.add(kkt.reg_number)

        if save_to_database:
            try:
                if status_callback is not None and saved_kkt_count == 0:
                    status_callback("сохраняю кассы в основную базу…")

                save_kkt(item)
                saved_kkt_count += 1
            except Exception as error:
                errors.append(
                    "SQLite save_kkt "
                    f"РНМ={kkt.reg_number}: {type(error).__name__}: {error}"
                )

        fn_end_date = parse_sbis_date(kkt.fn_end_date)

        if fn_end_date is not None and fn_end_date < cutoff:
            expired_kkt_count += 1
            continue

        selected.append(kkt)

    selected.sort(key=kkt_sort_key)

    if status_callback is not None:
        status_callback("формирую результат…")

    return KKTLookupResult(
        owner_inn=owner_inn,
        contractor_id=raw_result.get("contractor_id"),
        accounts_count=len(accounts),
        source_kkt_count=len(raw_items),
        available_kkt_count=len(selected),
        foreign_kkt_count=foreign_kkt_count,
        expired_kkt_count=expired_kkt_count,
        cash_registers=tuple(selected),
        errors=tuple(errors),
        saved_kkt_count=saved_kkt_count,
    )


def find_kkt_by_owner_inn(
    owner_inn: str,
    *,
    kpp: str | None = None,
    today: date | None = None,
    verbose: bool = True,
    save_to_database: bool = False,
    backup_before_write: bool = True,
    status_callback: Callable[[str], None] | None = None,
) -> KKTLookupResult:
    """Возвращает первую актуальную ККТ владельца и общую статистику."""
    result = find_all_kkt_by_owner_inn(
        owner_inn,
        kpp=kpp,
        today=today,
        verbose=verbose,
        save_to_database=save_to_database,
        backup_before_write=backup_before_write,
        status_callback=status_callback,
    )
    return replace(
        result,
        cash_registers=result.cash_registers[:1],
    )


def display_value(value: str | None) -> str:
    return value if value not in {None, ""} else "—"


def print_result(result: KKTLookupResult, *, show_all: bool) -> None:
    registers = (
        result.cash_registers
        if show_all
        else result.cash_registers[:1]
    )

    print(f"ИНН владельца: {result.owner_inn}")
    print(f"Подходящих ККТ: {result.available_kkt_count}")

    for number, item in enumerate(registers, start=1):
        print()

        if show_all:
            print(f"Касса {number}")

        print("ФИО владельца:", display_value(item.owner_name))
        print("Модель кассы:", display_value(item.model))
        print("Рег. номер:", item.reg_number)
        print("Заводской номер:", display_value(item.manufacturer_number))
        print("Срок ФН:", display_value(item.fn_end_date))
        print("Срок ОФД:", display_value(item.ofd_end_date))

    if result.expired_kkt_count:
        print("Исключено по сроку ФН:", result.expired_kkt_count)

    if result.foreign_kkt_count:
        print("Исключено чужих ККТ:", result.foreign_kkt_count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Получает данные ККТ владельца из СБИС по ИНН."
    )
    parser.add_argument("-i", "--inn", required=True, help="ИНН владельца")
    parser.add_argument("--kpp", help="КПП владельца, если требуется")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Показать все ККТ, а не только первую",
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    lookup = (
        find_all_kkt_by_owner_inn
        if arguments.all
        else find_kkt_by_owner_inn
    )
    result = lookup(arguments.inn, kpp=arguments.kpp)
    print_result(result, show_all=arguments.all)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nЗапрос прерван пользователем.")
        raise SystemExit(130)
    except Exception as error:
        print(f"\nОшибка: {type(error).__name__}: {error}")
        raise SystemExit(1)
