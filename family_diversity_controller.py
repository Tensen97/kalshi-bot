from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_MAX_FAMILY_SHARE = 0.15
DEFAULT_MIN_DISTINCT_FAMILIES = 8
DEFAULT_MIN_DISTINCT_CATEGORIES = 3


def normalize_family(value: Any) -> str:
    family = str(value or "").strip().upper()
    return family or "UNKNOWN"


def normalize_category(value: Any) -> str:
    category = str(value or "").strip().lower()
    return category or "unknown"


def _market_category_label(market: Dict[str, Any]) -> str:
    market = market if isinstance(market, dict) else {}
    family = normalize_family(market.get("family") or market.get("category") or market.get("series_ticker") or market.get("event_ticker") or market.get("ticker"))
    explicit_category = normalize_category(market.get("category") or market.get("market_category") or market.get("event_category") or market.get("market_type"))
    haystack = " ".join(
        str(market.get(key, "") or "")
        for key in ("ticker", "event_ticker", "series_ticker", "title", "subtitle", "category", "market_category", "event_category")
    ).upper()
    if explicit_category not in {"", "unknown"}:
        if "spread" in explicit_category:
            if family.startswith("KXNBA"):
                return "nba_spreads"
            if family.startswith("KXMLB"):
                return "mlb_spreads"
            return f"{explicit_category}_spreads"
        if "total" in explicit_category:
            if family.startswith("KXNBA"):
                return "nba_totals"
            if family.startswith("KXMLB"):
                return "mlb_totals"
            return f"{explicit_category}_totals"
        return explicit_category
    if family.startswith(("KXBTC", "KXETH", "KXXRP", "BTC", "ETH", "XRP")) or any(token in haystack for token in ("BTC", "BITCOIN", "CRYPTO", "ETH", "ETHER", "XRP", "RIPPLE")):
        return "crypto"
    if family.startswith(("KXTEMP", "KXRAIN", "KXSNOW", "KXWEATHER")) or any(token in haystack for token in ("TEMP", "TEMPERATURE", "RAIN", "SNOW", "WEATHER")):
        if "RAIN" in haystack or family.startswith("KXRAIN"):
            return "weather_rain"
        if "SNOW" in haystack or family.startswith("KXSNOW"):
            return "weather_snow"
        if "TEMP" in haystack or "TEMPERATURE" in haystack or family.startswith("KXTEMP"):
            return "weather_temp"
        return "weather"
    if family.startswith("KXNBA"):
        if "SPREAD" in family or "SPREAD" in haystack:
            return "nba_spreads"
        if "TOTAL" in family or "TOTAL" in haystack:
            return "nba_totals"
        if "1H" in family or "HALF" in haystack:
            return "nba_first_half"
        return "nba"
    if family.startswith("KXMLB"):
        if "SPREAD" in family or "SPREAD" in haystack:
            return "mlb_spreads"
        if "TOTAL" in family or "TOTAL" in haystack:
            return "mlb_totals"
        return "mlb"
    if family.startswith("KXNFL"):
        if "SPREAD" in family or "SPREAD" in haystack:
            return "nfl_spreads"
        if "TOTAL" in family or "TOTAL" in haystack:
            return "nfl_totals"
        return "nfl"
    if family.startswith("KXNHL"):
        return "nhl"
    if family.startswith("KX") and ("SPREAD" in family or "SPREAD" in haystack):
        return "sports_spreads"
    if family.startswith("KX") and ("TOTAL" in family or "TOTAL" in haystack):
        return "sports_totals"
    if "macro" in explicit_category or any(token in haystack for token in ("CPI", "GDP", "FED", "RATE", "INFLATION", "MACRO", "ECONOMIC")):
        return "macro"
    if "halftime" in explicit_category or "first_half" in explicit_category or "first half" in haystack.lower():
        return "halftime"
    return explicit_category if explicit_category not in {"", "unknown"} else "general"


def discover_enabled_families(markets: Iterable[Dict[str, Any]]) -> List[str]:
    families = sorted(
        {
            normalize_family(market.get("family") or market.get("category") or market.get("series_ticker") or market.get("event_ticker"))
            for market in markets
            if isinstance(market, dict)
        }
    )
    return [family for family in families if family and family != "UNKNOWN"]


def discover_enabled_categories(markets: Iterable[Dict[str, Any]]) -> List[str]:
    categories = sorted(
        {
            _market_category_label(market)
            for market in markets
            if isinstance(market, dict)
        }
    )
    return [category for category in categories if category and category != "unknown"]


def build_family_quota(total_candidates: int, family_count: int, max_share: float = DEFAULT_MAX_FAMILY_SHARE) -> int:
    total_candidates = max(1, int(total_candidates or 1))
    family_count = max(1, int(family_count or 1))
    max_share = max(0.01, min(float(max_share or DEFAULT_MAX_FAMILY_SHARE), 1.0))
    return max(1, int(math.floor(total_candidates * max_share)))


def score_family_rotation(
    *,
    family: str,
    seen_count: int,
    pool_count: int,
    family_value: float = 0.0,
    family_confidence: float = 0.0,
    family_liquidity: float = 0.0,
) -> float:
    family = normalize_family(family)
    under_tested_boost = 1.0 / max(1.0, float(seen_count) + 1.0)
    coverage_boost = 1.0 / max(1.0, float(pool_count) + 1.0)
    quality_boost = max(0.0, min(1.0, 0.5 + family_value * 0.2 + family_confidence * 0.2 + family_liquidity * 0.2))
    return round((under_tested_boost + coverage_boost + quality_boost) / 3.0, 6)


def select_diverse_market_batch(
    markets: Sequence[Dict[str, Any]],
    *,
    sample_size: int,
    family_seen_counts: Optional[Dict[str, int]] = None,
    category_seen_counts: Optional[Dict[str, int]] = None,
    max_family_share: float = DEFAULT_MAX_FAMILY_SHARE,
    min_distinct_families: int = DEFAULT_MIN_DISTINCT_FAMILIES,
    min_distinct_categories: int = DEFAULT_MIN_DISTINCT_CATEGORIES,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    family_seen_counts = dict(family_seen_counts or {})
    category_seen_counts = dict(category_seen_counts or {})
    category_family_buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for market in markets:
        if not isinstance(market, dict):
            continue
        family = normalize_family(market.get("family") or market.get("category") or market.get("series_ticker") or market.get("event_ticker"))
        if family == "UNKNOWN":
            continue
        category = _market_category_label(market)
        category_family_buckets[category][family].append(market)
    family_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for category, bucket_map in category_family_buckets.items():
        for family, bucket in bucket_map.items():
            family_buckets[family].extend(bucket)
    category_order = sorted(
        category_family_buckets.keys(),
        key=lambda cat: (
            -score_family_rotation(
                family=cat,
                seen_count=category_seen_counts.get(cat, 0),
                pool_count=sum(len(bucket) for bucket in category_family_buckets.get(cat, {}).values()),
                family_value=0.0,
                family_confidence=0.0,
                family_liquidity=0.0,
            ),
            category_seen_counts.get(cat, 0),
            -sum(len(bucket) for bucket in category_family_buckets.get(cat, {}).values()),
            cat,
        ),
    )
    family_order = sorted(
        family_buckets.keys(),
        key=lambda fam: (
            -score_family_rotation(
                family=fam,
                seen_count=family_seen_counts.get(fam, 0),
                pool_count=len(family_buckets.get(fam, [])),
                family_value=0.0,
                family_confidence=0.0,
                family_liquidity=0.0,
            ),
            family_seen_counts.get(fam, 0),
            -len(family_buckets.get(fam, [])),
            fam,
        ),
    )
    if not family_order:
        return [], {
            "families_seen": [],
            "family_order": [],
            "categories_seen": [],
            "category_order": [],
            "quota_per_family": 0,
            "suppressed_families": [],
            "suppressed_categories": [],
            "rotated_families": [],
            "rotated_categories": [],
            "selected_family_counts": {},
            "selected_category_counts": {},
            "sample_size": int(sample_size or 0),
            "min_distinct_families": int(min_distinct_families or DEFAULT_MIN_DISTINCT_FAMILIES),
            "min_distinct_categories": int(min_distinct_categories or DEFAULT_MIN_DISTINCT_CATEGORIES),
        }
    quota_per_family = build_family_quota(sample_size, len(family_order), max_share=max_family_share)
    selected: List[Dict[str, Any]] = []
    selected_family_counts: Dict[str, int] = defaultdict(int)
    selected_category_counts: Dict[str, int] = defaultdict(int)
    suppressed_families: List[str] = []
    suppressed_categories: List[str] = []
    rotated_families: List[str] = []
    rotated_categories: List[str] = []
    shuffled_buckets = {
        category: {family: list(bucket) for family, bucket in bucket_map.items()}
        for category, bucket_map in category_family_buckets.items()
    }
    for category, family_map in shuffled_buckets.items():
        for family in family_map:
            random.shuffle(family_map[family])
    category_family_order = {
        category: sorted(
            family_map.keys(),
            key=lambda fam: (
                -score_family_rotation(
                    family=fam,
                    seen_count=family_seen_counts.get(fam, 0),
                    pool_count=len(family_buckets.get(fam, [])),
                    family_value=0.0,
                    family_confidence=0.0,
                    family_liquidity=0.0,
                ),
                family_seen_counts.get(fam, 0),
                -len(family_buckets.get(fam, [])),
                fam,
            ),
        )
        for category, family_map in shuffled_buckets.items()
    }
    active_category_order = category_order[:]
    seed_target = min(int(sample_size or 0), max(int(min_distinct_families or DEFAULT_MIN_DISTINCT_FAMILIES), min(len(category_order) or 1, max(min_distinct_categories, 1))))
    seeded_families = set()
    while len(selected) < int(sample_size or 0):
        made_progress = False
        for category in active_category_order:
            if len(selected) >= int(sample_size or 0):
                break
            if selected_category_counts[category] >= max(1, int(sample_size * 0.5)) and len(active_category_order) > 1:
                suppressed_categories.append(category)
                continue
            for family in category_family_order.get(category, []):
                if len(selected) >= int(sample_size or 0):
                    break
                bucket = shuffled_buckets.get(category, {}).get(family) or []
                if not bucket:
                    continue
                if selected_family_counts[family] >= quota_per_family and len(family_order) > 1:
                    suppressed_families.append(family)
                    continue
                if len(seeded_families) < seed_target and family in seeded_families and len(family_order) > 1:
                    continue
                market = bucket.pop()
                selected.append(market)
                selected_family_counts[family] += 1
                selected_category_counts[category] += 1
                seeded_families.add(family)
                rotated_families.append(family)
                rotated_categories.append(category)
                made_progress = True
                break
        if not made_progress:
            break
    if len(selected) < int(sample_size or 0):
        fallback_pool = [market for category_map in shuffled_buckets.values() for bucket in category_map.values() for market in bucket]
        while fallback_pool and len(selected) < int(sample_size or 0):
            market = random.choice(fallback_pool)
            family = normalize_family(market.get("family") or market.get("category") or market.get("series_ticker") or market.get("event_ticker"))
            category = _market_category_label(market)
            if selected_family_counts.get(family, 0) >= quota_per_family and len(family_order) > 1:
                suppressed_families.append(family)
                fallback_pool.remove(market)
                continue
            if selected_category_counts.get(category, 0) >= max(1, int(sample_size * 0.5)) and len(active_category_order) > 1:
                suppressed_categories.append(category)
                fallback_pool.remove(market)
                continue
            selected.append(market)
            selected_family_counts[family] += 1
            selected_category_counts[category] += 1
            rotated_families.append(family)
            rotated_categories.append(category)
            fallback_pool.remove(market)
    telemetry = {
        "families_seen": family_order,
        "family_order": family_order,
        "categories_seen": category_order,
        "category_order": active_category_order,
        "quota_per_family": quota_per_family,
        "selected_family_counts": dict(selected_family_counts),
        "selected_category_counts": dict(selected_category_counts),
        "suppressed_families": sorted(set(suppressed_families)),
        "suppressed_categories": sorted(set(suppressed_categories)),
        "rotated_families": rotated_families[: min(20, len(rotated_families))],
        "rotated_categories": rotated_categories[: min(20, len(rotated_categories))],
        "sample_size": int(sample_size or 0),
        "min_distinct_families": int(min_distinct_families or DEFAULT_MIN_DISTINCT_FAMILIES),
        "min_distinct_categories": int(min_distinct_categories or DEFAULT_MIN_DISTINCT_CATEGORIES),
    }
    return selected[: int(sample_size or 0)], telemetry
