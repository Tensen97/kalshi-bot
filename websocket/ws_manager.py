import asyncio
import json
import threading
import time
from datetime import datetime, timezone

from .ws_cache import LiveMarketCache
from .ws_client import WebSocketClient
from .ws_health import WebSocketHealth


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class WebSocketManager:
    def __init__(
        self,
        bot=None,
        enabled=False,
        ws_url=None,
        summary_interval_seconds=60.0,
        rest_sync_interval_seconds=30.0,
        stale_seconds=45.0,
        max_reconnect_attempts=10,
        subscription_limit=200,
        max_cache_entries=2000,
        ping_interval_seconds=15.0,
        ping_timeout_seconds=10.0,
    ):
        self.bot = bot
        self.enabled = bool(enabled)
        self.ws_url = (ws_url or "").strip()
        self.summary_interval_seconds = float(summary_interval_seconds or 60.0)
        self.rest_sync_interval_seconds = float(rest_sync_interval_seconds or 30.0)
        self.stale_seconds = float(stale_seconds or 45.0)
        self.max_reconnect_attempts = int(max_reconnect_attempts or 10)
        self.subscription_limit = int(subscription_limit or 200)
        self.max_cache_entries = int(max_cache_entries or 2000)
        self.stop_event = threading.Event()
        self.thread = None
        self.loop = None
        self.last_rest_sync_ts = 0.0
        self.last_summary_ts = 0.0
        self.last_recovered_ts = 0.0
        self.last_subscription_targets = []
        self.last_rest_sync_summary = {}
        self.last_desync = {}
        self.cache = LiveMarketCache(
            max_age_seconds=self.stale_seconds,
            max_entries=self.max_cache_entries,
            cleanup_interval_seconds=max(5.0, self.summary_interval_seconds / 2.0),
            summary_interval_seconds=self.summary_interval_seconds,
        )
        self.health = WebSocketHealth(
            summary_interval_seconds=self.summary_interval_seconds,
            heartbeat_timeout_seconds=self.stale_seconds,
        )
        self.client = WebSocketClient(
            url=self.ws_url,
            cache=self.cache,
            health=self.health,
            subscribe_builder=self._build_subscription_payload,
            message_handler=self.handle_message,
            subscription_limit=self.subscription_limit,
            max_reconnect_attempts=self.max_reconnect_attempts,
            ping_interval_seconds=min(self.stale_seconds / 2.0, 15.0),
            ping_timeout_seconds=max(5.0, min(self.stale_seconds / 4.0, 15.0)),
        )

    def _log(self, prefix, **fields):
        parts = [prefix]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        print(" ".join(parts))

    def start(self):
        if not self.enabled or not self.ws_url or self.thread is not None:
            if not self.enabled:
                self._log("[WS-DEGRADED]", reason="disabled", timestamp=_now_iso())
            elif not self.ws_url:
                self._log("[WS-DEGRADED]", reason="missing_url", timestamp=_now_iso())
            return False
        self.thread = threading.Thread(target=self._thread_main, name="kalshi-ws", daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        self.thread = None
        self.loop = None

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._log("[WS-ERROR]", reason=exc.__class__.__name__, timestamp=_now_iso())

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        await self.client.run(self.stop_event)

    def _normalize_targets(self, targets):
        deduped = []
        seen = set()
        for target in targets or []:
            target = str(target or "").strip()
            if not target or target in seen:
                continue
            seen.add(target)
            deduped.append(target)
        return deduped[: self.subscription_limit]

    def _build_subscription_targets(self, markets=None, quotes=None, candidates=None, selected_quote=None, live_select_summary=None, source="live"):
        targets = []
        if selected_quote is not None:
            ticker = str(getattr(selected_quote, "ticker", "") or "").strip()
            if ticker:
                targets.append(ticker)
        for quote in quotes or []:
            ticker = str(getattr(quote, "ticker", "") or "").strip()
            if ticker:
                targets.append(ticker)
        for score, quote in (candidates or []):
            ticker = str(getattr(quote, "ticker", "") or "").strip()
            if ticker:
                targets.append(ticker)
        for market in markets or []:
            if isinstance(market, dict):
                ticker = str(market.get("ticker") or "").strip()
                if ticker:
                    targets.append(ticker)
        family_focus = str((live_select_summary or {}).get("family_focus") or "").strip()
        if family_focus and self.bot is not None and hasattr(self.bot, "get_market_family"):
            pass
        return self._normalize_targets(targets)

    def _build_subscription_payload(self, targets):
        targets = self._normalize_targets(targets)
        if not targets:
            return None
        self.health.record_subscription_count(len(targets))
        for ticker in targets:
            self._log("[WS-SUBSCRIBE-MARKET]", ticker=ticker, timestamp=_now_iso())
        return {
            "type": "subscribe",
            "markets": targets,
            "timestamp": _now_iso(),
        }

    def update_subscriptions(self, markets=None, quotes=None, candidates=None, selected_quote=None, live_select_summary=None, source="live"):
        targets = self._build_subscription_targets(
            markets=markets,
            quotes=quotes,
            candidates=candidates,
            selected_quote=selected_quote,
            live_select_summary=live_select_summary,
            source=source,
        )
        added = [ticker for ticker in targets if ticker not in self.last_subscription_targets]
        removed = [ticker for ticker in self.last_subscription_targets if ticker not in targets]
        for ticker in added:
            self._log("[WS-SUBSCRIBE-MARKET]", ticker=ticker, source=source, timestamp=_now_iso())
        for ticker in removed:
            self._log("[WS-UNSUBSCRIBE-MARKET]", ticker=ticker, source=source, timestamp=_now_iso())
        self.last_subscription_targets = targets
        self.health.record_subscription_count(len(targets))
        return targets

    def sync_from_rest(self, markets=None, quotes=None, source="live"):
        now = time.time()
        if not self.enabled:
            return {"synced": 0, "source": source, "enabled": False}
        if (now - float(self.last_rest_sync_ts or 0.0)) < self.rest_sync_interval_seconds:
            return {"synced": 0, "source": source, "throttled": True, "enabled": True}
        synced = self.cache.bulk_sync_from_rest(markets=markets, quotes=quotes, source=f"rest:{source}")
        self.last_rest_sync_ts = now
        self.last_rest_sync_summary = {
            "synced": synced,
            "source": source,
            "timestamp": _now_iso(),
        }
        return self.last_rest_sync_summary

    def handle_message(self, message):
        if not isinstance(message, dict):
            return None
        was_stale = self.health.is_stale()
        self.health.record_message(message_type=str(message.get("type") or message.get("event") or "message"))
        ticker = message.get("ticker") or message.get("market_ticker") or message.get("symbol")
        if message.get("type") in {"heartbeat", "ping", "pong"}:
            self.health.record_heartbeat(latency_ms=_safe_float(message.get("latency_ms"), None))
        if ticker:
            self.cache.upsert_market(message, source="ws", message_type=str(message.get("type") or "message"))
            if was_stale:
                self._log("[WS-RECOVERED]", ticker=str(ticker).strip(), timestamp=_now_iso())
        else:
            markets = message.get("markets")
            if isinstance(markets, list):
                for item in markets:
                    if isinstance(item, dict):
                        self.cache.upsert_market(item, source="ws", message_type=str(message.get("type") or "message"))
                if was_stale and markets:
                    self._log("[WS-RECOVERED]", ticker="batch", timestamp=_now_iso())
        if message.get("type") == "disconnect":
            self.health.record_disconnect(reason=str(message.get("reason") or "remote_disconnect"))
        self._maybe_emit_desync(message)
        return message

    def _maybe_emit_desync(self, message=None):
        if not self.enabled or not self.last_subscription_targets:
            return None
        if self.health.is_stale():
            self.health.record_rest_fallback(reason="ws_stale")
            self._log("[WS-FALLBACK-REST]", reason="ws_stale", timestamp=_now_iso())
        if message and isinstance(message, dict):
            ticker = str(message.get("ticker") or message.get("market_ticker") or "").strip()
            if ticker and self.bot is not None and hasattr(self.bot, "build_quote"):
                try:
                    market = {"ticker": ticker}
                    quote = self.bot.build_quote(market)
                    cache_snapshot = self.cache.get_snapshot(ticker)
                    if quote is not None and cache_snapshot is not None:
                        quote_spread = _safe_float(getattr(quote, "yes_ask", None), 0.0) - _safe_float(getattr(quote, "yes_bid", None), 0.0)
                        cache_spread = _safe_float(cache_snapshot.get("spread"), None)
                        if cache_spread is not None and abs(float(quote_spread) - float(cache_spread)) >= 0.03:
                            self.last_desync = {
                                "ticker": ticker,
                                "quote_spread": round(float(quote_spread), 4),
                                "cache_spread": round(float(cache_spread), 4),
                                "timestamp": _now_iso(),
                            }
                            self._log(
                                "[WS-REST-DESYNC]",
                                ticker=ticker,
                                quote_spread=self.last_desync["quote_spread"],
                                cache_spread=self.last_desync["cache_spread"],
                                timestamp=_now_iso(),
                            )
                except Exception as exc:
                    self._log("[WS-ERROR]", reason=exc.__class__.__name__, timestamp=_now_iso())
        return None

    def get_market_snapshot(self, ticker):
        snapshot = self.cache.get_snapshot(ticker)
        if snapshot is None:
            return None
        snapshot["websocket_enabled"] = self.enabled
        snapshot["subscription_count"] = len(self.last_subscription_targets)
        snapshot["rest_sync_age_seconds"] = None if not self.last_rest_sync_ts else round(time.time() - self.last_rest_sync_ts, 4)
        return snapshot

    def maybe_emit_summary(self, force=False, source="live"):
        if not self.enabled:
            summary = {
                "enabled": False,
                "source": source,
                "message": "disabled",
            }
            if force:
                self._log("[WS-SUMMARY]", enabled="no", source=source, message="disabled", timestamp=_now_iso())
            return summary
        health_summary = self.health.maybe_log_summary(force=force)
        cache_summary = self.cache.summary()
        now = time.time()
        if not force and (now - float(self.last_summary_ts or 0.0)) < self.summary_interval_seconds:
            return {
                "enabled": True,
                "source": source,
                "health": health_summary or self.health.summary(),
                "cache": cache_summary,
                "subscriptions": list(self.last_subscription_targets),
            }
        self.last_summary_ts = now
        summary = {
            "enabled": True,
            "source": source,
            "health": health_summary or self.health.summary(),
            "cache": cache_summary,
            "subscriptions": list(self.last_subscription_targets),
            "last_rest_sync": self.last_rest_sync_summary,
            "last_desync": self.last_desync,
        }
        self._log(
            "[WS-SUMMARY]",
            active_subscriptions=len(self.last_subscription_targets),
            cache_size=cache_summary.get("cache_size", 0),
            stale_markets=cache_summary.get("stale_count", 0),
            msg_per_sec=summary["health"].get("message_rate_per_second", 0.0),
            reconnects=summary["health"].get("reconnect_count", 0),
            rest_fallbacks=summary["health"].get("fallback_rest_count", 0),
            timestamp=_now_iso(),
        )
        return summary
