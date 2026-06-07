"""Orderbook and sports-market parsing for the AION live engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


SPORTS_PREFIXES = (
    "KXNBA",
    "KXWNBA",
    "KXMLB",
    "KXNFL",
    "KXNHL",
    "KXNCAAB",
    "KXNCAAF",
    "KXNCAASB",
    "KXCFB",
    "KXCBB",
    "KXUFC",
    "KXMMA",
    "KXGOLF",
    "KXEPL",
    "KXSOCCER",
    "KXSQUASH",
    "KXBOX",
    "KXMVE",
)

TENNIS_PREFIXES = (
    "KXATP",
    "KXWTA",
    "KXITF",
    "KXTENNIS",
)

DISABLED_NEW_ENTRY_PREFIXES = ("KXMLBKS",)


@dataclass(frozen=True)
class BookTop:
    price: Optional[float] = None
    size: float = 0.0
    source: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderbookSnapshot:
    ticker: str
    yes_bid: Optional[float]
    yes_bid_size: float
    no_bid: Optional[float]
    no_bid_size: float
    source: str
    real: bool
    synthetic: bool
    reason: str

    @property
    def yes_ask(self) -> Optional[float]:
        return complement_price(self.no_bid)

    @property
    def no_ask(self) -> Optional[float]:
        return complement_price(self.yes_bid)

    @property
    def yes_ask_size(self) -> float:
        return self.no_bid_size

    @property
    def no_ask_size(self) -> float:
        return self.yes_bid_size

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["yes_ask"] = self.yes_ask
        payload["no_ask"] = self.no_ask
        payload["yes_ask_size"] = self.yes_ask_size
        payload["no_ask_size"] = self.no_ask_size
        return payload


@dataclass(frozen=True)
class Quote:
    ticker: str
    family: str
    title: str
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    yes_bid_size: float
    yes_ask_size: float
    no_bid_size: float
    no_ask_size: float
    spread: Optional[float]
    liquidity: float
    volume: float
    open_interest: float
    orderbook_snapshot_id: str
    orderbook_source: str
    orderbook_real: bool
    synthetic: bool
    raw_market: Dict[str, Any]

    def price_for_side(self, side: str, action: str = "buy") -> Optional[float]:
        side = normalize_side(side)
        action = str(action or "buy").strip().lower()
        if side == "yes":
            return self.yes_ask if action == "buy" else self.yes_bid
        if side == "no":
            return self.no_ask if action == "buy" else self.no_bid
        return None

    def size_for_side(self, side: str, action: str = "buy") -> float:
        side = normalize_side(side)
        action = str(action or "buy").strip().lower()
        if side == "yes":
            return self.yes_ask_size if action == "buy" else self.yes_bid_size
        if side == "no":
            return self.no_ask_size if action == "buy" else self.no_bid_size
        return 0.0

    def spread_for_side(self, side: str) -> Optional[float]:
        side = normalize_side(side)
        if side == "yes" and self.yes_bid is not None and self.yes_ask is not None:
            return round(max(0.0, self.yes_ask - self.yes_bid), 4)
        if side == "no" and self.no_bid is not None and self.no_ask is not None:
            return round(max(0.0, self.no_ask - self.no_bid), 4)
        return self.spread

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["raw_market"] = dict(self.raw_market or {})
        return payload


def normalize_side(side: Any) -> Optional[str]:
    text = str(side or "").strip().lower()
    if text in {"yes", "y", "buy_yes"}:
        return "yes"
    if text in {"no", "n", "buy_no"}:
        return "no"
    return None


def coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, "", [], {}):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def coerce_price(value: Any) -> Optional[float]:
    numeric = coerce_float(value, None)
    if numeric is None:
        return None
    if 1.0 < numeric <= 100.0:
        numeric = numeric / 100.0
    if 0.0 <= numeric <= 1.0:
        return round(float(numeric), 4)
    return None


def complement_price(value: Any) -> Optional[float]:
    price = coerce_price(value)
    if price is None:
        return None
    return round(max(0.0, min(1.0, 1.0 - price)), 4)


def valid_trade_price(value: Any) -> bool:
    price = coerce_price(value)
    return bool(price is not None and 0.0 < price < 1.0)


def price_zone(value: Any) -> str:
    price = coerce_price(value)
    if price is None:
        return "unknown"
    if price < 0.20:
        return "low"
    if price < 0.40:
        return "lower_mid"
    if price <= 0.60:
        return "mid"
    if price <= 0.80:
        return "upper_mid"
    return "high"


def family_from_ticker(ticker: Any) -> str:
    ticker_text = str(ticker or "").strip().upper()
    if not ticker_text:
        return "UNKNOWN"
    return ticker_text.split("-", 1)[0]


def market_ticker(market: Dict[str, Any]) -> str:
    market = market if isinstance(market, dict) else {}
    return str(
        market.get("ticker")
        or market.get("market_ticker")
        or market.get("id")
        or ""
    ).strip()


def market_family(market: Dict[str, Any]) -> str:
    market = market if isinstance(market, dict) else {}
    explicit = str(
        market.get("series_ticker")
        or market.get("event_ticker")
        or market.get("family")
        or ""
    ).strip().upper()
    if explicit.startswith("KX"):
        return explicit.split("-", 1)[0]
    return family_from_ticker(market_ticker(market))


def market_title(market: Dict[str, Any]) -> str:
    market = market if isinstance(market, dict) else {}
    return str(
        market.get("title")
        or market.get("market_title")
        or market.get("subtitle")
        or market.get("name")
        or ""
    ).strip()


def market_text(market: Dict[str, Any]) -> str:
    market = market if isinstance(market, dict) else {}
    parts = [
        market_ticker(market),
        market_family(market),
        market_title(market),
        str(market.get("event_title") or ""),
        str(market.get("category") or ""),
    ]
    return " ".join(part for part in parts if part).upper()


def is_tennis_market(family: Any = None, ticker: Any = None, market: Dict[str, Any] | None = None) -> bool:
    text = " ".join(
        str(value or "").upper()
        for value in (
            family,
            ticker,
            market_text(market or {}) if market else "",
        )
    )
    return any(prefix in text for prefix in TENNIS_PREFIXES) or "TENNIS" in text or " ATP " in f" {text} " or " WTA " in f" {text} "


def is_kxmlbks_market(family: Any = None, ticker: Any = None, market: Dict[str, Any] | None = None) -> bool:
    text = " ".join(
        str(value or "").upper()
        for value in (
            family,
            ticker,
            market_text(market or {}) if market else "",
        )
    )
    return any(prefix in text for prefix in DISABLED_NEW_ENTRY_PREFIXES)


def is_sports_market(family: Any = None, ticker: Any = None, market: Dict[str, Any] | None = None) -> bool:
    family_text = str(family or "").strip().upper()
    ticker_text = str(ticker or "").strip().upper()
    if market:
        family_text = family_text or market_family(market)
        ticker_text = ticker_text or market_ticker(market).upper()
        if "SPORT" in str(market.get("category") or market.get("category_name") or "").upper():
            return True
    text = " ".join((family_text, ticker_text, market_text(market or {}))).upper()
    return any(text.startswith(prefix) or f" {prefix}" in f" {text}" for prefix in SPORTS_PREFIXES + TENNIS_PREFIXES)


def _walk_values(payload: Any, keys: Iterable[str]) -> Iterable[Any]:
    queue: List[Any] = [payload]
    seen: set[int] = set()
    wanted = {str(key) for key in keys}
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)
            for key, value in current.items():
                if str(key) in wanted:
                    yield value
                if isinstance(value, (dict, list, tuple)):
                    queue.append(value)
        elif isinstance(current, (list, tuple)):
            for value in current:
                if isinstance(value, (dict, list, tuple)):
                    queue.append(value)


def _first_value(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if isinstance(payload, dict) and payload.get(key) not in (None, "", [], {}):
            return payload.get(key)
    for value in _walk_values(payload, keys):
        if value not in (None, "", [], {}):
            return value
    return None


def _level_from_mapping(price_key: Any, size_value: Any, source: str) -> Optional[BookTop]:
    price = coerce_price(price_key)
    size = coerce_float(size_value, 0.0) or 0.0
    if price is None or size <= 0.0:
        return None
    return BookTop(price=price, size=size, source=source)


def _iter_levels(raw: Any, source: str) -> Iterable[BookTop]:
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in {"price", "price_dollars", "price_fp", "yes_price", "no_price"}:
                price = coerce_price(value)
                size = coerce_float(
                    raw.get("size")
                    or raw.get("count")
                    or raw.get("quantity")
                    or raw.get("contracts")
                    or raw.get("remaining"),
                    0.0,
                ) or 0.0
                if price is not None and size > 0.0:
                    yield BookTop(price=price, size=size, source=source)
                    return
            mapped = _level_from_mapping(key, value, source)
            if mapped is not None:
                yield mapped
        for nested_key in ("levels", "orders", "bids", "asks", "yes", "no", "yes_dollars", "no_dollars"):
            nested = raw.get(nested_key)
            if nested not in (None, "", [], {}):
                yield from _iter_levels(nested, f"{source}.{nested_key}")
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                price = coerce_price(
                    item.get("price")
                    or item.get("price_dollars")
                    or item.get("price_fp")
                    or item.get("yes_price")
                    or item.get("no_price")
                    or item.get("bid")
                    or item.get("ask")
                )
                size = coerce_float(
                    item.get("size")
                    or item.get("count")
                    or item.get("quantity")
                    or item.get("qty")
                    or item.get("contracts")
                    or item.get("remaining"),
                    0.0,
                ) or 0.0
                if price is not None and size > 0.0:
                    yield BookTop(price=price, size=size, source=source)
                else:
                    yield from _iter_levels(item, source)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                level = _level_from_mapping(item[0], item[1], source)
                if level is not None:
                    yield level


def _side_raw(payload: Dict[str, Any], side: str) -> Tuple[Any, str]:
    payload = payload if isinstance(payload, dict) else {}
    direct_keys = (
        ("yes", "yes_dollars", "yes_orders", "yes_bids", "yes_book", "bids")
        if side == "yes"
        else ("no", "no_dollars", "no_orders", "no_bids", "no_book", "asks")
    )
    for container_name in ("orderbook", "orderbook_fp", "book", "book_fp"):
        container = payload.get(container_name)
        if isinstance(container, dict):
            for key in direct_keys:
                if container.get(key) not in (None, "", [], {}):
                    return container.get(key), f"{container_name}.{key}"
    for key in direct_keys:
        if payload.get(key) not in (None, "", [], {}):
            return payload.get(key), key
    return None, "none"


def _best_bid(raw: Any, source: str) -> BookTop:
    levels = list(_iter_levels(raw, source))
    if not levels:
        return BookTop(source=source)
    return max(levels, key=lambda item: (item.price or 0.0, item.size))


def parse_orderbook(ticker: str, payload: Dict[str, Any] | None) -> OrderbookSnapshot:
    payload = payload if isinstance(payload, dict) else {}
    book_payload = payload.get("orderbook") if isinstance(payload.get("orderbook"), dict) else payload
    yes_raw, yes_source = _side_raw(book_payload, "yes")
    no_raw, no_source = _side_raw(book_payload, "no")
    yes_top = _best_bid(yes_raw, yes_source)
    no_top = _best_bid(no_raw, no_source)
    synthetic = bool(
        payload.get("synthetic")
        or payload.get("fake")
        or payload.get("derived")
        or payload.get("simulated")
        or str(payload.get("source") or "").strip().lower() in {"synthetic", "fake", "simulated"}
    )
    real = bool(
        not synthetic
        and (
            (yes_top.price is not None and yes_top.size > 0.0)
            or (no_top.price is not None and no_top.size > 0.0)
        )
    )
    reason = "ok" if real else "fake_or_empty_orderbook" if synthetic else "missing_real_orderbook_levels"
    source = "orderbook_endpoint"
    if yes_source != "none" or no_source != "none":
        source = f"orderbook_endpoint:{yes_source}|{no_source}"
    return OrderbookSnapshot(
        ticker=str(ticker or "").strip(),
        yes_bid=yes_top.price,
        yes_bid_size=yes_top.size,
        no_bid=no_top.price,
        no_bid_size=no_top.size,
        source=source,
        real=real,
        synthetic=synthetic,
        reason=reason,
    )


def _market_price(market: Dict[str, Any], *keys: str) -> Optional[float]:
    return coerce_price(_first_value(market, *keys))


def _market_size(market: Dict[str, Any], *keys: str) -> float:
    return coerce_float(_first_value(market, *keys), 0.0) or 0.0


def build_quote(market: Dict[str, Any], orderbook_payload: Dict[str, Any] | None = None) -> Quote:
    market = market if isinstance(market, dict) else {}
    ticker = market_ticker(market)
    family = market_family(market)
    book = parse_orderbook(ticker, orderbook_payload or {})
    yes_bid = book.yes_bid if book.yes_bid is not None else _market_price(market, "yes_bid", "yes_bid_dollars", "best_yes_bid")
    no_bid = book.no_bid if book.no_bid is not None else _market_price(market, "no_bid", "no_bid_dollars", "best_no_bid")
    yes_ask = _market_price(market, "yes_ask", "yes_ask_dollars", "best_yes_ask") or complement_price(no_bid)
    no_ask = _market_price(market, "no_ask", "no_ask_dollars", "best_no_ask") or complement_price(yes_bid)
    yes_bid_size = book.yes_bid_size or _market_size(market, "yes_bid_size", "best_yes_bid_size", "yes_size")
    no_bid_size = book.no_bid_size or _market_size(market, "no_bid_size", "best_no_bid_size", "no_size")
    yes_ask_size = book.yes_ask_size or _market_size(market, "yes_ask_size", "best_yes_ask_size")
    no_ask_size = book.no_ask_size or _market_size(market, "no_ask_size", "best_no_ask_size")
    spreads: List[float] = []
    if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid:
        spreads.append(round(yes_ask - yes_bid, 4))
    if no_bid is not None and no_ask is not None and no_ask >= no_bid:
        spreads.append(round(no_ask - no_bid, 4))
    liquidity = coerce_float(
        market.get("liquidity")
        or market.get("liquidity_dollars")
        or market.get("dollar_volume")
        or 0.0,
        0.0,
    ) or 0.0
    volume = coerce_float(market.get("volume") or market.get("volume_24h") or market.get("dollar_volume") or 0.0, 0.0) or 0.0
    open_interest = coerce_float(market.get("open_interest") or market.get("open_interest_dollars") or 0.0, 0.0) or 0.0
    snapshot_id = f"{ticker}:{round(yes_bid or 0.0, 4)}:{round(no_bid or 0.0, 4)}:{round(yes_bid_size, 4)}:{round(no_bid_size, 4)}"
    return Quote(
        ticker=ticker,
        family=family,
        title=market_title(market),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        yes_bid_size=yes_bid_size,
        yes_ask_size=yes_ask_size,
        no_bid_size=no_bid_size,
        no_ask_size=no_ask_size,
        spread=min(spreads) if spreads else None,
        liquidity=liquidity,
        volume=volume,
        open_interest=open_interest,
        orderbook_snapshot_id=snapshot_id,
        orderbook_source=book.source,
        orderbook_real=book.real,
        synthetic=book.synthetic,
        raw_market=dict(market),
    )


def extract_markets(payload: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not isinstance(payload, dict):
        return [], None
    markets = payload.get("markets") or payload.get("data") or payload.get("results") or []
    if isinstance(markets, dict):
        markets = list(markets.values())
    if not isinstance(markets, list):
        markets = []
    cursor = payload.get("cursor") or payload.get("next_cursor") or payload.get("next")
    return [market for market in markets if isinstance(market, dict)], str(cursor) if cursor else None
