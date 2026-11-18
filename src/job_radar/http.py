"""A deliberately slow HTTP client: fixed pause per host, request budget, backoff on 429/5xx."""

from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

USER_AGENT = "job-radar/0.1 (personal job search; respects source terms)"


class BudgetExceeded(RuntimeError):
    """The per-run request budget for a source is used up."""


class PoliteClient:
    def __init__(
        self,
        min_interval_s: float = 2.0,
        max_requests: int = 500,
        max_retries: int = 3,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.requests = 0
        self._last: dict[str, float] = {}
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            follow_redirects=True,
        )

    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last.get(host)
        if last is not None:
            delta = self._clock() - last
            if delta < self.min_interval_s:
                self._sleep(self.min_interval_s - delta)
        self._last[host] = self._clock()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            if self.requests >= self.max_requests:
                raise BudgetExceeded(f"request budget of {self.max_requests} used up")
            self._wait_for_host(url)
            self.requests += 1
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                # Dropped keep-alive connections and timeouts: wait longer, then retry.
                if attempt == self.max_retries:
                    raise
                self._sleep(min(self.min_interval_s * 2 ** (attempt + 2), 300))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else self.min_interval_s * 2 ** (attempt + 1)
                self._sleep(min(delay, 300))
                continue
            return resp
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()
