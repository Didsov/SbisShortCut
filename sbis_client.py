import base64
import time

import requests
from requests.adapters import HTTPAdapter

from config.cookies import COOKIES
from config.headers import HEADERS
from services.performance import RpcTracker, track_rpc


DEFAULT_URL = (
    "https://online.sbis.ru/"
    "billing_public/service/?x_version=26.3248-150.3"
)


class SBISClient:

    RETRYABLE_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        *,
        retry_attempts: int = 4,
        backoff_seconds: float = 0.75,
    ):

        self.retry_attempts = max(1, retry_attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)

        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.headers["Cookie"] = COOKIES.strip()
        adapter = HTTPAdapter(
            pool_connections=8,
            pool_maxsize=8,
            max_retries=0,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def call(
        self,
        method: str,
        params: dict,
        url: str = DEFAULT_URL,
        *,
        protocol: int = 7,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        with track_rpc(method) as metrics:
            return self._call(
                method=method,
                params=params,
                url=url,
                metrics=metrics,
                protocol=protocol,
                extra_headers=extra_headers,
            )

    def _call(
        self,
        *,
        method: str,
        params: dict,
        url: str,
        metrics: RpcTracker,
        protocol: int,
        extra_headers: dict[str, str] | None,
    ) -> dict:

        headers = self.session.headers.copy()

        headers["X-Calledmethod"] = method
        headers["X-Originalmethodname"] = (
            base64.b64encode(
                method.encode("utf-8")
            ).decode("ascii")
        )

        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "jsonrpc": "2.0",
            "protocol": protocol,
            "method": method,
            "params": params,
            "id": 1,
        }

        last_error: Exception | None = None

        for attempt in range(1, self.retry_attempts + 1):
            metrics.attempt()

            try:
                response = self.session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=(10, 35),
                )

                try:
                    data = response.json()
                except ValueError as error:
                    message = (
                        f"SBIS вернул не JSON. HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

                    if attempt >= self.retry_attempts:
                        raise RuntimeError(message) from error

                    last_error = RuntimeError(message)
                else:
                    if response.status_code < 400:
                        if "error" not in data:
                            metrics.mark_success()
                        else:
                            metrics.api_error()
                        return data

                    error_data = data.get("error")
                    if isinstance(error_data, dict):
                        details = (
                            error_data.get("details")
                            or error_data.get("message")
                            or response.text
                        )
                    else:
                        details = error_data or response.text
                    message = (
                        f"Ошибка SBIS HTTP {response.status_code}: {details}"
                    )

                    if (
                        response.status_code
                        not in self.RETRYABLE_STATUS_CODES
                        or attempt >= self.retry_attempts
                    ):
                        raise RuntimeError(message)

                    last_error = RuntimeError(message)

                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), 30.0)
                        except ValueError:
                            delay = self.backoff_seconds * attempt
                    else:
                        delay = self.backoff_seconds * attempt

                    metrics.retry()
                    metrics.backoff(delay)
                    time.sleep(delay)
                    continue

            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error

                if attempt >= self.retry_attempts:
                    raise RuntimeError(
                        f"Сетевая ошибка SBIS после {attempt} попыток: {error}"
                    ) from error

            if attempt < self.retry_attempts:
                metrics.retry()

            delay = self.backoff_seconds * attempt
            if delay > 0:
                metrics.backoff(delay)
                time.sleep(delay)

        raise RuntimeError(
            f"Не удалось выполнить {method}: {last_error}"
        )
