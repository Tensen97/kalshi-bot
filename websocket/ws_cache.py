import threading
import time
from collections import deque
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LiveMarketCache:
    def __init__(self, max_age_seconds=120.0, max_entries=2000, cleanup_interval_seconds=30.0, summary_interval_seconds=60.0):
        self.max_age_seconds = float(max_age_seconds or 120.0)
        self.max_entries = int(max_entries or 2000)
        self.cleanup_interval_seconds = float(cleanup_interval_seconds or 30.0)
        self.summary_interval_seconds = float(summary_interval_seconds or 60.0)
        self._lock = threading.RLock()
        self._markets = {}
        self._last_cleanup_ts = 0.0
        self._last_summary_ts = 0.0
        self._recent_updates = deque(maxlen=50)

    def _log(self, prefix, **fields):
        parts = [prefix]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        print(" ".join(parts))

    def _normalize_ticker(self, payload):
        if not isinstance(payload, dict):
            return None
        for key in ("ticker", "market_ticker", "symbol", "market", "event_ticker"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return None

    def _extract_depth(self, payload):
        depth = {}
        orderbook = payload.get("orderbook") if isinstance(payload, dict) else None
        if isinstance(orderbook, dict):
            depth["orderbook"] = orderbook
        for key in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size", "bid_size", "ask_size", "yes_depth", "no_depth"):
            value = payload.get(key)
            if value is not None:
                depth[key] = value
        return depth

    def upsert_market(self, payload, source="ws", message_type="market_update"):
        ticker = self._normalize_ticker(payload)
        if not ticker:
            return None
        now = time.time()
        orderbook = payload.get("orderbook") if isinstance(payload, dict) else None
        yes_bid = payload.get("yes_bid", payload.get("best_bid"))
        yes_ask = payload.get("yes_ask", payload.get("best_ask"))
        no_bid = payload.get("no_bid")
        no_ask = payload.get("no_ask")
        record = {
            "ticker": ticker,
            "source": source,
            "message_type": message_type,
            "updated_ts": now,
            "updated_at": _now_iso(),
            "best_bid": payload.get("best_bid", payload.get("yes_bid")),
            "best_ask": payload.get("best_ask", payload.get("yes_ask")),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "spread": payload.get("spread"),
            "last_trade": payload.get("last_trade", payload.get("last_price")),
            "volume": payload.get("volume"),
            "liquidity": payload.get("liquidity", payload.get("liquidity_estimate")),
            "depth": self._extract_depth(payload),
            "raw": payload,
        }
        if orderbook is not None:
            record["orderbook"] = orderbook
        with self._lock:
            self._markets[ticker] = record
            self._recent_updates.append({"ticker": ticker, "source": source, "ts": now})
            if len(self._markets) > self.max_entries:
                self._trim_oldest_locked()
        self._log("[WS-CACHE-UPDATE]", ticker=ticker, source=source, message_type=message_type, timestamp=record["updated_at"])
        self.cleanup_stale(force=False)
        return record

    def bulk_sync_from_rest(self, markets=None, quotes=None, source="rest"):
        updates = 0
        for market in markets or []:
            if self.upsert_market(market, source=source, message_type="rest_market"):
                updates += 1
        for quote in quotes or []:
            if quote is None:
                continue
            payload = {
                "ticker": getattr(quote, "ticker", None),
                "best_bid": getattr(quote, "yes_bid", None),
                "best_ask": getattr(quote, "yes_ask", None),
                "spread": (getattr(quote, "yes_ask", 0.0) - getattr(quote, "yes_bid", 0.0)) if getattr(quote, "yes_bid", 0.0) and getattr(quote, "yes_ask", 0.0) else None,
                "last_trade": getattr(quote, "last_price", None),
                "volume": getattr(quote, "volume", None),
                "liquidity": getattr(quote, "liquidity", None),
            }
            if self.upsert_market(payload, source=source, message_type="rest_quote"):
                updates += 1
        if updates:
            self._log("[WS-CACHE-UPDATE]", source=source, updated_records=updates, timestamp=_now_iso())
        return updates

    def _trim_oldest_locked(self):
        if not self._markets:
            return
        oldest_ticker = min(self._markets.items(), key=lambda item: float(item[1].get("updated_ts", 0.0) or 0.0))[0]
        self._markets.pop(oldest_ticker, None)

    def cleanup_stale(self, force=False):
        now = time.time()
        if not force and (now - float(self._last_cleanup_ts or 0.0)) < self.cleanup_interval_seconds:
            return []
        removed = []
        with self._lock:
            stale = [ticker for ticker, record in self._markets.items() if (now - float(record.get("updated_ts", 0.0) or 0.0)) > self.max_age_seconds]
            for ticker in stale:
                removed.append(self._markets.pop(ticker, None))
        self._last_cleanup_ts = now
        if removed:
            self._log("[WS-CACHE-STALE]", removed=len(removed), timestamp=_now_iso())
        return removed

    def get_snapshot(self, ticker):
        ticker = str(ticker or "").strip()
        if not ticker:
            return None
        with self._lock:
            record = dict(self._markets.get(ticker) or {})
        if not record:
            return None
        age_seconds = max(0.0, time.time() - float(record.get("updated_ts", 0.0) or 0.0))
        record["age_seconds"] = round(age_seconds, 4)
        record["stale"] = age_seconds > self.max_age_seconds
        return record

    def summary(self):
        now = time.time()
        with self._lock:
            entries = list(self._markets.values())
        stale_count = sum(1 for item in entries if (now - float(item.get("updated_ts", 0.0) or 0.0)) > self.max_age_seconds)
        average_age = round(sum(max(0.0, now - float(item.get("updated_ts", 0.0) or 0.0)) for item in entries) / max(len(entries), 1), 4) if entries else 0.0
        summary = {
            "cache_size": len(entries),
            "stale_count": stale_count,
            "average_age_seconds": average_age,
            "recent_updates": len(self._recent_updates),
        }
        if (now - float(self._last_summary_ts or 0.0)) >= self.summary_interval_seconds:
            self._last_summary_ts = now
            self._log(
                "[WS-CACHE-SUMMARY]",
                cache_size=summary["cache_size"],
                stale_count=summary["stale_count"],
                average_age_seconds=summary["average_age_seconds"],
                timestamp=_now_iso(),
            )
        return summary
