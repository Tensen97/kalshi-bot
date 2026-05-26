from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from runtime.paths import runtime_state_file, safe_atomic_json_write
except Exception:  # pragma: no cover - import fallback for standalone scripts
    def runtime_state_file(name):
        return str(Path("/tmp") / str(name))

    def safe_atomic_json_write(path, payload, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=kwargs.get("indent", 2), sort_keys=kwargs.get("sort_keys", True))
        return True


MODEL_VERSION = 1
DEFAULT_MODEL_PATH = runtime_state_file("aion_sports_ml_probability_model.json")
DEFAULT_SCHEMA_PATH = runtime_state_file("aion_sports_ml_probability_schema.json")
DEFAULT_SUMMARY_PATH = runtime_state_file("aion_sports_ml_probability_training_summary.json")
DEFAULT_ARCHIVE_PATH = "data/aion_learning_archive.jsonl"

BANNED_NONSPORTS_TOKENS = {"BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB", "CRYPTO"}
SPORTS_FAMILY_PREFIXES = (
    "KXMLB",
    "KXNBA",
    "KXNFL",
    "KXNHL",
    "KXWNBA",
    "KXATP",
    "KXWTA",
    "KXITF",
    "KXEPL",
    "KXSOC",
    "KXGOLF",
    "KXPGA",
    "KXUFC",
    "KXMMA",
    "KXNCAAB",
    "KXNCAAF",
)

FEATURE_NAMES = [
    "price",
    "spread",
    "spread_to_price",
    "liquidity_log",
    "confidence",
    "ev_score",
    "base_probability",
    "base_edge",
    "sports_implied_probability",
    "sports_memory_rank_adjustment",
    "sports_memory_size_multiplier",
    "sports_regime_rank_adjustment",
    "sports_regime_size_multiplier",
    "sports_execution_fill_probability",
    "sports_execution_quality_score",
    "sports_graph_rank_adjustment",
    "sports_graph_size_multiplier",
    "sports_graph_conflict_count",
    "sports_graph_alignment_count",
    "game_thesis_confidence",
    "game_thesis_rank_boost",
    "game_thesis_size_multiplier",
    "sports_ladder_score",
    "sports_ladder_size_cap",
    "orderbook_imbalance",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "has_full_book",
    "side_yes",
    "side_no",
    "family_hash",
    "sport_hash",
    "entry_mode_hash",
    "thesis_hash",
    "regime_hash",
    "ladder_role_hash",
]

_MODEL_CACHE = {"path": None, "mtime": None, "model": None}
_LOG_STATE = {}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric):
            return default
        return numeric
    except Exception:
        return default


def _normal_price(value):
    numeric = _safe_float(value, None)
    if numeric is None:
        return None
    if 1.0 < numeric <= 100.0:
        numeric /= 100.0
    if 0.0 < numeric < 1.0:
        return numeric
    return None


def _clip(value, low, high):
    numeric = _safe_float(value, low)
    return max(float(low), min(float(high), float(numeric)))


def _sigmoid(value):
    if value >= 35.0:
        return 1.0
    if value <= -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _hash_feature(value):
    text = str(value or "").strip().lower()
    if not text:
        return 0.0
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / float(0xFFFFFFFF)


def _record_get(record, *names):
    if isinstance(record, dict):
        for name in names:
            if name in record and record.get(name) is not None:
                return record.get(name)
    for name in names:
        try:
            value = getattr(record, name, None)
        except Exception:
            value = None
        if value is not None:
            return value
    return None


def _ranked_payload(item):
    if isinstance(item, dict):
        return item, item.get("q") or item.get("candidate") or item.get("market") or item
    if isinstance(item, (list, tuple)) and item:
        q = item[1] if len(item) > 1 else item[0]
        payload = {}
        if len(item) > 0:
            payload["score"] = item[0]
        if len(item) > 2:
            payload["spread"] = item[2]
        if len(item) > 3:
            payload["family"] = item[3]
        return payload, q
    return {}, item


def _extract_value(item, q, market, *names):
    for source in (item, q, market):
        value = _record_get(source, *names)
        if value is not None:
            return value
    return None


def is_sports_record(record):
    family = str(_record_get(record, "family", "series_ticker") or "").strip().upper()
    ticker = str(_record_get(record, "ticker", "market_ticker") or "").strip().upper()
    category = str(_record_get(record, "category", "market_category") or "").strip().lower()
    joined = f"{family} {ticker}"
    if any(token in joined for token in BANNED_NONSPORTS_TOKENS):
        return False
    if category == "sports":
        return True
    return bool(family.startswith(SPORTS_FAMILY_PREFIXES))


def infer_selected_price(record, market=None):
    side = str(_record_get(record, "selected_side", "side", "outcome_side") or "").strip().lower()
    explicit = _normal_price(_record_get(record, "selected_price", "selected_price_dollars", "price", "entry_price"))
    if explicit is not None:
        return explicit
    if side == "no":
        return _normal_price(_record_get(record, "no_ask", "no_price") or _record_get(market, "no_ask", "no_price"))
    return _normal_price(_record_get(record, "yes_ask", "yes_price") or _record_get(market, "yes_ask", "yes_price"))


def infer_base_probability(record, *, price=None, base_probability=None, base_edge=None):
    explicit = _normal_price(base_probability)
    if explicit is not None:
        return explicit
    for name in (
        "estimated_win_probability",
        "calibrated_probability",
        "final_probability",
        "probability_estimate",
        "model_probability",
    ):
        value = _normal_price(_record_get(record, name))
        if value is not None:
            return value
    price = _normal_price(price)
    edge = _safe_float(base_edge, None)
    if edge is None:
        edge = _safe_float(_record_get(record, "edge_after_cost", "sports_edge_after_cost", "ev_score", "final_edge"), None)
    if price is not None and edge is not None:
        return _clip(price + edge, 0.01, 0.99)
    confidence = _safe_float(_record_get(record, "confidence", "confidence_score", "candidate_score"), None)
    if confidence is not None:
        if confidence > 1.0:
            confidence /= 100.0
        if 0.0 < confidence < 1.0:
            return _clip(confidence, 0.01, 0.99)
    implied = _normal_price(_record_get(record, "sports_implied_probability"))
    if implied is not None:
        return implied
    return price if price is not None else 0.50


def build_sports_ml_features(candidate=None, market=None, *, family=None, base_probability=None, base_edge=None):
    item, q = _ranked_payload(candidate)
    merged = {}
    for source in (market if isinstance(market, dict) else {}, item if isinstance(item, dict) else {}):
        merged.update(source)
    for name in (
        "ticker",
        "market_ticker",
        "family",
        "category",
        "selected_side",
        "side",
        "selected_price",
        "selected_price_dollars",
        "price",
        "spread",
        "liquidity",
        "liquidity_dollars",
        "confidence",
        "ev_score",
        "sports_implied_probability",
        "sports_memory_rank_adjustment",
        "sports_memory_size_multiplier",
        "sports_regime_rank_adjustment",
        "sports_regime_size_multiplier",
        "sports_execution_fill_probability",
        "sports_execution_quality_score",
        "sports_graph_rank_adjustment",
        "sports_graph_size_multiplier",
        "sports_graph_conflict_count",
        "sports_graph_alignment_count",
        "game_thesis_confidence",
        "game_thesis_rank_boost",
        "game_thesis_size_multiplier",
        "sports_ladder_score",
        "sports_ladder_size_cap",
        "orderbook_imbalance",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "entry_mode",
        "sport_type",
        "game_thesis_type",
        "sports_memory_regime",
        "sports_regime",
        "sports_ladder_role",
    ):
        if name not in merged:
            value = _extract_value(item, q, market, name)
            if value is not None:
                merged[name] = value
    if family is not None and not merged.get("family"):
        merged["family"] = family
    price = infer_selected_price(merged, market=market)
    base_prob = infer_base_probability(merged, price=price, base_probability=base_probability, base_edge=base_edge)
    base_edge_value = _safe_float(base_edge, None)
    if base_edge_value is None and price is not None:
        base_edge_value = base_prob - price
    spread = _safe_float(merged.get("spread"), 0.0) or 0.0
    if spread > 1.0 and spread <= 100.0:
        spread /= 100.0
    liquidity = _safe_float(merged.get("liquidity_dollars", merged.get("liquidity")), 0.0) or 0.0
    side = str(merged.get("selected_side") or merged.get("side") or "").strip().lower()
    sport = merged.get("sport_type")
    memory_dimensions = merged.get("sports_memory_dimensions")
    if sport is None and isinstance(memory_dimensions, dict):
        sport = memory_dimensions.get("sport")
    regime = merged.get("sports_regime") or merged.get("sports_memory_regime")
    yes_bid = _normal_price(merged.get("yes_bid"))
    yes_ask = _normal_price(merged.get("yes_ask"))
    no_bid = _normal_price(merged.get("no_bid"))
    no_ask = _normal_price(merged.get("no_ask"))
    has_full_book = 1.0 if yes_bid is not None and yes_ask is not None and no_bid is not None and no_ask is not None else 0.0
    features = {
        "price": price or 0.50,
        "spread": max(0.0, spread),
        "spread_to_price": max(0.0, spread) / max(price or 0.50, 0.01),
        "liquidity_log": math.log1p(max(0.0, liquidity)),
        "confidence": _normal_feature(merged.get("confidence")),
        "ev_score": _safe_float(merged.get("ev_score"), _safe_float(merged.get("edge_after_cost"), 0.0)) or 0.0,
        "base_probability": base_prob,
        "base_edge": base_edge_value or 0.0,
        "sports_implied_probability": _normal_price(merged.get("sports_implied_probability")) or 0.0,
        "sports_memory_rank_adjustment": _safe_float(merged.get("sports_memory_rank_adjustment"), 0.0) or 0.0,
        "sports_memory_size_multiplier": _safe_float(merged.get("sports_memory_size_multiplier"), 1.0) or 1.0,
        "sports_regime_rank_adjustment": _safe_float(merged.get("sports_regime_rank_adjustment"), 0.0) or 0.0,
        "sports_regime_size_multiplier": _safe_float(merged.get("sports_regime_size_multiplier"), 1.0) or 1.0,
        "sports_execution_fill_probability": _normal_feature(merged.get("sports_execution_fill_probability")),
        "sports_execution_quality_score": _normal_feature(merged.get("sports_execution_quality_score")),
        "sports_graph_rank_adjustment": _safe_float(merged.get("sports_graph_rank_adjustment"), 0.0) or 0.0,
        "sports_graph_size_multiplier": _safe_float(merged.get("sports_graph_size_multiplier"), 1.0) or 1.0,
        "sports_graph_conflict_count": _safe_float(merged.get("sports_graph_conflict_count"), 0.0) or 0.0,
        "sports_graph_alignment_count": _safe_float(merged.get("sports_graph_alignment_count"), 0.0) or 0.0,
        "game_thesis_confidence": _normal_feature(merged.get("game_thesis_confidence")),
        "game_thesis_rank_boost": _safe_float(merged.get("game_thesis_rank_boost"), 0.0) or 0.0,
        "game_thesis_size_multiplier": _safe_float(merged.get("game_thesis_size_multiplier"), 1.0) or 1.0,
        "sports_ladder_score": _normal_feature(merged.get("sports_ladder_score")),
        "sports_ladder_size_cap": _safe_float(merged.get("sports_ladder_size_cap"), 0.0) or 0.0,
        "orderbook_imbalance": _safe_float(merged.get("orderbook_imbalance"), 0.0) or 0.0,
        "yes_bid": yes_bid or 0.0,
        "yes_ask": yes_ask or 0.0,
        "no_bid": no_bid or 0.0,
        "no_ask": no_ask or 0.0,
        "has_full_book": has_full_book,
        "side_yes": 1.0 if side == "yes" else 0.0,
        "side_no": 1.0 if side == "no" else 0.0,
        "family_hash": _hash_feature(merged.get("family")),
        "sport_hash": _hash_feature(sport),
        "entry_mode_hash": _hash_feature(merged.get("entry_mode")),
        "thesis_hash": _hash_feature(merged.get("game_thesis_type")),
        "regime_hash": _hash_feature(regime),
        "ladder_role_hash": _hash_feature(merged.get("sports_ladder_role")),
    }
    return {
        "ticker": str(merged.get("ticker") or merged.get("market_ticker") or "").strip(),
        "family": str(merged.get("family") or family or "").strip().upper(),
        "selected_side": side or None,
        "selected_price": price,
        "base_probability": base_prob,
        "base_edge": base_edge_value,
        "features": {name: float(features.get(name, 0.0) or 0.0) for name in FEATURE_NAMES},
    }


def _normal_feature(value):
    numeric = _safe_float(value, 0.0) or 0.0
    if abs(numeric) > 1.0:
        numeric /= 100.0
    return _clip(numeric, -1.0, 1.0)


def vectorize_features(feature_payload):
    features = feature_payload.get("features") if isinstance(feature_payload, dict) else {}
    return [float(features.get(name, 0.0) or 0.0) for name in FEATURE_NAMES]


def _load_json_model(path=DEFAULT_MODEL_PATH):
    model_path = str(path or DEFAULT_MODEL_PATH)
    try:
        stat = os.stat(model_path)
    except Exception:
        _MODEL_CACHE.update({"path": model_path, "mtime": None, "model": None})
        return None
    if _MODEL_CACHE.get("path") == model_path and _MODEL_CACHE.get("mtime") == stat.st_mtime:
        return _MODEL_CACHE.get("model")
    try:
        with open(model_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("model_type") != "aion_sports_logistic_probability":
            payload = None
    except Exception:
        payload = None
    _MODEL_CACHE.update({"path": model_path, "mtime": stat.st_mtime, "model": payload})
    return payload


def _predict_probability(model, feature_payload):
    values = vectorize_features(feature_payload)
    means = model.get("feature_means") or [0.0] * len(FEATURE_NAMES)
    scales = model.get("feature_scales") or [1.0] * len(FEATURE_NAMES)
    weights = model.get("weights") or [0.0] * len(FEATURE_NAMES)
    intercept = _safe_float(model.get("intercept"), 0.0) or 0.0
    total = intercept
    for index, value in enumerate(values):
        mean = _safe_float(means[index] if index < len(means) else 0.0, 0.0) or 0.0
        scale = _safe_float(scales[index] if index < len(scales) else 1.0, 1.0) or 1.0
        if abs(scale) < 1e-9:
            scale = 1.0
        weight = _safe_float(weights[index] if index < len(weights) else 0.0, 0.0) or 0.0
        total += weight * ((value - mean) / scale)
    return _clip(_sigmoid(total), 0.01, 0.99)


def get_sports_ml_probability_adjustment(
    candidate=None,
    *,
    market=None,
    family=None,
    base_probability=None,
    base_edge=None,
    source="ranking",
    model_path=DEFAULT_MODEL_PATH,
    log=False,
):
    feature_payload = build_sports_ml_features(
        candidate,
        market=market,
        family=family,
        base_probability=base_probability,
        base_edge=base_edge,
    )
    if not is_sports_record(feature_payload):
        return _neutral_context(feature_payload, reason="not_sports")
    model = _load_json_model(model_path)
    base_prob = _clip(feature_payload.get("base_probability"), 0.01, 0.99)
    selected_price = feature_payload.get("selected_price")
    if model is None:
        context = _neutral_context(feature_payload, reason="model_missing")
        _maybe_log_context(context, source=source, enabled=bool(log and os.environ.get("AION_SPORTS_ML_LOG_FALLBACK")))
        return context
    ml_prob = _predict_probability(model, feature_payload)
    examples = int(_safe_float((model.get("training_summary") or {}).get("examples"), 0) or 0)
    blend_weight = _clip((model.get("training_summary") or {}).get("blend_weight", min(0.35, max(0.10, examples / 800.0))), 0.10, 0.35)
    final_prob = _clip((base_prob * (1.0 - blend_weight)) + (ml_prob * blend_weight), 0.01, 0.99)
    delta = final_prob - base_prob
    edge_estimate = None
    if selected_price is not None:
        edge_estimate = final_prob - selected_price
    rank_adjustment = _clip(delta * 35.0, -4.0, 4.0)
    size_multiplier = _clip(1.0 + (delta * 1.4), 0.75, 1.15)
    if edge_estimate is not None and edge_estimate < -0.01:
        size_multiplier = min(size_multiplier, 0.88)
    action = "boost" if rank_adjustment > 0.05 else "demote" if rank_adjustment < -0.05 else "neutral"
    context = {
        "allowed": True,
        "hard_reject": False,
        "fallback_used": False,
        "reason": f"sports_ml_{action}",
        "action": action,
        "ticker": feature_payload.get("ticker"),
        "family": feature_payload.get("family"),
        "selected_side": feature_payload.get("selected_side"),
        "selected_price": _round_or_none(selected_price, 4),
        "base_probability": round(base_prob, 4),
        "ml_probability": round(ml_prob, 4),
        "final_probability": round(final_prob, 4),
        "edge_estimate": _round_or_none(edge_estimate, 5),
        "base_edge": _round_or_none(feature_payload.get("base_edge"), 5),
        "rank_adjustment": round(rank_adjustment, 4),
        "size_multiplier": round(size_multiplier, 4),
        "model_version": model.get("version"),
        "model_examples": examples,
        "source": source,
    }
    _maybe_log_context(context, source=source, enabled=bool(log))
    return context


def _neutral_context(feature_payload, *, reason):
    base_prob = _clip(feature_payload.get("base_probability"), 0.01, 0.99)
    selected_price = feature_payload.get("selected_price")
    edge_estimate = base_prob - selected_price if selected_price is not None else feature_payload.get("base_edge")
    return {
        "allowed": True,
        "hard_reject": False,
        "fallback_used": True,
        "reason": reason,
        "action": "fallback",
        "ticker": feature_payload.get("ticker"),
        "family": feature_payload.get("family"),
        "selected_side": feature_payload.get("selected_side"),
        "selected_price": _round_or_none(selected_price, 4),
        "base_probability": round(base_prob, 4),
        "ml_probability": None,
        "final_probability": round(base_prob, 4),
        "edge_estimate": _round_or_none(edge_estimate, 5),
        "base_edge": _round_or_none(feature_payload.get("base_edge"), 5),
        "rank_adjustment": 0.0,
        "size_multiplier": 1.0,
    }


def _round_or_none(value, digits):
    numeric = _safe_float(value, None)
    if numeric is None:
        return None
    return round(numeric, digits)


def _maybe_log_context(context, *, source, enabled):
    if not enabled:
        return
    key = (
        context.get("ticker") or "unknown",
        context.get("action") or "unknown",
        context.get("reason") or "unknown",
        int(time.time() // 180),
    )
    if _LOG_STATE.get(key):
        return
    _LOG_STATE[key] = True
    builtins.print(
        "[SPORTS-ML] "
        + json.dumps(
            {
                "ticker": context.get("ticker"),
                "family": context.get("family"),
                "selected_side": context.get("selected_side"),
                "selected_price": context.get("selected_price"),
                "base_probability": context.get("base_probability"),
                "ml_probability": context.get("ml_probability"),
                "final_probability": context.get("final_probability"),
                "edge_estimate": context.get("edge_estimate"),
                "rank_adjustment": context.get("rank_adjustment"),
                "size_multiplier": context.get("size_multiplier"),
                "fallback_used": context.get("fallback_used"),
                "reason": context.get("reason"),
                "source": source,
            },
            sort_keys=True,
            default=str,
        )
    )


def iter_jsonl_records(paths):
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for child in sorted(path.glob("*.jsonl")):
                yield from iter_jsonl_records([child])
            continue
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, dict):
                    yield record


def _training_label(record):
    if str(record.get("event_type") or "").strip().lower() != "trade_closed":
        return None
    if not is_sports_record(record):
        return None
    pnl = _safe_float(record.get("pnl_dollars"), None)
    won = record.get("won")
    result = str(record.get("result") or record.get("outcome") or "").strip().lower()
    contracts = _safe_float(record.get("contracts", record.get("position_size")), 0.0) or 0.0
    exit_reason = str(record.get("exit_reason") or "").strip().lower()
    if pnl is not None and abs(pnl) > 1e-9:
        return 1 if pnl > 0 else 0
    if won is True:
        return 1
    if result in {"won", "win", "profit"}:
        return 1
    if result in {"lost", "loss"} or (won is False and contracts > 0 and "reconcile" not in exit_reason):
        return 0
    return None


def build_training_examples(paths, *, max_records=None):
    recent_features = {}
    examples = []
    seen = 0
    for record in iter_jsonl_records(paths):
        seen += 1
        if max_records is not None and seen > max_records:
            break
        if not is_sports_record(record):
            continue
        event_type = str(record.get("event_type") or "").strip().lower()
        keys = _record_keys(record)
        if event_type in {"candidate_seen", "trade_opened"}:
            for key in keys:
                recent_features[key] = record
            continue
        label = _training_label(record)
        if label is None:
            continue
        merged = dict(record)
        for key in keys:
            prior = recent_features.get(key)
            if isinstance(prior, dict):
                tmp = dict(prior)
                tmp.update({name: value for name, value in record.items() if value is not None})
                merged = tmp
                break
        features = build_sports_ml_features(merged)
        examples.append({"label": int(label), "features": features, "ticker": features.get("ticker"), "family": features.get("family")})
    return examples


def _record_keys(record):
    keys = []
    for name in ("position_id", "order_id", "ticker", "market_ticker"):
        value = str(record.get(name) or "").strip()
        if value:
            keys.append(f"{name}:{value}")
    return keys


def train_sports_ml_probability_model(
    paths,
    *,
    model_path=DEFAULT_MODEL_PATH,
    schema_path=DEFAULT_SCHEMA_PATH,
    summary_path=DEFAULT_SUMMARY_PATH,
    min_examples=25,
    max_records=None,
    epochs=260,
    learning_rate=0.08,
):
    examples = build_training_examples(paths, max_records=max_records)
    positives = sum(1 for row in examples if row["label"] == 1)
    negatives = sum(1 for row in examples if row["label"] == 0)
    summary = {
        "ok": False,
        "trained_at_utc": _utc_now(),
        "input_paths": [str(path) for path in paths],
        "examples": len(examples),
        "positives": positives,
        "negatives": negatives,
        "model_path": str(model_path),
        "schema_path": str(schema_path),
        "summary_path": str(summary_path),
        "sklearn_available": _sklearn_available(),
    }
    if len(examples) < int(min_examples):
        summary["reason"] = "not_enough_labeled_sports_examples"
        safe_atomic_json_write(summary_path, summary)
        return summary
    if positives < 2 or negatives < 2:
        summary["reason"] = "need_both_winning_and_losing_sports_examples"
        safe_atomic_json_write(summary_path, summary)
        return summary
    vectors = [vectorize_features(row["features"]) for row in examples]
    labels = [int(row["label"]) for row in examples]
    means, scales = _feature_stats(vectors)
    weights, intercept = _train_logistic(vectors, labels, means, scales, epochs=epochs, learning_rate=learning_rate)
    metrics = _evaluate_logistic(vectors, labels, means, scales, weights, intercept)
    blend_weight = min(0.35, max(0.10, len(examples) / 800.0))
    model = {
        "model_type": "aion_sports_logistic_probability",
        "version": MODEL_VERSION,
        "trained_at_utc": summary["trained_at_utc"],
        "feature_names": FEATURE_NAMES,
        "feature_means": means,
        "feature_scales": scales,
        "weights": weights,
        "intercept": intercept,
        "training_summary": {
            **summary,
            **metrics,
            "ok": True,
            "reason": "trained",
            "blend_weight": round(blend_weight, 4),
        },
    }
    schema = {
        "version": MODEL_VERSION,
        "feature_names": FEATURE_NAMES,
        "target": "selected_side_realized_win_probability",
        "sports_only": True,
        "created_at_utc": summary["trained_at_utc"],
    }
    safe_atomic_json_write(model_path, model)
    safe_atomic_json_write(schema_path, schema)
    summary = dict(model["training_summary"])
    safe_atomic_json_write(summary_path, summary)
    _MODEL_CACHE.update({"path": None, "mtime": None, "model": None})
    return summary


def _feature_stats(vectors):
    count = max(1, len(vectors))
    means = []
    scales = []
    for index in range(len(FEATURE_NAMES)):
        column = [row[index] for row in vectors]
        mean = sum(column) / count
        variance = sum((value - mean) ** 2 for value in column) / count
        means.append(round(mean, 8))
        scales.append(round(math.sqrt(variance) or 1.0, 8))
    return means, scales


def _train_logistic(vectors, labels, means, scales, *, epochs, learning_rate):
    weights = [0.0] * len(FEATURE_NAMES)
    positive_rate = max(0.01, min(0.99, sum(labels) / float(len(labels) or 1)))
    intercept = math.log(positive_rate / (1.0 - positive_rate))
    positives = max(1, sum(labels))
    negatives = max(1, len(labels) - positives)
    l2 = 0.001
    for _epoch in range(int(epochs)):
        for vector, label in zip(vectors, labels):
            z = intercept
            scaled = []
            for index, value in enumerate(vector):
                scale = scales[index] if abs(scales[index]) > 1e-9 else 1.0
                x = (value - means[index]) / scale
                scaled.append(x)
                z += weights[index] * x
            pred = _sigmoid(z)
            sample_weight = len(labels) / (2.0 * (positives if label else negatives))
            error = (pred - label) * sample_weight
            intercept -= learning_rate * error
            for index, x in enumerate(scaled):
                weights[index] -= learning_rate * ((error * x) + (l2 * weights[index]))
    return [round(weight, 10) for weight in weights], round(intercept, 10)


def _evaluate_logistic(vectors, labels, means, scales, weights, intercept):
    predictions = []
    for vector in vectors:
        z = intercept
        for index, value in enumerate(vector):
            scale = scales[index] if abs(scales[index]) > 1e-9 else 1.0
            z += weights[index] * ((value - means[index]) / scale)
        predictions.append(_clip(_sigmoid(z), 0.01, 0.99))
    correct = 0
    brier = 0.0
    logloss = 0.0
    for pred, label in zip(predictions, labels):
        correct += int((pred >= 0.50) == bool(label))
        brier += (pred - label) ** 2
        logloss -= (label * math.log(pred)) + ((1 - label) * math.log(1.0 - pred))
    count = max(1, len(labels))
    return {
        "accuracy": round(correct / count, 4),
        "brier_score": round(brier / count, 6),
        "logloss": round(logloss / count, 6),
    }


def _sklearn_available():
    try:
        import importlib.util

        return importlib.util.find_spec("sklearn") is not None
    except Exception:
        return False


def prediction_sanity(input_path=None, *, model_path=DEFAULT_MODEL_PATH):
    sample = None
    paths = [input_path] if input_path else [DEFAULT_ARCHIVE_PATH]
    for record in iter_jsonl_records(paths):
        if is_sports_record(record) and str(record.get("event_type") or "").strip().lower() in {"candidate_seen", "trade_opened"}:
            sample = record
    if sample is None:
        sample = {
            "ticker": "SAMPLE-SPORTS",
            "family": "KXMLBMATCH",
            "category": "sports",
            "side": "yes",
            "price": 0.48,
            "spread": 0.04,
            "liquidity_dollars": 25.0,
            "ev_score": 0.035,
            "sports_execution_fill_probability": 0.62,
            "sports_execution_quality_score": 72.0,
        }
    return get_sports_ml_probability_adjustment(sample, model_path=model_path, log=True, source="sports_ml_sanity")


def cli_train(argv=None):
    parser = argparse.ArgumentParser(description="Train AION sports-only ML probability model from learning archive JSONL.")
    parser.add_argument("--input", nargs="+", default=[DEFAULT_ARCHIVE_PATH])
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--summary", default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--min-examples", type=int, default=25)
    parser.add_argument("--max-records", type=int, default=None)
    args = parser.parse_args(argv)
    summary = train_sports_ml_probability_model(
        args.input,
        model_path=args.model,
        schema_path=args.schema,
        summary_path=args.summary,
        min_examples=args.min_examples,
        max_records=args.max_records,
    )
    builtins.print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary.get("ok") or summary.get("reason") in {"not_enough_labeled_sports_examples", "need_both_winning_and_losing_sports_examples"} else 1


def cli_sanity(argv=None):
    parser = argparse.ArgumentParser(description="Run AION sports ML probability prediction sanity check.")
    parser.add_argument("--input", default=DEFAULT_ARCHIVE_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    args = parser.parse_args(argv)
    result = prediction_sanity(args.input, model_path=args.model)
    builtins.print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0
