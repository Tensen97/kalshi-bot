import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from runtime.paths import runtime_memory_file, runtime_state_file
except Exception:
    runtime_memory_file = None
    runtime_state_file = None


REALM_XVIII_ENABLE = (os.environ.get("REALM_XVIII_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XVIII_SHADOW_ONLY = (os.environ.get("REALM_XVIII_SHADOW_ONLY", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
REALM_XVIII_MAX_TOTAL_EXPOSURE_DOLLARS = float(os.environ.get("REALM_XVIII_MAX_TOTAL_EXPOSURE_DOLLARS", "25") or 25)
REALM_XVIII_MAX_FAMILY_EXPOSURE_DOLLARS = float(os.environ.get("REALM_XVIII_MAX_FAMILY_EXPOSURE_DOLLARS", "10") or 10)
REALM_XVIII_MAX_CORRELATED_EXPOSURE_DOLLARS = float(os.environ.get("REALM_XVIII_MAX_CORRELATED_EXPOSURE_DOLLARS", "15") or 15)
REALM_XVIII_MAX_DAILY_RISK_DOLLARS = float(os.environ.get("REALM_XVIII_MAX_DAILY_RISK_DOLLARS", "10") or 10)
REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE = float(os.environ.get("REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE", "0.60") or 0.60)
REALM_XVIII_MAX_SCORE_PENALTY = float(os.environ.get("REALM_XVIII_MAX_SCORE_PENALTY", "0.07") or 0.07)
REALM_XVIII_ALLOW_BLOCKS = (os.environ.get("REALM_XVIII_ALLOW_BLOCKS", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XVIII_STATE_FILE = os.environ.get(
    "REALM_XVIII_STATE_FILE",
    runtime_memory_file("realm_xviii_portfolio_brain.sqlite3") if callable(runtime_memory_file) else "/opt/kalshi-bot/runtime/memory/realm_xviii_portfolio_brain.sqlite3",
)
REALM_XVIII_HISTORY_LIMIT = int(float(os.environ.get("REALM_XVIII_HISTORY_LIMIT", "1000") or 1000))


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clamp(value, minimum=0.0, maximum=1.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _round(value, digits=4, default=None):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def _stable_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _family_to_category(family):
    family = str(family or "").strip().upper()
    if not family:
        return "unknown"
    if family.startswith(("KXBTC", "KXETH", "KXSOL", "KXCRYPTO")):
        return "crypto"
    if family.startswith(("KXNBA", "KXMLB", "KXNHL", "KXNFL", "KXATP", "KXTENN")):
        return "sports"
    if family.startswith(("KXTEMP", "KXRAIN", "KXSNOW")):
        return "weather"
    return "other"


def _family_prefix(family):
    family = str(family or "").strip().upper()
    if not family:
        return "UNKNOWN"
    return family.split("-", 1)[0]


def _correlation_group(family, category):
    family = _family_prefix(family)
    category = str(category or _family_to_category(family)).strip().lower()
    if category == "crypto":
        return "crypto_cluster"
    if category == "sports":
        if family.startswith("KXNBA"):
            return "nba_cluster"
        if family.startswith("KXMLB"):
            return "mlb_cluster"
        if family.startswith("KXNHL"):
            return "nhl_cluster"
        if family.startswith("KXATP") or family.startswith("KXTENN"):
            return "tennis_cluster"
        return "sports_cluster"
    if category == "weather":
        return "weather_cluster"
    return "other_cluster"


@dataclass
class PortfolioState:
    total_open_positions: int = 0
    total_exposure_dollars: float = 0.0
    available_balance: float | None = None
    realized_daily_pnl: float = 0.0
    unrealized_pnl: float | None = None
    active_tickers: list | None = None
    active_families: list | None = None
    exposure_by_family: dict | None = None
    exposure_by_category: dict | None = None
    exposure_by_side: dict | None = None
    correlated_exposure_groups: dict | None = None
    max_daily_risk_used: float = 0.0
    remaining_risk_budget: float = 0.0
    portfolio_health_score: float = 1.0
    portfolio_mode: str = "normal"
    recommendation: str = "continue normal trading"
    risk_of_ruin_guard: str = "clear"
    drawdown_pressure: float = 0.0
    loss_streak: int = 0
    strategy_quality_score: float | None = None
    volatility_regime: str = "unknown"
    strategy_quality_source: str = "unknown"
    family_concentration: float = 0.0
    correlated_concentration: float = 0.0
    health_components: dict | None = None
    candidate_context: dict | None = None

    def to_dict(self):
        return {
            "total_open_positions": self.total_open_positions,
            "total_exposure_dollars": _round(self.total_exposure_dollars, 4, 0.0),
            "available_balance": _round(self.available_balance, 4, None) if self.available_balance is not None else None,
            "realized_daily_pnl": _round(self.realized_daily_pnl, 4, 0.0),
            "unrealized_pnl": _round(self.unrealized_pnl, 4, None) if self.unrealized_pnl is not None else None,
            "active_tickers": list(self.active_tickers or []),
            "active_families": list(self.active_families or []),
            "exposure_by_family": dict(self.exposure_by_family or {}),
            "exposure_by_category": dict(self.exposure_by_category or {}),
            "exposure_by_side": dict(self.exposure_by_side or {}),
            "correlated_exposure_groups": dict(self.correlated_exposure_groups or {}),
            "max_daily_risk_used": _round(self.max_daily_risk_used, 4, 0.0),
            "remaining_risk_budget": _round(self.remaining_risk_budget, 4, 0.0),
            "portfolio_health_score": _round(self.portfolio_health_score, 4, 0.0),
            "portfolio_mode": self.portfolio_mode,
            "recommendation": self.recommendation,
            "risk_of_ruin_guard": self.risk_of_ruin_guard,
            "drawdown_pressure": _round(self.drawdown_pressure, 4, 0.0),
            "loss_streak": int(self.loss_streak or 0),
            "strategy_quality_score": _round(self.strategy_quality_score, 4, None) if self.strategy_quality_score is not None else None,
            "volatility_regime": self.volatility_regime,
            "strategy_quality_source": self.strategy_quality_source,
            "family_concentration": _round(self.family_concentration, 4, 0.0),
            "correlated_concentration": _round(self.correlated_concentration, 4, 0.0),
            "health_components": dict(self.health_components or {}),
            "candidate_context": dict(self.candidate_context or {}),
        }


class PortfolioBrain:
    def __init__(
        self,
        state_file=REALM_XVIII_STATE_FILE,
        enabled=REALM_XVIII_ENABLE,
        shadow_only=REALM_XVIII_SHADOW_ONLY,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.shadow_only = bool(shadow_only)
        self.last_state = PortfolioState().to_dict()
        self.last_report = {}
        self.last_snapshot_ts = 0.0
        self._ensure_store()

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _connect(self):
        _ensure_dir(self.state_file)
        conn = sqlite3.connect(self.state_file, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_store(self):
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        health_score REAL,
                        mode TEXT,
                        recommendation TEXT,
                        portfolio_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            return True
        except Exception as exc:
            self._log("[REALM-XVIII-ERROR]", reason=f"store_init_{exc.__class__.__name__}")
            return False

    def _append_telemetry(self, event_type, payload):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO portfolio_telemetry (event_type, created_at, payload_json) VALUES (?, ?, ?)",
                    (event_type, _now_iso(), _stable_json(payload if isinstance(payload, dict) else {"payload": payload})),
                )
                conn.commit()
        except Exception:
            pass

    def _save_report(self, state, source="live"):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO portfolio_reports (created_at, source, health_score, mode, recommendation, portfolio_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _now_iso(),
                        source,
                        _safe_float(state.get("portfolio_health_score"), None),
                        str(state.get("portfolio_mode") or "normal"),
                        str(state.get("recommendation") or "continue normal trading"),
                        _stable_json(state),
                    ),
                )
                conn.commit()
        except Exception as exc:
            self._log("[REALM-XVIII-ERROR]", reason=f"report_save_{exc.__class__.__name__}")

    def _normalize_position(self, position):
        position = position if isinstance(position, dict) else {}
        ticker = str(position.get("ticker") or "").strip()
        family = str(position.get("family") or position.get("market_family") or "").strip().upper()
        if not family and ticker:
            family = ticker.split("-", 1)[0].upper()
        category = str(position.get("category") or position.get("market_category") or _family_to_category(family)).strip().lower()
        side = str(position.get("side") or "yes").strip().lower()
        count = _safe_float(position.get("count") or position.get("size") or 0.0, 0.0) or 0.0
        avg_price = _safe_float(position.get("avg_price") or position.get("entry_price"), None)
        market_exposure = _safe_float(position.get("market_exposure") or position.get("market_exposure_dollars"), None)
        if market_exposure is None and avg_price is not None and count:
            market_exposure = abs(float(count)) * float(avg_price)
        if market_exposure is None:
            market_exposure = abs(count)
        entry_context = position.get("entry_context") if isinstance(position.get("entry_context"), dict) else {}
        if not family and entry_context:
            family = str(entry_context.get("family") or entry_context.get("market_family") or "").strip().upper() or family
        if not category and entry_context:
            category = str(entry_context.get("category") or "unknown").strip().lower()
        return {
            "ticker": ticker,
            "family": family or "UNKNOWN",
            "category": category or "unknown",
            "side": side if side in {"yes", "no"} else "yes",
            "count": abs(float(count)),
            "avg_price": avg_price,
            "market_exposure": abs(float(market_exposure or 0.0)),
            "entry_context": entry_context,
        }

    def _extract_relationship_groups(self, realm_xvi_summary=None):
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        relationships = realm_xvi_summary.get("relationships") if isinstance(realm_xvi_summary.get("relationships"), dict) else {}
        groups = defaultdict(list)
        for key, record in relationships.items():
            if not isinstance(record, dict):
                continue
            positive_strength = _safe_float(record.get("positive_strength"), 0.0) or 0.0
            confidence = _safe_float(record.get("confidence"), 0.0) or 0.0
            if positive_strength < 0.45 or confidence < 0.35:
                continue
            family_a = str(record.get("family_a") or "").strip().upper()
            family_b = str(record.get("family_b") or "").strip().upper()
            group_name = "correlated_" + key.replace("|", "_")
            groups[group_name].extend([family_a, family_b])
        return {group: sorted({family for family in families if family}) for group, families in groups.items()}

    def _compute_health(self, portfolio_state):
        exposure_ratio = min(1.5, (portfolio_state["total_exposure_dollars"] / max(REALM_XVIII_MAX_TOTAL_EXPOSURE_DOLLARS, 1.0)))
        family_concentration = portfolio_state.get("family_concentration", 0.0)
        correlated_concentration = portfolio_state.get("correlated_concentration", 0.0)
        drawdown_pressure = portfolio_state.get("drawdown_pressure", 0.0)
        loss_streak = int(portfolio_state.get("loss_streak", 0) or 0)
        realized_pnl = _safe_float(portfolio_state.get("realized_daily_pnl"), 0.0) or 0.0
        unrealized_pnl = _safe_float(portfolio_state.get("unrealized_pnl"), 0.0) or 0.0
        volatility_regime = str(portfolio_state.get("volatility_regime") or "unknown").strip().lower()
        strategy_quality = _safe_float(portfolio_state.get("strategy_quality_score"), 0.5) if portfolio_state.get("strategy_quality_score") is not None else 0.5
        strategy_bonus = max(0.0, strategy_quality - 0.55) * 0.15
        pnl_bonus = max(0.0, realized_pnl + unrealized_pnl) / max(REALM_XVIII_MAX_DAILY_RISK_DOLLARS, 1.0) * 0.20
        pnl_penalty = max(0.0, -(realized_pnl + unrealized_pnl)) / max(REALM_XVIII_MAX_DAILY_RISK_DOLLARS, 1.0) * 0.25
        volatility_penalty = 0.10 if volatility_regime in {"high_volatility", "unstable_spread_conditions", "choppy"} else 0.0
        score = 1.0
        score -= min(0.30, exposure_ratio * 0.25)
        score -= min(0.20, family_concentration * 0.20)
        score -= min(0.20, correlated_concentration * 0.20)
        score -= min(0.20, drawdown_pressure * 0.20)
        score -= min(0.15, loss_streak * 0.04)
        score -= min(0.20, pnl_penalty)
        score -= volatility_penalty
        score += min(0.10, pnl_bonus)
        score += min(0.08, strategy_bonus)
        score = _clamp(score, 0.0, 1.0)
        if score >= 0.85:
            mode = "growth"
            recommendation = "continue normal trading"
        elif score >= 0.70:
            mode = "normal"
            recommendation = "continue normal trading"
        elif score >= 0.55:
            mode = "cautious"
            recommendation = "reduce aggression"
        elif score >= 0.40:
            mode = "defensive"
            recommendation = "reduce exposure and avoid concentration"
        else:
            mode = "risk_off"
            recommendation = "skip new risk until portfolio recovers"
        return score, mode, recommendation

    def _derive_daily_objective(self, portfolio_state):
        realized = _safe_float(portfolio_state.get("realized_daily_pnl"), 0.0) or 0.0
        loss_streak = int(portfolio_state.get("loss_streak", 0) or 0)
        total_exposure = _safe_float(portfolio_state.get("total_exposure_dollars"), 0.0) or 0.0
        risk_used = min(1.5 * REALM_XVIII_MAX_DAILY_RISK_DOLLARS, max(0.0, abs(min(0.0, realized)) + total_exposure * 0.20))
        remaining = max(0.0, REALM_XVIII_MAX_DAILY_RISK_DOLLARS - risk_used)
        portfolio_state["max_daily_risk_used"] = risk_used
        portfolio_state["remaining_risk_budget"] = remaining
        portfolio_state["drawdown_pressure"] = min(1.0, max(0.0, abs(min(0.0, realized)) / max(REALM_XVIII_MAX_DAILY_RISK_DOLLARS, 1.0)))
        portfolio_state["loss_streak"] = loss_streak
        return portfolio_state

    def analyze_portfolio(
        self,
        live_positions=None,
        position_summaries=None,
        realized_pnl_today=0.0,
        unrealized_pnl=None,
        available_balance=None,
        realm_xvi_summary=None,
        realm_xvii_summary=None,
        candidate_context=None,
        source="live",
    ):
        if not self.enabled:
            self._log("[REALM-XVIII-DEGRADED]", reason="disabled")
            state = PortfolioState(portfolio_mode="normal", recommendation="feature disabled").to_dict()
            self.last_state = state
            return state
        live_positions = [self._normalize_position(position) for position in (live_positions or []) if isinstance(position, dict)]
        position_summaries = [self._normalize_position(position) for position in (position_summaries or []) if isinstance(position, dict)]
        positions = live_positions or position_summaries
        if not positions:
            positions = []
        total_exposure = sum(position.get("market_exposure", 0.0) or 0.0 for position in positions)
        active_tickers = [position.get("ticker") for position in positions if position.get("ticker")]
        active_families = [position.get("family") for position in positions if position.get("family")]
        family_exposure = defaultdict(float)
        category_exposure = defaultdict(float)
        side_exposure = defaultdict(float)
        family_counts = defaultdict(int)
        correlated_groups = defaultdict(float)
        for position in positions:
            family = position.get("family") or "UNKNOWN"
            category = position.get("category") or _family_to_category(family)
            side = position.get("side") or "yes"
            exposure = _safe_float(position.get("market_exposure"), 0.0) or 0.0
            family_exposure[family] += exposure
            category_exposure[category] += exposure
            side_exposure[side] += exposure
            family_counts[family] += 1
        relationships = realm_xvi_summary.get("relationships") if isinstance(realm_xvi_summary, dict) else {}
        for record in (relationships or {}).values():
            if not isinstance(record, dict):
                continue
            positive_strength = _safe_float(record.get("positive_strength"), 0.0) or 0.0
            confidence = _safe_float(record.get("confidence"), 0.0) or 0.0
            if positive_strength < 0.45 or confidence < 0.35:
                continue
            family_a = str(record.get("family_a") or "").strip().upper()
            family_b = str(record.get("family_b") or "").strip().upper()
            if not family_a or not family_b:
                continue
            combined = family_exposure.get(family_a, 0.0) + family_exposure.get(family_b, 0.0)
            group_name = f"{family_a}|{family_b}"
            correlated_groups[group_name] = combined
        if not correlated_groups:
            derived_groups = self._extract_relationship_groups(realm_xvi_summary)
            for group_name, families in derived_groups.items():
                correlated_groups[group_name] = sum(family_exposure.get(family, 0.0) for family in families)
        family_concentration = max(family_exposure.values() or [0.0]) / max(total_exposure, 1.0)
        correlated_concentration = max(correlated_groups.values() or [0.0]) / max(total_exposure, 1.0)
        volatility_regime = str((realm_xvi_summary or {}).get("dominant_regime") or (realm_xvi_summary or {}).get("regime_label") or "unknown").strip().lower()
        strategy_quality_score = _safe_float((realm_xvii_summary or {}).get("baseline", {}).get("overall_evolution_score"), None)
        strategy_quality_source = "realm_xvii" if strategy_quality_score is not None else "unknown"
        portfolio_state = PortfolioState(
            total_open_positions=len(positions),
            total_exposure_dollars=total_exposure,
            available_balance=available_balance,
            realized_daily_pnl=_safe_float(realized_pnl_today, 0.0) or 0.0,
            unrealized_pnl=_safe_float(unrealized_pnl, None) if unrealized_pnl is not None else None,
            active_tickers=sorted({ticker for ticker in active_tickers if ticker}),
            active_families=sorted({family for family in active_families if family}),
            exposure_by_family=dict(sorted(family_exposure.items(), key=lambda item: item[1], reverse=True)),
            exposure_by_category=dict(sorted(category_exposure.items(), key=lambda item: item[1], reverse=True)),
            exposure_by_side=dict(sorted(side_exposure.items(), key=lambda item: item[1], reverse=True)),
            correlated_exposure_groups=dict(sorted(correlated_groups.items(), key=lambda item: item[1], reverse=True)),
            volatility_regime=volatility_regime,
            strategy_quality_score=strategy_quality_score,
            strategy_quality_source=strategy_quality_source,
            family_concentration=family_concentration,
            correlated_concentration=correlated_concentration,
            candidate_context=candidate_context if isinstance(candidate_context, dict) else {},
        ).to_dict()
        portfolio_state = self._derive_daily_objective(portfolio_state)
        portfolio_state["health_components"] = {
            "exposure_ratio": _round(total_exposure / max(REALM_XVIII_MAX_TOTAL_EXPOSURE_DOLLARS, 1.0), 4, 0.0),
            "family_concentration": _round(family_concentration, 4, 0.0),
            "correlated_concentration": _round(correlated_concentration, 4, 0.0),
            "realized_pnl": _round(realized_pnl_today, 4, 0.0),
            "unrealized_pnl": _round(unrealized_pnl, 4, 0.0) if unrealized_pnl is not None else None,
            "volatility_regime": volatility_regime,
            "strategy_quality_source": strategy_quality_source,
        }
        health_score, mode, recommendation = self._compute_health(portfolio_state)
        portfolio_state["portfolio_health_score"] = health_score
        portfolio_state["portfolio_mode"] = mode
        portfolio_state["recommendation"] = recommendation
        portfolio_state["risk_of_ruin_guard"] = "active" if health_score < REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE else "clear"
        self.last_state = portfolio_state
        self.last_snapshot_ts = time.time()
        self._append_telemetry("portfolio", portfolio_state)
        self._save_report(portfolio_state, source=source)
        self._log(
            "[REALM-XVIII-PORTFOLIO]",
            total_open_positions=portfolio_state["total_open_positions"],
            total_exposure_dollars=_round(portfolio_state["total_exposure_dollars"], 4, 0.0),
            realized_daily_pnl=_round(portfolio_state["realized_daily_pnl"], 4, 0.0),
            risk_budget_used=_round(portfolio_state["max_daily_risk_used"], 4, 0.0),
            remaining_risk_budget=_round(portfolio_state["remaining_risk_budget"], 4, 0.0),
            mode=mode,
        )
        self._log(
            "[REALM-XVIII-EXPOSURE]",
            exposure_by_family=list(portfolio_state["exposure_by_family"].items())[:5],
            exposure_by_category=list(portfolio_state["exposure_by_category"].items())[:5],
            exposure_by_side=list(portfolio_state["exposure_by_side"].items())[:5],
            correlated_groups=list(portfolio_state["correlated_exposure_groups"].items())[:5],
        )
        self._log(
            "[REALM-XVIII-HEALTH-SCORE]",
            score=portfolio_state["portfolio_health_score"],
            family_concentration=_round(family_concentration, 4, 0.0),
            correlated_concentration=_round(correlated_concentration, 4, 0.0),
            drawdown_pressure=portfolio_state.get("drawdown_pressure"),
            volatility_regime=volatility_regime,
        )
        if health_score < 0.40:
            self._log("[REALM-XVIII-RISK-OFF]", score=health_score, reason="health_below_0_40")
        elif health_score < 0.55:
            self._log("[REALM-XVIII-DEFENSIVE]", score=health_score, reason="health_below_0_55")
        self._log(
            "[REALM-XVIII-DAILY-OBJECTIVE]",
            realized_pnl=_round(portfolio_state["realized_daily_pnl"], 4, 0.0),
            risk_budget_used=_round(portfolio_state["max_daily_risk_used"], 4, 0.0),
            remaining_risk_budget=_round(portfolio_state["remaining_risk_budget"], 4, 0.0),
            loss_streak=portfolio_state.get("loss_streak", 0),
        )
        self._log(
            "[REALM-XVIII-DRAWDOWN]",
            drawdown_pressure=portfolio_state.get("drawdown_pressure"),
            unrealized_pnl=portfolio_state.get("unrealized_pnl"),
            risk_of_ruin_guard=portfolio_state.get("risk_of_ruin_guard"),
        )
        self._log(
            "[REALM-XVIII-CAPITAL-PRESERVATION]",
            portfolio_mode=mode,
            recommendation=recommendation,
            health_score=health_score,
        )
        family_warning = any(value >= REALM_XVIII_MAX_FAMILY_EXPOSURE_DOLLARS for value in family_exposure.values())
        if family_warning:
            self._log(
                "[REALM-XVIII-FAMILY-CONCENTRATION]",
                max_family_exposure=_round(max(family_exposure.values()), 4, 0.0) if family_exposure else 0.0,
                limit=REALM_XVIII_MAX_FAMILY_EXPOSURE_DOLLARS,
            )
            self._log(
                "[REALM-XVIII-CONCENTRATION-WARNING]",
                warning="family_exposure_high",
                recommendation="reduce_family_concentration",
            )
        if correlated_concentration * total_exposure >= REALM_XVIII_MAX_CORRELATED_EXPOSURE_DOLLARS:
            self._log(
                "[REALM-XVIII-CORRELATED-EXPOSURE]",
                correlated_exposure=_round(max(correlated_groups.values()) if correlated_groups else 0.0, 4, 0.0),
                limit=REALM_XVIII_MAX_CORRELATED_EXPOSURE_DOLLARS,
            )
            self._log(
                "[REALM-XVIII-CORRELATION-RISK]",
                warning="correlated_exposure_high",
                recommendation="reduce_related_positions",
            )
        if portfolio_state["remaining_risk_budget"] <= 0:
            self._log("[REALM-XVIII-RISK-OFF]", score=health_score, reason="risk_budget_exhausted")
        self._log(
            "[REALM-XVIII-ALLOCATION]",
            mode=mode,
            recommendation=recommendation,
            shadow_only="yes" if self.shadow_only else "no",
            allow_blocks="yes" if REALM_XVIII_ALLOW_BLOCKS else "no",
        )
        size_multiplier = 1.0
        if mode == "cautious":
            size_multiplier = 0.75
        elif mode == "defensive":
            size_multiplier = 0.50
        elif mode == "risk_off":
            size_multiplier = 0.0
        self._log(
            "[REALM-XVIII-SIZE-RECOMMENDATION]",
            size_multiplier=_round(size_multiplier, 4, 1.0),
            recommendation=recommendation,
            portfolio_health_score=health_score,
        )
        self._log(
            "[REALM-XVIII-SHADOW-SIZING]",
            shadow_only="yes" if self.shadow_only else "no",
            size_multiplier=_round(size_multiplier, 4, 1.0),
            note="advisory_only" if self.shadow_only else "soft_guidance_only",
        )
        if not self.shadow_only and REALM_XVIII_ALLOW_BLOCKS and health_score < REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE:
            self._log(
                "[REALM-XVIII-SHADOW-BLOCK]",
                reason="portfolio_health_below_threshold",
                threshold=REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE,
                health_score=health_score,
            )
            portfolio_state["would_block"] = True
            portfolio_state["block_reason"] = "portfolio_health_below_threshold"
        else:
            portfolio_state["would_block"] = False
            portfolio_state["block_reason"] = None
        if health_score < REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE:
            self._log(
                "[REALM-XVIII-PORTFOLIO-GATE]",
                health_score=health_score,
                threshold=REALM_XVIII_MIN_PORTFOLIO_HEALTH_SCORE,
                gate="soft_warn" if self.shadow_only else "soft_block_candidate",
            )
        return portfolio_state

    def score_strategy_candidate(self, strategy_metrics=None, portfolio_state=None, realm_xvi_summary=None, realm_xvii_summary=None):
        strategy_metrics = strategy_metrics if isinstance(strategy_metrics, dict) else {}
        portfolio_state = portfolio_state if isinstance(portfolio_state, dict) else self.last_state or {}
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        realm_xvii_summary = realm_xvii_summary if isinstance(realm_xvii_summary, dict) else {}
        base_score = _safe_float(
            strategy_metrics.get("overall_evolution_score")
            or strategy_metrics.get("risk_adjusted_candidate_score")
            or strategy_metrics.get("portfolio_adjusted_strategy_score"),
            0.0,
        ) or 0.0
        total_exposure = _safe_float(portfolio_state.get("total_exposure_dollars"), 0.0) or 0.0
        family_concentration = _safe_float(portfolio_state.get("family_concentration"), 0.0) or 0.0
        correlated_concentration = _safe_float(portfolio_state.get("correlated_concentration"), 0.0) or 0.0
        health_score = _safe_float(portfolio_state.get("portfolio_health_score"), 1.0) or 1.0
        mode = str(portfolio_state.get("portfolio_mode") or "normal").strip().lower()
        realized_pnl = _safe_float(portfolio_state.get("realized_daily_pnl"), 0.0) or 0.0
        loss_streak = int(portfolio_state.get("loss_streak", 0) or 0)
        volatility_regime = str(portfolio_state.get("volatility_regime") or "unknown").strip().lower()
        regime_penalty = 0.0
        if mode == "defensive":
            regime_penalty += 0.025
        elif mode == "risk_off":
            regime_penalty += 0.045
        elif mode == "cautious":
            regime_penalty += 0.015
        if volatility_regime in {"high_volatility", "unstable_spread_conditions", "choppy"}:
            regime_penalty += 0.010
        regime_penalty += min(0.025, max(0.0, total_exposure / max(REALM_XVIII_MAX_TOTAL_EXPOSURE_DOLLARS, 1.0)) * 0.015)
        regime_penalty += min(0.020, family_concentration * 0.020)
        regime_penalty += min(0.020, correlated_concentration * 0.020)
        if realized_pnl < 0:
            regime_penalty += min(0.020, abs(realized_pnl) / max(REALM_XVIII_MAX_DAILY_RISK_DOLLARS, 1.0) * 0.020)
        if loss_streak >= 2:
            regime_penalty += 0.015
        regime_penalty += max(0.0, 0.60 - health_score) * 0.050
        regime_penalty = min(REALM_XVIII_MAX_SCORE_PENALTY, regime_penalty)
        adjusted_score = base_score * (1.0 - regime_penalty)
        risk_adjusted_score = adjusted_score - (regime_penalty * 10.0)
        portfolio_state = dict(portfolio_state)
        portfolio_state.update(
            {
                "portfolio_adjusted_strategy_score": _round(adjusted_score, 4, base_score),
                "risk_adjusted_candidate_score": _round(risk_adjusted_score, 4, base_score),
                "portfolio_score_penalty": _round(regime_penalty, 4, 0.0),
                "strategy_quality_source": str(portfolio_state.get("strategy_quality_source") or "unknown"),
            }
        )
        self._log(
            "[REALM-XVIII-STRATEGY-RISK]",
            base_score=_round(base_score, 4, 0.0),
            penalty=_round(regime_penalty, 4, 0.0),
            health_score=_round(health_score, 4, 0.0),
            mode=mode,
        )
        self._log(
            "[REALM-XVIII-PORTFOLIO-ADJUSTED-SCORE]",
            portfolio_adjusted_strategy_score=portfolio_state["portfolio_adjusted_strategy_score"],
            risk_adjusted_candidate_score=portfolio_state["risk_adjusted_candidate_score"],
            penalty=portfolio_state["portfolio_score_penalty"],
        )
        return portfolio_state

    def replay_portfolio_overlay(self, snapshots=None, realm_xvi_summary=None, realm_xvii_summary=None):
        snapshots = [snapshot for snapshot in (snapshots or []) if isinstance(snapshot, dict)]
        running_positions = {}
        simulated_positions = []
        skipped = 0
        reduced = 0
        for snapshot in snapshots:
            ticker = str(snapshot.get("ticker") or "").strip()
            if not ticker:
                continue
            family = str(snapshot.get("market_family") or snapshot.get("family") or ticker.split("-", 1)[0]).strip().upper()
            category = str(snapshot.get("category") or _family_to_category(family)).strip().lower()
            decision = str(snapshot.get("decision_result") or "").strip().upper()
            if decision == "BUY":
                entry_price = _safe_float(snapshot.get("intended_entry_price"), None)
                if entry_price is None:
                    entry_price = _safe_float(snapshot.get("yes_ask"), None)
                exposure = abs(entry_price or 0.0)
                running_positions[ticker] = {
                    "ticker": ticker,
                    "family": family,
                    "category": category,
                    "side": str(snapshot.get("selected_side") or "yes").strip().lower(),
                    "count": 1.0,
                    "avg_price": entry_price,
                    "market_exposure": exposure,
                    "entry_context": snapshot.get("entry_context") if isinstance(snapshot.get("entry_context"), dict) else {},
                }
                simulated_positions.append(running_positions[ticker])
            elif decision == "BLOCK":
                skipped += 1
            if len(simulated_positions) >= REALM_XVIII_HISTORY_LIMIT:
                break
        state = self.analyze_portfolio(
            live_positions=simulated_positions,
            realized_pnl_today=0.0,
            realm_xvi_summary=realm_xvi_summary,
            realm_xvii_summary=realm_xvii_summary,
            source="realm_xviii_replay",
        )
        state["simulated_buy_count"] = len([snapshot for snapshot in snapshots if str(snapshot.get("decision_result") or "").strip().upper() == "BUY"])
        state["simulated_block_count"] = skipped
        state["simulated_reduced_count"] = reduced
        state["last_replay_status"] = "successful"
        self._log(
            "[REALM-XVIII-REPLAY]",
            simulated_buy_count=state["simulated_buy_count"],
            simulated_block_count=state["simulated_block_count"],
            portfolio_health_score=state.get("portfolio_health_score"),
            mode=state.get("portfolio_mode"),
        )
        self._log(
            "[REALM-XVIII-REPLAY-PORTFOLIO]",
            total_exposure_dollars=state.get("total_exposure_dollars"),
            family_concentration=state.get("family_concentration"),
            correlated_concentration=state.get("correlated_concentration"),
            risk_budget_used=state.get("max_daily_risk_used"),
        )
        self._log(
            "[REALM-XVIII-REPLAY-SUMMARY]",
            mode=state.get("portfolio_mode"),
            recommendation=state.get("recommendation"),
            health_score=state.get("portfolio_health_score"),
        )
        self.last_report = state
        return state

    def report_summary(self):
        state = dict(self.last_state or {})
        if not state:
            try:
                with self._connect() as conn:
                    row = conn.execute("SELECT portfolio_json FROM portfolio_reports ORDER BY id DESC LIMIT 1").fetchone()
                    if row and row[0]:
                        state = json.loads(row[0])
            except Exception:
                state = {}
        if not state:
            state = PortfolioState().to_dict()
        print()
        print("REALM XVIII - PORTFOLIO BRAIN REPORT")
        print("Portfolio Health:")
        print(f"- Score: {state.get('portfolio_health_score', 1.0)}")
        print(f"- Mode: {str(state.get('portfolio_mode') or 'normal').capitalize()}")
        print(f"- Total Exposure: ${_round(state.get('total_exposure_dollars'), 2, 0.0)}")
        print(f"- Available Balance: ${_round(state.get('available_balance'), 2, 0.0) if state.get('available_balance') is not None else 'unknown'}")
        print("Exposure:")
        for label, value in list((state.get("exposure_by_category") or {}).items())[:5]:
            print(f"- {label.capitalize()}: ${_round(value, 2, 0.0)}")
        print("Concentration Warnings:")
        family_values = list((state.get("exposure_by_family") or {}).items())
        if family_values:
            top_family = max(family_values, key=lambda item: item[1])
            print(f"- Highest family exposure: {top_family[0]} (${_round(top_family[1], 2, 0.0)})")
        else:
            print("- none")
        print("Daily Objective:")
        print(f"- Realized PnL: ${_round(state.get('realized_daily_pnl'), 2, 0.0)}")
        risk_used = _round(state.get("max_daily_risk_used"), 2, 0.0)
        print(f"- Risk Budget Used: {risk_used / max(REALM_XVIII_MAX_DAILY_RISK_DOLLARS, 1.0) * 100:.0f}%")
        print("Recommendation:")
        print(f"- {state.get('recommendation', 'continue normal trading')}")
        return state


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument(
        "--snapshot-file",
        default=runtime_state_file("simulation_snapshots.jsonl") if callable(runtime_state_file) else "/opt/kalshi-bot/runtime/state/simulation_snapshots.jsonl",
    )
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    brain = PortfolioBrain()
    if args.report:
        brain.report_summary()
        return
    if args.replay:
        try:
            from simulation_worlds import RealmXVSimulationWorlds
        except Exception:
            print("[REALM-XVIII-ERROR] reason=realm_xv_module_unavailable")
            return
        sim = RealmXVSimulationWorlds(snapshot_path=args.snapshot_file, record_enabled=False)
        snapshots = sim.load_snapshots(limit=args.limit)
        brain.replay_portfolio_overlay(snapshots=snapshots)
        return
    print("Use --report to print the Realm XVIII portfolio report or --replay to evaluate Realm XV snapshots.")


if __name__ == "__main__":
    main()
