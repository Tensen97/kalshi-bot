import time
from collections import deque
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WebSocketHealth:
    def __init__(self, summary_interval_seconds=60.0, heartbeat_timeout_seconds=30.0):
        self.summary_interval_seconds = float(summary_interval_seconds or 60.0)
        self.heartbeat_timeout_seconds = float(heartbeat_timeout_seconds or 30.0)
        self.started_at = time.time()
        self.connected = False
        self.connect_count = 0
        self.disconnect_count = 0
        self.reconnect_count = 0
        self.heartbeat_count = 0
        self.message_count = 0
        self.error_count = 0
        self.fallback_rest_count = 0
        self.subscription_count = 0
        self.last_connect_ts = None
        self.last_disconnect_ts = None
        self.last_heartbeat_ts = None
        self.last_message_ts = None
        self.last_error_ts = None
        self.last_summary_ts = 0.0
        self.last_latency_ms = None
        self._last_message_log_count = 0
        self.reconnect_history = deque(maxlen=20)
        self.errors = deque(maxlen=20)

    def _log(self, prefix, **fields):
        parts = [prefix]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        print(" ".join(parts))

    def record_connect(self):
        now = time.time()
        self.connected = True
        self.connect_count += 1
        self.last_connect_ts = now
        self._log("[WS-CONNECT]", connect_count=self.connect_count, connected="yes", timestamp=_now_iso())

    def record_disconnect(self, reason="unknown"):
        now = time.time()
        self.connected = False
        self.disconnect_count += 1
        self.last_disconnect_ts = now
        self._log("[WS-DISCONNECT]", disconnect_count=self.disconnect_count, reason=reason, timestamp=_now_iso())

    def record_reconnect(self, reason="unknown", delay_seconds=None):
        now = time.time()
        self.reconnect_count += 1
        self.reconnect_history.append({"ts": now, "reason": reason, "delay_seconds": delay_seconds})
        self._log(
            "[WS-RECONNECT]",
            reconnect_count=self.reconnect_count,
            reason=reason,
            delay_seconds=delay_seconds if delay_seconds is not None else "none",
            timestamp=_now_iso(),
        )

    def record_heartbeat(self, latency_ms=None):
        now = time.time()
        self.heartbeat_count += 1
        self.last_heartbeat_ts = now
        if latency_ms is not None:
            self.last_latency_ms = float(latency_ms)
        self._log(
            "[WS-HEARTBEAT]",
            heartbeat_count=self.heartbeat_count,
            latency_ms=self.last_latency_ms if self.last_latency_ms is not None else "none",
            timestamp=_now_iso(),
        )

    def record_message(self, message_type="message"):
        now = time.time()
        self.message_count += 1
        self.last_message_ts = now
        if self.message_count == 1 or self.message_count % 25 == 0 or message_type in {"heartbeat", "ping", "pong", "disconnect"}:
            self._last_message_log_count = self.message_count
            self._log("[WS-MSG-RATE]", message_count=self.message_count, message_type=message_type, timestamp=_now_iso())

    def record_error(self, reason="unknown"):
        now = time.time()
        self.error_count += 1
        self.last_error_ts = now
        self.errors.append({"ts": now, "reason": reason})
        self._log("[WS-ERROR]", error_count=self.error_count, reason=reason, timestamp=_now_iso())

    def record_subscription_count(self, count):
        self.subscription_count = int(count or 0)
        self._log("[WS-SUBSCRIPTION-COUNT]", subscription_count=self.subscription_count, timestamp=_now_iso())

    def record_rest_fallback(self, reason="stale_feed"):
        self.fallback_rest_count += 1
        self._log("[WS-FALLBACK-REST]", fallback_rest_count=self.fallback_rest_count, reason=reason, timestamp=_now_iso())

    def is_stale(self):
        if self.last_message_ts is None:
            return True
        return (time.time() - float(self.last_message_ts)) > self.heartbeat_timeout_seconds

    def summary(self):
        now = time.time()
        uptime_seconds = max(0.0, now - float(self.started_at))
        message_rate = self.message_count / max(uptime_seconds, 1.0)
        heartbeat_age = None if self.last_heartbeat_ts is None else round(now - self.last_heartbeat_ts, 4)
        message_age = None if self.last_message_ts is None else round(now - self.last_message_ts, 4)
        return {
            "connected": self.connected,
            "connect_count": self.connect_count,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "heartbeat_count": self.heartbeat_count,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "fallback_rest_count": self.fallback_rest_count,
            "subscription_count": self.subscription_count,
            "uptime_seconds": round(uptime_seconds, 4),
            "message_rate_per_second": round(message_rate, 6),
            "last_latency_ms": self.last_latency_ms,
            "heartbeat_age_seconds": heartbeat_age,
            "message_age_seconds": message_age,
            "stale": self.is_stale(),
            "last_summary_ts": self.last_summary_ts,
        }

    def maybe_log_summary(self, force=False):
        now = time.time()
        if not force and (now - float(self.last_summary_ts or 0.0)) < self.summary_interval_seconds:
            return None
        self.last_summary_ts = now
        summary = self.summary()
        self._log(
            "[WS-HEALTH-SUMMARY]",
            connected="yes" if summary["connected"] else "no",
            reconnects=summary["reconnect_count"],
            stale="yes" if summary["stale"] else "no",
            latency_ms=summary["last_latency_ms"] if summary["last_latency_ms"] is not None else "none",
            message_rate=summary["message_rate_per_second"],
            subscriptions=summary["subscription_count"],
            timestamp=_now_iso(),
        )
        return summary
