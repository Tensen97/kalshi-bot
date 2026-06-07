#!/usr/bin/env python3
"""AION sports-only live trading engine."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

import kalshi_execution
import kalshi_exits
import kalshi_risk
from kalshi_orderbook import (
    Quote,
    build_quote,
    coerce_float,
    coerce_price,
    extract_markets,
    is_kxmlbks_market,
    is_sports_market,
    is_tennis_market,
    market_family,
    market_text,
    market_ticker,
    normalize_side,
    price_zone,
    valid_trade_price,
)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TradeTarget:
    target_id: str
    ticker: str
    family: str
    side: str
    selected_price: float
    count: int
    confidence: float
    ranking_score: float
    spread: Optional[float]
    liquidity: float
    volume: float
    orderbook_snapshot_id: str
    price_zone: str
    source: str
    created_ts: float
    signal_detected_ts: Optional[float] = None
    target_created_ts: Optional[float] = None
    trace_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PositionExecutor = kalshi_exits.PositionExecutor


class KalshiSniperBot:
    def __init__(self, *, live_mode: bool = False, live_armed: bool = False, live_env_confirmed: bool = False) -> None:
        self.repo_root = Path(__file__).resolve().parent
        self.runtime_root = Path(os.environ.get("AION_RUNTIME_ROOT", self.repo_root / "runtime"))
        self.state_path = Path(os.environ.get("AION_LIVE_ENGINE_STATE", self.runtime_root / "state" / "aion_live_engine.json"))
        self.session = requests.Session()
        self.live_mode = bool(live_mode)
        self.live_armed = bool(live_armed)
        self.live_env_confirmed = bool(live_env_confirmed)
        self.configured_dry_run = env_bool("DRY_RUN_LIVE_ORDERS", True)
        self.effective_live_dry_run = kalshi_execution.effective_dry_run(
            live_mode=self.live_mode,
            live_armed=self.live_armed,
            live_env_confirmed=self.live_env_confirmed,
            configured_dry_run=self.configured_dry_run,
        )

        self.scan_limit = max(1, env_int("AION_SCAN_LIMIT", 200))
        self.scan_pages = max(1, env_int("AION_SCAN_PAGES", 3))
        self.max_hydrate_per_cycle = max(1, env_int("AION_MAX_HYDRATE_PER_CYCLE", 80))
        self.loop_sleep_seconds = max(1.0, env_float("AION_LOOP_SLEEP_SECONDS", 5.0))
        self.max_live_order_size = max(1, env_int("MAX_LIVE_ORDER_SIZE", env_int("AION_MAX_LIVE_ORDER_SIZE", 3)))
        self.base_order_size = max(1, env_int("AION_BASE_ORDER_SIZE", 1))
        self.max_open_positions = max(1, env_int("AION_MAX_OPEN_POSITIONS", 8))
        self.max_daily_loss = max(0.0, env_float("AION_MAX_DAILY_LOSS", env_float("MAX_DAILY_LOSS", 50.0)))
        self.live_order_rate_limit = max(1, env_int("LIVE_ORDER_RATE_LIMIT", env_int("AION_LIVE_ORDER_RATE_LIMIT", 5)))
        self.live_order_rate_window_sec = max(1.0, env_float("LIVE_ORDER_RATE_WINDOW_SEC", 60.0))
        self.max_dollars_per_trade = max(1.0, env_float("AION_MAX_DOLLARS_PER_TRADE", 20.0))
        self.max_single_position_pct = min(1.0, max(0.001, env_float("AION_MAX_SINGLE_POSITION_PCT", 0.05)))
        self.min_confidence_to_submit = max(0.0, env_float("AION_MIN_CONFIDENCE_TO_SUBMIT", 0.15))
        self.min_score_to_submit = max(0.0, env_float("AION_MIN_SCORE_TO_SUBMIT", 8.0))
        self.max_entry_spread_hard = min(0.99, max(0.05, env_float("AION_MAX_ENTRY_SPREAD_HARD", 0.55)))
        configured_signal_age = env_float("AION_MAX_SIGNAL_AGE_SECONDS", env_float("MAX_SIGNAL_AGE_SECONDS", 45.0))
        self.max_signal_age_seconds = min(99.0, max(1.0, configured_signal_age))
        self.balance_cache_ttl = max(1.0, env_float("AION_BALANCE_CACHE_SECONDS", 20.0))
        self.exit_config = {
            "take_profit": max(0.005, env_float("AION_EXIT_TAKE_PROFIT", 0.04)),
            "stop_loss": max(0.005, env_float("AION_EXIT_STOP_LOSS", 0.04)),
            "trailing_start": max(0.005, env_float("AION_EXIT_TRAILING_START", 0.03)),
            "trailing_distance": max(0.0025, env_float("AION_EXIT_TRAILING_DISTANCE", 0.015)),
            "max_hold_seconds": max(30.0, env_float("AION_EXIT_MAX_HOLD_SECONDS", 900.0)),
        }

        self.live_positions: List[Dict[str, Any]] = []
        self.live_order_timestamps: List[float] = []
        self._balance_cache: Dict[str, Any] = {}
        self._last_reconcile_ts = 0.0
        self.cycle_count = 0
        self.stop_requested = False
        self.state = self.load_state()
        self.live_positions = self._local_open_positions()

    def to_float(self, value: Any, default: Optional[float] = 0.0) -> Optional[float]:
        return coerce_float(value, default)

    def normalize_contract_price(self, value: Any, **_: Any) -> Optional[float]:
        return coerce_price(value)

    def log_json(self, tag: str, payload: Dict[str, Any] | None = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        payload.setdefault("ts", utc_now_iso())
        try:
            print(f"[{tag}] " + json.dumps(payload, sort_keys=True, default=str), flush=True)
        except Exception:
            print(f"[{tag}] {payload}", flush=True)

    def load_state(self) -> Dict[str, Any]:
        default = {
            "daily": {
                "date": time.strftime("%Y-%m-%d"),
                "realized_pnl": 0.0,
                "live_trade_count": 0,
                "submitted_order_count": 0,
            },
            "positions": {},
            "learning": {
                "family_side_zone": {},
                "exit_outcomes": [],
            },
        }
        try:
            if self.state_path.exists():
                loaded = json.loads(self.state_path.read_text())
                if isinstance(loaded, dict):
                    for key, value in default.items():
                        loaded.setdefault(key, value)
                    return loaded
        except Exception as exc:
            self.log_json("AION-STATE-LOAD-ERROR", {"reason": exc.__class__.__name__, "path": str(self.state_path)})
        return default

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self.state, sort_keys=True, indent=2, default=str))
        tmp_path.replace(self.state_path)

    def _local_open_positions(self) -> List[Dict[str, Any]]:
        positions = []
        for ticker, position in (self.state.get("positions") or {}).items():
            if not isinstance(position, dict):
                continue
            row = dict(position)
            row["ticker"] = str(row.get("ticker") or ticker)
            if str(row.get("status") or "open").lower() in {"open", "exit_pending"}:
                positions.append(row)
        return positions

    def get_live_base_url(self) -> str:
        return os.environ.get("KALSHI_LIVE_BASE_URL", "") or "https://api.elections.kalshi.com"

    def build_live_request_url(self, path: str) -> str:
        base_url = self.get_live_base_url().rstrip("/")
        canonical_prefix = "/trade-api/v2"
        if base_url.endswith(canonical_prefix) and path.startswith(f"{canonical_prefix}/"):
            return f"{base_url}{path[len(canonical_prefix):]}"
        return f"{base_url}{path}"

    def get_live_auth_config(self) -> Dict[str, Any]:
        api_key_source = None
        api_key = ""
        if os.environ.get("KALSHI_API_KEY_ID"):
            api_key_source = "KALSHI_API_KEY_ID"
            api_key = os.environ.get("KALSHI_API_KEY_ID", "")
        elif os.environ.get("KALSHI_API_KEY"):
            api_key_source = "KALSHI_API_KEY"
            api_key = os.environ.get("KALSHI_API_KEY", "")

        private_key_source = None
        private_key_path = ""
        for key in ("LIVE_PRIVATE_KEY_PATH", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_PRIVATE_KEY_FILE"):
            if os.environ.get(key):
                private_key_source = key
                private_key_path = os.environ.get(key, "")
                break
        if not private_key_path:
            private_key_source = "DEFAULT_LIVE_PRIVATE_KEY_PATH"
            private_key_path = str(self.repo_root / "keys" / "kalshi-live.pem")
        return {
            "api_key": api_key,
            "api_key_source": api_key_source,
            "private_key_path": private_key_path,
            "private_key_path_source": private_key_source,
            "private_key_exists": bool(private_key_path and Path(private_key_path).exists()),
        }

    def get_live_api_key(self) -> str:
        return str(self.get_live_auth_config().get("api_key") or "")

    def get_live_api_key_source(self) -> Optional[str]:
        return self.get_live_auth_config().get("api_key_source")

    def get_live_private_key_path(self) -> str:
        return str(self.get_live_auth_config().get("private_key_path") or "")

    def load_live_private_key(self) -> Optional[bytes]:
        path = self.get_live_private_key_path()
        try:
            if path and Path(path).exists():
                return Path(path).read_bytes()
        except OSError:
            return None
        return None

    def can_build_live_auth(self) -> bool:
        config = self.get_live_auth_config()
        return bool(config.get("api_key") and config.get("private_key_exists") and self.load_live_private_key())

    def sign_request(self, private_key: bytes, timestamp: str, method: str, path: str) -> bytes:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        key_obj = serialization.load_pem_private_key(private_key, password=None)
        message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
        return key_obj.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )

    def build_live_auth_headers(self, method: str, path: str) -> Dict[str, Any]:
        path_to_sign = str(path or "").split("?", 1)[0]
        timestamp = str(int(time.time() * 1000))
        private_key = self.load_live_private_key()
        api_key = self.get_live_api_key()
        if not api_key or not private_key or not path_to_sign.startswith("/"):
            return {"ok": False, "reason": "missing_auth_config", "headers": None}
        try:
            signature = self.sign_request(private_key, timestamp, method, path_to_sign)
            return {
                "ok": True,
                "reason": "headers_built",
                "path_to_sign": path_to_sign,
                "headers": {
                    "KALSHI-ACCESS-KEY": api_key,
                    "KALSHI-ACCESS-TIMESTAMP": timestamp,
                    "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
                },
            }
        except Exception as exc:
            return {"ok": False, "reason": f"sign_error:{exc.__class__.__name__}", "headers": None}

    def _decode_live_response_json(self, response: requests.Response) -> Dict[str, Any]:
        try:
            return response.json() if getattr(response, "text", "") else {}
        except Exception:
            return {"raw_text": str(getattr(response, "text", "") or "")[:1000]}

    def _live_request_json(self, method: str, path: str, *, json_payload: Dict[str, Any] | None = None, timeout: int = 15) -> Dict[str, Any]:
        auth = self.build_live_auth_headers(method, path)
        if not auth.get("ok"):
            return {"ok": False, "reason": auth.get("reason"), "status_code": None, "path": path, "body": None}
        headers = dict(auth.get("headers") or {})
        headers["Accept"] = "application/json"
        if json_payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.session.request(
                str(method or "GET").upper(),
                self.build_live_request_url(path),
                headers=headers,
                json=json_payload,
                timeout=timeout,
            )
            return {
                "ok": bool(response.ok),
                "reason": "ok" if response.ok else f"http_{response.status_code}",
                "status_code": response.status_code,
                "path": path,
                "body": self._decode_live_response_json(response),
            }
        except Exception as exc:
            return {"ok": False, "reason": f"request_error:{exc.__class__.__name__}", "status_code": None, "path": path, "body": None}

    def run_live_auth_get_smoke_test(self) -> Dict[str, Any]:
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=15)
        self.log_json(
            "LIVE-AUTH-GET-SMOKE",
            {
                "ok": bool(result.get("ok")),
                "reason": result.get("reason"),
                "status_code": result.get("status_code"),
                "path": result.get("path"),
            },
        )
        return result

    def run_live_smoke_test(self) -> Dict[str, Any]:
        auth = self.run_live_auth_get_smoke_test()
        positions = self.reconcile_live_positions(force=True)
        preview = self.preview_live_ticker_selection()
        result = {
            "ok": bool(auth.get("ok")),
            "auth_ok": bool(auth.get("ok")),
            "positions_ok": bool(positions.get("ok")),
            "preview_candidates": preview.get("candidate_count"),
            "open_positions": len(self.live_positions),
        }
        self.log_json("LIVE-SMOKE", result)
        return result

    def get_markets(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        extra_params: Dict[str, Any] | None = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {"limit": int(limit or self.scan_limit), "status": "open"}
        if isinstance(extra_params, dict):
            params.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        if cursor:
            params["cursor"] = cursor
        try:
            response = self.session.get(self.build_live_request_url("/trade-api/v2/markets"), params=params, timeout=15)
            response.raise_for_status()
            return extract_markets(response.json() if response.content else {})
        except Exception as exc:
            self.log_json("AION-DISCOVERY-ERROR", {"reason": exc.__class__.__name__, "cursor": cursor})
            return [], None

    def get_live_market_detail(self, ticker: str) -> Dict[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            return {}
        response = self.session.get(self.build_live_request_url(f"/trade-api/v2/markets/{ticker}"), timeout=10)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return payload.get("market", payload) if isinstance(payload, dict) else {}

    def get_live_market_orderbook(self, ticker: str) -> Dict[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            return {}
        response = self.session.get(self.build_live_request_url(f"/trade-api/v2/markets/{ticker}/orderbook"), timeout=10)
        response.raise_for_status()
        return response.json() if response.content else {}

    def fetch_live_available_cash(self, *, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and self._balance_cache and now - float(self._balance_cache.get("ts", 0.0) or 0.0) < self.balance_cache_ttl:
            return dict(self._balance_cache)
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=10)
        body = result.get("body") if isinstance(result.get("body"), dict) else {}
        cash = self._extract_cash_from_balance(body)
        out = {
            "ok": bool(cash is not None),
            "available_cash": cash,
            "reason": "ok" if cash is not None else result.get("reason") or "cash_balance_unavailable",
            "status_code": result.get("status_code"),
            "ts": now,
        }
        self._balance_cache = out
        return out

    def _extract_cash_from_balance(self, body: Dict[str, Any]) -> Optional[float]:
        dollar_fields = (
            "available_balance_dollars",
            "available_cash_dollars",
            "buying_power_dollars",
            "cash_balance_dollars",
            "balance_dollars",
        )
        cent_fields = (
            "available_balance_cents",
            "available_cash_cents",
            "buying_power_cents",
            "cash_balance_cents",
            "balance_cents",
            "available_balance",
            "buying_power",
            "cash_balance",
            "balance",
        )
        queue = [body] if isinstance(body, dict) else []
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if not isinstance(current, dict):
                continue
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)
            for field in dollar_fields:
                value = coerce_float(current.get(field), None)
                if value is not None and value >= 0:
                    return round(float(value), 4)
            for field in cent_fields:
                value = coerce_float(current.get(field), None)
                if value is not None and value >= 0:
                    return round(float(value) / 100.0, 4)
            for value in current.values():
                if isinstance(value, dict):
                    queue.append(value)
        return None

    def get_market_family(self, market: Dict[str, Any]) -> str:
        return market_family(market)

    def is_sports_family(self, family: str, ticker: str | None = None) -> bool:
        return is_sports_market(family=family, ticker=ticker)

    def _disabled_new_entry_reason(self, *, family: str, ticker: str, market: Dict[str, Any] | None = None) -> Optional[str]:
        if is_tennis_market(family=family, ticker=ticker, market=market):
            return "tennis_live_trading_disabled"
        if is_kxmlbks_market(family=family, ticker=ticker, market=market):
            return "kxmlbks_live_trading_disabled"
        if not is_sports_market(family=family, ticker=ticker, market=market):
            return "sports_only_non_sports_execution_disabled"
        return None

    def _emit_final_reject(
        self,
        *,
        ticker: str | None,
        family: str | None,
        side: str | None = None,
        selected_price: Any = None,
        spread: Any = None,
        liquidity: Any = None,
        volume: Any = None,
        reason: str,
        subsystem: str,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        self.log_json(
            "AION-FINAL-REJECT",
            {
                "ticker": ticker,
                "family": family,
                "side": side,
                "selected_price": coerce_price(selected_price),
                "spread": spread,
                "liquidity": liquidity,
                "volume": volume,
                "reason": reason,
                "responsible_subsystem": subsystem,
                **(extra or {}),
            },
        )

    def _coerce_epoch_seconds(self, value: Any) -> Optional[float]:
        if value in (None, "", [], {}):
            return None
        numeric = coerce_float(value, None)
        if numeric is not None:
            if numeric > 1_000_000_000_000:
                numeric = numeric / 1000.0
            if 0.0 < numeric < 4_102_444_800.0:
                return float(numeric)
            return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None

    def _entry_signal_age(self, target: TradeTarget, payload: Dict[str, Any], *, now: float) -> Dict[str, Any]:
        signal_ts = None
        signal_source = None
        for source, value in (
            ("payload.signal_detected_ts", payload.get("signal_detected_ts")),
            ("target.signal_detected_ts", getattr(target, "signal_detected_ts", None)),
        ):
            signal_ts = self._coerce_epoch_seconds(value)
            if signal_ts is not None:
                signal_source = source
                break
        if signal_ts is None:
            for source, value in (
                ("payload.target_created_ts", payload.get("target_created_ts")),
                ("target.target_created_ts", getattr(target, "target_created_ts", None)),
                ("target.created_ts", getattr(target, "created_ts", None)),
                ("payload.created_ts", payload.get("created_ts")),
            ):
                signal_ts = self._coerce_epoch_seconds(value)
                if signal_ts is not None:
                    signal_source = source
                    break
        if signal_ts is None:
            signal_ts = now
            signal_source = "confirm_entry_now"
        age = round(max(0.0, now - signal_ts), 3)
        return {"signal_age_seconds": age, "signal_ts": signal_ts, "signal_ts_source": signal_source}

    def _log_confirm_entry_reject(self, target: TradeTarget, payload: Dict[str, Any], *, reason: str, extra: Dict[str, Any] | None = None) -> None:
        self.log_json(
            "AION-CONFIRM-ENTRY-REJECT",
            {
                "reason": reason,
                "ticker": target.ticker,
                "family": target.family,
                "selected_side": normalize_side(target.side),
                "target_id": target.target_id,
                "trace_id": payload.get("trace_id") or getattr(target, "trace_id", None) or target.target_id,
                **(extra or {}),
            },
        )

    def fetch_live_sports_discovery_markets(self) -> List[Dict[str, Any]]:
        markets: List[Dict[str, Any]] = []
        for extra_params in ({}, {"category": "Sports"}):
            cursor = None
            for _ in range(self.scan_pages):
                page, cursor = self.get_markets(limit=self.scan_limit, cursor=cursor, extra_params=extra_params)
                if not page:
                    break
                markets.extend(page)
                if not cursor:
                    break
        deduped: Dict[str, Dict[str, Any]] = {}
        for market in markets:
            ticker = market_ticker(market)
            if ticker:
                deduped.setdefault(ticker, market)
        sports = [
            market for market in deduped.values()
            if is_sports_market(family=market_family(market), ticker=market_ticker(market), market=market)
        ]
        self.log_json(
            "AION-LIVE-DISCOVERY",
            {
                "raw_markets": len(markets),
                "deduped_markets": len(deduped),
                "sports_candidates": len(sports),
                "hydration_cap": self.max_hydrate_per_cycle,
                "scan_pages": self.scan_pages,
            },
        )
        return sports[: self.max_hydrate_per_cycle]

    def hydrate_quote(self, market: Dict[str, Any]) -> Optional[Quote]:
        ticker = market_ticker(market)
        if not ticker:
            return None
        try:
            detail = self.get_live_market_detail(ticker)
            merged = dict(market)
            if isinstance(detail, dict):
                merged.update(detail)
        except Exception as exc:
            merged = dict(market)
            self.log_json(
                "AION-MARKET-DETAIL-FALLBACK",
                {
                    "ticker": ticker,
                    "family": market_family(market),
                    "reason": exc.__class__.__name__,
                    "subsystem": "market_detail_hydration",
                },
            )
        try:
            orderbook = self.get_live_market_orderbook(ticker)
        except Exception as exc:
            self._emit_final_reject(
                ticker=ticker,
                family=market_family(merged),
                reason=f"orderbook_api_error:{exc.__class__.__name__}",
                subsystem="orderbook_api",
                liquidity=merged.get("liquidity"),
                volume=merged.get("volume"),
            )
            return None
        return build_quote(merged, orderbook)

    def _learning_bucket_key(self, family: str, side: str, zone: str) -> str:
        return f"{str(family or 'UNKNOWN').upper()}|{normalize_side(side) or 'unknown'}|{zone or 'unknown'}"

    def _learning_multiplier(self, family: str, side: str, zone: str) -> float:
        bucket = ((self.state.get("learning") or {}).get("family_side_zone") or {}).get(self._learning_bucket_key(family, side, zone), {})
        trades = int(bucket.get("trades") or 0)
        if trades < 3:
            return 1.0
        avg_pnl = float(bucket.get("pnl") or 0.0) / max(1, trades)
        if avg_pnl > 0.10:
            return 1.20
        if avg_pnl > 0.02:
            return 1.10
        if avg_pnl < -0.10:
            return 0.70
        if avg_pnl < -0.02:
            return 0.85
        return 1.0

    def _score_side(self, quote: Quote, side: str) -> Optional[Dict[str, Any]]:
        side = normalize_side(side)
        if side not in {"yes", "no"}:
            return None
        selected_price = quote.price_for_side(side, "buy")
        executable_size = quote.size_for_side(side, "buy")
        spread = quote.spread_for_side(side)
        if not valid_trade_price(selected_price):
            return None
        if executable_size <= 0.0:
            return None
        if spread is not None and spread > self.max_entry_spread_hard:
            return {
                "blocked": True,
                "reason": "spread_absurd_unusable",
                "side": side,
                "selected_price": selected_price,
                "spread": spread,
            }
        depth_score = min(35.0, math.log1p(max(0.0, executable_size)) * 8.0)
        liquidity_score = min(20.0, math.log1p(max(0.0, quote.liquidity)) * 2.5)
        volume_score = min(10.0, math.log1p(max(0.0, quote.volume)) * 1.5)
        spread_score = 25.0
        if spread is not None:
            spread_score = max(0.0, 25.0 * (1.0 - min(spread, self.max_entry_spread_hard) / self.max_entry_spread_hard))
        price = coerce_price(selected_price) or 0.5
        boundary_bonus = 5.0 if 0.05 <= price <= 0.95 else 0.0
        zone = price_zone(price)
        learning_multiplier = self._learning_multiplier(quote.family, side, zone)
        raw_score = depth_score + liquidity_score + volume_score + spread_score + boundary_bonus
        score = round(raw_score * learning_multiplier, 4)
        confidence = round(max(0.0, min(1.0, score / 100.0)), 4)
        return {
            "blocked": False,
            "side": side,
            "selected_price": price,
            "spread": spread,
            "score": score,
            "confidence": confidence,
            "executable_size": executable_size,
            "price_zone": zone,
            "learning_multiplier": learning_multiplier,
        }

    def _confidence_size(self, *, confidence: float, selected_price: float, family: str, side: str, zone: str) -> Tuple[int, Dict[str, Any]]:
        multiplier = self._learning_multiplier(family, side, zone)
        raw_count = self.base_order_size
        if confidence >= 0.85:
            raw_count += 2
        elif confidence >= 0.60:
            raw_count += 1
        raw_count = max(1, int(math.floor(raw_count * multiplier)))
        raw_count = min(raw_count, self.max_live_order_size)

        balance = self.fetch_live_available_cash()
        cash = balance.get("available_cash")
        if cash is None:
            if self.effective_live_dry_run:
                cash = self.max_dollars_per_trade
            else:
                return 0, {"ok": False, "reason": "cash_balance_unavailable", "balance": balance}
        max_position_dollars = min(self.max_dollars_per_trade, max(1.0, float(cash) * self.max_single_position_pct))
        affordable_count = int(max_position_dollars // max(0.01, float(selected_price)))
        account_affordable = int(float(cash) // max(0.01, float(selected_price)))
        final_count = min(raw_count, affordable_count, account_affordable, self.max_live_order_size)
        return max(0, final_count), {
            "ok": final_count >= 1,
            "reason": "passed" if final_count >= 1 else "insufficient_cash_for_one_contract",
            "available_cash": cash,
            "max_position_dollars": round(max_position_dollars, 4),
            "raw_count": raw_count,
            "affordable_count": affordable_count,
            "account_affordable": account_affordable,
            "learning_multiplier": multiplier,
        }

    def _build_target(self, quote: Quote, side_score: Dict[str, Any]) -> Optional[TradeTarget]:
        side = normalize_side(side_score.get("side"))
        price = coerce_price(side_score.get("selected_price"))
        if side not in {"yes", "no"} or price is None:
            return None
        count, sizing = self._confidence_size(
            confidence=float(side_score.get("confidence") or 0.0),
            selected_price=price,
            family=quote.family,
            side=side,
            zone=side_score.get("price_zone") or price_zone(price),
        )
        if count < 1:
            self._emit_final_reject(
                ticker=quote.ticker,
                family=quote.family,
                side=side,
                selected_price=price,
                spread=side_score.get("spread"),
                liquidity=quote.liquidity,
                volume=quote.volume,
                reason=sizing.get("reason") or "capital_routing_rejected",
                subsystem="capital_routing",
                extra=sizing,
            )
            return None
        created_ts = time.time()
        target_id = f"{quote.ticker}:{side}:{int(created_ts * 1000)}"
        return TradeTarget(
            target_id=target_id,
            ticker=quote.ticker,
            family=quote.family,
            side=side,
            selected_price=price,
            count=count,
            confidence=float(side_score.get("confidence") or 0.0),
            ranking_score=float(side_score.get("score") or 0.0),
            spread=side_score.get("spread"),
            liquidity=quote.liquidity,
            volume=quote.volume,
            orderbook_snapshot_id=quote.orderbook_snapshot_id,
            price_zone=side_score.get("price_zone") or price_zone(price),
            source="sports_orderbook_ranker",
            created_ts=created_ts,
            signal_detected_ts=self._coerce_epoch_seconds(side_score.get("signal_detected_ts")),
            target_created_ts=created_ts,
            trace_id=target_id,
        )

    def discover_trade_targets(self) -> List[Tuple[TradeTarget, Quote]]:
        targets: List[Tuple[TradeTarget, Quote]] = []
        for market in self.fetch_live_sports_discovery_markets():
            ticker = market_ticker(market)
            family = market_family(market)
            disabled_reason = self._disabled_new_entry_reason(family=family, ticker=ticker, market=market)
            if disabled_reason:
                self._emit_final_reject(
                    ticker=ticker,
                    family=family,
                    reason=disabled_reason,
                    subsystem="sports_admission",
                    liquidity=market.get("liquidity"),
                    volume=market.get("volume"),
                )
                continue
            quote = self.hydrate_quote(market)
            if quote is None:
                continue
            if quote.synthetic or not quote.orderbook_real:
                self._emit_final_reject(
                    ticker=quote.ticker,
                    family=quote.family,
                    reason="fake_or_synthetic_book_rejected" if quote.synthetic else "real_orderbook_required",
                    subsystem="orderbook_validation",
                    liquidity=quote.liquidity,
                    volume=quote.volume,
                    extra={"orderbook_source": quote.orderbook_source},
                )
                continue
            side_results = []
            for side in ("yes", "no"):
                side_score = self._score_side(quote, side)
                if side_score is None:
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=quote.price_for_side(side, "buy"),
                        spread=quote.spread_for_side(side),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason="no_executable_selected_side_price",
                        subsystem="side_selection",
                    )
                    continue
                if side_score.get("blocked"):
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=side_score.get("selected_price"),
                        spread=side_score.get("spread"),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason=side_score.get("reason") or "side_blocked",
                        subsystem="side_selection",
                    )
                    continue
                if float(side_score.get("score") or 0.0) < self.min_score_to_submit:
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=side_score.get("selected_price"),
                        spread=side_score.get("spread"),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason="score_below_minimum",
                        subsystem="single_ranker",
                    )
                    continue
                if float(side_score.get("confidence") or 0.0) < self.min_confidence_to_submit:
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=side_score.get("selected_price"),
                        spread=side_score.get("spread"),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason="confidence_below_minimum",
                        subsystem="confidence_sizing",
                    )
                    continue
                side_results.append(side_score)
            for side_score in side_results:
                target = self._build_target(quote, side_score)
                if target is not None:
                    targets.append((target, quote))
        return targets

    def select_best_target(self, targets: List[Tuple[TradeTarget, Quote]]) -> Optional[Tuple[TradeTarget, Quote]]:
        if not targets:
            return None
        ranked = sorted(targets, key=lambda item: (item[0].ranking_score, item[0].confidence, item[0].liquidity), reverse=True)
        best = ranked[0]
        for target, quote in ranked[1:]:
            self._emit_final_reject(
                ticker=target.ticker,
                family=target.family,
                side=target.side,
                selected_price=target.selected_price,
                spread=target.spread,
                liquidity=target.liquidity,
                volume=target.volume,
                reason="lower_rank_candidate",
                subsystem="single_ranker",
                extra={"winning_ticker": best[0].ticker, "winning_score": best[0].ranking_score},
            )
        return best

    def _payload_for_target(self, target: TradeTarget) -> Dict[str, Any]:
        payload = kalshi_execution.canonical_order_payload(
            ticker=target.ticker,
            side=target.side,
            action="buy",
            count=target.count,
            price=target.selected_price,
            reduce_only=False,
            target_id=target.target_id,
        )
        payload.update(
            {
                "family": target.family,
                "selected_side": target.side,
                "selected_price": target.selected_price,
                "confidence": target.confidence,
                "ranking_score": target.ranking_score,
                "spread": target.spread,
                "liquidity": target.liquidity,
                "volume": target.volume,
                "orderbook_snapshot_id": target.orderbook_snapshot_id,
                "price_zone": target.price_zone,
                "target_id": target.target_id,
                "trace_id": target.trace_id or target.target_id,
                "signal_detected_ts": target.signal_detected_ts,
                "target_created_ts": target.target_created_ts or target.created_ts,
                "created_ts": target.created_ts,
            }
        )
        return payload

    def confirm_entry(self, target: TradeTarget, current_orderbook: Quote | Dict[str, Any] | None = None, *, payload: Dict[str, Any] | None = None, source: str = "confirm_entry") -> Dict[str, Any]:
        if not isinstance(target, TradeTarget):
            return {"allowed": False, "reason": "target_not_immutable_trade_target"}
        payload = payload if isinstance(payload, dict) else self._payload_for_target(target)
        side = normalize_side(target.side)
        payload_side = normalize_side(payload.get("side"))
        payload_selected_side = normalize_side(payload.get("selected_side"))
        disabled_reason = self._disabled_new_entry_reason(family=target.family, ticker=target.ticker, market=getattr(current_orderbook, "raw_market", None))
        if disabled_reason:
            return {"allowed": False, "reason": disabled_reason, "payload": payload}
        if side not in {"yes", "no"}:
            return {"allowed": False, "reason": "missing_selected_side", "payload": payload}
        for payload_field, value in (("side", payload_side), ("selected_side", payload_selected_side)):
            if value in {"yes", "no"} and value != side:
                self._log_confirm_entry_reject(
                    target,
                    payload,
                    reason="payload_side_mismatch",
                    extra={"payload_field": payload_field, "payload_side": value},
                )
                return {"allowed": False, "reason": "payload_side_mismatch", "payload": payload}
        price_keys = [key for key in ("yes_price", "no_price") if payload.get(key) not in (None, "")]
        expected_price_key = "yes_price" if side == "yes" else "no_price"
        if price_keys and price_keys != [expected_price_key]:
            self._log_confirm_entry_reject(
                target,
                payload,
                reason="payload_side_price_mismatch",
                extra={"price_keys": price_keys, "expected_price_key": expected_price_key},
            )
            return {"allowed": False, "reason": "payload_side_price_mismatch", "payload": payload}
        freshness = self._entry_signal_age(target, payload, now=time.time())
        max_signal_age = float(self.max_signal_age_seconds)
        if freshness["signal_age_seconds"] > max_signal_age:
            self._log_confirm_entry_reject(
                target,
                payload,
                reason="stale_signal",
                extra={
                    "signal_age_seconds": freshness["signal_age_seconds"],
                    "max_signal_age_seconds": max_signal_age,
                    "signal_ts_source": freshness.get("signal_ts_source"),
                },
            )
            return {
                "allowed": False,
                "reason": "stale_signal",
                "payload": payload,
                "signal_age_seconds": freshness["signal_age_seconds"],
                "max_signal_age_seconds": max_signal_age,
            }
        if not valid_trade_price(target.selected_price):
            return {"allowed": False, "reason": "selected_price_out_of_bounds", "payload": payload}
        if target.count < 1 or target.count > self.max_live_order_size:
            return {"allowed": False, "reason": "sane_max_sizing_guard", "payload": payload}
        quote = current_orderbook if isinstance(current_orderbook, Quote) else None
        if quote is not None:
            if quote.synthetic or not quote.orderbook_real:
                return {"allowed": False, "reason": "fake_or_synthetic_book_rejected", "payload": payload}
            executable_price = quote.price_for_side(side, "buy")
            executable_size = quote.size_for_side(side, "buy")
            if not valid_trade_price(executable_price):
                return {"allowed": False, "reason": "no_executable_selected_side_price", "payload": payload}
            if executable_size <= 0.0:
                return {"allowed": False, "reason": "selected_side_no_visible_liquidity", "payload": payload}
        self.reconcile_live_positions(min_interval_seconds=5.0)
        risk = kalshi_risk.evaluate_entry_risk(self, target, payload)
        if not risk.get("ok"):
            return {"allowed": False, "reason": risk.get("reason"), "payload": payload, "risk": risk}
        payload["_aion_confirm_entry_passed"] = True
        payload["_aion_confirm_entry_source"] = source
        return {"allowed": True, "reason": "passed", "payload": payload}

    def confirm_exit(self, position: Dict[str, Any], decision: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"allowed": False, "reason": "invalid_exit_payload"}
        if not valid_trade_price(payload.get("yes_price") if payload.get("side") == "yes" else payload.get("no_price")):
            return {"allowed": False, "reason": "invalid_exit_price", "payload": payload}
        risk = kalshi_risk.evaluate_exit_risk(self, position, payload)
        if not risk.get("ok"):
            return {"allowed": False, "reason": risk.get("reason"), "payload": payload, "risk": risk}
        payload["_aion_confirm_exit_passed"] = True
        return {"allowed": True, "reason": "passed", "payload": payload}

    def submit_target(self, target: TradeTarget, quote: Quote) -> Dict[str, Any]:
        payload = self._payload_for_target(target)
        confirmation = self.confirm_entry(target, quote, payload=payload, source="submit_target")
        if not confirmation.get("allowed"):
            self._emit_final_reject(
                ticker=target.ticker,
                family=target.family,
                side=target.side,
                selected_price=target.selected_price,
                spread=target.spread,
                liquidity=target.liquidity,
                volume=target.volume,
                reason=confirmation.get("reason") or "confirm_entry_rejected",
                subsystem="confirm_entry",
                extra={"risk": confirmation.get("risk")},
            )
            return {"submitted": False, "reason": confirmation.get("reason"), "payload": payload}
        payload = confirmation.get("payload") if isinstance(confirmation.get("payload"), dict) else payload
        self.log_json("AION-QUALIFIED", target.to_dict())
        self.log_json("AION-DISPATCH", {**target.to_dict(), "lane": "canonical_submit"})
        self.log_json("LIVE-EXECUTION", {**target.to_dict(), "dry_run": self.effective_live_dry_run})
        result = kalshi_execution.submit_canonical_order(
            self,
            payload,
            dry_run=self.effective_live_dry_run,
            meta={
                "family": target.family,
                "side": target.side,
                "selected_price": target.selected_price,
                "spread": target.spread,
                "liquidity": target.liquidity,
                "volume": target.volume,
                "target_id": target.target_id,
            },
        )
        if result.get("submitted"):
            self.live_order_timestamps.append(time.time())
            self.state.setdefault("daily", {})["submitted_order_count"] = int(self.state.get("daily", {}).get("submitted_order_count") or 0) + 1
            self.state.setdefault("daily", {})["live_trade_count"] = int(self.state.get("daily", {}).get("live_trade_count") or 0) + 1
            row = self.record_live_position(target, order_id=result.get("order_id"))
            self.log_json("POSITION-OPEN", {**row, "submit_reason": result.get("reason")})
            self.save_state()
        return result

    def record_live_position(self, target: TradeTarget, *, order_id: str | None = None) -> Dict[str, Any]:
        executor = PositionExecutor(
            ticker=target.ticker,
            family=target.family,
            side=target.side,
            entry_price=target.selected_price,
            count=target.count,
            entry_ts=time.time(),
            tp=self.exit_config["take_profit"],
            sl=self.exit_config["stop_loss"],
            max_hold_seconds=self.exit_config["max_hold_seconds"],
            trailing_start=self.exit_config["trailing_start"],
            trailing_distance=self.exit_config["trailing_distance"],
        )
        row = {
            "ticker": target.ticker,
            "family": target.family,
            "side": target.side,
            "count": target.count,
            "entry_price": target.selected_price,
            "entry_ts": time.time(),
            "status": "open",
            "order_id": order_id,
            "target_id": target.target_id,
            "confidence": target.confidence,
            "ranking_score": target.ranking_score,
            "price_zone": target.price_zone,
            "position_executor": executor.to_dict(),
        }
        self.state.setdefault("positions", {})[target.ticker] = row
        self.live_positions = [
            position for position in self.live_positions
            if str(position.get("ticker") or "") != target.ticker
        ] + [row]
        return row

    def extract_position_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(row.get("ticker") or row.get("market_ticker") or row.get("market_id") or "").strip()
        yes_count = abs(float(self.to_float(row.get("yes_count") or row.get("yes_contracts"), 0.0) or 0.0))
        no_count = abs(float(self.to_float(row.get("no_count") or row.get("no_contracts"), 0.0) or 0.0))
        signed_position = float(self.to_float(row.get("position") or row.get("position_count"), 0.0) or 0.0)
        side = None
        count = 0.0
        if yes_count > 0.0:
            side = "yes"
            count = yes_count
        elif no_count > 0.0:
            side = "no"
            count = no_count
        elif signed_position != 0.0:
            side = "yes" if signed_position > 0 else "no"
            count = abs(signed_position)
        else:
            side = normalize_side(row.get("side")) or normalize_side(row.get("outcome"))
            count = abs(float(self.to_float(row.get("count"), 0.0) or 0.0))
        entry_price = coerce_price(
            row.get("avg_price")
            or row.get("average_price")
            or row.get("entry_price")
            or row.get("price")
        )
        local = (self.state.get("positions") or {}).get(ticker, {}) if ticker else {}
        return {
            **(local if isinstance(local, dict) else {}),
            "ticker": ticker,
            "family": str((local or {}).get("family") or market_family({"ticker": ticker})).upper(),
            "side": side,
            "count": count,
            "entry_price": entry_price or (local or {}).get("entry_price"),
            "status": "open" if count > 0.0 else "closed",
            "raw": row,
            "updated_ts": time.time(),
        }

    def reconcile_live_positions(self, *, force: bool = False, min_interval_seconds: float = 15.0) -> Dict[str, Any]:
        now = time.time()
        if not force and now - self._last_reconcile_ts < min_interval_seconds:
            return {"ok": True, "reason": "recent_reconcile", "positions": self.live_positions}
        self._last_reconcile_ts = now
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/positions", timeout=15)
        if not result.get("ok"):
            self.live_positions = self._local_open_positions()
            self.log_json(
                "LIVE-POSITIONS-FALLBACK",
                {
                    "reason": result.get("reason"),
                    "status_code": result.get("status_code"),
                    "open_positions": len(self.live_positions),
                    "source": "local_state",
                },
            )
            return {"ok": False, "reason": result.get("reason"), "positions": self.live_positions}
        body = result.get("body") if isinstance(result.get("body"), dict) else {}
        raw_positions = body.get("market_positions") or body.get("positions") or body.get("event_positions") or body.get("data") or []
        if isinstance(raw_positions, dict):
            raw_positions = list(raw_positions.values())
        normalized = [
            self.extract_position_summary(position)
            for position in raw_positions
            if isinstance(position, dict)
        ]
        open_by_ticker = {
            position["ticker"]: position
            for position in normalized
            if position.get("ticker") and str(position.get("status")) == "open"
        }
        previous_positions = self.state.setdefault("positions", {})
        for ticker, old_position in list(previous_positions.items()):
            if ticker not in open_by_ticker and isinstance(old_position, dict) and str(old_position.get("status") or "open") == "open":
                closed = dict(old_position)
                closed["status"] = "closed"
                closed["closed_ts"] = time.time()
                previous_positions[ticker] = closed
                self.record_exit_outcome(closed, {"reason": "position_reconcile_closed"})
        for ticker, position in open_by_ticker.items():
            previous_positions[ticker] = position
        self.live_positions = list(open_by_ticker.values())
        self.save_state()
        self.log_json(
            "LIVE-POSITIONS-API",
            {
                "ok": True,
                "open_positions": len(self.live_positions),
                "status_code": result.get("status_code"),
            },
        )
        return {"ok": True, "positions": self.live_positions, "source": "api"}

    def record_exit_outcome(self, position: Dict[str, Any], decision: Dict[str, Any], result: Dict[str, Any] | None = None) -> Dict[str, Any]:
        record = kalshi_exits.outcome_record(position, decision, result)
        learning = self.state.setdefault("learning", {})
        outcomes = learning.setdefault("exit_outcomes", [])
        outcomes.append(record)
        del outcomes[:-500]
        key = self._learning_bucket_key(record.get("family"), record.get("side"), record.get("price_zone"))
        bucket = learning.setdefault("family_side_zone", {}).setdefault(key, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "mfe": 0.0, "mae": 0.0})
        pnl = coerce_float(record.get("pnl"), 0.0) or 0.0
        bucket["trades"] = int(bucket.get("trades") or 0) + 1
        bucket["wins"] = int(bucket.get("wins") or 0) + (1 if pnl > 0 else 0)
        bucket["losses"] = int(bucket.get("losses") or 0) + (1 if pnl < 0 else 0)
        bucket["pnl"] = round(float(bucket.get("pnl") or 0.0) + pnl, 4)
        bucket["mfe"] = round(max(float(bucket.get("mfe") or 0.0), float(coerce_float(record.get("mfe"), 0.0) or 0.0)), 4)
        bucket["mae"] = round(min(float(bucket.get("mae") or 0.0), float(coerce_float(record.get("mae"), 0.0) or 0.0)), 4)
        daily = self.state.setdefault("daily", {})
        daily["realized_pnl"] = round(float(daily.get("realized_pnl") or 0.0) + pnl, 4)
        self.log_json("AION-EXIT-OUTCOME-LEARNING", {"record": record, "bucket": key, "bucket_state": bucket})
        self.save_state()
        return record

    def manage_live_positions(self) -> Dict[str, Any]:
        self.reconcile_live_positions(force=True)
        submitted = 0
        decisions = []
        for position in list(self.live_positions):
            ticker = str(position.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                market = self.get_live_market_detail(ticker)
                orderbook = self.get_live_market_orderbook(ticker)
                quote = build_quote(market, orderbook)
            except Exception as exc:
                self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": False, "reason": f"quote_error:{exc.__class__.__name__}"})
                continue
            decision = kalshi_exits.evaluate_exit(position, quote, self.exit_config)
            position["position_executor"] = decision.get("position_executor") or position.get("position_executor")
            self.state.setdefault("positions", {}).setdefault(ticker, {}).update({"position_executor": position["position_executor"]})
            self.log_json("AION-POSITION-INTELLIGENCE", decision)
            if decision.get("action") != "exit":
                decisions.append(decision)
                continue
            payload = kalshi_exits.build_exit_payload(position, decision)
            confirmation = self.confirm_exit(position, decision, payload or {})
            if not confirmation.get("allowed"):
                self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": False, "reason": confirmation.get("reason")})
                decisions.append({**decision, "submitted": False, "submit_reason": confirmation.get("reason")})
                continue
            result = kalshi_execution.submit_canonical_order(
                self,
                confirmation.get("payload"),
                dry_run=self.effective_live_dry_run,
                meta={
                    "family": decision.get("family"),
                    "side": decision.get("side"),
                    "selected_price": decision.get("exit_price"),
                    "spread": quote.spread,
                    "liquidity": quote.liquidity,
                    "volume": quote.volume,
                    "exit_reason": decision.get("reason"),
                },
            )
            if result.get("submitted"):
                submitted += 1
                self.live_order_timestamps.append(time.time())
                position["status"] = "exit_pending"
                position["exit_reason"] = decision.get("reason")
                position["exit_price"] = decision.get("exit_price")
                self.state.setdefault("positions", {})[ticker] = position
                self.record_exit_outcome(position, decision, result)
                self.log_json("EXIT_SUBMITTED", {"ticker": ticker, "side": decision.get("side"), "price": decision.get("exit_price"), "reason": decision.get("reason"), "order_id": result.get("order_id")})
            self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": bool(result.get("submitted")), "reason": result.get("reason"), "pnl": decision.get("unrealized_pnl")})
            decisions.append({**decision, "submitted": bool(result.get("submitted")), "submit_reason": result.get("reason")})
        self.save_state()
        return {"ok": True, "exit_orders_submitted": submitted, "decisions": decisions}

    def cancel_live_order(self, order_id: str) -> Dict[str, Any]:
        order_id = str(order_id or "").strip()
        if not order_id:
            return {"ok": False, "reason": "missing_order_id"}
        result = self._live_request_json("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}", timeout=15)
        self.log_json("LIVE-CANCEL", {"ok": bool(result.get("ok")), "reason": result.get("reason"), "status_code": result.get("status_code"), "order_id": order_id})
        return result

    def preview_live_ticker_selection(self) -> Dict[str, Any]:
        targets = self.discover_trade_targets()
        selected = self.select_best_target(targets)
        payload = {
            "candidate_count": len(targets),
            "selected": selected[0].to_dict() if selected else None,
        }
        self.log_json("AION-PREVIEW-LIVE-TICKER", payload)
        return payload

    def run_once(self) -> Dict[str, Any]:
        self.cycle_count += 1
        self.log_json("AION-CYCLE", {"cycle": self.cycle_count, "stage": "scan"})
        self.reconcile_live_positions(min_interval_seconds=20.0)
        targets = self.discover_trade_targets()
        selected = self.select_best_target(targets)
        submit_result = {"submitted": False, "reason": "no_candidate"}
        if selected is None:
            self.log_json("AION-LIVE-CANDIDATE-FUNNEL", {"cycle": self.cycle_count, "candidate_count": 0, "reason": "no_candidate"})
        else:
            target, quote = selected
            self.log_json("AION-LIVE-CANDIDATE-FUNNEL", {"cycle": self.cycle_count, "candidate_count": len(targets), "selected": target.to_dict()})
            submit_result = self.submit_target(target, quote)
        exit_result = self.manage_live_positions()
        self.log_json(
            "AION-CYCLE-SUMMARY",
            {
                "cycle": self.cycle_count,
                "candidate_count": len(targets),
                "submitted": bool(submit_result.get("submitted")),
                "submit_reason": submit_result.get("reason"),
                "open_positions": len(self.live_positions),
                "exit_orders_submitted": exit_result.get("exit_orders_submitted"),
            },
        )
        return {"submit": submit_result, "exits": exit_result}

    def submit_live_order(self, q: Any, size: int | None = None, lane: str | None = None, payload_override: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = dict(payload_override or {})
        ticker = str(payload.get("ticker") or getattr(q, "ticker", "") or "").strip()
        if not ticker:
            return {"submitted": False, "reason": "missing_ticker"}
        try:
            market = self.get_live_market_detail(ticker)
            orderbook = self.get_live_market_orderbook(ticker)
            quote = build_quote(market, orderbook)
        except Exception as exc:
            return {"submitted": False, "reason": f"quote_error:{exc.__class__.__name__}"}
        side = normalize_side(payload.get("side") or payload.get("selected_side") or getattr(q, "selected_side", None) or getattr(q, "side", None))
        price = coerce_price(payload.get("selected_price") or payload.get("price") or getattr(q, "selected_price", None))
        if side not in {"yes", "no"}:
            side_score = max((self._score_side(quote, "yes"), self._score_side(quote, "no")), key=lambda item: float((item or {}).get("score") or -1))
        else:
            side_score = self._score_side(quote, side)
        if not side_score or side_score.get("blocked"):
            return {"submitted": False, "reason": (side_score or {}).get("reason") or "no_executable_selected_side_price"}
        if price is not None:
            side_score = dict(side_score)
            side_score["selected_price"] = price
        target = self._build_target(quote, side_score)
        if target is None:
            return {"submitted": False, "reason": "target_build_failed"}
        if size:
            target = TradeTarget(**{**target.to_dict(), "count": min(int(size), self.max_live_order_size)})
        self.log_json("AION-SUBMIT-BYPASS-REMOVED", {"ticker": ticker, "source": "submit_live_order_wrapper", "lane": lane, "decision": "canonical_submit_only"})
        return self.submit_target(target, quote)

    def place_live_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        action = str(payload.get("action") or "buy").strip().lower()
        if action in {"sell", "exit", "close"} or payload.get("reduce_only"):
            ticker = str(payload.get("ticker") or "").strip()
            position = next((item for item in self.live_positions if str(item.get("ticker") or "") == ticker), {})
            confirmation = self.confirm_exit(position, {"reason": payload.get("exit_reason") or "direct_reduce_only_exit"}, payload)
            if not confirmation.get("allowed"):
                return {"submitted": False, "reason": confirmation.get("reason"), "payload": payload}
            return kalshi_execution.submit_canonical_order(self, confirmation.get("payload"), dry_run=self.effective_live_dry_run)
        self.log_json("AION-SUBMIT-BYPASS-REMOVED", {"ticker": payload.get("ticker"), "source": "place_live_order_wrapper", "decision": "canonical_submit_only"})
        proxy = type("PayloadProxy", (), payload)
        return self.submit_live_order(proxy, size=payload.get("count"), payload_override=payload)

    def run_live_preflight(self) -> Dict[str, Any]:
        auth = self.run_live_auth_get_smoke_test()
        preview = self.preview_live_ticker_selection()
        ok = bool((self.effective_live_dry_run or auth.get("ok")) and preview.get("candidate_count", 0) >= 0)
        payload = {
            "ready": ok,
            "effective_dry_run": self.effective_live_dry_run,
            "auth_ok": bool(auth.get("ok")),
            "candidate_count": preview.get("candidate_count"),
            "live_mode": self.live_mode,
            "live_armed": self.live_armed,
            "live_env_confirmed": self.live_env_confirmed,
        }
        self.log_json("AION-LIVE-PREFLIGHT", payload)
        return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--arm-live", action="store_true")
    parser.add_argument("--live-smoke-test", action="store_true")
    parser.add_argument("--live-auth-get-smoke-test", action="store_true")
    parser.add_argument("--live-preflight", action="store_true")
    parser.add_argument("--monitor-live-positions-once", action="store_true")
    parser.add_argument("--manage-live-positions", action="store_true")
    parser.add_argument("--preview-live-ticker", action="store_true")
    parser.add_argument("--cancel-live-order-id")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    live_env_confirmed = env_bool("KALSHI_ALLOW_LIVE", False) or env_bool("LIVE_ENV_CONFIRMED", False)
    bot = KalshiSniperBot(live_mode=args.live, live_armed=bool(args.live and args.arm_live), live_env_confirmed=live_env_confirmed)

    def _stop(_signum: int, _frame: Any) -> None:
        bot.stop_requested = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    auth_config = bot.get_live_auth_config()
    bot.log_json(
        "LIVE-CONFIG",
        {
            "live_mode": bot.live_mode,
            "live_armed": bot.live_armed,
            "live_env_confirmed": bot.live_env_confirmed,
            "configured_dry_run": bot.configured_dry_run,
            "effective_dry_run": bot.effective_live_dry_run,
            "api_key_source": auth_config.get("api_key_source"),
            "private_key_path": auth_config.get("private_key_path"),
            "private_key_exists": auth_config.get("private_key_exists"),
            "base_url": bot.get_live_base_url(),
            "max_live_order_size": bot.max_live_order_size,
            "max_open_positions": bot.max_open_positions,
            "sports_only": True,
            "tennis_shutdown": True,
            "kxmlbks_shutdown": True,
        },
    )

    if args.live_auth_get_smoke_test:
        bot.run_live_auth_get_smoke_test()
        return
    if args.live_smoke_test:
        bot.run_live_smoke_test()
        return
    if args.live_preflight:
        bot.run_live_preflight()
        return
    if args.monitor_live_positions_once:
        bot.reconcile_live_positions(force=True)
        bot.manage_live_positions()
        return
    if args.manage_live_positions:
        while not bot.stop_requested:
            bot.manage_live_positions()
            time.sleep(bot.loop_sleep_seconds)
        return
    if args.preview_live_ticker:
        bot.preview_live_ticker_selection()
        return
    if args.cancel_live_order_id:
        bot.cancel_live_order(args.cancel_live_order_id)
        return

    if not args.live:
        bot.log_json("SAFEGUARD", {"reason": "running_without_live_flag", "effective_dry_run": True})
    elif args.live and args.arm_live and bot.live_env_confirmed and not bot.effective_live_dry_run:
        bot.log_json("SAFEGUARD", {"reason": "live_trading_armed", "effective_dry_run": False})
    else:
        bot.log_json("SAFEGUARD", {"reason": "live_trading_not_fully_armed", "effective_dry_run": bot.effective_live_dry_run})

    while not bot.stop_requested:
        try:
            bot.run_once()
            if args.once:
                break
            time.sleep(bot.loop_sleep_seconds)
        except Exception as exc:
            bot.log_json("MAIN-LOOP-ERROR", {"reason": exc.__class__.__name__, "message": str(exc)[:300]})
            time.sleep(5.0)


if __name__ == "__main__":
    main()
