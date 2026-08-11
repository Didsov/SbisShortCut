from pathlib import Path
from collections.abc import Callable


DATA_DIR = Path("data")
BACKUP_DIR = DATA_DIR / "backups"
METRICS_DIR = DATA_DIR / "metrics"

# Настройки скорости и устойчивости сбора.
CRM_PAGE_SIZE = 100
KKT_WORKERS = 4
CLIENT_ATTEMPTS = 2

# За это время другой процесс не заберёт обрабатываемый AccountId.
# После аварийного завершения просроченная блокировка будет захвачена заново.
ACCOUNT_LEASE_SECONDS = 30 * 60

# Фоновый монитор автоматически пишет статистику в консоль и JSON.
METRICS_ENABLED = True
METRICS_INTERVAL_SECONDS = 60

# Защищает от случайного нажатия X в консоли Windows.
PROTECT_CONSOLE_CLOSE = True


def database_path_for_list(list_id: int) -> Path:
    """Возвращает отдельную SQLite-базу для CRM-списка."""
    return DATA_DIR / f"list_{list_id}.db"


def discover_database_paths(
    data_dir: Path | None = None,
) -> list[Path]:
    """Возвращает доступные SQLite-базы из рабочего каталога data."""
    directory = Path(data_dir) if data_dir is not None else DATA_DIR

    if not directory.exists():
        return []

    return sorted(
        (
            path
            for path in directory.glob("*.db")
            if path.is_file()
        ),
        key=lambda path: path.name.lower(),
    )


def request_database_path(
    list_id: int,
    stop_checker: Callable[[], bool] | None = None,
    *,
    data_dir: Path | None = None,
    input_func: Callable[[str], str] = input,
) -> Path:
    """Выбирает стандартную или уже существующую базу для записи списка."""
    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    default_path = directory / f"list_{list_id}.db"
    default_resolved = default_path.resolve()
    databases = [
        path
        for path in discover_database_paths(directory)
        if path.resolve() != default_resolved
    ]

    print()
    print("Куда сохранять результаты списка:")
    print(f"  0. Стандартная база: {default_path}")

    for number, path in enumerate(databases, start=1):
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  {number}. {path} ({size_mb:.1f} МБ)")

    print("  P. Указать путь к существующей базе")

    while True:
        if stop_checker is not None and stop_checker():
            raise KeyboardInterrupt

        choice = input_func("Выберите базу [0]: ").strip()

        if not choice or choice == "0":
            return default_path

        if choice.lower() in {"p", "п"}:
            if stop_checker is not None and stop_checker():
                raise KeyboardInterrupt

            raw_path = input_func("Введите путь к существующей .db: ").strip()

            if not raw_path:
                print("Путь не введён.")
                continue

            custom_path = Path(raw_path).expanduser()

            if not custom_path.is_absolute():
                custom_path = Path.cwd() / custom_path

            custom_path = custom_path.resolve()

            if not custom_path.is_file():
                print("База не найдена:", custom_path)
                continue

            return custom_path

        try:
            index = int(choice) - 1
        except ValueError:
            print("Введите номер базы, 0 или P.")
            continue

        if 0 <= index < len(databases):
            return databases[index]

        print("Базы с таким номером нет.")


def request_list_id(
    stop_checker: Callable[[], bool] | None = None,
) -> int:
    """Запрашивает корректный числовой ListId в консоли."""
    while True:
        if stop_checker is not None and stop_checker():
            raise KeyboardInterrupt

        value = input("Введите номер CRM-списка (ListId): ").strip()

        try:
            list_id = int(value)
        except ValueError:
            print("ListId должен быть положительным целым числом.")
            continue

        if list_id > 0:
            return list_id

        print("ListId должен быть положительным целым числом.")
