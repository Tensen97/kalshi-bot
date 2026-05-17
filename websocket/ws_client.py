import asyncio
import json
import os
import time
from datetime import datetime, timezone

try:
    import websockets
except Exception:  # pragma: no cover - optional dependency
    websockets = None


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json(value):
    try:
        return json.dumps(value)
    except Exception:
        return json.dumps({"raw": str(value)})


class WebSocketClient:
    def __init__(
        self,
        url=None,
        cache=None,
        health=None,
        subscribe_builder=None,
        message_handler=None,
        subscription_limit=200,
        max_reconnect_attempts=10,
        ping_interval_seconds=15.0,
        ping_timeout_seconds=10.0,
    ):
        self.url = (url or "").strip()
        self.cache = cache
        self.health = health
        self.subscribe_builder = subscribe_builder
        self.message_handler = message_handler
        self.subscription_limit = int(subscription_limit or 200)
        self.max_reconnect_attempts = int(max_reconnect_attempts or 10)
        self.ping_interval_seconds = float(ping_interval_seconds or 15.0)
        self.ping_timeout_seconds = float(ping_timeout_seconds or 10.0)
        self._current_targets = []
        self._socket = None
        self._last_ping_ts = 0.0

    def _log(self, prefix, **fields):
        parts = [prefix]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        print(" ".join(parts))

    def set_targets(self, targets):
        deduped = []
        seen = set()
        for target in targets or []:
            target = str(target or "").strip()
            if not target or target in seen:
                continue
            seen.add(target)
            deduped.append(target)
        self._current_targets = deduped[: self.subscription_limit]
        return list(self._current_targets)

    def build_subscription_payload(self):
        if callable(self.subscribe_builder):
            return self.subscribe_builder(list(self._current_targets))
        if not self._current_targets:
            return None
        return {
            "type": "subscribe",
            "markets": list(self._current_targets),
            "timestamp": _now_iso(),
        }

    def build_unsubscribe_payload(self, targets):
        targets = [str(item).strip() for item in (targets or []) if str(item or "").strip()]
        if not targets:
            return None
        return {
            "type": "unsubscribe",
            "markets": targets[: self.subscription_limit],
            "timestamp": _now_iso(),
        }

    def _decode_message(self, raw_message):
        if isinstance(raw_message, (bytes, bytearray)):
            raw_message = raw_message.decode("utf-8", errors="ignore")
        if isinstance(raw_message, str):
            try:
                return json.loads(raw_message)
            except Exception:
                return {"type": "raw", "payload": raw_message}
        return raw_message

    async def run(self, stop_event):
        if websockets is None:
            self._log("[WS-ERROR]", reason="websockets_dependency_missing", timestamp=_now_iso())
            if self.health is not None:
                self.health.record_error("websockets_dependency_missing")
            return
        if not self.url:
            self._log("[WS-ERROR]", reason="missing_url", timestamp=_now_iso())
            if self.health is not None:
                self.health.record_error("missing_url")
            return
        reconnect_delay = 1.0
        attempts = 0
        while not stop_event.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=None,
                    close_timeout=5,
                    max_queue=256,
                ) as socket:
                    self._socket = socket
                    reconnect_delay = 1.0
                    attempts = 0
                    if self.health is not None:
                        self.health.record_connect()
                    self._log("[WS-CONNECT]", url=self.url, timestamp=_now_iso())
                    subscribe_payload = self.build_subscription_payload()
                    if subscribe_payload is not None:
                        await socket.send(_safe_json(subscribe_payload))
                        self._log("[WS-SUBSCRIBE]", markets=len(self._current_targets), timestamp=_now_iso())
                    while not stop_event.is_set():
                        try:
                            raw_message = await asyncio.wait_for(socket.recv(), timeout=self.ping_interval_seconds)
                            decoded = self._decode_message(raw_message)
                            if self.health is not None:
                                self.health.record_message(message_type=str(decoded.get("type") or decoded.get("event") or "message"))
                            if callable(self.message_handler):
                                self.message_handler(decoded)
                            now = time.time()
                            if self.health is not None and self._last_ping_ts and (now - self._last_ping_ts) >= self.ping_interval_seconds:
                                self.health.record_heartbeat(latency_ms=None)
                            self._last_ping_ts = now
                        except asyncio.TimeoutError:
                            try:
                                pong_waiter = await socket.ping()
                                start = time.time()
                                await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout_seconds)
                                latency_ms = round((time.time() - start) * 1000.0, 3)
                                if self.health is not None:
                                    self.health.record_heartbeat(latency_ms=latency_ms)
                                self._log("[WS-HEARTBEAT]", latency_ms=latency_ms, timestamp=_now_iso())
                            except Exception as exc:
                                if self.health is not None:
                                    self.health.record_error(exc.__class__.__name__)
                                self._log("[WS-ERROR]", reason=exc.__class__.__name__, timestamp=_now_iso())
                                raise
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts += 1
                if self.health is not None:
                    self.health.record_disconnect(reason=exc.__class__.__name__)
                    self.health.record_error(exc.__class__.__name__)
                self._log(
                    "[WS-DISCONNECT]",
                    reason=exc.__class__.__name__,
                    attempts=attempts,
                    timestamp=_now_iso(),
                )
                if self.max_reconnect_attempts and attempts >= self.max_reconnect_attempts:
                    self._log("[WS-ERROR]", reason="max_reconnect_attempts_reached", timestamp=_now_iso())
                    break
                if self.health is not None:
                    self.health.record_reconnect(reason=exc.__class__.__name__, delay_seconds=reconnect_delay)
                self._log("[WS-RECONNECT]", delay_seconds=reconnect_delay, attempt=attempts, timestamp=_now_iso())
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, 60.0)
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
        self._socket = None
