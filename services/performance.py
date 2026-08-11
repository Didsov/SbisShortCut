import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from services.console_output import print_event


@dataclass
class RpcMethodStats:
    calls: int = 0
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    transport_retries: int = 0
    api_errors: int = 0
    backoff_seconds: float = 0.0
    total_seconds: float = 0.0
    max_seconds: float = 0.0
    in_flight: int = 0
    max_in_flight: int = 0


class PerformanceMonitor:
    """Потокобезопасный фоновый монитор одного запуска CRM-списка."""

    def __init__(
        self,
        *,
        list_id: int,
        report_dir: Path,
        interval_seconds: float = 60.0,
    ) -> None:
        self.list_id = list_id
        self.report_dir = Path(report_dir)
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.started_at = datetime.now()
        self.started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped = False
        self._rpc: dict[str, RpcMethodStats] = {}
        self._rpc_durations: dict[str, deque[float]] = {}
        self._pages = 0
        self._crm_rows = 0
        self._clients_processed = 0
        self._clients_failed = 0
        self._clients_skipped = 0
        self._kkt_collected = 0
        self._kkt_writes = 0
        self._client_retries = 0
        self._kkt_read_retries = 0
        self._registry_retries = 0
        self._last_progress_monotonic = self.started_monotonic

        timestamp = self.started_at.strftime("%Y-%m-%d_%H-%M-%S_%f")
        self.report_path = (
            self.report_dir
            / f"list_{self.list_id}_{timestamp}_pid{os.getpid()}.json"
        )

    def __enter__(self):
        activate_monitor(self)
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()
        deactivate_monitor(self)

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._background_loop,
            name="performance-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True

        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)

        self.publish(final=True)

    def rpc_started(self, method: str) -> float:
        started = time.monotonic()

        with self._lock:
            stats = self._rpc.setdefault(method, RpcMethodStats())
            stats.calls += 1
            stats.in_flight += 1
            stats.max_in_flight = max(
                stats.max_in_flight,
                stats.in_flight,
            )

        return started

    def rpc_attempted(self, method: str) -> None:
        with self._lock:
            self._rpc.setdefault(method, RpcMethodStats()).attempts += 1

    def rpc_retried(self, method: str) -> None:
        with self._lock:
            stats = self._rpc.setdefault(method, RpcMethodStats())
            stats.transport_retries += 1

    def rpc_api_error(self, method: str) -> None:
        with self._lock:
            self._rpc.setdefault(method, RpcMethodStats()).api_errors += 1

    def rpc_backoff(self, method: str, seconds: float) -> None:
        with self._lock:
            stats = self._rpc.setdefault(method, RpcMethodStats())
            stats.backoff_seconds += max(0.0, seconds)

    def rpc_finished(
        self,
        method: str,
        started: float,
        success: bool,
    ) -> None:
        elapsed = max(0.0, time.monotonic() - started)

        with self._lock:
            stats = self._rpc.setdefault(method, RpcMethodStats())
            stats.in_flight = max(0, stats.in_flight - 1)
            stats.total_seconds += elapsed
            stats.max_seconds = max(stats.max_seconds, elapsed)
            durations = self._rpc_durations.setdefault(
                method,
                deque(maxlen=2000),
            )
            durations.append(elapsed)

            if success:
                stats.successes += 1
                self._last_progress_monotonic = time.monotonic()
            else:
                stats.failures += 1

    def record_page(self, received: int) -> None:
        with self._lock:
            self._pages += 1
            self._crm_rows += max(0, received)
            self._last_progress_monotonic = time.monotonic()

    def record_client(
        self,
        outcome: str,
        kkt_count: int = 0,
    ) -> None:
        with self._lock:
            if outcome == "processed":
                self._clients_processed += 1
                self._last_progress_monotonic = time.monotonic()
            elif outcome == "failed":
                self._clients_failed += 1
            elif outcome == "skipped":
                self._clients_skipped += 1

            self._kkt_collected += max(0, kkt_count)

    def record_kkt_write(self) -> None:
        with self._lock:
            self._kkt_writes += 1
            self._last_progress_monotonic = time.monotonic()

    def record_operation_retry(self, level: str) -> None:
        with self._lock:
            if level == "client":
                self._client_retries += 1
            elif level == "kkt_read":
                self._kkt_read_retries += 1
            elif level == "registry":
                self._registry_retries += 1

    def snapshot(self) -> dict:
        elapsed = max(0.001, time.monotonic() - self.started_monotonic)
        elapsed_minutes = elapsed / 60

        with self._lock:
            rpc = {}

            for method, stats in sorted(self._rpc.items()):
                data = asdict(stats)
                durations = sorted(
                    self._rpc_durations.get(method, ())
                )
                p95_index = max(
                    0,
                    int(len(durations) * 0.95) - 1,
                )
                data["average_seconds"] = (
                    stats.total_seconds / stats.calls
                    if stats.calls
                    else 0.0
                )
                data["p95_seconds"] = (
                    durations[p95_index]
                    if durations
                    else 0.0
                )
                data["calls_per_minute"] = stats.calls / elapsed_minutes
                rpc[method] = data

            return {
                "list_id": self.list_id,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "elapsed_seconds": elapsed,
                "pages": self._pages,
                "crm_rows": self._crm_rows,
                "clients": {
                    "processed": self._clients_processed,
                    "failed": self._clients_failed,
                    "skipped": self._clients_skipped,
                    "processed_per_minute": (
                        self._clients_processed / elapsed_minutes
                    ),
                },
                "kkt": {
                    "collected": self._kkt_collected,
                    "writes": self._kkt_writes,
                    "collected_per_minute": (
                        self._kkt_collected / elapsed_minutes
                    ),
                    "writes_per_minute": self._kkt_writes / elapsed_minutes,
                },
                "retries": {
                    "client": self._client_retries,
                    "kkt_read": self._kkt_read_retries,
                    "registry": self._registry_retries,
                },
                "seconds_without_progress": max(
                    0.0,
                    time.monotonic() - self._last_progress_monotonic,
                ),
                "rpc": rpc,
            }

    def publish(self, *, final: bool = False) -> None:
        try:
            snapshot = self.snapshot()
            self._print_snapshot(snapshot, final=final)
            self._write_snapshot(snapshot, final=final)

            if final:
                print_event(
                    f"[СКОРОСТЬ] Подробный отчёт: {self.report_path.resolve()}"
                )
        except Exception as error:
            # Монитор никогда не должен останавливать основной сбор.
            print_event(f"[СКОРОСТЬ] Ошибка фонового мониторинга: {error}")

    def _background_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.publish()

    def _print_snapshot(self, snapshot: dict, *, final: bool) -> None:
        elapsed = snapshot["elapsed_seconds"]
        clients = snapshot["clients"]
        kkt = snapshot["kkt"]
        retries = snapshot["retries"]
        kkt_rpc = snapshot["rpc"].get("KKT.Read", {})
        title = "итог" if final else "сейчас"

        print_event(
            f"[СКОРОСТЬ {title}] {elapsed / 60:.1f} мин | "
            f"клиенты={clients['processed']} "
            f"({clients['processed_per_minute']:.2f}/мин), "
            f"ошибки={clients['failed']}, "
            f"пропуск={clients['skipped']} | "
            f"ККТ={kkt['collected']} "
            f"({kkt['collected_per_minute']:.2f}/мин), "
            f"записей БД={kkt['writes']}, "
            f"KKT.Read={kkt_rpc.get('successes', 0)}/"
            f"{kkt_rpc.get('calls', 0)}, "
            f"в работе={kkt_rpc.get('in_flight', 0)} | "
            f"повторы: HTTP={sum(x['transport_retries'] for x in snapshot['rpc'].values())}, "
            f"KKT={retries['kkt_read']}, Registry={retries['registry']}, "
            f"клиент={retries['client']} | "
            f"без прогресса={snapshot['seconds_without_progress']:.1f}с"
        )

    def _write_snapshot(self, snapshot: dict, *, final: bool) -> None:
        payload = dict(snapshot)
        payload["final"] = final
        payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = self.report_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.report_path)


class RpcTracker:
    def __init__(self, method: str) -> None:
        self.method = method
        self.monitor = get_active_monitor()
        self.started = 0.0
        self.success = False

    def __enter__(self):
        if self.monitor is not None:
            self.started = self.monitor.rpc_started(self.method)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.monitor is not None:
            self.monitor.rpc_finished(
                self.method,
                self.started,
                self.success,
            )

    def attempt(self) -> None:
        if self.monitor is not None:
            self.monitor.rpc_attempted(self.method)

    def retry(self) -> None:
        if self.monitor is not None:
            self.monitor.rpc_retried(self.method)

    def api_error(self) -> None:
        if self.monitor is not None:
            self.monitor.rpc_api_error(self.method)

    def backoff(self, seconds: float) -> None:
        if self.monitor is not None:
            self.monitor.rpc_backoff(self.method, seconds)

    def mark_success(self) -> None:
        self.success = True


_active_monitor: PerformanceMonitor | None = None
_active_monitor_lock = threading.Lock()


def activate_monitor(monitor: PerformanceMonitor) -> None:
    global _active_monitor

    with _active_monitor_lock:
        if _active_monitor is not None and _active_monitor is not monitor:
            raise RuntimeError("Монитор производительности уже запущен")
        _active_monitor = monitor


def deactivate_monitor(monitor: PerformanceMonitor) -> None:
    global _active_monitor

    with _active_monitor_lock:
        if _active_monitor is monitor:
            _active_monitor = None


def get_active_monitor() -> PerformanceMonitor | None:
    with _active_monitor_lock:
        return _active_monitor


def track_rpc(method: str) -> RpcTracker:
    return RpcTracker(method)


def record_page(received: int) -> None:
    monitor = get_active_monitor()
    if monitor is not None:
        monitor.record_page(received)


def record_client(outcome: str, kkt_count: int = 0) -> None:
    monitor = get_active_monitor()
    if monitor is not None:
        monitor.record_client(outcome, kkt_count)


def record_kkt_write() -> None:
    monitor = get_active_monitor()
    if monitor is not None:
        monitor.record_kkt_write()


def record_operation_retry(level: str) -> None:
    monitor = get_active_monitor()
    if monitor is not None:
        monitor.record_operation_retry(level)
