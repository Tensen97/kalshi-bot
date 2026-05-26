#!/usr/bin/env python3
"""Lean replay/backtest runner for AION sports execution snapshots.

Replay is passive by design. It captures and replays real quote/orderbook
snapshots, reuses the existing side-selection/orderbook pipeline when
available, and never calls live order submission.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_SNAPSHOT_FILENAME = "aion_replay_snapshots.jsonl"
SPORTS_FAMILY_PREFIXES = (
    "KXNBA",
    "KXWNBA",
    "KXMLB",
    "KXNFL",
    "KXNHL",
    "KXNCAA",
    "KXNCAAB",
    "KXNCAAF",
    "KXCFB",
    "KXCBB",
    "KXATP",
    "KXWTA",
    "KXITF",
    "KXTENNIS",
    "KXGOLF",
    "KXEPL",
    "KXSOCCER",
    "KXUFC",
    "KXMMA",
    "KXBOX",
)


def _truthy_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_snapshot_path():
    explicit = str(os.environ.get("AION_REPLAY_SNAPSHOT_FILE") or "").strip()
    if explicit:
        return explicit
    try:
        from runtime.paths import ensure_runtime_layout, runtime_cache_file

        ensure_runtime_layout()
        return runtime_cache_file(DEFAULT_SNAPSHOT_FILENAME)
    except Exception:
        pass
    return str(Path("runtime") / "cache" / DEFAULT_SNAPSHOT_FILENAME)


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        numeric = float(value)
        if not math.isfinite(numeric):
            return default
        return numeric
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    numeric = _safe_float(value, None)
    if numeric is None:
        return default
    try:
        return int(round(numeric))
    except Exception:
        return default


def _price(value):
    numeric = _safe_float(value, None)
    if numeric is None:
        return None
    if 1.0 < numeric <= 100.0:
        numeric = numeric / 100.0
    if 0.0 <= numeric <= 1.0:
        return round(float(numeric), 4)
    return None


def _positive_size(value):
    numeric = _safe_float(value, None)
    if numeric is None:
        return None
    return max(0.0, float(numeric))


def _parse_timestamp(value):
    if value in (None, "", [], {}):
        return time.time()
    numeric = _safe_float(value, None)
    if numeric is not None:
        return float(numeric / 1000.0 if numeric > 10_000_000_000 else numeric)
    text = str(value).strip()
    if not text:
        return time.time()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return time.time()


def _iso_utc(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _mapping_value(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, "", [], {}):
            return mapping.get(key)
    return None


def _object_value(obj, *names):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return _mapping_value(obj, *names)
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value not in (None, "", [], {}):
            return value
    return None


def _family_from_ticker(ticker):
    text = str(ticker or "").strip().upper()
    if not text:
        return "UNKNOWN"
    return text.split("-")[0].strip() or "UNKNOWN"


def _is_sports_family(family, ticker=None):
    family = str(family or _family_from_ticker(ticker) or "").strip().upper()
    return bool(family.startswith(SPORTS_FAMILY_PREFIXES))


def _infer_binary_quotes(yes_bid, yes_ask, no_bid, no_ask):
    if no_bid is None and yes_ask is not None:
        no_bid = max(0.0, min(1.0, 1.0 - float(yes_ask)))
    if no_ask is None and yes_bid is not None:
        no_ask = max(0.0, min(1.0, 1.0 - float(yes_bid)))
    if yes_bid is None and no_ask is not None:
        yes_bid = max(0.0, min(1.0, 1.0 - float(no_ask)))
    if yes_ask is None and no_bid is not None:
        yes_ask = max(0.0, min(1.0, 1.0 - float(no_bid)))
    return yes_bid, yes_ask, no_bid, no_ask


def _first_price(*sources):
    for value in sources:
        result = _price(value)
        if result is not None:
            return result
    return None


def _first_size(*sources):
    for value in sources:
        result = _positive_size(value)
        if result is not None:
            return result
    return None


def _selected_side_from_payload(payload, orderbook_resolution, decision, q, market):
    for source in (payload, orderbook_resolution, decision, market):
        side = str(_mapping_value(source, "selected_side", "side", "outcome_side") or "").strip().lower()
        if side in {"yes", "no"}:
            return side
    side = str(_object_value(q, "selected_side", "side") or "").strip().lower()
    return side if side in {"yes", "no"} else None


def build_replay_snapshot(
    *,
    market=None,
    q=None,
    quote_diag=None,
    orderbook_resolution=None,
    payload=None,
    decision=None,
    source=None,
    extra=None,
):
    """Build a compact replay snapshot from real live market/candidate context."""
    market = market if isinstance(market, dict) else {}
    quote_diag = quote_diag if isinstance(quote_diag, dict) else {}
    orderbook_resolution = orderbook_resolution if isinstance(orderbook_resolution, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    ticker = str(
        payload.get("ticker")
        or orderbook_resolution.get("ticker")
        or decision.get("ticker")
        or quote_diag.get("ticker")
        or _object_value(q, "ticker")
        or market.get("ticker")
        or ""
    ).strip()
    family = str(
        decision.get("family")
        or quote_diag.get("family")
        or market.get("series_ticker")
        or market.get("event_ticker")
        or _object_value(q, "family")
        or _family_from_ticker(ticker)
        or "UNKNOWN"
    ).strip().upper()
    ts = _parse_timestamp(
        extra.get("timestamp")
        or decision.get("timestamp")
        or quote_diag.get("timestamp")
        or market.get("timestamp")
        or market.get("ts")
        or orderbook_resolution.get("ts")
    )

    yes_bid = _first_price(
        orderbook_resolution.get("yes_bid_dollars"),
        orderbook_resolution.get("yes_bid"),
        quote_diag.get("yes_bid_dollars"),
        quote_diag.get("yes_bid"),
        market.get("yes_bid_dollars"),
        market.get("yes_bid"),
        _object_value(q, "yes_bid_dollars", "yes_bid"),
    )
    yes_ask = _first_price(
        orderbook_resolution.get("yes_ask_dollars"),
        orderbook_resolution.get("yes_ask"),
        quote_diag.get("yes_ask_dollars"),
        quote_diag.get("yes_ask"),
        market.get("yes_ask_dollars"),
        market.get("yes_ask"),
        _object_value(q, "yes_ask_dollars", "yes_ask"),
    )
    no_bid = _first_price(
        orderbook_resolution.get("no_bid_dollars"),
        orderbook_resolution.get("no_bid"),
        quote_diag.get("no_bid_dollars"),
        quote_diag.get("no_bid"),
        market.get("no_bid_dollars"),
        market.get("no_bid"),
        _object_value(q, "no_bid_dollars", "no_bid"),
    )
    no_ask = _first_price(
        orderbook_resolution.get("no_ask_dollars"),
        orderbook_resolution.get("no_ask"),
        quote_diag.get("no_ask_dollars"),
        quote_diag.get("no_ask"),
        market.get("no_ask_dollars"),
        market.get("no_ask"),
        _object_value(q, "no_ask_dollars", "no_ask"),
    )
    yes_bid, yes_ask, no_bid, no_ask = _infer_binary_quotes(yes_bid, yes_ask, no_bid, no_ask)

    yes_top_size = _first_size(
        orderbook_resolution.get("yes_top_size"),
        orderbook_resolution.get("yes_ask_size"),
        quote_diag.get("yes_top_size"),
        quote_diag.get("yes_ask_size"),
        market.get("yes_top_size"),
        market.get("yes_ask_size"),
        market.get("yes_ask_size_fp"),
        _object_value(q, "yes_top_size", "yes_ask_size"),
    )
    no_top_size = _first_size(
        orderbook_resolution.get("no_top_size"),
        orderbook_resolution.get("no_ask_size"),
        quote_diag.get("no_top_size"),
        quote_diag.get("no_ask_size"),
        market.get("no_top_size"),
        market.get("no_ask_size"),
        market.get("no_ask_size_fp"),
        _object_value(q, "no_top_size", "no_ask_size"),
    )
    selected_side = _selected_side_from_payload(payload, orderbook_resolution, decision, q, market)
    selected_price = _first_price(
        payload.get("selected_price_dollars"),
        payload.get("selected_price"),
        payload.get("yes_price") if selected_side == "yes" else None,
        payload.get("no_price") if selected_side == "no" else None,
        orderbook_resolution.get("selected_price_dollars"),
        orderbook_resolution.get("selected_price"),
        decision.get("selected_price"),
        quote_diag.get("selected_price"),
        _object_value(q, "selected_price"),
    )
    if selected_price is None and selected_side == "yes":
        selected_price = yes_ask
    if selected_price is None and selected_side == "no":
        selected_price = no_ask

    yes_spread = round(yes_ask - yes_bid, 4) if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid else None
    no_spread = round(no_ask - no_bid, 4) if no_bid is not None and no_ask is not None and no_ask >= no_bid else None
    selected_spread = yes_spread if selected_side == "yes" else no_spread if selected_side == "no" else None
    spread = _safe_float(
        decision.get("spread")
        or quote_diag.get("spread")
        or market.get("spread")
        or selected_spread
        or yes_spread
        or no_spread,
        None,
    )
    liquidity = _safe_float(
        decision.get("liquidity")
        or quote_diag.get("liquidity_depth")
        or quote_diag.get("liquidity")
        or market.get("liquidity_dollars")
        or market.get("liquidity")
        or _object_value(q, "liquidity"),
        None,
    )
    selected_top_size = _first_size(
        decision.get("selected_top_size"),
        orderbook_resolution.get("selected_top_size"),
        yes_top_size if selected_side == "yes" else None,
        no_top_size if selected_side == "no" else None,
    )

    candidate_score = _safe_float(
        decision.get("candidate_score")
        or decision.get("score")
        or quote_diag.get("candidate_score")
        or quote_diag.get("score")
        or _object_value(q, "candidate_score", "score", "final_score"),
        None,
    )
    edge_after_cost = _safe_float(
        decision.get("edge_after_cost")
        or decision.get("EV")
        or decision.get("ev")
        or quote_diag.get("edge_after_cost")
        or quote_diag.get("final_edge")
        or _object_value(q, "edge_after_cost", "EV", "ev"),
        None,
    )
    probability = _safe_float(
        decision.get("probability")
        or decision.get("model_probability")
        or quote_diag.get("model_probability")
        or quote_diag.get("probability")
        or _object_value(q, "model_probability", "probability"),
        None,
    )
    fill_probability = _safe_float(
        decision.get("fill_probability")
        or quote_diag.get("fill_probability")
        or orderbook_resolution.get("fill_probability")
        or _object_value(q, "fill_probability"),
        None,
    )
    intended_size = _safe_int(
        payload.get("count")
        or decision.get("count")
        or decision.get("intended_order_size")
        or quote_diag.get("intended_order_size")
        or _object_value(q, "count", "size"),
        1,
    )
    intended_size = max(1, int(intended_size or 1))
    selected_decision = str(
        decision.get("selected_decision")
        or decision.get("decision")
        or quote_diag.get("decision")
        or ("BUY" if selected_side and selected_price is not None else "OBSERVE")
    ).strip().upper()

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": source or extra.get("source") or "unknown",
        "timestamp": _iso_utc(ts),
        "timestamp_ts": round(float(ts), 6),
        "ticker": ticker,
        "family": family,
        "sport": market.get("sport") or quote_diag.get("sport") or decision.get("sport"),
        "title": market.get("title") or market.get("subtitle") or _object_value(q, "title"),
        "event_ticker": market.get("event_ticker"),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_spread": yes_spread,
        "no_spread": no_spread,
        "spread": None if spread is None else round(float(spread), 4),
        "yes_top_size": yes_top_size,
        "no_top_size": no_top_size,
        "selected_top_size": selected_top_size,
        "liquidity": liquidity,
        "candidate_score": candidate_score,
        "probability": probability,
        "edge_after_cost": edge_after_cost,
        "EV": edge_after_cost,
        "fill_probability": fill_probability,
        "selected_decision": selected_decision,
        "selected_side": selected_side,
        "selected_price": selected_price,
        "intended_order_side": selected_side,
        "intended_order_price": selected_price,
        "intended_order_size": intended_size,
        "orderbook_ready": bool(
            orderbook_resolution.get("orderbook_ready")
            or (yes_bid is not None and yes_ask is not None)
            or (no_bid is not None and no_ask is not None)
        ),
        "payload_ready": bool(payload),
    }
    for key, value in extra.items():
        if key not in snapshot and value not in (None, "", [], {}):
            snapshot[key] = value
    return snapshot


def normalize_replay_snapshot(raw):
    if not isinstance(raw, dict):
        return None
    snapshot = dict(raw)
    ticker = str(snapshot.get("ticker") or "").strip()
    family = str(snapshot.get("family") or snapshot.get("series_ticker") or _family_from_ticker(ticker)).strip().upper()
    snapshot["schema_version"] = int(_safe_int(snapshot.get("schema_version"), SNAPSHOT_SCHEMA_VERSION) or SNAPSHOT_SCHEMA_VERSION)
    snapshot["ticker"] = ticker
    snapshot["family"] = family
    ts = _parse_timestamp(snapshot.get("timestamp_ts") or snapshot.get("timestamp") or snapshot.get("ts"))
    snapshot["timestamp_ts"] = round(float(ts), 6)
    snapshot["timestamp"] = _iso_utc(ts)
    for key in ("yes_bid", "yes_ask", "no_bid", "no_ask", "selected_price", "intended_order_price"):
        snapshot[key] = _price(snapshot.get(key))
    snapshot["yes_bid"], snapshot["yes_ask"], snapshot["no_bid"], snapshot["no_ask"] = _infer_binary_quotes(
        snapshot.get("yes_bid"),
        snapshot.get("yes_ask"),
        snapshot.get("no_bid"),
        snapshot.get("no_ask"),
    )
    yes_spread = (
        round(snapshot["yes_ask"] - snapshot["yes_bid"], 4)
        if snapshot.get("yes_bid") is not None and snapshot.get("yes_ask") is not None and snapshot["yes_ask"] >= snapshot["yes_bid"]
        else None
    )
    no_spread = (
        round(snapshot["no_ask"] - snapshot["no_bid"], 4)
        if snapshot.get("no_bid") is not None and snapshot.get("no_ask") is not None and snapshot["no_ask"] >= snapshot["no_bid"]
        else None
    )
    side = str(snapshot.get("selected_side") or snapshot.get("intended_order_side") or "").strip().lower()
    if side not in {"yes", "no"}:
        side = None
    snapshot["selected_side"] = side
    snapshot["intended_order_side"] = side
    selected_spread = yes_spread if side == "yes" else no_spread if side == "no" else None
    snapshot["yes_spread"] = yes_spread
    snapshot["no_spread"] = no_spread
    snapshot["spread"] = _safe_float(snapshot.get("spread"), selected_spread or yes_spread or no_spread)
    for key in ("yes_top_size", "no_top_size", "selected_top_size", "liquidity"):
        snapshot[key] = _positive_size(snapshot.get(key))
    for key in ("candidate_score", "probability", "edge_after_cost", "EV", "fill_probability"):
        snapshot[key] = _safe_float(snapshot.get(key), None)
    snapshot["intended_order_size"] = max(1, int(_safe_int(snapshot.get("intended_order_size"), 1) or 1))
    if snapshot.get("selected_price") is None:
        snapshot["selected_price"] = snapshot.get("yes_ask") if side == "yes" else snapshot.get("no_ask") if side == "no" else None
    if snapshot.get("intended_order_price") is None:
        snapshot["intended_order_price"] = snapshot.get("selected_price")
    return snapshot


def record_replay_snapshot(snapshot, path=None):
    normalized = normalize_replay_snapshot(snapshot)
    if not normalized:
        return False
    if not _is_sports_family(normalized.get("family"), normalized.get("ticker")):
        return False
    path = Path(path or _default_snapshot_path()).expanduser().resolve()
    line = json.dumps(normalized, sort_keys=True, default=str) + "\n"

    def _append_jsonl(target):
        target = Path(target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return True

    try:
        return _append_jsonl(path)
    except OSError:
        fallback = Path(os.environ.get("AION_REPLAY_SNAPSHOT_FALLBACK_FILE") or "/tmp/aion_replay_snapshots.jsonl").expanduser().resolve()
        if fallback == path:
            return False
        try:
            return _append_jsonl(fallback)
        except OSError:
            return False


def record_replay_snapshot_from_context(**kwargs):
    if not _truthy_env("AION_REPLAY_SNAPSHOT_CAPTURE", default=False):
        return False
    snapshot = build_replay_snapshot(**kwargs)
    return record_replay_snapshot(snapshot)


def _iter_snapshot_files(path):
    path = Path(path).expanduser()
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.glob("**/*")):
        if candidate.is_file() and candidate.suffix.lower() in {".jsonl", ".json", ".ndjson"}:
            yield candidate


def load_snapshots(input_path, *, max_snapshots=None):
    snapshots = []
    for path in _iter_snapshot_files(input_path):
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    normalized = normalize_replay_snapshot(item)
                    if normalized:
                        snapshots.append(normalized)
                    if max_snapshots and len(snapshots) >= max_snapshots:
                        break
        elif path.suffix.lower() == ".json":
            try:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                payload = None
            items = payload if isinstance(payload, list) else payload.get("snapshots") if isinstance(payload, dict) else []
            for item in items or []:
                normalized = normalize_replay_snapshot(item)
                if normalized:
                    snapshots.append(normalized)
                if max_snapshots and len(snapshots) >= max_snapshots:
                    break
        if max_snapshots and len(snapshots) >= max_snapshots:
            break
    snapshots.sort(key=lambda item: (item.get("timestamp_ts") or 0.0, item.get("ticker") or ""))
    return snapshots


class ReplayBacktester:
    def __init__(self, *, use_bot=True, stderr=None, verbose_bot=False):
        self.use_bot = bool(use_bot)
        self.stderr = stderr or sys.stderr
        self.verbose_bot = bool(verbose_bot)
        self.bot = None
        self.kalshi_web = None
        os.environ["AION_REPLAY_MODE"] = "1"
        os.environ.setdefault("AION_REPLAY_PASSIVE", "1")

    def _load_bot(self):
        if not self.use_bot or self.bot is not None:
            return self.bot
        try:
            if "runtime.paths" not in sys.modules:
                os.environ.setdefault("AION_RUNTIME_ROOT", os.environ.get("AION_REPLAY_RUNTIME_ROOT", "/tmp/aion_replay_runtime"))
            bot_stdout = self.stderr if self.verbose_bot else io.StringIO()
            with contextlib.redirect_stdout(bot_stdout):
                import kalshi_web

                kalshi_web.LIVE_MODE = False
                kalshi_web.LIVE_ARMED = False
                kalshi_web.LIVE_ENV_CONFIRMED = False
                self.bot = kalshi_web.KalshiSniperBot()
                self.kalshi_web = kalshi_web
        except Exception as exc:
            print(f"[AION-REPLAY] existing_pipeline_unavailable={exc.__class__.__name__}:{exc}", file=self.stderr)
            self.bot = None
        return self.bot

    def _is_sports_snapshot(self, snapshot):
        family = snapshot.get("family")
        ticker = snapshot.get("ticker")
        bot = self._load_bot()
        if bot is not None:
            try:
                return bool(bot._is_sports_market_or_family(family or {"ticker": ticker}))
            except Exception:
                pass
        return _is_sports_family(family, ticker)

    def _pipeline_decision(self, snapshot):
        bot = self._load_bot()
        side = snapshot.get("selected_side")
        q = SimpleNamespace(
            ticker=snapshot.get("ticker"),
            title=snapshot.get("title") or snapshot.get("ticker"),
            yes_bid=snapshot.get("yes_bid"),
            yes_ask=snapshot.get("yes_ask"),
            no_bid=snapshot.get("no_bid"),
            no_ask=snapshot.get("no_ask"),
            last_price=snapshot.get("selected_price") or 0.0,
            volume=_safe_float(snapshot.get("volume"), 0.0) or 0.0,
            liquidity=_safe_float(snapshot.get("liquidity"), 0.0) or 0.0,
            yes_top_size=snapshot.get("yes_top_size"),
            no_top_size=snapshot.get("no_top_size"),
            selected_side=side,
            selected_price=snapshot.get("selected_price"),
            edge_after_cost=snapshot.get("edge_after_cost"),
            probability=snapshot.get("probability"),
            fill_probability=snapshot.get("fill_probability"),
        )
        market = dict(snapshot)
        if bot is not None:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    side_decision = bot._aion_select_bidirectional_side(
                        ticker=snapshot.get("ticker"),
                        family=snapshot.get("family"),
                        lane="SPORTS",
                        market=market,
                        q=q,
                        quote_diag=market,
                        yes_bid=snapshot.get("yes_bid"),
                        yes_ask=snapshot.get("yes_ask"),
                        no_bid=snapshot.get("no_bid"),
                        no_ask=snapshot.get("no_ask"),
                        yes_top_size=snapshot.get("yes_top_size"),
                        no_top_size=snapshot.get("no_top_size"),
                        preferred_side=side,
                        source="replay_backtest",
                        emit_log=False,
                    )
                    if isinstance(side_decision, dict) and side_decision.get("selected_side"):
                        side = side_decision.get("selected_side")
                        q.selected_side = side
                        q.selected_price = side_decision.get("selected_price")
                        market.update(
                            {
                                "selected_side": side,
                                "selected_price": side_decision.get("selected_price"),
                                "selected_top_size": side_decision.get("selected_top_size"),
                                "fill_probability": side_decision.get("selected_fill_probability"),
                                "edge_after_cost": side_decision.get("selected_edge_after_cost"),
                                "candidate_score": side_decision.get("selected_final_score"),
                            }
                        )
                    else:
                        side_decision = side_decision if isinstance(side_decision, dict) else {}
                    orderbook_resolution = bot.resolve_aion_orderbook_for_payload(
                        snapshot.get("ticker"),
                        candidate=q,
                        market=market,
                        quote_diag=market,
                        price_source="ask",
                        selected_side=side,
                        selection_stage="replay_backtest",
                        allow_refresh=False,
                    )
                if isinstance(orderbook_resolution, dict):
                    market.update(
                        {
                            "selected_side": orderbook_resolution.get("selected_side") or market.get("selected_side"),
                            "selected_price": orderbook_resolution.get("selected_price_dollars")
                            or orderbook_resolution.get("selected_price")
                            or market.get("selected_price"),
                            "orderbook_ready": bool(orderbook_resolution.get("orderbook_ready")),
                        }
                    )
                market["pipeline_reason"] = side_decision.get("reason") if isinstance(side_decision, dict) else None
                return market
            except Exception as exc:
                market["pipeline_error"] = f"{exc.__class__.__name__}:{exc}"
        return self._fallback_decision(market)

    def _fallback_decision(self, snapshot):
        if snapshot.get("selected_side") in {"yes", "no"}:
            return snapshot
        candidates = []
        for side, bid_key, ask_key, size_key in (
            ("yes", "yes_bid", "yes_ask", "yes_top_size"),
            ("no", "no_bid", "no_ask", "no_top_size"),
        ):
            ask = snapshot.get(ask_key)
            if ask is None or ask <= 0.0 or ask >= 1.0:
                continue
            bid = snapshot.get(bid_key)
            spread = max(0.0, ask - bid) if bid is not None else 0.20
            edge = _safe_float(snapshot.get(f"{side}_edge_after_cost"), None)
            if edge is None:
                probability = _safe_float(snapshot.get("probability"), None)
                if side == "no" and probability is not None:
                    probability = 1.0 - probability
                edge = (probability - ask) if probability is not None else _safe_float(snapshot.get("edge_after_cost"), 0.0)
            size = _safe_float(snapshot.get(size_key), 0.0) or 0.0
            score = float(edge or 0.0) * 100.0 - float(spread or 0.0) * 18.0 + min(6.0, math.log1p(size))
            candidates.append((score, side, ask, size))
        if candidates:
            _, side, price, size = max(candidates)
            snapshot["selected_side"] = side
            snapshot["selected_price"] = price
            snapshot["selected_top_size"] = size
            snapshot["pipeline_reason"] = "fallback_side_score"
        else:
            snapshot["pipeline_reason"] = "no_executable_side"
        return snapshot

    def _simulate_fill(self, snapshot):
        side = str(snapshot.get("selected_side") or snapshot.get("intended_order_side") or "").strip().lower()
        if side not in {"yes", "no"}:
            return {"filled": False, "reason": "selected_side_missing"}
        ask = snapshot.get("yes_ask") if side == "yes" else snapshot.get("no_ask")
        bid = snapshot.get("yes_bid") if side == "yes" else snapshot.get("no_bid")
        limit_price = snapshot.get("selected_price") or snapshot.get("intended_order_price") or ask
        ask = _price(ask)
        bid = _price(bid)
        limit_price = _price(limit_price)
        if ask is None or not (0.0 < ask < 1.0):
            return {"filled": False, "reason": "selected_price_missing_or_invalid"}
        if limit_price is None or not (0.0 < limit_price < 1.0):
            return {"filled": False, "reason": "limit_price_missing_or_invalid"}
        if limit_price + 1e-9 < ask:
            return {"filled": False, "reason": "limit_below_top_ask", "top_ask": ask, "limit_price": limit_price}
        visible_size = snapshot.get("selected_top_size")
        if visible_size is None:
            visible_size = snapshot.get("yes_top_size") if side == "yes" else snapshot.get("no_top_size")
        visible_size = _positive_size(visible_size)
        if visible_size is None or visible_size <= 0.0:
            return {"filled": False, "reason": "top_book_size_missing_or_zero", "top_ask": ask, "limit_price": limit_price}
        requested_count = max(1, int(_safe_int(snapshot.get("intended_order_size"), 1) or 1))
        fill_count = int(max(0, min(requested_count, math.floor(visible_size))))
        if fill_count <= 0:
            return {"filled": False, "reason": "top_book_size_below_one", "top_size": visible_size}
        spread = None if bid is None else round(max(0.0, ask - bid), 4)
        return {
            "filled": True,
            "reason": "top_book_fill" if fill_count == requested_count else "partial_top_book_fill",
            "side": side,
            "entry_price": ask,
            "limit_price": limit_price,
            "count": fill_count,
            "requested_count": requested_count,
            "top_size": visible_size,
            "spread": spread,
        }

    def _exit_value(self, trade_snapshot, future_snapshots):
        side = trade_snapshot.get("selected_side")
        explicit_pnl = _safe_float(
            trade_snapshot.get("realized_pnl")
            or trade_snapshot.get("pnl")
            or trade_snapshot.get("pnl_dollars"),
            None,
        )
        if explicit_pnl is not None:
            return {"closed": True, "pnl": explicit_pnl, "reason": "explicit_realized_pnl"}
        explicit_exit = _price(
            trade_snapshot.get("exit_price")
            or trade_snapshot.get("settlement_price")
            or trade_snapshot.get("result_price")
            or trade_snapshot.get("outcome_price")
        )
        if explicit_exit is not None:
            return {"closed": True, "exit_price": explicit_exit, "reason": "explicit_exit_price"}
        ticker = trade_snapshot.get("ticker")
        ts = trade_snapshot.get("timestamp_ts") or 0.0
        for candidate in future_snapshots:
            if candidate.get("ticker") != ticker or (candidate.get("timestamp_ts") or 0.0) <= ts:
                continue
            exit_price = candidate.get("yes_bid") if side == "yes" else candidate.get("no_bid")
            exit_price = _price(exit_price)
            if exit_price is not None:
                return {
                    "closed": True,
                    "exit_price": exit_price,
                    "exit_timestamp": candidate.get("timestamp"),
                    "reason": "next_snapshot_mark_to_market",
                }
        return {"closed": False, "reason": "no_future_mark_or_outcome"}

    def run(self, snapshots):
        normalized_snapshots = []
        for item in snapshots:
            normalized = normalize_replay_snapshot(item)
            if normalized:
                normalized_snapshots.append(normalized)
        snapshots = normalized_snapshots
        snapshots.sort(key=lambda item: (item.get("timestamp_ts") or 0.0, item.get("ticker") or ""))
        totals = {
            "snapshots": len(snapshots),
            "sports_snapshots": 0,
            "decisions": 0,
            "fills": 0,
            "closed_trades": 0,
            "open_trades": 0,
            "net_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "max_drawdown": 0.0,
            "avg_ev": 0.0,
            "avg_edge_after_cost": 0.0,
        }
        reject_reasons = Counter()
        no_fill_reasons = Counter()
        family = defaultdict(lambda: {"trades": 0, "fills": 0, "closed": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        equity = 0.0
        peak_equity = 0.0
        ev_values = []
        trades = []
        for index, raw_snapshot in enumerate(snapshots):
            if not self._is_sports_snapshot(raw_snapshot):
                reject_reasons["non_sports_snapshot"] += 1
                continue
            totals["sports_snapshots"] += 1
            snapshot = self._pipeline_decision(dict(raw_snapshot))
            totals["decisions"] += 1
            family_name = str(snapshot.get("family") or "UNKNOWN").upper()
            family[family_name]["trades"] += 1
            ev = _safe_float(snapshot.get("EV"), _safe_float(snapshot.get("edge_after_cost"), None))
            if ev is not None:
                ev_values.append(float(ev))
            hard_reject = self._hard_reject_reason(snapshot)
            if hard_reject:
                reject_reasons[hard_reject] += 1
                continue
            fill = self._simulate_fill(snapshot)
            if not fill.get("filled"):
                no_fill_reasons[fill.get("reason") or "no_fill"] += 1
                continue
            totals["fills"] += 1
            family[family_name]["fills"] += 1
            snapshot.update(fill)
            exit_result = self._exit_value(snapshot, snapshots[index + 1 :])
            if exit_result.get("closed"):
                if exit_result.get("pnl") is not None:
                    pnl = float(exit_result.get("pnl") or 0.0)
                else:
                    pnl = (float(exit_result.get("exit_price") or 0.0) - float(fill.get("entry_price") or 0.0)) * int(fill.get("count") or 0)
                snapshot["simulated_pnl"] = round(pnl, 4)
                snapshot["exit_reason"] = exit_result.get("reason")
                totals["closed_trades"] += 1
                totals["net_pnl"] += pnl
                family[family_name]["closed"] += 1
                family[family_name]["pnl"] += pnl
                if pnl > 0.0:
                    totals["wins"] += 1
                    family[family_name]["wins"] += 1
                elif pnl < 0.0:
                    totals["losses"] += 1
                    family[family_name]["losses"] += 1
                equity += pnl
                peak_equity = max(peak_equity, equity)
                totals["max_drawdown"] = max(totals["max_drawdown"], peak_equity - equity)
            else:
                totals["open_trades"] += 1
                snapshot["exit_reason"] = exit_result.get("reason")
            trades.append(snapshot)
        closed = max(1, int(totals["closed_trades"] or 0))
        totals["win_rate"] = round(float(totals["wins"]) / closed, 4) if totals["closed_trades"] else 0.0
        totals["net_pnl"] = round(totals["net_pnl"], 4)
        totals["max_drawdown"] = round(totals["max_drawdown"], 4)
        totals["fill_rate"] = round(float(totals["fills"]) / max(1, totals["decisions"]), 4)
        totals["avg_ev"] = round(sum(ev_values) / len(ev_values), 6) if ev_values else 0.0
        totals["avg_edge_after_cost"] = totals["avg_ev"]
        family_rows = []
        for name, row in family.items():
            closed_count = max(1, int(row["closed"] or 0))
            family_rows.append(
                {
                    "family": name,
                    "trades": int(row["trades"]),
                    "fills": int(row["fills"]),
                    "closed": int(row["closed"]),
                    "pnl": round(float(row["pnl"]), 4),
                    "win_rate": round(float(row["wins"]) / closed_count, 4) if row["closed"] else 0.0,
                }
            )
        family_rows.sort(key=lambda item: item["pnl"], reverse=True)
        return {
            "totals": totals,
            "family_results": family_rows,
            "best_families": family_rows[:5],
            "worst_families": list(reversed(family_rows[-5:])) if family_rows else [],
            "reject_reasons": dict(reject_reasons),
            "no_fill_reasons": dict(no_fill_reasons),
            "sample_trades": trades[:10],
            "replay_passive": True,
        }

    def _hard_reject_reason(self, snapshot):
        side = snapshot.get("selected_side")
        if side not in {"yes", "no"}:
            return "selected_side_missing"
        price_value = _price(snapshot.get("selected_price"))
        if price_value is None or not (0.0 < price_value < 1.0):
            return "selected_price_missing_or_invalid"
        yes_quote_ready = snapshot.get("yes_bid") is not None and snapshot.get("yes_ask") is not None
        no_quote_ready = snapshot.get("no_bid") is not None and snapshot.get("no_ask") is not None
        if not yes_quote_ready and not no_quote_ready:
            return "malformed_orderbook"
        return None


def _format_report(result):
    totals = result.get("totals", {})
    lines = [
        "AION Replay Backtest",
        f"Snapshots: {totals.get('snapshots', 0)}",
        f"Sports snapshots: {totals.get('sports_snapshots', 0)}",
        f"Total decisions: {totals.get('decisions', 0)}",
        f"Simulated fills: {totals.get('fills', 0)}",
        f"Closed trades: {totals.get('closed_trades', 0)}",
        f"Open/unmarked trades: {totals.get('open_trades', 0)}",
        f"Net PnL: {totals.get('net_pnl', 0.0):.4f}",
        f"Win rate: {totals.get('win_rate', 0.0):.2%}",
        f"Max drawdown: {totals.get('max_drawdown', 0.0):.4f}",
        f"Average EV/edge: {totals.get('avg_ev', 0.0):.6f}",
        f"Fill rate: {totals.get('fill_rate', 0.0):.2%}",
        "",
        "Best families:",
    ]
    for row in result.get("best_families", [])[:5]:
        lines.append(f"  {row['family']}: pnl={row['pnl']:.4f} fills={row['fills']} win_rate={row['win_rate']:.2%}")
    if not result.get("best_families"):
        lines.append("  none")
    lines.append("")
    lines.append("Worst families:")
    for row in result.get("worst_families", [])[:5]:
        lines.append(f"  {row['family']}: pnl={row['pnl']:.4f} fills={row['fills']} win_rate={row['win_rate']:.2%}")
    if not result.get("worst_families"):
        lines.append("  none")
    lines.append("")
    lines.append("Rejected reasons:")
    reject_reasons = result.get("reject_reasons") or {}
    if reject_reasons:
        for reason, count in sorted(reject_reasons.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {reason}: {count}")
    else:
        lines.append("  none")
    lines.append("")
    lines.append("No-fill reasons:")
    no_fill_reasons = result.get("no_fill_reasons") or {}
    if no_fill_reasons:
        for reason, count in sorted(no_fill_reasons.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {reason}: {count}")
    else:
        lines.append("  none")
    return "\n".join(lines)


def _self_test_snapshots():
    return [
        {
            "timestamp": "2026-05-26T00:00:00+00:00",
            "ticker": "KXNBA-SELFTEST",
            "family": "KXNBA",
            "yes_bid": 0.42,
            "yes_ask": 0.46,
            "no_bid": 0.54,
            "no_ask": 0.58,
            "yes_top_size": 4,
            "no_top_size": 3,
            "selected_side": "yes",
            "selected_price": 0.46,
            "intended_order_size": 2,
            "edge_after_cost": 0.03,
            "fill_probability": 0.72,
        },
        {
            "timestamp": "2026-05-26T00:05:00+00:00",
            "ticker": "KXNBA-SELFTEST",
            "family": "KXNBA",
            "yes_bid": 0.51,
            "yes_ask": 0.55,
            "no_bid": 0.45,
            "no_ask": 0.49,
            "yes_top_size": 4,
            "no_top_size": 4,
        },
        {
            "timestamp": "2026-05-26T00:06:00+00:00",
            "ticker": "KXMLB-SELFTEST",
            "family": "KXMLB",
            "yes_bid": 0.67,
            "yes_ask": 0.72,
            "no_bid": 0.28,
            "no_ask": 0.33,
            "yes_top_size": 1,
            "no_top_size": 2,
            "selected_side": "no",
            "selected_price": 0.33,
            "intended_order_size": 1,
            "edge_after_cost": 0.025,
            "fill_probability": 0.66,
        },
        {
            "timestamp": "2026-05-26T00:12:00+00:00",
            "ticker": "KXMLB-SELFTEST",
            "family": "KXMLB",
            "yes_bid": 0.60,
            "yes_ask": 0.65,
            "no_bid": 0.35,
            "no_ask": 0.40,
            "yes_top_size": 2,
            "no_top_size": 2,
        },
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay captured AION sports snapshots without live submission.")
    parser.add_argument("--input", help="Snapshot JSONL/JSON file or directory.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    parser.add_argument("--no-bot", action="store_true", help="Use local fallback decision adapter instead of importing kalshi_web.py.")
    parser.add_argument("--verbose-bot", action="store_true", help="Show kalshi_web.py initialization logs during replay.")
    parser.add_argument("--max-snapshots", type=int, default=None, help="Limit loaded snapshots for a quick replay.")
    parser.add_argument("--self-test", action="store_true", help="Run an embedded passive replay smoke test.")
    args = parser.parse_args(argv)

    if not args.input and not args.self_test:
        parser.error("--input is required unless --self-test is used")
    snapshots = _self_test_snapshots() if args.self_test else load_snapshots(args.input, max_snapshots=args.max_snapshots)
    runner = ReplayBacktester(use_bot=not args.no_bot, verbose_bot=args.verbose_bot)
    result = runner.run(snapshots)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(_format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
