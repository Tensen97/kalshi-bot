"""Optimized market discovery for the Kalshi sniper bot."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import KalshiHttpClient
from .utils import TTLCache, env_bool, env_float, env_int, should_emit_service_log, stable_json_key, utc_now_iso


@dataclass
class MarketDiscoveryConfig:
    base_url: str
    live_batch_size: int = 200
    max_scan_batches: int = 10
    live_select_max_markets: int = 1000
    min_markets_in_window_target: int = 40
    min_minutes_to_event: float = 5.0
    max_minutes_to_event: float = 480.0
    targeted_series: list[str] = field(default_factory=list)
    optional_targeted_series: list[str] = field(default_factory=list)
    targeted_exclude_keywords: tuple[str, ...] = ()
    base_max_targeted_contracts_per_family: int = 8
    targeted_contract_distance_buffer_minutes: float = 30.0
    market_cache_ttl_seconds: float = 4.0
    targeted_cache_ttl_seconds: float = 12.0
    broad_cache_ttl_seconds: float = 8.0
    max_pages_per_series: int = 3
    verbose: bool = False

    @classmethod
    def from_legacy_globals(cls, legacy: dict[str, Any]) -> "MarketDiscoveryConfig":
        return cls(
            base_url=str(legacy.get("BASE_URL") or "").rstrip("/"),
            live_batch_size=int(legacy.get("LIVE_SELECT_BATCH_SIZE") or 200),
            max_scan_batches=int(legacy.get("MAX_SCAN_BATCHES") or 10),
            live_select_max_markets=int(legacy.get("LIVE_SELECT_MAX_MARKETS") or 1000),
            min_markets_in_window_target=int(legacy.get("MIN_MARKETS_IN_WINDOW_TARGET") or 40),
            min_minutes_to_event=float(legacy.get("MIN_MINUTES_TO_EVENT") or 5.0),
            max_minutes_to_event=float(legacy.get("MAX_MINUTES_TO_EVENT") or 480.0),
            targeted_series=list(legacy.get("TARGETED_SERIES_TICKERS") or []),
            optional_targeted_series=list(legacy.get("OPTIONAL_TARGETED_SERIES_TICKERS") or []),
            targeted_exclude_keywords=tuple(legacy.get("TARGETED_SERIES_EXCLUDE_KEYWORDS") or ()),
            base_max_targeted_contracts_per_family=int(legacy.get("BASE_MAX_TARGETED_CONTRACTS_PER_FAMILY") or 8),
            targeted_contract_distance_buffer_minutes=float(legacy.get("TARGETED_CONTRACT_DISTANCE_BUFFER_MINUTES") or 30.0),
            market_cache_ttl_seconds=env_float("KALSHI_MARKET_CACHE_TTL_SECONDS", 4.0),
            targeted_cache_ttl_seconds=env_float("KALSHI_TARGETED_DISCOVERY_CACHE_TTL_SECONDS", 12.0),
            broad_cache_ttl_seconds=env_float("KALSHI_BROAD_DISCOVERY_CACHE_TTL_SECONDS", 8.0),
            max_pages_per_series=env_int("KALSHI_TARGETED_MAX_PAGES_PER_SERIES", 3),
            verbose=env_bool("KALSHI_DISCOVERY_VERBOSE", False),
        )


class MarketDiscoveryService:
    """Cached, cursor-aware discovery service.

    The service deliberately accepts ``bot`` as a duck-typed object.  That lets
    the legacy bot keep its existing family/time-window logic while the slow API
    access moves into a focused, testable module.
    """

    def __init__(
        self,
        *,
        bot: Any,
        http_client: KalshiHttpClient,
        config: MarketDiscoveryConfig,
        live_mode_getter: Callable[[], bool] | None = None,
        request_url_builder: Callable[[str], str] | None = None,
        logger=None,
    ):
        self.bot = bot
        self.http_client = http_client
        self.config = config
        self.live_mode_getter = live_mode_getter or (lambda: False)
        self.request_url_builder = request_url_builder
        self.logger = logger
        self.discovery_cache = TTLCache(max_entries=512, default_ttl_seconds=config.broad_cache_ttl_seconds)
        self.last_summary: dict[str, Any] = {}

    def _log(self, prefix: str, **fields: Any) -> None:
        if not should_emit_service_log(prefix):
            return
        if callable(self.logger):
            self.logger(prefix, **fields)

    def _markets_url(self) -> str:
        if bool(self.live_mode_getter()) and callable(self.request_url_builder):
            return self.request_url_builder("/trade-api/v2/markets")
        return f"{self.config.base_url}/markets"

    def _cache_key(self, name: str, payload: Any) -> str:
        return f"{name}:{stable_json_key(payload)}"

    def invalidate(self) -> int:
        return self.discovery_cache.invalidate()

    def get_markets(
        self,
        *,
        limit: int = 25,
        status: str = "open",
        cursor: str | None = None,
        return_cursor: bool = False,
        extra_params: dict[str, Any] | None = None,
        cache_ttl: float | None = None,
        bypass_cache: bool = False,
    ):
        params: dict[str, Any] = {"limit": int(limit or 25), "status": status}
        if cursor:
            params["cursor"] = cursor
        if extra_params:
            params.update(extra_params)
        if bool(self.live_mode_getter()):
            params.setdefault("mve_filter", "exclude")
        ttl = self.config.market_cache_ttl_seconds if cache_ttl is None else float(cache_ttl or 0.0)
        response_data = self.http_client.get_json(
            self._markets_url(),
            params=params,
            timeout=15,
            cache_ttl=ttl,
            bypass_cache=bypass_cache,
        )
        markets = response_data.get("markets", []) if isinstance(response_data, dict) else []
        next_cursor = (response_data.get("cursor") or response_data.get("next_cursor")) if isinstance(response_data, dict) else None
        if return_cursor:
            return markets, next_cursor
        return markets

    def fetch_market_batch(self, cursor: str | None = None):
        params = {"mve_filter": "exclude"}
        if self.config.verbose:
            self._log("[LIVE-SELECT-SCAN]", request_params=params, mve_filter_applied="yes")
        return self.get_markets(
            limit=self.config.live_batch_size,
            status="open",
            cursor=cursor,
            return_cursor=True,
            extra_params=params,
            cache_ttl=self.config.broad_cache_ttl_seconds,
        )

    def _get_targeted_series(self) -> list[str]:
        if hasattr(self.bot, "get_prioritized_targeted_series"):
            try:
                series = self.bot.get_prioritized_targeted_series()
                if series:
                    return list(series)
            except Exception as exc:
                self._log("[DISCOVERY-DEGRADED]", stage="prioritized_targeted_series", reason=exc.__class__.__name__)
        deduped = []
        seen = set()
        for item in list(self.config.targeted_series) + list(self.config.optional_targeted_series):
            item = str(item or "").strip()
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _targeted_family_cap(self, series_ticker: str) -> int:
        if hasattr(self.bot, "_get_targeted_contract_cap_for_family"):
            try:
                return int(self.bot._get_targeted_contract_cap_for_family(series_ticker, utc_hour=time.gmtime().tm_hour))
            except Exception:
                pass
        return max(1, int(self.config.base_max_targeted_contracts_per_family or 8))

    def _broad_max_minutes_to_event(self) -> float:
        if hasattr(self.bot, "get_broad_discovery_max_minutes_to_event"):
            try:
                return float(self.bot.get_broad_discovery_max_minutes_to_event())
            except Exception:
                pass
        return float(self.config.max_minutes_to_event)

    def _market_family(self, market: dict[str, Any]) -> str:
        if hasattr(self.bot, "get_market_family"):
            try:
                family = self.bot.get_market_family(market)
                if family:
                    return str(family)
            except Exception:
                pass
        return str(market.get("series_ticker") or market.get("event_ticker") or market.get("ticker") or "").split("-", 1)[0]

    def _minutes_to_event(self, market: dict[str, Any]) -> float | None:
        if hasattr(self.bot, "get_minutes_to_event"):
            try:
                return self.bot.get_minutes_to_event(market)
            except Exception:
                return None
        return None

    def _is_in_time_window(self, market: dict[str, Any], max_minutes_to_event: float | None = None) -> bool:
        minutes = self._minutes_to_event(market)
        if minutes is None:
            return False
        max_minutes = self._broad_max_minutes_to_event() if max_minutes_to_event is None else max_minutes_to_event
        return self.config.min_minutes_to_event <= minutes <= max_minutes

    def _is_excluded_targeted_market(self, market: dict[str, Any]) -> bool:
        if hasattr(self.bot, "is_excluded_targeted_market"):
            try:
                return bool(self.bot.is_excluded_targeted_market(market))
            except Exception:
                pass
        haystack = " ".join(
            str(market.get(key, "") or "")
            for key in ("ticker", "event_ticker", "series_ticker", "title", "subtitle")
        ).upper()
        return any(keyword in haystack for keyword in self.config.targeted_exclude_keywords)

    def _rank_targeted_market(self, market: dict[str, Any], max_minutes_to_event: float) -> dict[str, Any]:
        if hasattr(self.bot, "get_targeted_contract_window_rank"):
            try:
                return self.bot.get_targeted_contract_window_rank(market, max_minutes_to_event=max_minutes_to_event)
            except Exception:
                pass
        minutes = self._minutes_to_event(market)
        if minutes is None:
            return {"ticker": market.get("ticker"), "minutes_to_event": None, "distance": float("inf"), "status": "no_timestamp"}
        if minutes < self.config.min_minutes_to_event:
            return {
                "ticker": market.get("ticker"),
                "minutes_to_event": minutes,
                "distance": self.config.min_minutes_to_event - minutes,
                "status": "too_close",
            }
        if minutes > max_minutes_to_event:
            return {
                "ticker": market.get("ticker"),
                "minutes_to_event": minutes,
                "distance": minutes - max_minutes_to_event,
                "status": "too_early",
            }
        return {"ticker": market.get("ticker"), "minutes_to_event": minutes, "distance": max(0.0, minutes - self.config.min_minutes_to_event), "status": "target_window"}

    def _presolve_score(self, market: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self.bot, "compute_entry_presolve_score"):
            try:
                value = self.bot.compute_entry_presolve_score(market)
                if isinstance(value, dict):
                    return value
            except Exception:
                pass
        yes_bid = market.get("yes_bid_dollars", market.get("yes_bid"))
        yes_ask = market.get("yes_ask_dollars", market.get("yes_ask"))
        has_top_book = bool(yes_bid not in (None, "", 0, 0.0) and yes_ask not in (None, "", 0, 0.0))
        return {"has_top_book": has_top_book, "score": 1.0 if has_top_book else 0.0}

    def _fetch_series_pages(self, series_ticker: str, effective_limit: int) -> list[dict[str, Any]]:
        cache_key = self._cache_key("targeted_series", {"series": series_ticker, "limit": effective_limit})
        cached = self.discovery_cache.get(cache_key)
        if cached is not None:
            self._log("[DISCOVERY-CACHE-HIT]", scope="targeted_series", family=series_ticker, count=len(cached), timestamp=utc_now_iso())
            return list(cached)

        markets: list[dict[str, Any]] = []
        seen = set()
        cursor = None
        page_limit = min(self.config.live_batch_size, max(1, effective_limit))
        max_pages = max(1, min(self.config.max_pages_per_series, int(math.ceil(max(1, effective_limit) / max(page_limit, 1))) + 1))
        for page in range(1, max_pages + 1):
            batch, next_cursor = self.get_markets(
                limit=page_limit,
                status="open",
                cursor=cursor,
                return_cursor=True,
                extra_params={"series_ticker": series_ticker, "mve_filter": "exclude"},
                cache_ttl=self.config.targeted_cache_ttl_seconds,
            )
            for market in batch or []:
                if not isinstance(market, dict):
                    continue
                ticker = str(market.get("ticker") or "").strip()
                if ticker and ticker in seen:
                    continue
                if ticker:
                    seen.add(ticker)
                markets.append(market)
                if len(markets) >= effective_limit:
                    break
            if len(markets) >= effective_limit or not next_cursor:
                break
            cursor = next_cursor
            if page >= max_pages:
                break
        self.discovery_cache.set(cache_key, list(markets), ttl_seconds=self.config.targeted_cache_ttl_seconds)
        return markets

    def fetch_targeted_series_markets(self, scan_depth: int = 1) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        seen_tickers = set()
        total_found = 0
        targeted_series = self._get_targeted_series()
        scan_depth = max(1, int(scan_depth or 1))
        targeted_max_minutes = self._broad_max_minutes_to_event()
        effective_limit = min(
            self.config.live_batch_size + max(0, scan_depth - 1) * 25,
            self.config.live_batch_size * max(1, self.config.max_pages_per_series),
        )
        self._log(
            "[DISCOVERY-TARGETED-START]",
            targeted_series=targeted_series,
            scan_depth=scan_depth,
            effective_limit=effective_limit,
            timestamp=utc_now_iso(),
        )
        selected_by_family: dict[str, int] = {}
        rejected_by_family: dict[str, int] = {}
        for series_ticker in targeted_series:
            series_markets = self._fetch_series_pages(series_ticker, effective_limit)
            total_found += len(series_markets)
            family_candidates = []
            for market in series_markets:
                if self._is_excluded_targeted_market(market):
                    rejected_by_family[series_ticker] = rejected_by_family.get(series_ticker, 0) + 1
                    continue
                ticker = str(market.get("ticker") or "").strip()
                if ticker and ticker in seen_tickers:
                    continue
                rank_info = self._rank_targeted_market(market, targeted_max_minutes)
                presolve = self._presolve_score(market)
                family_candidates.append((market, rank_info, presolve))
            family_cap = self._targeted_family_cap(series_ticker)
            family_candidates.sort(
                key=lambda item: (
                    0 if item[1].get("status") == "target_window" else 1,
                    0 if item[2].get("has_top_book") else 1,
                    -float(item[2].get("score") or 0.0),
                    float(item[1].get("distance") if item[1].get("distance") is not None else float("inf")),
                    item[1].get("minutes_to_event") if item[1].get("minutes_to_event") is not None else float("inf"),
                )
            )
            if family_candidates:
                cap_index = min(len(family_candidates), family_cap) - 1
                distance_threshold = float(family_candidates[cap_index][1].get("distance") or 0.0)
                distance_threshold += self.config.targeted_contract_distance_buffer_minutes
            else:
                distance_threshold = float("inf")
            selected = []
            for index, (market, rank_info, _presolve) in enumerate(family_candidates, start=1):
                distance = float(rank_info.get("distance") if rank_info.get("distance") is not None else float("inf"))
                if index <= family_cap or distance <= distance_threshold:
                    ticker = str(market.get("ticker") or "").strip()
                    if ticker and ticker in seen_tickers:
                        continue
                    if ticker:
                        seen_tickers.add(ticker)
                    selected.append(market)
                else:
                    rejected_by_family[series_ticker] = rejected_by_family.get(series_ticker, 0) + 1
            markets.extend(selected)
            selected_by_family[series_ticker] = len(selected)
            self._log(
                "[DISCOVERY-TARGETED-FAMILY]",
                family=series_ticker,
                fetched=len(series_markets),
                selected=len(selected),
                cap=family_cap,
                rejected=rejected_by_family.get(series_ticker, 0),
                timestamp=utc_now_iso(),
            )
        self.last_summary = {
            "mode": "targeted",
            "total_found": total_found,
            "returned": len(markets),
            "selected_by_family": selected_by_family,
            "rejected_by_family": rejected_by_family,
            "cache": self.discovery_cache.summary(),
            "http": self.http_client.summary(),
        }
        self._log("[DISCOVERY-TARGETED-SUMMARY]", total_found=total_found, returned=len(markets), timestamp=utc_now_iso())
        return markets

    def fetch_broad_discovery_markets(self, scan_depth: int = 1) -> list[dict[str, Any]]:
        scan_depth = max(1, int(scan_depth or 1))
        cache_key = self._cache_key("broad", {"scan_depth": scan_depth, "hour": time.gmtime().tm_hour})
        cached = self.discovery_cache.get(cache_key)
        if cached is not None:
            self._log("[DISCOVERY-CACHE-HIT]", scope="broad", count=len(cached), timestamp=utc_now_iso())
            return list(cached)

        markets: list[dict[str, Any]] = []
        seen_tickers = set()
        cursor = None
        total_examined = 0
        in_window_seen = 0
        batches_fetched = 0
        effective_max_batches = min(self.config.max_scan_batches + max(0, scan_depth - 1) * 2, self.config.max_scan_batches + 6)
        effective_market_limit = min(self.config.live_select_max_markets + max(0, scan_depth - 1) * 250, self.config.live_select_max_markets + 750)
        effective_in_window_target = self.config.min_markets_in_window_target + max(0, scan_depth - 1) * 12
        for batch in range(1, effective_max_batches + 1):
            batch_markets, next_cursor = self.fetch_market_batch(cursor=cursor)
            batches_fetched = batch
            total_examined += len(batch_markets or [])
            for market in batch_markets or []:
                if not isinstance(market, dict):
                    continue
                ticker = str(market.get("ticker") or "").strip()
                if ticker and ticker in seen_tickers:
                    continue
                if ticker:
                    seen_tickers.add(ticker)
                markets.append(market)
                if self._is_in_time_window(market):
                    in_window_seen += 1
            if not next_cursor or total_examined >= effective_market_limit or in_window_seen >= effective_in_window_target:
                break
            cursor = next_cursor

        self.discovery_cache.set(cache_key, list(markets), ttl_seconds=self.config.broad_cache_ttl_seconds)
        self.last_summary = {
            "mode": "broad",
            "batches_fetched": batches_fetched,
            "total_examined": total_examined,
            "in_window_seen": in_window_seen,
            "returned": len(markets),
            "effective_in_window_target": effective_in_window_target,
            "cache": self.discovery_cache.summary(),
            "http": self.http_client.summary(),
        }
        self._log(
            "[DISCOVERY-BROAD-SUMMARY]",
            batches_fetched=batches_fetched,
            total_examined=total_examined,
            in_window_seen=in_window_seen,
            target=effective_in_window_target,
            scan_depth=scan_depth,
            returned=len(markets),
            timestamp=utc_now_iso(),
        )
        return markets
