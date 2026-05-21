"""HTTP, rate-limit, retry, and circuit-breaker primitives."""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests

from .utils import TTLCache, should_emit_service_log, stable_json_key, utc_now_iso


@dataclass
class BackoffConfig:
    attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    jitter_seconds: float = 0.15
    retry_statuses: set[int] = field(default_factory=lambda: {408, 409, 425, 429, 500, 502, 503, 504})

    def delay_for_attempt(self, attempt_index: int) -> float:
        exponent = max(0, int(attempt_index))
        raw_delay = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** exponent))
        if self.jitter_seconds > 0:
            raw_delay += random.uniform(0.0, self.jitter_seconds)
        return max(0.0, raw_delay)


class RateLimiter:
    """Sliding-window limiter for REST calls."""

    def __init__(self, max_calls: int = 120, window_seconds: float = 60.0, logger=None):
        self.max_calls = max(1, int(max_calls or 1))
        self.window_seconds = max(0.001, float(window_seconds or 60.0))
        self.logger = logger
        self._lock = threading.RLock()
        self._calls: deque[float] = deque()

    def wait(self) -> float:
        slept = 0.0
        while True:
            now = time.time()
            with self._lock:
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return slept
                wait_for = max(0.001, self.window_seconds - (now - self._calls[0]))
            if callable(self.logger) and should_emit_service_log("[HTTP-RATE-LIMIT]"):
                self.logger(
                    "[HTTP-RATE-LIMIT]",
                    waiting_seconds=round(wait_for, 4),
                    max_calls=self.max_calls,
                    window_seconds=self.window_seconds,
                    timestamp=utc_now_iso(),
                )
            time.sleep(wait_for)
            slept += wait_for


class CircuitBreaker:
    """Fail-fast guard for repeated REST failures."""

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30.0, logger=None):
        self.failure_threshold = max(1, int(failure_threshold or 1))
        self.recovery_seconds = max(0.1, float(recovery_seconds or 30.0))
        self.logger = logger
        self._lock = threading.RLock()
        self.failure_count = 0
        self.opened_at = 0.0
        self.last_error = None

    def before_request(self) -> None:
        with self._lock:
            if not self.opened_at:
                return
            now = time.time()
            if now - self.opened_at >= self.recovery_seconds:
                if callable(self.logger) and should_emit_service_log("[HTTP-CIRCUIT-HALF-OPEN]"):
                    self.logger("[HTTP-CIRCUIT-HALF-OPEN]", timestamp=utc_now_iso())
                self.opened_at = 0.0
                self.failure_count = 0
                return
            raise RuntimeError(f"http_circuit_open:{self.last_error or 'unknown'}")

    def record_success(self) -> None:
        with self._lock:
            if self.failure_count or self.opened_at:
                if callable(self.logger) and should_emit_service_log("[HTTP-CIRCUIT-CLOSED]"):
                    self.logger("[HTTP-CIRCUIT-CLOSED]", timestamp=utc_now_iso())
            self.failure_count = 0
            self.opened_at = 0.0
            self.last_error = None

    def record_failure(self, reason: str) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_error = reason
            if self.failure_count >= self.failure_threshold and not self.opened_at:
                self.opened_at = time.time()
                if callable(self.logger) and should_emit_service_log("[HTTP-CIRCUIT-OPEN]"):
                    self.logger(
                        "[HTTP-CIRCUIT-OPEN]",
                        reason=reason,
                        failures=self.failure_count,
                        recovery_seconds=self.recovery_seconds,
                        timestamp=utc_now_iso(),
                    )

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "failure_count": self.failure_count,
                "open": bool(self.opened_at),
                "opened_at": self.opened_at,
                "last_error": self.last_error,
            }


class KalshiHttpClient:
    """Requests session with cache, backoff, rate limiting, and circuit breaker."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        backoff: BackoffConfig | None = None,
        cache: TTLCache | None = None,
        logger=None,
    ):
        self.session = session or requests.Session()
        self.logger = logger
        self.rate_limiter = rate_limiter or RateLimiter(logger=logger)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(logger=logger)
        self.backoff = backoff or BackoffConfig()
        self.cache = cache or TTLCache(max_entries=2048, default_ttl_seconds=5.0)
        self.request_count = 0
        self.retry_count = 0
        self.cache_hits = 0
        self.last_response_status = None

    def _log(self, prefix: str, **fields: Any) -> None:
        if not should_emit_service_log(prefix):
            return
        if callable(self.logger):
            self.logger(prefix, **fields)

    def _cache_key(self, method: str, url: str, params: dict[str, Any] | None, body: Any = None) -> str:
        return stable_json_key(
            {
                "method": method.upper(),
                "url": url,
                "params": params or {},
                "body": body if method.upper() != "GET" else None,
            }
        )

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
        cache_ttl: float = 0.0,
        bypass_cache: bool = False,
    ) -> dict[str, Any]:
        return self.request_json(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            cache_ttl=cache_ttl,
            bypass_cache=bypass_cache,
        )

    def post_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        return self.request_json("POST", url, params=params, json_body=json_body, data=data, headers=headers, timeout=timeout)

    def delete_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        return self.request_json("DELETE", url, params=params, headers=headers, timeout=timeout)

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
        cache_ttl: float = 0.0,
        bypass_cache: bool = False,
    ) -> dict[str, Any]:
        method = method.upper()
        cache_key = self._cache_key(method, url, params, json_body if json_body is not None else data)
        if method == "GET" and cache_ttl > 0 and not bypass_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.cache_hits += 1
                self._log("[HTTP-CACHE-HIT]", method=method, url=url, timestamp=utc_now_iso())
                return cached

        attempts = max(1, int(self.backoff.attempts or 1))
        last_exc: Exception | None = None
        for attempt_index in range(attempts):
            self.circuit_breaker.before_request()
            self.rate_limiter.wait()
            self.request_count += 1
            response = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
                self.last_response_status = response.status_code
                if response.status_code in self.backoff.retry_statuses and attempt_index < attempts - 1:
                    self.retry_count += 1
                    self._log(
                        "[HTTP-RETRY]",
                        method=method,
                        status=response.status_code,
                        attempt=attempt_index + 1,
                        url=url,
                        timestamp=utc_now_iso(),
                    )
                    time.sleep(self.backoff.delay_for_attempt(attempt_index))
                    continue
                response.raise_for_status()
                payload = response.json() if response.content else {}
                self.circuit_breaker.record_success()
                if method == "GET" and cache_ttl > 0:
                    self.cache.set(cache_key, payload, ttl_seconds=cache_ttl)
                return payload
            except requests.RequestException as exc:
                last_exc = exc
                reason = exc.__class__.__name__
                status = getattr(response, "status_code", None)
                if status:
                    reason = f"http_{status}"
                if attempt_index < attempts - 1:
                    self.retry_count += 1
                    self._log(
                        "[HTTP-RETRY]",
                        method=method,
                        reason=reason,
                        attempt=attempt_index + 1,
                        url=url,
                        timestamp=utc_now_iso(),
                    )
                    time.sleep(self.backoff.delay_for_attempt(attempt_index))
                    continue
                self.circuit_breaker.record_failure(reason)
                raise

        self.circuit_breaker.record_failure(last_exc.__class__.__name__ if last_exc else "unknown")
        if last_exc:
            raise last_exc
        raise RuntimeError("request_failed_without_exception")

    def summary(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "retry_count": self.retry_count,
            "cache_hits": self.cache_hits,
            "last_response_status": self.last_response_status,
            "cache": self.cache.summary(),
            "circuit": self.circuit_breaker.summary(),
        }
