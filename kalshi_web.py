import argparse
import base64
from datetime import datetime, timezone
import csv
import json
import os
import sys
import time
import requests
from dataclasses import dataclass


def send_telegram_message(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
    token_prefix = f"{token[:8]}..." if token else "missing"
    chat_prefix = f"{chat_id[:8]}..." if chat_id else "missing"
    print(
        f"[TELEGRAM-DEBUG] token_present={bool(token)} token_prefix={token_prefix} "
        f"chat_present={bool(chat_id)} chat_prefix={chat_prefix}"
    )
    if not token or not chat_id or not text:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": str(text)[:4000]},
            timeout=10,
        )
        try:
            response_body = response.json()
        except Exception:
            response_body = {"raw_text": response.text[:500]}
        print(
            f"[TELEGRAM-DEBUG] status_code={response.status_code} "
            f"response={response_body}"
        )
        if isinstance(response_body, dict):
            return bool(response.ok and response_body.get("ok"))
        return bool(response.ok)
    except Exception as exc:
        print(f"[TELEGRAM-DEBUG] exception={exc}")
        return False

# Safe live test runbook:
# 1. Live preflight:
#    python3 /Users/Kevin/Desktop/kalshi_web.py --live --arm-live --live-test-ticker TICKER --live-preflight
# 2. Live smoke test:
#    python3 /Users/Kevin/Desktop/kalshi_web.py --live --arm-live --live-smoke-test
# 3. Live auth GET smoke test:
#    python3 /Users/Kevin/Desktop/kalshi_web.py --live --arm-live --live-auth-get-smoke-test
# 4. Live balance GET smoke test:
#    Not implemented in this file yet. Skip this step here.
# 5. Run live canary:
#    python3 /Users/Kevin/Desktop/kalshi_web.py --live --arm-live --live-test-ticker TICKER --run-live-canary
# 6. Optionally cancel canary order:
#    python3 /Users/Kevin/Desktop/kalshi_web.py --live --arm-live --cancel-live-order-id ORDER_ID

BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
TAKE_PROFIT_MIN = 0.03
TAKE_PROFIT_MAX = 0.05
STOP_LOSS_MIN = 0.02
STOP_LOSS_MAX = 0.03
STOP_LOSS_COOLDOWN_SECONDS = 180
MAX_HOLD_SECONDS = 600
MIN_LIQUIDITY_EXIT = 20
MIN_ENTRY_LIQUIDITY = 50
MIN_ENTRY_VOLUME = 25
MAX_ENTRY_SPREAD = 0.06
MAX_YES_ASK_TO_ENTER = 0.92
TAKE_PROFIT_DELTA = 0.08
STOP_LOSS_DELTA = 0.06
MAX_HOLD_MINUTES = 20
MAX_EXIT_SPREAD = 0.12
LIVE_POSITION_POLL_SECONDS = 15
ALLOW_CONTINUOUS_LIVE_EXITS = False
LIVE_EXIT_COOLDOWN_SECONDS = 60
LIVE_SELECT_BATCH_SIZE = 200
LIVE_SELECT_MAX_BATCHES = 5
LIVE_SELECT_MAX_MARKETS = 1000
MIN_MARKETS_IN_WINDOW_TARGET = 40
MAX_SCAN_BATCHES = 10
DISCOVERY_RETRY_DELAY_SECONDS = 120
MAX_DISCOVERY_RETRIES = 12
DISCOVERY_RETRY_BACKOFF_MULTIPLIER = 1.5
MAX_DISCOVERY_RETRY_DELAY_SECONDS = 300
TELEGRAM_HEARTBEAT_INTERVAL_SECONDS = 3600
PHASE5_BUY_SCORE_THRESHOLD = 70.0
ENABLE_TARGETED_SERIES_MODE = True
TARGETED_SERIES_TICKERS = ["KXBTC15M"]
TARGETED_DISCOVERY_EXPANSION_SERIES_TICKERS = [
    "KXTEMP",
    "KXRAIN",
]
HIGH_LIQUIDITY_PRIORITY_FAMILIES = (
    "KXBTC15M",
    "KXNBA1HTOTAL",
    "KXNBA1HSPREAD",
    "KXNBASPREAD",
)
DISCOVERY_DEPRIORITIZED_FAMILIES = (
    "KXTEMP",
    "KXRAIN",
    "KXSNOW",
)
OPTIONAL_TARGETED_SERIES_TICKERS = [
    "KXNBA1HTOTAL",
    "KXNBA1HSPREAD",
    "KXNBASPREAD",
] + TARGETED_DISCOVERY_EXPANSION_SERIES_TICKERS
TARGETED_SERIES_PRIORITY_WINDOWS = {
    "KXBTC15M": {"hours_utc": (), "reason": "always_on_crypto_window"},
    "KXNBA1HTOTAL": {"hours_utc": (16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4), "reason": "us_basketball_game_window"},
    "KXNBA1HSPREAD": {"hours_utc": (16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4), "reason": "us_basketball_game_window"},
    "KXNBASPREAD": {"hours_utc": (16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4), "reason": "us_basketball_game_window"},
    "KXTEMP": {"hours_utc": (), "reason": "weather_active_short_horizon_window"},
    "KXRAIN": {"hours_utc": (), "reason": "weather_active_short_horizon_window"},
}
TARGETED_FAMILY_PRIORITY_SCHEDULES = (
    {
        "name": "nba_game_start_window",
        "hours_utc": (23, 0, 1, 2, 3),
        "ordered": ["KXNBA1HTOTAL", "KXNBA1HSPREAD", "KXNBASPREAD", "KXBTC15M", "KXTEMP", "KXRAIN"],
        "reason": "nba_game_start_and_early_game_emphasis",
    },
    {
        "name": "crypto_default_window",
        "hours_utc": (),
        "ordered": ["KXBTC15M", "KXNBA1HTOTAL", "KXNBA1HSPREAD", "KXNBASPREAD", "KXTEMP", "KXRAIN"],
        "reason": "outside_nba_start_window_keep_crypto_and_mainline_sports_ahead_of_weather",
    },
)
BASE_MAX_TARGETED_CONTRACTS_PER_FAMILY = 8
TARGETED_CONTRACT_CAP_INCREASE = 2
MAX_TARGETED_CONTRACTS_PER_FAMILY = BASE_MAX_TARGETED_CONTRACTS_PER_FAMILY + TARGETED_CONTRACT_CAP_INCREASE
TARGETED_CONTRACT_DISTANCE_BUFFER_MINUTES = 30
TARGETED_SERIES_EXCLUDE_KEYWORDS = (
    "CHAMP",
    "CHAMPIONSHIP",
    "OUTRIGHT",
    "SEASON",
    "FUTURE",
    "FINALS",
    "TITLE",
)
TARGETED_ORDERBOOK_MIN_SIZE = 5.0
TARGETED_SERIES_MIN_LIQUIDITY = {
    "KXBTC15M": 10.0,
}
TARGETED_SERIES_MIN_TOP_OF_BOOK_SIZE = {
    "KXBTC15M": 5.0,
}
BTC15M_DEAD_BOOK_MIN_CYCLES = 3
TARGETED_DEAD_BOOK_MIN_CYCLES = 3
PREFERRED_MARKET_FAMILIES = HIGH_LIQUIDITY_PRIORITY_FAMILIES
FALLBACK_MARKET_FAMILIES = DISCOVERY_DEPRIORITIZED_FAMILIES
DEPRIORITIZED_MARKET_FAMILIES = (
    "KXMLBHRR",
    "KXMLBTB",
    "KXMLBHIT",
    "KXNHLGOAL",
    "KXMLBHR",
)
BLOCKED_MARKET_FAMILIES = (
    "KXMVECROSSCATEGORY",
    "KXMVESPORTSMULTIGAMEEXTENDED",
)
MAX_MARKETS_PER_FAMILY = 75
FAMILY_MAX_MISSING_QUOTES_RATE = 0.60
FAMILY_MAX_WIDE_SPREAD_RATE = 0.60
FAMILY_MAX_LOW_LIQUIDITY_RATE = 0.60
FAMILY_MAX_TOO_EARLY_RATE = 0.85
FAMILY_MIN_SURVIVOR_RATE = 0.03
FAMILY_SPREAD_QUALITY_WEIGHT = 2.0
FAMILY_MISSING_QUOTES_PENALTY = 1.0
FAMILY_LOW_LIQUIDITY_PENALTY = 1.0
FAMILY_WIDE_SPREAD_PENALTY = 1.5
FAMILY_TOO_EARLY_PENALTY = 1.0
FAMILY_SURVIVOR_BONUS = 3.0
MAX_FAMILIES_PER_SCAN = 12
MAX_PREFERRED_FAMILIES_PER_PASS = 4
MAX_BATCHES_PER_FAMILY = 2
MAX_QUALITY_EXPANSION_FAMILIES = 4
QUALITY_EXPANSION_PREFERRED_FAMILY_PREFIXES = (
    "KXTEMP",
    "KXRAIN",
    "KXSNOW",
)
MAX_PREFILTER_POOL = 300
MAX_STRICT_VALIDATION_POOL = 100
MIN_MINUTES_TO_EVENT = 5
MAX_MINUTES_TO_EVENT = 480
TIME_WINDOW_EXTENSION_MINUTES = 60
BROAD_DISCOVERY_TIME_WINDOW_EXTENSION_MINUTES = 720
EVENT_TIME_WEIGHT = 3.0
SIM_POSITION_SIZE = 1
MAX_TOTAL_EXPOSURE = 2
MAX_DAILY_LOSS = 0.10
MAX_CONSECUTIVE_LOSSES = 3
MAX_POSITION_COST = 0.85
MIN_ENTRY_PRICE = 0.10
MAX_ENTRY_PRICE = 0.90
TRADE_LOG_CSV = "kalshi_trade_log.csv"
LIVE_POSITION_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_position_state.json")
STATUS_EVERY_N_CYCLES = 1
SHOW_TOP_CANDIDATES = 3
ENABLE_GROUP_ALLOWLIST = False
ALLOWED_GROUPS = set()
ENABLE_GROUP_BLOCKLIST = False
BLOCKED_GROUPS = set()
ENABLE_GROUP_SCORE_WEIGHTS = False
GROUP_SCORE_WEIGHTS = {}
MIN_TRADES_FOR_GROUP_WEIGHT_SUGGESTION = 5
MAX_SUGGESTED_GROUP_MULTIPLIER = 1.15
MIN_SUGGESTED_GROUP_MULTIPLIER = 0.85
DRY_RUN_LIVE_ORDERS = True
DEFAULT_LIVE_ORDER_SIDE = "yes"
DEFAULT_LIVE_ORDER_ACTION = "buy"
LIVE_MODE = False
LIVE_ARMED = False
LIVE_ENV_CONFIRMED = False
LIVE_ORDER_RATE_LIMIT = 3
LIVE_ORDER_RATE_WINDOW_SEC = 60
MAX_SIMULTANEOUS_LIVE_POSITIONS = 3
MAX_LIVE_ORDER_SIZE = 1
MAX_LIVE_TRADES_PER_DAY = 1
LIVE_TEST_TICKER = None
LIVE_AUDIT_LOG_FILE = "live_audit_log.csv"
DEFAULT_LIVE_PRIVATE_KEY_PATH = "/Users/Kevin/Downloads/kalshi.key.txt"


@dataclass
class Quote:
    ticker: str
    title: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    last_price: float
    volume: float
    liquidity: float


class KalshiSniperBot:
    def __init__(self):
        self.max_markets = 50
        self.max_spread = 0.20
        self.min_volume = 1
        self.min_price = 0.10
        self.max_price = 0.90
        self.take_profit = 0.04
        self.stop_loss = 0.03
        self.cooldown_seconds = 120
        self.last_stop_time = 0
        self.positions = []
        self.market_cooldowns = {}
        self.realized_pnl = 0.0
        self.consecutive_losses = 0
        self.trading_halted = False
        self.trade_log_csv = TRADE_LOG_CSV
        self.cycle_count = 0
        self.live_positions = []
        self._live_order_timestamps = []
        self.live_trade_day = None
        self.live_trade_count_today = 0
        self.live_canary_submitted = False
        self.live_canary_selected_ticker = None
        self.live_canary_authorized_ticker = None
        self.live_pending_entry_ticker = None
        self.live_exit_cooldowns = {}
        self.live_family_runtime_stats = {}
        self.last_live_select_summary = {}
        self.targeted_orderbook_cache = {}
        self.targeted_family_hourly_stats = {}
        self.live_family_hourly_observation_stats = {}
        self.btc15m_top_book_observation_state = {}
        self.btc15m_near_miss_persistence = {}
        self.last_safety_alert_reason = None
        self.last_telegram_heartbeat_ts = 0.0
        self.last_error_message = None
        self.no_candidate_cycle_streak = 0
        self.market_condition_state = "GOOD"
        self.market_condition_reason = "normal"
        self.phase55_rolling_price_history = {}
        self.phase55_rolling_price_marker_cache = {}
        self.phase55_market_history_cache = {}
        self.phase55_current_select_pass_marker = ""
        self.phase55_select_pass_counter = 0
        self.strict_validation_liquidity_trace_tickers = set()
        self.last_targeted_priority_adaptation = {}

    def get_markets(self, limit=25, status="open", cursor=None, return_cursor=False, extra_params=None):
        if globals().get("LIVE_MODE", False):
            base_url = self.get_live_base_url()
            url = self.build_live_request_url("/trade-api/v2/markets")
            print(f"[LIVE-MARKETS-DEBUG] base_url={base_url}")
            print(f"[LIVE-MARKETS-DEBUG] url={url}")
        else:
            url = f"{BASE_URL}/markets"
        params = {
            "limit": limit,
            "status": status,
        }
        if cursor:
            params["cursor"] = cursor
        if extra_params:
            params.update(extra_params)
        if globals().get("LIVE_MODE", False):
            params.setdefault("mve_filter", "exclude")
        if globals().get("LIVE_MODE", False):
            print(f"[LIVE-MARKETS-DEBUG] params={params}")
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        response_data = response.json()
        markets = response_data.get("markets", [])
        if return_cursor:
            next_cursor = response_data.get("cursor") or response_data.get("next_cursor")
            return markets, next_cursor
        return markets

    def to_float(self, value, default=0.0):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_market_quote_values(self, market):
        return {
            "yes_bid": self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")), None),
            "yes_ask": self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")), None),
            "no_bid": self.to_float(market.get("no_bid_dollars", market.get("no_bid")), None),
            "no_ask": self.to_float(market.get("no_ask_dollars", market.get("no_ask")), None),
        }

    def has_complete_quote_side(self, bid_value, ask_value):
        return bid_value is not None and ask_value is not None

    def has_complete_numeric_quotes(self, market):
        quote_values = self.get_market_quote_values(market)
        return all(value is not None for value in quote_values.values())

    def append_live_audit_log(self, event_type, **fields):
        try:
            log_path = LIVE_AUDIT_LOG_FILE
            file_exists = os.path.exists(log_path)
            fieldnames = [
                "timestamp",
                "event_type",
                "ticker",
                "order_id",
                "side",
                "action",
                "count",
                "price",
                "reason",
                "status_code",
            ]
            row = {name: "" for name in fieldnames}
            row["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            row["event_type"] = event_type
            for key in fieldnames:
                if key in fields:
                    row[key] = fields[key]
            needs_header = not file_exists or os.path.getsize(log_path) == 0
            with open(log_path, "a", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                if needs_header:
                    writer.writeheader()
                writer.writerow(row)
        except OSError:
            return

    def load_live_position_state(self):
        if not os.path.exists(LIVE_POSITION_STATE_FILE):
            return {}
        try:
            with open(LIVE_POSITION_STATE_FILE) as state_file:
                data = json.load(state_file)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            print(f"[LIVE-STATE] warning unable_to_load path={LIVE_POSITION_STATE_FILE}")
            return {}

    def save_live_position_state(self, state):
        try:
            with open(LIVE_POSITION_STATE_FILE, "w") as state_file:
                json.dump(state if isinstance(state, dict) else {}, state_file, sort_keys=True, indent=2)
        except OSError:
            print(f"[LIVE-STATE] warning unable_to_save path={LIVE_POSITION_STATE_FILE}")

    def persist_live_entry_metadata(self, order=None, payload=None, order_id=None):
        summary = self.extract_order_execution_summary(order or {})
        ticker = (summary.get("ticker") or (payload or {}).get("ticker") or "").strip()
        side = summary.get("side") or (payload or {}).get("side")
        entry_price = summary.get("price")
        if entry_price is None and payload:
            entry_price = payload.get("yes_price")
            if entry_price is None:
                entry_price = payload.get("no_price")
        count = summary.get("filled_count")
        if count is None and payload:
            try:
                count = float(payload.get("count"))
            except (TypeError, ValueError):
                count = None
        if not ticker or not side or count in (None, 0):
            return
        state = self.load_live_position_state()
        state[ticker] = {
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "count": count,
            "entry_ts": time.time(),
            "order_id": summary.get("order_id") or order_id,
        }
        self.save_live_position_state(state)

    def remove_live_entry_metadata(self, ticker):
        ticker = (ticker or "").strip()
        if not ticker:
            return
        state = self.load_live_position_state()
        if ticker in state:
            state.pop(ticker, None)
            self.save_live_position_state(state)

    def get_live_entry_metadata(self, ticker):
        ticker = (ticker or "").strip()
        state = self.load_live_position_state()
        entry = state.get(ticker)
        print(
            f"[LIVE-STATE-LOOKUP] ticker={ticker} "
            f"found={'yes' if isinstance(entry, dict) else 'no'} keys={sorted(list(state.keys()))[:10]}"
        )
        return entry if isinstance(entry, dict) else None

    def upsert_live_entry_metadata_from_position(self, position_summary):
        ticker = (position_summary.get("ticker") or "").strip()
        count = position_summary.get("count")
        if not ticker or count is None or abs(count) == 0:
            return None
        state = self.load_live_position_state()
        existing = state.get(ticker) if isinstance(state.get(ticker), dict) else None
        updated_ts = position_summary.get("updated_ts")
        source = "last_updated_ts" if updated_ts is not None else "now"
        entry_ts = updated_ts if updated_ts is not None else time.time()
        if existing and existing.get("entry_ts") is not None:
            entry_ts = existing.get("entry_ts")
        elif not existing:
            print(f"[LIVE-STATE-BACKFILL] ticker={ticker} entry_ts={entry_ts} source={source}")
        state[ticker] = {
            "ticker": ticker,
            "side": position_summary.get("side"),
            "entry_price": position_summary.get("avg_price"),
            "count": count,
            "entry_ts": entry_ts,
            "last_update_ts": updated_ts if updated_ts is not None else time.time(),
        }
        self.save_live_position_state(state)
        return state[ticker]

    def print_recent_live_audit_entries(self, last_n=5):
        if not os.path.exists(LIVE_AUDIT_LOG_FILE):
            print("[LIVE-AUDIT] no audit log found")
            return
        try:
            with open(LIVE_AUDIT_LOG_FILE, newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
        except OSError:
            print("[LIVE-AUDIT] unable to read audit log")
            return
        if not rows:
            print("[LIVE-AUDIT] no audit log found")
            return
        recent_rows = rows[-last_n:]
        print(f"[LIVE-AUDIT] showing last {len(recent_rows)} entries")
        for row in recent_rows:
            print(
                f"[LIVE-AUDIT] {row.get('timestamp', '')} | {row.get('event_type', '')} | "
                f"ticker={row.get('ticker', '')} | order_id={row.get('order_id', '')} | "
                f"reason={row.get('reason', '')}"
            )

    def print_live_startup_checklist(self, args=None):
        config_status = self.get_live_config_status()
        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (
            globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
        )
        canary_required = bool(args is not None and getattr(args, "run_live_canary", False))
        canary_ticker_ok = (not canary_required) or bool(
            getattr(args, "live_test_ticker", None) or globals().get("LIVE_TEST_TICKER")
        )
        canary_available_ok = (not canary_required) or (not self.live_canary_submitted)
        checks = [
            ("LIVE_MODE", bool(globals().get("LIVE_MODE", False))),
            ("LIVE_ARMED", bool(globals().get("LIVE_ARMED", False))),
            ("ENV_CONFIRMED", bool(globals().get("LIVE_ENV_CONFIRMED", False))),
            ("EFFECTIVE_DRY_RUN", bool(effective_live_dry_run)),
            ("CONFIG_READY", bool(config_status.get("ok"))),
            ("AUTH_READY", bool(self.can_build_live_auth())),
            ("ORDER_API_PATH", bool(self.has_live_order_api_path())),
            ("RATE_LIMIT_CONFIG", bool(LIVE_ORDER_RATE_LIMIT and LIVE_ORDER_RATE_WINDOW_SEC)),
            ("DAILY_CAP_CONFIG", bool(MAX_LIVE_TRADES_PER_DAY)),
            ("SIZE_CAP_CONFIG", bool(MAX_LIVE_ORDER_SIZE)),
            ("CANARY_TICKER", canary_ticker_ok),
            ("CANARY_AVAILABLE", canary_available_ok),
        ]
        for name, ok in checks:
            print(f"[LIVE-CHECK] {name}: {'PASS' if ok else 'FAIL'}")

    def run_live_preflight(self, args=None):
        print("[LIVE-PREFLIGHT] starting")
        config_status = self.get_live_config_status()
        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (
            globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
        )
        canary_required = bool(args is not None and getattr(args, "run_live_canary", False))
        canary_ticker_ok = (not canary_required) or bool(
            getattr(args, "live_test_ticker", None) or globals().get("LIVE_TEST_TICKER")
        )
        canary_available_ok = (not canary_required) or (not self.live_canary_submitted)
        checks = {
            "LIVE_MODE": bool(globals().get("LIVE_MODE", False)),
            "LIVE_ARMED": bool(globals().get("LIVE_ARMED", False)),
            "ENV_CONFIRMED": bool(globals().get("LIVE_ENV_CONFIRMED", False)),
            "EFFECTIVE_DRY_RUN": bool(effective_live_dry_run),
            "CONFIG_READY": bool(config_status.get("ok")),
            "AUTH_READY": bool(self.can_build_live_auth()),
            "ORDER_API_PATH": bool(self.has_live_order_api_path()),
            "RATE_LIMIT_CONFIG": bool(LIVE_ORDER_RATE_LIMIT and LIVE_ORDER_RATE_WINDOW_SEC),
            "DAILY_CAP_CONFIG": bool(MAX_LIVE_TRADES_PER_DAY),
            "SIZE_CAP_CONFIG": bool(MAX_LIVE_ORDER_SIZE),
            "CANARY_TICKER": canary_ticker_ok,
            "CANARY_AVAILABLE": canary_available_ok,
        }
        for name, ok in checks.items():
            print(f"[LIVE-PREFLIGHT] {name}={'PASS' if ok else 'FAIL'}")
        required_checks = (
            "LIVE_MODE",
            "LIVE_ARMED",
            "ENV_CONFIRMED",
            "CONFIG_READY",
            "AUTH_READY",
            "ORDER_API_PATH",
            "RATE_LIMIT_CONFIG",
            "DAILY_CAP_CONFIG",
            "SIZE_CAP_CONFIG",
        )
        if canary_required:
            required_checks = required_checks + ("CANARY_TICKER", "CANARY_AVAILABLE")
        ready = all(checks[name] for name in required_checks)
        print(f"[LIVE-PREFLIGHT] {'READY' if ready else 'NOT READY'}")
        return {
            "ok": ready,
            "reason": "ready" if ready else "not_ready",
            "checks": checks,
        }

    def build_quote(self, market):
        ticker = market.get("ticker", "N/A")
        title = market.get("title") or market.get("subtitle") or "Untitled"

        yes_bid = self.to_float(
            market.get("yes_bid_dollars", market.get("yes_bid"))
        )
        yes_ask = self.to_float(
            market.get("yes_ask_dollars", market.get("yes_ask"))
        )
        last_price = self.to_float(
            market.get("last_price_dollars", market.get("last_price", yes_bid))
        )
        volume = self.to_float(market.get("volume_fp", market.get("volume")), 0.0)
        liquidity = self.to_float(market.get("liquidity_dollars"), 0.0)

        no_bid = self.to_float(
            market.get("no_bid_dollars", market.get("no_bid"))
        )
        no_ask = self.to_float(
            market.get("no_ask_dollars", market.get("no_ask"))
        )

        if no_bid == 0.0 and yes_ask > 0.0:
            no_bid = max(0.0, 1.0 - yes_ask)
        if no_ask == 0.0 and yes_bid > 0.0:
            no_ask = min(1.0, 1.0 - yes_bid)

        return Quote(
            ticker=ticker,
            title=title,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            last_price=last_price,
            volume=volume,
            liquidity=liquidity,
        )

    def _score_live_ticker_candidate(self, q: Quote):
        spread = q.yes_ask - q.yes_bid
        mid_price = q.yes_ask
        if q.yes_bid > 0:
            mid_price = (q.yes_bid + q.yes_ask) / 2
        score = 0.0
        score += max(0.0, MAX_ENTRY_SPREAD - spread) * 40
        score += min(q.liquidity, 500) / 25
        score += min(q.volume, 500) / 40
        score += max(0.0, 0.25 - abs(mid_price - 0.5)) * 8
        score += max(0.0, 24 - len(q.ticker)) / 24
        return round(score, 4)

    def _filter_live_entry_candidate(self, market, q):
        ticker = getattr(q, "ticker", "") or market.get("ticker", "")
        family = self.get_market_family(market)
        should_trace_priority_family = self._should_trace_priority_topbook_family(family)
        if not ticker:
            return False, "missing_ticker"
        if self._is_combo_like_market(market):
            return False, "combo_or_mve"
        status = (market.get("status") or "").lower()
        if status not in ("open", "active"):
            return False, "inactive"
        if market.get("can_close") is False or market.get("can_open") is False:
            return False, "not_tradable"
        yes_bid = getattr(q, "yes_bid", 0.0) or 0.0
        yes_ask = getattr(q, "yes_ask", 0.0) or 0.0
        if yes_bid <= 0 or yes_ask <= 0:
            return False, "missing_quotes"
        if yes_ask <= 0:
            return False, "missing_quotes"
        spread = yes_ask - yes_bid
        if spread < 0 or spread > MAX_ENTRY_SPREAD:
            return False, "wide_spread"
        liquidity = getattr(q, "liquidity", 0.0) or 0.0
        min_liquidity = self.get_market_min_entry_liquidity(market)
        series_ticker = self.get_targeted_series_ticker(market)
        strict_orderbook_details = None
        strict_orderbook_ok = None
        if self.is_targeted_series_market(market):
            print(
                f"[LIVE-SELECT-DEBUG] targeted_liquidity ticker={ticker} "
                f"default={MIN_ENTRY_LIQUIDITY} targeted={min_liquidity} seen={liquidity}"
            )
        if liquidity <= min_liquidity:
            if self.is_targeted_series_market(market):
                strict_orderbook_ok, strict_orderbook_details = self.passes_targeted_orderbook_quality(market, q)
                field_source = "targeted_orderbook_quality"
                if should_trace_priority_family:
                    print(
                        f"[LIVE-SELECT-STRICT-LIQUIDITY-FIELDS] ticker={ticker} family={family} "
                        f"reported_liquidity={liquidity} threshold={min_liquidity} "
                        f"yes_top_size={(strict_orderbook_details or {}).get('yes_top_size')} "
                        f"no_top_size={(strict_orderbook_details or {}).get('no_top_size')} "
                        f"min_top_size={(strict_orderbook_details or {}).get('min_top_size')} "
                        f"spread={spread} source={field_source}"
                    )
                    print(
                        f"[LIVE-SELECT-STRICT-LIQUIDITY-FIX] ticker={ticker} family={family} "
                        f"yes_top_size={(strict_orderbook_details or {}).get('yes_top_size')} "
                        f"no_top_size={(strict_orderbook_details or {}).get('no_top_size')} "
                        f"field_source={field_source} pass={'yes' if strict_orderbook_ok else 'no'}"
                    )
                    print(
                        f"[LIVE-SELECT-STRICT-LIQUIDITY-CHECK] ticker={ticker} family={family} "
                        f"reported_liquidity={liquidity} threshold={min_liquidity} "
                        f"yes_top_size={(strict_orderbook_details or {}).get('yes_top_size')} "
                        f"no_top_size={(strict_orderbook_details or {}).get('no_top_size')} "
                        f"min_top_size={(strict_orderbook_details or {}).get('min_top_size')} "
                        f"pass={'yes' if strict_orderbook_ok else 'no'} "
                        f"reject_reason={'None' if strict_orderbook_ok else 'low_liquidity'} source={field_source}"
                    )
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_liquidity_gate ticker={ticker} "
                    f"result={'pass' if strict_orderbook_ok else 'fail'} seen={liquidity} threshold={min_liquidity} fallback={field_source}"
                )
                if not strict_orderbook_ok:
                    return False, "low_liquidity"
            else:
                if should_trace_priority_family:
                    liquidity_priority = self.get_market_liquidity_priority(market)
                    print(
                        f"[LIVE-SELECT-STRICT-LIQUIDITY-FIELDS] ticker={ticker} family={family} "
                        f"reported_liquidity={liquidity} threshold={min_liquidity} "
                        f"yes_top_size={liquidity_priority.get('yes_top_size')} no_top_size={liquidity_priority.get('no_top_size')} "
                        f"min_top_size={self.get_market_min_top_of_book_size(market)} spread={spread} source=strict_low_liquidity_gate"
                    )
                    print(
                        f"[LIVE-SELECT-STRICT-LIQUIDITY-CHECK] ticker={ticker} family={family} "
                        f"reported_liquidity={liquidity} threshold={min_liquidity} "
                        f"yes_top_size={liquidity_priority.get('yes_top_size')} no_top_size={liquidity_priority.get('no_top_size')} "
                        f"min_top_size={self.get_market_min_top_of_book_size(market)} pass=no reject_reason=low_liquidity "
                        f"source=strict_low_liquidity_gate"
                    )
                return False, "low_liquidity"
        else:
            if should_trace_priority_family:
                liquidity_priority = self.get_market_liquidity_priority(market)
                print(
                    f"[LIVE-SELECT-STRICT-LIQUIDITY-FIELDS] ticker={ticker} family={family} "
                    f"reported_liquidity={liquidity} threshold={min_liquidity} "
                    f"yes_top_size={liquidity_priority.get('yes_top_size')} no_top_size={liquidity_priority.get('no_top_size')} "
                    f"min_top_size={self.get_market_min_top_of_book_size(market)} spread={spread} source=strict_low_liquidity_gate"
                )
                print(
                    f"[LIVE-SELECT-STRICT-LIQUIDITY-CHECK] ticker={ticker} family={family} "
                    f"reported_liquidity={liquidity} threshold={min_liquidity} "
                    f"yes_top_size={liquidity_priority.get('yes_top_size')} no_top_size={liquidity_priority.get('no_top_size')} "
                    f"min_top_size={self.get_market_min_top_of_book_size(market)} pass=yes reject_reason=None "
                    f"source=strict_low_liquidity_gate"
                )
            if self.is_targeted_series_market(market):
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_liquidity_gate ticker={ticker} "
                    f"result=pass seen={liquidity} threshold={min_liquidity}"
                )
        if getattr(q, "volume", 0.0) <= MIN_ENTRY_VOLUME:
            return False, "low_volume"
        if yes_ask <= MIN_ENTRY_PRICE or yes_ask >= MAX_ENTRY_PRICE:
            return False, "unsafe_price"
        return True, None

    def get_market_family(self, market):
        ticker = str(market.get("ticker", "") or "").strip()
        if ticker:
            return self.get_market_group(ticker)
        event_ticker = str(market.get("event_ticker", "") or "").strip()
        if event_ticker:
            return self.get_market_group(event_ticker)
        return "UNKNOWN"

    def fetch_market_batch(self, cursor=None):
        params = {
            "limit": LIVE_SELECT_BATCH_SIZE,
            "status": "open",
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        print(f"[LIVE-SELECT-SCAN] request_params={params} mve_filter_applied=yes")
        return self.get_markets(
            limit=LIVE_SELECT_BATCH_SIZE,
            status="open",
            cursor=cursor,
            return_cursor=True,
            extra_params={"mve_filter": "exclude"},
        )

    def is_targeted_series_market(self, market):
        ticker = str(market.get("ticker", "") or "").strip()
        series_ticker = str(market.get("series_ticker", "") or "").strip()
        for target in TARGETED_SERIES_TICKERS + OPTIONAL_TARGETED_SERIES_TICKERS:
            if series_ticker == target or ticker.startswith(f"{target}-") or ticker == target:
                return True
        return False

    def get_targeted_series_ticker(self, market):
        ticker = str(market.get("ticker", "") or "").strip()
        series_ticker = str(market.get("series_ticker", "") or "").strip()
        for target in TARGETED_SERIES_TICKERS + OPTIONAL_TARGETED_SERIES_TICKERS:
            if series_ticker == target or ticker.startswith(f"{target}-") or ticker == target:
                return target
        return None

    def is_excluded_targeted_market(self, market):
        haystack = " ".join(
            [
                str(market.get("ticker", "") or ""),
                str(market.get("event_ticker", "") or ""),
                str(market.get("series_ticker", "") or ""),
                str(market.get("title", "") or ""),
                str(market.get("subtitle", "") or ""),
            ]
        ).upper()
        return any(keyword in haystack for keyword in TARGETED_SERIES_EXCLUDE_KEYWORDS)

    def get_market_min_entry_liquidity(self, market):
        series_ticker = self.get_targeted_series_ticker(market)
        if series_ticker and series_ticker in TARGETED_SERIES_MIN_LIQUIDITY:
            return TARGETED_SERIES_MIN_LIQUIDITY[series_ticker]
        return MIN_ENTRY_LIQUIDITY

    def get_market_min_top_of_book_size(self, market):
        series_ticker = self.get_targeted_series_ticker(market)
        if series_ticker and series_ticker in TARGETED_SERIES_MIN_TOP_OF_BOOK_SIZE:
            return TARGETED_SERIES_MIN_TOP_OF_BOOK_SIZE[series_ticker]
        return TARGETED_ORDERBOOK_MIN_SIZE

    def _parse_market_timestamp(self, value):
        if value in (None, ""):
            return None
        try:
            ts = float(value)
        except (TypeError, ValueError):
            text_value = str(value).strip()
            if not text_value:
                return None
            if text_value.endswith("Z"):
                text_value = text_value[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text_value)
            except ValueError:
                try:
                    dt = datetime.strptime(text_value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        if ts > 1e12:
            ts = ts / 1000.0
        return ts

    def get_market_event_timestamp_details(self, market, keys=None):
        timestamp_keys = keys or (
            "event_start_time",
            "event_start_time_ts",
            "expected_expiration_time",
            "expected_expiration_time_ts",
            "open_time",
            "open_time_ts",
            "close_time",
            "close_time_ts",
            "expiration_time",
            "expiration_time_ts",
            "settlement_time",
            "settlement_time_ts",
        )
        for key in timestamp_keys:
            value = market.get(key)
            ts = self._parse_market_timestamp(value)
            if ts is None:
                continue
            return {
                "source_key": key,
                "raw_value": value,
                "timestamp": ts,
                "event_ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        return {
            "source_key": None,
            "raw_value": None,
            "timestamp": None,
            "event_ts": None,
        }

    def get_targeted_contract_window_rank(self, market, max_minutes_to_event=None):
        ticker = str(market.get("ticker", "") or "").strip()
        now_ts = time.time()
        timestamp_details = self.get_market_event_timestamp_details(market)
        event_ts = timestamp_details.get("timestamp")
        minutes_to_event = None if event_ts is None else (event_ts - now_ts) / 60.0
        if minutes_to_event is None:
            return {
                "ticker": ticker,
                "minutes_to_event": None,
                "distance": float("inf"),
                "status": "no_timestamp",
            }
        if minutes_to_event < MIN_MINUTES_TO_EVENT:
            return {
                "ticker": ticker,
                "minutes_to_event": minutes_to_event,
                "distance": MIN_MINUTES_TO_EVENT - minutes_to_event,
                "status": "too_close",
            }
        if max_minutes_to_event is None:
            max_minutes_to_event = self.get_broad_discovery_max_minutes_to_event()
        if minutes_to_event > max_minutes_to_event:
            return {
                "ticker": ticker,
                "minutes_to_event": minutes_to_event,
                "distance": minutes_to_event - max_minutes_to_event,
                "status": "too_early",
            }
        return {
            "ticker": ticker,
            "minutes_to_event": minutes_to_event,
            "distance": max(0.0, minutes_to_event - MIN_MINUTES_TO_EVENT),
            "status": "target_window",
        }

    def fetch_targeted_series_markets(self):
        markets = []
        seen_tickers = set()
        total_found = 0
        targeted_series = self.get_prioritized_targeted_series()
        targeted_max_minutes_to_event = self.get_broad_discovery_max_minutes_to_event()
        print(f"[LIVE-SELECT-DEBUG] targeted_mode_enabled={'yes' if ENABLE_TARGETED_SERIES_MODE else 'no'}")
        print(f"[LIVE-SELECT-DEBUG] targeted_series_scanned={targeted_series}")
        print(f"[LIVE-SELECT-DEBUG] targeted_series_added={TARGETED_DISCOVERY_EXPANSION_SERIES_TICKERS}")
        print("[LIVE-SELECT-DEBUG] targeted_fetch_params mve_filter=exclude")
        print(
            f"[LIVE-SELECT-DEBUG] targeted_effective_max_minutes={targeted_max_minutes_to_event}"
        )
        total_selected_after_cap = 0
        total_rejected_after_cap = 0
        for series_ticker in targeted_series:
            config = TARGETED_SERIES_PRIORITY_WINDOWS.get(series_ticker, {})
            print(
                f"[LIVE-SELECT-DEBUG] targeted_priority family={series_ticker} "
                f"reason={config.get('reason', 'default_order')}"
            )
            series_markets = self.get_markets(
                limit=LIVE_SELECT_BATCH_SIZE,
                status="open",
                extra_params={"series_ticker": series_ticker, "mve_filter": "exclude"},
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_family_scanned family={series_ticker} "
                f"fetched={len(series_markets)}"
            )
            total_found += len(series_markets)
            for market in series_markets:
                ticker = str(market.get("ticker", "") or "").strip()
                if self.is_excluded_targeted_market(market):
                    print(
                        f"[LIVE-SELECT-DEBUG] targeted_reject family={series_ticker} "
                        f"ticker={ticker} reason=excluded_targeted_category"
                    )
                    continue
                if ticker and ticker in seen_tickers:
                    continue
                if ticker:
                    seen_tickers.add(ticker)
                markets.append(market)
            family_markets = [market for market in markets if self.get_market_family(market) == series_ticker]
            ranked_family_entries = sorted(
                [
                    (
                        market,
                        self.get_targeted_contract_window_rank(
                            market,
                            max_minutes_to_event=targeted_max_minutes_to_event,
                        ),
                    )
                    for market in family_markets
                ],
                key=lambda item: (
                    item[1]["distance"],
                    0 if item[1]["status"] == "target_window" else 1,
                    item[1]["minutes_to_event"]
                    if item[1]["minutes_to_event"] is not None
                    else float("inf"),
                ),
            )
            previous_distance_threshold = float("inf")
            if ranked_family_entries:
                cutoff_index = min(len(ranked_family_entries), MAX_TARGETED_CONTRACTS_PER_FAMILY) - 1
                previous_distance_threshold = ranked_family_entries[cutoff_index][1]["distance"]
            new_distance_threshold = previous_distance_threshold
            if previous_distance_threshold != float("inf"):
                new_distance_threshold = previous_distance_threshold + TARGETED_CONTRACT_DISTANCE_BUFFER_MINUTES
            widened_family_entries = []
            additional_distance_pass_count = 0
            for index, (market, rank_info) in enumerate(ranked_family_entries, start=1):
                within_original_cap = index <= MAX_TARGETED_CONTRACTS_PER_FAMILY
                within_distance_buffer = rank_info["distance"] <= new_distance_threshold
                if within_original_cap or within_distance_buffer:
                    widened_family_entries.append((market, rank_info))
                    if (not within_original_cap) and within_distance_buffer:
                        additional_distance_pass_count += 1
            ranked_family_entries = sorted(
                widened_family_entries,
                key=lambda item: (
                    0 if item[1]["status"] == "target_window" else 1,
                    0 if self.compute_entry_presolve_score(item[0])["has_top_book"] else 1,
                    -self.compute_entry_presolve_score(item[0])["score"],
                    item[1]["distance"],
                    item[1]["minutes_to_event"]
                    if item[1]["minutes_to_event"] is not None
                    else float("inf"),
                ),
            )
            selected_tickers = set()
            selected_family_markets = []
            rejected_family_markets = 0
            print(
                f"[LIVE-SELECT-DEBUG] targeted_contract_cap family={series_ticker} "
                f"previous_cap={BASE_MAX_TARGETED_CONTRACTS_PER_FAMILY} "
                f"new_cap={MAX_TARGETED_CONTRACTS_PER_FAMILY}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_contract_distance_threshold family={series_ticker} "
                f"previous_threshold={previous_distance_threshold} new_threshold={new_distance_threshold} "
                f"additional_candidates_pass={additional_distance_pass_count}"
            )
            for index, (market, rank_info) in enumerate(ranked_family_entries, start=1):
                selected = index <= MAX_TARGETED_CONTRACTS_PER_FAMILY
                passed_due_to_adjustment = rank_info["distance"] > previous_distance_threshold and rank_info["distance"] <= new_distance_threshold
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_contract_rank family={series_ticker} "
                    f"ticker={rank_info['ticker']} minutes_to_event={rank_info['minutes_to_event']} "
                    f"distance={rank_info['distance']} status={rank_info['status']} "
                    f"distance_threshold={new_distance_threshold} adjustment_pass={'yes' if passed_due_to_adjustment else 'no'} "
                    f"selected={'yes' if selected else 'no'}"
                )
                if selected:
                    selected_tickers.add(rank_info["ticker"])
                    selected_family_markets.append(market)
                else:
                    rejected_family_markets += 1
                    print(
                        f"[LIVE-SELECT-DEBUG] targeted_contract_skip family={series_ticker} "
                        f"ticker={rank_info['ticker']} distance={rank_info['distance']} "
                        f"reason=outside_nearest_contract_cap"
                    )
            total_selected_after_cap += len(selected_family_markets)
            total_rejected_after_cap += rejected_family_markets
            print(
                f"[LIVE-SELECT-DEBUG] targeted_family_selected family={series_ticker} "
                f"selected={len(selected_family_markets)} cap={MAX_TARGETED_CONTRACTS_PER_FAMILY}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_contract_cap_result family={series_ticker} "
                f"kept={len(selected_family_markets)} rejected={rejected_family_markets} "
                f"additional_candidates_pass={additional_distance_pass_count} "
                f"final_selected_candidates={len(selected_family_markets)}"
            )
            markets = [
                market for market in markets
                if self.get_market_family(market) != series_ticker
                or str(market.get("ticker", "") or "").strip() in selected_tickers
            ]
        print(f"[LIVE-SELECT-DEBUG] targeted_markets_found={total_found}")
        print(f"[LIVE-SELECT-DEBUG] total_markets_fetched_before_filter={total_found}")
        print(f"[LIVE-SELECT-DEBUG] total_markets_after_initial_fetch={len(markets)}")
        print(
            f"[LIVE-SELECT-DEBUG] targeted_contract_cap_summary previous_cap={BASE_MAX_TARGETED_CONTRACTS_PER_FAMILY} "
            f"new_cap={MAX_TARGETED_CONTRACTS_PER_FAMILY} kept={total_selected_after_cap} "
            f"rejected={total_rejected_after_cap} final_selected_candidates={len(markets)}"
        )
        return markets

    def get_prioritized_targeted_series(self):
        utc_hour = time.gmtime().tm_hour
        schedule = TARGETED_FAMILY_PRIORITY_SCHEDULES[-1]
        for candidate_schedule in TARGETED_FAMILY_PRIORITY_SCHEDULES:
            hours_utc = tuple(candidate_schedule.get("hours_utc", ()))
            if not hours_utc or utc_hour in hours_utc:
                schedule = candidate_schedule
                break
        base_order = list(schedule.get("ordered", TARGETED_SERIES_TICKERS + OPTIONAL_TARGETED_SERIES_TICKERS))
        print(
            f"[LIVE-SELECT-DEBUG] targeted_priority_bucket hour_utc={utc_hour} "
            f"bucket={schedule.get('name')} reason={schedule.get('reason')} base_order={base_order}"
        )
        print(f"[LIVE-SELECT-DEBUG] high_liquidity_priority_families={list(HIGH_LIQUIDITY_PRIORITY_FAMILIES)}")
        print(f"[LIVE-SELECT-DEBUG] deprioritized_families={list(DISCOVERY_DEPRIORITIZED_FAMILIES)}")
        ordered = []
        deprioritized = []
        suppressed = []
        adaptation_details = []
        for series_ticker in base_order:
            hour_stats = self.targeted_family_hourly_stats.get(series_ticker, {}).get(utc_hour, {})
            dead_book_cycles = int(hour_stats.get("dead_book_cycles", 0) or 0)
            in_window_cycles = int(hour_stats.get("in_window_cycles", 0) or 0)
            non_empty_top_book_cycles = int(hour_stats.get("non_empty_top_book_cycles", 0) or 0)
            quote_alive_cycles = int(hour_stats.get("quote_alive_cycles", 0) or 0)
            prefilter_survivor_cycles = int(hour_stats.get("prefilter_survivor_cycles", 0) or 0)
            print(
                f"[LIVE-SELECT-TARGETED-SUPPRESS-CHECK] family={series_ticker} hour_utc={utc_hour} "
                f"dead_book_cycles={dead_book_cycles} in_window_cycles={in_window_cycles} "
                f"non_empty_top_book_cycles={non_empty_top_book_cycles} quote_alive_cycles={quote_alive_cycles} "
                f"prefilter_survivor_cycles={prefilter_survivor_cycles}"
            )
            dead_book_suppressed = (
                series_ticker in OPTIONAL_TARGETED_SERIES_TICKERS
                and dead_book_cycles >= TARGETED_DEAD_BOOK_MIN_CYCLES
                and in_window_cycles >= TARGETED_DEAD_BOOK_MIN_CYCLES
                and non_empty_top_book_cycles == 0
                and prefilter_survivor_cycles == 0
            )
            dead_book_deprioritized = (
                series_ticker == "KXBTC15M"
                and dead_book_cycles >= BTC15M_DEAD_BOOK_MIN_CYCLES
                and in_window_cycles >= BTC15M_DEAD_BOOK_MIN_CYCLES
                and non_empty_top_book_cycles == 0
                and prefilter_survivor_cycles == 0
            )
            adaptation_details.append(
                (series_ticker, dead_book_cycles, in_window_cycles, non_empty_top_book_cycles, quote_alive_cycles, prefilter_survivor_cycles)
            )
            if dead_book_suppressed:
                print(
                    f"[LIVE-SELECT-FAMILY-SUPPRESS] family={series_ticker} cycle_id={self._get_phase55_cycle_id()} "
                    f"suppress_reason=targeted_dead_book_hour action=applied dead_book_cycles={dead_book_cycles} "
                    f"in_window_cycles={in_window_cycles} non_empty_top_book_cycles={non_empty_top_book_cycles} "
                    f"quote_alive_cycles={quote_alive_cycles} prefilter_survivor_cycles={prefilter_survivor_cycles}"
                )
                suppressed.append(series_ticker)
                continue
            if dead_book_deprioritized:
                print(
                    f"[LIVE-SELECT-FAMILY-SUPPRESS] family={series_ticker} cycle_id={self._get_phase55_cycle_id()} "
                    f"suppress_reason=targeted_dead_book_hour action=deprioritized dead_book_cycles={dead_book_cycles} "
                    f"in_window_cycles={in_window_cycles} non_empty_top_book_cycles={non_empty_top_book_cycles} "
                    f"quote_alive_cycles={quote_alive_cycles} prefilter_survivor_cycles={prefilter_survivor_cycles}"
                )
                deprioritized.append(series_ticker)
            else:
                ordered.append(series_ticker)
        ordered = ordered + deprioritized
        self.last_targeted_priority_adaptation = {
            "hour_utc": utc_hour,
            "base_order": base_order,
            "adapted_order": list(ordered),
            "suppressed_families": list(suppressed),
            "deprioritized_families": list(deprioritized),
            "details": adaptation_details,
        }
        print(
            f"[LIVE-SELECT-DEBUG] targeted_priority_order hour_utc={utc_hour} ordered={ordered} "
            f"suppressed_dead_book_families={suppressed} deprioritized_dead_book_families={deprioritized}"
        )
        return ordered

    def fetch_broad_discovery_markets(self):
        markets = []
        seen_tickers = set()
        cursor = None
        total_examined = 0
        in_window_seen = 0
        batches_fetched = 0
        for batch in range(1, MAX_SCAN_BATCHES + 1):
            batch_markets, next_cursor = self.fetch_market_batch(cursor=cursor)
            print(f"[LIVE-SELECT-SCAN] batch={batch} fetched={len(batch_markets)}")
            batches_fetched = batch
            total_examined += len(batch_markets)
            for market in batch_markets:
                ticker = market.get("ticker", "")
                if ticker and ticker in seen_tickers:
                    continue
                if ticker:
                    seen_tickers.add(ticker)
                markets.append(market)
                minutes_to_event = self.get_minutes_to_event(market)
                if (
                    minutes_to_event is not None
                    and MIN_MINUTES_TO_EVENT
                    <= minutes_to_event
                    <= self.get_broad_discovery_max_minutes_to_event()
                ):
                    in_window_seen += 1
            if not next_cursor or total_examined >= LIVE_SELECT_MAX_MARKETS:
                break
            if in_window_seen >= MIN_MARKETS_IN_WINDOW_TARGET:
                break
            cursor = next_cursor
        print(
            f"[LIVE-SELECT-SCAN] batches_fetched={batches_fetched} total_scanned={total_examined} "
            f"in_window_seen={in_window_seen} target={MIN_MARKETS_IN_WINDOW_TARGET}"
        )
        print("[LIVE-SELECT-DEBUG] broad_fetch_params mve_filter=exclude")
        print(f"[LIVE-SELECT-DEBUG] total_markets_fetched_before_filter={total_examined}")
        print(f"[LIVE-SELECT-DEBUG] total_markets_after_initial_fetch={len(markets)}")
        return markets

    def get_live_market_orderbook(self, ticker):
        path = f"/trade-api/v2/markets/{ticker}/orderbook"
        url = self.build_live_request_url(path)
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()

    def _extract_orderbook_top_level(self, levels):
        if not isinstance(levels, list) or not levels:
            return {"price": None, "size": 0.0}
        level = levels[0]
        if isinstance(level, dict):
            return {
                "price": self.to_float(
                    level.get("price")
                    or level.get("price_dollars")
                    or level.get("yes_price")
                    or level.get("no_price"),
                    None,
                ),
                "size": self.to_float(
                    level.get("size")
                    or level.get("count")
                    or level.get("quantity")
                    or level.get("contracts"),
                    0.0,
                ),
            }
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            return {
                "price": self.to_float(level[0], None),
                "size": self.to_float(level[1], 0.0),
            }
        return {"price": None, "size": 0.0}

    def _get_liquidity_trace_origin(self, market):
        for key in ("selection_origin", "source", "origin", "discovery_source", "source_stage", "path"):
            value = market.get(key) if isinstance(market, dict) else None
            if value not in (None, ""):
                return value
        return "unknown"

    def _should_trace_priority_topbook_family(self, family):
        return family in {"KXBTC15M", "KXNBA1HTOTAL", "KXNBA1HSPREAD", "KXNBASPREAD"}

    def _collect_topbook_candidate_state(self, market):
        state = {
            "raw_orderbook_container_present": False,
            "raw_field_names": [],
            "yes_no_arrays_exist": False,
            "size_fields_exist": False,
            "best_bid_ask_fields_exist": False,
            "mapped_topbook_fields_present": False,
            "non_zero_size_fields_present": False,
            "relevant_keys": [],
        }
        if not isinstance(market, dict):
            return state
        relevant_keys = [
            key for key in (
                "orderbook", "yes", "no", "yes_orders", "no_orders",
                "yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size",
                "best_yes_bid_size", "best_yes_ask_size", "best_no_bid_size", "best_no_ask_size",
                "yes_size", "no_size", "bid_size", "ask_size",
                "best_yes_bid", "best_yes_ask", "best_no_bid", "best_no_ask",
                "yes_top_size", "no_top_size", "yes_top_price", "no_top_price",
                "liquidity", "liquidity_dollars",
            )
            if market.get(key) not in (None, "", [], {})
        ]
        state["relevant_keys"] = relevant_keys
        container = market.get("orderbook") if isinstance(market.get("orderbook"), dict) else None
        state["raw_orderbook_container_present"] = bool(container)
        state["yes_no_arrays_exist"] = bool(
            market.get("yes") or market.get("no") or market.get("yes_orders") or market.get("no_orders")
            or (isinstance(container, dict) and (container.get("yes") or container.get("no") or container.get("yes_orders") or container.get("no_orders")))
        )
        size_field_names = [
            key for key in (
                "yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size",
                "best_yes_bid_size", "best_yes_ask_size", "best_no_bid_size", "best_no_ask_size",
                "yes_size", "no_size", "bid_size", "ask_size",
            )
            if market.get(key) not in (None, "", [], {})
        ]
        state["raw_field_names"] = size_field_names + (["orderbook"] if container else [])
        state["size_fields_exist"] = bool(size_field_names)
        state["best_bid_ask_fields_exist"] = any(
            market.get(key) not in (None, "", [], {})
            for key in ("best_yes_bid", "best_yes_ask", "best_no_bid", "best_no_ask")
        )
        mapped_size_values = [
            self.to_float(market.get(key), None) for key in ("yes_top_size", "no_top_size")
        ]
        state["mapped_topbook_fields_present"] = any(value is not None for value in mapped_size_values) or any(
            market.get(key) not in (None, "", [], {}) for key in ("yes_top_price", "no_top_price")
        )
        raw_size_values = [self.to_float(market.get(key), None) for key in size_field_names]
        state["non_zero_size_fields_present"] = any(
            value is not None and value > 0 for value in mapped_size_values + raw_size_values
        )
        return state

    def _emit_priority_upstream_trace(self, prefix, market, stage_name, **extra_fields):
        family = self.get_market_family(market) if isinstance(market, dict) else "UNKNOWN"
        if not self._should_trace_priority_topbook_family(family):
            return
        ticker = str((market or {}).get("ticker", "") or "").strip() if isinstance(market, dict) else ""
        state = self._collect_topbook_candidate_state(market)
        derived_only = (
            state.get("mapped_topbook_fields_present")
            and not state.get("raw_orderbook_container_present")
            and not state.get("yes_no_arrays_exist")
            and not state.get("size_fields_exist")
        )
        extra_parts = []
        for key, value in extra_fields.items():
            if value is None:
                continue
            extra_parts.append(f"{key}={value}")
        print(
            f"{prefix} ticker={ticker} family={family} stage={stage_name} "
            f"raw_orderbook_present={'yes' if state.get('raw_orderbook_container_present') else 'no'} "
            f"topbook_keys={state.get('relevant_keys')} "
            f"raw_field_names={state.get('raw_field_names')} "
            f"yes_no_arrays_present={'yes' if state.get('yes_no_arrays_exist') else 'no'} "
            f"mapped_topbook_present={'yes' if state.get('mapped_topbook_fields_present') else 'no'} "
            f"non_zero_size_fields={'yes' if state.get('non_zero_size_fields_present') else 'no'} "
            f"derived_summary_only={'yes' if derived_only else 'no'}"
            f"{' ' + ' '.join(extra_parts) if extra_parts else ''}"
        )

    def _emit_priority_downstream_trace(self, prefix, market=None, family=None, **extra_fields):
        trace_family = family or (self.get_market_family(market) if isinstance(market, dict) else "UNKNOWN")
        if not self._should_trace_priority_topbook_family(trace_family):
            return
        ticker = str((market or {}).get("ticker", "") or "").strip() if isinstance(market, dict) else str(extra_fields.pop("ticker", "") or "").strip()
        extra_parts = []
        for key, value in extra_fields.items():
            if value is None:
                continue
            extra_parts.append(f"{key}={value}")
        print(
            f"{prefix} ticker={ticker} family={trace_family}"
            f"{' ' + ' '.join(extra_parts) if extra_parts else ''}"
        )

    def _get_raw_liquidity_candidate_fields(self, market):
        raw_keys = (
            "yes_bid", "yes_ask", "no_bid", "no_ask",
            "best_yes_bid", "best_yes_ask", "best_no_bid", "best_no_ask",
            "yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size",
            "best_yes_bid_size", "best_yes_ask_size", "best_no_bid_size", "best_no_ask_size",
            "yes_size", "no_size", "bid_size", "ask_size",
            "volume", "liquidity", "orderbook", "yes", "no", "yes_orders", "no_orders",
            "quote_age", "quote_ts", "quote_timestamp", "updated_at", "updated_at_ts",
            "last_updated", "last_updated_ts", "timestamp", "ts", "age_seconds", "staleness_seconds",
        )
        raw_fields = {}
        if not isinstance(market, dict):
            return raw_fields
        for key in raw_keys:
            value = market.get(key)
            if value not in (None, "", [], {}):
                raw_fields[key] = value
        return raw_fields

    def _extract_non_targeted_topbook_snapshot(self, market):
        snapshot = {
            "raw_orderbook_container_present": False,
            "raw_field_names": [],
            "raw_yes_sizes": {},
            "raw_no_sizes": {},
            "yes_top_size": None,
            "no_top_size": None,
            "yes_bid_size": None,
            "yes_ask_size": None,
            "no_bid_size": None,
            "no_ask_size": None,
            "source_path": "none",
            "failure_stage": None,
            "failure_reason": None,
        }
        if not isinstance(market, dict):
            snapshot["failure_stage"] = "raw_missing"
            snapshot["failure_reason"] = "market_not_dict"
            return snapshot
        container = market.get("orderbook") if isinstance(market.get("orderbook"), dict) else None
        fp_container = market.get("orderbook_fp") if isinstance(market.get("orderbook_fp"), dict) else None
        yes_levels = []
        no_levels = []
        if isinstance(container, dict):
            yes_levels = container.get("yes") or container.get("yes_orders") or []
            no_levels = container.get("no") or container.get("no_orders") or []
            snapshot["raw_orderbook_container_present"] = True
            snapshot["source_path"] = "orderbook_container"
        elif isinstance(fp_container, dict):
            yes_levels = fp_container.get("yes_dollars") or fp_container.get("yes") or []
            no_levels = fp_container.get("no_dollars") or fp_container.get("no") or []
            snapshot["raw_orderbook_container_present"] = True
            snapshot["source_path"] = "orderbook_fp_container"
        elif (
            market.get("yes") or market.get("no") or market.get("yes_orders") or market.get("no_orders")
            or market.get("yes_dollars") or market.get("no_dollars")
        ):
            yes_levels = market.get("yes") or market.get("yes_orders") or market.get("yes_dollars") or []
            no_levels = market.get("no") or market.get("no_orders") or market.get("no_dollars") or []
            snapshot["raw_orderbook_container_present"] = True
            snapshot["source_path"] = "root_orderbook"
        yes_top = self._extract_orderbook_top_level(yes_levels)
        no_top = self._extract_orderbook_top_level(no_levels)
        yes_size_fields = (
            "yes_bid_size", "best_yes_bid_size", "yes_size", "bid_size",
        )
        no_size_fields = (
            "no_bid_size", "best_no_bid_size", "no_size", "ask_size",
        )
        for key in yes_size_fields:
            value = self.to_float(market.get(key), None)
            if value is not None:
                snapshot["raw_yes_sizes"][key] = value
        for key in no_size_fields:
            value = self.to_float(market.get(key), None)
            if value is not None:
                snapshot["raw_no_sizes"][key] = value
        raw_names = list(snapshot["raw_yes_sizes"].keys()) + list(snapshot["raw_no_sizes"].keys())
        if isinstance(container, dict):
            raw_names.append("orderbook")
        if isinstance(fp_container, dict):
            raw_names.append("orderbook_fp")
        if market.get("yes_dollars") not in (None, "", [], {}):
            raw_names.append("yes_dollars")
        if market.get("no_dollars") not in (None, "", [], {}):
            raw_names.append("no_dollars")
        snapshot["raw_field_names"] = sorted(set(raw_names))
        if yes_top.get("size", 0.0) > 0 or no_top.get("size", 0.0) > 0:
            snapshot["yes_top_size"] = yes_top.get("size", 0.0)
            snapshot["no_top_size"] = no_top.get("size", 0.0)
        else:
            yes_candidates = list(snapshot["raw_yes_sizes"].values())
            no_candidates = list(snapshot["raw_no_sizes"].values())
            if yes_candidates:
                snapshot["yes_top_size"] = max(yes_candidates)
            if no_candidates:
                snapshot["no_top_size"] = max(no_candidates)
            if yes_candidates or no_candidates:
                snapshot["source_path"] = "raw_size_fields"
        snapshot["yes_bid_size"] = snapshot["yes_top_size"]
        snapshot["no_bid_size"] = snapshot["no_top_size"]
        if snapshot["yes_top_size"] is None and snapshot["no_top_size"] is None:
            if snapshot["raw_orderbook_container_present"] or snapshot["raw_field_names"]:
                snapshot["failure_stage"] = "mapping_missing"
                snapshot["failure_reason"] = "no_size_fields_mapped"
            else:
                snapshot["failure_stage"] = "raw_missing"
                snapshot["failure_reason"] = "no_orderbook_or_size_fields"
        elif (snapshot["yes_top_size"] or 0.0) == 0.0 and (snapshot["no_top_size"] or 0.0) == 0.0:
            snapshot["failure_stage"] = "zero_sizes"
            snapshot["failure_reason"] = "mapped_sizes_zero"
        return snapshot

    def _emit_strict_liquidity_trace(self, market, q, family, validation_snapshot, orderbook_snapshot, reject_reason):
        ticker = getattr(q, "ticker", "") or str((market or {}).get("ticker", "") or "").strip()
        print(
            f"[LIVE-SELECT-LIQUIDITY-TRACE] ticker={ticker} family={family} "
            f"origin={self._get_liquidity_trace_origin(market)} targeted={'yes' if self.is_targeted_series_market(market) else 'no'} "
            f"spread={validation_snapshot.get('spread')} yes_bid={validation_snapshot.get('yes_bid')} "
            f"yes_ask={validation_snapshot.get('yes_ask')} no_bid={validation_snapshot.get('no_bid')} "
            f"no_ask={validation_snapshot.get('no_ask')} yes_top_size={orderbook_snapshot.get('yes_top_size')} "
            f"no_top_size={orderbook_snapshot.get('no_top_size')} yes_top_price={orderbook_snapshot.get('yes_top_price')} "
            f"no_top_price={orderbook_snapshot.get('no_top_price')} min_top_size={orderbook_snapshot.get('min_top_size')} "
            f"top_book_bucket={orderbook_snapshot.get('top_book_bucket')} orderbook_source={orderbook_snapshot.get('orderbook_source')} "
            f"raw_yes_top_level={orderbook_snapshot.get('yes_raw_top_level')} raw_no_top_level={orderbook_snapshot.get('no_raw_top_level')} "
            f"size_fallback_fields=size|count|quantity|contracts price_fallback_fields=price|price_dollars|yes_price|no_price "
            f"candidate_raw_fields={self._get_raw_liquidity_candidate_fields(market)} reject_reason={reject_reason}"
        )

    def _classify_targeted_orderbook_bucket(self, yes_top_size, no_top_size, min_top_size, spread):
        if spread > MAX_ENTRY_SPREAD:
            return "spread_fail"
        if yes_top_size <= 0 and no_top_size <= 0:
            return "both_sides_zero"
        if yes_top_size <= 0:
            return "yes_zero_only"
        if no_top_size <= 0:
            return "no_zero_only"
        if yes_top_size < min_top_size or no_top_size < min_top_size:
            return "top_size_fail"
        return "pass"

    def get_live_market_detail(self, ticker):
        path = f"/trade-api/v2/markets/{ticker}"
        url = self.build_live_request_url(path) if globals().get("LIVE_MODE", False) else f"{BASE_URL}/markets/{ticker}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return payload.get("market", payload) if isinstance(payload, dict) else {}

    def _hydrate_targeted_zero_book_from_market_detail(self, market, spread):
        ticker = str(market.get("ticker", "") or "").strip()
        min_top_size = self.get_market_min_top_of_book_size(market)
        detail = self.get_live_market_detail(ticker)
        if not isinstance(detail, dict) or not detail:
            return None, "market_detail_empty"
        container = detail.get("orderbook") if isinstance(detail.get("orderbook"), dict) else None
        if isinstance(container, dict):
            yes_levels = container.get("yes") or container.get("yes_orders") or []
            no_levels = container.get("no") or container.get("no_orders") or []
            source = "market_detail.orderbook"
        else:
            yes_levels = detail.get("yes") if isinstance(detail, dict) else []
            no_levels = detail.get("no") if isinstance(detail, dict) else []
            source = "market_detail.root"
        if not isinstance(yes_levels, list):
            yes_levels = []
        if not isinstance(no_levels, list):
            no_levels = []
        if not yes_levels and not no_levels:
            return None, "market_detail_no_orderbook_levels"
        yes_top = self._extract_orderbook_top_level(yes_levels)
        no_top = self._extract_orderbook_top_level(no_levels)
        hydrated = {
            "yes_top_price": yes_top["price"],
            "yes_top_size": yes_top["size"],
            "no_top_price": no_top["price"],
            "no_top_size": no_top["size"],
            "min_top_size": min_top_size,
            "orderbook_source": source,
            "yes_raw_top_level": yes_levels[0] if yes_levels else None,
            "no_raw_top_level": no_levels[0] if no_levels else None,
            "top_book_bucket": self._classify_targeted_orderbook_bucket(
                yes_top["size"],
                no_top["size"],
                min_top_size,
                spread,
            ),
        }
        self.targeted_orderbook_cache[ticker] = hydrated
        return hydrated, source

    def _hydrate_market_snapshot_from_market_detail(self, market):
        ticker = str((market or {}).get("ticker", "") or "").strip()
        family = self.get_market_family(market) if isinstance(market, dict) else "UNKNOWN"
        event_ticker = str((market or {}).get("event_ticker", "") or "").strip()
        source_path = "market_detail_pre_prefilter_for_broad_missing_quotes"
        if not ticker:
            print(
                f"[LIVE-SELECT-HYDRATION-REQUEST] ticker={ticker} event_ticker={event_ticker} "
                f"endpoint=trade-api/v2/markets/{{ticker}} attempted=no source_path={source_path}"
            )
            print(
                f"[LIVE-SELECT-HYDRATION-FAIL] ticker={ticker} event_ticker={event_ticker} "
                f"failure_stage=request reason=missing_ticker source_path={source_path}"
            )
            print(
                f"[LIVE-SELECT-TOPBOOK-SOURCE-FAIL] ticker={ticker} family={family} expected_source=market_detail_then_orderbook "
                f"actual_source=none reason=missing_ticker"
            )
            return market, "missing_ticker"
        path = f"/trade-api/v2/markets/{ticker}"
        url = self.build_live_request_url(path) if globals().get("LIVE_MODE", False) else f"{BASE_URL}/markets/{ticker}"
        print(
            f"[LIVE-SELECT-HYDRATION-REQUEST] ticker={ticker} event_ticker={event_ticker} "
            f"endpoint={path} attempted=yes source_path={source_path}"
        )
        try:
            response = requests.get(url, timeout=10)
            status_code = getattr(response, "status_code", "unknown")
            response.raise_for_status()
        except Exception as exc:
            print(
                f"[LIVE-SELECT-HYDRATION-FAIL] ticker={ticker} event_ticker={event_ticker} "
                f"failure_stage=request reason={exc.__class__.__name__} source_path={source_path}"
            )
            print(
                f"[LIVE-SELECT-TOPBOOK-SOURCE-FAIL] ticker={ticker} family={family} expected_source=market_detail_then_orderbook "
                f"actual_source=request_error exact_reason={exc.__class__.__name__}"
            )
            return market, f"request_error:{exc.__class__.__name__}"
        payload = response.json() if getattr(response, "content", b"") else {}
        detail = payload.get("market", payload) if isinstance(payload, dict) else {}
        top_level_fields = sorted(payload.keys())[:12] if isinstance(payload, dict) else []
        detail_fields = sorted(detail.keys())[:12] if isinstance(detail, dict) else []
        has_orderbook_container = isinstance(detail.get("orderbook"), dict) if isinstance(detail, dict) else False
        has_root_yes = bool(detail.get("yes") or detail.get("yes_orders")) if isinstance(detail, dict) else False
        has_root_no = bool(detail.get("no") or detail.get("no_orders")) if isinstance(detail, dict) else False
        print(
            f"[LIVE-SELECT-HYDRATION-RESPONSE] ticker={ticker} event_ticker={event_ticker} status={status_code} "
            f"response_present={'yes' if bool(detail) else 'no'} payload_fields={top_level_fields} detail_fields={detail_fields} "
            f"orderbook_container_present={'yes' if has_orderbook_container else 'no'} "
            f"expected_orderbook_fields_present={'yes' if (has_orderbook_container or has_root_yes or has_root_no) else 'no'}"
        )
        if not isinstance(detail, dict) or not detail:
            print(
                f"[LIVE-SELECT-HYDRATION-PARSE] ticker={ticker} event_ticker={event_ticker} parser_accepted=no "
                f"parsed_yes_bid=None parsed_yes_ask=None parsed_no_bid=None parsed_no_ask=None reason=market_detail_empty"
            )
            print(
                f"[LIVE-SELECT-HYDRATION-FAIL] ticker={ticker} event_ticker={event_ticker} "
                f"failure_stage=response reason=market_detail_empty source_path={source_path}"
            )
            print(
                f"[LIVE-SELECT-TOPBOOK-SOURCE-FAIL] ticker={ticker} family={family} expected_source=market_detail_then_orderbook "
                f"actual_source=market_detail exact_reason=market_detail_empty"
            )
            return market, "market_detail_empty"
        hydrated_market = dict(market)
        copy_keys = (
            "yes_bid", "yes_ask", "no_bid", "no_ask",
            "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars",
            "best_yes_bid", "best_yes_ask", "best_no_bid", "best_no_ask",
            "last_price", "last_price_dollars", "volume", "volume_fp",
            "liquidity", "liquidity_dollars", "orderbook", "yes", "no",
            "yes_orders", "no_orders", "updated_at", "updated_at_ts",
            "last_updated", "last_updated_ts", "quote_timestamp", "quote_ts",
            "timestamp", "ts",
        )
        copied_keys = []
        for key in copy_keys:
            value = detail.get(key)
            if value not in (None, "", [], {}):
                hydrated_market[key] = value
                copied_keys.append(key)
        source = "market_detail_snapshot"
        source_reason = "copied_market_detail_fields_only"
        if has_orderbook_container:
            source = "market_detail.orderbook"
            source_reason = "market_detail_contains_orderbook_container"
        elif has_root_yes or has_root_no:
            source = "market_detail.root"
            source_reason = "market_detail_contains_root_orderbook_levels"
        else:
            print(
                f"[LIVE-SELECT-TOPBOOK-SOURCE] ticker={ticker} family={family} source_path=market_detail why_chosen={source_reason} "
                f"raw_payload_source_exists=no"
            )
            try:
                orderbook_payload = self.get_live_market_orderbook(ticker)
            except Exception as exc:
                print(
                    f"[LIVE-SELECT-TOPBOOK-SOURCE-FAIL] ticker={ticker} family={family} expected_source=orderbook_endpoint "
                    f"actual_source=market_detail exact_reason=orderbook_request_error:{exc.__class__.__name__}"
                )
                orderbook_payload = None
            container = orderbook_payload.get("orderbook") if isinstance(orderbook_payload, dict) and isinstance(orderbook_payload.get("orderbook"), dict) else None
            if isinstance(container, dict):
                hydrated_market["orderbook"] = container
                hydrated_market["yes"] = container.get("yes") or container.get("yes_orders") or []
                hydrated_market["no"] = container.get("no") or container.get("no_orders") or []
                source = "orderbook_endpoint.container"
                source_reason = "market_detail_missing_size_payload_fallback_to_orderbook_endpoint"
            elif isinstance(orderbook_payload, dict) and (orderbook_payload.get("yes") or orderbook_payload.get("no") or orderbook_payload.get("yes_orders") or orderbook_payload.get("no_orders")):
                hydrated_market["yes"] = orderbook_payload.get("yes") or orderbook_payload.get("yes_orders") or []
                hydrated_market["no"] = orderbook_payload.get("no") or orderbook_payload.get("no_orders") or []
                source = "orderbook_endpoint.root"
                source_reason = "market_detail_missing_size_payload_fallback_to_orderbook_endpoint"
            else:
                print(
                    f"[LIVE-SELECT-TOPBOOK-SOURCE-FAIL] ticker={ticker} family={family} expected_source=orderbook_endpoint "
                    f"actual_source=market_detail exact_reason=no_orderbook_levels_returned"
                )
        print(
            f"[LIVE-SELECT-TOPBOOK-SOURCE] ticker={ticker} family={family} source_path={source} why_chosen={source_reason} "
            f"raw_payload_source_exists={'yes' if source != 'market_detail_snapshot' else 'no'}"
        )
        if self._should_trace_priority_topbook_family(family):
            source_state = self._collect_topbook_candidate_state(hydrated_market)
            hydration_stats = getattr(self, '_live_select_hydration_source_summary', None)
            if isinstance(hydration_stats, dict):
                stage_stats = hydration_stats.setdefault(
                    family,
                    {"candidates_seen": 0, "raw_payload_present_count": 0, "mapped_non_empty_count": 0, "zero_size_count": 0},
                )
                stage_stats["candidates_seen"] += 1
                if source_state.get('raw_orderbook_container_present') or source_state.get('size_fields_exist') or source_state.get('yes_no_arrays_exist'):
                    stage_stats['raw_payload_present_count'] += 1
                if source_state.get('mapped_topbook_fields_present') or source_state.get('non_zero_size_fields_present'):
                    stage_stats['mapped_non_empty_count'] += 1
                if not source_state.get('non_zero_size_fields_present'):
                    stage_stats['zero_size_count'] += 1
            print(
                f"[LIVE-SELECT-HYDRATION-SOURCE] ticker={ticker} family={family} "
                f"raw_orderbook_container_exists={'yes' if source_state.get('raw_orderbook_container_present') else 'no'} "
                f"raw_field_names={source_state.get('raw_field_names')} "
                f"yes_no_side_arrays_exist={'yes' if source_state.get('yes_no_arrays_exist') else 'no'} "
                f"size_fields_exist={'yes' if source_state.get('size_fields_exist') else 'no'} "
                f"best_bid_ask_like_exists={'yes' if source_state.get('best_bid_ask_fields_exist') else 'no'} "
                f"source_object={source}"
            )
        parsed_yes_bid = self.to_float(hydrated_market.get("yes_bid_dollars", hydrated_market.get("yes_bid")), None)
        parsed_yes_ask = self.to_float(hydrated_market.get("yes_ask_dollars", hydrated_market.get("yes_ask")), None)
        parsed_no_bid = self.to_float(hydrated_market.get("no_bid_dollars", hydrated_market.get("no_bid")), None)
        parsed_no_ask = self.to_float(hydrated_market.get("no_ask_dollars", hydrated_market.get("no_ask")), None)
        parser_accepted = bool(copied_keys)
        parse_reason = "copied_market_detail_fields" if parser_accepted else "no_supported_fields_in_market_detail"
        quotes_complete = all(value is not None for value in (parsed_yes_bid, parsed_yes_ask, parsed_no_bid, parsed_no_ask))
        validation_reason = "complete_numeric_quotes" if quotes_complete else "missing_numeric_quote_field"
        print(
            f"[LIVE-SELECT-HYDRATION-PARSE] ticker={ticker} event_ticker={event_ticker} parser_accepted={'yes' if parser_accepted else 'no'} "
            f"parsed_yes_bid={parsed_yes_bid} parsed_yes_ask={parsed_yes_ask} parsed_no_bid={parsed_no_bid} parsed_no_ask={parsed_no_ask} "
            f"copied_keys={copied_keys} reason={parse_reason}"
        )
        print(
            f"[LIVE-SELECT-HYDRATION-VALIDATION] ticker={ticker} parsed_yes_bid={parsed_yes_bid} parsed_yes_ask={parsed_yes_ask} "
            f"parsed_no_bid={parsed_no_bid} parsed_no_ask={parsed_no_ask} completeness_result={'complete' if quotes_complete else 'incomplete'} "
            f"reason={validation_reason}"
        )
        if not parser_accepted:
            print(
                f"[LIVE-SELECT-HYDRATION-FAIL] ticker={ticker} event_ticker={event_ticker} "
                f"failure_stage=parse reason=no_supported_fields_in_market_detail source_path={source_path}"
            )
        elif not quotes_complete:
            print(
                f"[LIVE-SELECT-HYDRATION-FAIL] ticker={ticker} event_ticker={event_ticker} "
                f"failure_stage=validation reason=quotes_still_incomplete source_path={source_path}"
            )
        return hydrated_market, source

    def _evaluate_targeted_orderbook_gate(self, market, spread):
        self._emit_priority_upstream_trace(
            "[LIVE-SELECT-GATE-ENTRY]",
            market,
            "targeted_orderbook_gate_entry",
            source_stage="_evaluate_targeted_orderbook_gate",
            spread=round(spread, 4) if spread is not None else None,
        )
        ticker = str(market.get("ticker", "") or "").strip()
        min_top_size = self.get_market_min_top_of_book_size(market)
        cached = self.targeted_orderbook_cache.get(ticker)
        if cached is None:
            try:
                orderbook = self.get_live_market_orderbook(ticker)
            except Exception:
                print(f"[LIVE-SELECT-DEBUG] targeted_orderbook ticker={ticker} result=fail reason=request_error")
                return False, {
                    "yes_top_price": None,
                    "yes_top_size": 0.0,
                    "no_top_price": None,
                    "no_top_size": 0.0,
                    "min_top_size": min_top_size,
                    "spread": spread,
                    "top_book_bucket": "both_sides_zero",
                }
            container = orderbook.get("orderbook") if isinstance(orderbook, dict) else None
            fp_container = orderbook.get("orderbook_fp") if isinstance(orderbook, dict) else None
            if isinstance(container, dict):
                yes_levels = container.get("yes") or container.get("yes_orders") or []
                no_levels = container.get("no") or container.get("no_orders") or []
                orderbook_source = "orderbook"
                if isinstance(market, dict):
                    market["orderbook"] = container
                    market["yes"] = yes_levels
                    market["no"] = no_levels
            elif isinstance(fp_container, dict):
                yes_levels = fp_container.get("yes_dollars") or fp_container.get("yes") or []
                no_levels = fp_container.get("no_dollars") or fp_container.get("no") or []
                orderbook_source = "orderbook_fp"
                if isinstance(market, dict):
                    market["orderbook_fp"] = fp_container
                    market["yes"] = yes_levels if isinstance(yes_levels, list) else []
                    market["no"] = no_levels if isinstance(no_levels, list) else []
                    if fp_container.get("yes_dollars") is not None:
                        market["yes_dollars"] = fp_container.get("yes_dollars")
                    if fp_container.get("no_dollars") is not None:
                        market["no_dollars"] = fp_container.get("no_dollars")
            else:
                yes_levels = (orderbook.get("yes") or orderbook.get("yes_orders") or orderbook.get("yes_dollars")) if isinstance(orderbook, dict) else []
                no_levels = (orderbook.get("no") or orderbook.get("no_orders") or orderbook.get("no_dollars")) if isinstance(orderbook, dict) else []
                orderbook_source = "root"
                if isinstance(market, dict):
                    if isinstance(orderbook, dict):
                        market["yes"] = yes_levels if isinstance(yes_levels, list) else []
                        market["no"] = no_levels if isinstance(no_levels, list) else []
                        if orderbook.get("yes_orders") is not None:
                            market["yes_orders"] = orderbook.get("yes_orders")
                        if orderbook.get("no_orders") is not None:
                            market["no_orders"] = orderbook.get("no_orders")
                        if orderbook.get("yes_dollars") is not None:
                            market["yes_dollars"] = orderbook.get("yes_dollars")
                        if orderbook.get("no_dollars") is not None:
                            market["no_dollars"] = orderbook.get("no_dollars")
            yes_top = self._extract_orderbook_top_level(yes_levels)
            no_top = self._extract_orderbook_top_level(no_levels)
            cached = {
                "yes_top_price": yes_top["price"],
                "yes_top_size": yes_top["size"],
                "no_top_price": no_top["price"],
                "no_top_size": no_top["size"],
                "min_top_size": min_top_size,
                "orderbook_source": orderbook_source,
                "yes_raw_top_level": yes_levels[0] if isinstance(yes_levels, list) and yes_levels else None,
                "no_raw_top_level": no_levels[0] if isinstance(no_levels, list) and no_levels else None,
            }
            self.targeted_orderbook_cache[ticker] = cached
        yes_top_size = cached["yes_top_size"]
        no_top_size = cached["no_top_size"]
        top_book_bucket = self._classify_targeted_orderbook_bucket(
            yes_top_size,
            no_top_size,
            min_top_size,
            spread,
        )
        passed = (
            cached["yes_top_price"] is not None
            and cached["no_top_price"] is not None
            and yes_top_size >= min_top_size
            and no_top_size >= min_top_size
            and spread <= MAX_ENTRY_SPREAD
        )
        print(
            f"[LIVE-SELECT-DEBUG] targeted_orderbook ticker={ticker} "
            f"result={'pass' if passed else 'fail'} yes_top_size={yes_top_size} "
            f"no_top_size={no_top_size} min_top_size={min_top_size} spread={spread} "
            f"bucket={top_book_bucket}"
        )
        if ticker in self.strict_validation_liquidity_trace_tickers:
            print(
                f"[LIVE-SELECT-LIQUIDITY-TRACE] stage=orderbook_helper ticker={ticker} family={self.get_market_family(market)} "
                f"origin={self._get_liquidity_trace_origin(market)} orderbook_source={cached.get('orderbook_source')} "
                f"raw_yes_top_level={cached.get('yes_raw_top_level')} raw_no_top_level={cached.get('no_raw_top_level')} "
                f"size_fallback_fields=size|count|quantity|contracts price_fallback_fields=price|price_dollars|yes_price|no_price "
                f"yes_top_price={cached.get('yes_top_price')} yes_top_size={yes_top_size} "
                f"no_top_price={cached.get('no_top_price')} no_top_size={no_top_size} min_top_size={min_top_size} "
                f"spread={spread} top_book_bucket={top_book_bucket}"
            )
        self._emit_priority_upstream_trace(
            "[LIVE-SELECT-GATE-RETURN]",
            market,
            "targeted_orderbook_gate_return",
            source_stage="_evaluate_targeted_orderbook_gate:return",
            orderbook_source=cached.get("orderbook_source") or 'none',
            cache_only_summary='yes' if not (isinstance(market, dict) and (market.get('orderbook') or market.get('yes') or market.get('no') or market.get('yes_orders') or market.get('no_orders'))) else 'no',
        )
        return passed, {
            "yes_top_price": cached["yes_top_price"],
            "yes_top_size": yes_top_size,
            "no_top_price": cached["no_top_price"],
            "no_top_size": no_top_size,
            "min_top_size": min_top_size,
            "spread": spread,
            "top_book_bucket": top_book_bucket,
            "orderbook_source": cached.get("orderbook_source"),
            "yes_raw_top_level": cached.get("yes_raw_top_level"),
            "no_raw_top_level": cached.get("no_raw_top_level"),
        }

    def passes_targeted_orderbook_quality(self, market, q):
        if not self.is_targeted_series_market(market):
            return True, {"yes_top_size": None, "no_top_size": None, "min_top_size": None}
        spread = (getattr(q, "yes_ask", 0.0) or 0.0) - (getattr(q, "yes_bid", 0.0) or 0.0)
        return self._evaluate_targeted_orderbook_gate(market, spread)

    def get_market_liquidity_priority(self, market):
        q = self.build_quote(market)
        volume = getattr(q, "volume", 0.0) or 0.0
        liquidity = getattr(q, "liquidity", 0.0) or 0.0
        quote_values = self.get_market_quote_values(market)
        yes_bid = quote_values.get("yes_bid")
        yes_ask = quote_values.get("yes_ask")
        no_bid = quote_values.get("no_bid")
        no_ask = quote_values.get("no_ask")
        both_sides_quoted = self.has_complete_quote_side(yes_bid, yes_ask) and self.has_complete_quote_side(no_bid, no_ask)
        recent_activity = volume > 0
        score = 0.0
        reasons = []
        empty_book = False
        has_real_liquidity = False
        top_book_state = "not_checked"
        yes_top_size = None
        no_top_size = None
        topbook_snapshot = None
        if self.is_targeted_series_market(market):
            ticker = str(market.get("ticker", "") or "").strip()
            cached = self.targeted_orderbook_cache.get(ticker)
            if cached is None:
                spread = yes_ask - yes_bid if self.has_complete_quote_side(yes_bid, yes_ask) else 1.0
                _passed, details = self._evaluate_targeted_orderbook_gate(market, spread)
            else:
                details = {
                    "yes_top_price": cached.get("yes_top_price"),
                    "yes_top_size": cached.get("yes_top_size", 0.0),
                    "no_top_price": cached.get("no_top_price"),
                    "no_top_size": cached.get("no_top_size", 0.0),
                    "min_top_size": cached.get("min_top_size"),
                }
            yes_top_size = details.get("yes_top_size", 0.0) or 0.0
            no_top_size = details.get("no_top_size", 0.0) or 0.0
            if yes_top_size > 0 and no_top_size > 0:
                score += 8.0
                reasons.append("non_zero_top_book_both_sides")
                top_book_state = "both_non_zero"
                has_real_liquidity = True
            elif yes_top_size > 0 or no_top_size > 0:
                score += 3.0
                reasons.append("non_zero_top_book_one_side")
                top_book_state = "one_non_zero"
            else:
                score -= 8.0
                reasons.append("empty_top_book")
                top_book_state = "zero_top_book"
                empty_book = True
            if details.get("yes_top_price") is not None and details.get("no_top_price") is not None:
                score += 3.0
                reasons.append("both_sides_top_book_quoted")
        else:
            topbook_snapshot = self._extract_non_targeted_topbook_snapshot(market)
            yes_top_size = topbook_snapshot.get("yes_top_size")
            no_top_size = topbook_snapshot.get("no_top_size")
            if (yes_top_size or 0.0) > 0.0 and (no_top_size or 0.0) > 0.0:
                score += 5.0
                reasons.append("non_zero_top_book_both_sides")
                top_book_state = "both_non_zero"
                has_real_liquidity = True
            elif (yes_top_size or 0.0) > 0.0 or (no_top_size or 0.0) > 0.0:
                score += 2.0
                reasons.append("non_zero_top_book_one_side")
                top_book_state = "one_non_zero"
            elif both_sides_quoted:
                score += 3.0
                reasons.append("both_sides_quoted")
                top_book_state = "zero_top_book"
                empty_book = True
                has_real_liquidity = liquidity > 0 or recent_activity
            else:
                score -= 2.0
                reasons.append("missing_two_sided_quote")
                top_book_state = "zero_top_book"
                empty_book = True
        if recent_activity:
            activity_score = min(volume, 100.0) / 20.0
            score += activity_score
            reasons.append("recent_trading_activity")
        else:
            reasons.append("no_recent_activity")
        if liquidity <= 0:
            score -= 2.0
            reasons.append("zero_reported_liquidity")
        elif liquidity >= self.get_market_min_entry_liquidity(market):
            score += 2.0
            reasons.append("reported_liquidity_present")
            has_real_liquidity = True or has_real_liquidity
        if not has_real_liquidity and not empty_book and liquidity <= 0 and not recent_activity:
            reasons.append("deprioritized_thin_market")
        return {
            "score": round(score, 4),
            "reasons": reasons,
            "has_real_liquidity": bool(has_real_liquidity),
            "empty_book": bool(empty_book),
            "both_sides_quoted": bool(both_sides_quoted),
            "recent_activity": bool(recent_activity),
            "top_book_state": top_book_state,
            "yes_top_size": yes_top_size,
            "no_top_size": no_top_size,
            "topbook_snapshot": topbook_snapshot,
        }

    def get_prefilter_liquidity_snapshot(self, market):
        q = self.build_quote(market)
        spread = (getattr(q, "yes_ask", 0.0) or 0.0) - (getattr(q, "yes_bid", 0.0) or 0.0)
        liquidity_priority = self.get_market_liquidity_priority(market)
        yes_top_size = liquidity_priority.get("yes_top_size")
        no_top_size = liquidity_priority.get("no_top_size")
        effective_top_size = None
        top_sizes = [size for size in (yes_top_size, no_top_size) if size is not None]
        if top_sizes:
            effective_top_size = max(top_sizes)
        return {
            "ticker": getattr(q, "ticker", "") or str(market.get("ticker", "") or "").strip(),
            "liquidity": getattr(q, "liquidity", 0.0) or 0.0,
            "threshold": self.get_market_min_entry_liquidity(market),
            "yes_bid": getattr(q, "yes_bid", 0.0) or 0.0,
            "yes_ask": getattr(q, "yes_ask", 0.0) or 0.0,
            "no_bid": getattr(q, "no_bid", 0.0) or 0.0,
            "no_ask": getattr(q, "no_ask", 0.0) or 0.0,
            "yes_top_size": yes_top_size,
            "no_top_size": no_top_size,
            "effective_top_size": effective_top_size,
            "effective_liquidity_metric": "reported_liquidity_dollars",
            "effective_liquidity_value": getattr(q, "liquidity", 0.0) or 0.0,
            "min_top_size_threshold": self.get_market_min_top_of_book_size(market) if self.is_targeted_series_market(market) else None,
            "spread": round(spread, 4),
            "both_sides_quoted": liquidity_priority.get("both_sides_quoted", False),
            "top_book_state": liquidity_priority.get("top_book_state"),
        }

    def get_market_quality_preference(self, market, liquidity_priority=None):
        q = self.build_quote(market)
        liquidity_priority = liquidity_priority or self.get_market_liquidity_priority(market)
        quote_values = self.get_market_quote_values(market)
        yes_bid = quote_values.get("yes_bid")
        yes_ask = quote_values.get("yes_ask")
        no_bid = quote_values.get("no_bid")
        no_ask = quote_values.get("no_ask")
        spread = yes_ask - yes_bid if self.has_complete_quote_side(yes_bid, yes_ask) else 1.0
        both_sides_quoted = self.has_complete_quote_side(yes_bid, yes_ask) and self.has_complete_quote_side(no_bid, no_ask)
        recent_activity = (getattr(q, "volume", 0.0) or 0.0) > 0
        narrow_spread = spread >= 0 and spread <= (MAX_ENTRY_SPREAD / 2.0)
        score = 0.0
        reasons = []
        if liquidity_priority.get("top_book_state") == "both_non_zero":
            score += 5.0
            reasons.append("non_empty_top_book")
        elif liquidity_priority.get("top_book_state") == "zero_top_book":
            score -= 5.0
            reasons.append("empty_top_book")
        if both_sides_quoted:
            score += 3.0
            reasons.append("both_sides_quoted")
        else:
            score -= 2.0
            reasons.append("missing_both_sides_quote")
        if narrow_spread:
            score += 4.0
            reasons.append("lower_spread")
        elif spread > MAX_ENTRY_SPREAD:
            score -= 4.0
            reasons.append("wide_spread")
        if recent_activity:
            score += 2.0
            reasons.append("recent_activity")
        if (getattr(q, "liquidity", 0.0) or 0.0) <= 0:
            score -= 2.0
            reasons.append("zero_reported_liquidity")
        return {
            "score": round(score, 4),
            "reasons": reasons,
            "both_sides_quoted": both_sides_quoted,
            "narrow_spread": narrow_spread,
            "recent_activity": recent_activity,
            "top_book_state": liquidity_priority.get("top_book_state"),
            "spread": round(spread, 4),
        }

    def evaluate_broad_prefilter_grace(self, market, original_reason, liquidity_priority=None, market_quality=None):
        liquidity_priority = liquidity_priority or self.get_market_liquidity_priority(market)
        market_quality = market_quality or self.get_market_quality_preference(market, liquidity_priority=liquidity_priority)
        top_book_state = liquidity_priority.get("top_book_state")
        yes_top_size = liquidity_priority.get("yes_top_size")
        no_top_size = liquidity_priority.get("no_top_size")
        one_sided_top_book = top_book_state == "one_non_zero"
        spread_threshold = round(MAX_ENTRY_SPREAD * 1.25, 4)
        liquidity = self.to_float(market.get("liquidity_dollars"), 0.0)
        liquidity_threshold = self.get_market_min_entry_liquidity(market)
        both_sides_quoted = bool(market_quality.get("both_sides_quoted"))
        recent_activity = bool(market_quality.get("recent_activity"))
        metrics = {
            "spread": None,
            "spread_threshold": spread_threshold,
            "yes_top_size": yes_top_size,
            "no_top_size": no_top_size,
            "both_sides_quoted": both_sides_quoted,
            "recent_activity": recent_activity,
            "liquidity": liquidity,
            "liquidity_threshold": liquidity_threshold,
            "one_sided_top_book": one_sided_top_book,
            "near_miss_reasons": [],
        }
        if original_reason not in ("missing_quotes", "wide_spread", "low_liquidity"):
            return False, "reason_not_eligible", metrics
        if top_book_state == "zero_top_book":
            return False, "dead_book", metrics
        has_quality_signal = (
            both_sides_quoted
            or recent_activity
            or top_book_state in ("both_non_zero", "one_non_zero")
        )
        if not has_quality_signal:
            return False, "no_quality_signal", metrics
        yes_bid = self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")))
        yes_ask = self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")))
        no_bid = self.to_float(market.get("no_bid_dollars", market.get("no_bid")))
        no_ask = self.to_float(market.get("no_ask_dollars", market.get("no_ask")))
        if yes_bid <= 0 and no_ask > 0:
            yes_bid = max(0.0, 1.0 - no_ask)
        if yes_ask <= 0 and no_bid > 0:
            yes_ask = min(1.0, 1.0 - no_bid)
        spread = market_quality.get("spread")
        if yes_bid > 0 and yes_ask > 0:
            spread = yes_ask - yes_bid
        if spread is not None:
            metrics["spread"] = round(spread, 4)
        if spread is None or spread < 0:
            return False, "invalid_spread", metrics
        near_miss_margin = round(MAX_ENTRY_SPREAD * 0.1, 4)
        grace_spread_limit = MAX_ENTRY_SPREAD * 1.25
        if spread > grace_spread_limit:
            if spread <= grace_spread_limit + near_miss_margin:
                metrics["near_miss_reasons"].append("spread_near_threshold")
            return False, "spread_too_wide", metrics
        if one_sided_top_book:
            metrics["near_miss_reasons"].append("one_sided_top_book")
        if recent_activity and not both_sides_quoted:
            metrics["near_miss_reasons"].append("recent_activity_incomplete_quote")
        if original_reason == "low_liquidity":
            if liquidity <= (liquidity_threshold * 0.75):
                return False, "far_below_liquidity_threshold", metrics
        return True, None, metrics

    def get_controlled_activation_status(self, market, family, prefilter_survivor_family_counts):
        q = self.build_quote(market)
        liquidity_priority = self.get_market_liquidity_priority(market)
        market_quality = self.get_market_quality_preference(market, liquidity_priority=liquidity_priority)
        family_survivors = prefilter_survivor_family_counts.get(family, 0)
        denied_reasons = []
        if liquidity_priority.get("top_book_state") != "both_non_zero":
            denied_reasons.append("top_book_not_fully_non_empty")
        if not market_quality.get("both_sides_quoted"):
            denied_reasons.append("missing_both_sides_quote")
        if not market_quality.get("narrow_spread"):
            denied_reasons.append("spread_not_narrow")
        if not market_quality.get("recent_activity"):
            denied_reasons.append("no_recent_activity")
        if family_survivors <= 0:
            denied_reasons.append("no_prefilter_survivors")
        return {
            "armed": len(denied_reasons) == 0,
            "ticker": getattr(q, "ticker", "") or str(market.get("ticker", "") or "").strip(),
            "family": family,
            "top_book_state": liquidity_priority.get("top_book_state"),
            "both_sides_quoted": market_quality.get("both_sides_quoted", False),
            "narrow_spread": market_quality.get("narrow_spread", False),
            "recent_activity": market_quality.get("recent_activity", False),
            "prefilter_survivors": family_survivors,
            "denied_reasons": denied_reasons,
        }

    def _resolve_selector_family_order(self, fetched_family_counts):
        available_families = [family for family in fetched_family_counts if family not in BLOCKED_MARKET_FAMILIES]
        if PREFERRED_MARKET_FAMILIES:
            preferred = [family for family in PREFERRED_MARKET_FAMILIES if family in available_families]
        else:
            preferred = list(available_families)
        fallback = [family for family in FALLBACK_MARKET_FAMILIES if family in available_families and family not in preferred]
        return preferred, fallback

    def _get_viable_selector_families(self, family_priority, family_diagnostics):
        viable_families = []
        for family in family_priority:
            stats = family_diagnostics.get(family, {})
            if stats.get("target_window", 0) <= 0:
                continue
            if stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE:
                continue
            if stats.get("wide_spread_rate", 0.0) >= FAMILY_MAX_WIDE_SPREAD_RATE:
                continue
            if stats.get("low_liquidity_rate", 0.0) >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
                continue
            if stats.get("too_early_share", 0.0) >= FAMILY_MAX_TOO_EARLY_RATE:
                continue
            viable_families.append(family)
        return viable_families

    def select_quality_expansion_families(self, markets, exclude_families=()):
        family_markets = {}
        for market in markets:
            family = self.get_market_family(market)
            if not family or family in BLOCKED_MARKET_FAMILIES or family in exclude_families:
                continue
            family_markets.setdefault(family, []).append(market)
        if not family_markets:
            print("[LIVE-SELECT-QUALITY-EXPANSION] newly_considered_families=[]")
            print("[LIVE-SELECT-QUALITY-EXPANSION] new_families_added=[] reason=no_candidate_families")
            return []
        broad_discovery_max_minutes_to_event = self.get_broad_discovery_max_minutes_to_event()
        family_priority, family_diagnostics = self._rank_selector_families(
            family_markets,
            max_minutes_to_event=broad_discovery_max_minutes_to_event,
        )
        newly_considered_families = [
            family for family in family_priority
            if any(family.startswith(prefix) for prefix in QUALITY_EXPANSION_PREFERRED_FAMILY_PREFIXES)
        ]
        selected = []
        decisions = {}
        eligible_families = []
        for family in family_priority:
            stats = family_diagnostics.get(family, {})
            decision = "promote"
            reason = "quality_pass"
            preferred_prefix = next(
                (prefix for prefix in QUALITY_EXPANSION_PREFERRED_FAMILY_PREFIXES if family.startswith(prefix)),
                None,
            )
            preferred_bonus = 3.0 if preferred_prefix else 0.0
            quality_total = stats.get("family_quality_score", 0.0) + stats.get("spread_quality_score", 0.0) + preferred_bonus
            if family in DEPRIORITIZED_MARKET_FAMILIES:
                decision = "reject"
                reason = "deprioritized_family"
            elif stats.get("target_window", 0) <= 0:
                decision = "reject"
                reason = "outside_target_window"
            elif stats.get("wide_spread_rate", 0.0) >= FAMILY_MAX_WIDE_SPREAD_RATE:
                decision = "reject"
                reason = "wide_spread_rate"
            elif stats.get("low_liquidity_rate", 0.0) >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
                decision = "reject"
                reason = "low_liquidity_rate"
            elif quality_total <= 0.0:
                decision = "reject"
                reason = "quality_score_not_positive"
            else:
                decision = "consider"
                reason = "preferred_family_prefix" if preferred_prefix else "quality_pass"
                eligible_families.append((quality_total, family))
            decisions[family] = {
                "decision": decision,
                "reason": reason,
                "preferred_prefix": preferred_prefix,
                "quality_total": round(quality_total, 4),
            }
        eligible_families.sort(reverse=True)
        for _quality_total, family in eligible_families:
            if len(selected) >= MAX_QUALITY_EXPANSION_FAMILIES:
                decisions[family]["decision"] = "skipped"
                decisions[family]["reason"] = "expansion_family_cap_reached"
                continue
            selected.append(family)
            decisions[family]["decision"] = "promote"
        print(f"[LIVE-SELECT-QUALITY-EXPANSION] newly_considered_families={newly_considered_families}")
        print(f"[LIVE-SELECT-QUALITY-EXPANSION] new_families_added={selected}")
        for family, stats in sorted(family_diagnostics.items()):
            family_decision = decisions.get(family, {})
            print(
                f"[LIVE-SELECT-QUALITY-EXPANSION] family={family} scan_count={len(family_markets.get(family, []))} "
                f"family_quality_score={stats.get('family_quality_score', 0.0)} "
                f"spread_quality_score={stats.get('spread_quality_score', 0.0)} "
                f"narrow_spread={stats.get('narrow_spread', 0)} wide_spread={stats.get('wide_spread', 0)} "
                f"quality_reasons={stats.get('quality_reasons', [])} "
                f"decision={family_decision.get('decision')} reason={family_decision.get('reason')} "
                f"preferred_prefix={family_decision.get('preferred_prefix')}"
            )
        return selected

    def _get_family_expansion_reason(self, lead_family, lead_stats):
        if not lead_family:
            return None
        if not lead_stats:
            return "missing_lead_family_stats"
        if lead_stats.get("target_window", 0) <= 0:
            return "lead_family_outside_target_window"
        if lead_stats.get("survivor_rate", 0.0) > 0.0:
            return None
        if lead_stats.get("low_liquidity_rate", 0.0) >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
            return "lead_family_dead_liquidity"
        if lead_stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE:
            return "lead_family_missing_quotes"
        if lead_stats.get("wide_spread_rate", 0.0) >= FAMILY_MAX_WIDE_SPREAD_RATE:
            return "lead_family_wide_spreads"
        if lead_stats.get("too_early_share", 0.0) >= FAMILY_MAX_TOO_EARLY_RATE:
            return "lead_family_too_early"
        return None

    def _rank_selector_families(self, family_markets, max_minutes_to_event=None):
        ranked = []
        diagnostics = {}
        no_candidate_recovery_mode = self.no_candidate_cycle_streak >= 2
        for family, markets in family_markets.items():
            fetched_count = len(markets)
            missing_quotes = 0
            wide_spread = 0
            low_liquidity = 0
            too_early = 0
            target_window = 0
            survivors = 0
            narrow_spread = 0
            yes_quote_count = 0
            no_quote_count = 0
            both_sides_quoted = 0
            non_empty_top_book = 0
            recent_activity = 0
            zero_reported_liquidity = 0
            for market in markets:
                timing_bucket = self.get_event_time_bucket(self.get_minutes_to_event(market))
                if timing_bucket == "too_early":
                    too_early += 1
                elif timing_bucket == "target_window":
                    target_window += 1
                yes_bid = self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")))
                yes_ask = self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")))
                no_bid = self.to_float(market.get("no_bid_dollars", market.get("no_bid")))
                no_ask = self.to_float(market.get("no_ask_dollars", market.get("no_ask")))
                volume = self.to_float(market.get("volume_fp", market.get("volume")), 0.0)
                liquidity = self.to_float(market.get("liquidity_dollars"), 0.0)
                min_liquidity = self.get_market_min_entry_liquidity(market)
                liquidity_priority = self.get_market_liquidity_priority(market)
                if liquidity_priority.get("top_book_state") in ("both_non_zero", "one_non_zero"):
                    non_empty_top_book += 1
                if liquidity <= 0:
                    zero_reported_liquidity += 1
                if volume > 0:
                    recent_activity += 1
                if yes_bid > 0 and yes_ask > 0:
                    yes_quote_count += 1
                if no_bid > 0 and no_ask > 0:
                    no_quote_count += 1
                if yes_bid > 0 and yes_ask > 0 and no_bid > 0 and no_ask > 0:
                    both_sides_quoted += 1
                if yes_bid <= 0 or yes_ask <= 0:
                    missing_quotes += 1
                    continue
                spread = yes_ask - yes_bid
                if spread >= 0 and spread <= (MAX_ENTRY_SPREAD / 2.0):
                    narrow_spread += 1
                if spread < 0 or spread > MAX_ENTRY_SPREAD:
                    wide_spread += 1
                    continue
                if liquidity <= min_liquidity:
                    low_liquidity += 1
                    continue
                survivors += 1
            runtime_stats = self.live_family_runtime_stats.get(family, {})
            score = float(survivors) * 5.0
            score += max(0.0, fetched_count - wide_spread) * FAMILY_SPREAD_QUALITY_WEIGHT
            score -= missing_quotes * FAMILY_MISSING_QUOTES_PENALTY
            score -= low_liquidity * FAMILY_LOW_LIQUIDITY_PENALTY
            score -= wide_spread * FAMILY_WIDE_SPREAD_PENALTY
            score -= too_early * FAMILY_TOO_EARLY_PENALTY
            score += float(runtime_stats.get("prefilter_survivors", 0)) * 1.5
            score += float(runtime_stats.get("strict_validation_entrants", 0)) * 2.0
            score += float(runtime_stats.get("final_candidates", 0)) * 3.0
            score += float(runtime_stats.get("prefilter_survivors", 0)) * FAMILY_SURVIVOR_BONUS
            score -= float(runtime_stats.get("wide_spread", 0)) * 0.5
            score -= float(runtime_stats.get("missing_quotes", 0)) * 0.5
            score -= float(runtime_stats.get("low_liquidity", 0)) * 0.5
            score -= float(runtime_stats.get("too_early", 0)) * 0.25
            if fetched_count > 0:
                target_window_share = target_window / float(fetched_count)
                too_early_share = too_early / float(fetched_count)
                missing_quotes_rate = missing_quotes / float(fetched_count)
                wide_spread_rate = wide_spread / float(fetched_count)
                low_liquidity_rate = low_liquidity / float(fetched_count)
                survivor_rate = survivors / float(fetched_count)
                narrow_spread_rate = narrow_spread / float(fetched_count)
                both_sides_quoted_rate = both_sides_quoted / float(fetched_count)
                recent_activity_rate = recent_activity / float(fetched_count)
                zero_reported_liquidity_rate = zero_reported_liquidity / float(fetched_count)
                spread_quality_score = max(0.0, narrow_spread_rate - wide_spread_rate) * 15.0
                family_quality_score = (
                    both_sides_quoted_rate * 10.0
                    + recent_activity_rate * 6.0
                    - zero_reported_liquidity_rate * 8.0
                )
                score += target_window_share * EVENT_TIME_WEIGHT * 4.0
                score -= too_early_share * FAMILY_TOO_EARLY_PENALTY * 4.0
                score += spread_quality_score + family_quality_score
            else:
                target_window_share = 0.0
                too_early_share = 0.0
                missing_quotes_rate = 0.0
                wide_spread_rate = 0.0
                low_liquidity_rate = 0.0
                survivor_rate = 0.0
                narrow_spread_rate = 0.0
                both_sides_quoted_rate = 0.0
                recent_activity_rate = 0.0
                zero_reported_liquidity_rate = 0.0
                spread_quality_score = 0.0
                family_quality_score = 0.0
            if PREFERRED_MARKET_FAMILIES and family in PREFERRED_MARKET_FAMILIES:
                score += 10.0
            if family in DEPRIORITIZED_MARKET_FAMILIES:
                score -= 20.0
            if missing_quotes_rate >= FAMILY_MAX_MISSING_QUOTES_RATE:
                score -= 25.0
            if wide_spread_rate >= FAMILY_MAX_WIDE_SPREAD_RATE:
                score -= 25.0
            if low_liquidity_rate >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
                score -= 20.0
            if too_early_share >= FAMILY_MAX_TOO_EARLY_RATE:
                score -= 20.0
            if survivor_rate < FAMILY_MIN_SURVIVOR_RATE:
                score -= 15.0
            quality_reasons = []
            if spread_quality_score > 0:
                quality_reasons.append("promote_narrow_spread")
            elif wide_spread_rate > 0.4:
                quality_reasons.append("deprioritize_wide_spread")
            if both_sides_quoted_rate > 0.4:
                quality_reasons.append("promote_two_sided_quotes")
            if recent_activity_rate > 0.4:
                quality_reasons.append("promote_recent_activity")
            if zero_reported_liquidity_rate > 0.4:
                quality_reasons.append("deprioritize_zero_reported_liquidity")
            recovery_adjustment = 0.0
            recovery_reason = None
            if no_candidate_recovery_mode:
                runtime_survivors = float(runtime_stats.get("prefilter_survivors", 0))
                runtime_final_candidates = float(runtime_stats.get("final_candidates", 0))
                runtime_structure_penalty = float(runtime_stats.get("wide_spread", 0)) + float(runtime_stats.get("missing_quotes", 0)) + float(runtime_stats.get("low_liquidity", 0))
                if survivor_rate > 0 or runtime_survivors > 0 or runtime_final_candidates > 0:
                    recovery_adjustment += 12.0
                    recovery_reason = "recent_better_structure"
                elif runtime_structure_penalty > 0 and survivor_rate == 0:
                    recovery_adjustment -= 8.0
                    recovery_reason = "repeated_weak_structure"
                score += recovery_adjustment
            ranked.append((score, family))
            diagnostics[family] = {
                "fetched": fetched_count,
                "missing_quotes": missing_quotes,
                "wide_spread": wide_spread,
                "low_liquidity": low_liquidity,
                "too_early": too_early,
                "target_window": target_window,
                "narrow_spread": narrow_spread,
                "yes_quote_count": yes_quote_count,
                "no_quote_count": no_quote_count,
                "both_sides_quoted": both_sides_quoted,
                "non_empty_top_book": non_empty_top_book,
                "recent_activity": recent_activity,
                "zero_reported_liquidity": zero_reported_liquidity,
                "target_window_share": round(target_window_share, 4),
                "too_early_share": round(too_early_share, 4),
                "missing_quotes_rate": round(missing_quotes_rate, 4),
                "wide_spread_rate": round(wide_spread_rate, 4),
                "low_liquidity_rate": round(low_liquidity_rate, 4),
                "survivor_rate": round(survivor_rate, 4),
                "narrow_spread_rate": round(narrow_spread_rate, 4),
                "both_sides_quoted_rate": round(both_sides_quoted_rate, 4),
                "recent_activity_rate": round(recent_activity_rate, 4),
                "zero_reported_liquidity_rate": round(zero_reported_liquidity_rate, 4),
                "spread_quality_score": round(spread_quality_score, 4),
                "family_quality_score": round(family_quality_score, 4),
                "quality_reasons": quality_reasons,
                "prefilter_survivors": survivors,
                "recovery_adjustment": round(recovery_adjustment, 4),
                "recovery_reason": recovery_reason,
                "score": round(score, 4),
            }
        ranked.sort(reverse=True)
        return [family for _, family in ranked[:MAX_FAMILIES_PER_SCAN]], diagnostics

    def classify_market_condition(self):
        summary = self.last_live_select_summary or {}
        spread_skip_count = int(summary.get("spread_skip_count", 0) or 0)
        low_liquidity_reject_count = int(summary.get("low_liquidity_reject_count", 0) or 0)
        missing_quotes_reject_count = int(summary.get("missing_quotes_reject_count", 0) or 0)
        if self.no_candidate_cycle_streak >= 3 and spread_skip_count >= 3:
            return "POOR", "high_spread_and_no_candidates"
        if self.no_candidate_cycle_streak >= 3 and (
            low_liquidity_reject_count >= 3 or missing_quotes_reject_count >= 3
        ):
            return "POOR", "illiquid_or_unquoted_and_no_candidates"
        if (
            self.no_candidate_cycle_streak >= 1
            or spread_skip_count >= 2
            or low_liquidity_reject_count >= 2
            or missing_quotes_reject_count >= 2
        ):
            return "NEUTRAL", "mixed_structure"
        return "GOOD", "normal"

    def coarse_prefilter(self, market):
        ticker = str(market.get("ticker", "") or "").strip()
        family = self.get_market_family(market)
        series_ticker = self.get_targeted_series_ticker(market)
        if not ticker:
            return False, "missing_ticker", family
        if BLOCKED_MARKET_FAMILIES and family in BLOCKED_MARKET_FAMILIES:
            return False, "blocked_family", family
        if self._is_combo_like_market(market):
            return False, "combo_or_mve", family
        status = (market.get("status") or "").lower()
        if status not in ("open", "active"):
            return False, "inactive", family
        if market.get("can_close") is False or market.get("can_open") is False:
            return False, "not_tradable", family
        quote_values = self.get_market_quote_values(market)
        yes_bid = quote_values.get("yes_bid")
        yes_ask = quote_values.get("yes_ask")
        no_bid = quote_values.get("no_bid")
        no_ask = quote_values.get("no_ask")
        both_sides_quoted = self.has_complete_quote_side(yes_bid, yes_ask) and self.has_complete_quote_side(no_bid, no_ask)
        volume = self.to_float(market.get("volume_fp", market.get("volume")), 0.0)
        liquidity = self.to_float(market.get("liquidity_dollars"), 0.0)
        min_liquidity = self.get_market_min_entry_liquidity(market)
        if not self.has_complete_quote_side(yes_bid, yes_ask):
            return False, "missing_quotes", family
        spread = yes_ask - yes_bid
        if spread < 0 or spread > MAX_ENTRY_SPREAD:
            return False, "wide_spread", family
        if series_ticker == "KXBTC15M":
            print(
                f"[LIVE-SELECT-DEBUG] targeted_prefilter ticker={ticker} family={family} "
                f"threshold={min_liquidity} liquidity={liquidity} "
                f"pass={'yes' if liquidity > min_liquidity else 'no'}"
            )
        if liquidity <= min_liquidity:
            if series_ticker == "KXBTC15M":
                orderbook_ok, orderbook_details = self._evaluate_targeted_orderbook_gate(market, spread)
                print(f"[LIVE-SELECT-DEBUG] targeted_btc15m_liquidity_seen={liquidity}")
                print("[LIVE-SELECT-DEBUG] targeted_btc15m_quote_fallback_used=yes")
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_btc15m_top_of_book yes_price={orderbook_details.get('yes_top_price')} "
                    f"yes_size={orderbook_details.get('yes_top_size')} no_price={orderbook_details.get('no_top_price')} "
                    f"no_size={orderbook_details.get('no_top_size')}"
                )
                print(f"[LIVE-SELECT-DEBUG] targeted_btc15m_spread_seen={orderbook_details.get('spread')}")
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_btc15m_fallback_gate="
                    f"{'PASS' if orderbook_ok else 'FAIL'}"
                )
                if orderbook_ok:
                    print(
                        f"[LIVE-SELECT-DEBUG] coarse_prefilter_targeted_liquidity_fallback_used=yes "
                        f"ticker={ticker} series={series_ticker} liquidity={liquidity} threshold={min_liquidity}"
                    )
                    return True, None, family
                print(
                    f"[LIVE-SELECT-DEBUG] coarse_prefilter_targeted_liquidity_fallback_not_used "
                    f"ticker={ticker} series={series_ticker} liquidity={liquidity} threshold={min_liquidity} "
                    f"top_book_ok={'yes' if orderbook_ok else 'no'} "
                    f"yes_top={orderbook_details.get('yes_top_price')}@{orderbook_details.get('yes_top_size')} "
                    f"no_top={orderbook_details.get('no_top_price')}@{orderbook_details.get('no_top_size')}"
                )
                print("[LIVE-SELECT-DEBUG] targeted_btc15m_final_reject_reason=low_liquidity")
            return False, "low_liquidity", family
        if volume <= MIN_ENTRY_VOLUME:
            return False, "low_volume", family
        if yes_ask <= MIN_ENTRY_PRICE or yes_ask >= MAX_ENTRY_PRICE:
            return False, "unsafe_price", family
        return True, None, family

    def compute_entry_presolve_score(self, market):
        ticker = str(market.get("ticker", "") or "").strip()
        family = self.get_market_family(market)
        yes_bid = self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")))
        yes_ask = self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")))
        volume = self.to_float(market.get("volume_fp", market.get("volume")), 0.0)
        liquidity = self.to_float(market.get("liquidity_dollars"), 0.0)
        spread = yes_ask - yes_bid if yes_bid > 0 and yes_ask > 0 else 1.0
        mid_price = yes_ask
        if yes_bid > 0 and yes_ask > 0:
            mid_price = (yes_bid + yes_ask) / 2
        spread_component = max(0.0, MAX_ENTRY_SPREAD - spread) * 40
        liquidity_component = min(liquidity, 500) / 25
        volume_component = min(volume, 500) / 40
        price_component = max(0.0, 0.25 - abs(mid_price - 0.5)) * 8
        family_component = 0.0
        if PREFERRED_MARKET_FAMILIES and family in PREFERRED_MARKET_FAMILIES:
            family_component += 2.0
        if FALLBACK_MARKET_FAMILIES and family in FALLBACK_MARKET_FAMILIES:
            family_component -= 0.5
        ticker_component = max(0.0, 24 - len(ticker)) / 24
        liquidity_priority = self.get_market_liquidity_priority(market)
        market_quality = self.get_market_quality_preference(market, liquidity_priority=liquidity_priority)
        liquidity_priority_component = liquidity_priority.get("score", 0.0)
        market_quality_component = market_quality.get("score", 0.0)
        has_top_book = bool(
            (liquidity_priority.get("yes_top_size") or 0.0) > 0
            or (liquidity_priority.get("no_top_size") or 0.0) > 0
        )
        top_book_signal_component = 0.0
        if self.is_targeted_series_market(market):
            top_book_signal_component = 6.0 if has_top_book else -12.0
        print(
            f"[LIVE-SELECT-DEBUG] prescore_liquidity_signal ticker={ticker} "
            f"has_top_book={'yes' if has_top_book else 'no'}"
        )
        minutes_to_event = self.get_minutes_to_event(market)
        event_time_component = 0.0
        if minutes_to_event is not None:
            if MIN_MINUTES_TO_EVENT <= minutes_to_event <= self.get_effective_max_minutes_to_event():
                event_time_component += EVENT_TIME_WEIGHT
            elif minutes_to_event < MIN_MINUTES_TO_EVENT:
                event_time_component -= EVENT_TIME_WEIGHT
            else:
                event_time_component -= EVENT_TIME_WEIGHT / 2
        score = (
            spread_component
            + liquidity_component
            + volume_component
            + price_component
            + family_component
            + ticker_component
            + event_time_component
            + liquidity_priority_component
            + market_quality_component
            + top_book_signal_component
        )
        return {
            "score": round(score, 4),
            "spread": spread,
            "volume": volume,
            "liquidity": liquidity,
            "price": yes_ask,
            "family": family,
            "minutes_to_event": minutes_to_event,
            "liquidity_priority": liquidity_priority,
            "market_quality": market_quality,
            "has_top_book": has_top_book,
            "components": {
                "spread": round(spread_component, 4),
                "liquidity": round(liquidity_component, 4),
                "volume": round(volume_component, 4),
                "price": round(price_component, 4),
                "family": round(family_component, 4),
                "ticker": round(ticker_component, 4),
                "event_time": round(event_time_component, 4),
                "liquidity_priority": round(liquidity_priority_component, 4),
                "market_quality": round(market_quality_component, 4),
                "top_book_signal": round(top_book_signal_component, 4),
            },
        }

    def get_minutes_to_event(self, market):
        now_ts = time.time()
        timestamp_details = self.get_market_event_timestamp_details(market)
        event_ts = timestamp_details.get("timestamp")
        if event_ts is None:
            return None
        return (event_ts - now_ts) / 60.0

    def get_effective_max_minutes_to_event(self):
        return MAX_MINUTES_TO_EVENT + TIME_WINDOW_EXTENSION_MINUTES

    def get_broad_discovery_max_minutes_to_event(self):
        return self.get_effective_max_minutes_to_event() + BROAD_DISCOVERY_TIME_WINDOW_EXTENSION_MINUTES

    def get_time_window_inclusion_reason(self, minutes_to_event, max_minutes_to_event=None):
        if minutes_to_event is None:
            return None
        if minutes_to_event < MIN_MINUTES_TO_EVENT:
            return None
        if max_minutes_to_event is None:
            max_minutes_to_event = self.get_effective_max_minutes_to_event()
        if minutes_to_event <= MAX_MINUTES_TO_EVENT:
            return "base_window"
        if minutes_to_event <= max_minutes_to_event:
            return "extended_window"
        return None

    def get_event_time_bucket(self, minutes_to_event, max_minutes_to_event=None):
        if minutes_to_event is None:
            return "unknown"
        if minutes_to_event < MIN_MINUTES_TO_EVENT:
            return "too_close"
        if max_minutes_to_event is None:
            max_minutes_to_event = self.get_effective_max_minutes_to_event()
        if minutes_to_event <= max_minutes_to_event:
            return "target_window"
        return "too_early"

    def get_validation_timing_score(self, minutes_to_event):
        if minutes_to_event is None:
            return 0.0
        if MIN_MINUTES_TO_EVENT <= minutes_to_event <= self.get_effective_max_minutes_to_event():
            return 15.0
        if minutes_to_event < MIN_MINUTES_TO_EVENT:
            return 2.0
        return 6.0

    def get_cached_targeted_orderbook_details(self, market, spread):
        if not self.is_targeted_series_market(market):
            return {
                "yes_top_price": None,
                "yes_top_size": None,
                "no_top_price": None,
                "no_top_size": None,
                "min_top_size": None,
                "spread": spread,
                "top_book_bucket": None,
                "orderbook_source": None,
                "yes_raw_top_level": None,
                "no_raw_top_level": None,
            }
        ticker = str(market.get("ticker", "") or "").strip()
        cached = self.targeted_orderbook_cache.get(ticker)
        if not cached:
            return {
                "yes_top_price": None,
                "yes_top_size": 0.0,
                "no_top_price": None,
                "no_top_size": 0.0,
                "min_top_size": self.get_market_min_top_of_book_size(market),
                "spread": spread,
                "top_book_bucket": None,
                "orderbook_source": None,
                "yes_raw_top_level": None,
                "no_raw_top_level": None,
            }
        return {
            "yes_top_price": cached.get("yes_top_price"),
            "yes_top_size": cached.get("yes_top_size", 0.0),
            "no_top_price": cached.get("no_top_price"),
            "no_top_size": cached.get("no_top_size", 0.0),
            "min_top_size": cached.get("min_top_size"),
            "spread": spread,
            "top_book_bucket": cached.get("top_book_bucket"),
            "orderbook_source": cached.get("orderbook_source"),
            "yes_raw_top_level": cached.get("yes_raw_top_level"),
            "no_raw_top_level": cached.get("no_raw_top_level"),
        }

    def get_strict_validation_snapshot(self, market, q):
        spread = (getattr(q, "yes_ask", 0.0) or 0.0) - (getattr(q, "yes_bid", 0.0) or 0.0)
        minutes_to_event = self.get_minutes_to_event(market)
        return {
            "spread": round(spread, 4),
            "yes_bid": getattr(q, "yes_bid", 0.0) or 0.0,
            "yes_ask": getattr(q, "yes_ask", 0.0) or 0.0,
            "no_bid": getattr(q, "no_bid", 0.0) or 0.0,
            "no_ask": getattr(q, "no_ask", 0.0) or 0.0,
            "liquidity": getattr(q, "liquidity", 0.0) or 0.0,
            "volume": getattr(q, "volume", 0.0) or 0.0,
            "minutes_to_event": round(minutes_to_event, 4) if minutes_to_event is not None else None,
            "timing_score": self.get_validation_timing_score(minutes_to_event),
            "orderbook": self.get_cached_targeted_orderbook_details(market, spread),
        }

    def strict_entry_validate(self, market):
        q = self.build_quote(market)
        return self._filter_live_entry_candidate(market, q), q

    def _extract_entry_timing_float(self, market, q, keys):
        for key in keys:
            value = None
            if market is not None and isinstance(market, dict):
                value = market.get(key)
            if value is None and q is not None and hasattr(q, key):
                value = getattr(q, key)
            value = self.to_float(value, None)
            if value is not None:
                return value
        return None

    def _normalize_phase55_price_series(self, raw_series):
        if not isinstance(raw_series, list):
            return []
        normalized = []
        for item in raw_series:
            price = None
            if isinstance(item, (int, float, str)):
                price = self.to_float(item, None)
            elif isinstance(item, dict):
                price = self.to_float(
                    item.get("close")
                    or item.get("close_price")
                    or item.get("last_price")
                    or item.get("last_price_dollars")
                    or item.get("price")
                    or item.get("price_dollars"),
                    None,
                )
            elif isinstance(item, (list, tuple)) and item:
                price = self.to_float(item[-1], None)
            if price is not None and price > 0:
                normalized.append(price)
        return normalized

    def _extract_phase55_price_series_context_from_payload(self, payload, source_label="market"):
        if not isinstance(payload, dict):
            return {
                "series": [],
                "stochastic_source": "unavailable",
                "history_points": 0,
                "history_reason": f"{source_label}_not_dict",
            }
        candidate_keys = (
            "price_history",
            "last_price_history",
            "history",
            "recent_prices",
            "prices",
            "closes",
            "close_prices",
            "candles",
            "series",
        )
        for key in candidate_keys:
            series = self._normalize_phase55_price_series(payload.get(key))
            if series:
                return {
                    "series": series,
                    "stochastic_source": source_label,
                    "history_points": len(series),
                    "history_reason": key,
                }
        nested_keys = ("market", "history", "data", "series")
        for key in nested_keys:
            nested = payload.get(key)
            if isinstance(nested, dict):
                nested_context = self._extract_phase55_price_series_context_from_payload(nested, source_label=source_label)
                if nested_context.get("series"):
                    nested_reason = nested_context.get("history_reason", "series")
                    nested_context["history_reason"] = f"{key}.{nested_reason}"
                    return nested_context
        return {
            "series": [],
            "stochastic_source": "unavailable",
            "history_points": 0,
            "history_reason": "no_price_series",
        }

    def _extract_phase55_price_series_from_payload(self, payload):
        return self._extract_phase55_price_series_context_from_payload(payload).get("series", [])

    def _build_phase55_snapshot_series(self, market, q):
        previous_price = self._extract_entry_timing_float(
            market,
            q,
            ["previous_price_dollars", "previous_price", "previous_last_price_dollars", "previous_last_price"],
        )
        current_price = self._extract_entry_timing_float(
            market,
            q,
            ["last_price_dollars", "last_price"],
        )
        snapshot_series = []
        if previous_price is not None and previous_price > 0:
            snapshot_series.append(previous_price)
        if current_price is not None and current_price > 0:
            snapshot_series.append(current_price)
        if snapshot_series:
            history_reason = "previous_and_last_price" if len(snapshot_series) >= 2 else "last_price_only"
            return {
                "series": snapshot_series,
                "stochastic_source": "market_snapshot",
                "history_points": len(snapshot_series),
                "history_reason": history_reason,
            }
        return {
            "series": [],
            "stochastic_source": "unavailable",
            "history_points": 0,
            "history_reason": "no_snapshot_prices",
        }

    def _get_phase55_observed_price_point(self, market, q):
        price_candidates = (
            ("last_price", self._extract_entry_timing_float(market, q, ["last_price_dollars", "last_price"])),
            ("yes_ask", self._extract_entry_timing_float(market, q, ["yes_ask_dollars", "yes_ask"])),
            ("yes_bid", self._extract_entry_timing_float(market, q, ["yes_bid_dollars", "yes_bid"])),
            ("previous_price", self._extract_entry_timing_float(market, q, ["previous_price_dollars", "previous_price"])),
        )
        for reason, price in price_candidates:
            if price is not None and price > 0:
                return {"price": price, "history_reason": reason}
        return {"price": None, "history_reason": "no_observed_price"}

    def _normalize_phase55_history_key_component(self, value):
        value = str(value or "").strip().lower()
        if not value:
            return ""
        return "_".join(value.split())

    def _build_phase55_timing_history_key(self, market, q, family=None):
        market = market if isinstance(market, dict) else {}
        ticker = getattr(q, "ticker", "") or str(market.get("ticker", "") or "").strip()
        family_value = family or self.get_market_family(market)
        series_ticker = str(market.get("series_ticker", "") or "").strip()
        event_ticker = str(market.get("event_ticker", "") or "").strip()
        normalized_family = self._normalize_phase55_history_key_component(family_value)
        normalized_series = self._normalize_phase55_history_key_component(series_ticker)
        normalized_event = self._normalize_phase55_history_key_component(event_ticker)
        normalized_ticker = self._normalize_phase55_history_key_component(ticker)
        if normalized_event in {normalized_family, normalized_series}:
            normalized_event = ""
        if normalized_event and normalized_family and normalized_event.startswith(f"{normalized_family}-"):
            normalized_event = ""
        elif normalized_event and normalized_series and normalized_event.startswith(f"{normalized_series}-"):
            normalized_event = ""
        key_parts = [normalized_family, normalized_series, normalized_event]
        key = "|".join(part for part in key_parts if part)
        if key:
            return key
        return normalized_ticker

    def _get_phase55_cycle_id(self):
        current_marker = str(self.phase55_current_select_pass_marker or "").strip()
        if current_marker:
            return current_marker
        return f"run_cycle_{int(self.cycle_count or 0)}"

    def _start_phase55_select_pass(self):
        self.phase55_select_pass_counter += 1
        self.phase55_current_select_pass_marker = (
            f"run_cycle_{int(self.cycle_count or 0)}_select_pass_{self.phase55_select_pass_counter}"
        )
        return self.phase55_current_select_pass_marker

    def _build_phase55_rolling_history_marker(self, timing_history_key, stage_label):
        timing_history_key = str(timing_history_key or "").strip()
        stage_label = str(stage_label or "entry_timing").strip() or "entry_timing"
        cycle_id = self._get_phase55_cycle_id()
        return f"{cycle_id}:{stage_label}:{timing_history_key}"

    def _append_phase55_rolling_price_history(self, timing_history_key, price, marker=None):
        timing_history_key = str(timing_history_key or "").strip()
        if not timing_history_key:
            return {"history": [], "appended": False, "append_reason": "missing_history_key"}
        history = list(self.phase55_rolling_price_history.get(timing_history_key, []))
        previous_marker = str(self.phase55_rolling_price_marker_cache.get(timing_history_key, "") or "")
        normalized_price = self.to_float(price, None)
        if normalized_price is None or normalized_price <= 0:
            return {"history": history, "appended": False, "append_reason": "insufficient_price_input"}
        if marker and previous_marker == marker:
            return {"history": history, "appended": False, "append_reason": "true_same_cycle_duplicate"}
        previous_last_price = history[-1] if history else None
        history.append(normalized_price)
        history = history[-16:]
        self.phase55_rolling_price_history[timing_history_key] = history
        if marker:
            self.phase55_rolling_price_marker_cache[timing_history_key] = marker
        append_reason = "later_cycle_appended"
        if previous_last_price is not None and abs(previous_last_price - normalized_price) < 1e-9:
            append_reason = "same_price_but_new_cycle_appended"
        return {"history": history, "appended": True, "append_reason": append_reason}

    def _fetch_phase55_market_history(self, ticker):
        ticker = str(ticker or "").strip()
        if not ticker:
            return {
                "series": [],
                "stochastic_source": "unavailable",
                "history_points": 0,
                "history_reason": "missing_ticker",
            }
        cached = self.phase55_market_history_cache.get(ticker)
        if isinstance(cached, dict) and (time.time() - cached.get("ts", 0.0)) <= 60.0:
            return dict(cached.get("context", {}))
        path = f"/trade-api/v2/markets/{ticker}"
        url = self.build_live_request_url(path) if globals().get("LIVE_MODE", False) else f"{BASE_URL}/markets/{ticker}"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            payload = response.json() if response.content else {}
        except requests.RequestException as exc:
            context = {
                "series": [],
                "stochastic_source": "unavailable",
                "history_points": 0,
                "history_reason": exc.__class__.__name__,
            }
            self.phase55_market_history_cache[ticker] = {"ts": time.time(), "context": context}
            return context
        except ValueError:
            context = {
                "series": [],
                "stochastic_source": "unavailable",
                "history_points": 0,
                "history_reason": "invalid_json",
            }
            self.phase55_market_history_cache[ticker] = {"ts": time.time(), "context": context}
            return context
        market_payload = payload.get("market", payload) if isinstance(payload, dict) else {}
        context = self._extract_phase55_price_series_context_from_payload(market_payload, source_label="fetched_market_history")
        if not context.get("series"):
            context = self._build_phase55_snapshot_series(market_payload, None)
            if context.get("series"):
                context["stochastic_source"] = "fetched_market_snapshot"
            else:
                context["history_reason"] = f"fetched_market.{context.get('history_reason', 'no_price_series')}"
        self.phase55_market_history_cache[ticker] = {"ts": time.time(), "context": dict(context)}
        return context

    def _phase55_required_history_points(self, lookback=5, d_period=3):
        return max(lookback + d_period, lookback + 2)

    def _compute_phase55_stochastic_from_series(self, prices, lookback=5, d_period=3):
        prices = [self.to_float(price, None) for price in prices]
        prices = [price for price in prices if price is not None and price > 0]
        if len(prices) < max(lookback + d_period, lookback + 2):
            return None
        k_values = []
        for idx in range(lookback - 1, len(prices)):
            window = prices[idx - lookback + 1:idx + 1]
            window_high = max(window)
            window_low = min(window)
            close = prices[idx]
            if window_high <= window_low:
                k_value = 50.0
            else:
                k_value = ((close - window_low) / (window_high - window_low)) * 100.0
            k_values.append(round(k_value, 4))
        if len(k_values) < d_period:
            return None
        d_values = []
        for idx in range(d_period - 1, len(k_values)):
            d_window = k_values[idx - d_period + 1:idx + 1]
            d_values.append(round(sum(d_window) / float(len(d_window)), 4))
        if not d_values:
            return None
        return {
            "stochastic_k": k_values[-1],
            "stochastic_d": d_values[-1],
            "previous_stochastic_k": k_values[-2] if len(k_values) >= 2 else None,
            "previous_stochastic_d": d_values[-2] if len(d_values) >= 2 else None,
        }

    def _get_phase55_stochastic_context(self, market, q, stage_label="entry_timing", family=None):
        ticker = getattr(q, "ticker", "") or str((market or {}).get("ticker", "") or "").strip()
        required_points = self._phase55_required_history_points()
        direct_k = self._extract_entry_timing_float(
            market, q, ["stochastic_k", "stoch_k", "stochK", "stochasticK", "k_value", "k"]
        )
        direct_d = self._extract_entry_timing_float(
            market, q, ["stochastic_d", "stoch_d", "stochD", "stochasticD", "d_value", "d"]
        )
        direct_prev_k = self._extract_entry_timing_float(
            market, q, ["previous_stochastic_k", "prev_stochastic_k", "previous_stoch_k", "prev_stoch_k", "previous_k", "prev_k"]
        )
        direct_prev_d = self._extract_entry_timing_float(
            market, q, ["previous_stochastic_d", "prev_stochastic_d", "previous_stoch_d", "prev_stoch_d", "previous_d", "prev_d"]
        )
        timing_history_key = self._build_phase55_timing_history_key(market, q, family=family)
        cycle_id = self._get_phase55_cycle_id()
        if direct_k is not None and direct_d is not None:
            return {
                "stochastic_inputs_available": True,
                "stochastic_k": direct_k,
                "stochastic_d": direct_d,
                "previous_stochastic_k": direct_prev_k,
                "previous_stochastic_d": direct_prev_d,
                "stochastic_source": "direct_fields",
                "history_points": 0,
                "required_points": required_points,
                "history_reason": "direct_stochastic_fields",
                "timing_history_key": timing_history_key,
                "history_appended": False,
                "history_append_reason": "direct_fields_available",
                "cycle_id": cycle_id,
            }

        best_context = {
            "stochastic_inputs_available": False,
            "stochastic_k": None,
            "stochastic_d": None,
            "previous_stochastic_k": None,
            "previous_stochastic_d": None,
            "stochastic_source": "unavailable",
            "history_points": 0,
            "required_points": required_points,
            "history_reason": "no_price_series",
            "timing_history_key": timing_history_key,
            "history_appended": False,
            "history_append_reason": "not_attempted",
            "cycle_id": cycle_id,
        }

        observation_context = self._get_phase55_observed_price_point(market, q)
        observation_marker = self._build_phase55_rolling_history_marker(timing_history_key, stage_label)
        rolling_history_result = self._append_phase55_rolling_price_history(
            timing_history_key,
            observation_context.get("price"),
            marker=str(observation_marker),
        )
        rolling_history = rolling_history_result.get("history", [])
        best_context["history_appended"] = bool(rolling_history_result.get("appended"))
        best_context["history_append_reason"] = rolling_history_result.get("append_reason", "not_attempted")
        if rolling_history:
            computed = self._compute_phase55_stochastic_from_series(rolling_history)
            if computed:
                computed.update(
                    {
                        "stochastic_inputs_available": True,
                        "stochastic_source": "rolling_history",
                        "history_points": len(rolling_history),
                        "required_points": required_points,
                        "history_reason": observation_context.get("history_reason", "rolling_history"),
                        "timing_history_key": timing_history_key,
                        "history_appended": bool(rolling_history_result.get("appended")),
                        "history_append_reason": rolling_history_result.get("append_reason", "observed_price_appended"),
                        "cycle_id": cycle_id,
                    }
                )
                return computed
            best_context.update(
                {
                    "stochastic_source": "rolling_history",
                    "history_points": len(rolling_history),
                    "history_reason": f"insufficient_history_points_{len(rolling_history)}_of_{required_points}:{observation_context.get('history_reason', 'rolling_history')}",
                }
            )
        elif observation_context.get("history_reason") != "no_observed_price":
            best_context.update(
                {
                    "stochastic_source": "unavailable",
                    "history_points": 0,
                    "history_reason": observation_context.get("history_reason", "no_observed_price"),
                }
            )

        inline_context = self._extract_phase55_price_series_context_from_payload(market, source_label="market_payload")
        inline_series = inline_context.get("series", [])
        if inline_series:
            computed = self._compute_phase55_stochastic_from_series(inline_series)
            if computed:
                computed.update(
                    {
                        "stochastic_inputs_available": True,
                        "stochastic_source": inline_context.get("stochastic_source", "market_payload"),
                        "history_points": len(inline_series),
                        "required_points": required_points,
                        "history_reason": inline_context.get("history_reason", "price_history"),
                        "timing_history_key": timing_history_key,
                        "history_appended": bool(rolling_history_result.get("appended")),
                        "history_append_reason": rolling_history_result.get("append_reason", "not_attempted"),
                        "cycle_id": cycle_id,
                    }
                )
                return computed
            if len(inline_series) > best_context.get("history_points", 0):
                best_context.update(
                    {
                        "stochastic_source": inline_context.get("stochastic_source", "market_payload"),
                        "history_points": len(inline_series),
                        "history_reason": f"insufficient_history_points_{len(inline_series)}_of_{required_points}:{inline_context.get('history_reason', 'price_history')}",
                    }
                )

        snapshot_context = self._build_phase55_snapshot_series(market, q)
        snapshot_series = snapshot_context.get("series", [])
        if snapshot_series:
            computed = self._compute_phase55_stochastic_from_series(snapshot_series)
            if computed:
                computed.update(
                    {
                        "stochastic_inputs_available": True,
                        "stochastic_source": snapshot_context.get("stochastic_source", "market_snapshot"),
                        "history_points": len(snapshot_series),
                        "required_points": required_points,
                        "history_reason": snapshot_context.get("history_reason", "snapshot_cache"),
                        "timing_history_key": timing_history_key,
                        "history_appended": bool(rolling_history_result.get("appended")),
                        "history_append_reason": rolling_history_result.get("append_reason", "not_attempted"),
                        "cycle_id": cycle_id,
                    }
                )
                return computed
            if len(snapshot_series) > best_context.get("history_points", 0):
                best_context.update(
                    {
                        "stochastic_source": snapshot_context.get("stochastic_source", "market_snapshot"),
                        "history_points": len(snapshot_series),
                        "history_reason": f"insufficient_history_points_{len(snapshot_series)}_of_{required_points}:{snapshot_context.get('history_reason', 'snapshot_cache')}",
                    }
                )
        elif snapshot_context.get("history_reason") != "no_snapshot_prices" and best_context.get("history_points", 0) == 0:
            best_context.update(
                {
                    "stochastic_source": snapshot_context.get("stochastic_source", "market_snapshot"),
                    "history_reason": snapshot_context.get("history_reason", "no_snapshot_prices"),
                }
            )

        fetched_context = self._fetch_phase55_market_history(ticker)
        fetched_series = fetched_context.get("series", [])
        if fetched_series:
            computed = self._compute_phase55_stochastic_from_series(fetched_series)
            if computed:
                computed.update(
                    {
                        "stochastic_inputs_available": True,
                        "stochastic_source": fetched_context.get("stochastic_source", "fetched_market_history"),
                        "history_points": len(fetched_series),
                        "required_points": required_points,
                        "history_reason": fetched_context.get("history_reason", "fetched_history"),
                        "timing_history_key": timing_history_key,
                        "history_appended": bool(rolling_history_result.get("appended")),
                        "history_append_reason": rolling_history_result.get("append_reason", "not_attempted"),
                        "cycle_id": cycle_id,
                    }
                )
                return computed
            if len(fetched_series) > best_context.get("history_points", 0):
                best_context.update(
                    {
                        "stochastic_source": fetched_context.get("stochastic_source", "fetched_market_history"),
                        "history_points": len(fetched_series),
                        "history_reason": f"insufficient_history_points_{len(fetched_series)}_of_{required_points}:{fetched_context.get('history_reason', 'fetched_history')}",
                    }
                )
        elif best_context.get("history_points", 0) == 0:
            best_context.update(
                {
                    "stochastic_source": fetched_context.get("stochastic_source", "unavailable"),
                    "history_points": fetched_context.get("history_points", 0),
                    "history_reason": fetched_context.get("history_reason", "no_price_series"),
                }
            )

        return best_context

    def get_entry_timing_observation(self, market, q, family, stage_label="entry_timing"):
        current_price = getattr(q, "yes_ask", 0.0) or 0.0
        best_bid = getattr(q, "yes_bid", 0.0) or 0.0
        last_price = getattr(q, "last_price", 0.0) or 0.0
        stochastic_context = self._get_phase55_stochastic_context(market, q, stage_label=stage_label, family=family)
        stochastic_k = stochastic_context.get("stochastic_k")
        stochastic_d = stochastic_context.get("stochastic_d")
        previous_k = stochastic_context.get("previous_stochastic_k")
        previous_d = stochastic_context.get("previous_stochastic_d")
        stochastic_source = stochastic_context.get("stochastic_source", "unavailable")
        history_points = int(stochastic_context.get("history_points", 0) or 0)
        required_points = int(stochastic_context.get("required_points", self._phase55_required_history_points()) or self._phase55_required_history_points())
        history_reason = str(stochastic_context.get("history_reason", "no_price_series") or "no_price_series")
        timing_history_key = str(stochastic_context.get("timing_history_key", "") or "")
        history_appended = bool(stochastic_context.get("history_appended"))
        history_append_reason = str(stochastic_context.get("history_append_reason", "not_attempted") or "not_attempted")
        cycle_id = str(stochastic_context.get("cycle_id", self._get_phase55_cycle_id()) or self._get_phase55_cycle_id())
        stochastic_inputs_available = bool(stochastic_context.get("stochastic_inputs_available")) and stochastic_k is not None and stochastic_d is not None
        bullish_crossover = bool(
            stochastic_inputs_available
            and previous_k is not None
            and previous_d is not None
            and previous_k <= previous_d
            and stochastic_k > stochastic_d
        )
        bearish_crossover = bool(
            stochastic_inputs_available
            and previous_k is not None
            and previous_d is not None
            and previous_k >= previous_d
            and stochastic_k < stochastic_d
        )
        crossover_detected = bool(bullish_crossover or bearish_crossover)
        micro_pullback_detected = False
        micro_pullback_flag = None
        if market is not None and isinstance(market, dict):
            micro_pullback_flag = market.get("micro_pullback")
            if micro_pullback_flag is None:
                micro_pullback_flag = market.get("micro_pullback_detected")
        if micro_pullback_flag is not None:
            micro_pullback_detected = bool(micro_pullback_flag)
        immediate_entry_blocked = False
        immediate_entry_flag = None
        if market is not None and isinstance(market, dict):
            immediate_entry_flag = market.get("immediate_entry_blocked")
            if immediate_entry_flag is None:
                immediate_entry_flag = market.get("avoid_immediate_entry")
            if immediate_entry_flag is None:
                immediate_entry_flag = market.get("post_signal_entry_blocked")
        if immediate_entry_flag is not None:
            immediate_entry_blocked = bool(immediate_entry_flag)
        trace_live_entry = stage_label == "entry_timing" and self._should_trace_priority_topbook_family(family)
        trace_ticker = getattr(q, "ticker", "") or str((market or {}).get("ticker", "") or "").strip()
        if stage_label == "pre_strict_validation":
            print(
                f"[LIVE-ENTRY-TIMING-SHADOW-BUFFER] ticker={trace_ticker} family={family} "
                f"cycle_id={cycle_id} timing_history_key={timing_history_key} history_points={history_points} "
                f"appended={'yes' if history_appended else 'no'} append_reason={history_append_reason}"
            )
        if not stochastic_inputs_available:
            phase55_status = "insufficient_data"
        elif immediate_entry_blocked:
            phase55_status = "immediate_reject"
        elif crossover_detected:
            if micro_pullback_detected:
                phase55_status = "entry_ready"
            else:
                phase55_status = "pullback_wait"
        else:
            phase55_status = "trigger_wait"
        final_decision = "enter" if phase55_status == "entry_ready" else ("reject" if phase55_status == "immediate_reject" else "wait")
        reject_reason = {
            "insufficient_data": "insufficient_history",
            "trigger_wait": "no_stochastic_crossover",
            "pullback_wait": "no_micro_pullback",
            "immediate_reject": "immediate_entry_protection",
            "entry_ready": "None",
        }.get(phase55_status, phase55_status)
        if trace_live_entry:
            print(
                f"[LIVE-ENTRY-TIMING-HISTORY] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
            print(
                f"[LIVE-ENTRY-TIMING-STOCHASTIC] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"stochastic_k={None if stochastic_k is None else round(stochastic_k, 4)} "
                f"stochastic_d={None if stochastic_d is None else round(stochastic_d, 4)} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
            print(
                f"[LIVE-ENTRY-TIMING-CROSSOVER] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"stochastic_k={None if stochastic_k is None else round(stochastic_k, 4)} "
                f"stochastic_d={None if stochastic_d is None else round(stochastic_d, 4)} "
                f"crossover_detected={'yes' if crossover_detected else 'no'} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
            print(
                f"[LIVE-ENTRY-TIMING-PULLBACK] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"pullback_detected={'yes' if micro_pullback_detected else 'no'} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
            print(
                f"[LIVE-ENTRY-TIMING-IMMEDIATE] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"immediate_block={'yes' if immediate_entry_blocked else 'no'} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
            print(
                f"[LIVE-ENTRY-TIMING-FINAL] ticker={trace_ticker} family={family} "
                f"history_points={history_points} required_points={required_points} "
                f"stochastic_k={None if stochastic_k is None else round(stochastic_k, 4)} "
                f"stochastic_d={None if stochastic_d is None else round(stochastic_d, 4)} "
                f"crossover_detected={'yes' if crossover_detected else 'no'} "
                f"pullback_detected={'yes' if micro_pullback_detected else 'no'} "
                f"immediate_block={'yes' if immediate_entry_blocked else 'no'} "
                f"final_decision={final_decision} reject_reason={reject_reason}"
            )
        return {
            "ticker": trace_ticker,
            "family": family,
            "current_price": round(current_price, 4),
            "best_bid": round(best_bid, 4),
            "last_price": round(last_price, 4),
            "stochastic_inputs_available": stochastic_inputs_available,
            "stochastic_source": stochastic_source,
            "history_points": history_points,
            "required_points": required_points,
            "history_reason": history_reason,
            "timing_history_key": timing_history_key,
            "history_appended": history_appended,
            "history_append_reason": history_append_reason,
            "cycle_id": cycle_id,
            "stochastic_k": None if stochastic_k is None else round(stochastic_k, 4),
            "stochastic_d": None if stochastic_d is None else round(stochastic_d, 4),
            "bullish_crossover": bullish_crossover,
            "bearish_crossover": bearish_crossover,
            "micro_pullback_detected": micro_pullback_detected,
            "immediate_entry_blocked": immediate_entry_blocked,
            "phase55_status": phase55_status,
        }

    def rank_final_candidates(self, candidates):
        return sorted(
            candidates,
            key=lambda item: (
                -item[0],
                item[2],
                -getattr(item[1], "liquidity", 0.0),
                -getattr(item[1], "volume", 0.0),
            ),
        )

    def score_final_candidate_decision(self, market, q, spread, family):
        ticker = getattr(q, "ticker", "") or str((market or {}).get("ticker", "") or "").strip()
        zero_breakdown = {
            "liquidity": 0.0,
            "spread": 0.0,
            "structure": 0.0,
            "timing": 0.0,
            "confidence": 0.0,
        }
        debug_context = {
            "ticker": ticker,
            "family": family,
            "yes_bid": getattr(q, "yes_bid", 0.0) or 0.0,
            "yes_ask": getattr(q, "yes_ask", 0.0) or 0.0,
            "spread": round(spread, 4),
            "liquidity": getattr(q, "liquidity", 0.0) or 0.0,
            "volume": getattr(q, "volume", 0.0) or 0.0,
            "minutes_to_event": None,
            "min_liquidity": None,
            "buy_threshold": PHASE5_BUY_SCORE_THRESHOLD,
            "hard_reject_reason": None,
            "timing_bucket": "unknown",
            "targeted_orderbook_ok": None,
        }
        if not market or not ticker:
            debug_context["hard_reject_reason"] = "missing_market_or_ticker"
            return {
                "decision": "SKIP",
                "decision_reason": "hard_filter_reject",
                "candidate_score": 0.0,
                "score_breakdown": zero_breakdown,
                "debug_context": debug_context,
            }
        print(f"[LIVE-SELECT-DEBUG] spread_threshold_used={MAX_ENTRY_SPREAD}")
        print(f"[LIVE-SELECT-DEBUG] candidate_spread_seen={spread}")
        if spread < 0 or spread > MAX_ENTRY_SPREAD:
            print("[LIVE-SELECT-DEBUG] final_spread_reject_reason=wide_spread")
            debug_context["hard_reject_reason"] = "wide_spread"
            return {
                "decision": "SKIP",
                "decision_reason": "hard_filter_reject",
                "candidate_score": 0.0,
                "score_breakdown": zero_breakdown,
                "debug_context": debug_context,
            }
        orderbook_ok = True
        orderbook_details = {
            "yes_top_price": None,
            "yes_top_size": 0.0,
            "no_top_price": None,
            "no_top_size": 0.0,
            "min_top_size": None,
        }
        if market and self.is_targeted_series_market(market):
            min_liquidity = self.get_market_min_entry_liquidity(market)
            seen_liquidity = getattr(q, "liquidity", 0.0) or 0.0
            series_ticker = self.get_targeted_series_ticker(market)
            debug_context["min_liquidity"] = min_liquidity
            print(f"[LIVE-SELECT-DEBUG] targeted_candidate_ticker={ticker}")
            print(f"[LIVE-SELECT-DEBUG] targeted_series_ticker={series_ticker}")
            print(f"[LIVE-SELECT-DEBUG] targeted_liquidity_threshold_used={min_liquidity}")
            print(f"[LIVE-SELECT-DEBUG] targeted_market_liquidity_seen={seen_liquidity}")
            print(f"[LIVE-SELECT-DEBUG] targeted_spread_seen={spread}")
            liquidity_pass = seen_liquidity > min_liquidity
            print(f"[LIVE-SELECT-DEBUG] targeted_liquidity_gate={'PASS' if liquidity_pass else 'FAIL'}")
            orderbook_ok, orderbook_details = self.passes_targeted_orderbook_quality(market, q)
            debug_context["targeted_orderbook_ok"] = orderbook_ok
            print(
                f"[LIVE-SELECT-DEBUG] targeted_top_of_book_threshold_used="
                f"{orderbook_details.get('min_top_size')}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_top_of_book_yes_price="
                f"{orderbook_details.get('yes_top_price')}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_top_of_book_yes_size="
                f"{orderbook_details.get('yes_top_size')}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_top_of_book_no_price="
                f"{orderbook_details.get('no_top_price')}"
            )
            print(
                f"[LIVE-SELECT-DEBUG] targeted_top_of_book_no_size="
                f"{orderbook_details.get('no_top_size')}"
            )
            print(f"[LIVE-SELECT-DEBUG] targeted_top_of_book_gate={'PASS' if orderbook_ok else 'FAIL'}")
            print("[LIVE-SELECT-DEBUG] generic_family_skip_bypassed_for_targeted_candidate=yes")
            targeted_liquidity_fallback_used = (
                series_ticker == "KXBTC15M" and (not liquidity_pass) and orderbook_ok
            )
            print(
                f"[LIVE-SELECT-DEBUG] final_targeted_liquidity_check "
                f"series={series_ticker} liquidity={seen_liquidity} threshold={min_liquidity} "
                f"top_book_ok={'yes' if orderbook_ok else 'no'} "
                f"fallback_eligible={'yes' if targeted_liquidity_fallback_used else 'no'}"
            )
            if targeted_liquidity_fallback_used:
                print(
                    f"[LIVE-SELECT-DEBUG] final_targeted_liquidity_fallback_used=yes "
                    f"ticker={ticker} series={series_ticker} liquidity={seen_liquidity} "
                    f"threshold={min_liquidity}"
                )
            if (not orderbook_ok) or ((not liquidity_pass) and not targeted_liquidity_fallback_used):
                reason = "low_liquidity" if not liquidity_pass else "targeted_orderbook"
                print(f"[LIVE-SELECT-DEBUG] final_targeted_reject_reason={reason}")
                debug_context["hard_reject_reason"] = reason
                return {
                    "decision": "SKIP",
                    "decision_reason": "hard_filter_reject",
                    "candidate_score": 0.0,
                    "score_breakdown": zero_breakdown,
                    "debug_context": debug_context,
                }
            print("[LIVE-SELECT-DEBUG] targeted_candidate_passed_prefilter=yes")
        min_liquidity = self.get_market_min_entry_liquidity(market)
        seen_liquidity = getattr(q, "liquidity", 0.0) or 0.0
        volume = getattr(q, "volume", 0.0) or 0.0
        debug_context["min_liquidity"] = min_liquidity
        liquidity_score = min(25.0, max(0.0, (seen_liquidity / max(min_liquidity, 1.0)) * 12.5))
        spread_score = min(25.0, max(0.0, ((MAX_ENTRY_SPREAD - spread) / MAX_ENTRY_SPREAD) * 25.0))
        structure_score = 8.0
        if self.is_targeted_series_market(market):
            if orderbook_ok:
                structure_score = 20.0
            elif orderbook_details.get("yes_top_price") is not None or orderbook_details.get("no_top_price") is not None:
                structure_score = 10.0
        elif q.yes_bid > 0 and q.yes_ask > 0 and q.no_bid > 0 and q.no_ask > 0:
            structure_score = 18.0
        elif q.yes_bid > 0 and q.yes_ask > 0:
            structure_score = 12.0
        minutes_to_event = self.get_minutes_to_event(market)
        debug_context["minutes_to_event"] = round(minutes_to_event, 4) if minutes_to_event is not None else None
        debug_context["timing_bucket"] = self.get_event_time_bucket(minutes_to_event)
        if minutes_to_event is None:
            timing_score = 0.0
        elif MIN_MINUTES_TO_EVENT <= minutes_to_event <= self.get_effective_max_minutes_to_event():
            timing_score = 15.0
        elif minutes_to_event < MIN_MINUTES_TO_EVENT:
            timing_score = 2.0
        else:
            timing_score = 6.0
        confidence_score = min(
            15.0,
            max(0.0, (min(volume, 100.0) / 100.0) * 7.5 + (spread_score / 25.0) * 7.5),
        )
        total_score = round(
            liquidity_score + spread_score + structure_score + timing_score + confidence_score,
            4,
        )
        score_breakdown = {
            "liquidity": round(liquidity_score, 4),
            "spread": round(spread_score, 4),
            "structure": round(structure_score, 4),
            "timing": round(timing_score, 4),
            "confidence": round(confidence_score, 4),
        }
        if total_score >= PHASE5_BUY_SCORE_THRESHOLD:
            decision = "BUY"
            decision_reason = "score_above_threshold"
        elif liquidity_score < 10.0:
            decision = "SKIP"
            decision_reason = "low_liquidity_score"
        elif spread_score < 10.0:
            decision = "SKIP"
            decision_reason = "poor_spread_score"
        elif structure_score < 10.0:
            decision = "SKIP"
            decision_reason = "weak_structure"
        elif timing_score < 5.0:
            decision = "SKIP"
            decision_reason = "timing_penalty"
        else:
            decision = "SKIP"
            decision_reason = "low_score"
        return {
            "decision": decision,
            "decision_reason": decision_reason,
            "candidate_score": total_score,
            "score_breakdown": score_breakdown,
            "debug_context": debug_context,
        }

    def find_best_live_ticker(self, markets=None):
        full_select_cycle_start = time.perf_counter()
        self._start_phase55_select_pass()

        def emit_full_select_cycle_timing():
            print(
                f"[LIVE-SELECT-TIMING] stage=full_select_cycle seconds={round(time.perf_counter() - full_select_cycle_start, 6)}"
            )

        spread_skip_count = 0
        selection_merge_source = "provided_markets" if markets is not None else "unresolved"
        current_hour_utc = time.gmtime().tm_hour
        targeted_in_window = []
        targeted_in_window_family_counts = {}
        broad_in_window = []
        fallback_to_broad_scan = False
        targeted_path_weak = False
        broad_time_extension_triggered = False
        broad_in_window_family_observation = {}
        targeted_selection_count = 0
        broad_selection_count = 0
        merged_selection_count = len(markets) if markets is not None else 0
        cycle_targeted_dead_book_suppressed_families = []
        self.targeted_orderbook_cache = {}
        targeted_family_activity = {
            family: {
                "fetched": 0,
                "in_window": 0,
                "non_empty_top_book": 0,
                "both_sides_quoted": 0,
                "min_top_size_met": 0,
                "prefilter_survivors": 0,
                "targeted_orderbook_pass": 0,
                "targeted_orderbook_fail": 0,
                "usable_top_book": 0,
                "empty_top_book": 0,
                "empty_top_book_fail": 0,
                "one_sided_quote_fail": 0,
                "min_top_size_fail": 0,
                "prefilter_to_strict_removed": 0,
                "prefilter_to_strict_removed_reasons": {},
                "narrow_spread_count": 0,
                "recent_activity_count": 0,
                "targeted_orderbook_buckets": {},
            }
            for family in (TARGETED_SERIES_TICKERS + OPTIONAL_TARGETED_SERIES_TICKERS)
        }
        broad_origin_families = set()
        btc15m_near_miss_count = 0
        btc15m_in_window_count = 0
        btc15m_usable_top_book_count = 0
        btc15m_zero_top_book_count = 0
        btc15m_near_miss_cycle_tickers = set()
        btc15m_watchlist_tickers = []
        btc15m_current_cycle_in_window_tickers = set()
        adaptive_probe_mode = fallback_to_broad_scan or targeted_path_weak
        family_adaptive_probe_pool = []
        family_adaptive_probe_meta = {}
        family_probe_entered_count = 0
        family_probe_passed_count = 0
        family_probe_failed_count = 0
        family_probe_blocker_counts = {}
        family_probe_families = set()
        family_probe_strict_liquidity_failures = 0
        family_probe_strict_zero_top_book_count = 0
        family_probe_strict_one_sided_top_book_count = 0
        family_probe_strict_both_sides_top_book_count = 0

        def emit_phase5_observation_logs(candidates_found, no_candidate_reason):
            print(
                f"[LIVE-SELECT-OBSERVE] hour_utc={current_hour_utc} targeted_in_window={len(targeted_in_window)} "
                f"broad_in_window={len(broad_in_window)} candidates_found={candidates_found} "
                f"fallback={'yes' if fallback_to_broad_scan else 'no'} broad_time_extension_triggered={'yes' if broad_time_extension_triggered else 'no'}"
            )
            for family in sorted(broad_in_window_family_observation.keys()):
                stats = broad_in_window_family_observation.get(family, {})
                print(
                    f"[LIVE-SELECT-OBSERVE] broad_family hour_utc={current_hour_utc} family={family} "
                    f"in_window_count={stats.get('in_window_count', 0)} "
                    f"both_sides_quoted_count={stats.get('both_sides_quoted_count', 0)} "
                    f"one_sided_top_book_count={stats.get('one_sided_top_book_count', 0)} "
                    f"narrow_spread_count={stats.get('narrow_spread_count', 0)} "
                    f"recent_activity_count={stats.get('recent_activity_count', 0)}"
                )
            for family in sorted(targeted_in_window_family_counts.keys()):
                stats = targeted_family_activity.get(family, {})
                print(
                    f"[LIVE-SELECT-OBSERVE] targeted_family hour_utc={current_hour_utc} family={family} "
                    f"in_window_count={stats.get('in_window', 0)} usable_top_book_count={stats.get('usable_top_book', 0)} "
                    f"narrow_spread_count={stats.get('narrow_spread_count', 0)} recent_activity_count={stats.get('recent_activity_count', 0)}"
                )
            top_targeted_families = sorted(
                (
                    (
                        family,
                        stats.get("usable_top_book", 0),
                        stats.get("narrow_spread_count", 0),
                        stats.get("recent_activity_count", 0),
                    )
                    for family, stats in targeted_family_activity.items()
                    if stats.get("in_window", 0) > 0
                ),
                key=lambda item: (item[1], item[2], item[3], targeted_family_activity.get(item[0], {}).get("in_window", 0)),
                reverse=True,
            )[:5]
            top_broad_families = sorted(
                (
                    (
                        family,
                        stats.get("in_window_count", 0),
                        stats.get("both_sides_quoted_count", 0),
                        stats.get("narrow_spread_count", 0),
                    )
                    for family, stats in broad_in_window_family_observation.items()
                    if stats.get("in_window_count", 0) > 0
                ),
                key=lambda item: (item[1], item[2], item[3]),
                reverse=True,
            )[:5]
            print(
                f"[LIVE-SELECT-OBSERVE] cycle hour_utc={current_hour_utc} top_targeted_families={top_targeted_families} "
                f"top_broad_families={top_broad_families} no_candidate_reason={no_candidate_reason}"
            )
            print(
                f"[LIVE-SELECT-OBSERVE] btc15m_near_miss_summary hour_utc={current_hour_utc} "
                f"count={btc15m_near_miss_count} in_window_count={btc15m_in_window_count} "
                f"usable_top_book_count={btc15m_usable_top_book_count} zero_top_book_count={btc15m_zero_top_book_count}"
            )
            print(
                f"[LIVE-SELECT-OBSERVE] btc15m_watchlist_summary count={len(btc15m_watchlist_tickers)} "
                f"tickers={btc15m_watchlist_tickers}"
            )

        if markets is None:
            if ENABLE_TARGETED_SERIES_MODE:
                targeted_markets = self.fetch_targeted_series_markets()
                for market in targeted_markets:
                    family = self.get_market_family(market)
                    self._emit_priority_upstream_trace(
                        "[LIVE-SELECT-UPSTREAM-SOURCE]",
                        market,
                        "targeted_fetch_result",
                        source_stage="fetch_targeted_series_markets",
                        handoff_target="targeted_in_window_builder",
                        object_path="targeted_markets_list",
                    )
                    if family in targeted_family_activity:
                        targeted_family_activity[family]["fetched"] += 1
                targeted_in_window = [
                    market for market in targeted_markets
                    if self.get_time_window_inclusion_reason(self.get_minutes_to_event(market))
                ]
                print(f"[LIVE-SELECT-DEBUG] targeted_markets_in_window={len(targeted_in_window)}")
                targeted_in_window_family_counts = {}
                targeted_selected_usable_top_book = 0
                targeted_selected_empty_top_book = 0
                for market in targeted_in_window:
                    family = self.get_market_family(market)
                    if family in targeted_family_activity:
                        targeted_family_activity[family]["in_window"] += 1
                        spread = (
                            self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")))
                            - self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")))
                        )
                        self._emit_priority_upstream_trace(
                            "[LIVE-SELECT-GATE-INPUT]",
                            market,
                            "targeted_gate_callsite",
                            source_stage="targeted_in_window_loop",
                            handoff_target="_evaluate_targeted_orderbook_gate",
                        )
                        _passed, details = self._evaluate_targeted_orderbook_gate(
                            market,
                            spread,
                        )
                        recent_activity = self.to_float(market.get("volume_fp", market.get("volume")), 0.0) > 0
                        if spread >= 0 and spread <= (MAX_ENTRY_SPREAD / 2.0):
                            targeted_family_activity[family]["narrow_spread_count"] += 1
                        if recent_activity:
                            targeted_family_activity[family]["recent_activity_count"] += 1
                        bucket = details.get("top_book_bucket", "unknown")
                        ticker = str(market.get("ticker", "") or "").strip()
                        yes_top_size = details.get("yes_top_size", 0.0) or 0.0
                        no_top_size = details.get("no_top_size", 0.0) or 0.0
                        if yes_top_size > 0 and no_top_size > 0:
                            btc15m_top_book_state = "usable_top_book"
                        elif yes_top_size > 0 or no_top_size > 0:
                            btc15m_top_book_state = "one_sided_top_book"
                        else:
                            btc15m_top_book_state = "zero_top_book"
                        both_sides_quoted = bool(
                            details.get("yes_top_price") is not None and details.get("no_top_price") is not None
                        )
                        cache_keys = sorted(
                            key for key, value in details.items()
                            if value not in (None, "", [], {}, 0.0)
                        )
                        self._emit_priority_upstream_trace(
                            "[LIVE-SELECT-UPSTREAM-HANDOFF]",
                            market,
                            "targeted_orderbook_gate_return",
                            source_stage="_evaluate_targeted_orderbook_gate",
                            handoff_target="targeted_orderbook_cache",
                            cache_payload_present='yes' if bool(cache_keys) else 'no',
                            cache_orderbook_source=details.get("orderbook_source") or 'none',
                            cache_keys=cache_keys,
                            object_identity_changed='no',
                        )
                        if family == "KXBTC15M":
                            btc15m_in_window_count += 1
                            btc15m_current_cycle_in_window_tickers.add(ticker)
                            if btc15m_top_book_state == "usable_top_book":
                                btc15m_usable_top_book_count += 1
                            if btc15m_top_book_state == "zero_top_book":
                                btc15m_zero_top_book_count += 1
                            if (
                                spread >= 0 and spread <= (MAX_ENTRY_SPREAD / 2.0)
                                and recent_activity
                                and btc15m_top_book_state == "zero_top_book"
                            ):
                                btc15m_near_miss_count += 1
                                btc15m_near_miss_cycle_tickers.add(ticker)
                                consecutive_cycles = self.btc15m_near_miss_persistence.get(ticker, 0) + 1
                                self.btc15m_near_miss_persistence[ticker] = consecutive_cycles
                                print(
                                    f"[LIVE-SELECT-OBSERVE] btc15m_near_miss hour_utc={current_hour_utc} "
                                    f"ticker={ticker} spread={round(spread, 4)} recent_activity={recent_activity} "
                                    f"yes_top_size={yes_top_size} no_top_size={no_top_size} "
                                    f"minutes_to_event={round(self.get_minutes_to_event(market), 4) if self.get_minutes_to_event(market) is not None else None} "
                                    f"both_sides_quoted={both_sides_quoted}"
                                )
                                print(
                                    f"[LIVE-SELECT-OBSERVE] btc15m_near_miss_persist ticker={ticker} "
                                    f"consecutive_cycles={consecutive_cycles} spread={round(spread, 4)} "
                                    f"minutes_to_event={round(self.get_minutes_to_event(market), 4) if self.get_minutes_to_event(market) is not None else None} "
                                    f"recent_activity={recent_activity}"
                                )
                                if consecutive_cycles > 1:
                                    btc15m_watchlist_tickers.append(ticker)
                                    print(
                                        f"[LIVE-SELECT-OBSERVE] btc15m_promoted_watchlist ticker={ticker} "
                                        f"consecutive_cycles={consecutive_cycles} reason=persistent_near_miss"
                                    )
                            else:
                                if ticker in self.btc15m_near_miss_persistence:
                                    self.btc15m_near_miss_persistence.pop(ticker, None)
                            previous_btc15m_state = self.btc15m_top_book_observation_state.get(ticker)
                            if previous_btc15m_state == "zero_top_book" and btc15m_top_book_state == "usable_top_book":
                                print(
                                    f"[LIVE-SELECT-OBSERVE] btc15m_state_change ticker={ticker} previous_state=zero_top_book "
                                    f"new_state=usable_top_book spread={round(spread, 4)} recent_activity={recent_activity}"
                                )
                            self.btc15m_top_book_observation_state[ticker] = btc15m_top_book_state
                        if _passed:
                            targeted_family_activity[family]["targeted_orderbook_pass"] += 1
                        else:
                            targeted_family_activity[family]["targeted_orderbook_fail"] += 1
                        targeted_family_activity[family]["targeted_orderbook_buckets"][bucket] = (
                            targeted_family_activity[family]["targeted_orderbook_buckets"].get(bucket, 0) + 1
                        )
                        if bucket in ("pass", "top_size_fail"):
                            targeted_family_activity[family]["usable_top_book"] += 1
                            targeted_selected_usable_top_book += 1
                        if bucket in ("both_sides_zero", "yes_zero_only", "no_zero_only"):
                            targeted_family_activity[family]["empty_top_book"] += 1
                            targeted_selected_empty_top_book += 1
                        if bucket == "both_sides_zero":
                            targeted_family_activity[family]["empty_top_book_fail"] += 1
                        elif bucket in ("yes_zero_only", "no_zero_only"):
                            targeted_family_activity[family]["one_sided_quote_fail"] += 1
                        elif bucket == "top_size_fail":
                            targeted_family_activity[family]["min_top_size_fail"] += 1
                        if (
                            details.get("yes_top_price") is not None
                            or details.get("no_top_price") is not None
                            or details.get("yes_top_size", 0.0) > 0
                            or details.get("no_top_size", 0.0) > 0
                        ):
                            targeted_family_activity[family]["non_empty_top_book"] += 1
                        if (
                            details.get("yes_top_price") is not None
                            and details.get("no_top_price") is not None
                        ):
                            targeted_family_activity[family]["both_sides_quoted"] += 1
                        if (
                            details.get("yes_top_size", 0.0) >= details.get("min_top_size", 0.0)
                            and details.get("no_top_size", 0.0) >= details.get("min_top_size", 0.0)
                        ):
                            targeted_family_activity[family]["min_top_size_met"] += 1
                    targeted_in_window_family_counts[family] = targeted_in_window_family_counts.get(family, 0) + 1
                stale_btc15m_near_miss_tickers = [
                    ticker for ticker in list(self.btc15m_near_miss_persistence.keys())
                    if ticker not in btc15m_near_miss_cycle_tickers
                ]
                for ticker in stale_btc15m_near_miss_tickers:
                    self.btc15m_near_miss_persistence.pop(ticker, None)
                btc15m_watchlist_tickers = sorted(set(btc15m_watchlist_tickers))
                for family, count in sorted(targeted_in_window_family_counts.items()):
                    print(f"[LIVE-SELECT-DEBUG] targeted_in_window family={family} count={count}")
                print(
                    f"[LIVE-SELECT-COUNT] stage=targeted_selection_top_book before_coarse_prefilter selected={len(targeted_in_window)} "
                    f"usable_top_book={targeted_selected_usable_top_book} empty_top_book={targeted_selected_empty_top_book}"
                )
                for family in sorted(targeted_in_window_family_counts.keys()):
                    stats = targeted_family_activity.get(family, {})
                    print(
                        f"[LIVE-SELECT-DEBUG] targeted_orderbook_family_summary family={family} "
                        f"pass_count={stats.get('targeted_orderbook_pass', 0)} fail_count={stats.get('targeted_orderbook_fail', 0)} "
                        f"usable_top_book={stats.get('usable_top_book', 0)} empty_top_book={stats.get('empty_top_book', 0)} "
                        f"buckets={sorted((stats.get('targeted_orderbook_buckets') or {}).items(), key=lambda item: item[1], reverse=True)}"
                    )
                targeted_markets_in_window = len(targeted_in_window)
                targeted_insufficient = (
                    targeted_markets_in_window <= 3
                    or (targeted_markets_in_window > 0 and targeted_selected_usable_top_book <= 0)
                )
                targeted_path_weak = targeted_insufficient
                fallback_to_broad_scan = (not targeted_in_window) or targeted_insufficient
                prioritized_in_window = [family for family in self.get_prioritized_targeted_series() if family in targeted_in_window_family_counts]
                targeted_non_empty_top_book_count = sum(
                    stats.get("non_empty_top_book", 0) for stats in targeted_family_activity.values()
                )
                targeted_viable_count = sum(
                    1 for stats in targeted_family_activity.values()
                    if stats.get("prefilter_survivors", 0) > 0
                )
                fallback_reason = "targeted_sufficient"
                if not targeted_in_window:
                    fallback_reason = "no_targeted_in_window"
                elif targeted_markets_in_window <= 3:
                    fallback_reason = "targeted_in_window_count_below_threshold"
                elif targeted_selected_usable_top_book <= 0:
                    fallback_reason = "targeted_zero_usable_top_book"
                print(f"[LIVE-SELECT-DEBUG] prioritized_in_window_families={prioritized_in_window}")
                print(
                    f"[LIVE-SELECT-FALLBACK-DECISION] targeted_prefilter_survivors={targeted_viable_count} "
                    f"targeted_viable_count={targeted_viable_count} "
                    f"targeted_non_empty_top_book_count={targeted_non_empty_top_book_count} "
                    f"broad_enabled=yes broad_available=unknown fallback_to_broad={'yes' if fallback_to_broad_scan else 'no'} "
                    f"reason={fallback_reason}"
                )
                if targeted_insufficient:
                    print("[LIVE-SELECT-DEBUG] fallback_reason=targeted_insufficient")
                if not fallback_to_broad_scan:
                    print(
                        f"[LIVE-SELECT-FALLBACK-BYPASS] fallback_to_broad=no reason={fallback_reason} "
                        f"targeted_markets_in_window={targeted_markets_in_window} targeted_usable_top_book={targeted_selected_usable_top_book}"
                    )
                print(
                    f"[LIVE-SELECT-DEBUG] fallback_to_broad_scan={'yes' if fallback_to_broad_scan else 'no'}"
                )
                if targeted_in_window:
                    if fallback_to_broad_scan:
                        print(
                            f"[LIVE-SELECT-FALLBACK-ENTER] branch=targeted_in_window_with_broad hour_utc={current_hour_utc} "
                            f"reason={fallback_reason} targeted_markets_in_window={targeted_markets_in_window}"
                        )
                    broad_fetch_start = time.perf_counter()
                    broad_markets = self.fetch_broad_discovery_markets()
                    print(
                        f"[LIVE-SELECT-TIMING] stage=broad_fetch seconds={round(time.perf_counter() - broad_fetch_start, 6)}"
                    )
                    broad_in_window_filter_start = time.perf_counter()
                    broad_default_max_minutes = self.get_broad_discovery_max_minutes_to_event()
                    broad_in_window = [
                        market for market in broad_markets
                        if self.get_time_window_inclusion_reason(
                            self.get_minutes_to_event(market),
                            max_minutes_to_event=broad_default_max_minutes,
                        )
                    ]
                    broad_time_extension_triggered = False
                    broad_time_extension_reason = "broad_markets_in_window_present"
                    broad_time_extension_old_max = broad_default_max_minutes
                    broad_time_extension_new_max = broad_default_max_minutes
                    if fallback_to_broad_scan and not broad_in_window:
                        broad_time_extension_triggered = True
                        broad_time_extension_reason = "no_broad_markets_in_window"
                        broad_time_extension_new_max = broad_default_max_minutes + TIME_WINDOW_EXTENSION_MINUTES
                        broad_in_window = [
                            market for market in broad_markets
                            if self.get_time_window_inclusion_reason(
                                self.get_minutes_to_event(market),
                                max_minutes_to_event=broad_time_extension_new_max,
                            )
                        ]
                    elif not fallback_to_broad_scan:
                        broad_time_extension_reason = "fallback_disabled"
                    print(
                        f"[LIVE-SELECT-TIMING] stage=broad_in_window_filter seconds={round(time.perf_counter() - broad_in_window_filter_start, 6)}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_time_extension_triggered={'yes' if broad_time_extension_triggered else 'no'} "
                        f"reason={broad_time_extension_reason}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_time_extension_window old_max={broad_time_extension_old_max} "
                        f"new_max={broad_time_extension_new_max}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_time_extension_in_window_count={len(broad_in_window)}"
                    )
                    existing_families = (
                        set() if targeted_insufficient else set(targeted_in_window_family_counts.keys())
                    )
                    targeted_discovery_active = bool(
                        ENABLE_TARGETED_SERIES_MODE and targeted_in_window and not targeted_insufficient
                    )
                    broad_source_builder_start = time.perf_counter()
                    broad_source_origin_paths = {"fetch_broad_discovery_markets:time_window": len(broad_in_window)}
                    broad_source_prefilter_context = {
                        "targeted_in_window_families": sorted(existing_families),
                        "broad_markets_fetched": len(broad_markets),
                        "broad_in_window": len(broad_in_window),
                        "time_window_max_minutes": broad_time_extension_new_max,
                    }
                    broad_source_limit_context = {
                        "scan_batches": MAX_SCAN_BATCHES,
                        "select_max_markets": LIVE_SELECT_MAX_MARKETS,
                        "markets_fetched": len(broad_markets),
                        "markets_in_window": len(broad_in_window),
                    }
                    upstream_broad_family_source_raw = sorted(
                        {
                            str(market.get("series_ticker", "") or market.get("event_ticker", "") or self.get_market_family(market) or "").strip()
                            for market in broad_in_window
                            if str(market.get("series_ticker", "") or market.get("event_ticker", "") or self.get_market_family(market) or "").strip()
                        }
                    )
                    upstream_broad_family_source_normalized = sorted(
                        {
                            self.get_market_family(market)
                            for market in broad_in_window
                            if self.get_market_family(market)
                        }
                    )
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source_raw={upstream_broad_family_source_raw}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source_raw_count={len(upstream_broad_family_source_raw)}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source_normalized={upstream_broad_family_source_normalized}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source_normalized_count={len(upstream_broad_family_source_normalized)}")
                    if len(upstream_broad_family_source_raw) == 0:
                        broad_source_raw_empty_cause = "no_broad_markets_in_window"
                        if len(broad_markets) == 0:
                            broad_source_raw_empty_cause = "no_broad_markets_fetched"
                        elif len(broad_in_window) > 0:
                            broad_source_raw_empty_cause = "no_raw_family_identifiers"
                        print(
                            f"[LIVE-SELECT-DEBUG] broad_source_raw_empty_cause cause={broad_source_raw_empty_cause}"
                        )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_step step=supplementation_gate before={len(upstream_broad_family_source_normalized)} "
                        f"after={len(upstream_broad_family_source_normalized)} removed=0 "
                        f"reason={'empty_source_guard' if not broad_in_window else 'entered'}"
                    )
                    families_after_targeted_exclusion = sorted(
                        set(upstream_broad_family_source_normalized).difference(existing_families)
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_step step=targeted_family_exclusion before={len(upstream_broad_family_source_normalized)} "
                        f"after={len(families_after_targeted_exclusion)} "
                        f"removed={len(upstream_broad_family_source_normalized) - len(families_after_targeted_exclusion)} "
                        f"reason={'targeted_family_exclusion_removed_all' if upstream_broad_family_source_normalized and not families_after_targeted_exclusion else 'targeted_family_exclusion'}"
                    )
                    families_after_blocked_filter = sorted(
                        family for family in families_after_targeted_exclusion if family not in BLOCKED_MARKET_FAMILIES
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_step step=blocked_family_filter before={len(families_after_targeted_exclusion)} "
                        f"after={len(families_after_blocked_filter)} "
                        f"removed={len(families_after_targeted_exclusion) - len(families_after_blocked_filter)} "
                        f"reason={'blocked_family_filter_removed_all' if families_after_targeted_exclusion and not families_after_blocked_filter else 'blocked_family_filter'}"
                    )
                    broad_available_families = sorted(
                        {
                            self.get_market_family(market)
                            for market in broad_in_window
                            if self.get_market_family(market)
                            and self.get_market_family(market) not in existing_families
                            and self.get_market_family(market) not in BLOCKED_MARKET_FAMILIES
                        }
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_supplementation entered=yes "
                        f"targeted_families={sorted(existing_families)} broad_available_count={len(broad_available_families)}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] targeted_discovery_active={'yes' if targeted_discovery_active else 'no'}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_empty={'yes' if not broad_in_window else 'no'}"
                    )
                    quality_expansion_families = self.select_quality_expansion_families(
                        broad_in_window,
                        exclude_families=existing_families,
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_step step=preferred_prefix_logic before={len(broad_available_families)} "
                        f"after={len(quality_expansion_families)} removed={max(0, len(broad_available_families) - len(quality_expansion_families))} "
                        f"reason={'preferred_prefix_logic_removed_all' if broad_available_families and not quality_expansion_families else 'preferred_prefix_logic_and_quality_gates'}"
                    )
                    discovery_family_cap = 6
                    broad_family_markets = {}
                    broad_family_rejections = []
                    broad_source_records_examined = len(broad_in_window)
                    broad_source_records_eligible = 0
                    broad_source_skip_reason_counts = {}
                    broad_source_eligible_tickers = []
                    broad_source_ticker_family_map = []
                    broad_source_family_record_counts = {}
                    broad_source_family_normalization_changes = []
                    for market in broad_in_window:
                        family = self.get_market_family(market)
                        ticker = str(market.get("ticker", "") or "").strip()
                        raw_family = str(market.get("series_ticker", "") or market.get("event_ticker", "") or "").strip()
                        if not family:
                            broad_source_skip_reason_counts["not_in_scope"] = broad_source_skip_reason_counts.get("not_in_scope", 0) + 1
                            continue
                        if family in existing_families:
                            broad_source_skip_reason_counts["duplicate_family"] = broad_source_skip_reason_counts.get("duplicate_family", 0) + 1
                            if family not in [item[0] for item in broad_family_rejections]:
                                print(
                                    f"[LIVE-SELECT-DEBUG] broad_family_reject_checkpoint family={family} "
                                    f"reason=duplicate_family stage=broad_source_filter"
                                )
                                broad_family_rejections.append((family, "duplicate_family"))
                            continue
                        if family in BLOCKED_MARKET_FAMILIES:
                            broad_source_skip_reason_counts["filtered_out"] = broad_source_skip_reason_counts.get("filtered_out", 0) + 1
                            if family not in [item[0] for item in broad_family_rejections]:
                                print(
                                    f"[LIVE-SELECT-DEBUG] broad_family_reject_checkpoint family={family} "
                                    f"reason=filtered_out stage=broad_source_filter"
                                )
                                broad_family_rejections.append((family, "filtered_out"))
                            continue
                        broad_source_records_eligible += 1
                        broad_source_eligible_tickers.append(ticker)
                        broad_source_ticker_family_map.append((ticker, family))
                        broad_source_family_record_counts[family] = broad_source_family_record_counts.get(family, 0) + 1
                        if raw_family and raw_family != family:
                            broad_source_family_normalization_changes.append((ticker, raw_family, family))
                        broad_family_markets.setdefault(family, []).append(market)
                    broad_source_records_skipped = broad_source_records_examined - broad_source_records_eligible
                    broad_family_raw_pool = sorted(broad_family_markets.keys())
                    broad_source_raw_family_counts = {}
                    for _ticker, _raw_family, _family in broad_source_family_normalization_changes:
                        broad_source_raw_family_counts[_raw_family] = broad_source_raw_family_counts.get(_raw_family, 0) + 1
                    for market in broad_in_window:
                        family = self.get_market_family(market)
                        raw_family = str(market.get("series_ticker", "") or market.get("event_ticker", "") or "").strip()
                        if not family or family in existing_families or family in BLOCKED_MARKET_FAMILIES:
                            continue
                        raw_key = raw_family or family
                        broad_source_raw_family_counts[raw_key] = broad_source_raw_family_counts.get(raw_key, 0) + 1
                    broad_source_normalized_family_counts = dict(broad_source_family_record_counts)
                    print(f"[LIVE-SELECT-DEBUG] broad_source_origin_paths={broad_source_origin_paths}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_prefilter_context={broad_source_prefilter_context}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_raw_family_counts={broad_source_raw_family_counts}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_normalized_family_counts={broad_source_normalized_family_counts}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_limit_context={broad_source_limit_context}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_targeted_discovery_active={'yes' if targeted_discovery_active else 'no'}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_records_examined={broad_source_records_examined}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_records_eligible={broad_source_records_eligible}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_records_skipped={broad_source_records_skipped}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_skip_reason_counts={broad_source_skip_reason_counts}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_eligible_tickers={broad_source_eligible_tickers}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_ticker_family_map={broad_source_ticker_family_map}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_family_record_counts={broad_source_family_record_counts}")
                    print(f"[LIVE-SELECT-DEBUG] broad_source_family_normalization_changes={broad_source_family_normalization_changes}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source={broad_family_raw_pool}")
                    print(f"[LIVE-SELECT-DEBUG] upstream_broad_family_source_count={len(broad_family_raw_pool)}")
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_step step=normalization_and_record_filter before={len(upstream_broad_family_source_normalized)} "
                        f"after={len(broad_family_raw_pool)} removed={len(upstream_broad_family_source_normalized) - len(broad_family_raw_pool)} "
                        f"reason={'record_filter_removed_all' if upstream_broad_family_source_normalized and not broad_family_raw_pool else 'normalization_dedupe_and_record_filter'}"
                    )
                    print(
                        f"[LIVE-SELECT-TIMING] stage=broad_source_builder seconds={round(time.perf_counter() - broad_source_builder_start, 6)}"
                    )
                    broad_family_pool_build_start = time.perf_counter()
                    broad_family_admitted_pre_cap = []
                    soft_admitted_broad_families = []
                    broad_family_admission_debug = {}
                    broad_family_admission_reason_counts = {}
                    if broad_family_markets:
                        broad_family_priority, broad_family_diagnostics = self._rank_selector_families(broad_family_markets)
                        viable_broad_families = self._get_viable_selector_families(
                            broad_family_priority,
                            broad_family_diagnostics,
                        )
                        broad_quote_suppressed_families = []
                        broad_spread_suppressed_families = []
                        broad_deprioritized_families = []
                        broad_retained_families = []
                        for family in broad_family_priority:
                            stats = broad_family_diagnostics.get(family, {})
                            family_market_count = len(broad_family_markets.get(family, []))
                            quote_dead_for_cycle = (
                                family_market_count > 0
                                and stats.get("yes_quote_count", 0) <= 0
                                and stats.get("both_sides_quoted", 0) <= 0
                                and stats.get("missing_quotes", 0) >= family_market_count
                            )
                            spread_dead_for_cycle = (
                                family_market_count > 0
                                and stats.get("both_sides_quoted", 0) > 0
                                and stats.get("narrow_spread", 0) <= 0
                            )
                            poor_quality_for_cycle = (
                                family_market_count > 0
                                and stats.get("both_sides_quoted", 0) > 0
                                and stats.get("non_empty_top_book", 0) <= 0
                                and stats.get("prefilter_survivors", 0) <= 0
                            )
                            if quote_dead_for_cycle:
                                broad_quote_suppressed_families.append(family)
                                print(
                                    f"[LIVE-SELECT-BROAD-FAMILY-QUOTE-SUPPRESS] family={family} candidates_seen={family_market_count} "
                                    f"yes_quote_count={stats.get('yes_quote_count', 0)} no_quote_count={stats.get('no_quote_count', 0)} "
                                    f"both_sides_quote_count={stats.get('both_sides_quoted', 0)} missing_quote_count={stats.get('missing_quotes', 0)} "
                                    f"reason=cycle_quote_dead_family"
                                )
                                continue
                            if spread_dead_for_cycle:
                                broad_spread_suppressed_families.append(family)
                                print(
                                    f"[LIVE-SELECT-BROAD-FAMILY-SPREAD-SUPPRESS] family={family} in_window_count={stats.get('target_window', 0)} "
                                    f"both_sides_quoted_count={stats.get('both_sides_quoted', 0)} narrow_spread_count={stats.get('narrow_spread', 0)} "
                                    f"recent_activity_count={stats.get('recent_activity', 0)} reason=cycle_zero_narrow_spread_family"
                                )
                                continue
                            if poor_quality_for_cycle:
                                broad_deprioritized_families.append(family)
                                print(
                                    f"[LIVE-SELECT-BROAD-FAMILY-DEPRIORITIZE] family={family} reason=quoted_but_empty_top_book_cycle_quality "
                                    f"scan_count={family_market_count} both_sides_quoted_count={stats.get('both_sides_quoted', 0)} "
                                    f"non_empty_top_book_count={stats.get('non_empty_top_book', 0)} "
                                    f"prefilter_survivors={stats.get('prefilter_survivors', 0)} final_candidates=0"
                                )
                            else:
                                broad_retained_families.append(family)
                        broad_family_priority = broad_retained_families + broad_deprioritized_families
                        print(
                            f"[LIVE-SELECT-BROAD-FAMILY-QUOTE-SUPPRESS-SUMMARY] suppressed_families={broad_quote_suppressed_families} "
                            f"deprioritized_families={broad_deprioritized_families}"
                        )
                        print(
                            f"[LIVE-SELECT-BROAD-FAMILY-SPREAD-SUPPRESS-SUMMARY] suppressed_families={broad_spread_suppressed_families} "
                            f"deprioritized_families={broad_deprioritized_families}"
                        )
                        if broad_deprioritized_families:
                            print(
                                f"[LIVE-SELECT-BROAD-FAMILY-DEPRIORITIZE] adapted_order={broad_family_priority} "
                                f"deprioritized_families={broad_deprioritized_families}"
                            )
                        for family in broad_family_priority:
                            stats = broad_family_diagnostics.get(family, {})
                            family_market_count = len(broad_family_markets.get(family, []))
                            preferred_prefix = next(
                                (prefix for prefix in QUALITY_EXPANSION_PREFERRED_FAMILY_PREFIXES if family.startswith(prefix)),
                                None,
                            )
                            diagnostic_blockers = []
                            reject_reason = "insufficient_quality"
                            if stats.get("target_window", 0) <= 0:
                                reject_reason = "outside_target_window"
                                diagnostic_blockers.append("outside_target_window")
                            elif stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE:
                                reject_reason = "missing_quotes"
                                diagnostic_blockers.append("missing_quotes")
                            elif stats.get("wide_spread_rate", 0.0) >= FAMILY_MAX_WIDE_SPREAD_RATE:
                                reject_reason = "wide_spread"
                                diagnostic_blockers.append("wide_spread")
                            elif stats.get("low_liquidity_rate", 0.0) >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
                                reject_reason = "low_liquidity"
                                diagnostic_blockers.append("low_liquidity")
                            elif stats.get("too_early_share", 0.0) >= FAMILY_MAX_TOO_EARLY_RATE:
                                reject_reason = "filtered_out"
                                diagnostic_blockers.append("too_early_rate")
                            elif stats.get("survivor_rate", 0.0) < FAMILY_MIN_SURVIVOR_RATE:
                                reject_reason = "no_survivors"
                                diagnostic_blockers.append("no_survivors")
                            elif (
                                stats.get("family_quality_score", 0.0) + stats.get("spread_quality_score", 0.0)
                            ) <= 0.0:
                                reject_reason = "quality_score_not_positive"
                                diagnostic_blockers.append("quality_score_not_positive")
                            if preferred_prefix is None:
                                diagnostic_blockers.append("preferred_prefix_mismatch")
                            soft_admit_reason = None
                            if family in viable_broad_families:
                                broad_family_admitted_pre_cap.append(family)
                                broad_family_admission_debug[family] = {
                                    "admitted": "yes",
                                    "reason": "viable_family",
                                    "blockers": diagnostic_blockers,
                                    "preferred_prefix": preferred_prefix,
                                }
                                continue
                            if family_market_count > 0 and stats.get("target_window", 0) <= 0:
                                soft_admit_reason = "has_in_window_market"
                            elif (
                                stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE
                                and (
                                    stats.get("both_sides_quoted", 0) > 0
                                    or stats.get("narrow_spread", 0) > 0
                                )
                            ):
                                soft_admit_reason = "missing_quotes_but_has_quotes_or_tight_spread"
                            elif fallback_to_broad_scan and reject_reason in (
                                "no_survivors",
                                "missing_quotes",
                                "outside_target_window",
                                "quality_score_not_positive",
                            ):
                                soft_admit_reason = reject_reason
                            if soft_admit_reason:
                                soft_admitted_broad_families.append(family)
                                broad_family_admitted_pre_cap.append(family)
                                broad_family_admission_debug[family] = {
                                    "admitted": "yes",
                                    "reason": f"soft_admitted:{soft_admit_reason}",
                                    "blockers": diagnostic_blockers,
                                    "preferred_prefix": preferred_prefix,
                                }
                                if fallback_to_broad_scan and soft_admit_reason in (
                                    "no_survivors",
                                    "missing_quotes",
                                    "outside_target_window",
                                    "quality_score_not_positive",
                                ):
                                    print(
                                        f"[LIVE-SELECT-DEBUG] broad_family_soft_admitted family={family} "
                                        f"reason={soft_admit_reason}"
                                    )
                                continue
                            broad_family_admission_debug[family] = {
                                "admitted": "no",
                                "reason": reject_reason,
                                "blockers": diagnostic_blockers if diagnostic_blockers else ["other_admission_gate"],
                                "preferred_prefix": preferred_prefix,
                            }
                            if family not in [item[0] for item in broad_family_rejections]:
                                print(
                                    f"[LIVE-SELECT-DEBUG] broad_family_reject_checkpoint family={family} "
                                    f"reason={reject_reason} stage=broad_family_admission"
                                )
                                broad_family_rejections.append((family, reject_reason))
                        broad_candidate_families = [
                            family for family in broad_family_priority
                            if family in viable_broad_families or family in soft_admitted_broad_families
                        ]
                        cap_reached_for_broad_pool = False
                        for family in broad_candidate_families:
                            admission_debug = broad_family_admission_debug.setdefault(
                                family,
                                {"admitted": "yes", "reason": "candidate_family", "blockers": [], "preferred_prefix": None},
                            )
                            if family in quality_expansion_families:
                                admission_debug["admitted"] = "no"
                                admission_debug["reason"] = "duplicate_family"
                                admission_debug.setdefault("blockers", []).append("duplicate_family")
                                if family not in [item[0] for item in broad_family_rejections]:
                                    print(
                                        f"[LIVE-SELECT-DEBUG] broad_family_reject_checkpoint family={family} "
                                        f"reason=duplicate_family stage=broad_quality_expansion"
                                    )
                                    broad_family_rejections.append((family, "duplicate_family"))
                                continue
                            if cap_reached_for_broad_pool:
                                admission_debug["admitted"] = "no"
                                admission_debug["reason"] = "discovery_family_cap_reached"
                                admission_debug.setdefault("blockers", []).append("cap_pruning_rule")
                                if family not in [item[0] for item in broad_family_rejections]:
                                    broad_family_rejections.append((family, "discovery_family_cap_reached"))
                                continue
                            quality_expansion_families.append(family)
                            if len(existing_families.union(set(quality_expansion_families))) >= discovery_family_cap:
                                cap_reached_for_broad_pool = True
                        broad_family_admission_summary_rows = []
                        for family in broad_family_priority:
                            stats = broad_family_diagnostics.get(family, {})
                            admission_debug = broad_family_admission_debug.get(
                                family,
                                {"admitted": "no", "reason": "other_admission_gate", "blockers": ["other_admission_gate"], "preferred_prefix": None},
                            )
                            reason = admission_debug.get("reason") or "other_admission_gate"
                            broad_family_admission_reason_counts[reason] = broad_family_admission_reason_counts.get(reason, 0) + 1
                            blocker_tokens = list(admission_debug.get("blockers", []))
                            if fallback_to_broad_scan and not blocker_tokens:
                                blocker_tokens = ["none"]
                            broad_family_admission_summary_rows.append((family, admission_debug.get("admitted") == "yes", reason))
                            print(
                                f"[LIVE-SELECT-DEBUG] broad_family_admission_decision family={family} "
                                f"admitted={admission_debug.get('admitted')} reason={reason} "
                                f"scan_count={len(broad_family_markets.get(family, []))} "
                                f"family_quality_score={stats.get('family_quality_score', 0.0)} "
                                f"spread_quality_score={stats.get('spread_quality_score', 0.0)} "
                                f"narrow_spread={stats.get('narrow_spread', 0)} wide_spread={stats.get('wide_spread', 0)} "
                                f"both_sides_quoted={stats.get('both_sides_quoted', 0)} recent_activity={stats.get('recent_activity', 0)} "
                                f"missing_quotes_count={stats.get('missing_quotes', 0)} low_liquidity_count={stats.get('low_liquidity', 0)} "
                                f"fallback_blockers={blocker_tokens} preferred_prefix={admission_debug.get('preferred_prefix')}"
                            )
                        print(
                            f"[LIVE-SELECT-DEBUG] broad_family_admission_summary admitted={sum(1 for _family, admitted, _reason in broad_family_admission_summary_rows if admitted)} "
                            f"rejected={sum(1 for _family, admitted, _reason in broad_family_admission_summary_rows if not admitted)} "
                            f"reasons={sorted(broad_family_admission_reason_counts.items(), key=lambda item: item[1], reverse=True)}"
                        )
                    broad_family_pool = sorted(set(quality_expansion_families))
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_source_final_summary raw_count={len(upstream_broad_family_source_raw)} "
                        f"normalized_count={len(upstream_broad_family_source_normalized)} final_count={len(sorted(broad_family_markets.keys()))} "
                        f"fallback={'yes' if fallback_to_broad_scan else 'no'} targeted_discovery_active={'yes' if targeted_discovery_active else 'no'}"
                    )
                    print(
                        f"[LIVE-SELECT-DEBUG] broad_family_pool_build_inputs considered={sorted(broad_family_markets.keys())} "
                        f"admitted_pre_cap={broad_family_admitted_pre_cap} rejected={broad_family_rejections}"
                    )
                    print(f"[LIVE-SELECT-DEBUG] broad_family_raw_pool={broad_family_raw_pool}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_raw_pool_count={len(broad_family_raw_pool)}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_admitted_pre_cap={broad_family_admitted_pre_cap}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_admitted_pre_cap_count={len(broad_family_admitted_pre_cap)}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_rejected={broad_family_rejections}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_rejected_count={len(broad_family_rejections)}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_considered={sorted(broad_family_markets.keys())}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_pool={broad_family_pool}")
                    print(f"[LIVE-SELECT-DEBUG] broad_family_pool_count={len(broad_family_pool)}")
                    print(
                        f"[LIVE-SELECT-TIMING] stage=broad_family_pool_build seconds={round(time.perf_counter() - broad_family_pool_build_start, 6)}"
                    )
                    current_cycle_dead_targeted_families = sorted(
                        family
                        for family in prioritized_in_window
                        if family in OPTIONAL_TARGETED_SERIES_TICKERS
                        and targeted_family_activity.get(family, {}).get("in_window", 0) > 0
                        and targeted_family_activity.get(family, {}).get("non_empty_top_book", 0) <= 0
                        and targeted_family_activity.get(family, {}).get("prefilter_survivors", 0) <= 0
                    )
                    promote_broad_over_dead_targeted = bool(
                        fallback_to_broad_scan
                        and quality_expansion_families
                        and targeted_selected_usable_top_book <= 0
                        and current_cycle_dead_targeted_families
                    )
                    cycle_targeted_dead_book_suppressed_families = list(current_cycle_dead_targeted_families) if promote_broad_over_dead_targeted else []
                    if cycle_targeted_dead_book_suppressed_families:
                        for family in cycle_targeted_dead_book_suppressed_families:
                            stats = targeted_family_activity.get(family, {})
                            print(
                                f"[LIVE-SELECT-FAMILY-SUPPRESS] family={family} cycle_id={self._get_phase55_cycle_id()} "
                                f"suppress_reason=cycle_targeted_dead_book_fallback_promotion action=applied "
                                f"in_window={stats.get('in_window', 0)} non_empty_top_book={stats.get('non_empty_top_book', 0)} "
                                f"prefilter_survivors={stats.get('prefilter_survivors', 0)}"
                            )
                    expanded_markets = [
                        market for market in targeted_in_window
                        if self.get_market_family(market) not in set(cycle_targeted_dead_book_suppressed_families)
                    ]
                    added_family_counts = {}
                    seen_expanded_tickers = {
                        str(market.get("ticker", "") or "").strip()
                        for market in expanded_markets
                        if str(market.get("ticker", "") or "").strip()
                    }
                    allowed_discovery_families = set(quality_expansion_families)
                    for market in broad_in_window:
                        family = self.get_market_family(market)
                        ticker = str(market.get("ticker", "") or "").strip()
                        if family not in allowed_discovery_families or not ticker or ticker in seen_expanded_tickers:
                            continue
                        expanded_markets.append(market)
                        seen_expanded_tickers.add(ticker)
                        added_family_counts[family] = added_family_counts.get(family, 0) + 1
                    print(
                        f"[LIVE-SELECT-QUALITY-EXPANSION] added_family_scan_counts={sorted(added_family_counts.items())}"
                    )
                    if cycle_targeted_dead_book_suppressed_families:
                        effective_targeted_order = [
                            family for family in prioritized_in_window
                            if family not in set(cycle_targeted_dead_book_suppressed_families)
                        ]
                        print(
                            f"[LIVE-SELECT-TARGETED-ROUTING] cycle_id={self._get_phase55_cycle_id()} "
                            f"suppressed_dead_book_families={cycle_targeted_dead_book_suppressed_families} "
                            f"suppression_reason=cycle_targeted_dead_book_fallback_promotion "
                            f"adapted_order={effective_targeted_order} broad_promoted=yes "
                            f"promoted_broad_families={sorted(added_family_counts.items())}"
                        )
                    broad_origin_families = set(added_family_counts.keys())
                    markets = expanded_markets
                    selection_merge_source = (
                        "targeted_plus_broad_dead_targeted_suppressed"
                        if cycle_targeted_dead_book_suppressed_families
                        else "targeted_plus_broad"
                    )
                    targeted_selection_count = len(expanded_markets) - sum(added_family_counts.values())
                    broad_selection_count = sum(added_family_counts.values())
                    merged_selection_count = len(expanded_markets)
                else:
                    print(
                        f"[LIVE-SELECT-FALLBACK-ENTER] branch=targeted_empty_broad_only hour_utc={current_hour_utc} "
                        f"reason=no_targeted_in_window targeted_markets_in_window={targeted_markets_in_window}"
                    )
                    broad_fetch_start = time.perf_counter()
                    markets = self.fetch_broad_discovery_markets()
                    print(
                        f"[LIVE-SELECT-TIMING] stage=broad_fetch seconds={round(time.perf_counter() - broad_fetch_start, 6)}"
                    )
                    broad_origin_families = set()
                    selection_merge_source = "broad_only_targeted_empty"
                    targeted_selection_count = len(targeted_in_window)
                    broad_selection_count = len(markets)
                    merged_selection_count = len(markets)
            else:
                print("[LIVE-SELECT-DEBUG] targeted_mode_enabled=no")
                print("[LIVE-SELECT-DEBUG] fallback_to_broad_scan=yes")
                print(
                    f"[LIVE-SELECT-FALLBACK-ENTER] branch=targeted_disabled_broad_only hour_utc={current_hour_utc} "
                    f"reason=targeted_mode_disabled targeted_markets_in_window=0"
                )
                broad_fetch_start = time.perf_counter()
                markets = self.fetch_broad_discovery_markets()
                print(
                    f"[LIVE-SELECT-TIMING] stage=broad_fetch seconds={round(time.perf_counter() - broad_fetch_start, 6)}"
                )
                broad_origin_families = set()
                selection_merge_source = "broad_only_targeted_disabled"
                broad_selection_count = len(markets)
                merged_selection_count = len(markets)

        original_market_count = len(markets)
        print(f"[LIVE-SELECT-DEBUG] total_markets_after_initial_fetch={original_market_count}")
        time_window_markets = []
        base_window_market_count = 0
        extended_window_market_count = 0
        extended_window_included = []
        skipped_too_early_markets = 0
        skipped_too_close_markets = 0
        skipped_no_timestamp_markets = 0
        effective_max_minutes_to_event = self.get_effective_max_minutes_to_event()
        broad_discovery_max_minutes_to_event = self.get_broad_discovery_max_minutes_to_event()
        print(
            f"[LIVE-SELECT-DEBUG] broad_discovery_effective_max_minutes={broad_discovery_max_minutes_to_event}"
        )
        timing_sample_logged = False
        for market in markets:
            timestamp_details = self.get_market_event_timestamp_details(market)
            minutes_to_event = None
            if timestamp_details.get("timestamp") is not None:
                minutes_to_event = (timestamp_details["timestamp"] - time.time()) / 60.0
            inclusion_reason = self.get_time_window_inclusion_reason(
                minutes_to_event,
                max_minutes_to_event=broad_discovery_max_minutes_to_event,
            )
            if not timing_sample_logged:
                timing_sample_reason = inclusion_reason
                if minutes_to_event is None:
                    timing_sample_reason = "no_timestamp"
                elif minutes_to_event < MIN_MINUTES_TO_EVENT:
                    timing_sample_reason = "too_close"
                elif inclusion_reason is None:
                    timing_sample_reason = "too_early"
                print(
                    f"[LIVE-SELECT-DEBUG] timing_sample ticker={str(market.get('ticker', '') or '').strip()} "
                    f"source={timestamp_details.get('source_key')} event_ts={timestamp_details.get('event_ts')} "
                    f"minutes_to_event={round(minutes_to_event, 2) if minutes_to_event is not None else None} "
                    f"window_max={broad_discovery_max_minutes_to_event} "
                    f"admitted={'yes' if inclusion_reason else 'no'} reason={timing_sample_reason}"
                )
                timing_sample_logged = True
            if inclusion_reason == "base_window":
                base_window_market_count += 1
                time_window_markets.append(market)
                continue
            if inclusion_reason == "extended_window":
                extended_window_market_count += 1
                time_window_markets.append(market)
                if len(extended_window_included) < 5:
                    extended_window_included.append((
                        str(market.get("ticker", "") or "").strip(),
                        round(minutes_to_event, 2),
                        inclusion_reason,
                    ))
                continue
            if minutes_to_event is None:
                skipped_no_timestamp_markets += 1
                continue
            if minutes_to_event < MIN_MINUTES_TO_EVENT:
                skipped_too_close_markets += 1
                continue
            skipped_too_early_markets += 1
        print(
            f"[LIVE-SELECT-DEBUG] active_time_window "
            f"MIN_MINUTES_TO_EVENT={MIN_MINUTES_TO_EVENT} "
            f"old_max_minutes={MAX_MINUTES_TO_EVENT} "
            f"new_max_minutes={effective_max_minutes_to_event} "
            f"broad_discovery_max_minutes={broad_discovery_max_minutes_to_event}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] time_window_adjustment_passed "
            f"base_window={base_window_market_count} "
            f"extended_window={extended_window_market_count} "
            f"reason=broad_discovery_extension_within_{BROAD_DISCOVERY_TIME_WINDOW_EXTENSION_MINUTES}m_buffer"
        )
        if extended_window_included:
            print(f"[LIVE-SELECT-DEBUG] time_window_adjustment_samples={extended_window_included}")
        print(
            f"[LIVE-SELECT-DEBUG] timing_filter original={original_market_count} "
            f"within_window={len(time_window_markets)} skipped_too_early_markets={skipped_too_early_markets} "
            f"skipped_too_close_markets={skipped_too_close_markets} "
            f"skipped_no_timestamp_markets={skipped_no_timestamp_markets}"
        )
        print(
            f"[LIVE-SELECT-COUNT] stage=selection_merge source={selection_merge_source} "
            f"after={merged_selection_count or original_market_count} targeted_selected={targeted_selection_count} "
            f"broad_selected={broad_selection_count}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] timing_skip_reason_counts={{'too_early': {skipped_too_early_markets}, "
            f"'too_close': {skipped_too_close_markets}, 'no_timestamp': {skipped_no_timestamp_markets}}}"
        )
        print(f"[LIVE-SELECT-DEBUG] remaining_after_time_filter={len(time_window_markets)}")
        print(f"[LIVE-SELECT-DEBUG] total_markets_after_time_filter={len(time_window_markets)}")
        print(
            f"[LIVE-SELECT-COUNT] stage=time_window before={original_market_count} "
            f"after={len(time_window_markets)} filtered={original_market_count - len(time_window_markets)}"
        )
        in_window_family_counts = {}
        for market in time_window_markets:
            family = self.get_market_family(market)
            in_window_family_counts[family] = in_window_family_counts.get(family, 0) + 1
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            runtime_stats["in_window"] = runtime_stats.get("in_window", 0) + 1
        print(
            f"[LIVE-SELECT-DEBUG] families_in_window={sorted(in_window_family_counts.keys())}"
        )
        print(f"[LIVE-SELECT-DEBUG] multi_family_candidates={sorted(in_window_family_counts.keys())}")
        for family, count in sorted(in_window_family_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] in_window_family family={family} count={count}")
        if not time_window_markets:
            print(
                f"[LIVE-SELECT-FALLBACK-EARLY-EXIT] stage=time_window reason=no_markets_in_time_window "
                f"selection_source={selection_merge_source} original_market_count={original_market_count} "
                f"fallback_to_broad={'yes' if fallback_to_broad_scan else 'no'} broad_selected={broad_selection_count}"
            )
            self.no_candidate_cycle_streak += 1
            self.last_live_select_summary = {
                "original_market_count": original_market_count,
                "within_window": 0,
                "families_in_window": [],
                "skipped_too_early_markets": skipped_too_early_markets,
                "skipped_too_close_markets": skipped_too_close_markets,
                "skipped_no_timestamp_markets": skipped_no_timestamp_markets,
                "candidates_found": 0,
            }
            print("[LIVE-SELECT] no_markets_in_time_window")
            print("[LIVE-SELECT-DEBUG] early_return_reason=no_markets_in_time_window")
            emit_phase5_observation_logs(0, "no_markets_in_time_window")
            emit_full_select_cycle_timing()
            return None, None
        markets = time_window_markets

        prefilter_rejects = {}
        strict_rejects = {}
        sample_skips = []
        liquidity_visible_market_count = 0
        empty_book_market_count = 0
        low_liquidity_prefilter_stats = {}
        liquidity_check_family_stats = {}
        family_observation_stats = {}
        fetched_family_counts = {}
        family_markets = {}
        fetched_timing_buckets = {}
        prefilter_survivor_family_counts = {}
        topbook_family_stats = {}
        hydration_source_summary = {}
        self._live_select_hydration_source_summary = hydration_source_summary
        candidate_topbook_summary = {}
        prefilter_input_summary = {}
        family_scanned_counts = {}
        family_skip_counts = {}
        family_skip_reason_counts = {}
        prefilter_reject_reason_counts = {}
        prefilter_low_liquidity_top_book_states = {}
        broad_prefilter_grace_used_count = 0
        broad_prefilter_grace_blocked_count = 0
        broad_prefilter_grace_reason_counts = {}
        broad_prefilter_grace_blocked_spread_values = []
        broad_prefilter_grace_blocked_one_sided_top_book_count = 0
        broad_prefilter_grace_blocked_both_sides_quoted_count = 0
        broad_prefilter_grace_blocked_recent_activity_count = 0
        targeted_liquidity_failures_count = 0
        broad_liquidity_failures_count = 0
        liquidity_failure_zero_top_book_count = 0
        liquidity_failure_one_sided_top_book_count = 0
        liquidity_failure_both_sides_top_book_count = 0
        adaptive_probe_mode = fallback_to_broad_scan or targeted_path_weak
        family_adaptive_probe_pool = []
        family_adaptive_probe_meta = {}
        family_probe_entered_count = 0
        family_probe_passed_count = 0
        family_probe_failed_count = 0
        family_probe_blocker_counts = {}
        family_probe_families = set()
        for market in markets:
            family = self.get_market_family(market)
            self._emit_priority_upstream_trace(
                "[LIVE-SELECT-CANDIDATE-INPUT]",
                market,
                "pre_family_markets_append",
                source_stage="time_window_markets",
                handoff_target="family_markets",
                selection_source=selection_merge_source,
                object_identity_changed='no',
            )
            fetched_family_counts[family] = fetched_family_counts.get(family, 0) + 1
            family_markets.setdefault(family, []).append(market)
            bucket = self.get_event_time_bucket(
                self.get_minutes_to_event(market),
                max_minutes_to_event=broad_discovery_max_minutes_to_event,
            )
            fetched_timing_buckets[bucket] = fetched_timing_buckets.get(bucket, 0) + 1

        if not broad_origin_families and selection_merge_source in ("broad_only_targeted_empty", "broad_only_targeted_disabled"):
            broad_origin_families = set(family_markets.keys())
        for family in sorted(broad_origin_families):
            family_summary = {
                "in_window_count": len(family_markets.get(family, [])),
                "both_sides_quoted_count": 0,
                "one_sided_top_book_count": 0,
                "narrow_spread_count": 0,
                "recent_activity_count": 0,
            }
            for market in family_markets.get(family, []):
                liquidity_priority = self.get_market_liquidity_priority(market)
                market_quality = self.get_market_quality_preference(market, liquidity_priority=liquidity_priority)
                if market_quality.get("both_sides_quoted"):
                    family_summary["both_sides_quoted_count"] += 1
                if liquidity_priority.get("top_book_state") == "one_non_zero":
                    family_summary["one_sided_top_book_count"] += 1
                if market_quality.get("narrow_spread"):
                    family_summary["narrow_spread_count"] += 1
                if market_quality.get("recent_activity"):
                    family_summary["recent_activity_count"] += 1
            broad_in_window_family_observation[family] = family_summary

        family_priority, family_diagnostics = self._rank_selector_families(family_markets)
        preferred_families, fallback_families = self._resolve_selector_family_order(fetched_family_counts)
        broad_candidates_before_family_rejection = sum(
            len(family_markets.get(family, [])) for family in broad_origin_families
        )
        print(f"[LIVE-SELECT-DEBUG] broad_candidates_before_family_rejection={broad_candidates_before_family_rejection}")
        print(
            f"[LIVE-SELECT-COUNT] stage=family_ranking markets={len(markets)} families_before={len(family_markets)} "
            f"families_after={len(family_priority)}"
        )
        market_condition_state, market_condition_reason = self.classify_market_condition()
        self.market_condition_state = market_condition_state
        self.market_condition_reason = market_condition_reason
        print(f"[MARKET-CONDITION] state={market_condition_state} reason={market_condition_reason}")
        effective_max_markets_per_family = MAX_MARKETS_PER_FAMILY
        if market_condition_state == "POOR":
            effective_max_markets_per_family = max(4, MAX_MARKETS_PER_FAMILY // 2)
        elif market_condition_state == "NEUTRAL":
            effective_max_markets_per_family = max(6, int(MAX_MARKETS_PER_FAMILY * 0.75))
        recovery_deprioritized = [
            family for family, stats in family_diagnostics.items()
            if stats.get("recovery_adjustment", 0.0) < 0
        ]
        recovery_promoted = [
            family for family, stats in family_diagnostics.items()
            if stats.get("recovery_adjustment", 0.0) > 0
        ]
        if preferred_families:
            preferred_order = [family for family in family_priority if family in preferred_families]
        else:
            preferred_order = [
                family for family in family_priority
                if family not in DEPRIORITIZED_MARKET_FAMILIES
            ]
        neutral_families = [
            family for family in family_priority
            if family not in preferred_order
            and family not in fallback_families
            and family not in DEPRIORITIZED_MARKET_FAMILIES
        ]
        deprioritized_order = [
            family for family in family_priority
            if family in DEPRIORITIZED_MARKET_FAMILIES or family in fallback_families
        ]
        fallback_order = neutral_families + deprioritized_order
        active_broad_deprioritized_families = []
        active_broad_retained_families = []
        for family in fallback_order:
            if family not in broad_origin_families:
                active_broad_retained_families.append(family)
                continue
            stats = family_diagnostics.get(family, {})
            poor_quality_for_cycle = (
                len(family_markets.get(family, [])) > 0
                and stats.get("both_sides_quoted", 0) > 0
                and stats.get("non_empty_top_book", 0) <= 0
                and stats.get("prefilter_survivors", 0) <= 0
            )
            if poor_quality_for_cycle:
                active_broad_deprioritized_families.append(family)
                print(
                    f"[LIVE-SELECT-BROAD-FAMILY-DEPRIORITIZE] family={family} reason=active_broad_fallback_quoted_but_empty_top_book "
                    f"scan_count={len(family_markets.get(family, []))} both_sides_quoted_count={stats.get('both_sides_quoted', 0)} "
                    f"non_empty_top_book_count={stats.get('non_empty_top_book', 0)} "
                    f"prefilter_survivors={stats.get('prefilter_survivors', 0)} final_candidates={self.live_family_runtime_stats.get(family, {}).get('final_candidates', 0)}"
                )
            else:
                active_broad_retained_families.append(family)
        fallback_order = active_broad_retained_families + active_broad_deprioritized_families
        if active_broad_deprioritized_families:
            print(
                f"[LIVE-SELECT-BROAD-FAMILY-DEPRIORITIZE] adapted_order={fallback_order} "
                f"deprioritized_families={active_broad_deprioritized_families} branch=active_family_order"
            )
        for family in family_priority:
            stats = family_diagnostics.get(family, {})
            print(
                f"[LIVE-SELECT-DEBUG] family_timing_mix family={family} "
                f"target_window={stats.get('target_window', 0)} too_early={stats.get('too_early', 0)} "
                f"target_window_share={stats.get('target_window_share', 0.0)} "
                f"missing_quotes_rate={stats.get('missing_quotes_rate', 0.0)} "
                f"low_liquidity_rate={stats.get('low_liquidity_rate', 0.0)} "
                f"wide_spread_rate={stats.get('wide_spread_rate', 0.0)} "
                f"survivor_rate={stats.get('survivor_rate', 0.0)} "
                f"score={stats.get('score', 0.0)}"
            )
            print(
                f"[LIVE-SELECT-QUALITY] family={family} family_quality_score={stats.get('family_quality_score', 0.0)} "
                f"spread_quality_score={stats.get('spread_quality_score', 0.0)} "
                f"narrow_spread={stats.get('narrow_spread', 0)} wide_spread={stats.get('wide_spread', 0)} "
                f"both_sides_quoted={stats.get('both_sides_quoted', 0)} recent_activity={stats.get('recent_activity', 0)} "
                f"quality_reasons={stats.get('quality_reasons', [])}"
            )
        print(f"[LIVE-SELECT-DEBUG] no_candidate_cycle_count={self.no_candidate_cycle_streak}")
        print(f"[LIVE-SELECT-DEBUG] recovery_deprioritized_families={sorted(recovery_deprioritized)}")
        print(f"[LIVE-SELECT-DEBUG] recovery_promoted_families={sorted(recovery_promoted)}")
        print(f"[LIVE-SELECT-DEBUG] family_order preferred={preferred_order} fallback={fallback_order}")
        family_rebalance_cycle_id = self._get_phase55_cycle_id()
        family_rebalance_caps = {}
        rebalanced_families = []
        unchanged_rebalance_families = []
        for family in family_priority:
            stats = family_diagnostics.get(family, {})
            original_cap = effective_max_markets_per_family
            adjusted_cap = original_cap
            if stats.get("survivor_rate", 0.0) <= 0.0:
                failure_points = 0
                missing_quotes = int(stats.get("missing_quotes", 0) or 0)
                wide_spread = int(stats.get("wide_spread", 0) or 0)
                low_liquidity = int(stats.get("low_liquidity", 0) or 0)
                missing_quotes_rate = float(stats.get("missing_quotes_rate", 0.0) or 0.0)
                wide_spread_rate = float(stats.get("wide_spread_rate", 0.0) or 0.0)
                low_liquidity_rate = float(stats.get("low_liquidity_rate", 0.0) or 0.0)
                if missing_quotes >= 20 or missing_quotes_rate >= 0.7:
                    failure_points += 2
                elif missing_quotes >= 10 or missing_quotes_rate >= 0.5:
                    failure_points += 1
                if wide_spread >= 15 or wide_spread_rate >= 0.5:
                    failure_points += 2
                elif wide_spread >= 8 or wide_spread_rate >= 0.3:
                    failure_points += 1
                if low_liquidity >= 15 or low_liquidity_rate >= 0.5:
                    failure_points += 2
                elif low_liquidity >= 8 or low_liquidity_rate >= 0.3:
                    failure_points += 1
                if failure_points >= 5:
                    adjusted_cap = max(4, original_cap // 4)
                elif failure_points >= 3:
                    adjusted_cap = max(6, original_cap // 2)
                elif failure_points >= 1:
                    adjusted_cap = max(8, int(original_cap * 0.75))
            family_rebalance_caps[family] = adjusted_cap
            if adjusted_cap != original_cap:
                rebalanced_families.append((family, original_cap, adjusted_cap))
                print(
                    f"[LIVE-SELECT-FAMILY-REBALANCE] family={family} cycle_id={family_rebalance_cycle_id} "
                    f"original_cap={original_cap} adjusted_cap={adjusted_cap} "
                    f"missing_quotes_in_cycle={stats.get('missing_quotes', 0)} "
                    f"wide_spread_in_cycle={stats.get('wide_spread', 0)} "
                    f"low_liquidity_in_cycle={stats.get('low_liquidity', 0)} "
                    f"reason=cycle_failure_rebalance"
                )
            else:
                unchanged_rebalance_families.append(family)
        top_penalized_families = sorted(
            rebalanced_families,
            key=lambda item: (item[1] - item[2], item[0]),
            reverse=True,
        )
        print(
            f"[LIVE-SELECT-FAMILY-REBALANCE-SUMMARY] adjusted_families={[family for family, _orig, _adj in rebalanced_families]} "
            f"unchanged_families={unchanged_rebalance_families} "
            f"top_penalized_families={top_penalized_families[:10]}"
        )
        viable_families = self._get_viable_selector_families(family_priority, family_diagnostics)
        print(f"[LIVE-SELECT-DEBUG] viable_families_considered={viable_families}")
        print(
            f"[LIVE-SELECT-COUNT] stage=family_ranking_post_filter families_ranked={len(family_priority)} "
            f"viable_families={len(viable_families)}"
        )

        prefilter_pool = []
        strict_entry_family_counts = {}
        quote_handoff_summary = {
            "total_candidates_arriving": 0,
            "candidates_with_orderbook_payload": 0,
            "candidates_with_yes_quote": 0,
            "candidates_with_no_quote": 0,
            "candidates_with_both_sides": 0,
            "candidates_missing_quotes": 0,
        }
        quote_handoff_family = {}
        orderbook_hydration_summary = {
            "candidates_requested_for_hydration": 0,
            "candidates_successfully_hydrated": 0,
            "candidates_without_payload_after_hydration": 0,
            "hydration_path": "market_detail_pre_prefilter_for_broad_missing_quotes",
        }
        preferred_pass_survivors = 0
        preferred_lead_family = preferred_order[0] if preferred_order else None
        expand_to_additional_families = False
        preferred_phase_family_cap = MAX_PREFERRED_FAMILIES_PER_PASS
        expansion_reason = self._get_family_expansion_reason(
            preferred_lead_family,
            family_diagnostics.get(preferred_lead_family, {}),
        )
        if expansion_reason:
            preferred_phase_family_cap = min(len(preferred_order), MAX_FAMILIES_PER_SCAN)
            print(
                f"[LIVE-SELECT-DEBUG] family_expansion_triggered=yes "
                f"reason={expansion_reason} lead_family={preferred_lead_family} "
                f"expanded_family_cap={preferred_phase_family_cap}"
            )
        else:
            print(
                f"[LIVE-SELECT-DEBUG] family_expansion_triggered=no "
                f"lead_family={preferred_lead_family}"
            )

        def evaluate_family_skip(family):
            stats = family_diagnostics.get(family, {})
            family_pool = family_markets.get(family, [])
            family_has_targeted = any(self.is_targeted_series_market(market) for market in family_pool)
            skip_reasons = []
            if stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE:
                skip_reasons.append("missing_quotes_rate")
            if stats.get("wide_spread_rate", 0.0) >= FAMILY_MAX_WIDE_SPREAD_RATE:
                skip_reasons.append("wide_spread_rate")
            if stats.get("low_liquidity_rate", 0.0) >= FAMILY_MAX_LOW_LIQUIDITY_RATE:
                skip_reasons.append("low_liquidity_rate")
            if stats.get("too_early_share", 0.0) >= FAMILY_MAX_TOO_EARLY_RATE:
                skip_reasons.append("too_early_rate")
            if stats.get("survivor_rate", 0.0) < FAMILY_MIN_SURVIVOR_RATE:
                skip_reasons.append("survivor_rate")
            if family_has_targeted and skip_reasons:
                print(
                    f"[LIVE-SELECT-DEBUG] targeted_family_skip_bypass family={family} "
                    f"generic_family_skip_bypassed=yes reasons={skip_reasons}"
                )
                return False, skip_reasons
            if fallback_to_broad_scan and family in broad_origin_families and skip_reasons:
                fallback_soft_skip_reasons = []
                if stats.get("target_window", 0) <= 0:
                    fallback_soft_skip_reasons.append("outside_target_window")
                if stats.get("missing_quotes_rate", 0.0) >= FAMILY_MAX_MISSING_QUOTES_RATE:
                    fallback_soft_skip_reasons.append("missing_quotes")
                if stats.get("survivor_rate", 0.0) < FAMILY_MIN_SURVIVOR_RATE:
                    fallback_soft_skip_reasons.append("no_survivors")
                if (
                    stats.get("family_quality_score", 0.0) + stats.get("spread_quality_score", 0.0)
                ) <= 0.0:
                    fallback_soft_skip_reasons.append("quality_score_not_positive")
                non_soft_skip_reasons = [
                    reason for reason in skip_reasons
                    if reason not in ("missing_quotes_rate", "survivor_rate")
                ]
                if fallback_soft_skip_reasons and not non_soft_skip_reasons:
                    for soft_reason in fallback_soft_skip_reasons:
                        print(
                            f"[LIVE-SELECT-DEBUG] broad_family_soft_admitted family={family} "
                            f"reason={soft_reason}"
                        )
                    return False, skip_reasons
            return bool(skip_reasons), skip_reasons

        for phase_name, family_order in (("preferred", preferred_order), ("fallback", fallback_order)):
            if phase_name == "fallback" and preferred_pass_survivors > 0 and not expand_to_additional_families:
                print("[LIVE-SELECT-DEBUG] fallback_triggered=no")
                break
            if not family_order:
                continue
            phase_family_survivor_counts = {}
            if phase_name == "preferred":
                family_order = family_order[:preferred_phase_family_cap]
                print(
                    f"[LIVE-SELECT-DEBUG] preferred_pass_started families={family_order} "
                    f"family_cap={preferred_phase_family_cap}"
                )
            else:
                print(
                    f"[LIVE-SELECT-DEBUG] fallback_triggered=yes preferred_survivors={preferred_pass_survivors}"
                )
                print(f"[LIVE-SELECT-DEBUG] additional_families_scanned={family_order}")
            print(f"[LIVE-SELECT-DEBUG] scanning_phase={phase_name} families={family_order}")
            print(f"[LIVE-SELECT-DEBUG] runtime_family_priority={family_order}")
            phase_skip_plan = {}
            phase_skipped_family_count = 0
            for family in family_order:
                stats = family_diagnostics.get(family, {})
                if family in broad_origin_families:
                    scan_count = len(family_markets.get(family, []))
                    both_sides_quoted_count = stats.get('both_sides_quoted', 0)
                    non_empty_top_book_count = stats.get('non_empty_top_book', 0)
                    narrow_spread_count = stats.get('narrow_spread', 0)
                    prefilter_survivors = stats.get('prefilter_survivors', 0)
                    final_candidates = self.live_family_runtime_stats.get(family, {}).get('final_candidates', 0)
                    quoted_to_non_empty_rate = (
                        round(non_empty_top_book_count / float(both_sides_quoted_count), 4)
                        if both_sides_quoted_count > 0 else 0.0
                    )
                    scan_to_narrow_spread_rate = (
                        round(narrow_spread_count / float(scan_count), 4)
                        if scan_count > 0 else 0.0
                    )
                    prefilter_to_final_rate = (
                        round(final_candidates / float(prefilter_survivors), 4)
                        if prefilter_survivors > 0 else 0.0
                    )
                    print(
                        f"[LIVE-SELECT-BROAD-FAMILY-EVAL] family={family} hour_utc={current_hour_utc} "
                        f"scan_count={scan_count} in_window_count={stats.get('target_window', 0)} "
                        f"both_sides_quoted_count={both_sides_quoted_count} non_empty_top_book_count={non_empty_top_book_count} "
                        f"prefilter_survivors={prefilter_survivors} broad_fallback_active={'yes' if fallback_to_broad_scan else 'no'} "
                        f"phase={phase_name} branch=active_family_order"
                    )
                    print(
                        f"[LIVE-SELECT-FAMILY-QUALITY] family={family} scan_count={scan_count} "
                        f"both_sides_quoted_count={both_sides_quoted_count} non_empty_top_book_count={non_empty_top_book_count} "
                        f"narrow_spread_count={narrow_spread_count} prefilter_survivors={prefilter_survivors} "
                        f"final_candidates={final_candidates} quoted_to_non_empty_rate={quoted_to_non_empty_rate} "
                        f"scan_to_narrow_spread_rate={scan_to_narrow_spread_rate} prefilter_to_final_rate={prefilter_to_final_rate}"
                    )
                skip_family, skip_reasons = evaluate_family_skip(family)
                phase_skip_plan[family] = (skip_family, skip_reasons)
                if skip_family:
                    phase_skipped_family_count += 1
            bypass_family_skip_for_phase = bool(phase_skip_plan) and phase_skipped_family_count == len(phase_skip_plan)
            print(
                f"[LIVE-SELECT-COUNT] stage=family_filter phase={phase_name} "
                f"before={len(family_order)} after={len(family_order) - phase_skipped_family_count if not bypass_family_skip_for_phase else len(family_order)} "
                f"filtered={phase_skipped_family_count} bypassed={1 if bypass_family_skip_for_phase else 0}"
            )
            for family in family_order:
                stats = family_diagnostics.get(family, {})
                family_pool = family_markets.get(family, [])
                skip_family, skip_reasons = phase_skip_plan.get(family, (False, []))
                print(
                    f"[LIVE-SELECT-DEBUG] family_effective_rates family={family} "
                    f"missing_quotes_rate={stats.get('missing_quotes_rate', 0.0)} "
                    f"wide_spread_rate={stats.get('wide_spread_rate', 0.0)} "
                    f"low_liquidity_rate={stats.get('low_liquidity_rate', 0.0)} "
                    f"too_early_rate={stats.get('too_early_share', 0.0)} "
                    f"survivor_rate={stats.get('survivor_rate', 0.0)}"
                )
                if skip_family and bypass_family_skip_for_phase:
                    print(
                        f"[LIVE-SELECT-DEBUG] family_skip_bypass phase={phase_name} family={family} "
                        f"reason=all_families_would_be_filtered reasons={skip_reasons}"
                    )
                    skip_family = False
                if skip_family:
                    print(
                        f"[LIVE-SELECT-DEBUG] family_skip family={family} reasons={skip_reasons} "
                        f"rates={{'missing_quotes': {stats.get('missing_quotes_rate', 0.0)}, "
                        f"'wide_spread': {stats.get('wide_spread_rate', 0.0)}, "
                        f"'low_liquidity': {stats.get('low_liquidity_rate', 0.0)}, "
                        f"'too_early': {stats.get('too_early_share', 0.0)}, "
                        f"'survivor': {stats.get('survivor_rate', 0.0)}}}"
                    )
                    if family in broad_origin_families:
                        print(
                            f"[LIVE-SELECT-DEBUG] broad_family_reject_checkpoint family={family} "
                            f"reason={','.join(skip_reasons)} stage=family_filter"
                        )
                    family_skip_counts[family] = family_skip_counts.get(family, 0) + 1
                    for skip_reason in skip_reasons:
                        family_skip_reason_counts[skip_reason] = family_skip_reason_counts.get(skip_reason, 0) + 1
                    continue
                fetched_for_family = len(family_pool)
                if family in broad_origin_families:
                    print(f"[LIVE-SELECT-DEBUG] broad_family_enter_candidate_stage family={family}")
                family_budget_cap = family_rebalance_caps.get(family, effective_max_markets_per_family)
                if fetched_for_family > family_budget_cap:
                    print(
                        f"[LIVE-SELECT-DEBUG] family_budget_cap_applied family={family} "
                        f"cap={family_budget_cap} fetched={fetched_for_family}"
                    )
                family_pool = family_pool[:family_budget_cap]
                family_scanned_counts[family] = len(family_pool)
                print(
                    f"[LIVE-SELECT-DEBUG] family_scan family={family} scanned={len(family_pool)} "
                    f"budget_cap={family_budget_cap}"
                )
                for market in family_pool:
                    ticker = str(market.get("ticker", "") or "").strip()
                    family = self.get_market_family(market)
                    raw_orderbook_payload = False
                    if isinstance(market, dict):
                        raw_orderbook_payload = bool(
                            market.get("orderbook")
                            or market.get("yes")
                            or market.get("no")
                            or market.get("yes_orders")
                            or market.get("no_orders")
                        )
                    quote_values = self.get_market_quote_values(market)
                    yes_bid = quote_values.get("yes_bid")
                    yes_ask = quote_values.get("yes_ask")
                    no_bid = quote_values.get("no_bid")
                    no_ask = quote_values.get("no_ask")
                    yes_quote_present = self.has_complete_quote_side(yes_bid, yes_ask)
                    no_quote_present = self.has_complete_quote_side(no_bid, no_ask)
                    both_sides_quote_present = yes_quote_present and no_quote_present
                    if family in broad_origin_families and not raw_orderbook_payload and not both_sides_quote_present:
                        orderbook_hydration_summary["candidates_requested_for_hydration"] += 1
                        try:
                            hydrated_market, hydration_source = self._hydrate_market_snapshot_from_market_detail(market)
                        except Exception as exc:
                            hydrated_market, hydration_source = market, f"error:{exc.__class__.__name__}"
                        market = hydrated_market
                        ticker = str(market.get("ticker", ticker) or ticker).strip()
                        family = self.get_market_family(market)
                        raw_orderbook_payload = bool(
                            market.get("orderbook")
                            or market.get("yes")
                            or market.get("no")
                            or market.get("yes_orders")
                            or market.get("no_orders")
                        ) if isinstance(market, dict) else False
                        quote_values = self.get_market_quote_values(market)
                        yes_bid = quote_values.get("yes_bid")
                        yes_ask = quote_values.get("yes_ask")
                        no_bid = quote_values.get("no_bid")
                        no_ask = quote_values.get("no_ask")
                        yes_quote_present = self.has_complete_quote_side(yes_bid, yes_ask)
                        no_quote_present = self.has_complete_quote_side(no_bid, no_ask)
                        both_sides_quote_present = yes_quote_present and no_quote_present
                        hydrated_yes_bid = self.to_float(market.get("yes_bid_dollars", market.get("yes_bid")), None)
                        hydrated_yes_ask = self.to_float(market.get("yes_ask_dollars", market.get("yes_ask")), None)
                        hydrated_no_bid = self.to_float(market.get("no_bid_dollars", market.get("no_bid")), None)
                        hydrated_no_ask = self.to_float(market.get("no_ask_dollars", market.get("no_ask")), None)
                        hydrated_quotes_complete = all(
                            value is not None for value in (hydrated_yes_bid, hydrated_yes_ask, hydrated_no_bid, hydrated_no_ask)
                        )
                        if raw_orderbook_payload or hydrated_quotes_complete:
                            orderbook_hydration_summary["candidates_successfully_hydrated"] += 1
                        else:
                            orderbook_hydration_summary["candidates_without_payload_after_hydration"] += 1
                    if self._should_trace_priority_topbook_family(family):
                        candidate_state = self._collect_topbook_candidate_state(market)
                        stage_stats = candidate_topbook_summary.setdefault(
                            family,
                            {"candidates_seen": 0, "raw_payload_present_count": 0, "mapped_non_empty_count": 0, "zero_size_count": 0},
                        )
                        stage_stats["candidates_seen"] += 1
                        if candidate_state.get("raw_orderbook_container_present") or candidate_state.get("size_fields_exist") or candidate_state.get("yes_no_arrays_exist"):
                            stage_stats["raw_payload_present_count"] += 1
                        if candidate_state.get("mapped_topbook_fields_present") or candidate_state.get("non_zero_size_fields_present"):
                            stage_stats["mapped_non_empty_count"] += 1
                        if not candidate_state.get("non_zero_size_fields_present"):
                            stage_stats["zero_size_count"] += 1
                        print(
                            f"[LIVE-SELECT-CANDIDATE-TOPBOOK] ticker={ticker} family={family} "
                            f"raw_orderbook_container_present={'yes' if candidate_state.get('raw_orderbook_container_present') else 'no'} "
                            f"mapped_topbook_fields_present={'yes' if candidate_state.get('mapped_topbook_fields_present') else 'no'} "
                            f"non_zero_size_fields_present={'yes' if candidate_state.get('non_zero_size_fields_present') else 'no'} "
                            f"relevant_keys={candidate_state.get('relevant_keys')}"
                        )
                        prefilter_stats = prefilter_input_summary.setdefault(
                            family,
                            {"candidates_seen": 0, "raw_payload_present_count": 0, "mapped_non_empty_count": 0, "zero_size_count": 0},
                        )
                        prefilter_stats["candidates_seen"] += 1
                        if candidate_state.get('raw_orderbook_container_present') or candidate_state.get('size_fields_exist') or candidate_state.get('yes_no_arrays_exist'):
                            prefilter_stats['raw_payload_present_count'] += 1
                        if candidate_state.get('mapped_topbook_fields_present') or candidate_state.get('non_zero_size_fields_present'):
                            prefilter_stats['mapped_non_empty_count'] += 1
                        if not candidate_state.get('non_zero_size_fields_present'):
                            prefilter_stats['zero_size_count'] += 1
                        print(
                            f"[LIVE-SELECT-PREFILTER-INPUT] ticker={ticker} family={family} "
                            f"raw_orderbook_present={'yes' if candidate_state.get('raw_orderbook_container_present') else 'no'} "
                            f"candidate_topbook_fields_present={'yes' if candidate_state.get('mapped_topbook_fields_present') else 'no'} "
                            f"size_fields_present={'yes' if candidate_state.get('size_fields_exist') else 'no'} "
                            f"non_empty_raw_or_mapped={'yes' if candidate_state.get('non_zero_size_fields_present') else 'no'} "
                            f"gate_context=active_prefilter_topbook_mapping"
                        )
                    liquidity_priority = self.get_market_liquidity_priority(market)
                    market_quality = self.get_market_quality_preference(market, liquidity_priority=liquidity_priority)
                    topbook_snapshot = liquidity_priority.get("topbook_snapshot") or {}
                    topbook_family = topbook_family_stats.setdefault(
                        family,
                        {
                            "candidates_seen": 0,
                            "raw_payload_present_count": 0,
                            "mapped_non_empty_top_book_count": 0,
                            "zero_size_count": 0,
                        },
                    )
                    topbook_family["candidates_seen"] += 1
                    raw_payload_present = bool(topbook_snapshot.get("raw_orderbook_container_present") or topbook_snapshot.get("raw_field_names"))
                    mapped_non_empty_top_book = liquidity_priority.get("top_book_state") in ("both_non_zero", "one_non_zero")
                    if raw_payload_present:
                        topbook_family["raw_payload_present_count"] += 1
                    if mapped_non_empty_top_book:
                        topbook_family["mapped_non_empty_top_book_count"] += 1
                    if (liquidity_priority.get("yes_top_size") or 0.0) == 0.0 and (liquidity_priority.get("no_top_size") or 0.0) == 0.0:
                        topbook_family["zero_size_count"] += 1
                    if family in broad_origin_families:
                        print(
                            f"[LIVE-SELECT-TOPBOOK-RAW] ticker={ticker} family={family} raw_orderbook_container_present={'yes' if topbook_snapshot.get('raw_orderbook_container_present') else 'no'} "
                            f"raw_yes_sizes={topbook_snapshot.get('raw_yes_sizes')} raw_no_sizes={topbook_snapshot.get('raw_no_sizes')} raw_field_names={topbook_snapshot.get('raw_field_names')}"
                        )
                        print(
                            f"[LIVE-SELECT-TOPBOOK-MAPPED] ticker={ticker} family={family} yes_bid_size={topbook_snapshot.get('yes_bid_size')} "
                            f"yes_ask_size={topbook_snapshot.get('yes_ask_size')} no_bid_size={topbook_snapshot.get('no_bid_size')} "
                            f"no_ask_size={topbook_snapshot.get('no_ask_size')} mapped_yes_top_size={liquidity_priority.get('yes_top_size')} "
                            f"mapped_no_top_size={liquidity_priority.get('no_top_size')} non_empty_top_book={'yes' if mapped_non_empty_top_book else 'no'} "
                            f"size_gt_zero={'yes' if ((liquidity_priority.get('yes_top_size') or 0.0) > 0.0 or (liquidity_priority.get('no_top_size') or 0.0) > 0.0) else 'no'} "
                            f"both_sides_quoted={'yes' if market_quality.get('both_sides_quoted') else 'no'} source_path={topbook_snapshot.get('source_path') or 'none'}"
                        )
                        if topbook_snapshot.get("failure_stage"):
                            print(
                                f"[LIVE-SELECT-TOPBOOK-FAIL] ticker={ticker} family={family} failure_stage={topbook_snapshot.get('failure_stage')} "
                                f"reason={topbook_snapshot.get('failure_reason')}"
                            )
                    quote_handoff_summary["total_candidates_arriving"] += 1
                    if raw_orderbook_payload:
                        quote_handoff_summary["candidates_with_orderbook_payload"] += 1
                    if yes_quote_present:
                        quote_handoff_summary["candidates_with_yes_quote"] += 1
                    if no_quote_present:
                        quote_handoff_summary["candidates_with_no_quote"] += 1
                    if both_sides_quote_present:
                        quote_handoff_summary["candidates_with_both_sides"] += 1
                    else:
                        quote_handoff_summary["candidates_missing_quotes"] += 1
                    family_quote_handoff = quote_handoff_family.setdefault(
                        family,
                        {
                            "candidates_seen": 0,
                            "orderbook_payload_count": 0,
                            "yes_quote_count": 0,
                            "no_quote_count": 0,
                            "both_sides_quote_count": 0,
                            "missing_quote_count": 0,
                        },
                    )
                    family_quote_handoff["candidates_seen"] += 1
                    if raw_orderbook_payload:
                        family_quote_handoff["orderbook_payload_count"] += 1
                    if yes_quote_present:
                        family_quote_handoff["yes_quote_count"] += 1
                    if no_quote_present:
                        family_quote_handoff["no_quote_count"] += 1
                    if both_sides_quote_present:
                        family_quote_handoff["both_sides_quote_count"] += 1
                    else:
                        family_quote_handoff["missing_quote_count"] += 1
                    family_obs = family_observation_stats.setdefault(
                        family,
                        {
                            "scan_count": 0,
                            "in_window_count": in_window_family_counts.get(family, 0),
                            "non_empty_top_book_count": 0,
                            "both_sides_quoted_count": 0,
                            "narrow_spread_count": 0,
                        },
                    )
                    family_obs["scan_count"] += 1
                    family_obs["in_window_count"] = in_window_family_counts.get(family, 0)
                    if liquidity_priority.get("top_book_state") in ("both_non_zero", "one_non_zero"):
                        family_obs["non_empty_top_book_count"] += 1
                    if market_quality.get("both_sides_quoted"):
                        family_obs["both_sides_quoted_count"] += 1
                    if market_quality.get("narrow_spread"):
                        family_obs["narrow_spread_count"] += 1
                    runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
                    runtime_stats["scan_count"] = runtime_stats.get("scan_count", 0) + 1
                    runtime_stats["in_window"] = runtime_stats.get("in_window", 0) + (1 if in_window_family_counts.get(family, 0) > 0 and family_obs["scan_count"] == 1 else 0)
                    if liquidity_priority.get("top_book_state") in ("both_non_zero", "one_non_zero"):
                        runtime_stats["non_empty_top_book_count"] = runtime_stats.get("non_empty_top_book_count", 0) + 1
                    if market_quality.get("both_sides_quoted"):
                        runtime_stats["both_sides_quoted_count"] = runtime_stats.get("both_sides_quoted_count", 0) + 1
                    if market_quality.get("narrow_spread"):
                        runtime_stats["narrow_spread_count"] = runtime_stats.get("narrow_spread_count", 0) + 1
                    allowed, reason, family = self.coarse_prefilter(market)
                    if reason == "low_liquidity":
                        prefilter_snapshot = self.get_prefilter_liquidity_snapshot(market)
                        effective_liquidity_value = prefilter_snapshot.get("effective_liquidity_value")
                        threshold_value = prefilter_snapshot.get("threshold")
                        liquidity_family_stats = liquidity_check_family_stats.setdefault(
                            family,
                            {
                                "candidates_seen": 0,
                                "candidates_failed_low_liquidity": 0,
                                "effective_values": [],
                                "thresholds": [],
                            },
                        )
                        liquidity_family_stats["candidates_seen"] += 1
                        liquidity_family_stats["candidates_failed_low_liquidity"] += 1
                        if effective_liquidity_value is not None:
                            liquidity_family_stats["effective_values"].append(float(effective_liquidity_value))
                        if threshold_value is not None:
                            liquidity_family_stats["thresholds"].append(float(threshold_value))
                        print(
                            f"[LIVE-SELECT-LIQUIDITY-CHECK] family={family} ticker={ticker} "
                            f"yes_bid={prefilter_snapshot.get('yes_bid')} yes_ask={prefilter_snapshot.get('yes_ask')} "
                            f"no_bid={prefilter_snapshot.get('no_bid')} no_ask={prefilter_snapshot.get('no_ask')} "
                            f"yes_top_size={prefilter_snapshot.get('yes_top_size')} no_top_size={prefilter_snapshot.get('no_top_size')} "
                            f"effective_top_size={prefilter_snapshot.get('effective_top_size')} "
                            f"effective_liquidity_metric={prefilter_snapshot.get('effective_liquidity_metric')} "
                            f"effective_liquidity_value={effective_liquidity_value} min_top_size_threshold={prefilter_snapshot.get('min_top_size_threshold')} "
                            f"pass=no threshold={threshold_value} source_path=coarse_prefilter_liquidity_gate"
                        )
                        print(
                            f"[LIVE-SELECT-LIQUIDITY-DROP] family={family} ticker={ticker} "
                            f"effective_liquidity_value={effective_liquidity_value} threshold={threshold_value} "
                            f"yes_top_size={prefilter_snapshot.get('yes_top_size')} no_top_size={prefilter_snapshot.get('no_top_size')} "
                            f"reason=coarse_prefilter_low_liquidity"
                        )
                    if not allowed and fallback_to_broad_scan and family in broad_origin_families and not self.is_targeted_series_market(market):
                        grace_allowed, grace_blocker, grace_metrics = self.evaluate_broad_prefilter_grace(
                            market,
                            reason,
                            liquidity_priority=liquidity_priority,
                            market_quality=market_quality,
                        )
                        if grace_allowed:
                            broad_prefilter_grace_used_count += 1
                            broad_prefilter_grace_reason_counts[f"used:{reason}"] = broad_prefilter_grace_reason_counts.get(f"used:{reason}", 0) + 1
                            print(
                                f"[LIVE-SELECT-DEBUG] broad_prefilter_grace_used ticker={ticker} family={family} original_reason={reason}"
                            )
                            allowed = True
                            reason = None
                        else:
                            broad_prefilter_grace_blocked_count += 1
                            broad_prefilter_grace_reason_counts[f"blocked:{reason}:{grace_blocker}"] = broad_prefilter_grace_reason_counts.get(f"blocked:{reason}:{grace_blocker}", 0) + 1
                            if grace_metrics.get("spread") is not None:
                                broad_prefilter_grace_blocked_spread_values.append(grace_metrics.get("spread"))
                            if grace_metrics.get("one_sided_top_book"):
                                broad_prefilter_grace_blocked_one_sided_top_book_count += 1
                            if grace_metrics.get("both_sides_quoted"):
                                broad_prefilter_grace_blocked_both_sides_quoted_count += 1
                            if grace_metrics.get("recent_activity"):
                                broad_prefilter_grace_blocked_recent_activity_count += 1
                            print(
                                f"[LIVE-SELECT-DEBUG] broad_prefilter_grace_blocked ticker={ticker} family={family} original_reason={reason} blocker={grace_blocker} "
                                f"spread={grace_metrics.get('spread')} spread_threshold={grace_metrics.get('spread_threshold')} "
                                f"yes_top_size={grace_metrics.get('yes_top_size')} no_top_size={grace_metrics.get('no_top_size')} "
                                f"both_sides_quoted={grace_metrics.get('both_sides_quoted')} recent_activity={grace_metrics.get('recent_activity')} "
                                f"liquidity={grace_metrics.get('liquidity')} liquidity_threshold={grace_metrics.get('liquidity_threshold')} "
                                f"one_sided_top_book={grace_metrics.get('one_sided_top_book')}"
                            )
                            for near_miss_reason in grace_metrics.get("near_miss_reasons", []):
                                print(
                                    f"[LIVE-SELECT-DEBUG] broad_prefilter_near_miss ticker={ticker} family={family} "
                                    f"original_reason={reason} near_miss_reason={near_miss_reason}"
                                )
                    if not allowed:
                        if reason == "missing_quotes":
                            missing_side = "both"
                            if yes_quote_present and not no_quote_present:
                                missing_side = "no"
                            elif no_quote_present and not yes_quote_present:
                                missing_side = "yes"
                            hydrated_completeness_result = "complete" if self.has_complete_numeric_quotes(market) else "incomplete"
                            coarse_prefilter_both_sides_quoted = yes_quote_present and no_quote_present
                            print(
                                f"[LIVE-SELECT-QUOTE-CONSISTENCY] ticker={ticker} hydrated_completeness_result={hydrated_completeness_result} "
                                f"coarse_prefilter_both_sides_quoted={'yes' if coarse_prefilter_both_sides_quoted else 'no'} coarse_missing_side={missing_side} "
                                f"yes_bid={yes_bid} yes_ask={yes_ask} no_bid={no_bid} no_ask={no_ask} reason=coarse_prefilter_missing_quotes"
                            )
                            print(
                                f"[LIVE-SELECT-QUOTE-DROP] family={family} ticker={ticker} missing_side={missing_side} "
                                f"orderbook_payload_present={'yes' if raw_orderbook_payload else 'no'} gate=coarse_prefilter_missing_quotes"
                            )
                        if liquidity_priority.get("has_real_liquidity"):
                            liquidity_visible_market_count += 1
                        if liquidity_priority.get("empty_book"):
                            empty_book_market_count += 1
                        prefilter_rejects[(reason, family)] = prefilter_rejects.get((reason, family), 0) + 1
                        prefilter_reject_reason_counts[reason] = prefilter_reject_reason_counts.get(reason, 0) + 1
                        if len(sample_skips) < 5:
                            sample_skips.append((ticker, reason))
                        print(
                            f"[LIVE-SELECT-LIQUIDITY] ticker={ticker} family={family} action=skip "
                            f"reason={reason} liquidity_score={liquidity_priority.get('score', 0.0)} "
                            f"reasons={liquidity_priority.get('reasons', [])} "
                            f"top_book_state={liquidity_priority.get('top_book_state')}"
                        )
                        adaptive_probe_snapshot = self.get_prefilter_liquidity_snapshot(market)
                        adaptive_spread = adaptive_probe_snapshot.get('spread')
                        adaptive_yes_top_size = adaptive_probe_snapshot.get('yes_top_size', 0.0) or 0.0
                        adaptive_no_top_size = adaptive_probe_snapshot.get('no_top_size', 0.0) or 0.0
                        adaptive_top_book_state = adaptive_probe_snapshot.get('top_book_state')
                        adaptive_has_some_top_book = (adaptive_yes_top_size > 0 or adaptive_no_top_size > 0)
                        adaptive_near_narrow = (
                            adaptive_spread is not None
                            and adaptive_spread >= 0
                            and adaptive_spread <= MAX_ENTRY_SPREAD
                        )
                        adaptive_borderline_spread = bool(market_quality.get('narrow_spread')) or adaptive_near_narrow
                        adaptive_activity_or_book = (
                            bool(market_quality.get('recent_activity'))
                            or adaptive_has_some_top_book
                            or bool(adaptive_probe_snapshot.get('both_sides_quoted'))
                        )
                        adaptive_both_sides_dead = (
                            adaptive_top_book_state == 'zero_top_book'
                            and not adaptive_probe_snapshot.get('both_sides_quoted')
                        )
                        adaptive_allowed_reason = reason in ('missing_quotes', 'wide_spread', 'low_liquidity')
                        adaptive_probe_reason = None
                        adaptive_probe_blocker = None
                        if not adaptive_probe_mode:
                            adaptive_probe_blocker = 'adaptive_mode_disabled'
                        elif not adaptive_allowed_reason:
                            adaptive_probe_blocker = f'{reason}_not_probe_eligible'
                        elif not adaptive_borderline_spread:
                            adaptive_probe_blocker = 'spread_not_borderline'
                        elif not adaptive_activity_or_book:
                            adaptive_probe_blocker = 'no_quality_signal'
                        elif adaptive_both_sides_dead:
                            adaptive_probe_blocker = 'both_sides_dead'
                        else:
                            adaptive_probe_reason = f'{reason}_borderline_quality'
                        if adaptive_probe_reason and ticker not in family_adaptive_probe_meta:
                            probe_prescore = self.compute_entry_presolve_score(market)
                            probe_prescore['adaptive_probe'] = True
                            probe_prescore['adaptive_probe_reason'] = adaptive_probe_reason
                            family_adaptive_probe_pool.append((probe_prescore, market))
                            family_adaptive_probe_meta[ticker] = {
                                'family': family,
                                'reason': adaptive_probe_reason,
                            }
                            family_probe_families.add(family)
                            family_probe_entered_count += 1
                            print(
                                f"[LIVE-SELECT-ADAPTIVE] family_probe_enter family={family} ticker={ticker} reason={adaptive_probe_reason}"
                            )
                        elif adaptive_probe_blocker:
                            family_probe_blocker_counts[adaptive_probe_blocker] = family_probe_blocker_counts.get(adaptive_probe_blocker, 0) + 1
                            print(
                                f"[LIVE-SELECT-ADAPTIVE] family_probe_blocked family={family} ticker={ticker} blocker={adaptive_probe_blocker}"
                            )
                        if reason == "low_liquidity":
                            prefilter_snapshot = self.get_prefilter_liquidity_snapshot(market)
                            family_stats = low_liquidity_prefilter_stats.setdefault(
                                family,
                                {
                                    "count": 0,
                                    "liquidity_sum": 0.0,
                                    "empty_books": 0,
                                    "partially_quoted_books": 0,
                                },
                            )
                            family_stats["count"] += 1
                            family_stats["liquidity_sum"] += prefilter_snapshot.get("liquidity", 0.0)
                            top_book_state = prefilter_snapshot.get("top_book_state") or "unknown"
                            prefilter_low_liquidity_top_book_states[top_book_state] = (
                                prefilter_low_liquidity_top_book_states.get(top_book_state, 0) + 1
                            )
                            if top_book_state == "zero_top_book":
                                family_stats["empty_books"] += 1
                                liquidity_failure_zero_top_book_count += 1
                            elif top_book_state == "one_non_zero":
                                family_stats["partially_quoted_books"] += 1
                                liquidity_failure_one_sided_top_book_count += 1
                            elif top_book_state == "both_non_zero":
                                liquidity_failure_both_sides_top_book_count += 1
                            print(
                                f"[LIVE-SELECT-PREFILTER-LIQUIDITY] ticker={prefilter_snapshot.get('ticker')} "
                                f"family={family} liquidity={prefilter_snapshot.get('liquidity')} "
                                f"threshold={prefilter_snapshot.get('threshold')} "
                                f"yes_top_size={prefilter_snapshot.get('yes_top_size')} "
                                f"no_top_size={prefilter_snapshot.get('no_top_size')} "
                                f"spread={prefilter_snapshot.get('spread')} "
                                f"both_sides_quoted={prefilter_snapshot.get('both_sides_quoted')}"
                            )
                            if self.is_targeted_series_market(market):
                                targeted_liquidity_failures_count += 1
                                print(
                                    f"[LIVE-SELECT-OBSERVE] targeted_liquidity_fail hour_utc={current_hour_utc} "
                                    f"family={family} ticker={prefilter_snapshot.get('ticker')} "
                                    f"yes_top_size={prefilter_snapshot.get('yes_top_size')} no_top_size={prefilter_snapshot.get('no_top_size')} "
                                    f"both_sides_quoted={prefilter_snapshot.get('both_sides_quoted')} spread={prefilter_snapshot.get('spread')} "
                                    f"recent_activity={market_quality.get('recent_activity', False)} liquidity={prefilter_snapshot.get('liquidity')} "
                                    f"threshold={prefilter_snapshot.get('threshold')} top_book_state={prefilter_snapshot.get('top_book_state')}"
                                )
                            elif family in broad_origin_families:
                                broad_liquidity_failures_count += 1
                                print(
                                    f"[LIVE-SELECT-OBSERVE] broad_liquidity_fail hour_utc={current_hour_utc} "
                                    f"family={family} ticker={prefilter_snapshot.get('ticker')} "
                                    f"yes_top_size={prefilter_snapshot.get('yes_top_size')} no_top_size={prefilter_snapshot.get('no_top_size')} "
                                    f"both_sides_quoted={prefilter_snapshot.get('both_sides_quoted')} spread={prefilter_snapshot.get('spread')} "
                                    f"recent_activity={market_quality.get('recent_activity', False)} liquidity={prefilter_snapshot.get('liquidity')} "
                                    f"threshold={prefilter_snapshot.get('threshold')} top_book_state={prefilter_snapshot.get('top_book_state')}"
                                )
                        if self.is_targeted_series_market(market):
                            print(
                                f"[LIVE-SELECT-DEBUG] targeted_reject family={family} "
                                f"ticker={ticker} reason={reason}"
                            )
                        print(f"[LIVE-SELECT-FILTER] ticker={ticker} reason={reason}")
                        continue
                    prescore = self.compute_entry_presolve_score(market)
                    liquidity_priority = prescore.get("liquidity_priority", {})
                    if liquidity_priority.get("has_real_liquidity"):
                        liquidity_visible_market_count += 1
                    if liquidity_priority.get("empty_book"):
                        empty_book_market_count += 1
                    print(
                        f"[LIVE-SELECT-LIQUIDITY] ticker={ticker} family={family} action=prioritize "
                        f"liquidity_score={liquidity_priority.get('score', 0.0)} "
                        f"reasons={liquidity_priority.get('reasons', [])} "
                        f"top_book_state={liquidity_priority.get('top_book_state')}"
                    )
                    prefilter_pool.append((prescore, market))
                    prefilter_survivor_family_counts[family] = prefilter_survivor_family_counts.get(family, 0) + 1
                    phase_family_survivor_counts[family] = phase_family_survivor_counts.get(family, 0) + 1
                    if self.is_targeted_series_market(market) and family in targeted_family_activity:
                        targeted_family_activity[family]["prefilter_survivors"] += 1
                    if phase_name == "preferred":
                        preferred_pass_survivors += 1
                    runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
                    runtime_stats["prefilter_survivors"] = runtime_stats.get("prefilter_survivors", 0) + 1
                if len(prefilter_pool) >= MAX_PREFILTER_POOL:
                    break
            print(
                f"[LIVE-SELECT-DEBUG] phase_survivors phase={phase_name} "
                f"counts={sorted(phase_family_survivor_counts.items())}"
            )
            if phase_name == "preferred":
                print(
                    f"[LIVE-SELECT-DEBUG] preferred_pass_survivors={preferred_pass_survivors}"
                )
                lead_family_survivors = phase_family_survivor_counts.get(preferred_lead_family, 0)
                if preferred_lead_family and lead_family_survivors == 0:
                    preferred_family_rejects = sorted(
                        (
                            (reason, count)
                            for (reason, family), count in prefilter_rejects.items()
                            if family == preferred_lead_family
                        ),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:5]
                    print(
                        f"[LIVE-SELECT-DEBUG] preferred_family_failure family={preferred_lead_family} "
                        f"reason=no_survivors reject_reasons={preferred_family_rejects}"
                    )
                    expand_to_additional_families = True
            if prefilter_pool and not (phase_name == "preferred" and expand_to_additional_families):
                break

        prefilter_pool = sorted(prefilter_pool, key=lambda item: item[0]["score"], reverse=True)[:MAX_PREFILTER_POOL]
        post_prefilter_family_counts = {}
        for prescore, market in prefilter_pool:
            family = prescore.get("family") or self.get_market_family(market)
            if not self._should_trace_priority_topbook_family(family):
                continue
            post_prefilter_family_counts[family] = post_prefilter_family_counts.get(family, 0) + 1
            liquidity_priority = prescore.get("liquidity_priority", {}) or {}
            self._emit_priority_downstream_trace(
                "[LIVE-SELECT-POST-PREFILTER]",
                market,
                family=family,
                pass_fail="survivor",
                spread=round((prescore.get("yes_ask", 0.0) or 0.0) - (prescore.get("yes_bid", 0.0) or 0.0), 4),
                quote_alive='yes' if liquidity_priority.get("both_sides_quoted") else 'no',
                topbook=liquidity_priority.get("top_book_state"),
                yes_top_size=liquidity_priority.get("yes_top_size"),
                no_top_size=liquidity_priority.get("no_top_size"),
                min_top_size=self.get_market_min_top_of_book_size(market),
                removed_by="none",
            )
        self._emit_priority_downstream_trace(
            "[LIVE-SELECT-POST-PREFILTER]",
            family="KXBTC15M",
            ticker="summary",
            summary="yes",
            counts=sorted(post_prefilter_family_counts.items()),
        )
        print(
            f"[LIVE-SELECT-QUOTE-HANDOFF] total_candidates_arriving={quote_handoff_summary['total_candidates_arriving']} "
            f"candidates_with_orderbook_payload={quote_handoff_summary['candidates_with_orderbook_payload']} "
            f"candidates_with_yes_quote={quote_handoff_summary['candidates_with_yes_quote']} "
            f"candidates_with_no_quote={quote_handoff_summary['candidates_with_no_quote']} "
            f"candidates_with_both_sides={quote_handoff_summary['candidates_with_both_sides']} "
            f"candidates_missing_quotes={quote_handoff_summary['candidates_missing_quotes']} "
            f"source_breakdown={{'selection_source': '{selection_merge_source}', 'fallback_to_broad': {'yes' if fallback_to_broad_scan else 'no'}}}"
        )
        for family, stats in sorted(quote_handoff_family.items()):
            print(
                f"[LIVE-SELECT-QUOTE-HANDOFF-FAMILY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"orderbook_payload_count={stats.get('orderbook_payload_count', 0)} yes_quote_count={stats.get('yes_quote_count', 0)} "
                f"no_quote_count={stats.get('no_quote_count', 0)} both_sides_quote_count={stats.get('both_sides_quote_count', 0)} "
                f"missing_quote_count={stats.get('missing_quote_count', 0)}"
            )
        print(
            f"[LIVE-SELECT-ORDERBOOK-HYDRATION] candidates_requested_for_hydration={orderbook_hydration_summary['candidates_requested_for_hydration']} "
            f"candidates_successfully_hydrated={orderbook_hydration_summary['candidates_successfully_hydrated']} "
            f"candidates_without_payload_after_hydration={orderbook_hydration_summary['candidates_without_payload_after_hydration']} "
            f"batch_info={{'selection_source': '{selection_merge_source}', 'fallback_to_broad': {'yes' if fallback_to_broad_scan else 'no'}, 'hydration_path': '{orderbook_hydration_summary['hydration_path']}'}}"
        )
        print(
            f"[LIVE-SELECT-ORDERBOOK-HYDRATION-POST] requested={orderbook_hydration_summary['candidates_requested_for_hydration']} "
            f"hydrated={orderbook_hydration_summary['candidates_successfully_hydrated']} "
            f"still_without_payload={orderbook_hydration_summary['candidates_without_payload_after_hydration']} "
            f"source_path={orderbook_hydration_summary['hydration_path']}"
        )
        for family, stats in sorted(hydration_source_summary.items()):
            print(
                f"[LIVE-SELECT-HYDRATION-SUMMARY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"raw_payload_present_count={stats.get('raw_payload_present_count', 0)} mapped_non_empty_count={stats.get('mapped_non_empty_count', 0)} "
                f"zero_size_count={stats.get('zero_size_count', 0)}"
            )
        for family, stats in sorted(candidate_topbook_summary.items()):
            print(
                f"[LIVE-SELECT-CANDIDATE-TOPBOOK-SUMMARY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"raw_payload_present_count={stats.get('raw_payload_present_count', 0)} mapped_non_empty_count={stats.get('mapped_non_empty_count', 0)} "
                f"zero_size_count={stats.get('zero_size_count', 0)}"
            )
        for family, stats in sorted(prefilter_input_summary.items()):
            print(
                f"[LIVE-SELECT-PREFILTER-INPUT-SUMMARY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"raw_payload_present_count={stats.get('raw_payload_present_count', 0)} mapped_non_empty_count={stats.get('mapped_non_empty_count', 0)} "
                f"zero_size_count={stats.get('zero_size_count', 0)}"
            )
        print(
            f"[LIVE-SELECT-DEBUG] broad_candidates_after_family_rejection={sum(family_scanned_counts.get(family, 0) for family in broad_origin_families)}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] broad_candidates_before_prefilter={sum(family_scanned_counts.get(family, 0) for family in broad_origin_families)}"
        )
        family_skip_total = sum(family_skip_counts.values())
        scanned_market_count = sum(family_scanned_counts.values())
        print(f"[LIVE-SELECT-DEBUG] total_markets_after_family_filter={scanned_market_count}")
        prefilter_reject_total = sum(prefilter_rejects.values())
        print(
            f"[LIVE-SELECT-COUNT] stage=coarse_prefilter before={scanned_market_count} "
            f"after={len(prefilter_pool)} filtered={prefilter_reject_total} family_skipped={family_skip_total}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] family_skip_reason_counts={sorted(family_skip_reason_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] prefilter_reject_reason_counts={sorted(prefilter_reject_reason_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] prefilter_low_liquidity_top_book_states={sorted(prefilter_low_liquidity_top_book_states.items(), key=lambda item: item[1], reverse=True)}"
        )
        prefilter_timing_buckets = {}
        for prescore, market in prefilter_pool:
            bucket = self.get_event_time_bucket(prescore["minutes_to_event"])
            prefilter_timing_buckets[bucket] = prefilter_timing_buckets.get(bucket, 0) + 1
        print(
            f"[LIVE-SELECT-LIQUIDITY] visible_markets={liquidity_visible_market_count} "
            f"empty_books={empty_book_market_count} checked_markets={scanned_market_count}"
        )
        for family, stats in sorted(low_liquidity_prefilter_stats.items()):
            avg_liquidity = 0.0
            if stats.get("count", 0) > 0:
                avg_liquidity = stats.get("liquidity_sum", 0.0) / float(stats.get("count", 1))
            print(
                f"[LIVE-SELECT-PREFILTER-SUMMARY] family={family} low_liquidity_failures={stats.get('count', 0)} "
                f"avg_liquidity_seen={round(avg_liquidity, 4)} empty_books={stats.get('empty_books', 0)} "
                f"partially_quoted_books={stats.get('partially_quoted_books', 0)}"
            )
        for family, stats in sorted(liquidity_check_family_stats.items()):
            effective_values = stats.get("effective_values", [])
            thresholds = stats.get("thresholds", [])
            min_effective = min(effective_values) if effective_values else None
            avg_effective = (sum(effective_values) / float(len(effective_values))) if effective_values else None
            max_effective = max(effective_values) if effective_values else None
            threshold_used = thresholds[-1] if thresholds else None
            print(
                f"[LIVE-SELECT-LIQUIDITY-SUMMARY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"candidates_failed_low_liquidity={stats.get('candidates_failed_low_liquidity', 0)} "
                f"min_effective_top_size={min_effective} avg_effective_top_size={round(avg_effective, 4) if avg_effective is not None else None} "
                f"max_effective_top_size={max_effective} threshold_used={threshold_used}"
            )
        for family, stats in sorted(topbook_family_stats.items()):
            print(
                f"[LIVE-SELECT-TOPBOOK-SUMMARY] family={family} candidates_seen={stats.get('candidates_seen', 0)} "
                f"raw_payload_present_count={stats.get('raw_payload_present_count', 0)} mapped_non_empty_top_book_count={stats.get('mapped_non_empty_top_book_count', 0)} "
                f"zero_size_count={stats.get('zero_size_count', 0)} gate_context=active_prefilter_topbook_mapping"
            )
        print(
            f"[LIVE-SELECT-DEBUG] broad_candidates_after_prefilter={sum(prefilter_survivor_family_counts.get(family, 0) for family in broad_origin_families)}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] broad_prefilter_grace_summary used={broad_prefilter_grace_used_count} "
            f"blocked={broad_prefilter_grace_blocked_count} "
            f"reasons={sorted(broad_prefilter_grace_reason_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] broad_prefilter_grace_blocker_metrics "
            f"spread_values={broad_prefilter_grace_blocked_spread_values} "
            f"one_sided_top_book_count={broad_prefilter_grace_blocked_one_sided_top_book_count} "
            f"both_sides_quoted_count={broad_prefilter_grace_blocked_both_sides_quoted_count} "
            f"recent_activity_count={broad_prefilter_grace_blocked_recent_activity_count}"
        )
        print(
            f"[LIVE-SELECT-OBSERVE] liquidity_cycle_summary hour_utc={current_hour_utc} "
            f"targeted_failures={targeted_liquidity_failures_count} broad_failures={broad_liquidity_failures_count} "
            f"zero_top_book={liquidity_failure_zero_top_book_count} one_sided_top_book={liquidity_failure_one_sided_top_book_count} "
            f"both_sides_top_book={liquidity_failure_both_sides_top_book_count}"
        )
        for prescore, market in prefilter_pool[:5]:
            print(
                f"[LIVE-SELECT-PRESCORE] ticker={market.get('ticker')} family={prescore['family']} "
                f"score={prescore['score']} spread={prescore['spread']} volume={prescore['volume']} "
                f"liquidity={prescore['liquidity']} price={prescore['price']} "
                f"minutes_to_event={prescore['minutes_to_event']} "
                f"liquidity_priority={prescore.get('liquidity_priority', {})} "
                f"components={prescore['components']}"
            )

        family_suppression_cycle_id = self._get_phase55_cycle_id()
        missing_quote_suppressed_families = []
        family_suppression_skipped_by_family = {}
        for family, fetched_count in sorted(family_scanned_counts.items()):
            if fetched_count <= 0:
                continue
            prefilter_survivors = prefilter_survivor_family_counts.get(family, 0)
            missing_quotes_in_cycle = sum(
                count for (reason, reject_family), count in prefilter_rejects.items()
                if reject_family == family and reason == "missing_quotes"
            )
            total_rejects_for_family = sum(
                count for (_reason, reject_family), count in prefilter_rejects.items()
                if reject_family == family
            )
            if prefilter_survivors > 0:
                family_suppression_skipped_by_family[family] = prefilter_survivors
                print(
                    f"[LIVE-SELECT-FAMILY-SUPPRESS] family={family} cycle_id={family_suppression_cycle_id} "
                    f"suppress_reason=cycle_missing_quote_family action=skipped_due_to_prefilter_survivor fetched_in_cycle={fetched_count} "
                    f"missing_quotes_in_cycle={missing_quotes_in_cycle} "
                    f"prefilter_survivors_in_cycle={prefilter_survivors}"
                )
                continue
            if missing_quotes_in_cycle <= 0 or total_rejects_for_family != missing_quotes_in_cycle:
                continue
            missing_quote_suppressed_families.append(family)
            print(
                f"[LIVE-SELECT-FAMILY-SUPPRESS] family={family} cycle_id={family_suppression_cycle_id} "
                f"suppress_reason=cycle_missing_quote_family action=applied fetched_in_cycle={fetched_count} "
                f"missing_quotes_in_cycle={missing_quotes_in_cycle} "
                f"prefilter_survivors_in_cycle={prefilter_survivors}"
            )
        if missing_quote_suppressed_families:
            family_adaptive_probe_pool = [
                (prescore, market)
                for prescore, market in family_adaptive_probe_pool
                if prescore.get("family") not in set(missing_quote_suppressed_families)
            ]

        candidates = []
        final_family_counts = {}
        strict_reject_reason_counts = {}
        strict_pool = prefilter_pool[:MAX_STRICT_VALIDATION_POOL] + family_adaptive_probe_pool
        prefilter_survivors_total = sum(prefilter_survivor_family_counts.values())
        targeted_prefilter_survivors_total_handoff = sum(
            count for family, count in prefilter_survivor_family_counts.items()
            if family in targeted_family_activity
        )
        broad_prefilter_survivors_total_handoff = sum(
            count for family, count in prefilter_survivor_family_counts.items()
            if family in broad_origin_families
        )
        strict_pool_initial_count = len(strict_pool)
        strict_pool_after_family_suppression_count = strict_pool_initial_count
        strict_pool_removed_by_family_suppression = 0
        strict_pool_removed_by_prestrict = 0
        shadow_entry_timing_stats = {
            "shadow_candidates_seen": 0,
            "shadow_crossovers_detected": 0,
            "shadow_micro_pullbacks_detected": 0,
            "shadow_immediate_entry_blocks": 0,
            "shadow_entry_ready_count": 0,
            "shadow_insufficient_data_count": 0,
        }
        print(
            f"[LIVE-SELECT-COUNT] stage=pre_strict_validation before={len(prefilter_pool) + len(family_adaptive_probe_pool)} "
            f"after={len(strict_pool)} filtered={max(0, len(prefilter_pool) - MAX_STRICT_VALIDATION_POOL)}"
        )
        print(
            f"[LIVE-SELECT-STRICT-HANDOFF] prefilter_survivors_total={prefilter_survivors_total} "
            f"targeted_prefilter_survivors={targeted_prefilter_survivors_total_handoff} "
            f"broad_prefilter_survivors={broad_prefilter_survivors_total_handoff} "
            f"candidates_entering_strict_validation={strict_pool_initial_count} "
            f"source_breakdown={{'prefilter_pool': {len(prefilter_pool)}, 'adaptive_probe_pool': {len(family_adaptive_probe_pool)}}}"
        )
        print(f"[LIVE-SELECT-DEBUG] entering_strict_validation_initial={strict_pool_initial_count} source=strict_pool_initial")
        if strict_pool:
            for prescore, market in strict_pool:
                family = prescore["family"]
                shadow_ticker = str(market.get("ticker", "") or "").strip()
                try:
                    shadow_q = self.build_quote(market)
                except Exception:
                    shadow_q = None
                shadow_observation = self.get_entry_timing_observation(
                    market,
                    shadow_q,
                    family,
                    stage_label="pre_strict_validation",
                ) if shadow_q is not None else {
                    "ticker": shadow_ticker,
                    "family": family,
                    "stochastic_inputs_available": False,
                    "stochastic_source": "unavailable",
                    "history_points": 0,
                    "history_reason": "quote_build_failed",
                    "timing_history_key": self._build_phase55_timing_history_key(market, None, family=family),
                    "history_appended": False,
                    "history_append_reason": "quote_build_failed",
                    "cycle_id": self._get_phase55_cycle_id(),
                    "stochastic_k": None,
                    "stochastic_d": None,
                    "bullish_crossover": False,
                    "bearish_crossover": False,
                    "micro_pullback_detected": False,
                    "immediate_entry_blocked": False,
                    "phase55_status": "insufficient_data",
                }
                shadow_crossover_detected = bool(
                    shadow_observation.get("bullish_crossover") or shadow_observation.get("bearish_crossover")
                )
                shadow_entry_timing_stats["shadow_candidates_seen"] += 1
                if shadow_crossover_detected:
                    shadow_entry_timing_stats["shadow_crossovers_detected"] += 1
                    print(
                        f"[LIVE-ENTRY-TIMING-SHADOW-TRIGGER] ticker={shadow_observation.get('ticker')} family={family} "
                        f"source_stage=pre_strict_validation crossover_detected=yes"
                    )
                if shadow_observation.get("micro_pullback_detected"):
                    shadow_entry_timing_stats["shadow_micro_pullbacks_detected"] += 1
                if shadow_observation.get("immediate_entry_blocked"):
                    shadow_entry_timing_stats["shadow_immediate_entry_blocks"] += 1
                    print(
                        f"[LIVE-ENTRY-TIMING-SHADOW-REJECT] ticker={shadow_observation.get('ticker')} family={family} "
                        f"source_stage=pre_strict_validation blocker=immediate_entry_protection"
                    )
                if shadow_observation.get("phase55_status") == "entry_ready":
                    shadow_entry_timing_stats["shadow_entry_ready_count"] += 1
                if shadow_observation.get("phase55_status") == "insufficient_data":
                    shadow_entry_timing_stats["shadow_insufficient_data_count"] += 1
                if not shadow_observation.get("stochastic_inputs_available"):
                    print(
                        f"[LIVE-ENTRY-TIMING-SHADOW-DATA] ticker={shadow_observation.get('ticker')} family={family} "
                        f"stochastic_source={shadow_observation.get('stochastic_source')} "
                        f"history_points={shadow_observation.get('history_points')} "
                        f"history_reason={shadow_observation.get('history_reason')} "
                        f"timing_history_key={shadow_observation.get('timing_history_key')}"
                    )
                print(
                    f"[LIVE-ENTRY-TIMING-SHADOW] ticker={shadow_observation.get('ticker')} family={family} "
                    f"source_stage=pre_strict_validation stochastic_inputs_available={shadow_observation.get('stochastic_inputs_available')} "
                    f"stochastic_source={shadow_observation.get('stochastic_source')} "
                    f"history_points={shadow_observation.get('history_points')} "
                    f"history_reason={shadow_observation.get('history_reason')} "
                    f"timing_history_key={shadow_observation.get('timing_history_key')} "
                    f"stochastic_k={shadow_observation.get('stochastic_k')} stochastic_d={shadow_observation.get('stochastic_d')} "
                    f"crossover_detected={'yes' if shadow_crossover_detected else 'no'} "
                    f"micro_pullback_detected={'yes' if shadow_observation.get('micro_pullback_detected') else 'no'} "
                    f"immediate_entry_block={'yes' if shadow_observation.get('immediate_entry_blocked') else 'no'} "
                    f"phase55_shadow_status={shadow_observation.get('phase55_status')}"
                )
            print(
                f"[LIVE-ENTRY-TIMING-SHADOW-SUMMARY] shadow_candidates_seen={shadow_entry_timing_stats['shadow_candidates_seen']} "
                f"shadow_crossovers_detected={shadow_entry_timing_stats['shadow_crossovers_detected']} "
                f"shadow_micro_pullbacks_detected={shadow_entry_timing_stats['shadow_micro_pullbacks_detected']} "
                f"shadow_immediate_entry_blocks={shadow_entry_timing_stats['shadow_immediate_entry_blocks']} "
                f"shadow_entry_ready_count={shadow_entry_timing_stats['shadow_entry_ready_count']} "
                f"shadow_insufficient_data_count={shadow_entry_timing_stats['shadow_insufficient_data_count']}"
            )
        else:
            print("[LIVE-ENTRY-TIMING-SHADOW-SUMMARY] shadow_candidates_seen=0")
        strict_pool_target_count = len(strict_pool)
        strict_pool_overflow = prefilter_pool[MAX_STRICT_VALIDATION_POOL:]
        strict_pool_initial_family_counts = {}
        for prescore, _market in strict_pool:
            family = prescore.get("family")
            if family:
                strict_pool_initial_family_counts[family] = strict_pool_initial_family_counts.get(family, 0) + 1
        family_suppression_stats = {}
        for prescore, market in strict_pool:
            family = prescore["family"]
            is_family_probe = bool(prescore.get("adaptive_probe"))
            family_stats = family_suppression_stats.setdefault(
                family,
                {
                    "candidate_count": 0,
                    "dead_book_rejects": 0,
                    "missing_size_rejects": 0,
                    "zero_book_rejects": 0,
                    "positive_top_size_candidates": 0,
                },
            )
            family_stats["candidate_count"] += 1
            candidate_path = "targeted_series_path" if self.is_targeted_series_market(market) else (
                "adaptive_probe_path" if is_family_probe else (
                    "broad_path" if family in broad_origin_families else "unknown_path"
                )
            )
            try:
                suppression_q = self.build_quote(market)
                suppression_snapshot = self.get_strict_validation_snapshot(market, suppression_q)
            except Exception:
                continue
            suppression_orderbook = suppression_snapshot.get("orderbook", {})
            yes_top_size = suppression_orderbook.get("yes_top_size")
            no_top_size = suppression_orderbook.get("no_top_size")
            hydration_result = None
            if (
                candidate_path == "targeted_series_path"
                and (yes_top_size or 0.0) == 0.0
                and (no_top_size or 0.0) == 0.0
                and (
                    suppression_orderbook.get("orderbook_source") == "root"
                    or not suppression_orderbook.get("yes_raw_top_level")
                    or not suppression_orderbook.get("no_raw_top_level")
                )
            ):
                try:
                    hydrated_details, hydrate_source = self._hydrate_targeted_zero_book_from_market_detail(
                        market,
                        suppression_snapshot.get("spread"),
                    )
                    if hydrated_details is None:
                        hydration_result = f"no_data:{hydrate_source}"
                    else:
                        hydration_result = f"hydrated:{hydrate_source}"
                        suppression_snapshot = self.get_strict_validation_snapshot(market, suppression_q)
                        suppression_orderbook = suppression_snapshot.get("orderbook", {})
                        yes_top_size = suppression_orderbook.get("yes_top_size")
                        no_top_size = suppression_orderbook.get("no_top_size")
                        if (yes_top_size or 0.0) == 0.0 and (no_top_size or 0.0) == 0.0:
                            hydration_result = f"hydrated_still_zero:{hydrate_source}"
                except Exception as exc:
                    hydration_result = f"error:{exc.__class__.__name__}"
            if (yes_top_size or 0.0) > 0.0 or (no_top_size or 0.0) > 0.0:
                family_stats["positive_top_size_candidates"] += 1
                continue
            dead_book_reason = None
            if candidate_path in ("broad_path", "adaptive_probe_path"):
                if yes_top_size is None and no_top_size is None:
                    dead_book_reason = "dead_book_missing_sizes"
                    family_stats["missing_size_rejects"] += 1
                elif (yes_top_size or 0.0) == 0.0 and (no_top_size or 0.0) == 0.0:
                    dead_book_reason = "dead_book_zero_zero"
                    family_stats["zero_book_rejects"] += 1
            elif candidate_path == "targeted_series_path":
                if (
                    (yes_top_size or 0.0) == 0.0
                    and (no_top_size or 0.0) == 0.0
                    and hydration_result is not None
                    and not str(hydration_result).startswith("hydrated:")
                ):
                    dead_book_reason = "dead_book_targeted_zero_after_hydration"
                    family_stats["zero_book_rejects"] += 1
            if dead_book_reason is not None:
                family_stats["dead_book_rejects"] += 1
        suppressed_families = sorted(
            family
            for family, stats in family_suppression_stats.items()
            if stats.get("candidate_count", 0) > 0
            and stats.get("candidate_count", 0) == stats.get("dead_book_rejects", 0)
            and stats.get("positive_top_size_candidates", 0) <= 0
        )
        refined_suppressed_families = []
        for family in suppressed_families:
            stats = family_suppression_stats.get(family, {})
            prefilter_survivors = prefilter_survivor_family_counts.get(family, 0)
            if prefilter_survivors > 0:
                family_suppression_skipped_by_family[family] = prefilter_survivors
                print(
                    f"[LIVE-SELECT-FAMILY-SUPPRESS] family={family} cycle_id={family_suppression_cycle_id} "
                    f"suppress_reason=cycle_dead_book_family action=skipped_due_to_prefilter_survivor dead_book_rejects_in_cycle={stats.get('dead_book_rejects', 0)} "
                    f"missing_size_rejects_in_cycle={stats.get('missing_size_rejects', 0)} "
                    f"zero_book_rejects_in_cycle={stats.get('zero_book_rejects', 0)} "
                    f"positive_top_size_candidates_in_cycle={stats.get('positive_top_size_candidates', 0)}"
                )
                continue
            refined_suppressed_families.append(family)
            print(
                f"[LIVE-SELECT-FAMILY-SUPPRESS] family={family} cycle_id={family_suppression_cycle_id} "
                f"suppress_reason=cycle_dead_book_family action=applied dead_book_rejects_in_cycle={stats.get('dead_book_rejects', 0)} "
                f"missing_size_rejects_in_cycle={stats.get('missing_size_rejects', 0)} "
                f"zero_book_rejects_in_cycle={stats.get('zero_book_rejects', 0)} "
                f"positive_top_size_candidates_in_cycle={stats.get('positive_top_size_candidates', 0)}"
            )
        suppressed_families = refined_suppressed_families
        if suppressed_families:
            for family in suppressed_families:
                dropped_count = strict_pool_initial_family_counts.get(family, 0)
                if dropped_count > 0:
                    print(
                        f"[LIVE-SELECT-STRICT-DROP] family={family} count={dropped_count} "
                        f"reason=cycle_dead_book_family_suppressed gate=family_suppression"
                    )
            rebuilt_strict_pool = []
            seen_rebuilt_tickers = set()
            for prescore, market in list(strict_pool) + list(strict_pool_overflow):
                ticker = str(market.get("ticker", "") or "").strip()
                family = prescore.get("family")
                if family in suppressed_families:
                    continue
                if ticker and ticker in seen_rebuilt_tickers:
                    continue
                if ticker:
                    seen_rebuilt_tickers.add(ticker)
                rebuilt_strict_pool.append((prescore, market))
                if len(rebuilt_strict_pool) >= strict_pool_target_count:
                    break
            strict_pool = rebuilt_strict_pool
        strict_pool_after_family_suppression_count = len(strict_pool)
        strict_pool_removed_by_family_suppression = max(0, strict_pool_initial_count - strict_pool_after_family_suppression_count)
        print(
            f"[LIVE-SELECT-STRICT-POOL-ASSEMBLY] strict_pool_size={strict_pool_after_family_suppression_count} "
            f"families_represented={sorted({prescore.get('family') for prescore, _market in strict_pool if prescore.get('family')})} "
            f"all_prefilter_survivors_removed_before_pool={'yes' if prefilter_survivors_total > 0 and strict_pool_after_family_suppression_count == 0 else 'no'}"
        )
        surviving_family_counts = {}
        for prescore, _market in strict_pool:
            family = prescore.get("family")
            if not family:
                continue
            surviving_family_counts[family] = surviving_family_counts.get(family, 0) + 1
        rejected_by_family_counts = sorted(
            (
                (family, stats.get("dead_book_rejects", 0))
                for family, stats in family_suppression_stats.items()
                if stats.get("dead_book_rejects", 0) > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        all_suppressed_families = sorted(set(suppressed_families).union(set(missing_quote_suppressed_families)))
        print(
            f"[LIVE-SELECT-FAMILY-SUPPRESS-SUMMARY] suppressed_families={all_suppressed_families} "
            f"suppressed_count={len(all_suppressed_families)} rejected_by_family={rejected_by_family_counts} "
            f"missing_quote_suppressed_families={missing_quote_suppressed_families} "
            f"skipped_due_to_prefilter_survivor={sorted(family_suppression_skipped_by_family.items(), key=lambda item: item[1], reverse=True)} "
            f"skipped_by_family={sorted(family_suppression_skipped_by_family.items(), key=lambda item: item[1], reverse=True)} "
            f"surviving_family_counts={sorted(surviving_family_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        strict_pool_tickers = {
            str(market.get("ticker", "") or "").strip()
            for _prescore, market in strict_pool
            if str(market.get("ticker", "") or "").strip()
        }
        prefilter_handoff_summary = {
            "prefilter_survivor_count": len(prefilter_pool),
            "entered_strict_pool": 0,
            "excluded_after_prefilter": 0,
        }
        prefilter_handoff_reason_counts = {}
        prefilter_handoff_family_counts = {}
        prefilter_handoff_family_reason_counts = {}
        for index, (prescore, market) in enumerate(prefilter_pool):
            ticker = str(market.get("ticker", "") or "").strip()
            family = prescore.get("family") or self.get_market_family(market)
            candidate_path = "targeted_series_path" if self.is_targeted_series_market(market) else (
                "broad_path" if family in broad_origin_families else "unknown_path"
            )
            included_in_strict_pool = ticker in strict_pool_tickers
            exclusion_reason = None
            if not included_in_strict_pool:
                if family in missing_quote_suppressed_families:
                    exclusion_reason = "cycle_missing_quote_family_suppressed"
                elif family in suppressed_families:
                    exclusion_reason = "cycle_dead_book_family_suppressed"
                elif index >= MAX_STRICT_VALIDATION_POOL:
                    exclusion_reason = "strict_pool_cap_not_backfilled"
                else:
                    exclusion_reason = "unknown_after_prefilter"
            try:
                handoff_q = self.build_quote(market)
                handoff_snapshot = self.get_strict_validation_snapshot(market, handoff_q)
            except Exception:
                handoff_snapshot = {
                    "spread": None,
                    "yes_bid": None,
                    "yes_ask": None,
                    "no_bid": None,
                    "no_ask": None,
                    "orderbook": {"yes_top_size": None, "no_top_size": None},
                }
            if included_in_strict_pool:
                prefilter_handoff_summary["entered_strict_pool"] += 1
                stage_after_prefilter = "strict_pool"
            else:
                prefilter_handoff_summary["excluded_after_prefilter"] += 1
                prefilter_handoff_reason_counts[exclusion_reason] = prefilter_handoff_reason_counts.get(exclusion_reason, 0) + 1
                prefilter_handoff_family_counts[family] = prefilter_handoff_family_counts.get(family, 0) + 1
                prefilter_handoff_family_reason_counts.setdefault(family, {})[exclusion_reason] = (
                    prefilter_handoff_family_reason_counts.setdefault(family, {}).get(exclusion_reason, 0) + 1
                )
                if family in targeted_family_activity:
                    targeted_family_activity[family]["prefilter_to_strict_removed"] += 1
                    targeted_family_activity[family]["prefilter_to_strict_removed_reasons"][exclusion_reason] = (
                        targeted_family_activity[family]["prefilter_to_strict_removed_reasons"].get(exclusion_reason, 0) + 1
                    )
                stage_after_prefilter = "excluded_after_prefilter"
            print(
                f"[LIVE-SELECT-PREFILTER-HANDOFF] ticker={ticker} family={family} "
                f"origin={self._get_liquidity_trace_origin(market)} candidate_path={candidate_path} "
                f"selection_source={selection_merge_source} stage_after_prefilter={stage_after_prefilter} "
                f"included_in_strict_pool={'yes' if included_in_strict_pool else 'no'} exclusion_reason={exclusion_reason} "
                f"spread={handoff_snapshot.get('spread')} yes_bid={handoff_snapshot.get('yes_bid')} "
                f"yes_ask={handoff_snapshot.get('yes_ask')} no_bid={handoff_snapshot.get('no_bid')} "
                f"no_ask={handoff_snapshot.get('no_ask')} yes_top_size={(handoff_snapshot.get('orderbook') or {}).get('yes_top_size')} "
                f"no_top_size={(handoff_snapshot.get('orderbook') or {}).get('no_top_size')} "
                f"timing_bucket={self.get_event_time_bucket(prescore.get('minutes_to_event'))} "
                f"family_priority_score={prescore.get('score')}"
            )
        print(
            f"[LIVE-SELECT-PREFILTER-HANDOFF-SUMMARY] prefilter_survivor_count={prefilter_handoff_summary['prefilter_survivor_count']} "
            f"entered_strict_pool={prefilter_handoff_summary['entered_strict_pool']} "
            f"excluded_after_prefilter={prefilter_handoff_summary['excluded_after_prefilter']} "
            f"excluded_by_reason={sorted(prefilter_handoff_reason_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"excluded_by_family={sorted(prefilter_handoff_family_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        for family, reason_counts in sorted(prefilter_handoff_family_reason_counts.items()):
            print(
                f"[LIVE-SELECT-PREFILTER-HANDOFF-FAMILY] family={family} prefilter_survivors={prefilter_survivor_family_counts.get(family, 0)} "
                f"entered_strict_pool={prefilter_survivor_family_counts.get(family, 0) - prefilter_handoff_family_counts.get(family, 0)} "
                f"excluded_after_prefilter={prefilter_handoff_family_counts.get(family, 0)} "
                f"excluded_by_reason={sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)}"
            )
        prestrict_reject_summary = {
            "total_removed": 0,
            "removed_zero_zero": 0,
            "removed_missing_sizes": 0,
            "removed_targeted_zero_after_hydration": 0,
        }
        prestrict_reject_by_family = {}
        filtered_strict_pool = []
        for prescore, market in strict_pool:
            family = prescore["family"]
            is_family_probe = bool(prescore.get("adaptive_probe"))
            candidate_path = "targeted_series_path" if self.is_targeted_series_market(market) else (
                "adaptive_probe_path" if is_family_probe else (
                    "broad_path" if family in broad_origin_families else "unknown_path"
                )
            )
            try:
                prestrict_q = self.build_quote(market)
                prestrict_snapshot = self.get_strict_validation_snapshot(market, prestrict_q)
            except Exception:
                filtered_strict_pool.append((prescore, market))
                continue
            orderbook_snapshot = prestrict_snapshot.get("orderbook", {})
            yes_top_size = orderbook_snapshot.get("yes_top_size")
            no_top_size = orderbook_snapshot.get("no_top_size")
            previous_orderbook_source = orderbook_snapshot.get("orderbook_source")
            hydrated_orderbook_source = previous_orderbook_source
            hydration_result = None
            if (
                candidate_path == "targeted_series_path"
                and (yes_top_size or 0.0) == 0.0
                and (no_top_size or 0.0) == 0.0
                and (
                    orderbook_snapshot.get("orderbook_source") == "root"
                    or not orderbook_snapshot.get("yes_raw_top_level")
                    or not orderbook_snapshot.get("no_raw_top_level")
                )
            ):
                try:
                    hydrated_details, hydrate_source = self._hydrate_targeted_zero_book_from_market_detail(
                        market,
                        prestrict_snapshot.get("spread"),
                    )
                    if hydrated_details is None:
                        hydration_result = f"no_data:{hydrate_source}"
                    else:
                        hydration_result = f"hydrated:{hydrate_source}"
                        prestrict_snapshot = self.get_strict_validation_snapshot(market, prestrict_q)
                        orderbook_snapshot = prestrict_snapshot.get("orderbook", {})
                        yes_top_size = orderbook_snapshot.get("yes_top_size")
                        no_top_size = orderbook_snapshot.get("no_top_size")
                        hydrated_orderbook_source = orderbook_snapshot.get("orderbook_source")
                        if (yes_top_size or 0.0) == 0.0 and (no_top_size or 0.0) == 0.0:
                            hydration_result = f"hydrated_still_zero:{hydrate_source}"
                except Exception as exc:
                    hydration_result = f"error:{exc.__class__.__name__}"
            reject_reason = None
            if candidate_path in ("broad_path", "adaptive_probe_path"):
                if yes_top_size is None and no_top_size is None:
                    reject_reason = "dead_book_missing_sizes"
                    prestrict_reject_summary["removed_missing_sizes"] += 1
                elif (yes_top_size or 0.0) == 0.0 and (no_top_size or 0.0) == 0.0:
                    reject_reason = "dead_book_zero_zero"
                    prestrict_reject_summary["removed_zero_zero"] += 1
            elif candidate_path == "targeted_series_path":
                if (
                    (yes_top_size or 0.0) == 0.0
                    and (no_top_size or 0.0) == 0.0
                    and hydration_result is not None
                    and not str(hydration_result).startswith("hydrated:")
                ):
                    reject_reason = "dead_book_targeted_zero_after_hydration"
                    prestrict_reject_summary["removed_targeted_zero_after_hydration"] += 1
            if reject_reason is None:
                filtered_strict_pool.append((prescore, market))
                continue
            prestrict_reject_summary["total_removed"] += 1
            prestrict_reject_by_family[family] = prestrict_reject_by_family.get(family, 0) + 1
            print(
                f"[LIVE-SELECT-STRICT-DROP] family={family} count=1 reason={reject_reason} gate=prestrict_filter"
            )
            print(
                f"[LIVE-SELECT-PRESTRICT-REJECT] ticker={str(market.get('ticker', '') or '').strip()} family={family} "
                f"origin={self._get_liquidity_trace_origin(market)} candidate_path={candidate_path} "
                f"yes_top_size={yes_top_size} no_top_size={no_top_size} "
                f"previous_orderbook_source={previous_orderbook_source} hydrated_orderbook_source={hydrated_orderbook_source} "
                f"hydration_result={hydration_result} spread={prestrict_snapshot.get('spread')} "
                f"reason={reject_reason}"
            )
        strict_pool = filtered_strict_pool
        strict_pool_removed_by_prestrict = max(0, strict_pool_after_family_suppression_count - len(strict_pool))
        print(
            f"[LIVE-SELECT-PRESTRICT-REJECT-SUMMARY] total_removed={prestrict_reject_summary['total_removed']} "
            f"removed_zero_zero={prestrict_reject_summary['removed_zero_zero']} "
            f"removed_missing_sizes={prestrict_reject_summary['removed_missing_sizes']} "
            f"removed_targeted_zero_after_hydration={prestrict_reject_summary['removed_targeted_zero_after_hydration']} "
            f"removed_by_family={sorted(prestrict_reject_by_family.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-COUNT] stage=strict_validation before={len(strict_pool)} after=pending filtered=pending"
        )
        zero_book_hydrate_summary = {
            "attempted": 0,
            "hydrated_success": 0,
            "hydrated_still_zero": 0,
            "hydrated_no_data": 0,
            "hydrated_errors": 0,
        }
        zero_book_hydrate_by_family = {}
        strict_timing_buckets = {}
        strict_liquidity_presence_counts = {
            "both_sizes_missing": 0,
            "only_yes_top_size_missing": 0,
            "only_no_top_size_missing": 0,
            "both_sizes_present": 0,
        }
        prestrict_liquidity_summary = {
            "total_strict_pool_candidates": len(strict_pool),
            "both_sizes_zero": 0,
            "both_sizes_missing": 0,
            "both_sizes_positive": 0,
            "mixed_other": 0,
        }
        prestrict_zero_zero_family_counts = {}
        zero_zero_candidate_path_counts = {}
        zero_zero_selection_source_counts = {}
        zero_zero_orderbook_source_counts = {}
        self.strict_validation_liquidity_trace_tickers = set()
        strict_candidate_summary = {}
        strict_entry_priority_counts = {}
        for prescore, market in strict_pool:
            family = prescore.get("family") or self.get_market_family(market)
            if self._should_trace_priority_topbook_family(family):
                strict_entry_priority_counts[family] = strict_entry_priority_counts.get(family, 0) + 1
                family_summary = strict_candidate_summary.setdefault(family, {"entered": 0, "passed": 0, "rejected": 0, "reasons": {}})
                family_summary["entered"] += 1
                liquidity_priority = prescore.get("liquidity_priority", {}) or {}
                self._emit_priority_downstream_trace(
                    "[LIVE-SELECT-STRICT-ENTRY]",
                    market,
                    family=family,
                    pass_fail="enter",
                    spread=round((prescore.get("yes_ask", 0.0) or 0.0) - (prescore.get("yes_bid", 0.0) or 0.0), 4),
                    quote_alive='yes' if liquidity_priority.get("both_sides_quoted") else 'no',
                    topbook=liquidity_priority.get("top_book_state"),
                    yes_top_size=liquidity_priority.get("yes_top_size"),
                    no_top_size=liquidity_priority.get("no_top_size"),
                    min_top_size=self.get_market_min_top_of_book_size(market),
                    removed_by="pending_strict_validation",
                )
                self._emit_priority_downstream_trace(
                    "[LIVE-SELECT-STRICT-CANDIDATE-ENTRY]",
                    market,
                    family=family,
                    pass_fail="enter",
                    spread=round((prescore.get("yes_ask", 0.0) or 0.0) - (prescore.get("yes_bid", 0.0) or 0.0), 4),
                    quote_alive='yes' if liquidity_priority.get("both_sides_quoted") else 'no',
                    non_empty_top_book=liquidity_priority.get("top_book_state"),
                    yes_top_size=liquidity_priority.get("yes_top_size"),
                    no_top_size=liquidity_priority.get("no_top_size"),
                    min_top_size=self.get_market_min_top_of_book_size(market),
                )
        self._emit_priority_downstream_trace(
            "[LIVE-SELECT-STRICT-ENTRY]",
            family="KXBTC15M",
            ticker="summary",
            summary="yes",
            counts=sorted(strict_entry_priority_counts.items()),
        )
        for prescore, market in strict_pool:
            family = prescore["family"]
            is_family_probe = bool(prescore.get("adaptive_probe"))
            bucket = self.get_event_time_bucket(prescore["minutes_to_event"])
            strict_timing_buckets[bucket] = strict_timing_buckets.get(bucket, 0) + 1
            strict_entry_family_counts[family] = strict_entry_family_counts.get(family, 0) + 1
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            runtime_stats["strict_validation_entrants"] = runtime_stats.get("strict_validation_entrants", 0) + 1
            ticker = str(market.get("ticker", "") or "").strip()
            self.strict_validation_liquidity_trace_tickers.add(ticker)
            try:
                (allowed, reason), q = self.strict_entry_validate(market)
            except Exception:
                strict_rejects[("parser", family)] = strict_rejects.get(("parser", family), 0) + 1
                strict_reject_reason_counts["parser"] = strict_reject_reason_counts.get("parser", 0) + 1
                if len(sample_skips) < 5:
                    sample_skips.append((ticker, "parser"))
                print(
                    f"[LIVE-SELECT-VALIDATION] ticker={ticker} family={family} decision=reject "
                    f"spread=None yes_ask=None no_bid=None yes_top_size=None no_top_size=None "
                    f"timing_score=None reject_reason=parser"
                )
                if self._should_trace_priority_topbook_family(family):
                    family_summary = strict_candidate_summary.setdefault(family, {"entered": 0, "passed": 0, "rejected": 0, "reasons": {}})
                    family_summary["rejected"] += 1
                    family_summary["reasons"]["parser"] = family_summary["reasons"].get("parser", 0) + 1
                    self._emit_priority_downstream_trace(
                        "[LIVE-SELECT-STRICT-CANDIDATE-REJECT]",
                        market,
                        family=family,
                        pass_fail="reject",
                        reject_reason="parser",
                        spread="None",
                        quote_alive="unknown",
                        non_empty_top_book="unknown",
                    )
                    self._emit_priority_downstream_trace(
                        "[LIVE-SELECT-STRICT-CANDIDATE-EXIT]",
                        market,
                        family=family,
                        pass_fail="reject",
                        reject_reason="parser",
                        removed_by="strict_validation",
                    )
                if is_family_probe:
                    family_probe_failed_count += 1
                    print(
                        f"[LIVE-SELECT-ADAPTIVE] family_probe_strict_result family={family} ticker={ticker} passed=no reason=parser"
                    )
                continue
            validation_snapshot = self.get_strict_validation_snapshot(market, q)
            orderbook_snapshot = validation_snapshot.get("orderbook", {})
            candidate_path = "targeted_series_path" if self.is_targeted_series_market(market) else (
                "broad_path" if family in broad_origin_families else "unknown_path"
            )
            if (
                candidate_path == "targeted_series_path"
                and (orderbook_snapshot.get("yes_top_size") or 0.0) == 0.0
                and (orderbook_snapshot.get("no_top_size") or 0.0) == 0.0
                and (
                    orderbook_snapshot.get("orderbook_source") == "root"
                    or not orderbook_snapshot.get("yes_raw_top_level")
                    or not orderbook_snapshot.get("no_raw_top_level")
                )
            ):
                zero_book_hydrate_summary["attempted"] += 1
                previous_yes_top_size = orderbook_snapshot.get("yes_top_size")
                previous_no_top_size = orderbook_snapshot.get("no_top_size")
                previous_orderbook_source = orderbook_snapshot.get("orderbook_source")
                try:
                    hydrated_details, hydrate_source = self._hydrate_targeted_zero_book_from_market_detail(market, validation_snapshot.get("spread"))
                    if hydrated_details is None:
                        zero_book_hydrate_summary["hydrated_no_data"] += 1
                        print(
                            f"[LIVE-SELECT-ZEROBOOK-HYDRATE] ticker={ticker} family={family} candidate_path={candidate_path} "
                            f"previous_yes_top_size={previous_yes_top_size} previous_no_top_size={previous_no_top_size} "
                            f"hydrated_yes_top_size={previous_yes_top_size} hydrated_no_top_size={previous_no_top_size} "
                            f"previous_orderbook_source={previous_orderbook_source} hydrated_orderbook_source={previous_orderbook_source} "
                            f"result=no_data reason={hydrate_source}"
                        )
                    else:
                        validation_snapshot = self.get_strict_validation_snapshot(market, q)
                        orderbook_snapshot = validation_snapshot.get("orderbook", {})
                        hydrated_yes_top_size = orderbook_snapshot.get("yes_top_size")
                        hydrated_no_top_size = orderbook_snapshot.get("no_top_size")
                        hydrated_orderbook_source = orderbook_snapshot.get("orderbook_source")
                        if (hydrated_yes_top_size or 0.0) > 0.0 or (hydrated_no_top_size or 0.0) > 0.0:
                            zero_book_hydrate_summary["hydrated_success"] += 1
                            zero_book_hydrate_by_family[family] = zero_book_hydrate_by_family.get(family, 0) + 1
                            hydrate_result = "hydrated"
                        else:
                            zero_book_hydrate_summary["hydrated_still_zero"] += 1
                            hydrate_result = "hydrated"
                        print(
                            f"[LIVE-SELECT-ZEROBOOK-HYDRATE] ticker={ticker} family={family} candidate_path={candidate_path} "
                            f"previous_yes_top_size={previous_yes_top_size} previous_no_top_size={previous_no_top_size} "
                            f"hydrated_yes_top_size={hydrated_yes_top_size} hydrated_no_top_size={hydrated_no_top_size} "
                            f"previous_orderbook_source={previous_orderbook_source} hydrated_orderbook_source={hydrated_orderbook_source} "
                            f"result={hydrate_result} reason={hydrate_source}"
                        )
                except Exception as exc:
                    zero_book_hydrate_summary["hydrated_errors"] += 1
                    print(
                        f"[LIVE-SELECT-ZEROBOOK-HYDRATE] ticker={ticker} family={family} candidate_path={candidate_path} "
                        f"previous_yes_top_size={previous_yes_top_size} previous_no_top_size={previous_no_top_size} "
                        f"hydrated_yes_top_size={previous_yes_top_size} hydrated_no_top_size={previous_no_top_size} "
                        f"previous_orderbook_source={previous_orderbook_source} hydrated_orderbook_source={previous_orderbook_source} "
                        f"result=error reason={exc.__class__.__name__}"
                    )
            yes_top_snapshot = orderbook_snapshot.get("yes_top_size")
            no_top_snapshot = orderbook_snapshot.get("no_top_size")
            yes_top_missing = yes_top_snapshot is None
            no_top_missing = no_top_snapshot is None
            if yes_top_missing and no_top_missing:
                strict_liquidity_presence_counts["both_sizes_missing"] += 1
            elif yes_top_missing:
                strict_liquidity_presence_counts["only_yes_top_size_missing"] += 1
            elif no_top_missing:
                strict_liquidity_presence_counts["only_no_top_size_missing"] += 1
            else:
                strict_liquidity_presence_counts["both_sizes_present"] += 1
            if yes_top_missing and no_top_missing:
                prestrict_liquidity_summary["both_sizes_missing"] += 1
                prestrict_bucket = "both_sizes_missing"
            else:
                yes_top_value = yes_top_snapshot or 0.0
                no_top_value = no_top_snapshot or 0.0
                if yes_top_value == 0.0 and no_top_value == 0.0:
                    prestrict_liquidity_summary["both_sizes_zero"] += 1
                    prestrict_zero_zero_family_counts[family] = prestrict_zero_zero_family_counts.get(family, 0) + 1
                    prestrict_bucket = "both_sizes_zero"
                elif yes_top_value > 0.0 and no_top_value > 0.0:
                    prestrict_liquidity_summary["both_sizes_positive"] += 1
                    prestrict_bucket = "both_sizes_positive"
                else:
                    prestrict_liquidity_summary["mixed_other"] += 1
                    prestrict_bucket = "mixed_other"
            if prestrict_bucket == "both_sizes_zero":
                zero_zero_candidate_path_counts[candidate_path] = zero_zero_candidate_path_counts.get(candidate_path, 0) + 1
                zero_zero_selection_source_counts[selection_merge_source] = zero_zero_selection_source_counts.get(selection_merge_source, 0) + 1
                orderbook_source_key = orderbook_snapshot.get("orderbook_source") or "none"
                zero_zero_orderbook_source_counts[orderbook_source_key] = zero_zero_orderbook_source_counts.get(orderbook_source_key, 0) + 1
                print(
                    f"[LIVE-SELECT-ZEROBOOK-TRACE] ticker={ticker} family={family} "
                    f"origin={self._get_liquidity_trace_origin(market)} candidate_path={candidate_path} "
                    f"selection_source={selection_merge_source} targeted={'yes' if self.is_targeted_series_market(market) else 'no'} "
                    f"adaptive_probe={'yes' if is_family_probe else 'no'} family_priority_score={prescore.get('score')} "
                    f"timing_bucket={bucket} yes_bid={validation_snapshot.get('yes_bid')} yes_ask={validation_snapshot.get('yes_ask')} "
                    f"no_bid={validation_snapshot.get('no_bid')} no_ask={validation_snapshot.get('no_ask')} "
                    f"yes_top_size={yes_top_snapshot} no_top_size={no_top_snapshot} "
                    f"yes_top_price={orderbook_snapshot.get('yes_top_price')} no_top_price={orderbook_snapshot.get('no_top_price')} "
                    f"spread={validation_snapshot.get('spread')} top_book_bucket={orderbook_snapshot.get('top_book_bucket')} "
                    f"orderbook_source={orderbook_snapshot.get('orderbook_source')} raw_yes_top_level={orderbook_snapshot.get('yes_raw_top_level')} "
                    f"raw_no_top_level={orderbook_snapshot.get('no_raw_top_level')} candidate_raw_fields={self._get_raw_liquidity_candidate_fields(market)} "
                    f"quote_age={market.get('quote_age') if isinstance(market, dict) else None} quote_ts={market.get('quote_ts') if isinstance(market, dict) else None} "
                    f"quote_timestamp={market.get('quote_timestamp') if isinstance(market, dict) else None} updated_at={market.get('updated_at') if isinstance(market, dict) else None} "
                    f"updated_at_ts={market.get('updated_at_ts') if isinstance(market, dict) else None} last_updated={market.get('last_updated') if isinstance(market, dict) else None} "
                    f"last_updated_ts={market.get('last_updated_ts') if isinstance(market, dict) else None} timestamp={market.get('timestamp') if isinstance(market, dict) else None} "
                    f"ts={market.get('ts') if isinstance(market, dict) else None} age_seconds={market.get('age_seconds') if isinstance(market, dict) else None} "
                    f"staleness_seconds={market.get('staleness_seconds') if isinstance(market, dict) else None}"
                )
            print(
                f"[LIVE-SELECT-PRESTRICT-LIQUIDITY] ticker={ticker} family={family} "
                f"origin={self._get_liquidity_trace_origin(market)} targeted={'yes' if self.is_targeted_series_market(market) else 'no'} "
                f"stage=pre_strict_validation selection_source={selection_merge_source} candidate_path={candidate_path} "
                f"yes_bid={validation_snapshot.get('yes_bid')} yes_ask={validation_snapshot.get('yes_ask')} "
                f"no_bid={validation_snapshot.get('no_bid')} no_ask={validation_snapshot.get('no_ask')} "
                f"yes_top_size={yes_top_snapshot} no_top_size={no_top_snapshot} "
                f"yes_top_price={orderbook_snapshot.get('yes_top_price')} no_top_price={orderbook_snapshot.get('no_top_price')} "
                f"spread={validation_snapshot.get('spread')} timing_bucket={bucket} prestrict_bucket={prestrict_bucket} "
                f"family_priority_score={prescore.get('score')} adaptive_probe={'yes' if is_family_probe else 'no'} "
                f"adaptive_probe_reason={prescore.get('adaptive_probe_reason')}"
            )
            if not allowed:
                if self.is_targeted_series_market(market):
                    ticker = getattr(q, "ticker", "") or str(market.get("ticker", "") or "").strip()
                    series_ticker = self.get_targeted_series_ticker(market)
                    min_liquidity = self.get_market_min_entry_liquidity(market)
                    seen_liquidity = getattr(q, "liquidity", 0.0) or 0.0
                    spread = (getattr(q, "yes_ask", 0.0) or 0.0) - (getattr(q, "yes_bid", 0.0) or 0.0)
                    print(f"[LIVE-SELECT-DEBUG] targeted_candidate_ticker={ticker}")
                    print(f"[LIVE-SELECT-DEBUG] targeted_series_ticker={series_ticker}")
                    print(f"[LIVE-SELECT-DEBUG] targeted_liquidity_threshold_used={min_liquidity}")
                    print(f"[LIVE-SELECT-DEBUG] targeted_market_liquidity_seen={seen_liquidity}")
                    print(f"[LIVE-SELECT-DEBUG] targeted_spread_seen={spread}")
                    print(
                        f"[LIVE-SELECT-DEBUG] targeted_liquidity_gate="
                        f"{'PASS' if seen_liquidity > min_liquidity else 'FAIL'}"
                    )
                    print(f"[LIVE-SELECT-DEBUG] final_targeted_reject_reason={reason}")
                strict_rejects[(reason, family)] = strict_rejects.get((reason, family), 0) + 1
                strict_reject_reason_counts[reason] = strict_reject_reason_counts.get(reason, 0) + 1
                if len(sample_skips) < 5:
                    sample_skips.append((ticker, reason))
                if reason == "low_liquidity":
                    self._emit_strict_liquidity_trace(market, q, family, validation_snapshot, orderbook_snapshot, reason)
                print(
                    f"[LIVE-SELECT-VALIDATION] ticker={ticker} family={family} decision=reject "
                    f"spread={validation_snapshot.get('spread')} yes_ask={validation_snapshot.get('yes_ask')} "
                    f"no_bid={validation_snapshot.get('no_bid')} yes_top_size={orderbook_snapshot.get('yes_top_size')} "
                    f"no_top_size={orderbook_snapshot.get('no_top_size')} timing_score={validation_snapshot.get('timing_score')} "
                    f"reject_reason={reason}"
                )
                self._emit_priority_downstream_trace(
                    "[LIVE-SELECT-STRICT-CANDIDATE-REJECT]",
                    market,
                    family=family,
                    pass_fail="reject",
                    reject_reason=reason,
                    spread=validation_snapshot.get('spread'),
                    quote_alive='yes' if (orderbook_snapshot.get('yes_top_price') is not None and orderbook_snapshot.get('no_top_price') is not None) else 'no',
                    both_sides_quoted='yes' if (orderbook_snapshot.get('yes_top_price') is not None and orderbook_snapshot.get('no_top_price') is not None) else 'no',
                    non_empty_top_book=orderbook_snapshot.get('top_book_bucket'),
                    yes_top_size=orderbook_snapshot.get('yes_top_size'),
                    no_top_size=orderbook_snapshot.get('no_top_size'),
                    min_top_size=orderbook_snapshot.get('min_top_size'),
                )
                self._emit_priority_downstream_trace(
                    "[LIVE-SELECT-STRICT-EXIT]",
                    market,
                    family=family,
                    pass_fail="reject",
                    reject_reason=reason,
                    spread=validation_snapshot.get('spread'),
                    quote_alive='yes' if (orderbook_snapshot.get('yes_top_price') is not None and orderbook_snapshot.get('no_top_price') is not None) else 'no',
                    topbook=orderbook_snapshot.get('top_book_bucket'),
                    yes_top_size=orderbook_snapshot.get('yes_top_size'),
                    no_top_size=orderbook_snapshot.get('no_top_size'),
                    min_top_size=orderbook_snapshot.get('min_top_size'),
                    removed_by="strict_validation",
                )
                if self._should_trace_priority_topbook_family(family):
                    family_summary = strict_candidate_summary.setdefault(family, {"entered": 0, "passed": 0, "rejected": 0, "reasons": {}})
                    family_summary["rejected"] += 1
                    family_summary["reasons"][reason] = family_summary["reasons"].get(reason, 0) + 1
                    self._emit_priority_downstream_trace(
                        "[LIVE-SELECT-STRICT-CANDIDATE-EXIT]",
                        market,
                        family=family,
                        pass_fail="reject",
                        reject_reason=reason,
                        removed_by="strict_validation",
                    )
                if is_family_probe:
                    family_probe_failed_count += 1
                    print(
                        f"[LIVE-SELECT-ADAPTIVE] family_probe_strict_result family={family} ticker={ticker} passed=no reason={reason}"
                    )
                    if reason == "low_liquidity":
                        yes_top_size = orderbook_snapshot.get('yes_top_size', 0.0) or 0.0
                        no_top_size = orderbook_snapshot.get('no_top_size', 0.0) or 0.0
                        both_sides_quoted = bool(
                            orderbook_snapshot.get('yes_top_price') is not None
                            and orderbook_snapshot.get('no_top_price') is not None
                        )
                        if yes_top_size > 0 and no_top_size > 0:
                            strict_top_book_state = "both_non_zero"
                            family_probe_strict_both_sides_top_book_count += 1
                        elif yes_top_size > 0 or no_top_size > 0:
                            strict_top_book_state = "one_non_zero"
                            family_probe_strict_one_sided_top_book_count += 1
                        else:
                            strict_top_book_state = "zero_top_book"
                            family_probe_strict_zero_top_book_count += 1
                        family_probe_strict_liquidity_failures += 1
                        print(
                            f"[LIVE-SELECT-ADAPTIVE] family_probe_strict_liquidity_fail family={family} ticker={ticker} "
                            f"yes_top_size={yes_top_size} no_top_size={no_top_size} both_sides_quoted={both_sides_quoted} "
                            f"spread={validation_snapshot.get('spread')} recent_activity={validation_snapshot.get('volume', 0.0) > 0} "
                            f"liquidity={validation_snapshot.get('liquidity')} threshold={self.get_market_min_entry_liquidity(market)} "
                            f"top_book_state={strict_top_book_state}"
                        )
                print(f"[LIVE-SELECT-FILTER] ticker={ticker} reason={reason}")
                continue
            score = self._score_live_ticker_candidate(q)
            spread = q.yes_ask - q.yes_bid
            print(
                f"[LIVE-SELECT-VALIDATION] ticker={ticker} family={family} decision=pass "
                f"spread={validation_snapshot.get('spread')} yes_ask={validation_snapshot.get('yes_ask')} "
                f"no_bid={validation_snapshot.get('no_bid')} yes_top_size={orderbook_snapshot.get('yes_top_size')} "
                f"no_top_size={orderbook_snapshot.get('no_top_size')} timing_score={validation_snapshot.get('timing_score')} "
                f"reject_reason=None"
            )
            self._emit_priority_downstream_trace(
                "[LIVE-SELECT-STRICT-EXIT]",
                market,
                family=family,
                pass_fail="pass",
                reject_reason="none",
                spread=validation_snapshot.get('spread'),
                quote_alive='yes' if (orderbook_snapshot.get('yes_top_price') is not None and orderbook_snapshot.get('no_top_price') is not None) else 'no',
                topbook=orderbook_snapshot.get('top_book_bucket'),
                yes_top_size=orderbook_snapshot.get('yes_top_size'),
                no_top_size=orderbook_snapshot.get('no_top_size'),
                min_top_size=orderbook_snapshot.get('min_top_size'),
                removed_by="none",
            )
            if self._should_trace_priority_topbook_family(family):
                family_summary = strict_candidate_summary.setdefault(family, {"entered": 0, "passed": 0, "rejected": 0, "reasons": {}})
                family_summary["passed"] += 1
                self._emit_priority_downstream_trace(
                    "[LIVE-SELECT-STRICT-CANDIDATE-EXIT]",
                    market,
                    family=family,
                    pass_fail="pass",
                    reject_reason="none",
                    spread=validation_snapshot.get('spread'),
                    both_sides_quoted='yes' if (orderbook_snapshot.get('yes_top_price') is not None and orderbook_snapshot.get('no_top_price') is not None) else 'no',
                    non_empty_top_book=orderbook_snapshot.get('top_book_bucket'),
                    yes_top_size=orderbook_snapshot.get('yes_top_size'),
                    no_top_size=orderbook_snapshot.get('no_top_size'),
                    min_top_size=orderbook_snapshot.get('min_top_size'),
                    removed_by="none",
                )
            print(
                f"[LIVE-SELECT-SCORE] ticker={ticker} score={score} spread={spread} "
                f"volume={q.volume} liquidity={q.liquidity} price={q.yes_ask}"
            )
            if is_family_probe:
                family_probe_passed_count += 1
                print(
                    f"[LIVE-SELECT-ADAPTIVE] family_probe_strict_result family={family} ticker={ticker} passed=yes reason=None"
                )
                continue
            final_family_counts[family] = final_family_counts.get(family, 0) + 1
            runtime_stats["final_candidates"] = runtime_stats.get("final_candidates", 0) + 1
            candidates.append((score, q, spread, family))

        self.strict_validation_liquidity_trace_tickers = set()
        print(
            f"[LIVE-SELECT-PRESTRICT-LIQUIDITY-SUMMARY] total_strict_pool_candidates={prestrict_liquidity_summary['total_strict_pool_candidates']} "
            f"both_sizes_zero={prestrict_liquidity_summary['both_sizes_zero']} "
            f"both_sizes_missing={prestrict_liquidity_summary['both_sizes_missing']} "
            f"both_sizes_positive={prestrict_liquidity_summary['both_sizes_positive']} "
            f"mixed_other={prestrict_liquidity_summary['mixed_other']} "
            f"zero_zero_by_family={sorted(prestrict_zero_zero_family_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-ZEROBOOK-SUMMARY] total_zero_zero={prestrict_liquidity_summary['both_sizes_zero']} "
            f"zero_zero_by_family={sorted(prestrict_zero_zero_family_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"zero_zero_by_candidate_path={sorted(zero_zero_candidate_path_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"zero_zero_by_selection_source={sorted(zero_zero_selection_source_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"zero_zero_by_orderbook_source={sorted(zero_zero_orderbook_source_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-ZEROBOOK-HYDRATE-SUMMARY] attempted={zero_book_hydrate_summary['attempted']} "
            f"hydrated_success={zero_book_hydrate_summary['hydrated_success']} "
            f"hydrated_still_zero={zero_book_hydrate_summary['hydrated_still_zero']} "
            f"hydrated_no_data={zero_book_hydrate_summary['hydrated_no_data']} "
            f"hydrated_errors={zero_book_hydrate_summary['hydrated_errors']} "
            f"hydrated_by_family={sorted(zero_book_hydrate_by_family.items(), key=lambda item: item[1], reverse=True)}"
        )
        print(
            f"[LIVE-SELECT-LIQUIDITY-SUMMARY] strict_validation_candidates={len(strict_pool)} "
            f"both_sizes_missing={strict_liquidity_presence_counts['both_sizes_missing']} "
            f"only_yes_top_size_missing={strict_liquidity_presence_counts['only_yes_top_size_missing']} "
            f"only_no_top_size_missing={strict_liquidity_presence_counts['only_no_top_size_missing']} "
            f"both_sizes_present={strict_liquidity_presence_counts['both_sizes_present']}"
        )
        print(
            f"[LIVE-SELECT-STRICT-HANDOFF] prefilter_survivors_total={prefilter_survivors_total} "
            f"targeted_prefilter_survivors={targeted_prefilter_survivors_total_handoff} "
            f"broad_prefilter_survivors={broad_prefilter_survivors_total_handoff} "
            f"candidates_entering_strict_validation={len(strict_pool)} "
            f"source_breakdown={{'strict_pool_initial': {strict_pool_initial_count}, 'strict_pool_post_family_suppression': {strict_pool_after_family_suppression_count}, 'strict_pool_post_prestrict': {len(strict_pool)}}} "
            f"removed_between={'yes' if strict_pool_initial_count != len(strict_pool) else 'no'} "
            f"removed_by_family_suppression={strict_pool_removed_by_family_suppression} "
            f"removed_by_prestrict={strict_pool_removed_by_prestrict}"
        )
        print(f"[LIVE-SELECT-DEBUG] entering_strict_validation={len(strict_pool)} source=strict_pool_post_prestrict")
        strict_handoff_family_union = sorted(
            set(family for family, count in prefilter_survivor_family_counts.items() if count > 0)
            .union(set(strict_entry_family_counts.keys()))
            .union(set(final_family_counts.keys()))
        )
        for family in strict_handoff_family_union:
            prefilter_survivors = prefilter_survivor_family_counts.get(family, 0)
            entering_count = strict_entry_family_counts.get(family, 0)
            surviving_count = final_family_counts.get(family, 0)
            final_candidates_count = final_family_counts.get(family, 0)
            print(
                f"[LIVE-SELECT-STRICT-HANDOFF-FAMILY] family={family} prefilter_survivors={prefilter_survivors} "
                f"entering_strict_validation={entering_count} surviving_strict_validation={surviving_count} "
                f"final_candidates={final_candidates_count}"
            )
        print(f"[LIVE-SELECT-SCAN] total_examined={len(markets)} total_candidates={len(candidates)}")
        print(
            f"[LIVE-SELECT-DEBUG] broad_candidates_after_strict_validation={sum(final_family_counts.get(family, 0) for family in broad_origin_families)}"
        )
        print(f"[LIVE-SELECT-DEBUG] surviving_strict_validation={len(candidates)}")
        print(
            f"[LIVE-SELECT-COUNT] stage=strict_validation before={len(strict_pool)} "
            f"after={len(candidates)} filtered={len(strict_pool) - len(candidates)}"
        )
        print(
            f"[LIVE-SELECT-VALIDATION-SUMMARY] total_evaluated={len(strict_pool)} "
            f"passed={len(candidates)} failed={len(strict_pool) - len(candidates)} "
            f"failed_by_reason={sorted(strict_reject_reason_counts.items(), key=lambda item: item[1], reverse=True)}"
        )
        strict_reject_summary_rows = []
        for family, stats in sorted(strict_candidate_summary.items()):
            strict_reject_summary_rows.append((family, stats.get('entered', 0), stats.get('passed', 0), stats.get('rejected', 0), sorted(stats.get('reasons', {}).items(), key=lambda item: item[1], reverse=True)))
        self._emit_priority_downstream_trace(
            "[LIVE-SELECT-STRICT-REJECT-SUMMARY]",
            family="KXBTC15M",
            ticker="summary",
            summary="yes",
            families=strict_reject_summary_rows,
            top_reject_reasons=sorted(strict_reject_reason_counts.items(), key=lambda item: item[1], reverse=True),
        )
        print(
            f"[LIVE-SELECT-ADAPTIVE] family_probe_summary entered={family_probe_entered_count} "
            f"passed={family_probe_passed_count} failed={family_probe_failed_count} "
            f"families={sorted(family_probe_families)}"
        )
        print(
            f"[LIVE-SELECT-ADAPTIVE] family_probe_strict_liquidity_summary failures={family_probe_strict_liquidity_failures} "
            f"zero_top_book={family_probe_strict_zero_top_book_count} "
            f"one_sided_top_book={family_probe_strict_one_sided_top_book_count} "
            f"both_sides_top_book={family_probe_strict_both_sides_top_book_count}"
        )
        broad_prefilter_reason_counts = {}
        for (reason, family), count in prefilter_rejects.items():
            if family in broad_origin_families:
                broad_prefilter_reason_counts[reason] = broad_prefilter_reason_counts.get(reason, 0) + count
        broad_strict_reason_counts = {}
        for (reason, family), count in strict_rejects.items():
            if family in broad_origin_families:
                broad_strict_reason_counts[reason] = broad_strict_reason_counts.get(reason, 0) + count
        broad_post_prestrict_family_counts = {}
        for prescore, _market in strict_pool:
            family = prescore.get("family")
            if family in broad_origin_families:
                broad_post_prestrict_family_counts[family] = broad_post_prestrict_family_counts.get(family, 0) + 1
        broad_prestrict_family_rows = []
        broad_prestrict_top_survivors = []
        broad_prestrict_top_wide_spread = []
        broad_prestrict_top_low_liquidity = []
        for family in sorted(broad_origin_families):
            entered_downstream_prestrict = prefilter_survivor_family_counts.get(family, 0)
            rejected_wide_spread = prefilter_rejects.get(("wide_spread", family), 0)
            rejected_low_liquidity = prefilter_rejects.get(("low_liquidity", family), 0)
            survived_post_prestrict = broad_post_prestrict_family_counts.get(family, 0)
            broad_prestrict_family_rows.append(
                (family, entered_downstream_prestrict, rejected_wide_spread, rejected_low_liquidity, survived_post_prestrict)
            )
            print(
                f"[LIVE-SELECT-BROAD-PRESTRICT] family={family} entered_downstream_prestrict={entered_downstream_prestrict} "
                f"rejected_wide_spread={rejected_wide_spread} rejected_low_liquidity={rejected_low_liquidity} "
                f"survived_post_prestrict={survived_post_prestrict}"
            )
            if survived_post_prestrict > 0:
                broad_prestrict_top_survivors.append((family, survived_post_prestrict))
            if rejected_wide_spread > 0:
                broad_prestrict_top_wide_spread.append((family, rejected_wide_spread))
            if rejected_low_liquidity > 0:
                broad_prestrict_top_low_liquidity.append((family, rejected_low_liquidity))
        print(
            f"[LIVE-SELECT-BROAD-PRESTRICT-SUMMARY] top_survivors={sorted(broad_prestrict_top_survivors, key=lambda item: item[1], reverse=True)[:10]} "
            f"top_wide_spread={sorted(broad_prestrict_top_wide_spread, key=lambda item: item[1], reverse=True)[:10]} "
            f"top_low_liquidity={sorted(broad_prestrict_top_low_liquidity, key=lambda item: item[1], reverse=True)[:10]}"
        )
        print(
            f"[LIVE-SELECT-DEBUG] broad_downstream_filter_summary "
            f"prefilter_reasons={sorted(broad_prefilter_reason_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"strict_reasons={sorted(broad_strict_reason_counts.items(), key=lambda item: item[1], reverse=True)} "
            f"prestrict_by_family={sorted(broad_prestrict_family_rows, key=lambda item: (item[4], -item[2], -item[3], item[0]), reverse=True)[:10]}"
        )
        print(f"[LIVE-SELECT-DEBUG] fetched_markets={len(markets)}")
        print(f"[LIVE-SELECT-DEBUG] fetched_timing_buckets={fetched_timing_buckets}")
        print(f"[LIVE-SELECT-DEBUG] prefilter_timing_buckets={prefilter_timing_buckets}")
        print(f"[LIVE-SELECT-DEBUG] strict_timing_buckets={strict_timing_buckets}")
        for family, count in sorted(fetched_family_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] fetched_family family={family} count={count}")
        for (reason, family), count in sorted(prefilter_rejects.items()):
            print(f"[LIVE-SELECT-DEBUG] prefilter_reject reason={reason} family={family} count={count}")
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            if reason == "missing_quotes":
                runtime_stats["missing_quotes"] = runtime_stats.get("missing_quotes", 0) + count
            if reason == "wide_spread":
                runtime_stats["wide_spread"] = runtime_stats.get("wide_spread", 0) + count
            if reason == "low_liquidity":
                runtime_stats["low_liquidity"] = runtime_stats.get("low_liquidity", 0) + count
        for (reason, family), count in sorted(strict_rejects.items()):
            print(f"[LIVE-SELECT-DEBUG] strict_reject reason={reason} family={family} count={count}")
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            if reason == "missing_quotes":
                runtime_stats["missing_quotes"] = runtime_stats.get("missing_quotes", 0) + count
            if reason == "wide_spread":
                runtime_stats["wide_spread"] = runtime_stats.get("wide_spread", 0) + count
            if reason == "low_liquidity":
                runtime_stats["low_liquidity"] = runtime_stats.get("low_liquidity", 0) + count
        for skipped_ticker, reason in sample_skips:
            print(f"[LIVE-SELECT-DEBUG] sample_skip ticker={skipped_ticker} reason={reason}")
        for family, count in sorted(final_family_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] final_family family={family} count={count}")
        for family, count in sorted(strict_entry_family_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] strict_entry_family family={family} count={count}")
        for family, count in sorted(prefilter_survivor_family_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] prefilter_survivor_family family={family} count={count}")
        current_hour_utc = time.gmtime().tm_hour
        for family in sorted(set(list(family_observation_stats.keys()) + list(fetched_family_counts.keys()))):
            obs = family_observation_stats.get(family, {})
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            if obs:
                runtime_stats["session_scan_count"] = runtime_stats.get("session_scan_count", 0) + obs.get("scan_count", 0)
                runtime_stats["session_non_empty_top_book_count"] = runtime_stats.get("session_non_empty_top_book_count", 0) + obs.get("non_empty_top_book_count", 0)
                runtime_stats["session_both_sides_quoted_count"] = runtime_stats.get("session_both_sides_quoted_count", 0) + obs.get("both_sides_quoted_count", 0)
                runtime_stats["session_narrow_spread_count"] = runtime_stats.get("session_narrow_spread_count", 0) + obs.get("narrow_spread_count", 0)
                runtime_stats["session_in_window_count"] = runtime_stats.get("session_in_window_count", 0) + obs.get("in_window_count", 0)
            hourly_stats = self.live_family_hourly_observation_stats.setdefault(family, {}).setdefault(
                current_hour_utc,
                {
                    "scan_count": 0,
                    "in_window_count": 0,
                    "non_empty_top_book_count": 0,
                    "both_sides_quoted_count": 0,
                    "narrow_spread_count": 0,
                    "prefilter_survivors": 0,
                    "final_candidates": 0,
                },
            )
            hourly_stats["scan_count"] += obs.get("scan_count", 0)
            hourly_stats["in_window_count"] += obs.get("in_window_count", 0)
            hourly_stats["non_empty_top_book_count"] += obs.get("non_empty_top_book_count", 0)
            hourly_stats["both_sides_quoted_count"] += obs.get("both_sides_quoted_count", 0)
            hourly_stats["narrow_spread_count"] += obs.get("narrow_spread_count", 0)
            hourly_stats["prefilter_survivors"] += prefilter_survivor_family_counts.get(family, 0)
            hourly_stats["final_candidates"] += final_family_counts.get(family, 0)
            print(
                f"[LIVE-SELECT-OBSERVATION] family={family} scan_count={obs.get('scan_count', 0)} "
                f"in_window_count={obs.get('in_window_count', 0)} non_empty_top_book_count={obs.get('non_empty_top_book_count', 0)} "
                f"both_sides_quoted_count={obs.get('both_sides_quoted_count', 0)} narrow_spread_count={obs.get('narrow_spread_count', 0)} "
                f"prefilter_survivors={prefilter_survivor_family_counts.get(family, 0)} final_candidates={final_family_counts.get(family, 0)}"
            )
            if family in broad_origin_families:
                scan_count = obs.get('scan_count', 0)
                both_sides_quoted_count = obs.get('both_sides_quoted_count', 0)
                non_empty_top_book_count = obs.get('non_empty_top_book_count', 0)
                narrow_spread_count = obs.get('narrow_spread_count', 0)
                prefilter_survivors = prefilter_survivor_family_counts.get(family, 0)
                final_candidates = final_family_counts.get(family, 0)
                quoted_to_non_empty_rate = (
                    round(non_empty_top_book_count / float(both_sides_quoted_count), 4)
                    if both_sides_quoted_count > 0 else 0.0
                )
                scan_to_narrow_spread_rate = (
                    round(narrow_spread_count / float(scan_count), 4)
                    if scan_count > 0 else 0.0
                )
                prefilter_to_final_rate = (
                    round(final_candidates / float(prefilter_survivors), 4)
                    if prefilter_survivors > 0 else 0.0
                )
                print(
                    f"[LIVE-SELECT-FAMILY-QUALITY] family={family} scan_count={scan_count} "
                    f"both_sides_quoted_count={both_sides_quoted_count} non_empty_top_book_count={non_empty_top_book_count} "
                    f"narrow_spread_count={narrow_spread_count} prefilter_survivors={prefilter_survivors} "
                    f"final_candidates={final_candidates} quoted_to_non_empty_rate={quoted_to_non_empty_rate} "
                    f"scan_to_narrow_spread_rate={scan_to_narrow_spread_rate} prefilter_to_final_rate={prefilter_to_final_rate}"
                )
            print(
                f"[LIVE-SELECT-OBSERVATION-HOURLY] family={family} hour_utc={current_hour_utc} "
                f"scan_count={hourly_stats.get('scan_count', 0)} in_window_count={hourly_stats.get('in_window_count', 0)} "
                f"non_empty_top_book_count={hourly_stats.get('non_empty_top_book_count', 0)} "
                f"both_sides_quoted_count={hourly_stats.get('both_sides_quoted_count', 0)} "
                f"narrow_spread_count={hourly_stats.get('narrow_spread_count', 0)} "
                f"prefilter_survivors={hourly_stats.get('prefilter_survivors', 0)} final_candidates={hourly_stats.get('final_candidates', 0)}"
            )
        for family, count in sorted(family_skip_counts.items()):
            print(f"[LIVE-SELECT-DEBUG] skipped_family family={family} count={count}")
        for family, count in sorted(fetched_family_counts.items()):
            runtime_stats = self.live_family_runtime_stats.setdefault(family, {})
            runtime_stats["fetched"] = runtime_stats.get("fetched", 0) + count
            runtime_stats["too_early"] = runtime_stats.get("too_early", 0) + family_diagnostics.get(family, {}).get("too_early", 0)
        for family, scanned_count in sorted(family_scanned_counts.items()):
            prefilter_total = prefilter_survivor_family_counts.get(family, 0)
            reject_total = sum(
                count for (reason, reject_family), count in prefilter_rejects.items()
                if reject_family == family
            )
            unclassified = scanned_count - prefilter_total - reject_total
            if scanned_count != prefilter_total + reject_total:
                print(
                    f"[LIVE-SELECT-WARN] family={family} stage=prefilter "
                    f"scanned={scanned_count} rejects={reject_total} survivors={prefilter_total} "
                    f"unclassified={unclassified}"
                )
        if len(strict_pool) > sum(prefilter_survivor_family_counts.values()):
            print(
                f"[LIVE-SELECT-WARN] stage=strict_pool "
                f"entering={len(strict_pool)} survivors={sum(prefilter_survivor_family_counts.values())}"
            )
        if len(candidates) > len(strict_pool):
            print(
                f"[LIVE-SELECT-WARN] stage=survivors entering={len(strict_pool)} surviving={len(candidates)}"
            )
        top_fetched = sorted(fetched_family_counts.items(), key=lambda item: item[1], reverse=True)[:5]
        top_wide_spread = sorted(
            ((family, stats["wide_spread"]) for family, stats in family_diagnostics.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        top_missing_quotes = sorted(
            ((family, stats["missing_quotes"]) for family, stats in family_diagnostics.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        top_survivors = sorted(
            prefilter_survivor_family_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        top_low_liquidity = sorted(
            ((family, stats["low_liquidity"]) for family, stats in family_diagnostics.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        top_too_early = sorted(
            ((family, stats["too_early"]) for family, stats in family_diagnostics.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        print(f"[LIVE-SELECT-SUMMARY] top_fetched={top_fetched}")
        print(f"[LIVE-SELECT-SUMMARY] top_wide_spread={top_wide_spread}")
        print(f"[LIVE-SELECT-SUMMARY] top_missing_quotes={top_missing_quotes}")
        print(f"[LIVE-SELECT-SUMMARY] top_low_liquidity={top_low_liquidity}")
        print(f"[LIVE-SELECT-SUMMARY] top_too_early={top_too_early}")
        print(f"[LIVE-SELECT-SUMMARY] top_prefilter_survivors={top_survivors}")
        print(
            f"[LIVE-SELECT-SUMMARY] entering_strict_validation={len(strict_pool)} "
            f"surviving_strict_validation={len(candidates)} candidates_found={len(ranked := self.rank_final_candidates(candidates))}"
        )
        session_tradability = []
        for family, runtime_stats in sorted(self.live_family_runtime_stats.items()):
            tradability_score = (
                float(runtime_stats.get("session_non_empty_top_book_count", 0)) * 3.0
                + float(runtime_stats.get("session_both_sides_quoted_count", 0)) * 2.0
                + float(runtime_stats.get("session_narrow_spread_count", 0)) * 2.0
                + float(runtime_stats.get("prefilter_survivors", 0)) * 4.0
                + float(runtime_stats.get("final_candidates", 0)) * 6.0
            )
            session_tradability.append((round(tradability_score, 4), family))
        time_bucket_tradability = []
        for family, hourly_map in sorted(self.live_family_hourly_observation_stats.items()):
            for hour_utc, hourly_stats in sorted(hourly_map.items()):
                tradability_score = (
                    float(hourly_stats.get("non_empty_top_book_count", 0)) * 3.0
                    + float(hourly_stats.get("both_sides_quoted_count", 0)) * 2.0
                    + float(hourly_stats.get("narrow_spread_count", 0)) * 2.0
                    + float(hourly_stats.get("prefilter_survivors", 0)) * 4.0
                    + float(hourly_stats.get("final_candidates", 0)) * 6.0
                )
                time_bucket_tradability.append((round(tradability_score, 4), family, hour_utc))
        print(
            f"[LIVE-SELECT-OBSERVATION-SUMMARY] top_families={sorted(session_tradability, reverse=True)[:10]}"
        )
        print(
            f"[LIVE-SELECT-OBSERVATION-SUMMARY] top_time_buckets={sorted(time_bucket_tradability, reverse=True)[:10]}"
        )
        targeted_cycle_order = list(self.get_prioritized_targeted_series())
        targeted_cycle_stats = [
            (
                family,
                targeted_family_activity.get(family, {}).get("fetched", 0),
                targeted_family_activity.get(family, {}).get("in_window", 0),
                targeted_family_activity.get(family, {}).get("empty_top_book_fail", 0),
                targeted_family_activity.get(family, {}).get("prefilter_survivors", 0),
            )
            for family in targeted_cycle_order
        ]
        targeted_prefilter_survivors_total = sum(
            stats.get("prefilter_survivors", 0) for stats in targeted_family_activity.values()
        )
        targeted_empty_top_book_waste_families = [
            family
            for family in targeted_cycle_order
            if targeted_family_activity.get(family, {}).get("in_window", 0) > 0
            and targeted_family_activity.get(family, {}).get("empty_top_book_fail", 0) > 0
            and targeted_family_activity.get(family, {}).get("prefilter_survivors", 0) <= 0
        ]
        targeted_empty_top_book_waste_slots = sum(
            targeted_family_activity.get(family, {}).get("empty_top_book_fail", 0)
            for family in targeted_empty_top_book_waste_families
        )
        broad_prefilter_survivor_families = sorted(
            (
                (family, prefilter_survivor_family_counts.get(family, 0))
                for family in broad_origin_families
                if prefilter_survivor_family_counts.get(family, 0) > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        broad_prefilter_survivors_total = sum(count for _family, count in broad_prefilter_survivor_families)
        print(
            f"[LIVE-SELECT-TARGETED-ORDER] hour_utc={current_hour_utc} ordered={targeted_cycle_order} "
            f"family_stats={targeted_cycle_stats}"
        )
        print(
            f"[LIVE-SELECT-TARGETED-WASTE] hour_utc={current_hour_utc} "
            f"targeted_prefilter_survivors={targeted_prefilter_survivors_total} "
            f"wasted_empty_top_book_families={len(targeted_empty_top_book_waste_families)} "
            f"wasted_empty_top_book_slots={targeted_empty_top_book_waste_slots} "
            f"wasted_family_list={targeted_empty_top_book_waste_families}"
        )
        targeted_non_empty_top_book_total = sum(
            stats.get("non_empty_top_book", 0) for stats in targeted_family_activity.values()
        )
        targeted_priority_adaptation = self.last_targeted_priority_adaptation or {}
        promoted_viable_broad_families = (
            fallback_to_broad_scan
            and broad_prefilter_survivors_total > 0
            and (
                targeted_non_empty_top_book_total <= 0
                or targeted_prefilter_survivors_total <= 0
                or bool(targeted_priority_adaptation.get("suppressed_families"))
            )
        )
        if targeted_non_empty_top_book_total <= 0:
            promotion_reason = "targeted_zero_non_empty_top_book"
        elif targeted_prefilter_survivors_total <= 0:
            promotion_reason = "targeted_zero_prefilter_survivors"
        elif targeted_priority_adaptation.get("suppressed_families"):
            promotion_reason = "targeted_dead_book_family_suppressed"
        else:
            promotion_reason = "none"
        print(
            f"[LIVE-SELECT-TARGETED-VS-BROAD] hour_utc={current_hour_utc} "
            f"fallback_to_broad={'yes' if fallback_to_broad_scan else 'no'} "
            f"targeted_prefilter_survivors={targeted_prefilter_survivors_total} "
            f"fallback_after_zero_targeted_survivors={'yes' if fallback_to_broad_scan and targeted_prefilter_survivors_total <= 0 else 'no'} "
            f"broad_prefilter_survivors={broad_prefilter_survivors_total} "
            f"broad_viable_when_targeted_dead={'yes' if targeted_prefilter_survivors_total <= 0 and broad_prefilter_survivors_total > 0 else 'no'} "
            f"broad_survivor_families={broad_prefilter_survivor_families} "
            f"selection_source={selection_merge_source}"
        )
        effective_cycle_suppressed_families = sorted(
            set(targeted_priority_adaptation.get('suppressed_families', [])).union(
                set(cycle_targeted_dead_book_suppressed_families)
            )
        )
        effective_adapted_order = [
            family for family in targeted_priority_adaptation.get('adapted_order', targeted_cycle_order)
            if family not in set(cycle_targeted_dead_book_suppressed_families)
        ]
        print(
            f"[LIVE-SELECT-TARGETED-ADAPT] hour_utc={current_hour_utc} "
            f"suppressed_dead_book_families={effective_cycle_suppressed_families} "
            f"deprioritized_dead_book_families={targeted_priority_adaptation.get('deprioritized_families', [])} "
            f"adapted_order={effective_adapted_order} "
            f"promoted_viable_broad_families={'yes' if promoted_viable_broad_families else 'no'} "
            f"promotion_reason={promotion_reason} broad_survivor_families={broad_prefilter_survivor_families} "
            f"selection_source={selection_merge_source}"
        )
        for family, stats in targeted_family_activity.items():
            print(
                f"[LIVE-SELECT-TARGETED] family={family} fetched={stats['fetched']} "
                f"in_window={stats['in_window']} non_empty_top_book={stats['non_empty_top_book']} "
                f"both_sides_quoted={stats['both_sides_quoted']} "
                f"min_top_size_met={stats['min_top_size_met']} "
                f"prefilter_survivors={stats['prefilter_survivors']}"
            )
            print(
                f"[LIVE-SELECT-TARGETED-FUNNEL] family={family} in_window={stats['in_window']} "
                f"empty_top_book_fail={stats['empty_top_book_fail']} "
                f"one_sided_quote_fail={stats['one_sided_quote_fail']} "
                f"min_top_size_fail={stats['min_top_size_fail']} "
                f"prefilter_survivors={stats['prefilter_survivors']} "
                f"removed_before_strict={stats['prefilter_to_strict_removed']} "
                f"removed_before_strict_reasons={sorted(stats['prefilter_to_strict_removed_reasons'].items(), key=lambda item: item[1], reverse=True)}"
            )
            hour_stats = self.targeted_family_hourly_stats.setdefault(family, {}).setdefault(
                time.gmtime().tm_hour,
                {
                    "cycles": 0,
                    "in_window_cycles": 0,
                    "non_empty_top_book_cycles": 0,
                    "quote_alive_cycles": 0,
                    "prefilter_survivor_cycles": 0,
                    "dead_book_cycles": 0,
                },
            )
            hour_stats["cycles"] += 1
            if stats["in_window"] > 0:
                hour_stats["in_window_cycles"] += 1
            if stats["non_empty_top_book"] > 0:
                hour_stats["non_empty_top_book_cycles"] += 1
            if (
                stats["non_empty_top_book"] > 0
                or stats["both_sides_quoted"] > 0
                or stats["min_top_size_met"] > 0
            ):
                hour_stats["quote_alive_cycles"] += 1
            if stats["prefilter_survivors"] > 0:
                hour_stats["prefilter_survivor_cycles"] += 1
            if (
                family == "KXBTC15M"
                and stats["in_window"] > 0
                and stats["non_empty_top_book"] == 0
                and stats["both_sides_quoted"] == 0
                and stats["min_top_size_met"] == 0
                and stats["prefilter_survivors"] == 0
            ):
                hour_stats["dead_book_cycles"] += 1
        prioritized_quote_alive = [
            family
            for family in self.get_prioritized_targeted_series()
            if targeted_family_activity.get(family, {}).get("non_empty_top_book", 0) > 0
            or targeted_family_activity.get(family, {}).get("both_sides_quoted", 0) > 0
            or targeted_family_activity.get(family, {}).get("prefilter_survivors", 0) > 0
        ]
        print(f"[LIVE-SELECT-DEBUG] prioritized_quote_alive_families={prioritized_quote_alive}")
        targeted_viable_family_counts = {}
        for _score, q, _spread, family in ranked:
            market = next((item for item in markets if str(item.get("ticker", "") or "").strip() == q.ticker), None)
            if market and self.is_targeted_series_market(market):
                targeted_viable_family_counts[family] = targeted_viable_family_counts.get(family, 0) + 1
        print(f"[LIVE-SELECT-DEBUG] targeted_viable_families={sorted(targeted_viable_family_counts.items())}")
        family_candidate_counts = {}
        for _score, _q, _spread, family in ranked:
            family_candidate_counts[family] = family_candidate_counts.get(family, 0) + 1
        print(f"[LIVE-SELECT-DEBUG] family_candidate_counts={family_candidate_counts}")
        for score, q, _spread, family in ranked[:5]:
            print(
                f"[LIVE-SELECT-DEBUG] candidate ticker={q.ticker} family={family} "
                f"yes_bid={q.yes_bid:.4f} yes_ask={q.yes_ask:.4f} volume={q.volume}"
            )

        low_liquidity_reject_count = (
            sum(
                count for (reason, _family), count in prefilter_rejects.items()
                if reason == "low_liquidity"
            )
            + sum(
                count for (reason, _family), count in strict_rejects.items()
                if reason == "low_liquidity"
            )
        )
        missing_quotes_reject_count = (
            sum(
                count for (reason, _family), count in prefilter_rejects.items()
                if reason == "missing_quotes"
            )
            + sum(
                count for (reason, _family), count in strict_rejects.items()
                if reason == "missing_quotes"
            )
        )
        ranking_entry_priority_counts = {}
        for score, q, spread, family in candidates:
            if not self._should_trace_priority_topbook_family(family):
                continue
            ranking_entry_priority_counts[family] = ranking_entry_priority_counts.get(family, 0) + 1
            self._emit_priority_downstream_trace(
                "[LIVE-SELECT-RANKING-ENTRY]",
                {"ticker": q.ticker},
                family=family,
                pass_fail="enter",
                spread=round(spread, 4),
                quote_alive='yes' if (q.yes_bid is not None and q.yes_ask is not None and q.no_bid is not None and q.no_ask is not None) else 'no',
                removed_by="pending_ranking",
            )
        self._emit_priority_downstream_trace(
            "[LIVE-SELECT-RANKING-ENTRY]",
            family="KXBTC15M",
            ticker="summary",
            summary="yes",
            counts=sorted(ranking_entry_priority_counts.items()),
        )
        print(f"[LIVE-SELECT] candidates_found={len(ranked)}")
        print(
            f"[LIVE-SELECT-COUNT] stage=ranking before={len(candidates)} after={len(ranked)} filtered={len(candidates) - len(ranked)}"
        )
        for rank_position, (score, q, spread, family) in enumerate(ranked[:10], start=1):
            print(
                f"[LIVE-SELECT] candidate_rank={rank_position} ticker={q.ticker} family={family} score={score:.2f} "
                f"yes_bid={q.yes_bid:.4f} yes_ask={q.yes_ask:.4f} spread={spread:.4f} "
                f"last={q.last_price:.4f} volume={q.volume} liquidity={q.liquidity:.4f}"
            )
        handoff_candidates_after_strict_validation = len(candidates)
        handoff_candidates_after_decision_layer = len(ranked)
        handoff_candidates_presented_to_entry_timing = 0
        handoff_candidates_blocked_before_entry_timing = 0
        handoff_zero_stage = None
        handoff_block_records = []
        activation_candidates = []
        activation_denial_counts = {}
        decision_entry_priority_counts = {}
        for score, q, spread, family in ranked:
            if not self._should_trace_priority_topbook_family(family):
                continue
            decision_entry_priority_counts[family] = decision_entry_priority_counts.get(family, 0) + 1
            self._emit_priority_downstream_trace(
                "[LIVE-SELECT-DECISION-ENTRY]",
                {"ticker": q.ticker},
                family=family,
                pass_fail="enter",
                spread=round(spread, 4),
                quote_alive='yes' if (q.yes_bid is not None and q.yes_ask is not None and q.no_bid is not None and q.no_ask is not None) else 'no',
                removed_by="pending_decision_layer",
            )
        self._emit_priority_downstream_trace(
            "[LIVE-SELECT-DECISION-ENTRY]",
            family="KXBTC15M",
            ticker="summary",
            summary="yes",
            counts=sorted(decision_entry_priority_counts.items()),
        )
        for rank_position, ranked_candidate in enumerate(ranked, start=1):
            score, q, spread, family = ranked_candidate
            market = next((item for item in markets if str(item.get("ticker", "") or "").strip() == q.ticker), None)
            if market is None:
                activation_status = {
                    "armed": False,
                    "ticker": q.ticker,
                    "family": family,
                    "top_book_state": None,
                    "both_sides_quoted": False,
                    "narrow_spread": False,
                    "recent_activity": False,
                    "prefilter_survivors": prefilter_survivor_family_counts.get(family, 0),
                    "denied_reasons": ["market_lookup_failed"],
                }
            else:
                activation_status = self.get_controlled_activation_status(
                    market,
                    family,
                    prefilter_survivor_family_counts,
                )
            if activation_status["armed"]:
                activation_candidates.append(ranked_candidate)
            else:
                denied_reasons = activation_status.get("denied_reasons", []) or ["activation_not_armed"]
                handoff_candidates_blocked_before_entry_timing += 1
                for denied_reason in denied_reasons:
                    activation_denial_counts[denied_reason] = activation_denial_counts.get(denied_reason, 0) + 1
                    handoff_block_records.append((q.ticker, family, "activation_gate", denied_reason))
                    print(
                        f"[LIVE-ENTRY-TIMING-HANDOFF-BLOCK] ticker={q.ticker} family={family} "
                        f"stage_where_blocked=activation_gate explicit_reason={denied_reason}"
                    )
            print(
                f"[LIVE-SELECT-ACTIVATION] rank={rank_position} ticker={activation_status.get('ticker')} "
                f"family={family} armed_candidate={'yes' if activation_status.get('armed') else 'no'} "
                f"top_book_state={activation_status.get('top_book_state')} "
                f"both_sides_quoted={activation_status.get('both_sides_quoted')} "
                f"narrow_spread={activation_status.get('narrow_spread')} "
                f"recent_activity={activation_status.get('recent_activity')} "
                f"prefilter_survivors={activation_status.get('prefilter_survivors')} "
                f"denied_reasons={activation_status.get('denied_reasons', [])}"
            )
        if activation_candidates:
            ranked = activation_candidates
            handoff_candidates_presented_to_entry_timing = len(ranked)
            armed_score, armed_q, _armed_spread, armed_family = ranked[0]
            entry_timing_entry_priority_counts = {}
            for score, q, spread, family in ranked:
                if not self._should_trace_priority_topbook_family(family):
                    continue
                entry_timing_entry_priority_counts[family] = entry_timing_entry_priority_counts.get(family, 0) + 1
                self._emit_priority_downstream_trace(
                    "[LIVE-ENTRY-TIMING-ENTRY]",
                    {"ticker": q.ticker},
                    family=family,
                    pass_fail="enter",
                    spread=round(spread, 4),
                    removed_by="none",
                )
            self._emit_priority_downstream_trace(
                "[LIVE-ENTRY-TIMING-ENTRY]",
                family="KXBTC15M",
                ticker="summary",
                summary="yes",
                counts=sorted(entry_timing_entry_priority_counts.items()),
            )
            print(
                f"[LIVE-SELECT-ACTIVATION] armed=yes qualified_count={len(ranked)} "
                f"top_qualified_ticker={armed_q.ticker} top_qualified_family={armed_family} "
                f"top_qualified_score={armed_score:.4f} denied_count={sum(activation_denial_counts.values())}"
            )
        else:
            ranked = []
            print(
                f"[LIVE-SELECT-ACTIVATION] armed=no qualified_count=0 denied_reasons="
                f"{sorted(activation_denial_counts.items(), key=lambda item: item[1], reverse=True)}"
            )
        if handoff_candidates_after_strict_validation == 0:
            handoff_zero_stage = "zero_at_strict_validation"
        elif handoff_candidates_after_decision_layer == 0:
            handoff_zero_stage = "zero_at_decision_layer"
        elif handoff_candidates_presented_to_entry_timing == 0:
            handoff_zero_stage = "zero_before_entry_timing_dispatch"
        print(
            f"[LIVE-ENTRY-TIMING-HANDOFF-SUMMARY] candidates_after_strict_validation={handoff_candidates_after_strict_validation} "
            f"candidates_after_decision_layer={handoff_candidates_after_decision_layer} "
            f"candidates_presented_to_entry_timing={handoff_candidates_presented_to_entry_timing} "
            f"candidates_blocked_before_entry_timing={handoff_candidates_blocked_before_entry_timing} "
            f"zero_stage={handoff_zero_stage}"
        )
        for ticker, family, stage_where_blocked, explicit_reason in handoff_block_records[:20]:
            print(
                f"[LIVE-ENTRY-TIMING-HANDOFF] ticker={ticker} family={family} "
                f"stage_where_blocked={stage_where_blocked} explicit_reason={explicit_reason}"
            )
        decision_trace = []
        top_decision_skip_reasons = []
        entry_timing_cycle_stats = {
            "candidates_seen_by_entry_timing": 0,
            "stochastic_crossovers_detected": 0,
            "micro_pullbacks_detected": 0,
            "immediate_entry_blocks": 0,
            "entry_ready_count": 0,
            "insufficient_data_count": 0,
        }

        if not ranked:
            print(
                f"[LIVE-SELECT-FALLBACK-EARLY-EXIT] stage=ranking reason=no_ranked_candidates_pre_decision "
                f"selection_source={selection_merge_source} fallback_to_broad={'yes' if fallback_to_broad_scan else 'no'} "
                f"prefilter_survivors_total={sum(prefilter_survivor_family_counts.values())} strict_pool_size={len(strict_pool)}"
            )
            filter_breakdown = {}
            filter_breakdown["family_skip"] = family_skip_total
            for reject_reason, count in prefilter_rejects.items():
                filter_breakdown[f"prefilter:{reject_reason[0]}"] = filter_breakdown.get(f"prefilter:{reject_reason[0]}", 0) + count
            for reject_reason, count in strict_rejects.items():
                filter_breakdown[f"strict:{reject_reason[0]}"] = filter_breakdown.get(f"strict:{reject_reason[0]}", 0) + count
            for reject_reason, count in activation_denial_counts.items():
                filter_breakdown[f"activation:{reject_reason}"] = filter_breakdown.get(f"activation:{reject_reason}", 0) + count
            final_no_candidate_reason = "no_ranked_candidates"
            if filter_breakdown:
                final_no_candidate_reason = sorted(
                    filter_breakdown.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[0][0]
            print(f"[LIVE-SELECT-COUNT] stage=decision before=0 after=0 filtered=0")
            print("[LIVE-ENTRY-TIMING-SUMMARY] candidates_seen_by_entry_timing=0")
            print(
                f"[LIVE-SELECT-DEBUG] elimination_breakdown={sorted(filter_breakdown.items(), key=lambda item: item[1], reverse=True)}"
            )
            print(
                f"[LIVE-SELECT-DECISION] no_trade_path reason={final_no_candidate_reason} "
                f"top_skip_reasons={top_decision_skip_reasons} decision_trace={decision_trace}"
            )
            print(f"[LIVE-SELECT-DEBUG] final_no_candidate_reason={final_no_candidate_reason}")
            self.no_candidate_cycle_streak += 1
            self.last_live_select_summary = {
                "original_market_count": original_market_count,
                "within_window": len(time_window_markets),
                "families_in_window": sorted(in_window_family_counts.keys()),
                "skipped_too_early_markets": skipped_too_early_markets,
                "skipped_too_close_markets": skipped_too_close_markets,
                "skipped_no_timestamp_markets": skipped_no_timestamp_markets,
                "spread_skip_count": spread_skip_count,
                "low_liquidity_reject_count": low_liquidity_reject_count,
                "missing_quotes_reject_count": missing_quotes_reject_count,
                "candidates_found": 0,
            }
            print("[LIVE-SELECT] no_valid_candidates_after_filters")
            emit_phase5_observation_logs(0, final_no_candidate_reason)
            emit_full_select_cycle_timing()
            return None, None

        selected = None
        decision_buy_count = 0
        decision_skip_count = 0
        decision_skip_reasons = {}
        decision_trace = []
        for rank_position, (score, q, spread, family) in enumerate(ranked, start=1):
            market = next((item for item in markets if str(item.get("ticker", "") or "").strip() == q.ticker), None)
            score_result = self.score_final_candidate_decision(market, q, spread, family)
            score_breakdown = score_result.get("score_breakdown", {})
            debug_context = score_result.get("debug_context", {})
            candidate_score = score_result.get("candidate_score", 0.0)
            decision = score_result.get("decision", "SKIP")
            decision_reason = score_result.get("decision_reason", "low_score")
            entry_timing_observation = self.get_entry_timing_observation(
                market,
                q,
                family,
                stage_label="entry_timing",
            )
            trace_live_entry_timing = self._should_trace_priority_topbook_family(family)
            entry_timing_cycle_stats["candidates_seen_by_entry_timing"] += 1
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-DATA] ticker={q.ticker} family={family} "
                    f"history_points={entry_timing_observation.get('history_points')} "
                    f"stochastic_k={entry_timing_observation.get('stochastic_k')} "
                    f"stochastic_d={entry_timing_observation.get('stochastic_d')} "
                    f"data_available={'yes' if entry_timing_observation.get('stochastic_inputs_available') else 'no'} "
                    f"reject_reason={'insufficient_history' if not entry_timing_observation.get('stochastic_inputs_available') else 'None'}"
                )
            crossover_detected = bool(entry_timing_observation.get("bullish_crossover") or entry_timing_observation.get("bearish_crossover"))
            if crossover_detected:
                entry_timing_cycle_stats["stochastic_crossovers_detected"] += 1
                print(
                    f"[LIVE-ENTRY-TIMING-TRIGGER] ticker={q.ticker} family={family} "
                    f"bullish_crossover={entry_timing_observation.get('bullish_crossover')} "
                    f"bearish_crossover={entry_timing_observation.get('bearish_crossover')}"
                )
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-CROSSOVER] ticker={q.ticker} family={family} "
                    f"history_points={entry_timing_observation.get('history_points')} "
                    f"stochastic_k={entry_timing_observation.get('stochastic_k')} "
                    f"stochastic_d={entry_timing_observation.get('stochastic_d')} "
                    f"crossover_detected={'yes' if crossover_detected else 'no'} "
                    f"reject_reason={'no_stochastic_crossover' if entry_timing_observation.get('stochastic_inputs_available') and not crossover_detected else 'None'}"
                )
            if entry_timing_observation.get("micro_pullback_detected"):
                entry_timing_cycle_stats["micro_pullbacks_detected"] += 1
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-PULLBACK] ticker={q.ticker} family={family} "
                    f"history_points={entry_timing_observation.get('history_points')} "
                    f"crossover_detected={'yes' if crossover_detected else 'no'} "
                    f"micro_pullback_detected={'yes' if entry_timing_observation.get('micro_pullback_detected') else 'no'} "
                    f"reject_reason={'no_micro_pullback' if crossover_detected and not entry_timing_observation.get('micro_pullback_detected') else 'None'}"
                )
            if entry_timing_observation.get("immediate_entry_blocked"):
                entry_timing_cycle_stats["immediate_entry_blocks"] += 1
                print(
                    f"[LIVE-ENTRY-TIMING-REJECT] ticker={q.ticker} family={family} blocker=immediate_entry_protection"
                )
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-IMMEDIATE-BLOCK] ticker={q.ticker} family={family} "
                    f"immediate_entry_block={'yes' if entry_timing_observation.get('immediate_entry_blocked') else 'no'} "
                    f"reject_reason={'immediate_entry_protection' if entry_timing_observation.get('immediate_entry_blocked') else 'None'}"
                )
            if entry_timing_observation.get("phase55_status") == "entry_ready":
                entry_timing_cycle_stats["entry_ready_count"] += 1
            if entry_timing_observation.get("phase55_status") == "insufficient_data":
                entry_timing_cycle_stats["insufficient_data_count"] += 1
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-DECISION] ticker={q.ticker} family={family} "
                    f"history_points={entry_timing_observation.get('history_points')} "
                    f"stochastic_k={entry_timing_observation.get('stochastic_k')} stochastic_d={entry_timing_observation.get('stochastic_d')} "
                    f"crossover_detected={'yes' if crossover_detected else 'no'} "
                    f"micro_pullback_detected={'yes' if entry_timing_observation.get('micro_pullback_detected') else 'no'} "
                    f"immediate_entry_block={'yes' if entry_timing_observation.get('immediate_entry_blocked') else 'no'} "
                    f"pass={'yes' if entry_timing_observation.get('phase55_status') == 'entry_ready' else 'no'} "
                    f"reject_reason={entry_timing_observation.get('phase55_status')}"
                )
            print(
                f"[LIVE-ENTRY-TIMING] ticker={q.ticker} family={family} "
                f"current_price={entry_timing_observation.get('current_price')} "
                f"best_bid={entry_timing_observation.get('best_bid')} "
                f"last_price={entry_timing_observation.get('last_price')} "
                f"stochastic_inputs_available={entry_timing_observation.get('stochastic_inputs_available')} "
                f"stochastic_source={entry_timing_observation.get('stochastic_source')} "
                f"history_points={entry_timing_observation.get('history_points')} "
                f"history_reason={entry_timing_observation.get('history_reason')} "
                f"stochastic_k={entry_timing_observation.get('stochastic_k')} "
                f"stochastic_d={entry_timing_observation.get('stochastic_d')} "
                f"bullish_crossover={entry_timing_observation.get('bullish_crossover')} "
                f"bearish_crossover={entry_timing_observation.get('bearish_crossover')} "
                f"micro_pullback_detected={entry_timing_observation.get('micro_pullback_detected')} "
                f"immediate_entry_blocked={entry_timing_observation.get('immediate_entry_blocked')} "
                f"phase55_status={entry_timing_observation.get('phase55_status')}"
            )
            print(
                f"[SCORE] ticker={q.ticker} "
                f"liquidity={score_breakdown.get('liquidity', 0.0)} "
                f"spread={score_breakdown.get('spread', 0.0)} "
                f"structure={score_breakdown.get('structure', 0.0)} "
                f"timing={score_breakdown.get('timing', 0.0)} "
                f"confidence={score_breakdown.get('confidence', 0.0)} "
                f"total={candidate_score}"
            )
            print(
                f"[DECISION] ticker={q.ticker} decision={decision} "
                f"score={candidate_score} reason={decision_reason}"
            )
            print(
                f"[LIVE-SELECT-DECISION] rank={rank_position} ticker={q.ticker} family={family} "
                f"raw_rank_score={score:.4f} final_score={candidate_score:.4f} "
                f"buy_threshold={debug_context.get('buy_threshold', PHASE5_BUY_SCORE_THRESHOLD)} "
                f"price={debug_context.get('yes_ask', 0.0):.4f} spread={debug_context.get('spread', 0.0):.4f} "
                f"liquidity={debug_context.get('liquidity', 0.0)} min_liquidity={debug_context.get('min_liquidity')} "
                f"minutes_to_event={debug_context.get('minutes_to_event')} timing_bucket={debug_context.get('timing_bucket')} "
                f"scores={score_breakdown} decision={decision} reason={decision_reason} "
                f"hard_reject_reason={debug_context.get('hard_reject_reason')}"
            )
            if len(decision_trace) < 10:
                decision_trace.append({
                    "rank": rank_position,
                    "ticker": q.ticker,
                    "family": family,
                    "raw_rank_score": round(score, 4),
                    "final_score": candidate_score,
                    "decision": decision,
                    "reason": decision_reason,
                    "hard_reject_reason": debug_context.get("hard_reject_reason"),
                })
            if trace_live_entry_timing:
                print(
                    f"[LIVE-ENTRY-TIMING-LIVE-HANDOFF] ticker={q.ticker} family={family} "
                    f"phase55_status={entry_timing_observation.get('phase55_status')} decision={decision} "
                    f"pass={'yes' if decision == 'BUY' else 'no'} "
                    f"reject_reason={decision_reason if decision != 'BUY' else 'None'}"
                )
            if decision != "BUY":
                decision_skip_count += 1
                decision_skip_reasons[decision_reason] = decision_skip_reasons.get(decision_reason, 0) + 1
                continue
            decision_buy_count += 1
            selected = (score, q, spread, family)
            print(
                f"[LIVE-SELECT-DECISION] chosen rank={rank_position} ticker={q.ticker} family={family} "
                f"raw_rank_score={score:.4f} final_score={candidate_score:.4f}"
            )
            break
        top_decision_skip_reasons = sorted(
            decision_skip_reasons.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        spread_skip_count = (
            sum(
                count for (reason, _family), count in prefilter_rejects.items()
                if reason == "wide_spread"
            )
            + sum(
                count for (reason, _family), count in strict_rejects.items()
                if reason == "wide_spread"
            )
            + decision_skip_reasons.get("wide_spread", 0)
        )
        print(
            f"[LIVE-SELECT-SUMMARY] decision_candidates_considered={decision_buy_count + decision_skip_count} "
            f"buy_count={decision_buy_count} skip_count={decision_skip_count} "
            f"top_skip_reasons={top_decision_skip_reasons}"
        )
        if entry_timing_cycle_stats['candidates_seen_by_entry_timing'] == 0:
            print("[LIVE-ENTRY-TIMING-SUMMARY] candidates_seen_by_entry_timing=0")
        else:
            print(
                f"[LIVE-ENTRY-TIMING-SUMMARY] candidates_seen_by_entry_timing={entry_timing_cycle_stats['candidates_seen_by_entry_timing']} "
                f"stochastic_crossovers_detected={entry_timing_cycle_stats['stochastic_crossovers_detected']} "
                f"micro_pullbacks_detected={entry_timing_cycle_stats['micro_pullbacks_detected']} "
                f"immediate_entry_blocks={entry_timing_cycle_stats['immediate_entry_blocks']} "
                f"entry_ready_count={entry_timing_cycle_stats['entry_ready_count']} "
                f"insufficient_data_count={entry_timing_cycle_stats['insufficient_data_count']}"
            )
        print(f"[LIVE-SELECT-DECISION] top_decision_trace={decision_trace}")
        print(
            f"[LIVE-SELECT-COUNT] stage=decision before={len(ranked)} after={1 if selected else 0} "
            f"filtered={len(ranked) - (1 if selected else 0)}"
        )
        print(
            f"[LIVE-SELECT-SUMMARY] spread_threshold_used={MAX_ENTRY_SPREAD} "
            f"spread_skip_count={spread_skip_count}"
        )
        if not selected:
            filter_breakdown = {}
            filter_breakdown["family_skip"] = family_skip_total
            for reject_reason, count in prefilter_rejects.items():
                filter_breakdown[f"prefilter:{reject_reason[0]}"] = filter_breakdown.get(f"prefilter:{reject_reason[0]}", 0) + count
            for reject_reason, count in strict_rejects.items():
                filter_breakdown[f"strict:{reject_reason[0]}"] = filter_breakdown.get(f"strict:{reject_reason[0]}", 0) + count
            for reject_reason, count in decision_skip_reasons.items():
                filter_breakdown[f"decision:{reject_reason}"] = filter_breakdown.get(f"decision:{reject_reason}", 0) + count
            final_no_candidate_reason = "no_buy_decision"
            if filter_breakdown:
                final_no_candidate_reason = sorted(
                    filter_breakdown.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[0][0]
            print(
                f"[LIVE-SELECT-DEBUG] elimination_breakdown={sorted(filter_breakdown.items(), key=lambda item: item[1], reverse=True)}"
            )
            print(f"[LIVE-SELECT-DEBUG] final_no_candidate_reason={final_no_candidate_reason}")
            self.no_candidate_cycle_streak += 1
            self.last_live_select_summary = {
                "original_market_count": original_market_count,
                "within_window": len(time_window_markets),
                "families_in_window": sorted(in_window_family_counts.keys()),
                "skipped_too_early_markets": skipped_too_early_markets,
                "skipped_too_close_markets": skipped_too_close_markets,
                "skipped_no_timestamp_markets": skipped_no_timestamp_markets,
                "spread_skip_count": spread_skip_count,
                "low_liquidity_reject_count": low_liquidity_reject_count,
                "missing_quotes_reject_count": missing_quotes_reject_count,
                "candidates_found": 0,
            }
            print("[LIVE-SELECT] no_valid_candidates_after_filters")
            emit_phase5_observation_logs(0, final_no_candidate_reason)
            emit_full_select_cycle_timing()
            return None, None

        self.no_candidate_cycle_streak = 0
        score, q, spread, family = selected
        self.last_live_select_summary = {
            "original_market_count": original_market_count,
            "within_window": len(time_window_markets),
            "families_in_window": sorted(in_window_family_counts.keys()),
            "skipped_too_early_markets": skipped_too_early_markets,
            "skipped_too_close_markets": skipped_too_close_markets,
            "skipped_no_timestamp_markets": skipped_no_timestamp_markets,
            "spread_skip_count": spread_skip_count,
            "low_liquidity_reject_count": low_liquidity_reject_count,
            "missing_quotes_reject_count": missing_quotes_reject_count,
            "candidates_found": len(ranked),
        }
        reason = (
            f"best filtered candidate family={family} spread={spread:.4f}, "
            f"volume={q.volume}, liquidity={q.liquidity}, price={q.yes_ask}"
        )
        print(
            f"[LIVE-SELECT-DECISION] final_trade_path chosen_ticker={q.ticker} family={family} "
            f"raw_rank_score={score:.4f} spread={spread:.4f} volume={q.volume} liquidity={q.liquidity}"
        )
        print(f"[LIVE-SELECT] chosen_ticker={q.ticker}")
        print(f"[LIVE-SELECT] reason={reason}")
        emit_phase5_observation_logs(len(ranked), "selected_candidate")
        emit_full_select_cycle_timing()
        return q.ticker, reason

    def find_best_live_ticker_with_retries(self, markets=None):
        previous_within_window = None
        previous_families_in_window = None
        previous_candidates_found = None
        max_attempts = max(1, MAX_DISCOVERY_RETRIES + 1)
        retry_delay = DISCOVERY_RETRY_DELAY_SECONDS
        for attempt in range(1, max_attempts + 1):
            ticker, reason = self.find_best_live_ticker(markets=markets)
            summary = self.last_live_select_summary or {}
            within_window = summary.get("within_window")
            families_in_window = summary.get("families_in_window") or []
            candidates_found = summary.get("candidates_found", 0)
            if ticker:
                print(
                    f"[LIVE-SELECT] candidate_found retry={attempt - 1} "
                    f"within_window={within_window}"
                )
                return ticker, reason
            if attempt >= max_attempts:
                break
            within_window_changed = previous_within_window != within_window
            families_changed = previous_families_in_window != families_in_window
            market_quality = (
                "improved"
                if previous_candidates_found is not None and candidates_found > previous_candidates_found
                else "changed"
                if within_window_changed or families_changed
                else "flat"
            )
            print(
                f"[LIVE-SELECT] no_candidates_retrying retry={attempt}/{MAX_DISCOVERY_RETRIES} "
                f"wait_seconds={retry_delay} "
                f"within_window={within_window} previous_within_window={previous_within_window} "
                f"within_window_changed={'yes' if within_window_changed else 'no'} "
                f"families_in_window={families_in_window} "
                f"families_changed={'yes' if families_changed else 'no'} "
                f"market_quality={market_quality}"
            )
            try:
                time.sleep(retry_delay)
            except KeyboardInterrupt:
                print("[LIVE-SELECT] retry_stopped")
                raise
            previous_within_window = within_window
            previous_families_in_window = list(families_in_window)
            previous_candidates_found = candidates_found
            if market_quality == "flat":
                retry_delay = min(
                    MAX_DISCOVERY_RETRY_DELAY_SECONDS,
                    max(retry_delay, int(retry_delay * DISCOVERY_RETRY_BACKOFF_MULTIPLIER)),
                )
            markets = None
        return None, None

    def maybe_send_telegram_heartbeat(self):
        now = time.time()
        if now - self.last_telegram_heartbeat_ts < TELEGRAM_HEARTBEAT_INTERVAL_SECONDS:
            return
        summary = self.last_live_select_summary or {}
        family_focus = summary.get("family_focus")
        if not family_focus:
            families_in_window = summary.get("families_in_window") or []
            family_focus = families_in_window[0] if families_in_window else "None"
        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (LIVE_ARMED and LIVE_ENV_CONFIRMED)
        sent = send_telegram_message(
            "🫀 HEARTBEAT\n"
            f"Mode: {'LIVE' if LIVE_MODE else 'SIM'}\n"
            f"Armed: {LIVE_ARMED}\n"
            f"Effective Dry Run: {effective_live_dry_run}\n"
            f"Realized PnL: {self.realized_pnl:+.4f}\n"
            f"Consecutive Losses: {self.consecutive_losses}\n"
            f"Last Family Focus: {family_focus}\n"
            f"Last Error: {self.last_error_message or 'None'}"
        )
        if sent:
            self.last_telegram_heartbeat_ts = now
            print("[TELEGRAM] sent=heartbeat")
        else:
            print("[TELEGRAM] failed=heartbeat")

    def print_live_quote_quality_report(self, sample_n=10):
        markets = []
        seen_tickers = set()
        cursor = None
        total_examined = 0
        target_scan = max(sample_n * 5, 50)
        for _batch in range(1, LIVE_SELECT_MAX_BATCHES + 1):
            batch_markets, next_cursor = self.fetch_market_batch(cursor=cursor)
            total_examined += len(batch_markets)
            for market in batch_markets:
                ticker = str(market.get("ticker", "") or "").strip()
                if ticker and ticker in seen_tickers:
                    continue
                if ticker:
                    seen_tickers.add(ticker)
                markets.append(market)
                if len(markets) >= target_scan:
                    break
            if len(markets) >= target_scan or not next_cursor or total_examined >= LIVE_SELECT_MAX_MARKETS:
                break
            cursor = next_cursor

        print(f"[LIVE-QUOTE-REPORT] sampled_markets={min(len(markets), sample_n)} scanned={len(markets)}")
        for market in markets[:sample_n]:
            ticker = str(market.get("ticker", "") or "").strip()
            family = self.get_market_family(market)
            try:
                q = self.build_quote(market)
                yes_bid = getattr(q, "yes_bid", 0.0) or 0.0
                yes_ask = getattr(q, "yes_ask", 0.0) or 0.0
                spread = (yes_ask - yes_bid) if yes_bid > 0 and yes_ask > 0 else None
                quote_present = yes_bid > 0 and yes_ask > 0
                allowed, reason = self._filter_live_entry_candidate(market, q)
                rejection_reason = None if allowed else reason
            except Exception:
                yes_bid = None
                yes_ask = None
                spread = None
                quote_present = False
                rejection_reason = "parser"
            print(
                f"[LIVE-QUOTE-REPORT] ticker={ticker} family={family} "
                f"best_bid={yes_bid} best_ask={yes_ask} spread={spread} "
                f"quote_present={quote_present} rejection_reason={rejection_reason}"
            )

    def sniper_score(self, q: Quote):
        spread = q.yes_ask - q.yes_bid

        if q.yes_bid <= 0 or q.yes_ask <= 0:
            return -999

        if spread < 0 or spread > self.max_spread:
            return -999

        if q.volume < self.min_volume:
            return -999

        if q.liquidity <= 0:
            return -999

        if q.last_price <= 0:
            return -999

        if not (self.min_price <= q.yes_bid <= self.max_price):
            return -999

        score = 0.0

        # Prefer tighter spreads
        score += (self.max_spread - spread) * 20

        # Prefer more active markets
        score += min(q.volume, 100) / 20
        score += min(q.liquidity, 100) / 20

        # Prefer markets where last price is near the current tradable range
        mid = (q.yes_bid + q.yes_ask) / 2
        score += max(0.0, 0.10 - abs(q.last_price - mid)) * 20

        return round(score, 4)

    def should_enter_yes(self, q: Quote):
        if q.yes_bid <= 0 or q.yes_ask <= 0:
            return False

        if q.liquidity <= 0:
            return False

        if q.volume < self.min_volume:
            return False

        spread = q.yes_ask - q.yes_bid
        if spread > self.max_spread:
            return False

        if not (self.min_price <= q.yes_bid <= self.max_price):
            return False

        if q.last_price <= 0:
            return False

        mid = (q.yes_bid + q.yes_ask) / 2

        # Only enter if price is not badly stretched above mid
        if q.last_price > q.yes_ask:
            return False

        if abs(q.last_price - mid) > 0.12:
            return False

        return True

    def passes_entry_filters(self, q):
        """
        Return (True, None) if market is eligible for entry.
        Otherwise return (False, reason_string).
        """
        liquidity = getattr(q, "liquidity", 0.0) or 0.0
        if liquidity < MIN_ENTRY_LIQUIDITY:
            return False, "low_liquidity"

        volume = getattr(q, "volume", 0.0) or 0.0
        if volume < MIN_ENTRY_VOLUME:
            return False, "low_volume"

        bid = getattr(q, "yes_bid", 0.0) or 0.0
        ask = getattr(q, "yes_ask", 0.0) or 0.0
        if bid <= 0 or ask <= 0:
            return False, "missing_bid_ask"

        spread = ask - bid
        if spread > MAX_ENTRY_SPREAD:
            return False, "wide_spread"

        if ask >= MAX_YES_ASK_TO_ENTER:
            return False, "yes_too_expensive"

        return True, None

    def can_open_new_position(self, q):
        """
        Return (True, None) if a new simulated position may be opened.
        Otherwise return (False, reason_string).
        """
        if self.trading_halted:
            return False, "trading_halted"

        if len(self.positions) >= MAX_TOTAL_EXPOSURE:
            return False, "max_exposure"

        ask = getattr(q, "yes_ask", 0.0) or 0.0
        if ask <= 0:
            return False, "missing_yes_ask"
        if ask > MAX_POSITION_COST:
            return False, "position_too_expensive"

        if self.realized_pnl <= -MAX_DAILY_LOSS:
            self.trading_halted = True
            return False, "daily_loss_limit"

        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self.trading_halted = True
            return False, "loss_streak_limit"

        return True, None

    def get_live_exposure_count(self):
        open_live_positions = sum(
            1 for position in self.live_positions
            if position.get("status", "open") == "open"
        )
        pending_live_entries = 1 if getattr(self, "live_pending_entry_ticker", None) else 0
        return open_live_positions + pending_live_entries

    def get_market_group(self, ticker):
        """
        Return the group name for a ticker using the prefix before the first '-'.
        If no '-' exists, return the full ticker.
        """
        ticker = ticker or ""
        return ticker.split("-", 1)[0] if "-" in ticker else ticker

    def passes_group_filters(self, q):
        """
        Return (True, None) if this market passes group allow/block rules.
        Otherwise return (False, reason_string).
        """
        ticker = getattr(q, "ticker", "") or ""
        group = self.get_market_group(ticker)
        if ENABLE_GROUP_BLOCKLIST and group in BLOCKED_GROUPS:
            return False, f"blocked_group:{group}"
        if ENABLE_GROUP_ALLOWLIST and group not in ALLOWED_GROUPS:
            return False, f"not_allowed_group:{group}"
        return True, None

    def apply_group_score_weight(self, score, q):
        """
        Return adjusted score after applying optional market-group multiplier.
        If feature is disabled or no multiplier exists, return original score.
        """
        if not ENABLE_GROUP_SCORE_WEIGHTS:
            return score
        ticker = getattr(q, "ticker", "") or ""
        group = self.get_market_group(ticker)
        multiplier = GROUP_SCORE_WEIGHTS.get(group)
        try:
            if multiplier is None:
                return score
            return score * float(multiplier)
        except (TypeError, ValueError):
            return score

    def build_live_order_payload(self, q, size=None):
        """
        Build and return a dict payload representing the live order that would be sent.
        This is dry-run only and must not submit anything.
        Return None if the quote is not valid enough to build a payload safely.
        """
        ticker = getattr(q, "ticker", "") or ""
        yes_ask = getattr(q, "yes_ask", 0.0) or 0.0
        if not ticker or yes_ask <= 0:
            return None
        yes_price = int(round(yes_ask * 100)) if yes_ask <= 1 else int(round(yes_ask))
        yes_price = max(1, min(99, yes_price))
        requested_count = size if size is not None else globals().get("MAX_LIVE_ORDER_SIZE", 1)
        count = min(requested_count, MAX_LIVE_ORDER_SIZE)
        if count != requested_count:
            print(f"[LIVE-SAFETY] size clamped from {requested_count} to {count}")
        return {
            "ticker": ticker,
            "side": DEFAULT_LIVE_ORDER_SIDE,
            "action": DEFAULT_LIVE_ORDER_ACTION,
            "count": count,
            "yes_price": yes_price,
            "type": "limit",
            "is_dry_run": DRY_RUN_LIVE_ORDERS or not (
                globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
            ),
        }

    def print_live_order_preview(self, q, size=None):
        """
        Build the dry-run live order payload and print a concise preview.
        Do not send any order.
        Return the payload dict or None.
        """
        payload = self.build_live_order_payload(q, size=size)
        ticker = getattr(q, "ticker", "") or ""
        if payload is None:
            print(f"[LIVE-PREVIEW] invalid order for {ticker}")
            return None
        print(
            f"[LIVE-PREVIEW] ticker={payload['ticker']} side={payload['side']} "
            f"action={payload['action']} count={payload['count']} "
            f"price={payload.get('yes_price')} type={payload['type']} dry_run={payload['is_dry_run']}"
        )
        print(f"[LIVE-PAYLOAD] {payload}")
        return payload

    def has_live_order_api_path(self):
        """
        Return True only if this file appears to have a real reusable authenticated
        live order submission path wired in. "Wired" here means the submit path no
        longer depends on the placeholder no_order_api_path sentinel and there is a
        concrete authenticated POST path available through place_live_order().
        """
        return bool(self.get_live_base_url())

    def get_live_base_url(self):
        return os.environ.get("KALSHI_LIVE_BASE_URL", "") or "https://api.elections.kalshi.com"

    def build_live_request_url(self, path):
        base_url = self.get_live_base_url().rstrip("/")
        canonical_prefix = "/trade-api/v2"
        if base_url.endswith(canonical_prefix) and path.startswith(f"{canonical_prefix}/"):
            return f"{base_url}{path[len(canonical_prefix):]}"
        return f"{base_url}{path}"

    def get_live_auth_config(self):
        api_key_source = None
        api_key = ""
        if os.environ.get("KALSHI_API_KEY_ID", ""):
            api_key_source = "KALSHI_API_KEY_ID"
            api_key = os.environ.get("KALSHI_API_KEY_ID", "")
        elif os.environ.get("KALSHI_API_KEY", ""):
            api_key_source = "KALSHI_API_KEY"
            api_key = os.environ.get("KALSHI_API_KEY", "")

        private_key_path_source = None
        private_key_path = ""
        if os.environ.get("LIVE_PRIVATE_KEY_PATH", ""):
            private_key_path_source = "LIVE_PRIVATE_KEY_PATH"
            private_key_path = os.environ.get("LIVE_PRIVATE_KEY_PATH", "")
        elif os.environ.get("KALSHI_PRIVATE_KEY_PATH", ""):
            private_key_path_source = "KALSHI_PRIVATE_KEY_PATH"
            private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        elif os.environ.get("KALSHI_PRIVATE_KEY_FILE", ""):
            private_key_path_source = "KALSHI_PRIVATE_KEY_FILE"
            private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_FILE", "")
        else:
            private_key_path_source = "DEFAULT_LIVE_PRIVATE_KEY_PATH"
            private_key_path = DEFAULT_LIVE_PRIVATE_KEY_PATH

        return {
            "api_key": api_key,
            "api_key_source": api_key_source,
            "private_key_path": private_key_path,
            "private_key_path_source": private_key_path_source,
            "private_key_exists": bool(private_key_path and os.path.exists(private_key_path)),
        }

    def get_live_api_key(self):
        return self.get_live_auth_config()["api_key"]

    def get_live_api_key_source(self):
        return self.get_live_auth_config()["api_key_source"]

    def get_env_flag(self, name):
        return (os.environ.get(name, "") or "").strip().lower()

    def is_env_flag_enabled(self, name):
        return self.get_env_flag(name) in {"1", "true", "yes", "on"}

    def get_live_private_key_path(self):
        return self.get_live_auth_config()["private_key_path"]

    def load_live_private_key(self):
        auth_config = self.get_live_auth_config()
        key_path = auth_config["private_key_path"]
        if not auth_config["private_key_exists"]:
            return None
        try:
            with open(key_path, "rb") as key_file:
                return key_file.read()
        except OSError:
            return None

    def can_build_live_auth(self):
        auth_config = self.get_live_auth_config()
        return bool(auth_config["api_key"] and auth_config["private_key_exists"] and self.load_live_private_key())

    def sign_request(self, private_key, timestamp, method, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        message = f"{timestamp}{method}{path}".encode("utf-8")
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return signature

    def sign_live_request(self, method, path, timestamp_ms=None):
        method = (method or "").upper()
        raw_path = path or ""
        path_to_sign = raw_path.split("?", 1)[0]
        if not method or not path_to_sign.startswith("/"):
            return {
                "ok": False,
                "reason": "invalid_request_parts",
                "timestamp_ms": None,
                "path_to_sign": path_to_sign,
                "signed_message": None,
                "signature": None,
            }

        if timestamp_ms is None:
            timestamp_ms = str(int(time.time() * 1000))
        else:
            timestamp_ms = str(timestamp_ms)

        signed_message = f"{timestamp_ms}{method}{path_to_sign}"

        api_key = self.get_live_api_key()
        private_key_text = self.load_live_private_key()
        if not api_key or not private_key_text:
            return {
                "ok": False,
                "reason": "missing_auth_config",
                "timestamp_ms": timestamp_ms,
                "path_to_sign": path_to_sign,
                "signed_message": signed_message,
                "signature": None,
            }
        try:
            from cryptography.hazmat.primitives import serialization

            private_key = serialization.load_pem_private_key(
                private_key_text,
                password=None,
            )
            signature = self.sign_request(private_key, timestamp_ms, method, path_to_sign)
            signature_b64 = base64.b64encode(signature).decode("ascii")
            return {
                "ok": True,
                "reason": "signed",
                "timestamp_ms": timestamp_ms,
                "path_to_sign": path_to_sign,
                "signed_message": signed_message,
                "signature": signature_b64,
                "signature_len": len(signature_b64),
                "signature_prefix": signature_b64[:12],
                "private_key_loaded": "yes",
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"sign_error:{exc.__class__.__name__}",
                "timestamp_ms": timestamp_ms,
                "path_to_sign": path_to_sign,
                "signed_message": signed_message,
                "signature": None,
                "signature_len": None,
                "signature_prefix": None,
                "private_key_loaded": "no",
            }

    def build_live_auth_headers(self, method, path):
        sign_result = self.sign_live_request(method, path)
        if not sign_result["ok"]:
            return {
                "ok": False,
                "reason": sign_result["reason"],
                "path_to_sign": sign_result.get("path_to_sign"),
                "signed_message": sign_result.get("signed_message"),
                "signature_len": sign_result.get("signature_len"),
                "signature_prefix": sign_result.get("signature_prefix"),
                "private_key_loaded": sign_result.get("private_key_loaded"),
                "headers": None,
            }
        return {
            "ok": True,
            "reason": "headers_built",
            "path_to_sign": sign_result["path_to_sign"],
            "signed_message": sign_result["signed_message"],
            "signature_len": sign_result.get("signature_len"),
            "signature_prefix": sign_result.get("signature_prefix"),
            "private_key_loaded": sign_result.get("private_key_loaded"),
            "headers": {
                "KALSHI-ACCESS-KEY": self.get_live_api_key(),
                "KALSHI-ACCESS-TIMESTAMP": sign_result["timestamp_ms"],
                "KALSHI-ACCESS-SIGNATURE": sign_result["signature"],
            },
        }

    def get_live_config_status(self):
        present = []
        missing = []
        disabled = []

        if self.get_live_base_url():
            present.append("BASE_URL")
        else:
            missing.append("BASE_URL")

        allow_live_raw = os.environ.get("KALSHI_ALLOW_LIVE")
        if allow_live_raw is None:
            missing.append("KALSHI_ALLOW_LIVE")
        elif self.is_env_flag_enabled("KALSHI_ALLOW_LIVE"):
            present.append("KALSHI_ALLOW_LIVE")
        else:
            present.append("KALSHI_ALLOW_LIVE")
            disabled.append("KALSHI_ALLOW_LIVE")

        auth_config = self.get_live_auth_config()

        if auth_config["api_key"]:
            present.append(auth_config["api_key_source"] or "KALSHI_API_KEY_ID")
        else:
            missing.append("KALSHI_API_KEY_ID")

        key_path = auth_config["private_key_path"]
        if key_path:
            present.append(auth_config["private_key_path_source"] or "LIVE_PRIVATE_KEY_PATH")
        else:
            missing.append("LIVE_PRIVATE_KEY_PATH")

        if self.load_live_private_key():
            present.append("LIVE_PRIVATE_KEY_LOADED")

        ok = not missing and not disabled
        print(f"[LIVE-CONFIG-CHECK] present={present}")
        print(f"[LIVE-CONFIG-CHECK] missing={missing}")
        if disabled:
            print(f"[LIVE-CONFIG-CHECK] disabled={disabled}")
        if ok:
            print("[LIVE-CONFIG-CHECK] PASS")
        else:
            print("[LIVE-CONFIG-CHECK] FAIL reason=missing_config")
        return {
            "ok": ok,
            "missing": missing,
            "present": present,
            "disabled": disabled,
        }

    def run_live_smoke_test(self):
        print("[LIVE-SMOKE] starting")
        print(
            f"[LIVE-SMOKE] armed={globals().get('LIVE_ARMED', False)} "
            f"env_confirmed={globals().get('LIVE_ENV_CONFIRMED', False)}"
        )
        config_status = self.get_live_config_status()
        print(f"[LIVE-SMOKE] auth_ready={self.can_build_live_auth()}")
        print(f"[LIVE-AUTH] signing_ready={self.build_live_auth_headers('GET', '/trade-api/v2/portfolio').get('ok')}")
        if not config_status["ok"]:
            print("[LIVE-SMOKE] FAIL reason=missing_config")
            return {"ok": False, "reason": "missing_config", "config": config_status}
        order_api_path = self.has_live_order_api_path()
        print(f"[LIVE-SMOKE] order_api_path={order_api_path}")
        if order_api_path:
            print("[LIVE-SMOKE] PASS")
            return {"ok": True, "reason": "live_path_present", "config": config_status}
        print("[LIVE-SMOKE] FAIL reason=no_order_api_path")
        return {"ok": False, "reason": "no_order_api_path", "config": config_status}

    def run_live_auth_get_smoke_test(self):
        print("[LIVE-AUTH-GET] starting")
        path = "/trade-api/v2/portfolio/balance"
        print(f"[LIVE-AUTH-GET] path={path}")
        auth_config = self.get_live_auth_config()
        print(f"[LIVE-AUTH-GET] resolved_api_key_source_var={auth_config['api_key_source']}")
        print(f"[LIVE-AUTH-GET] api_key_prefix={auth_config['api_key'][:6]}")
        print(f"[LIVE-AUTH-GET] api_key_length={len(auth_config['api_key'])}")
        print(f"[LIVE-AUTH-GET] resolved_private_key_path={auth_config['private_key_path']}")
        print(f"[LIVE-AUTH-GET] private_key_exists={auth_config['private_key_exists']}")
        auth_result = self.build_live_auth_headers("GET", path)
        print(f"[LIVE-AUTH-GET] signed_message={auth_result.get('signed_message')}")
        if not auth_result.get("ok"):
            print(f"[LIVE-AUTH-GET] FAIL reason={auth_result.get('reason')}")
            return {"ok": False, "reason": auth_result.get("reason"), "status_code": None, "url": None}
        url = self.build_live_request_url(path)
        try:
            response = requests.get(url, headers=auth_result["headers"], timeout=15)
            print(f"[LIVE-AUTH-GET] status_code={response.status_code}")
            print(f"[LIVE-AUTH-GET] response_body={response.text}")
            if response.ok:
                print("[LIVE-AUTH-GET] PASS")
                return {"ok": True, "reason": "ok", "status_code": response.status_code, "url": url}
            print(f"[LIVE-AUTH-GET] FAIL reason=http_{response.status_code}")
            return {"ok": False, "reason": f"http_{response.status_code}", "status_code": response.status_code, "url": url}
        except requests.RequestException as exc:
            print(f"[LIVE-AUTH-GET] FAIL reason={exc.__class__.__name__}")
            return {"ok": False, "reason": exc.__class__.__name__, "status_code": None, "url": url}

    def verify_live_order(self, order_id):
        print(f"[LIVE-VERIFY] order_id={order_id} starting")
        if not order_id:
            print(f"[LIVE-VERIFY] order_id={order_id} FAIL reason=missing_order_id")
            self.append_live_audit_log("live_verify", order_id=order_id, reason="missing_order_id")
            return {"ok": False, "reason": "missing_order_id", "status_code": None, "order": None}

        path = f"/trade-api/v2/portfolio/orders/{order_id}"
        auth_result = self.build_live_auth_headers("GET", path)
        if not auth_result.get("ok"):
            print(f"[LIVE-VERIFY] order_id={order_id} FAIL reason={auth_result.get('reason')}")
            self.append_live_audit_log("live_verify", order_id=order_id, reason=auth_result.get("reason"))
            return {"ok": False, "reason": auth_result.get("reason"), "status_code": None, "order": None}

        url = self.build_live_request_url(path)
        try:
            response = requests.get(url, headers=auth_result["headers"], timeout=15)
            print(f"[LIVE-VERIFY] order_id={order_id} status_code={response.status_code}")
            response_data = response.json() if response.content else {}
            order = response_data.get("order", response_data)
            if response.ok:
                status = order.get("status")
                remaining_count = order.get("remaining_count")
                initial_count = order.get("initial_count")
                print(
                    f"[LIVE-VERIFY] order_id={order_id} PASS status={status} "
                    f"remaining_count={remaining_count} initial_count={initial_count}"
                )
                self.append_live_audit_log(
                    "live_verify",
                    order_id=order_id,
                    reason="ok",
                    status_code=response.status_code,
                )
                return {
                    "ok": True,
                    "reason": "ok",
                    "status_code": response.status_code,
                    "order": order,
                }
            print(f"[LIVE-VERIFY] order_id={order_id} FAIL reason=http_{response.status_code}")
            self.append_live_audit_log(
                "live_verify",
                order_id=order_id,
                reason=f"http_{response.status_code}",
                status_code=response.status_code,
            )
            return {
                "ok": False,
                "reason": f"http_{response.status_code}",
                "status_code": response.status_code,
                "order": order,
            }
        except requests.RequestException as exc:
            print(f"[LIVE-VERIFY] order_id={order_id} FAIL reason={exc.__class__.__name__}")
            self.append_live_audit_log("live_verify", order_id=order_id, reason=exc.__class__.__name__)
            return {"ok": False, "reason": exc.__class__.__name__, "status_code": None, "order": None}

    def cancel_live_order(self, order_id):
        print(f"[LIVE-CANCEL] order_id={order_id} starting")
        if not order_id:
            print(f"[LIVE-CANCEL] order_id={order_id} FAIL reason=missing_order_id")
            self.append_live_audit_log("live_cancel", order_id=order_id, reason="missing_order_id")
            return {"ok": False, "reason": "missing_order_id", "status_code": None, "response": None}

        path = f"/trade-api/v2/portfolio/orders/{order_id}"
        auth_result = self.build_live_auth_headers("DELETE", path)
        if not auth_result.get("ok"):
            print(f"[LIVE-CANCEL] order_id={order_id} FAIL reason={auth_result.get('reason')}")
            self.append_live_audit_log("live_cancel", order_id=order_id, reason=auth_result.get("reason"))
            return {"ok": False, "reason": auth_result.get("reason"), "status_code": None, "response": None}

        url = self.build_live_request_url(path)
        try:
            response = requests.delete(url, headers=auth_result["headers"], timeout=15)
            print(f"[LIVE-CANCEL] order_id={order_id} status_code={response.status_code}")
            response_data = response.json() if response.content else {}
            if response.ok:
                print(f"[LIVE-CANCEL] order_id={order_id} PASS")
                self.append_live_audit_log("live_cancel", order_id=order_id, reason="ok", status_code=response.status_code)
                return {"ok": True, "reason": "ok", "status_code": response.status_code, "response": response_data}
            print(f"[LIVE-CANCEL] order_id={order_id} FAIL reason=http_{response.status_code}")
            self.append_live_audit_log(
                "live_cancel",
                order_id=order_id,
                reason=f"http_{response.status_code}",
                status_code=response.status_code,
            )
            return {
                "ok": False,
                "reason": f"http_{response.status_code}",
                "status_code": response.status_code,
                "response": response_data,
            }
        except requests.RequestException as exc:
            print(f"[LIVE-CANCEL] order_id={order_id} FAIL reason={exc.__class__.__name__}")
            self.append_live_audit_log("live_cancel", order_id=order_id, reason=exc.__class__.__name__)
            return {"ok": False, "reason": exc.__class__.__name__, "status_code": None, "response": None}

    def confirm_live_order_submit(self, payload):
        display_price = payload.get("yes_price")
        if display_price is None:
            display_price = payload.get("no_price")
        print(
            f"[LIVE-APPROVAL] REAL order ticker={payload['ticker']} side={payload['side']} "
            f"action={payload['action']} count={payload['count']} price={display_price}"
        )
        try:
            return input("Type YES to place this real live order: ") == "YES"
        except Exception:
            print("[SAFEGUARD] live submit aborted: no terminal confirmation")
            return False

    def monitor_live_canary_order(self, order_id, polls=3, sleep_seconds=2):
        if not order_id:
            return {"ok": False, "reason": "missing_order_id", "final_status": None, "cancel": None}

        seen_statuses = []
        final_verify = None
        for poll_index in range(polls):
            verify_result = self.verify_live_order(order_id)
            final_verify = verify_result
            if not verify_result.get("ok"):
                return {
                    "ok": False,
                    "reason": verify_result.get("reason"),
                    "final_status": None,
                    "verification": verify_result,
                    "cancel": None,
                }
            order = verify_result.get("order") or {}
            status = order.get("status")
            if status and (not seen_statuses or seen_statuses[-1] != status):
                print(f"[LIVE-CANARY] monitor status={status}")
                seen_statuses.append(status)
            if status in ("executed", "canceled"):
                return {
                    "ok": True,
                    "reason": "terminal_status",
                    "final_status": status,
                    "verification": verify_result,
                    "cancel": None,
                }
            if poll_index < polls - 1:
                time.sleep(sleep_seconds)

        if seen_statuses and seen_statuses[-1] == "resting":
            print("[LIVE-CANARY] order still resting on the book after brief monitor window")
            cancel_result = self.cancel_live_order(order_id)
            if cancel_result.get("ok"):
                print("[LIVE-CANARY] auto-canceled resting canary order")
                return {
                    "ok": True,
                    "reason": "auto_canceled_resting_order",
                    "final_status": "canceled",
                    "verification": final_verify,
                    "cancel": cancel_result,
                }
            return {
                "ok": False,
                "reason": cancel_result.get("reason"),
                "final_status": "resting",
                "verification": final_verify,
                "cancel": cancel_result,
            }
        return {
            "ok": True,
            "reason": "monitor_window_complete",
            "final_status": seen_statuses[-1] if seen_statuses else None,
            "verification": final_verify,
            "cancel": None,
        }

    def print_live_canary_fill_summary(self, order_id, order):
        if not order:
            return
        summary = self.extract_order_execution_summary(order)
        print(
            f"[LIVE-CANARY-FILL] order_id={summary.get('order_id') or order_id} "
            f"ticker={summary.get('ticker')} side={summary.get('side')} "
            f"action={summary.get('action')} filled_count={summary.get('filled_count')} "
            f"price={summary.get('price')} fees={summary.get('fees')} "
            f"status={summary.get('status')}"
        )
        send_telegram_message(
            f"✅ Trade Filled | {summary.get('ticker') or order_id} | "
            f"{(summary.get('side') or '?').upper()} @ {summary.get('price')}"
        )

    def extract_order_execution_summary(self, order):
        order = order or {}

        def first_value(*keys):
            for key in keys:
                value = order.get(key)
                if value not in (None, ""):
                    return value
            return None

        def num_value(*keys):
            value = first_value(*keys)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        side = (first_value("side") or "").strip().lower() or None
        fill_count = num_value("fill_count_fp", "fill_count", "filled_count")
        if fill_count is None:
            initial_count = num_value("initial_count_fp", "initial_count")
            remaining_count = num_value("remaining_count_fp", "remaining_count")
            if initial_count is not None and remaining_count is not None:
                fill_count = initial_count - remaining_count

        fees = num_value("taker_fees_dollars")
        if not fees:
            fees = num_value("maker_fees_dollars")
        if fees is None:
            fees = num_value("fees", "fee")

        if side == "yes":
            price = num_value("yes_price_dollars", "yes_price", "price")
        elif side == "no":
            price = num_value("no_price_dollars", "no_price", "price")
        else:
            price = num_value(
                "yes_price_dollars",
                "no_price_dollars",
                "yes_price",
                "no_price",
                "price",
            )

        return {
            "status": first_value("status"),
            "filled_count": fill_count,
            "price": price,
            "fees": fees,
            "side": side,
            "action": first_value("action"),
            "ticker": first_value("ticker"),
            "order_id": first_value("order_id", "id"),
        }

    def _extract_position_candidates(self, response_data):
        top_keys = sorted(list(response_data.keys())) if isinstance(response_data, dict) else []
        candidate_containers = []
        positions = []
        if isinstance(response_data, list):
            candidate_containers.append("root_list")
            positions = response_data
        elif isinstance(response_data, dict):
            for key in ("market_positions", "positions"):
                value = response_data.get(key)
                if isinstance(value, list):
                    candidate_containers.append(key)
                    positions.extend(value)
            portfolio = response_data.get("portfolio")
            if isinstance(portfolio, dict):
                candidate_containers.append("portfolio")
                for key in ("market_positions", "positions"):
                    value = portfolio.get(key)
                    if isinstance(value, list):
                        candidate_containers.append(f"portfolio.{key}")
                        positions.extend(value)
        return positions, top_keys, candidate_containers

    def extract_position_summary(self, position):
        position = position or {}

        def first_value(*keys):
            for key in keys:
                value = position.get(key)
                if value not in (None, ""):
                    return value
            return None

        def normalized_side_value(value):
            if value in (None, ""):
                return None
            normalized = str(value).strip().lower()
            if normalized in ("yes", "long", "buy", "b", "1", "true"):
                return "yes"
            if normalized in ("no", "short", "sell", "s", "-1", "false"):
                return "no"
            return normalized or None

        def num_value(*keys):
            value = first_value(*keys)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def prioritized_num_value(*keys):
            zero_value = None
            for key in keys:
                value = position.get(key)
                if value in (None, ""):
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                if abs(numeric_value) > 0:
                    return numeric_value
                if zero_value is None:
                    zero_value = numeric_value
            return zero_value

        count = prioritized_num_value(
            "position",
            "count",
            "quantity",
            "contracts",
            "contracts_held",
            "shares",
            "size",
            "position_fp",
        )
        side = normalized_side_value(first_value("side", "position_side"))
        if side is None and count is not None:
            if count > 0:
                side = "yes"
            elif count < 0:
                side = "no"
        total_traded = num_value("total_traded_dollars")
        avg_price = num_value(
            "avg_price",
            "average_price",
            "avg_price_dollars",
            "average_price_dollars",
            "cost_basis",
            "entry_price",
        )
        if avg_price is None and total_traded is not None and count not in (None, 0):
            avg_price = total_traded / abs(count)
        updated_ts = num_value("opened_at_ts", "entry_ts", "created_ts", "last_updated_ts", "updated_ts")
        if updated_ts is not None and updated_ts > 1e12:
            updated_ts = updated_ts / 1000.0
        return {
            "ticker": first_value("ticker", "market_ticker"),
            "count": count,
            "side": side,
            "avg_price": avg_price,
            "market_exposure": num_value(
                "market_exposure_dollars",
                "market_exposure",
                "exposure",
                "notional_value",
                "value",
                "position_value",
                "current_value",
            ),
            "fees": num_value("fees_paid_dollars"),
            "total_traded": total_traded,
            "updated_ts": updated_ts,
        }

    def get_live_position_snapshot(self, ticker):
        if not ticker:
            return {"ok": False, "reason": "missing_ticker", "position": None}
        path = "/trade-api/v2/portfolio/positions"
        auth_result = self.build_live_auth_headers("GET", path)
        if not auth_result.get("ok"):
            return {"ok": False, "reason": auth_result.get("reason"), "position": None}
        url = self.build_live_request_url(path)
        try:
            response = requests.get(url, headers=auth_result["headers"], timeout=15)
            response_data = response.json() if response.content else {}
            positions, top_keys, candidate_containers = self._extract_position_candidates(response_data)
            target_ticker = (ticker or "").strip()
            position = next(
                (
                    p for p in positions
                    if isinstance(p, dict)
                    and (str(self.extract_position_summary(p).get("ticker", "")).strip() == target_ticker)
                ),
                None,
            )
            if not response.ok:
                return {
                    "ok": False,
                    "reason": f"http_{response.status_code}",
                    "position": position,
                    "path": path,
                    "top_keys": top_keys,
                    "candidate_containers": candidate_containers,
                    "inspected": len(positions),
                }
            return {
                "ok": True,
                "reason": "ok",
                "position": position,
                "path": path,
                "top_keys": top_keys,
                "candidate_containers": candidate_containers,
                "inspected": len(positions),
            }
        except requests.RequestException as exc:
            return {"ok": False, "reason": exc.__class__.__name__, "position": None}

    def get_live_position_summaries(self):
        path = "/trade-api/v2/portfolio/positions"
        auth_result = self.build_live_auth_headers("GET", path)
        if not auth_result.get("ok"):
            return {"ok": False, "reason": auth_result.get("reason"), "positions": []}
        url = self.build_live_request_url(path)
        try:
            response = requests.get(url, headers=auth_result["headers"], timeout=15)
            response_data = response.json() if response.content else {}
            positions, top_keys, candidate_containers = self._extract_position_candidates(response_data)
            summaries = []
            for position in positions:
                if not isinstance(position, dict):
                    continue
                summary = self.extract_position_summary(position)
                summary["raw_position"] = position
                summaries.append(summary)
            if not response.ok:
                return {
                    "ok": False,
                    "reason": f"http_{response.status_code}",
                    "positions": summaries,
                    "path": path,
                    "top_keys": top_keys,
                    "candidate_containers": candidate_containers,
                }
            return {
                "ok": True,
                "reason": "ok",
                "positions": summaries,
                "path": path,
                "top_keys": top_keys,
                "candidate_containers": candidate_containers,
            }
        except requests.RequestException as exc:
            return {"ok": False, "reason": exc.__class__.__name__, "positions": []}

    def get_live_quote_for_ticker(self, ticker):
        target_ticker = str(ticker or "").strip()
        if not target_ticker:
            return {"ok": False, "reason": "missing_ticker", "quote": None, "source": "missing"}
        markets = self.get_markets(limit=max(self.max_markets, 200), status="open")
        final_method = "market_list_lookup"
        market = next((m for m in markets if str(m.get("ticker", "")).strip() == target_ticker), None)
        if market:
            try:
                quote = self.build_quote(market)
            except Exception:
                quote = None
            else:
                if any(
                    (getattr(quote, field, 0.0) or 0.0) > 0
                    for field in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price")
                ):
                    return {"ok": True, "reason": "ok", "quote": quote, "source": "snapshot"}

        print(f"[LIVE-QUOTE-FALLBACK] ticker={target_ticker} mode=direct_fetch")
        path = f"/trade-api/v2/markets/{target_ticker}"
        url = self.build_live_request_url(path)
        final_reason = "ticker_not_found"
        final_keys = []
        try:
            response = requests.get(url, timeout=15)
            response_data = response.json() if response.content else {}
            if isinstance(response_data, dict):
                final_keys = sorted(list(response_data.keys()))
            if response.ok:
                market = response_data.get("market", response_data)
                if isinstance(market, dict) and str(market.get("ticker", "")).strip() == target_ticker:
                    try:
                        quote = self.build_quote(market)
                    except Exception as exc:
                        final_reason = exc.__class__.__name__
                    else:
                        if any(
                            (getattr(quote, field, 0.0) or 0.0) > 0
                            for field in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price")
                        ):
                            print(f"[LIVE-QUOTE-FALLBACK] ticker={target_ticker} result=success")
                            return {"ok": True, "reason": "ok", "quote": quote, "source": "direct_fetch"}
                        final_reason = "no_usable_quote_fields"
                else:
                    final_reason = "ticker_not_found"
            else:
                final_reason = f"http_{response.status_code}"
        except (requests.RequestException, ValueError) as exc:
            final_reason = exc.__class__.__name__
        print(f"[LIVE-QUOTE-FALLBACK] ticker={target_ticker} result=unusable")
        print(f"[LIVE-QUOTE-DEBUG] ticker={target_ticker} method=direct_fetch reason={final_reason} keys={final_keys}")
        return {"ok": False, "reason": final_reason, "quote": None, "source": "missing"}

    def resolve_live_exit_quote_for_ticker(self, ticker):
        quote_result = self.get_live_quote_for_ticker(ticker)
        print(f"[LIVE-QUOTE-SOURCE] ticker={ticker} source={quote_result.get('source', 'missing')}")
        return quote_result

    def evaluate_exit_decision(self, position_summary, quote, now_ts=None):
        side = (position_summary.get("side") or "").lower()
        avg_price = position_summary.get("avg_price")
        now_ts = now_ts or time.time()
        updated_ts = position_summary.get("updated_ts")
        if updated_ts is None:
            persisted_entry = self.get_live_entry_metadata(position_summary.get("ticker"))
            if persisted_entry:
                updated_ts = persisted_entry.get("entry_ts")
        hold_minutes = None
        if updated_ts:
            try:
                hold_minutes = max(0.0, (float(now_ts) - float(updated_ts)) / 60.0)
            except (TypeError, ValueError):
                hold_minutes = None
        if position_summary.get("updated_ts") is None and updated_ts is not None and hold_minutes is not None:
            print(
                f"[LIVE-EXIT-TIME] ticker={position_summary.get('ticker')} "
                f"hold_minutes={hold_minutes} source=state"
            )
        if avg_price is None or not side:
            return {
                "should_exit": False,
                "reason": "missing_position_fields",
                "target_side": side or None,
                "expected_exit_price": None,
                "spread": None,
                "hold_minutes": hold_minutes,
            }
        if side == "yes":
            current_price = getattr(quote, "yes_bid", 0.0) or 0.0
            exit_ask = getattr(quote, "yes_ask", 0.0) or 0.0
            if current_price <= 0:
                current_price = getattr(quote, "last_price", 0.0) or 0.0
            spread = (exit_ask - getattr(quote, "yes_bid", 0.0)) if exit_ask > 0 and getattr(quote, "yes_bid", 0.0) > 0 else None
        elif side == "no":
            current_price = getattr(quote, "no_bid", 0.0) or 0.0
            exit_ask = getattr(quote, "no_ask", 0.0) or 0.0
            if current_price <= 0:
                last_price = getattr(quote, "last_price", 0.0) or 0.0
                if 0 < last_price < 1:
                    current_price = max(0.0, 1.0 - last_price)
                else:
                    current_price = 0.0
            spread = (exit_ask - getattr(quote, "no_bid", 0.0)) if exit_ask > 0 and getattr(quote, "no_bid", 0.0) > 0 else None
        else:
            current_price = 0.0
            spread = None
        if current_price <= 0:
            return {
                "should_exit": False,
                "reason": "missing_quote_data",
                "target_side": side or None,
                "expected_exit_price": None,
                "spread": spread,
                "hold_minutes": hold_minutes,
            }
        stop_loss_triggered = current_price <= avg_price - STOP_LOSS_DELTA
        if stop_loss_triggered:
            return {
                "should_exit": True,
                "reason": "stop_loss",
                "target_side": side,
                "expected_exit_price": current_price,
                "spread": spread,
                "hold_minutes": hold_minutes,
            }
        if hold_minutes is not None and hold_minutes >= MAX_HOLD_MINUTES:
            return {
                "should_exit": True,
                "reason": "max_hold",
                "target_side": side,
                "expected_exit_price": current_price,
                "spread": spread,
                "hold_minutes": hold_minutes,
            }
        if spread is not None and spread > MAX_EXIT_SPREAD:
            return {
                "should_exit": False,
                "reason": "spread_too_wide",
                "target_side": side,
                "expected_exit_price": current_price,
                "spread": spread,
                "hold_minutes": hold_minutes,
            }
        if current_price >= avg_price + TAKE_PROFIT_DELTA:
            return {
                "should_exit": True,
                "reason": "take_profit",
                "target_side": side,
                "expected_exit_price": current_price,
                "spread": spread,
                "hold_minutes": hold_minutes,
            }
        return {
            "should_exit": False,
            "reason": "hold",
            "target_side": side,
            "expected_exit_price": current_price,
            "spread": spread,
            "hold_minutes": hold_minutes,
        }

    def print_live_canary_position_snapshot(self, ticker, verified_status=None):
        retry_delays = (0.0, 0.75, 1.5, 2.5, 4.0)
        last_snapshot = None
        for attempt_index, delay_seconds in enumerate(retry_delays):
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            snapshot = self.get_live_position_snapshot(ticker)
            last_snapshot = snapshot
            if not snapshot.get("ok"):
                print(f"[LIVE-CANARY-POSITION] ticker={ticker} unavailable reason={snapshot.get('reason')}")
                return
            position = snapshot.get("position")
            if position:
                summary = self.extract_position_summary(position)
                print(
                    f"[LIVE-CANARY-POSITION] ticker={ticker} position_found=yes count={summary.get('count')} "
                    f"side={summary.get('side')} avg_price={summary.get('avg_price')} "
                    f"market_exposure={summary.get('market_exposure')}"
                )
                if any(summary.get(field) is None for field in ("count", "side", "avg_price", "market_exposure")):
                    print(
                        f"[LIVE-CANARY-POSITION-FIELDS-DEBUG] ticker={ticker} "
                        f"keys={sorted(list(position.keys()))}"
                    )
                return
            if attempt_index < len(retry_delays) - 1:
                print(
                    f"[LIVE-CANARY-POSITION] ticker={ticker} position_found=no "
                    f"retry_in={retry_delays[attempt_index + 1]}"
                )
        if last_snapshot:
            print(
                f"[LIVE-CANARY-POSITION-DEBUG] path={last_snapshot.get('path')} "
                f"top_keys={last_snapshot.get('top_keys', [])} "
                f"containers={last_snapshot.get('candidate_containers', [])} "
                f"inspected={last_snapshot.get('inspected')}"
            )
        print(f"[LIVE-CANARY-POSITION] ticker={ticker} not_visible_yet verified_status={verified_status}")

    def run_live_flatten_ticker(self, ticker):
        print("[LIVE-FLATTEN] starting")
        snapshot = self.get_live_position_snapshot(ticker)
        if not snapshot.get("ok"):
            print(f"[LIVE-FLATTEN] position summary unavailable reason={snapshot.get('reason')}")
            return {"ok": False, "reason": snapshot.get("reason")}
        position = snapshot.get("position")
        if not position:
            print(f"[LIVE-FLATTEN] position summary ticker={ticker} no_position_found")
            return {"ok": True, "reason": "no_position"}
        position_summary = self.extract_position_summary(position)
        current_position = position_summary.get("count")
        total_traded = position_summary.get("total_traded")
        print(
            f"[LIVE-FLATTEN] position summary ticker={ticker} position={current_position} "
            f"side={position_summary.get('side')} total_traded={total_traded}"
        )
        try:
            size = int(abs(float(current_position)))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            print(f"[LIVE-FLATTEN] position summary ticker={ticker} no_position_found")
            return {"ok": True, "reason": "no_position"}
        markets = self.get_markets(limit=self.max_markets, status="open")
        market = next((m for m in markets if m.get("ticker") == ticker), None)
        if not market:
            print(f"[LIVE-FLATTEN] submit status=ticker_not_found")
            return {"ok": False, "reason": "ticker_not_found"}
        q = self.build_quote(market)
        flatten_position = {
            "ticker": ticker,
            "size": size,
            "count": current_position,
            "side": position_summary.get("side"),
        }
        payload = self.build_live_exit_payload(flatten_position, q)
        if payload is None:
            print(f"[LIVE-FLATTEN] submit status=invalid_payload")
            return {"ok": False, "reason": "invalid_payload"}
        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (
            globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
        )
        payload["is_dry_run"] = effective_live_dry_run
        if effective_live_dry_run:
            print(
                f"[LIVE-EXIT-PREVIEW] ticker={payload['ticker']} side={payload['side']} "
                f"action={payload['action']} count={payload['count']} "
                f"price={payload.get('yes_price')} type={payload['type']} dry_run={payload['is_dry_run']}"
            )
            print(f"[LIVE-PAYLOAD] {payload}")
            print("[LIVE-FLATTEN] submit status=dry_run_only")
            return {"ok": True, "reason": "dry_run_only", "payload": payload}
        auth_smoke_result = self.run_live_auth_get_smoke_test()
        if not auth_smoke_result.get("ok"):
            print("[LIVE-BLOCK] AUTH_SMOKE_REQUIRED failed; aborting live submit")
            return {"ok": False, "reason": "auth_smoke_required_failed"}
        print(
            f"[LIVE-CONFIRM] ticker={payload['ticker']} side={payload['side']} "
            f"action={payload['action']} count={payload['count']} "
            f"price={payload.get('yes_price')} type={payload['type']}"
        )
        if not self.confirm_live_order_submit(payload):
            print("[SAFEGUARD] live submit aborted by user")
            return {"ok": False, "reason": "user_aborted"}
        result = self.place_live_order(payload)
        print(f"[LIVE-FLATTEN] submit status={result.get('reason')}")
        if not result.get("ok"):
            return {"ok": False, "reason": result.get("reason"), "response": result.get("response")}
        response_data = result.get("response") or {}
        order_id = (
            response_data.get("order_id")
            or (response_data.get("order") or {}).get("order_id")
            or response_data.get("id")
        )
        verification = self.verify_live_order(order_id)
        verify_status = (verification.get("order") or {}).get("status") if verification.get("ok") else verification.get("reason")
        print(f"[LIVE-FLATTEN] verify status={verify_status}")
        return {"ok": True, "reason": "submitted", "verification": verification, "response": response_data}

    def run_live_canary_workflow(self, cancel_after_verify=False):
        print("[LIVE-CANARY] starting")
        supplied_ticker = globals().get("LIVE_TEST_TICKER")
        ticker, markets = self.select_live_canary_ticker(supplied_ticker)
        if not ticker:
            return {"ok": False, "reason": "no_live_ticker_found", "submit": None, "verify": None, "cancel": None}
        market = next((m for m in markets if m.get("ticker") == ticker), None)
        if not market:
            print("[LIVE-CANARY-AUTO] no tradable ticker found")
            return {"ok": False, "reason": "no_live_ticker_found", "submit": None, "verify": None, "cancel": None}

        q = self.build_quote(market)
        self.live_canary_selected_ticker = ticker
        self.live_canary_authorized_ticker = ticker
        print(f"[LIVE-CANARY] selected ticker={ticker}")
        print(f"[LIVE-CANARY] temporarily_authorized_for_run={self.live_canary_authorized_ticker == ticker}")
        if not supplied_ticker:
            print(f"[LIVE-CANARY] proceeding with auto-selected ticker={ticker}")
        print(
            f"[LIVE-CANARY] quote ticker={q.ticker} yes_bid={q.yes_bid} yes_ask={q.yes_ask} "
            f"no_bid={q.no_bid} no_ask={q.no_ask} last_price={q.last_price} "
            f"volume={q.volume} liquidity={q.liquidity}"
        )
        try:
            submit_result = self.submit_live_order(q)
        finally:
            self.live_canary_selected_ticker = None
            self.live_canary_authorized_ticker = None
        if not submit_result.get("submitted"):
            print(f"[LIVE-CANARY] FAIL reason={submit_result.get('reason')}")
            return {
                "ok": False,
                "reason": submit_result.get("reason"),
                "submit": submit_result,
                "verify": submit_result.get("verification"),
                "cancel": None,
            }

        response_data = submit_result.get("response") or {}
        order_id = (
            response_data.get("order_id")
            or (response_data.get("order") or {}).get("order_id")
            or response_data.get("id")
        )
        print(f"[LIVE-CANARY] submitted order_id={order_id}")

        verify_result = submit_result.get("verification")
        if verify_result and verify_result.get("ok"):
            order = verify_result.get("order") or {}
            print(f"[LIVE-CANARY] verified status={order.get('status')}")
        else:
            print(f"[LIVE-CANARY] FAIL reason={verify_result.get('reason') if verify_result else 'missing_verification'}")
            return {
                "ok": False,
                "reason": verify_result.get("reason") if verify_result else "missing_verification",
                "submit": submit_result,
                "verify": verify_result,
                "cancel": None,
            }

        monitor_result = self.monitor_live_canary_order(order_id)
        if not monitor_result.get("ok"):
            print(f"[LIVE-CANARY] FAIL reason={monitor_result.get('reason')}")
            return {
                "ok": False,
                "reason": monitor_result.get("reason"),
                "submit": submit_result,
                "verify": verify_result,
                "cancel": monitor_result.get("cancel"),
            }
        if monitor_result.get("final_status") == "executed":
            executed_order = (monitor_result.get("verification") or {}).get("order") or order
            self.persist_live_entry_metadata(
                order=executed_order,
                payload=submit_result.get("payload"),
                order_id=order_id,
            )
            self.print_live_canary_fill_summary(order_id, executed_order)
            self.print_live_canary_position_snapshot(
                executed_order.get("ticker") or ticker,
                verified_status=monitor_result.get("final_status"),
            )

        cancel_result = monitor_result.get("cancel")
        if cancel_after_verify and not cancel_result:
            cancel_result = self.cancel_live_order(order_id)
            if cancel_result.get("ok"):
                print("[LIVE-CANARY] canceled")
            else:
                print(f"[LIVE-CANARY] FAIL reason={cancel_result.get('reason')}")
                return {
                    "ok": False,
                    "reason": cancel_result.get("reason"),
                    "submit": submit_result,
                    "verify": verify_result,
                    "cancel": cancel_result,
                }

        return {
            "ok": True,
            "reason": "canary_completed",
            "submit": submit_result,
            "verify": verify_result,
            "cancel": cancel_result,
        }

    def _is_combo_like_market(self, market):
        combo_text = " ".join(
            str(market.get(key, "") or "")
            for key in (
                "ticker",
                "title",
                "subtitle",
                "market_type",
                "event_ticker",
                "mve_collection_ticker",
                "mve_selected_legs",
            )
        ).upper()
        return bool(
            market.get("mve_collection_ticker")
            or market.get("mve_selected_legs")
            or any(marker in combo_text for marker in ("CROSSCATEGORY", "COMBO", "MULTI"))
        )

    def select_live_canary_ticker(self, supplied_ticker=None):
        max_batches = 5
        batch_limit = max(self.max_markets, 200)
        max_total_markets_examined = 1000
        batch_params = {"mve_filter": "exclude"}
        cursor = None
        batch_number = 0
        total_examined = 0
        all_markets = []
        seen_tickers = set()
        strict_candidates = []
        relaxed_candidates = []
        rejection_counts = {
            "missing_ticker": 0,
            "status": 0,
            "quote_exception": 0,
            "empty_quote": 0,
            "no_price_data": 0,
            "combo": 0,
            "permissions": 0,
            "spread": 0,
            "liquidity": 0,
        }
        sample_rejections = []

        if supplied_ticker:
            for batch_try in range(max_batches):
                batch_markets, next_cursor = self.get_markets(
                    limit=batch_limit,
                    status="open",
                    cursor=cursor,
                    return_cursor=True,
                    extra_params=batch_params,
                )
                batch_number = batch_try + 1
                print(f"[LIVE-CANARY-AUTO] batch={batch_number} request_params={batch_params}")
                print(f"[LIVE-CANARY-AUTO] batch={batch_number} fetched_count={len(batch_markets)}")
                total_examined += len(batch_markets)
                all_markets.extend(batch_markets)
                market = next((m for m in batch_markets if m.get("ticker") == supplied_ticker), None)
                if market:
                    try:
                        q = self.build_quote(market)
                    except Exception:
                        q = None
                    if q and q.ticker and q.yes_ask > 0 and q.yes_ask < 1 and q.yes_bid >= 0:
                        print(f"[LIVE-CANARY-AUTO] using supplied ticker={supplied_ticker}")
                        return supplied_ticker, all_markets
                if not next_cursor or total_examined >= max_total_markets_examined:
                    break
                cursor = next_cursor
            print("[LIVE-CANARY-AUTO] supplied ticker invalid, falling back to auto-selection")
            cursor = None
            batch_number = 0
            total_examined = 0
            all_markets = []
            seen_tickers = set()

        while batch_number < max_batches and total_examined < max_total_markets_examined:
            batch_number += 1
            batch_markets, next_cursor = self.get_markets(
                limit=batch_limit,
                status="open",
                cursor=cursor,
                return_cursor=True,
                extra_params=batch_params,
            )
            print(f"[LIVE-CANARY-AUTO] batch={batch_number} request_params={batch_params}")
            print(f"[LIVE-CANARY-AUTO] batch={batch_number} fetched_count={len(batch_markets)}")
            total_examined += len(batch_markets)
            combo_like_markets = []
            regular_markets = []
            for market in batch_markets:
                ticker = market.get("ticker", "")
                if ticker and ticker in seen_tickers:
                    continue
                if ticker:
                    seen_tickers.add(ticker)
                all_markets.append(market)
                if self._is_combo_like_market(market):
                    combo_like_markets.append(market)
                else:
                    regular_markets.append(market)
            print(f"[LIVE-CANARY-AUTO] batch={batch_number} combo_like_markets={len(combo_like_markets)}")
            print(f"[LIVE-CANARY-AUTO] batch={batch_number} regular_markets={len(regular_markets)}")
            print(f"[LIVE-CANARY-AUTO] total_examined={total_examined}")
            if not regular_markets and combo_like_markets:
                print(f"[LIVE-CANARY-AUTO] batch={batch_number} endpoint_filter_still_combo_heavy=yes")

            for market in regular_markets:
                ticker = market.get("ticker", "")
                status = (market.get("status") or "").lower()
                if len(strict_candidates) + len(relaxed_candidates) + sum(rejection_counts.values()) < 10:
                    print(
                        f"[LIVE-CANARY-DEBUG] market ticker={ticker} status={status} "
                        f"can_open={market.get('can_open')} can_close={market.get('can_close')} "
                        f"keys={sorted(list(market.keys()))[:20]}"
                    )
                if not ticker:
                    rejection_counts["missing_ticker"] += 1
                    if len(sample_rejections) < 5:
                        sample_rejections.append(("", "missing_ticker"))
                    continue
                if status not in ("open", "active"):
                    rejection_counts["status"] += 1
                    if len(sample_rejections) < 5:
                        sample_rejections.append((ticker, "status"))
                    continue
                try:
                    q = self.build_quote(market)
                except Exception as exc:
                    print(f"[LIVE-CANARY-DEBUG] build_quote_failed ticker={ticker} error={exc}")
                    rejection_counts["quote_exception"] += 1
                    if len(sample_rejections) < 5:
                        sample_rejections.append((ticker, "quote_exception"))
                    continue
                if len(strict_candidates) + len(relaxed_candidates) < 5:
                    print(
                        f"[LIVE-CANARY-DEBUG] quote ticker={q.ticker} yes_bid={q.yes_bid} "
                        f"yes_ask={q.yes_ask} last_price={q.last_price} volume={q.volume} "
                        f"liquidity={q.liquidity}"
                    )
                if not q.ticker:
                    rejection_counts["empty_quote"] += 1
                    if len(sample_rejections) < 5:
                        sample_rejections.append((ticker, "empty_quote"))
                    continue
                allowed, reason = self._filter_live_entry_candidate(market, q)
                if not allowed:
                    if reason == "missing_quotes":
                        rejection_counts["no_price_data"] += 1
                    elif reason == "combo_or_mve":
                        rejection_counts["combo"] += 1
                    elif reason == "not_tradable":
                        rejection_counts["permissions"] += 1
                    elif reason == "wide_spread":
                        rejection_counts["spread"] += 1
                    elif reason in ("low_liquidity", "low_volume", "unsafe_price"):
                        rejection_counts["liquidity"] += 1
                    elif reason == "inactive":
                        rejection_counts["status"] += 1
                    if len(sample_rejections) < 5:
                        sample_rejections.append((ticker, reason))
                    print(f"[LIVE-SELECT-FILTER] ticker={ticker} reason={reason}")
                    continue
                score = self._score_live_ticker_candidate(q)
                spread = q.yes_ask - q.yes_bid
                print(
                    f"[LIVE-SELECT-SCORE] ticker={ticker} score={score} spread={spread} "
                    f"volume={q.volume} liquidity={q.liquidity} price={q.yes_ask}"
                )
                strict_candidates.append((score, q))

            print(f"[LIVE-CANARY-AUTO] strict_candidates={len(strict_candidates)}")
            print(f"[LIVE-CANARY-AUTO] relaxed_candidates={len(relaxed_candidates)}")
            if strict_candidates:
                break
            if not next_cursor:
                print("[LIVE-CANARY-AUTO] search_stop=no_next_cursor")
                break
            cursor = next_cursor

        print(f"[LIVE-CANARY-DEBUG] rejected_missing_ticker={rejection_counts['missing_ticker']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_status={rejection_counts['status']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_quote_exception={rejection_counts['quote_exception']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_empty_quote={rejection_counts['empty_quote']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_no_price_data={rejection_counts['no_price_data']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_combo={rejection_counts['combo']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_permissions={rejection_counts['permissions']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_spread={rejection_counts['spread']}")
        print(f"[LIVE-CANARY-DEBUG] rejected_liquidity={rejection_counts['liquidity']}")
        for rejected_ticker, reason in sample_rejections[:5]:
            print(f"[LIVE-CANARY-DEBUG] sample_reject ticker={rejected_ticker} reason={reason}")
        strict_candidates.sort(key=lambda item: item[0], reverse=True)
        if strict_candidates:
            selected = strict_candidates[0][1]
            print(
                f"[LIVE-CANARY-AUTO] selected ticker={selected.ticker} "
                f"reason=strict_regular_market ask={selected.yes_ask} bid={selected.yes_bid} "
                f"liquidity={selected.liquidity} total_examined={total_examined}"
            )
            return selected.ticker, all_markets

        print("[LIVE-CANARY-AUTO] no tradable ticker found reason=search_limit_reached_or_no_regular_candidates")
        return None, all_markets

    def preview_live_ticker_selection(self):
        ticker, reason = self.find_best_live_ticker_with_retries()
        if not ticker:
            return {"ok": False, "reason": "no_live_ticker_found", "ticker": None}
        return {"ok": True, "reason": reason, "ticker": ticker}

    def place_live_order(self, payload):
        ticker = payload.get("ticker", "")
        if (
            not payload.get("ticker")
            or not payload.get("count")
            or (payload.get("yes_price") is None and payload.get("no_price") is None)
        ):
            print(f"[LIVE-ERROR] {ticker} error=invalid_payload")
            return {
                "ok": False,
                "status_code": None,
                "response": None,
                "reason": "invalid_payload",
            }
        path = "/trade-api/v2/portfolio/orders"
        auth_result = self.build_live_auth_headers("POST", path)
        if not auth_result.get("ok"):
            return {
                "ok": False,
                "status_code": None,
                "response": None,
                "reason": auth_result.get("reason"),
            }
        url = self.build_live_request_url(path)
        headers = {**auth_result["headers"], "Content-Type": "application/json"}
        submit_payload = {
            key: value
            for key, value in payload.items()
            if value is not None and key not in ("price", "yes_price_dollars", "no_price_dollars")
        }
        print(f"[LIVE-SUBMIT] ticker={ticker} sending")
        print(f"[LIVE-SIGN-DEBUG] key_id={self.get_live_api_key()}")
        print("[LIVE-SIGN-DEBUG] method=POST")
        print(f"[LIVE-SIGN-DEBUG] path={path}")
        print(f"[LIVE-SIGN-DEBUG] timestamp={auth_result['headers'].get('KALSHI-ACCESS-TIMESTAMP')}")
        print(f"[LIVE-SIGN-DEBUG] message={auth_result.get('signed_message')}")
        print(f"[LIVE-SIGN-DEBUG] private_key_loaded={auth_result.get('private_key_loaded')}")
        print(f"[LIVE-SIGN-DEBUG] signature_len={auth_result.get('signature_len')}")
        print(f"[LIVE-SIGN-DEBUG] signature_prefix={auth_result.get('signature_prefix')}")
        access_key = headers.get("KALSHI-ACCESS-KEY", "") or ""
        print(f"[LIVE-HEADER-DEBUG] access_key_prefix={access_key[:6]}")
        print(f"[LIVE-HEADER-DEBUG] access_key_len={len(access_key)}")
        print(f"[LIVE-HEADER-DEBUG] timestamp={headers.get('KALSHI-ACCESS-TIMESTAMP')}")
        print(f"[LIVE-HEADER-DEBUG] signature_len={len(headers.get('KALSHI-ACCESS-SIGNATURE', '') or '')}")
        print(f"[LIVE-HEADER-DEBUG] header_names={sorted(list(headers.keys()))}")
        print("[LIVE-SUBMIT-DEBUG] url=", url)
        print("[LIVE-SUBMIT-DEBUG] signed_path=", path)
        print(f"[LIVE-SUBMIT-DEBUG] payload_keys={sorted(list(submit_payload.keys()))}")
        print(f"[LIVE-SUBMIT-DEBUG] payload_body={submit_payload}")
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=submit_payload,
                timeout=15,
            )
            response_text = resp.text
            content_type = resp.headers.get("Content-Type", "")
            print("[LIVE-SUBMIT-DEBUG] status=", resp.status_code)
            print("[LIVE-SUBMIT-DEBUG] body=", response_text[:500])
            response_data = response_text
            if "application/json" in content_type.lower():
                try:
                    response_data = resp.json()
                except Exception as e:
                    print("[LIVE-SUBMIT-DEBUG] json_error=", str(e))
                    response_data = response_text
            if resp.ok:
                return {
                    "ok": True,
                    "status_code": resp.status_code,
                    "response": response_data,
                    "reason": "submitted",
                }
            return {
                "ok": False,
                "status_code": resp.status_code,
                "response": response_data,
                "reason": f"http_{resp.status_code}",
            }
        except requests.RequestException as exc:
            print(f"[LIVE-ERROR] {ticker} error={exc}")
            return {
                "ok": False,
                "status_code": None,
                "response": None,
                "reason": "submit_error",
            }

    def submit_live_order(self, q, size=None):
        """
        Submit a real live order only if all live safety checks pass.
        If DRY_RUN_LIVE_ORDERS is True, preview only and do not submit.
        Return a result dict describing what happened.
        """
        ticker = getattr(q, "ticker", "") or ""
        live_check = getattr(self, "can_place_live_order", None)
        if callable(live_check):
            allowed, reason = live_check(q)
            if not allowed:
                print(f"[LIVE-SKIP] {ticker} reason={reason}")
                return {"ok": False, "reason": reason, "submitted": False}

        payload = self.build_live_order_payload(q, size=size)
        if payload is None:
            print(f"[LIVE-SKIP] {ticker} reason=invalid_payload")
            return {"ok": False, "reason": "invalid_payload", "submitted": False}

        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (
            globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
        )
        payload["is_dry_run"] = effective_live_dry_run

        if effective_live_dry_run:
            self.print_live_order_preview(q, size=size)
            return {"ok": True, "reason": "dry_run_only", "submitted": False, "payload": payload}

        supplied_ticker = globals().get("LIVE_TEST_TICKER")
        authorized_canary_ticker = getattr(self, "live_canary_authorized_ticker", None)
        allowed_ticker = authorized_canary_ticker or supplied_ticker
        if not allowed_ticker:
            print("[SAFEGUARD] no live test ticker provided")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="no_live_test_ticker")
            return {"ok": False, "reason": "no_live_test_ticker", "submitted": False}
        if payload.get("ticker") != allowed_ticker:
            print("[SAFEGUARD] ticker not allowed for canary live submit")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="ticker_not_allowed")
            return {"ok": False, "reason": "ticker_not_allowed", "submitted": False}
        if self.live_canary_submitted:
            print("[SAFEGUARD] canary live order already used")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="canary_already_used")
            return {"ok": False, "reason": "canary_already_used", "submitted": False}
        current_live_exposure = self.get_live_exposure_count()
        if current_live_exposure >= MAX_SIMULTANEOUS_LIVE_POSITIONS:
            print(
                f"[LIVE-GUARD] blocked new entry reason=max_simultaneous_live_positions "
                f"current={current_live_exposure} limit={MAX_SIMULTANEOUS_LIVE_POSITIONS} ticker={ticker}"
            )
            return {"ok": False, "reason": "max_simultaneous_live_positions", "submitted": False}

        now = time.time()
        self._live_order_timestamps = [
            ts
            for ts in self._live_order_timestamps
            if now - ts < LIVE_ORDER_RATE_WINDOW_SEC
        ]
        if len(self._live_order_timestamps) >= LIVE_ORDER_RATE_LIMIT:
            print("[LIVE-BLOCK] rate limit exceeded")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="rate_limited")
            return {"ok": False, "reason": "rate_limited", "submitted": False}

        today = time.strftime("%Y-%m-%d")
        if self.live_trade_day != today:
            self.live_trade_day = today
            self.live_trade_count_today = 0
        if self.live_trade_count_today >= MAX_LIVE_TRADES_PER_DAY:
            print("[LIVE-BLOCK] daily live trade cap reached")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="daily_trade_cap")
            return {"ok": False, "reason": "daily_trade_cap", "submitted": False}

        if not self.has_live_order_api_path():
            print(f"[LIVE-SKIP] {ticker} reason=no_order_api_path")
            self.append_live_audit_log("live_safeguard_block", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="no_order_api_path")
            return {"ok": False, "reason": "no_order_api_path", "submitted": False}

        auth_smoke_result = self.run_live_auth_get_smoke_test()
        if not auth_smoke_result.get("ok"):
            print("[LIVE-BLOCK] AUTH_SMOKE_REQUIRED failed; aborting live submit")
            return {"ok": False, "reason": "auth_smoke_required_failed", "submitted": False}

        self._live_order_timestamps.append(now)
        self.live_trade_count_today += 1
        print(
            f"[LIVE-CONFIRM] ticker={payload['ticker']} side={payload['side']} "
            f"action={payload['action']} count={payload['count']} "
            f"price={payload.get('yes_price')} type={payload['type']}"
        )
        if not self.confirm_live_order_submit(payload):
            print("[SAFEGUARD] live submit aborted by user")
            self.append_live_audit_log("live_user_aborted", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="user_aborted")
            return {"ok": False, "reason": "user_aborted", "submitted": False}
        self.append_live_audit_log("live_submit_attempt", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"))
        self.live_pending_entry_ticker = ticker
        try:
            result = self.place_live_order(payload)
        finally:
            self.live_pending_entry_ticker = None
        if result.get("ok"):
            response_data = result.get("response") or {}
            order_id = (
                response_data.get("order_id")
                or (response_data.get("order") or {}).get("order_id")
                or response_data.get("id")
            )
            verification = self.verify_live_order(order_id)
            verified_order = (verification.get("order") or {}) if verification.get("ok") else {}
            if verification.get("ok") and (verified_order.get("status") == "executed"):
                self.persist_live_entry_metadata(
                    order=verified_order,
                    payload=payload,
                    order_id=order_id,
                )
            self.live_canary_submitted = True
            print(f"[LIVE-SUBMIT] ticker={ticker} status_code={result.get('status_code')}")
            print(f"[LIVE-SUBMIT] ticker={ticker} success")
            alert_price = payload.get("yes_price")
            if alert_price is None:
                alert_price = payload.get("no_price")
            if alert_price is not None:
                try:
                    alert_price = float(alert_price) / 100.0
                except (TypeError, ValueError):
                    pass
            send_telegram_message(
                f"🟢 ENTRY\n"
                f"Ticker: {ticker}\n"
                f"Side: {(payload.get('side') or '?').upper()}\n"
                f"Price: {alert_price}\n"
                f"Size: {payload.get('count')}"
            )
            self.append_live_audit_log(
                "live_submit_success",
                ticker=payload.get("ticker"),
                order_id=order_id,
                side=payload.get("side"),
                action=payload.get("action"),
                count=payload.get("count"),
                price=payload.get("price"),
                status_code=result.get("status_code"),
            )
            return {
                "ok": True,
                "reason": "submitted",
                "submitted": True,
                "payload": payload,
                "response": result.get("response"),
                "status_code": result.get("status_code"),
                "verification": verification,
            }
        if result.get("reason") == "no_order_api_path":
            print(f"[LIVE-SKIP] {ticker} reason=no_order_api_path")
            self.append_live_audit_log("live_submit_failure", ticker=payload.get("ticker"), side=payload.get("side"), action=payload.get("action"), count=payload.get("count"), price=payload.get("price"), reason="no_order_api_path")
            return {"ok": False, "reason": "no_order_api_path", "submitted": False}
        self.append_live_audit_log(
            "live_submit_failure",
            ticker=payload.get("ticker"),
            side=payload.get("side"),
            action=payload.get("action"),
            count=payload.get("count"),
            price=payload.get("price"),
            reason="submit_error",
            status_code=result.get("status_code"),
        )
        return {
            "ok": False,
            "reason": "submit_error",
            "submitted": False,
            "payload": payload,
            "response": result.get("response"),
            "status_code": result.get("status_code"),
        }

    def already_holding(self, ticker):
        return any(p["ticker"] == ticker for p in self.positions)

    def enter_sim_trade(self, q: Quote):
        position = {
            "ticker": q.ticker,
            "side": "YES",
            "entry": q.yes_bid,
            "qty": 1,
            "size": SIM_POSITION_SIZE,
            "opened_at": time.time(),
        }
        self.positions.append(position)
        print(f"[SIM ENTRY] {q.ticker} YES @ {q.yes_bid:.4f}")

    def record_live_position(self, q, size=None, entry_price=None):
        """
        Record a successfully submitted live position in self.live_positions.
        This is bookkeeping only.
        """
        ticker = getattr(q, "ticker", "") or ""
        side = "YES"
        count = size if size is not None else globals().get("MAX_LIVE_ORDER_SIZE", 1)
        price = entry_price if entry_price is not None else (getattr(q, "yes_ask", 0.0) or 0.0)
        if not ticker or price <= 0:
            return None
        position = {
            "ticker": ticker,
            "side": side,
            "entry": price,
            "size": count,
            "opened_at": time.time(),
            "mode": "LIVE",
            "status": "open",
        }
        self.live_positions.append(position)
        return position

    def build_live_exit_payload(self, position, q):
        """
        Build and return a dict payload representing the live EXIT order
        that would be sent for an existing live position.
        Return None if the position/quote is not valid enough.
        """
        ticker = position.get("ticker", "")
        raw_count = position.get("count", position.get("size", 1))
        side = (position.get("side") or "yes").lower()
        try:
            size = int(abs(float(raw_count)))
        except (TypeError, ValueError):
            size = int(position.get("size", 1) or 1)
        yes_bid = getattr(q, "yes_bid", 0.0) or 0.0
        no_bid = getattr(q, "no_bid", 0.0) or 0.0
        if not ticker or size <= 0:
            return None
        payload = {
            "ticker": ticker,
            "side": side,
            "action": "sell",
            "count": size,
            "type": "limit",
            "is_dry_run": DRY_RUN_LIVE_ORDERS or not (
                globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
            ),
        }
        if side == "yes":
            if yes_bid <= 0:
                return None
            yes_price = int(round(yes_bid * 100)) if yes_bid <= 1 else int(round(yes_bid))
            payload["yes_price"] = max(1, min(99, yes_price))
        elif side == "no":
            if no_bid <= 0:
                return None
            no_price = int(round(no_bid * 100)) if no_bid <= 1 else int(round(no_bid))
            payload["no_price"] = max(1, min(99, no_price))
        else:
            return None
        return payload

    def run_live_monitor_positions_once(self):
        return self.run_live_monitor_positions_pass(allow_real_exits=False)

    def execute_live_exit_plan(self, ticker, payload, decision_reason, approval_required=True, position_summary=None, decision=None):
        print(
            f"[LIVE-EXIT-SUBMIT] ticker={ticker} count={payload.get('count')} "
            f"side={payload.get('side')} action={payload.get('action')}"
        )
        if approval_required:
            if not self.confirm_live_order_submit(payload):
                print("[SAFEGUARD] live submit aborted by user")
                return {"ok": False, "reason": "user_aborted"}
        else:
            print(f"[LIVE-APPROVAL-BYPASS] ticker={ticker} reason=manager_auto_exit")
        result = self.place_live_order(payload)
        print(
            f"[LIVE-EXIT-SUBMIT] ticker={ticker} status={result.get('reason')} "
            f"status_code={result.get('status_code')}"
        )
        if not result.get("ok"):
            print(f"[LIVE-EXIT-RESULT] ticker={ticker} ok=False reason={result.get('reason')}")
            return {"ok": False, "reason": result.get("reason"), "response": result.get("response")}
        response_data = result.get("response") or {}
        order_id = (
            response_data.get("order_id")
            or (response_data.get("order") or {}).get("order_id")
            or response_data.get("id")
        )
        verification = self.verify_live_order(order_id)
        verify_status = (verification.get("order") or {}).get("status") if verification.get("ok") else verification.get("reason")
        print(f"[LIVE-EXIT-VERIFY] ticker={ticker} order_id={order_id} status={verify_status}")
        print(f"[LIVE-EXIT-RESULT] ticker={ticker} ok={verification.get('ok')} reason={verify_status}")
        cleared = False
        if verification.get("ok") and verify_status == "executed":
            snapshot = self.get_live_position_snapshot(ticker)
            if snapshot.get("ok"):
                position = snapshot.get("position")
                if position:
                    summary = self.extract_position_summary(position)
                    count = summary.get("count")
                    if count is not None and abs(count) == 0:
                        self.remove_live_entry_metadata(ticker)
                        cleared = True
            print(f"[LIVE-EXIT-COMPLETE] ticker={ticker} cleared={'yes' if cleared else 'no'}")
            exit_price = payload.get("yes_price")
            if exit_price is None:
                exit_price = payload.get("no_price")
            try:
                exit_price = (float(exit_price) / 100.0) if exit_price is not None else None
            except (TypeError, ValueError):
                exit_price = None
            avg_price = (position_summary or {}).get("avg_price")
            count = (position_summary or {}).get("count")
            pnl = None
            try:
                if avg_price is not None and count is not None and exit_price is not None:
                    pnl = round((float(exit_price) - float(avg_price)) * abs(float(count)), 4)
            except (TypeError, ValueError):
                pnl = None
            hold_minutes = None
            if isinstance(decision, dict):
                hold_minutes = decision.get("hold_minutes")
            send_telegram_message(
                f"🔵 EXIT\n"
                f"Ticker: {ticker}\n"
                f"PnL: {pnl}\n"
                f"Hold Time: {hold_minutes}"
            )
        self.live_exit_cooldowns[ticker] = time.time() + LIVE_EXIT_COOLDOWN_SECONDS
        return {"ok": True, "reason": decision_reason, "verification": verification, "response": response_data}

    def run_live_monitor_positions_pass(self, allow_real_exits=True):
        auth_smoke_result = self.run_live_auth_get_smoke_test()
        if not auth_smoke_result.get("ok"):
            print("[LIVE-BLOCK] AUTH_SMOKE_REQUIRED failed; aborting live submit")
            return {"ok": False, "reason": "auth_smoke_required_failed"}
        positions_result = self.get_live_position_summaries()
        if not positions_result.get("ok"):
            return {"ok": False, "reason": positions_result.get("reason")}
        positions = positions_result.get("positions") or []
        actionable_positions = 0
        exits_planned = 0
        for position_summary in positions:
            ticker = position_summary.get("ticker")
            count = position_summary.get("count")
            side = position_summary.get("side")
            avg_price = position_summary.get("avg_price")
            market_exposure = position_summary.get("market_exposure")
            if not ticker:
                continue
            raw_position = position_summary.get("raw_position") or {}
            zero_count = count is not None and abs(count) == 0
            zero_exposure_without_side = market_exposure is not None and abs(market_exposure) == 0 and side is None
            if zero_count or zero_exposure_without_side:
                self.remove_live_entry_metadata(ticker)
                print(
                    f"[LIVE-EXIT-POSITION-DEBUG] ticker={ticker} "
                    f"keys={sorted(list(raw_position.keys()))} "
                    f"normalized_count={count} normalized_side={side} "
                    f"normalized_avg_price={avg_price} normalized_market_exposure={market_exposure}"
                )
                print(f"[LIVE-EXIT-SKIP] ticker={ticker} reason=zero_position")
                continue
            self.upsert_live_entry_metadata_from_position(position_summary)
            if count is None or side is None:
                print(
                    f"[LIVE-EXIT-POSITION-DEBUG] ticker={ticker} "
                    f"keys={sorted(list(raw_position.keys()))} "
                    f"normalized_count={count} normalized_side={side} "
                    f"normalized_avg_price={avg_price} normalized_market_exposure={market_exposure}"
                )
                print(f"[LIVE-EXIT-SKIP] ticker={ticker} reason=missing_position_fields")
                continue
            if avg_price is None:
                print(
                    f"[LIVE-EXIT-POSITION-DEBUG] ticker={ticker} "
                    f"keys={sorted(list(raw_position.keys()))} "
                    f"normalized_count={count} normalized_side={side} "
                    f"normalized_avg_price={avg_price} normalized_market_exposure={market_exposure}"
                )
                print(f"[LIVE-EXIT-SKIP] ticker={ticker} reason=missing_avg_price")
                continue
            actionable_positions += 1
            quote_result = self.resolve_live_exit_quote_for_ticker(ticker)
            if not quote_result.get("ok"):
                print(f"[LIVE-EXIT-SKIP] ticker={ticker} reason=missing_quote_data")
                continue
            quote = quote_result.get("quote")
            decision = self.evaluate_exit_decision(position_summary, quote, now_ts=time.time())
            current_price = decision.get("expected_exit_price")
            print(
                f"[LIVE-EXIT-CHECK] ticker={ticker} side={side} count={count} avg_price={avg_price} "
                f"current_price={current_price} spread={decision.get('spread')} "
                f"hold_minutes={decision.get('hold_minutes')} "
                f"should_exit={decision.get('should_exit')} reason={decision.get('reason')}"
            )
            if not decision.get("should_exit"):
                print(f"[LIVE-POSITION-STATUS] ticker={ticker} state=holding")
                continue
            print(f"[LIVE-EXIT-AUTO] ticker={ticker} trigger_reason={decision.get('reason')}")
            send_telegram_message(
                f"⚠️ Exit Trigger | {ticker} | {decision.get('reason')}"
            )
            print(f"[LIVE-POSITION-STATUS] ticker={ticker} state=exit_pending")
            now_ts = time.time()
            cooldown_until = self.live_exit_cooldowns.get(ticker, 0)
            if cooldown_until > now_ts:
                print(f"[LIVE-MANAGER-SAFE] ticker={ticker} would_exit=yes reason={decision.get('reason')} action_blocked=exit_cooldown")
                continue
            payload = self.build_live_exit_payload(position_summary, quote)
            if payload is None:
                print(f"[LIVE-EXIT-SKIP] ticker={ticker} reason=missing_quote_data")
                continue
            exits_planned += 1
            display_price = payload.get("yes_price")
            if display_price is None:
                display_price = payload.get("no_price")
            print(
                f"[LIVE-EXIT-PLAN] ticker={ticker} action=sell side={payload.get('side')} "
                f"count={payload.get('count')} price={display_price} reason={decision.get('reason')}"
            )
            if not allow_real_exits:
                print(
                    f"[LIVE-MANAGER-SAFE] ticker={ticker} would_exit=yes "
                    f"reason={decision.get('reason')} action_blocked=continuous_exits_disabled"
                )
                continue
            print(f"[LIVE-POSITION-STATUS] ticker={ticker} state=exit_submitted")
            self.execute_live_exit_plan(
                ticker,
                payload,
                decision.get("reason"),
                approval_required=False,
                position_summary=position_summary,
                decision=decision,
            )
        return {
            "ok": True,
            "reason": "completed",
            "actionable_positions": actionable_positions,
            "exits_planned": exits_planned,
        }

    def manage_live_positions(self):
        try:
            while True:
                cycle_ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[LIVE-MANAGER] cycle_start ts={cycle_ts}")
                result = self.run_live_monitor_positions_pass(
                    allow_real_exits=True,
                )
                print(
                    f"[LIVE-MANAGER] cycle_end actionable_positions={result.get('actionable_positions', 0)} "
                    f"exits_planned={result.get('exits_planned', 0)}"
                )
                time.sleep(LIVE_POSITION_POLL_SECONDS)
        except KeyboardInterrupt:
            print("[LIVE-MANAGER] stopped")
            return {"ok": True, "reason": "stopped"}

    def submit_live_exit(self, position, q):
        """
        Submit or preview a live exit order for an existing live position.
        If DRY_RUN_LIVE_ORDERS is True, preview only.
        Return a result dict describing what happened.
        """
        ticker = position.get("ticker", "")
        payload = self.build_live_exit_payload(position, q)
        if payload is None:
            print(f"[LIVE-EXIT-SKIP] {ticker} reason=invalid_payload")
            return {"ok": False, "reason": "invalid_payload", "submitted": False}
        effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (
            globals().get("LIVE_ARMED", False) and globals().get("LIVE_ENV_CONFIRMED", False)
        )
        payload["is_dry_run"] = effective_live_dry_run
        if effective_live_dry_run:
            print(
                f"[LIVE-EXIT-PREVIEW] ticker={payload['ticker']} side={payload['side']} "
                f"action={payload['action']} count={payload['count']} "
                f"price={payload['price']} type={payload['type']} dry_run={payload['is_dry_run']}"
            )
            print(f"[LIVE-PAYLOAD] {payload}")
            return {"ok": True, "reason": "dry_run_only", "submitted": False, "payload": payload}
        if not self.has_live_order_api_path():
            print(f"[LIVE-EXIT-SKIP] {ticker} reason=no_order_api_path")
            return {"ok": False, "reason": "no_order_api_path", "submitted": False}
        try:
            raise RuntimeError("no_order_api_path")
        except Exception as exc:
            if str(exc) == "no_order_api_path":
                print(f"[LIVE-EXIT-SKIP] {ticker} reason=no_order_api_path")
                return {"ok": False, "reason": "no_order_api_path", "submitted": False}
            print(f"[LIVE-EXIT-ERROR] {ticker} error={exc}")
            return {"ok": False, "reason": "submit_error", "submitted": False, "payload": payload}

    def maybe_exit_live_positions(self, quotes_by_ticker):
        """
        Evaluate open live positions using existing exit logic and submit/preview exits when needed.
        Keep live bookkeeping separate from simulated positions.
        """
        global LIVE_ARMED
        kept = []
        now = time.time()
        for position in self.live_positions:
            if position.get("status", "open") != "open":
                continue
            ticker = position.get("ticker", "")
            q = quotes_by_ticker.get(ticker)
            if not q:
                kept.append(position)
                continue
            should_exit, reason, _pnl = self.should_exit_position(position, q, now=now)
            if not should_exit:
                kept.append(position)
                continue
            result = self.submit_live_exit(position, q)
            if result.get("reason") == "dry_run_only":
                kept.append(position)
                continue
            if result.get("submitted"):
                payload = result.get("payload") or {}
                position["status"] = "closed"
                position["closed_at"] = now
                position["exit_reason"] = reason
                position["exit_price"] = payload.get("price")
                if reason == "stop_loss":
                    LIVE_ARMED = False
                    print("[SAFEGUARD] live trading DISARMED after stop-loss")
                continue
            kept.append(position)
        self.live_positions = kept

    def log_sim_trade(self, position, exit_price, pnl, realized_pnl, reason, hold_seconds, market=None):
        """
        Append one completed simulated trade to the CSV file.
        Create the file with headers if it does not exist yet.
        """
        csv_path = self.trade_log_csv
        file_exists = os.path.exists(csv_path)
        headers = [
            "timestamp",
            "ticker",
            "side",
            "entry_price",
            "exit_price",
            "pnl_per_contract",
            "size",
            "realized_pnl",
            "reason",
            "hold_seconds",
            "yes_bid",
            "yes_ask",
            "last_price",
            "volume",
            "liquidity",
        ]
        row = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "ticker": position.get("ticker", ""),
            "side": position.get("side", "YES"),
            "entry_price": position.get("entry", 0.0),
            "exit_price": exit_price,
            "pnl_per_contract": pnl,
            "size": position.get("size", 1),
            "realized_pnl": realized_pnl,
            "reason": reason,
            "hold_seconds": hold_seconds,
            "yes_bid": getattr(market, "yes_bid", 0.0) if market is not None else 0.0,
            "yes_ask": getattr(market, "yes_ask", 0.0) if market is not None else 0.0,
            "last_price": getattr(market, "last_price", 0.0) if market is not None else 0.0,
            "volume": getattr(market, "volume", 0.0) if market is not None else 0.0,
            "liquidity": getattr(market, "liquidity", 0.0) if market is not None else 0.0,
        }
        with open(csv_path, "a", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def print_performance_summary(self):
        """
        Read the trade log CSV and print a concise performance summary.
        If the file does not exist or has no trades, print a safe message and return.
        """
        csv_path = getattr(self, "trade_log_csv", TRADE_LOG_CSV)
        if not os.path.exists(csv_path):
            print("[PERF] no trades logged yet")
            return

        trades = []
        with open(csv_path, newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                if not row:
                    continue
                trades.append(row)

        if not trades:
            print("[PERF] no trades logged yet")
            return

        total_trades = len(trades)
        winning_trades = 0
        losing_trades = 0
        total_realized_pnl = 0.0
        total_hold_seconds = 0.0
        reason_counts = {}
        best_trade = None
        worst_trade = None

        for trade in trades:
            realized = self.to_float(trade.get("realized_pnl"), 0.0)
            hold_seconds = self.to_float(trade.get("hold_seconds"), 0.0)
            reason = trade.get("reason") or "unknown"
            total_realized_pnl += realized
            total_hold_seconds += hold_seconds
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

            if realized > 0:
                winning_trades += 1
            elif realized < 0:
                losing_trades += 1

            if best_trade is None or realized > self.to_float(best_trade.get("realized_pnl"), 0.0):
                best_trade = trade
            if worst_trade is None or realized < self.to_float(worst_trade.get("realized_pnl"), 0.0):
                worst_trade = trade

        win_rate = (winning_trades / total_trades) * 100 if total_trades else 0.0
        average_realized_pnl = total_realized_pnl / total_trades if total_trades else 0.0
        average_hold_seconds = total_hold_seconds / total_trades if total_trades else 0.0

        print(
            f"[PERF] trades={total_trades} wins={winning_trades} "
            f"losses={losing_trades} win_rate={win_rate:.1f}%"
        )
        print(
            f"[PERF] total_realized={total_realized_pnl:+.4f} "
            f"avg_trade={average_realized_pnl:+.4f} avg_hold={average_hold_seconds:.1f}s"
        )
        exits = " ".join(f"{reason}={count}" for reason, count in sorted(reason_counts.items()))
        print(f"[PERF] exits {exits}")
        if best_trade is not None:
            print(
                f"[PERF] best={best_trade.get('ticker', '')} "
                f"{self.to_float(best_trade.get('realized_pnl'), 0.0):+.4f} "
                f"reason={best_trade.get('reason', 'unknown')}"
            )
        if worst_trade is not None:
            print(
                f"[PERF] worst={worst_trade.get('ticker', '')} "
                f"{self.to_float(worst_trade.get('realized_pnl'), 0.0):+.4f} "
                f"reason={worst_trade.get('reason', 'unknown')}"
            )

    def print_recent_performance_summary(self, last_n=10):
        """
        Read the trade log CSV and print a concise summary for only the most recent N trades.
        If the file does not exist or has no trades, print a safe message and return.
        """
        csv_path = getattr(self, "trade_log_csv", TRADE_LOG_CSV)
        if not os.path.exists(csv_path):
            print("[RECENT] no trades logged yet")
            return

        trades = []
        with open(csv_path, newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                if not row:
                    continue
                trades.append(row)

        if not trades:
            print("[RECENT] no trades logged yet")
            return

        recent_trades = trades[-last_n:]
        total_trades = len(recent_trades)
        winning_trades = 0
        losing_trades = 0
        total_realized_pnl = 0.0
        total_hold_seconds = 0.0
        reason_counts = {}
        best_trade = None
        worst_trade = None

        for trade in recent_trades:
            realized = self.to_float(trade.get("realized_pnl"), 0.0)
            hold_seconds = self.to_float(trade.get("hold_seconds"), 0.0)
            reason = trade.get("reason") or "unknown"
            total_realized_pnl += realized
            total_hold_seconds += hold_seconds
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

            if realized > 0:
                winning_trades += 1
            elif realized < 0:
                losing_trades += 1

            if best_trade is None or realized > self.to_float(best_trade.get("realized_pnl"), 0.0):
                best_trade = trade
            if worst_trade is None or realized < self.to_float(worst_trade.get("realized_pnl"), 0.0):
                worst_trade = trade

        win_rate = (winning_trades / total_trades) * 100 if total_trades else 0.0
        average_realized_pnl = total_realized_pnl / total_trades if total_trades else 0.0
        average_hold_seconds = total_hold_seconds / total_trades if total_trades else 0.0

        print(f"[RECENT] n={total_trades} wins={winning_trades} losses={losing_trades} win_rate={win_rate:.1f}%")
        print(
            f"[RECENT] total_realized={total_realized_pnl:+.4f} "
            f"avg_trade={average_realized_pnl:+.4f} avg_hold={average_hold_seconds:.1f}s"
        )
        exits = " ".join(f"{reason}={count}" for reason, count in sorted(reason_counts.items()))
        print(f"[RECENT] exits {exits}")
        if best_trade is not None:
            print(
                f"[RECENT] best={best_trade.get('ticker', '')} "
                f"{self.to_float(best_trade.get('realized_pnl'), 0.0):+.4f} "
                f"reason={best_trade.get('reason', 'unknown')}"
            )
        if worst_trade is not None:
            print(
                f"[RECENT] worst={worst_trade.get('ticker', '')} "
                f"{self.to_float(worst_trade.get('realized_pnl'), 0.0):+.4f} "
                f"reason={worst_trade.get('reason', 'unknown')}"
            )

    def print_group_performance_summary(self):
        """
        Read the trade log CSV and group trades by ticker prefix.
        Print performance metrics per group.
        """
        csv_path = getattr(self, "trade_log_csv", TRADE_LOG_CSV)
        if not os.path.exists(csv_path):
            print("[GROUP] no trades logged yet")
            return

        groups = {}
        with open(csv_path, newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                if not row:
                    continue
                ticker = row.get("ticker", "")
                group = ticker.split("-", 1)[0] if "-" in ticker else ticker
                stats = groups.setdefault(
                    group,
                    {"trades": 0, "wins": 0, "losses": 0, "total_realized_pnl": 0.0},
                )
                realized = self.to_float(row.get("realized_pnl"), 0.0)
                stats["trades"] += 1
                stats["total_realized_pnl"] += realized
                if realized > 0:
                    stats["wins"] += 1
                elif realized < 0:
                    stats["losses"] += 1

        if not groups:
            print("[GROUP] no trades logged yet")
            return

        ranked_groups = sorted(
            groups.items(),
            key=lambda item: item[1]["total_realized_pnl"],
            reverse=True,
        )
        for group, stats in ranked_groups:
            win_rate = (stats["wins"] / stats["trades"]) * 100 if stats["trades"] else 0.0
            print(
                f"[GROUP] {group} trades={stats['trades']} "
                f"pnl={stats['total_realized_pnl']:+.4f} win_rate={win_rate:.1f}%"
            )

    def print_group_weight_suggestions(self):
        """
        Read the trade log CSV, group trades by market group, and print suggested
        score multipliers based on historical performance.
        This is read-only analysis only. It must not change config automatically.
        """
        csv_path = getattr(self, "trade_log_csv", TRADE_LOG_CSV)
        if not os.path.exists(csv_path):
            print("[SUGGEST] no trades logged yet")
            return

        groups = {}
        with open(csv_path, newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                if not row:
                    continue
                ticker = row.get("ticker", "")
                group = self.get_market_group(ticker)
                stats = groups.setdefault(
                    group,
                    {"trades": 0, "wins": 0, "losses": 0, "total_realized_pnl": 0.0},
                )
                realized = self.to_float(row.get("realized_pnl"), 0.0)
                stats["trades"] += 1
                stats["total_realized_pnl"] += realized
                if realized > 0:
                    stats["wins"] += 1
                elif realized < 0:
                    stats["losses"] += 1

        if not groups:
            print("[SUGGEST] no trades logged yet")
            return

        suggestions = []
        for group, stats in groups.items():
            trades = stats["trades"]
            if trades < MIN_TRADES_FOR_GROUP_WEIGHT_SUGGESTION:
                continue
            avg_realized_pnl = stats["total_realized_pnl"] / trades if trades else 0.0
            win_rate = (stats["wins"] / trades) * 100 if trades else 0.0
            multiplier = 1.0
            if stats["total_realized_pnl"] > 0 and avg_realized_pnl > 0:
                multiplier = 1.0 + min(avg_realized_pnl * 2, MAX_SUGGESTED_GROUP_MULTIPLIER - 1.0)
            elif stats["total_realized_pnl"] < 0 and avg_realized_pnl < 0:
                multiplier = 1.0 - min(abs(avg_realized_pnl) * 2, 1.0 - MIN_SUGGESTED_GROUP_MULTIPLIER)
            multiplier = max(MIN_SUGGESTED_GROUP_MULTIPLIER, min(MAX_SUGGESTED_GROUP_MULTIPLIER, multiplier))
            suggestions.append((group, stats, avg_realized_pnl, win_rate, multiplier))

        if not suggestions:
            print("[SUGGEST] no groups with enough trades yet")
            return

        for group, stats, avg_realized_pnl, win_rate, multiplier in sorted(
            suggestions,
            key=lambda item: item[1]["total_realized_pnl"],
            reverse=True,
        ):
            print(
                f"[SUGGEST] {group} trades={stats['trades']} "
                f"pnl={stats['total_realized_pnl']:+.4f} avg={avg_realized_pnl:+.4f} "
                f"win_rate={win_rate:.1f}% multiplier={multiplier:.2f}"
            )

        print("\nGROUP_SCORE_WEIGHTS = {")
        for group, _, _, _, multiplier in sorted(suggestions, key=lambda item: item[0]):
            print(f'    "{group}": {multiplier:.2f},')
        print("}")

    def print_status(self, candidates=None):
        """
        Print a concise heartbeat/status summary for the current bot state.
        candidates is optional and may contain scored candidate tuples.
        """
        now = time.time()
        active_cooldowns = {
            ticker: expiry - now
            for ticker, expiry in self.market_cooldowns.items()
            if expiry > now
        }
        print(
            f"[STATUS] cycle={self.cycle_count} positions={len(self.positions)} "
            f"live_positions={len(self.live_positions)} "
            f"realized={self.realized_pnl:+.4f} losses={self.consecutive_losses} "
            f"halted={self.trading_halted} cooldowns={len(active_cooldowns)}"
        )

        for pos in self.positions:
            ticker = pos.get("ticker", "")
            entry = pos.get("entry", 0.0)
            size = pos.get("size", 1)
            opened_at = pos.get("opened_at")
            hold_seconds = int(now - opened_at) if opened_at else 0
            print(f"[POSITION] {ticker} entry={entry:.4f} size={size} hold={hold_seconds}s")

        for pos in self.live_positions:
            ticker = pos.get("ticker", "")
            entry = pos.get("entry", 0.0)
            size = pos.get("size", 1)
            opened_at = pos.get("opened_at")
            hold_seconds = int(now - opened_at) if opened_at else 0
            print(f"[LIVE-POSITION] {ticker} entry={entry:.4f} size={size} hold={hold_seconds}s")

        if active_cooldowns:
            items = []
            for ticker, remaining in list(sorted(active_cooldowns.items()))[:5]:
                items.append(f"{ticker}={int(remaining)}s")
            print(f"[COOLDOWN] {' '.join(items)}")

        if candidates:
            for score, q in candidates[:SHOW_TOP_CANDIDATES]:
                print(
                    f"[CANDIDATE] {getattr(q, 'ticker', '')} score={score:.2f} "
                    f"bid={getattr(q, 'yes_bid', 0.0):.2f} ask={getattr(q, 'yes_ask', 0.0):.2f} "
                    f"volume={getattr(q, 'volume', 0.0)} liquidity={getattr(q, 'liquidity', 0.0):.0f}"
                )

    def get_position_pnl(self, position, market):
        side = position.get("side", "YES")
        entry_price = position.get("entry", 0.0)
        if side == "YES":
            exit_price = market.yes_bid if market.yes_bid > 0 else market.last_price
            if exit_price <= 0:
                return None
            return exit_price - entry_price
        exit_price = market.no_bid if market.no_bid > 0 else market.last_price
        if exit_price <= 0:
            return None
        return entry_price - exit_price

    def should_exit_position(self, position, market, now=None):
        now = time.time() if now is None else now
        pnl = self.get_position_pnl(position, market)
        if pnl is None:
            return False, None, 0.0

        opened_at = position.get("opened_at", now)
        hold_seconds = now - opened_at
        tp_min = TAKE_PROFIT_MIN
        sl_min = STOP_LOSS_MIN
        if hold_seconds > 120:
            tp_min *= 0.8
            sl_min *= 0.8

        if pnl >= TAKE_PROFIT_MAX:
            return True, "take_profit", pnl
        if pnl >= tp_min and hold_seconds > 30:
            return True, "take_profit", pnl

        if pnl <= -STOP_LOSS_MAX:
            return True, "stop_loss", pnl
        if pnl <= -sl_min:
            if hold_seconds > 20:
                return True, "stop_loss", pnl
            liquidity = getattr(market, "liquidity", 0.0) or 0.0
            if liquidity < MIN_LIQUIDITY_EXIT:
                return True, "stop_loss", pnl

        if hold_seconds >= MAX_HOLD_SECONDS * 0.75 and pnl <= 0:
            return True, "max_hold", pnl
        if hold_seconds >= MAX_HOLD_SECONDS:
            return True, "max_hold", pnl
        return False, None, pnl

    def record_stop_loss_cooldown(self, market_ticker, now=None):
        now = time.time() if now is None else now
        self.market_cooldowns[market_ticker] = now + STOP_LOSS_COOLDOWN_SECONDS

    def is_on_cooldown(self, market_ticker, now=None):
        now = time.time() if now is None else now
        expiry = self.market_cooldowns.get(market_ticker, 0)
        if expiry <= now:
            self.market_cooldowns.pop(market_ticker, None)
            return False
        return True

    def maybe_exit_positions(self, quotes_by_ticker):
        kept = []
        now = time.time()

        for pos in self.positions:
            q = quotes_by_ticker.get(pos["ticker"])
            if not q:
                kept.append(pos)
                continue

            should_exit, reason, pnl = self.should_exit_position(pos, q, now=now)
            if not should_exit:
                kept.append(pos)
                continue

            current = q.yes_bid if q.yes_bid > 0 else q.last_price
            hold_seconds = int(now - pos.get("opened_at", now))
            size = pos.get("size", SIM_POSITION_SIZE)
            realized_trade_pnl = pnl * size
            self.realized_pnl += realized_trade_pnl
            if realized_trade_pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
            print(
                f"[EXIT] {pos['ticker']} {reason} entry={pos['entry']:.4f} "
                f"exit={current:.4f} pnl={pnl:+.4f} hold={hold_seconds}s "
                f"size={size} realized={realized_trade_pnl:+.4f} "
                f"total_realized={self.realized_pnl:+.4f} losses={self.consecutive_losses}"
            )
            self.log_sim_trade(
                pos,
                current,
                pnl,
                realized_trade_pnl,
                reason,
                hold_seconds,
                market=q,
            )

            if reason == "stop_loss":
                self.last_stop_time = now
                self.record_stop_loss_cooldown(pos["ticker"], now=now)
                remaining = int(self.market_cooldowns[pos["ticker"]] - now)
                print(f"[COOLDOWN] {pos['ticker']} until {remaining}s")
                continue

            continue

        self.positions = kept

    def run_once(self):
        self.cycle_count += 1
        self.maybe_send_telegram_heartbeat()
        markets = self.get_markets(limit=self.max_markets, status="open")
        quotes = [self.build_quote(m) for m in markets]

        print("\nRAW sample markets:")
        for m in markets[:3]:
            print(m)

        print("\nParsed sample quotes:")
        for q in quotes[:5]:
            print(
                f"{q.ticker} | yes_bid={q.yes_bid:.4f} | yes_ask={q.yes_ask:.4f} "
                f"| no_bid={q.no_bid:.4f} | no_ask={q.no_ask:.4f} "
                f"| last={q.last_price:.4f} | volume={q.volume} | liquidity={q.liquidity:.4f}"
            )

        active_quotes = [
            q for q in quotes
            if q.yes_bid > 0 or q.yes_ask > 0 or q.no_bid > 0 or q.no_ask > 0 or q.liquidity > 0
        ]
        print("\nTop active markets:")
        for q in active_quotes[:10]:
            print(
                f"{q.ticker} | yes_bid={q.yes_bid:.4f} | yes_ask={q.yes_ask:.4f} "
                f"| no_bid={q.no_bid:.4f} | no_ask={q.no_ask:.4f} "
                f"| last={q.last_price:.4f} | volume={q.volume} | liquidity={q.liquidity:.4f}"
            )

        canary_quotes = [q for q in quotes if 0 < q.yes_ask < 1]
        print("\nTop canary candidate markets:")
        for q in canary_quotes[:10]:
            print(
                f"{q.ticker} | yes_bid={q.yes_bid:.4f} | yes_ask={q.yes_ask:.4f} "
                f"| no_bid={q.no_bid:.4f} | no_ask={q.no_ask:.4f} "
                f"| last={q.last_price:.4f} | volume={q.volume} | liquidity={q.liquidity:.4f}"
            )
        quotes_by_ticker = {q.ticker: q for q in quotes}

        self.maybe_exit_positions(quotes_by_ticker)
        self.maybe_exit_live_positions(quotes_by_ticker)

        candidates = []
        for q in quotes:
            raw_score = self.sniper_score(q)
            if raw_score > 0:
                adjusted_score = self.apply_group_score_weight(raw_score, q)
                candidates.append((adjusted_score, q))

        candidates.sort(key=lambda x: x[0], reverse=True)

        if self.cycle_count % STATUS_EVERY_N_CYCLES == 0:
            self.print_status(candidates)

        print("\nTop sniper candidates:")
        for score, q in candidates[:5]:
            spread = q.yes_ask - q.yes_bid
            print(
                f"{q.ticker} | score={score:.2f} | yes_bid={q.yes_bid:.4f} "
                f"| yes_ask={q.yes_ask:.4f} | spread={spread:.4f} "
                f"| last={q.last_price:.4f} | volume={q.volume} | liquidity={q.liquidity:.4f}"
            )

        auto_live_ticker = None
        if globals().get("LIVE_MODE", False) and not globals().get("LIVE_TEST_TICKER"):
            auto_live_ticker, auto_live_reason = self.find_best_live_ticker_with_retries()
            if auto_live_ticker:
                print(f"[LIVE-SELECT] using_auto_ticker={auto_live_ticker}")
                print(f"[LIVE-SELECT] using_auto_reason={auto_live_reason}")

        if len(self.positions) >= MAX_TOTAL_EXPOSURE:
            return

        for score, q in candidates[:3]:
            if self.already_holding(q.ticker):
                continue
            if self.is_on_cooldown(q.ticker):
                remaining = int(self.market_cooldowns[q.ticker] - time.time())
                print(f"[SKIP] {q.ticker} on cooldown")
                print(f"[COOLDOWN] {q.ticker} until {remaining}s")
                continue
            passes_filters, reason = self.passes_entry_filters(q)
            if not passes_filters:
                print(f"[SKIP] {q.ticker} filter={reason}")
                continue
            allowed, reason = self.can_open_new_position(q)
            if not allowed:
                print(f"[SKIP] {q.ticker} risk={reason}")
                if reason in ("daily_loss_limit", "loss_streak_limit", "trading_halted"):
                    print(
                        f"[HALT] trading halted reason={reason} "
                        f"realized_pnl={self.realized_pnl:+.4f} losses={self.consecutive_losses}"
                    )
                    if self.last_safety_alert_reason != reason:
                        send_telegram_message(f"🔴 SAFETY HALT\nReason: {reason}")
                        self.last_safety_alert_reason = reason
                continue
            passes_group_filters, reason = self.passes_group_filters(q)
            if not passes_group_filters:
                print(f"[SKIP] {q.ticker} group={reason}")
                continue
            if not self.should_enter_yes(q):
                continue
            if not globals().get("LIVE_MODE", False):
                self.enter_sim_trade(q)
                break
            if auto_live_ticker and q.ticker != auto_live_ticker:
                continue
            result = self.submit_live_order(q)
            if result.get("submitted"):
                payload = result.get("payload") or {}
                self.record_live_position(
                    q,
                    size=payload.get("count"),
                    entry_price=payload.get("price"),
                )
                break
            if result.get("reason") == "dry_run_only":
                break
            continue


def main():
    global LIVE_MODE, LIVE_ARMED, LIVE_ENV_CONFIRMED, LIVE_TEST_TICKER
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--arm-live",
        action="store_true",
        help="Explicitly arm live trading; required together with --live for real live orders.",
    )
    parser.add_argument(
        "--live-smoke-test",
        action="store_true",
        help="Verify live API/auth/order path wiring without placing any real order.",
    )
    parser.add_argument(
        "--live-auth-get-smoke-test",
        action="store_true",
        help="Run a safe authenticated live GET smoke test without placing any order.",
    )
    parser.add_argument(
        "--live-preflight",
        action="store_true",
        help="Run live readiness checks and print a final READY / NOT READY summary.",
    )
    parser.add_argument(
        "--cancel-live-order-id",
        help="Manually cancel an existing live order by order_id.",
    )
    parser.add_argument(
        "--flatten-live-ticker",
        help="Manually reduce or close live exposure for one ticker.",
    )
    parser.add_argument(
        "--manage-live-positions",
        action="store_true",
        help="Continuously monitor live positions and evaluate exits.",
    )
    parser.add_argument(
        "--monitor-live-positions-once",
        action="store_true",
        help="Run one authenticated exit-monitor pass across current live positions.",
    )
    parser.add_argument(
        "--run-live-canary",
        action="store_true",
        help="Run one explicit live canary workflow: submit, verify, and optionally cancel.",
    )
    parser.add_argument(
        "--cancel-canary-after-verify",
        action="store_true",
        help="After successful verification, manually cancel the canary order.",
    )
    parser.add_argument(
        "--live-test-ticker",
        help="Explicit ticker allowed for the first tiny real live canary order.",
    )
    parser.add_argument(
        "--preview-live-ticker",
        action="store_true",
        help="Preview the auto-selected live ticker without submitting anything.",
    )
    parser.add_argument(
        "--quote-quality-report",
        nargs="?",
        const=10,
        type=int,
        help="Print a compact live quote-quality sample report.",
    )
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--recent-summary", nargs="?", const=10, type=int)
    parser.add_argument("--group-summary", action="store_true")
    parser.add_argument("--suggest-group-weights", action="store_true")
    args = parser.parse_args()

    LIVE_MODE = bool(args.live)
    LIVE_ARMED = bool(args.live and args.arm_live)
    LIVE_ENV_CONFIRMED = (os.environ.get("KALSHI_ALLOW_LIVE", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    LIVE_TEST_TICKER = args.live_test_ticker
    effective_live_dry_run = DRY_RUN_LIVE_ORDERS or not (LIVE_ARMED and LIVE_ENV_CONFIRMED)

    bot = KalshiSniperBot()
    send_telegram_message("🚀 Kalshi bot started and running on server")
    print("[TELEGRAM] sent=startup")
    live_related_mode = any(
        (
            args.live,
            args.live_smoke_test,
            args.live_auth_get_smoke_test,
            args.live_preflight,
            args.run_live_canary,
            args.preview_live_ticker,
            bool(args.quote_quality_report),
            args.monitor_live_positions_once,
            args.manage_live_positions,
            bool(args.flatten_live_ticker),
            bool(args.cancel_live_order_id),
        )
    )
    if live_related_mode:
        api_key = bot.get_live_api_key()
        api_key_source = bot.get_live_api_key_source()
        base_url = bot.get_live_base_url()
        base_mode = "production" if "api.elections.kalshi.com" in base_url else "demo"
        print(
            f"[LIVE-CONFIG] api_key_env={api_key_source or 'unset'} "
            f"key_id_prefix={api_key[:6]} key_id_len={len(api_key)} "
            f"private_key_path={bot.get_live_private_key_path()} base_url_mode={base_mode}"
        )
        bot.print_recent_live_audit_entries()
        bot.print_live_startup_checklist(args=args)
    if args.live_preflight:
        bot.run_live_preflight(args=args)
        return
    if args.live_smoke_test:
        bot.run_live_smoke_test()
        return
    if args.live_auth_get_smoke_test:
        bot.run_live_auth_get_smoke_test()
        return
    if args.cancel_live_order_id:
        bot.cancel_live_order(args.cancel_live_order_id)
        return
    if args.flatten_live_ticker:
        bot.run_live_flatten_ticker(args.flatten_live_ticker)
        return
    if args.monitor_live_positions_once:
        bot.run_live_monitor_positions_once()
        return
    if args.manage_live_positions:
        bot.manage_live_positions()
        return
    if args.preview_live_ticker:
        bot.preview_live_ticker_selection()
        return
    if args.quote_quality_report:
        bot.print_live_quote_quality_report(sample_n=args.quote_quality_report)
        return
    if args.run_live_canary:
        bot.run_live_canary_workflow(cancel_after_verify=args.cancel_canary_after_verify)
        return
    if args.live and args.arm_live and LIVE_ENV_CONFIRMED:
        print("[SAFEGUARD] live trading ARMED")
    elif args.live and args.arm_live and not LIVE_ENV_CONFIRMED:
        print("[SAFEGUARD] live trading env confirmation missing")
    elif args.live and not args.arm_live:
        print("[SAFEGUARD] live trading NOT armed")
    if args.live:
        print(
            f"[LIVE-CONFIG] mode={'LIVE' if LIVE_MODE else 'SIM'} "
            f"armed={LIVE_ARMED} env_confirmed={LIVE_ENV_CONFIRMED} dry_run={effective_live_dry_run}"
        )
        print(
            f"[LIVE-CONFIG] rate_limit={LIVE_ORDER_RATE_LIMIT}/{LIVE_ORDER_RATE_WINDOW_SEC}s "
            f"max_order_size={MAX_LIVE_ORDER_SIZE} "
            f"max_daily_live_trades={MAX_LIVE_TRADES_PER_DAY}"
        )

    if args.suggest_group_weights:
        bot.print_group_weight_suggestions()
        return
    if args.group_summary:
        bot.print_group_performance_summary()
        return
    if args.recent_summary is not None:
        bot.print_recent_performance_summary(last_n=args.recent_summary)
        return
    if args.summary:
        bot.print_performance_summary()
        return
    print("Starting Kalshi sniper bot in SIM mode...\n")

    while True:
        try:
            bot.run_once()
            print(f"Open positions: {len(bot.positions)}")
            print("-" * 70)
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as e:
            print(f"Error: {e}")
            bot.last_error_message = str(e)[:200]
            send_telegram_message(f"🚨 ERROR\n{str(e)[:200]}")
            time.sleep(5)


if __name__ == "__main__":
    main()
