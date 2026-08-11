import threading


_output_lock = threading.Lock()
_status_width = 0


def _clear_status_unlocked() -> None:
    global _status_width

    if _status_width:
        print("\r" + (" " * _status_width) + "\r", end="", flush=True)
        _status_width = 0


def clear_status() -> None:
    """Убирает временную строку прогресса из консоли."""
    with _output_lock:
        _clear_status_unlocked()


def show_status(message: str) -> None:
    """Обновляет одну временную строку, не создавая поток сообщений."""
    global _status_width

    message = " ".join(str(message).split())

    with _output_lock:
        width = max(_status_width, len(message))
        print("\r" + message.ljust(width), end="", flush=True)
        _status_width = width


def print_event(message: str = "") -> None:
    """Печатает постоянное событие, предварительно убрав строку прогресса."""
    with _output_lock:
        _clear_status_unlocked()
        print(message, flush=True)
