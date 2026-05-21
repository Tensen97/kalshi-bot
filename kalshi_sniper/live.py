"""Live quote and operator-command helpers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .core import KalshiHttpClient
from .utils import env_bool, env_float, safe_float, should_emit_service_log, utc_now_iso


class LiveQuoteStreamService:
    """Hydrate REST markets from WebSocket cache when fresh data is available."""

    def __init__(self, *, bot: Any, stale_seconds: float = 15.0, logger=None):
        self.bot = bot
        self.stale_seconds = max(0.5, float(stale_seconds or 15.0))
        self.logger = logger
        self.hydration_count = 0
        self.fallback_count = 0

    def _log(self, prefix: str, **fields: Any) -> None:
        if not should_emit_service_log(prefix):
            return
        if callable(self.logger):
            self.logger(prefix, **fields)

    def _manager(self):
        return getattr(self.bot, "websocket_manager", None)

    def get_snapshot(self, ticker: str) -> dict[str, Any] | None:
        manager = self._manager()
        ticker = str(ticker or "").strip()
        if manager is None or not ticker:
            return None
        try:
            snapshot = manager.get_market_snapshot(ticker)
        except Exception as exc:
            self._log("[WS-HYDRATE-ERROR]", ticker=ticker, reason=exc.__class__.__name__, timestamp=utc_now_iso())
            return None
        if not isinstance(snapshot, dict) or not snapshot:
            return None
        age = safe_float(snapshot.get("age_seconds"), None)
        if snapshot.get("stale") or (age is not None and age > self.stale_seconds):
            self.fallback_count += 1
            return None
        return snapshot

    def market_from_snapshot(self, ticker: str) -> dict[str, Any] | None:
        snapshot = self.get_snapshot(ticker)
        if snapshot is None:
            return None
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        market = dict(raw)
        market.setdefault("ticker", ticker)
        self._apply_snapshot_fields(market, snapshot)
        market["_quote_source"] = "websocket_cache"
        market["_ws_age_seconds"] = snapshot.get("age_seconds")
        return market

    def hydrate_market(self, market: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(market, dict):
            return market
        ticker = str(market.get("ticker") or market.get("market_ticker") or "").strip()
        if not ticker:
            return market
        snapshot = self.get_snapshot(ticker)
        if snapshot is None:
            self.fallback_count += 1
            return market
        hydrated = dict(market)
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        for key, value in raw.items():
            if key not in hydrated or hydrated.get(key) in (None, "", [], {}, 0, 0.0):
                hydrated[key] = value
        self._apply_snapshot_fields(hydrated, snapshot)
        hydrated["_quote_source"] = "websocket_cache"
        hydrated["_ws_age_seconds"] = snapshot.get("age_seconds")
        self.hydration_count += 1
        self._log(
            "[WS-HYDRATE-MARKET]",
            ticker=ticker,
            age_seconds=snapshot.get("age_seconds"),
            source=snapshot.get("source"),
            timestamp=utc_now_iso(),
        )
        return hydrated

    def _first(self, *values):
        for value in values:
            if value not in (None, "", [], {}):
                return value
        return None

    def _apply_snapshot_fields(self, market: dict[str, Any], snapshot: dict[str, Any]) -> None:
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        yes_bid = self._first(
            raw.get("yes_bid_dollars"),
            raw.get("yes_bid"),
            raw.get("best_bid"),
            snapshot.get("best_bid"),
            raw.get("bid"),
        )
        yes_ask = self._first(
            raw.get("yes_ask_dollars"),
            raw.get("yes_ask"),
            raw.get("best_ask"),
            snapshot.get("best_ask"),
            raw.get("ask"),
        )
        no_bid = self._first(raw.get("no_bid_dollars"), raw.get("no_bid"))
        no_ask = self._first(raw.get("no_ask_dollars"), raw.get("no_ask"))
        mappings = {
            "yes_bid_dollars": yes_bid,
            "yes_bid": yes_bid,
            "yes_ask_dollars": yes_ask,
            "yes_ask": yes_ask,
            "no_bid_dollars": no_bid,
            "no_bid": no_bid,
            "no_ask_dollars": no_ask,
            "no_ask": no_ask,
            "last_price_dollars": self._first(raw.get("last_price_dollars"), raw.get("last_price"), snapshot.get("last_trade")),
            "last_price": self._first(raw.get("last_price"), snapshot.get("last_trade")),
            "volume": self._first(raw.get("volume"), snapshot.get("volume")),
            "volume_fp": self._first(raw.get("volume_fp"), snapshot.get("volume")),
            "liquidity_dollars": self._first(raw.get("liquidity_dollars"), snapshot.get("liquidity")),
            "liquidity": self._first(raw.get("liquidity"), snapshot.get("liquidity")),
        }
        for key, value in mappings.items():
            if value not in (None, "", [], {}):
                market[key] = value
        depth = snapshot.get("depth") if isinstance(snapshot.get("depth"), dict) else {}
        if depth.get("orderbook") and not market.get("orderbook"):
            market["orderbook"] = depth["orderbook"]

    def summary(self) -> dict[str, Any]:
        return {
            "hydration_count": self.hydration_count,
            "fallback_count": self.fallback_count,
            "stale_seconds": self.stale_seconds,
        }


class TelegramCommandService:
    """Small safe Telegram command poller.

    Commands are intentionally conservative.  Telegram can pause the bot and
    inspect state; it cannot arm live trading or bypass dry-run/canary gates.
    """

    SAFE_COMMANDS = {"/status", "/risk", "/ws", "/pause", "/resume_dryrun", "/help"}

    def __init__(
        self,
        *,
        http_client: KalshiHttpClient,
        send_message: Callable[[str], bool],
        control_state_path: str | Path,
        enabled: bool | None = None,
        poll_interval_seconds: float | None = None,
        logger=None,
    ):
        self.http_client = http_client
        self.send_message = send_message
        self.control_state_path = Path(control_state_path)
        self.enabled = env_bool("TELEGRAM_COMMANDS_ENABLE", False) if enabled is None else bool(enabled)
        self.poll_interval_seconds = (
            env_float("TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS", 10.0)
            if poll_interval_seconds is None
            else float(poll_interval_seconds or 10.0)
        )
        self.logger = logger
        self.last_poll_ts = 0.0
        self.offset = 0
        self.processed_count = 0

    def _log(self, prefix: str, **fields: Any) -> None:
        if not should_emit_service_log(prefix):
            return
        if callable(self.logger):
            self.logger(prefix, **fields)

    def _configured(self) -> tuple[str, str]:
        token = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat_id = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
        return token, chat_id

    def poll(self, bot: Any) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        now = time.time()
        if now - self.last_poll_ts < self.poll_interval_seconds:
            return {"ok": True, "reason": "throttled"}
        self.last_poll_ts = now
        token, allowed_chat_id = self._configured()
        if not token or not allowed_chat_id:
            return {"ok": False, "reason": "missing_telegram_config"}
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
        if self.offset:
            params["offset"] = self.offset + 1
        try:
            payload = self.http_client.get_json(
                url,
                params=params,
                timeout=5,
                cache_ttl=0,
                bypass_cache=True,
            )
        except Exception as exc:
            self._log("[TELEGRAM-COMMAND-ERROR]", reason=exc.__class__.__name__, timestamp=utc_now_iso())
            return {"ok": False, "reason": exc.__class__.__name__}
        updates = payload.get("result", []) if isinstance(payload, dict) else []
        handled = 0
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id") or 0)
            self.offset = max(self.offset, update_id)
            message = update.get("message") if isinstance(update.get("message"), dict) else {}
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            chat_id = str(chat.get("id") or "").strip()
            text = str(message.get("text") or "").strip()
            if not text or chat_id != str(allowed_chat_id).strip():
                continue
            command = text.split()[0].lower()
            if command not in self.SAFE_COMMANDS:
                self.send_message(f"Unsupported command: {command}. Send /help.")
                continue
            response = self.handle_command(command, bot)
            self.send_message(response)
            handled += 1
            self.processed_count += 1
        return {"ok": True, "reason": "ok", "updates": len(updates), "handled": handled}

    def handle_command(self, command: str, bot: Any) -> str:
        if command == "/help":
            return "Commands: /status /risk /ws /pause /resume_dryrun"
        if command == "/pause":
            setattr(bot, "trading_halted", True)
            self._write_control_state({"telegram_paused": True, "trading_enabled": False})
            return "Trading paused. Live arming and dry-run safeguards are unchanged."
        if command == "/resume_dryrun":
            if bool(getattr(bot, "live_mode", False)) and bool(getattr(bot, "live_armed", False)):
                return "Refused: Telegram cannot resume armed live trading. Use server controls."
            setattr(bot, "trading_halted", False)
            self._write_control_state({"telegram_paused": False})
            return "Dry-run/sim trading resumed."
        if command == "/risk":
            return self._risk_text(bot)
        if command == "/ws":
            manager = getattr(bot, "websocket_manager", None)
            summary = manager.maybe_emit_summary(force=True, source="telegram") if manager is not None else {"enabled": False}
            health = summary.get("health", {}) if isinstance(summary, dict) else {}
            cache = summary.get("cache", {}) if isinstance(summary, dict) else {}
            return (
                "WebSocket: "
                f"enabled={bool(summary.get('enabled')) if isinstance(summary, dict) else False} "
                f"subs={len(summary.get('subscriptions', []) or []) if isinstance(summary, dict) else 0} "
                f"cache={cache.get('cache_size', 0)} stale={cache.get('stale_count', 0)} "
                f"reconnects={health.get('reconnect_count', 0)}"
            )
        return self._status_text(bot)

    def _status_text(self, bot: Any) -> str:
        summary = getattr(bot, "last_live_select_summary", {}) or {}
        return (
            "Status: "
            f"cycle={getattr(bot, 'cycle_count', 0)} "
            f"halted={bool(getattr(bot, 'trading_halted', False))} "
            f"sim_positions={len(getattr(bot, 'positions', []) or [])} "
            f"live_positions={len(getattr(bot, 'live_positions', []) or [])} "
            f"live_mode={bool(getattr(bot, 'live_mode', False))} "
            f"armed={bool(getattr(bot, 'live_armed', False))} "
            f"last_candidates={summary.get('candidates_found', 0)}"
        )

    def _risk_text(self, bot: Any) -> str:
        return (
            "Risk: "
            f"realized_pnl={safe_float(getattr(bot, 'realized_pnl', 0.0), 0.0):+.4f} "
            f"losses={getattr(bot, 'consecutive_losses', 0)} "
            f"halted={bool(getattr(bot, 'trading_halted', False))} "
            f"live_trades_today={getattr(bot, 'live_trade_count_today', 0)}"
        )

    def _write_control_state(self, update: dict[str, Any]) -> None:
        try:
            existing = {}
            if self.control_state_path.exists():
                existing = json.loads(self.control_state_path.read_text() or "{}")
            existing.update(update)
            existing["updated_at"] = utc_now_iso()
            existing["updated_by"] = "telegram_command"
            self.control_state_path.write_text(json.dumps(existing, indent=2, sort_keys=True))
        except Exception as exc:
            self._log("[TELEGRAM-COMMAND-ERROR]", reason=exc.__class__.__name__, action="write_control_state", timestamp=utc_now_iso())
