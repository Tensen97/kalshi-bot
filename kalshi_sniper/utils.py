"""Small shared utilities for the modular Kalshi sniper services."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Hashable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw_default = "1" if default else "0"
    raw = (os.environ.get(name, raw_default) or raw_default).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        return float(default)


def env_int(name: str, default: int) -> int:
    try:
        return int(float((os.environ.get(name, str(default)) or str(default)).strip()))
    except (TypeError, ValueError):
        return int(default)


_MINIMAL_SERVICE_LOG_PREFIXES = (
    "[LIVE-MODE-STATUS]",
    "[LIVE-PREFLIGHT]",
    "[LIVE-CANARY]",
    "[LIVE-ENTRY]",
    "[LIVE-EXIT]",
    "[LIVE-EXECUTION]",
    "[LIVE-RESULT]",
    "[TRADE-RESULT]",
    "✅ Bot running",
)


def minimal_live_logging_active() -> bool:
    argv = set(sys.argv[1:])
    return bool(
        env_bool("MINIMAL_LIVE_LOGGING", True)
        and (
            {"--live", "--arm-live", "--run-live-canary", "--live-preflight"}.intersection(argv)
            or env_bool("KALSHI_ALLOW_LIVE", False)
            or env_bool("LIVE_ARMED", False)
            or env_bool("LIVE_ENV_CONFIRMED", False)
        )
    )


def should_emit_service_log(prefix: str) -> bool:
    if not minimal_live_logging_active():
        return True
    text = str(prefix or "").strip()
    return text.startswith(_MINIMAL_SERVICE_LOG_PREFIXES) or any(token in text for token in ("ERROR", "FAIL", "Traceback"))


def stable_json_key(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


class TTLCache:
    """Thread-safe TTL cache with a small LRU trim.

    This is intentionally tiny and dependency-free.  It is used only for short
    lived market-discovery pages and quote snapshots, not durable trading state.
    """

    def __init__(self, max_entries: int = 1024, default_ttl_seconds: float = 5.0):
        self.max_entries = max(1, int(max_entries or 1024))
        self.default_ttl_seconds = max(0.0, float(default_ttl_seconds or 0.0))
        self._lock = threading.RLock()
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return default
            expires_at, value = item
            if expires_at and expires_at <= now:
                self._data.pop(key, None)
                self.misses += 1
                self.evictions += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Hashable, value: Any, ttl_seconds: float | None = None) -> Any:
        ttl = self.default_ttl_seconds if ttl_seconds is None else float(ttl_seconds or 0.0)
        expires_at = time.time() + ttl if ttl > 0 else 0.0
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.evictions += 1
        return value

    def invalidate(self, prefix: str | None = None) -> int:
        removed = 0
        with self._lock:
            if prefix is None:
                removed = len(self._data)
                self._data.clear()
                self.evictions += removed
                return removed
            for key in list(self._data.keys()):
                if str(key).startswith(prefix):
                    self._data.pop(key, None)
                    removed += 1
            self.evictions += removed
        return removed

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
            }
