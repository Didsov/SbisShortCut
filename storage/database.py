import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path("data/test_small_list.db")


def configure_database(path: Path) -> None:
    """Устанавливает рабочую базу для текущего запуска процесса."""
    global DB_PATH
    DB_PATH = Path(path)

def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


def init_database() -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crm_client_id INTEGER,
                crm_person_id INTEGER,
                name TEXT,
                inn TEXT NOT NULL,
                kpp TEXT,
                address TEXT,
                billing_contractor_id INTEGER,
                parse_status TEXT NOT NULL DEFAULT 'pending',
                parsed_at TEXT,
                error TEXT,
                UNIQUE(inn, kpp)
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL UNIQUE,
                contractor_inn TEXT NOT NULL,
                account_name TEXT,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS kkt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                kkt_id INTEGER,
                reg_number TEXT,
                manufacturer_number TEXT,
                fn_number TEXT,
                kkt_name TEXT,
                model TEXT,
                organization TEXT,
                inn TEXT,
                kpp TEXT,
                address TEXT,
                active INTEGER,
                fn_end_date TEXT,
                ofd_end_date TEXT,
                raw_json TEXT,
                parsed_at TEXT,

                account_name TEXT,
                kkt_contractor_id INTEGER,
                source_client_inn TEXT,
                source_client_kpp TEXT,
                source_contractor_id INTEGER,
                status INTEGER,
                updated_at TEXT,

                UNIQUE(reg_number)
            );
            CREATE TABLE IF NOT EXISTS parse_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inn TEXT,
                account_id INTEGER,
                kkt_id INTEGER,
                stage TEXT,
                error TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS contractors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contractor_id INTEGER NOT NULL,
                inn TEXT NOT NULL UNIQUE,
                kpp TEXT,
                name TEXT,
                legal_address TEXT,
                region TEXT,
                account_id INTEGER,
                raw_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS account_scans (
                account_id INTEGER PRIMARY KEY,
                billing_contractor_id INTEGER NOT NULL,
                source_inn TEXT NOT NULL,
                account_name TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                worker_id TEXT,
                lease_until TEXT,
                registry_kkt_count INTEGER NOT NULL DEFAULT 0,
                saved_kkt_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS regional_contractors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_person_id INTEGER,
                print_contractor_id INTEGER,
                name TEXT,
                inn TEXT NOT NULL,
                kpp TEXT,
                manager_id INTEGER,
                manager_user_id INTEGER,
                manager_name TEXT,
                manager_guid TEXT,
                partner_name TEXT,
                partner_id INTEGER,
                user_id INTEGER,
                region_code TEXT NOT NULL,
                region_name TEXT,
                city TEXT,
                face_type TEXT,
                connected INTEGER,
                created_at TEXT,
                last_activity_at TEXT,
                license_end_date TEXT,
                active_license INTEGER,
                raw_json TEXT NOT NULL,
                source_page INTEGER NOT NULL,
                scan_id TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                parse_status TEXT NOT NULL DEFAULT 'pending',
                kkt_collected_at TEXT,
                error TEXT,
                crm_list_id INTEGER,
                crm_raw_json TEXT,
                crm_updated_at TEXT,
                collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_clients_inn
                ON clients(inn);

            CREATE INDEX IF NOT EXISTS idx_kkt_inn
                ON kkt(inn);

            CREATE INDEX IF NOT EXISTS idx_kkt_fn_end
                ON kkt(fn_end_date);

            CREATE INDEX IF NOT EXISTS idx_contractors_inn
                ON contractors(inn);

            CREATE INDEX IF NOT EXISTS idx_account_scans_status
                ON account_scans(status);

            CREATE UNIQUE INDEX IF NOT EXISTS uq_regional_contractors_identity
                ON regional_contractors(
                    region_code,
                    inn,
                    COALESCE(kpp, '')
                );

            CREATE INDEX IF NOT EXISTS idx_regional_contractors_pending
                ON regional_contractors(region_code, is_current, parse_status);

            CREATE INDEX IF NOT EXISTS idx_regional_contractors_manager
                ON regional_contractors(manager_id);
            """
        )

        regional_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(regional_contractors)"
            )
        }
        for column_name, column_type in (
            ("crm_list_id", "INTEGER"),
            ("crm_raw_json", "TEXT"),
            ("crm_updated_at", "TEXT"),
        ):
            if column_name not in regional_columns:
                connection.execute(
                    f"ALTER TABLE regional_contractors "
                    f"ADD COLUMN {column_name} {column_type}"
                )
        
