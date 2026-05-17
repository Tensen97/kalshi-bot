import bisect
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    from simulation_worlds import RealmXVSimulationWorlds
except Exception:
    RealmXVSimulationWorlds = None

try:
    from portfolio_brain import PortfolioBrain
except Exception:
    PortfolioBrain = None


REALM_XVII_ENABLE = (os.environ.get("REALM_XVII_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XVII_MAX_CANDIDATES = int(float(os.environ.get("REALM_XVII_MAX_CANDIDATES", "25") or 25))
REALM_XVII_MAX_MUTATION_PERCENT = float(os.environ.get("REALM_XVII_MAX_MUTATION_PERCENT", "0.10") or 0.10)
REALM_XVII_STATE_FILE = os.environ.get("REALM_XVII_STATE_FILE", "/opt/kalshi-bot/data/realm_xvii_strategy_evolution.sqlite3")
REALM_XVII_EXPORT_FILE = os.environ.get("REALM_XVII_EXPORT_FILE", "/opt/kalshi-bot/data/realm_xvii_latest_report.json")
REALM_XVII_SHADOW_THROTTLE_SECONDS = int(float(os.environ.get("REALM_XVII_SHADOW_THROTTLE_SECONDS", "300") or 300))
REALM_XVII_REPLAY_SAMPLE_LIMIT = int(float(os.environ.get("REALM_XVII_REPLAY_SAMPLE_LIMIT", "2500") or 2500))
REALM_XVII_MAX_EVALUATIONS = int(float(os.environ.get("REALM_XVII_MAX_EVALUATIONS", "2000") or 2000))

BASELINE_PROFILE = {
    "min_score": 14.0,
    "min_confidence": 0.55,
    "max_spread": 0.035,
    "min_liquidity": 50.0,
    "exit_delay_seconds": 300.0,
    "momentum_weight": 1.00,
    "divergence_penalty": 1.00,
    "correlation_weight": 1.00,
    "liquidity_weight": 1.00,
    "volatility_penalty": 1.00,
    "hold_multiplier": 1.00,
}

HARD_GUARDIAN_KEYS = {
    "duplicate_protection",
    "live_arming",
    "live_deployment",
    "live_trading",
    "guardian_override",
    "max_daily_trades",
    "max_concurrent_trades",
    "do_not_trade",
    "spread_guard",
    "liquidity_guard",
}

HARD_GUARDIAN_REASONS = {
    "duplicate_position_guard",
    "live_trading_not_armed",
    "live_not_armed",
    "live_env_not_confirmed",
    "daily_loss_limit",
    "loss_streak_limit",
    "max_exposure",
    "max_total_live_positions",
    "max_btc15m_live_positions",
    "max_sports_live_positions",
    "market_closed_or_resolved",
    "market_closed",
    "market_invalid",
    "market_expired_by_event_time",
    "dangerous_spread",
    "dangerous_liquidity",
    "no_order_api_path",
    "not_tradable",
    "guardian_veto",
}


def _now_ts():
    return time.time()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _slugify(value):
    text = str(value or "").strip().lower()
    text = "".join(ch if ch.isalnum() else "_" for ch in text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_") or "strategy"


def _stable_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _mutation_signature(overrides):
    return hashlib.sha1(_stable_json(overrides or {}).encode("utf-8")).hexdigest()[:12]


def _current_environment_label(snapshot=None, realm_xvi_summary=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
    regime_label = str(
        realm_xvi_summary.get("dominant_regime")
        or realm_xvi_summary.get("regime_label")
        or snapshot.get("entry_context", {}).get("regime_label")
        or snapshot.get("volatility_state", {}).get("regime_label")
        or ""
    ).strip().lower()
    spread = _safe_float(snapshot.get("spread"), None)
    liquidity = _safe_float(snapshot.get("liquidity"), None)
    category = str(snapshot.get("category") or "").strip().lower()
    hour = _safe_int(datetime.now(timezone.utc).hour, 0)
    if spread is not None and spread >= 0.07:
        return "unstable_spread_conditions"
    if liquidity is not None and liquidity <= 15.0:
        return "low_liquidity"
    if regime_label in {"trending", "strong_directional_momentum"}:
        return "trending"
    if regime_label in {"high_volatility", "choppy"}:
        return "high_volatility"
    if regime_label in {"thin_overnight_markets"} or (hour in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11} and (liquidity or 0.0) < 40.0):
        return "thin_overnight_markets"
    if category in {"sports", "crypto", "weather"}:
        return f"balanced_{category}"
    return "balanced"


@dataclass
class StrategyProfile:
    strategy_id: str
    name: str
    description: str
    origin: str
    created_at: str
    parent_strategy_id: str | None = None
    status: str = "experimental"
    parameter_overrides: dict = field(default_factory=dict)
    performance_metrics: dict = field(default_factory=dict)
    specialization: dict = field(default_factory=dict)
    mutation_signature: str | None = None
    lineage_depth: int = 0
    root_strategy_id: str | None = None
    archived_at: str | None = None
    archived_reason: str | None = None

    def to_record(self):
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "origin": self.origin,
            "created_at": self.created_at,
            "parent_strategy_id": self.parent_strategy_id,
            "status": self.status,
            "parameter_overrides_json": _stable_json(self.parameter_overrides or {}),
            "performance_metrics_json": _stable_json(self.performance_metrics or {}),
            "specialization_json": _stable_json(self.specialization or {}),
            "mutation_signature": self.mutation_signature,
            "lineage_depth": int(self.lineage_depth or 0),
            "root_strategy_id": self.root_strategy_id,
            "archived_at": self.archived_at,
            "archived_reason": self.archived_reason,
        }


class StrategyEvolutionEngine:
    def __init__(
        self,
        state_file=REALM_XVII_STATE_FILE,
        export_file=REALM_XVII_EXPORT_FILE,
        enabled=REALM_XVII_ENABLE,
        max_candidates=REALM_XVII_MAX_CANDIDATES,
        max_mutation_percent=REALM_XVII_MAX_MUTATION_PERCENT,
        replay_sample_limit=REALM_XVII_REPLAY_SAMPLE_LIMIT,
        shadow_throttle_seconds=REALM_XVII_SHADOW_THROTTLE_SECONDS,
    ):
        self.state_file = state_file
        self.export_file = export_file
        self.enabled = bool(enabled)
        self.max_candidates = max(1, int(max_candidates))
        self.max_mutation_percent = max(0.01, min(0.50, float(max_mutation_percent)))
        self.replay_sample_limit = max(250, int(replay_sample_limit))
        self.shadow_throttle_seconds = max(60, int(shadow_throttle_seconds))
        self.last_shadow_ts = 0.0
        self.last_shadow_signature = None
        self.last_report = {}
        self.portfolio_brain = None
        if PortfolioBrain is not None:
            try:
                self.portfolio_brain = PortfolioBrain()
            except Exception as exc:
                self._log("[REALM-XVIII-DEGRADED]", reason=exc.__class__.__name__)
        self._ensure_store()
        self._seed_baseline_strategy()

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
                    CREATE TABLE IF NOT EXISTS strategy_profiles (
                        strategy_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        origin TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        parent_strategy_id TEXT,
                        root_strategy_id TEXT,
                        status TEXT NOT NULL,
                        parameter_overrides_json TEXT NOT NULL,
                        performance_metrics_json TEXT NOT NULL,
                        specialization_json TEXT NOT NULL,
                        mutation_signature TEXT,
                        lineage_depth INTEGER NOT NULL DEFAULT 0,
                        archived_at TEXT,
                        archived_reason TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_profiles_status ON strategy_profiles(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_profiles_parent ON strategy_profiles(parent_strategy_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_profiles_root ON strategy_profiles(root_strategy_id)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_evaluations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        baseline_strategy_id TEXT,
                        replay_mode TEXT NOT NULL,
                        regime_label TEXT,
                        metrics_json TEXT NOT NULL,
                        environment_json TEXT NOT NULL,
                        score_json TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_strategy ON strategy_evaluations(strategy_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_created_at ON strategy_evaluations(created_at)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_lineage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        parent_strategy_id TEXT NOT NULL,
                        child_strategy_id TEXT NOT NULL,
                        relation TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_lineage_parent ON strategy_lineage(parent_strategy_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_lineage_child ON strategy_lineage(child_strategy_id)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        strategy_id TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_telemetry_event ON strategy_telemetry(event_type)")
                conn.commit()
            return True
        except Exception as exc:
            self._log("[REALM-XVII-ERROR]", reason=f"store_init_{exc.__class__.__name__}")
            return False

    def _seed_baseline_strategy(self):
        baseline = self.get_strategy("baseline_v1")
        if baseline is not None:
            return baseline
        profile = StrategyProfile(
            strategy_id="baseline_v1",
            name="baseline_v1",
            description="Controlled baseline strategy used as the reference point for Realm XVII.",
            origin="seed",
            created_at=_now_iso(),
            parent_strategy_id=None,
            status="elite_candidate",
            parameter_overrides=dict(BASELINE_PROFILE),
            performance_metrics={},
            specialization={
                "primary_environment": "balanced",
                "secondary_environment": "trending",
                "regime_affinity": ["balanced", "trending", "high_volatility"],
            },
            mutation_signature=_mutation_signature(BASELINE_PROFILE),
            lineage_depth=0,
            root_strategy_id="baseline_v1",
        )
        self._upsert_strategy(profile)
        return profile

    def _row_to_profile(self, row):
        if row is None:
            return None
        return StrategyProfile(
            strategy_id=row["strategy_id"],
            name=row["name"],
            description=row["description"] or "",
            origin=row["origin"] or "unknown",
            created_at=row["created_at"],
            parent_strategy_id=row["parent_strategy_id"],
            status=row["status"],
            parameter_overrides=json.loads(row["parameter_overrides_json"] or "{}"),
            performance_metrics=json.loads(row["performance_metrics_json"] or "{}"),
            specialization=json.loads(row["specialization_json"] or "{}"),
            mutation_signature=row["mutation_signature"],
            lineage_depth=_safe_int(row["lineage_depth"], 0) or 0,
            root_strategy_id=row["root_strategy_id"],
            archived_at=row["archived_at"],
            archived_reason=row["archived_reason"],
        )

    def get_strategy(self, strategy_id):
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM strategy_profiles WHERE strategy_id = ?", (strategy_id,)).fetchone()
                return self._row_to_profile(row)
        except Exception:
            return None

    def _existing_strategy_created_at(self, strategy_id):
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT created_at FROM strategy_profiles WHERE strategy_id = ?", (strategy_id,)).fetchone()
                if row:
                    return row[0]
        except Exception:
            return None
        return None

    def _upsert_strategy(self, profile):
        profile = profile if isinstance(profile, StrategyProfile) else None
        if profile is None:
            return {"ok": False, "reason": "invalid_profile"}
        try:
            existing_created_at = self._existing_strategy_created_at(profile.strategy_id) or profile.created_at
            record = profile.to_record()
            record["created_at"] = existing_created_at
            record["updated_at"] = _now_iso()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO strategy_profiles (
                        strategy_id, name, description, origin, created_at, updated_at,
                        parent_strategy_id, root_strategy_id, status, parameter_overrides_json,
                        performance_metrics_json, specialization_json, mutation_signature,
                        lineage_depth, archived_at, archived_reason
                    ) VALUES (
                        :strategy_id, :name, :description, :origin, :created_at, :updated_at,
                        :parent_strategy_id, :root_strategy_id, :status, :parameter_overrides_json,
                        :performance_metrics_json, :specialization_json, :mutation_signature,
                        :lineage_depth, :archived_at, :archived_reason
                    )
                    ON CONFLICT(strategy_id) DO UPDATE SET
                        name=excluded.name,
                        description=excluded.description,
                        origin=excluded.origin,
                        updated_at=excluded.updated_at,
                        parent_strategy_id=excluded.parent_strategy_id,
                        root_strategy_id=excluded.root_strategy_id,
                        status=excluded.status,
                        parameter_overrides_json=excluded.parameter_overrides_json,
                        performance_metrics_json=excluded.performance_metrics_json,
                        specialization_json=excluded.specialization_json,
                        mutation_signature=excluded.mutation_signature,
                        lineage_depth=excluded.lineage_depth,
                        archived_at=excluded.archived_at,
                        archived_reason=excluded.archived_reason
                    """,
                    record,
                )
                if profile.parent_strategy_id:
                    conn.execute(
                        """
                        INSERT INTO strategy_lineage (parent_strategy_id, child_strategy_id, relation, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (profile.parent_strategy_id, profile.strategy_id, "mutation", _now_iso()),
                    )
                conn.commit()
            return {"ok": True, "strategy_id": profile.strategy_id}
        except Exception as exc:
            self._log("[REALM-XVII-ERROR]", strategy_id=profile.strategy_id, reason=exc.__class__.__name__)
            return {"ok": False, "reason": exc.__class__.__name__}

    def _append_telemetry(self, event_type, payload, strategy_id=None):
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO strategy_telemetry (event_type, strategy_id, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_type, strategy_id, _stable_json(payload if isinstance(payload, dict) else {"payload": payload}), _now_iso()),
                )
                conn.commit()
        except Exception:
            pass

    def _load_active_strategies(self):
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM strategy_profiles
                    WHERE status IN ('experimental', 'candidate', 'elite_candidate')
                    ORDER BY
                        CASE status
                            WHEN 'elite_candidate' THEN 3
                            WHEN 'candidate' THEN 2
                            WHEN 'experimental' THEN 1
                            ELSE 0
                        END DESC,
                        created_at DESC
                    """
                ).fetchall()
                return [self._row_to_profile(row) for row in rows]
        except Exception:
            return []

    def _strategy_rank(self, profile):
        metrics = profile.performance_metrics or {}
        return (
            _safe_float(metrics.get("overall_evolution_score"), 0.0) or 0.0,
            _safe_float(metrics.get("edge_score"), 0.0) or 0.0,
            _safe_float(metrics.get("risk_score"), 0.0) or 0.0,
            _safe_float(metrics.get("stability_score"), 0.0) or 0.0,
            _safe_int(profile.lineage_depth, 0) or 0,
            profile.created_at,
        )

    def _prune_storage(self):
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM strategy_profiles").fetchall()
                profiles = [self._row_to_profile(row) for row in rows]
                active = [profile for profile in profiles if profile.status in {"experimental", "candidate", "elite_candidate"}]
                if len(active) > self.max_candidates:
                    active_sorted = sorted(active, key=self._strategy_rank, reverse=True)
                    for profile in active_sorted[self.max_candidates :]:
                        archived = StrategyProfile(
                            strategy_id=profile.strategy_id,
                            name=profile.name,
                            description=profile.description,
                            origin=profile.origin,
                            created_at=profile.created_at,
                            parent_strategy_id=profile.parent_strategy_id,
                            status="rejected",
                            parameter_overrides=profile.parameter_overrides,
                            performance_metrics=profile.performance_metrics,
                            specialization=profile.specialization,
                            mutation_signature=profile.mutation_signature,
                            lineage_depth=profile.lineage_depth,
                            root_strategy_id=profile.root_strategy_id,
                            archived_at=_now_iso(),
                            archived_reason="capacity_prune",
                        )
                        self._upsert_strategy(archived)
                        self._log("[REALM-XVII-ARCHIVED]", strategy=profile.name, strategy_id=profile.strategy_id, reason="capacity_prune")
                rejected = [profile for profile in profiles if profile.status == "rejected"]
                if len(rejected) > self.max_candidates:
                    rejected_sorted = sorted(rejected, key=lambda profile: profile.archived_at or profile.created_at)
                    delete_ids = [profile.strategy_id for profile in rejected_sorted[: len(rejected) - self.max_candidates]]
                    if delete_ids:
                        placeholders = ",".join("?" for _ in delete_ids)
                        conn.execute(f"DELETE FROM strategy_profiles WHERE strategy_id IN ({placeholders})", delete_ids)
                        conn.execute(f"DELETE FROM strategy_evaluations WHERE strategy_id IN ({placeholders})", delete_ids)
                        conn.execute(f"DELETE FROM strategy_lineage WHERE parent_strategy_id IN ({placeholders}) OR child_strategy_id IN ({placeholders})", delete_ids + delete_ids)
                        conn.commit()
                evaluation_rows = conn.execute("SELECT id FROM strategy_evaluations ORDER BY created_at DESC").fetchall()
                if len(evaluation_rows) > REALM_XVII_MAX_EVALUATIONS:
                    cutoff = len(evaluation_rows) - REALM_XVII_MAX_EVALUATIONS
                    delete_ids = [row[0] for row in evaluation_rows[cutoff:]]
                    placeholders = ",".join("?" for _ in delete_ids)
                    conn.execute(f"DELETE FROM strategy_evaluations WHERE id IN ({placeholders})", delete_ids)
                    conn.commit()
        except Exception as exc:
            self._log("[REALM-XVII-DEGRADED]", reason=f"prune_{exc.__class__.__name__}")

    def _bounded_delta(self, value, pct, direction=1.0, minimum=None, maximum=None):
        value = _safe_float(value, 0.0) or 0.0
        pct = abs(float(pct))
        delta = value * pct * float(direction)
        adjusted = value + delta
        if minimum is not None:
            adjusted = max(float(minimum), adjusted)
        if maximum is not None:
            adjusted = min(float(maximum), adjusted)
        return _round(adjusted, 4, adjusted)

    def _candidate_templates(self, realm_xvi_summary=None):
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        regime = str(realm_xvi_summary.get("dominant_regime") or realm_xvi_summary.get("regime_label") or "balanced").strip().lower()
        templates = [
            {
                "name": "conservative_entry_timing",
                "description": "Tightens entry confidence and liquidity filters while allowing slightly more patience on exits.",
                "overrides": {"min_score": 1.0, "min_confidence": 1.0, "min_liquidity": 1.0, "max_spread": -1.0, "exit_delay_seconds": 1.0, "divergence_penalty": 1.0},
                "specialization": {"primary_environment": "preservation", "secondary_environment": "balanced", "regime_affinity": ["balanced", "low_liquidity"]},
            },
            {
                "name": "faster_exit_variant",
                "description": "Reduces holding time to cut exposure faster after entry.",
                "overrides": {"exit_delay_seconds": -1.0, "volatility_penalty": 1.0, "hold_multiplier": -0.5},
                "specialization": {"primary_environment": "volatility_specialist", "secondary_environment": "high_volatility", "regime_affinity": ["high_volatility", "choppy"]},
            },
            {
                "name": "slower_exit_variant",
                "description": "Extends hold time for cleaner trend continuation when the environment stays coherent.",
                "overrides": {"exit_delay_seconds": 1.0, "correlation_weight": 1.0, "momentum_weight": 1.0},
                "specialization": {"primary_environment": "trend_specialist", "secondary_environment": "trending", "regime_affinity": ["trending", "strong_directional_momentum"]},
            },
            {
                "name": "volatility_sensitive_exits",
                "description": "Amplifies volatility penalties and tightens spread tolerance for noisy regimes.",
                "overrides": {"volatility_penalty": 1.0, "max_spread": -1.0, "min_liquidity": 1.0, "divergence_penalty": 1.0},
                "specialization": {"primary_environment": "volatility_specialist", "secondary_environment": "unstable_spread_conditions", "regime_affinity": ["high_volatility", "unstable_spread_conditions"]},
            },
            {
                "name": "stronger_correlation_weighting",
                "description": "Gives more weight to cross-market alignment and family concordance.",
                "overrides": {"correlation_weight": 1.0, "divergence_penalty": 1.0, "min_score": 1.0},
                "specialization": {"primary_environment": "cross_market_aligned", "secondary_environment": "trending", "regime_affinity": ["trending", "balanced_crypto"]},
            },
            {
                "name": "stricter_liquidity_filters",
                "description": "Raises the minimum liquidity floor to reduce slippage risk.",
                "overrides": {"min_liquidity": 1.0, "max_spread": -1.0, "min_confidence": 1.0},
                "specialization": {"primary_environment": "preservation", "secondary_environment": "low_liquidity", "regime_affinity": ["low_liquidity", "balanced"]},
            },
            {
                "name": "adaptive_threshold_lower",
                "description": "Slightly lowers score threshold to capture near-miss opportunities without touching guardian floors.",
                "overrides": {"min_score": -1.0, "momentum_weight": 1.0, "min_confidence": -0.5},
                "specialization": {"primary_environment": "momentum_hunter", "secondary_environment": "trending", "regime_affinity": ["trending", "balanced_sports"]},
            },
            {
                "name": "stronger_divergence_penalties",
                "description": "Penalizes divergent families and noisy cross-market disagreement more aggressively.",
                "overrides": {"divergence_penalty": 1.0, "correlation_weight": 1.0, "max_spread": -0.5},
                "specialization": {"primary_environment": "cross_market_aligned", "secondary_environment": "choppy", "regime_affinity": ["choppy", "unstable_spread_conditions"]},
            },
            {
                "name": "trend_following_specialist",
                "description": "Biases toward sustained directional move continuation.",
                "overrides": {"momentum_weight": 1.0, "exit_delay_seconds": 1.0, "min_score": 0.5},
                "specialization": {"primary_environment": "trend_specialist", "secondary_environment": "strong_directional_momentum", "regime_affinity": ["trending", "strong_directional_momentum"]},
            },
            {
                "name": "overnight_preservation_variant",
                "description": "Prefers thinner overnight markets only when the signal remains sufficiently clean.",
                "overrides": {"exit_delay_seconds": -0.5, "min_liquidity": 1.0, "min_confidence": 1.0},
                "specialization": {"primary_environment": "overnight_specialist", "secondary_environment": "thin_overnight_markets", "regime_affinity": ["thin_overnight_markets", "balanced_weather"]},
            },
            {
                "name": "family_sentiment_aware",
                "description": "Makes moderate use of family sentiment and regime coherence when picking entries.",
                "overrides": {"correlation_weight": 1.0, "momentum_weight": 0.5, "min_score": 0.5},
                "specialization": {"primary_environment": "family_sentiment", "secondary_environment": "balanced", "regime_affinity": ["balanced", "trending", "high_volatility"]},
            },
            {
                "name": "adaptive_volatility_exits",
                "description": "Keeps entries steady but shortens exits when volatility expands.",
                "overrides": {"volatility_penalty": 1.0, "exit_delay_seconds": -1.0, "min_confidence": 0.5},
                "specialization": {"primary_environment": "volatility_specialist", "secondary_environment": "high_volatility", "regime_affinity": ["high_volatility", "unstable_spread_conditions"]},
            },
            {
                "name": "balanced_preserver",
                "description": "Keeps the baseline shape but slightly favors cleaner setups and smaller drawdown.",
                "overrides": {"min_score": 0.5, "min_confidence": 0.5, "divergence_penalty": 0.5},
                "specialization": {"primary_environment": "preservation", "secondary_environment": "balanced", "regime_affinity": ["balanced", "low_liquidity", "choppy"]},
            },
        ]
        if regime == "trending":
            templates.insert(
                0,
                {
                    "name": "trending_crypto_specialist",
                    "description": "Prioritizes trend continuation and correlation alignment in directional environments.",
                    "overrides": {"momentum_weight": 1.0, "correlation_weight": 1.0, "exit_delay_seconds": 0.5},
                    "specialization": {"primary_environment": "trend_specialist", "secondary_environment": "balanced_crypto", "regime_affinity": ["trending", "strong_directional_momentum"]},
                },
            )
        elif regime in {"high_volatility", "unstable_spread_conditions"}:
            templates.insert(
                0,
                {
                    "name": "volatility_defensive_specialist",
                    "description": "Focuses on tighter entries and quicker exits when price action is noisy.",
                    "overrides": {"min_liquidity": 1.0, "max_spread": -1.0, "exit_delay_seconds": -1.0},
                    "specialization": {"primary_environment": "volatility_specialist", "secondary_environment": "high_volatility", "regime_affinity": ["high_volatility", "unstable_spread_conditions", "choppy"]},
                },
            )
        elif regime in {"thin_overnight_markets", "low_liquidity"}:
            templates.insert(
                0,
                {
                    "name": "overnight_preservation_specialist",
                    "description": "Preserves capital in thin hours by demanding cleaner setups and stronger confidence.",
                    "overrides": {"min_liquidity": 1.0, "min_confidence": 1.0, "exit_delay_seconds": -0.5},
                    "specialization": {"primary_environment": "overnight_specialist", "secondary_environment": "preservation", "regime_affinity": ["thin_overnight_markets", "low_liquidity"]},
                },
            )
        return templates

    def _mutation_overrides(self, template, baseline_profile):
        baseline_profile = baseline_profile if isinstance(baseline_profile, dict) else dict(BASELINE_PROFILE)
        overrides = {}
        pct = self.max_mutation_percent
        for key, direction in (template.get("overrides") or {}).items():
            base_value = _safe_float(baseline_profile.get(key, BASELINE_PROFILE.get(key)), BASELINE_PROFILE.get(key))
            if base_value is None:
                continue
            direction = _safe_float(direction, 0.0) or 0.0
            if key in {"max_spread", "min_liquidity"}:
                direction = -1.0 if key == "max_spread" else 1.0
            if key == "min_score":
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=10.0, maximum=baseline_profile.get(key, base_value) * 1.10)
            elif key == "min_confidence":
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=0.40, maximum=0.90)
            elif key == "max_spread":
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=0.010, maximum=base_value)
            elif key == "min_liquidity":
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=base_value, maximum=base_value * 1.25)
            elif key == "exit_delay_seconds":
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=120.0, maximum=900.0)
            elif key in {"momentum_weight", "divergence_penalty", "correlation_weight", "liquidity_weight", "volatility_penalty", "hold_multiplier"}:
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=0.80, maximum=1.25)
            else:
                adjusted = self._bounded_delta(base_value, pct, direction, minimum=0.0, maximum=base_value * 1.25)
            overrides[key] = _round(adjusted, 4, adjusted)
        return overrides

    def _guardian_veto(self, candidate_overrides, baseline_overrides):
        baseline_overrides = baseline_overrides if isinstance(baseline_overrides, dict) else dict(BASELINE_PROFILE)
        candidate_overrides = candidate_overrides if isinstance(candidate_overrides, dict) else {}
        violations = []
        for key in HARD_GUARDIAN_KEYS:
            if key in candidate_overrides:
                violations.append(f"attempted_guardian_override:{key}")
        if _safe_float(candidate_overrides.get("max_spread"), baseline_overrides.get("max_spread")) is not None:
            if _safe_float(candidate_overrides.get("max_spread"), baseline_overrides.get("max_spread")) > _safe_float(baseline_overrides.get("max_spread"), 0.035):
                violations.append("widened_max_spread")
        if _safe_float(candidate_overrides.get("min_liquidity"), baseline_overrides.get("min_liquidity")) is not None:
            if _safe_float(candidate_overrides.get("min_liquidity"), baseline_overrides.get("min_liquidity")) < _safe_float(baseline_overrides.get("min_liquidity"), 50.0):
                violations.append("reduced_min_liquidity")
        if _safe_float(candidate_overrides.get("min_confidence"), baseline_overrides.get("min_confidence")) is not None:
            if _safe_float(candidate_overrides.get("min_confidence"), baseline_overrides.get("min_confidence")) < 0.40:
                violations.append("confidence_floor_too_low")
        if _safe_float(candidate_overrides.get("min_score"), baseline_overrides.get("min_score")) is not None:
            if _safe_float(candidate_overrides.get("min_score"), baseline_overrides.get("min_score")) < 10.0:
                violations.append("score_floor_too_low")
        return violations

    def _create_candidate(self, baseline_profile, template, realm_xvi_summary=None):
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        baseline_profile = baseline_profile if isinstance(baseline_profile, StrategyProfile) else self._seed_baseline_strategy()
        overrides = self._mutation_overrides(template, baseline_profile.parameter_overrides)
        vetoes = self._guardian_veto(overrides, baseline_profile.parameter_overrides)
        label = _slugify(template.get("name") or "candidate")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        strategy_id = f"{label}__{timestamp}"
        specialization = dict(template.get("specialization") or {})
        specialization["source_regime"] = str(realm_xvi_summary.get("dominant_regime") or realm_xvi_summary.get("regime_label") or "balanced").strip().lower()
        specialization["best_environment"] = specialization.get("primary_environment") or "balanced"
        specialization["worst_environment"] = "unstable_spread_conditions" if specialization["best_environment"] != "unstable_spread_conditions" else "low_liquidity"
        status = "experimental" if not vetoes else "rejected"
        if vetoes:
            self._log("[REALM-XVII-GUARDIAN-VETO]", candidate=label, strategy_id=strategy_id, reason=";".join(vetoes))
        profile = StrategyProfile(
            strategy_id=strategy_id,
            name=label,
            description=str(template.get("description") or ""),
            origin="realm_xvii_mutation",
            created_at=_now_iso(),
            parent_strategy_id=baseline_profile.strategy_id,
            status=status,
            parameter_overrides={**baseline_profile.parameter_overrides, **overrides},
            performance_metrics={},
            specialization=specialization,
            mutation_signature=_mutation_signature(overrides),
            lineage_depth=(baseline_profile.lineage_depth or 0) + 1,
            root_strategy_id=baseline_profile.root_strategy_id or baseline_profile.strategy_id,
            archived_at=_now_iso() if vetoes else None,
            archived_reason="guardian_veto" if vetoes else None,
        )
        return profile, vetoes

    def generate_candidates(self, baseline_profile=None, realm_xvi_summary=None, snapshots=None):
        if not self.enabled:
            self._log("[REALM-XVII-DEGRADED]", reason="disabled")
            return []
        baseline_profile = baseline_profile if isinstance(baseline_profile, StrategyProfile) else self._seed_baseline_strategy()
        templates = self._candidate_templates(realm_xvi_summary=realm_xvi_summary)
        candidates = []
        seen_signatures = set()
        existing = self._load_active_strategies()
        for profile in existing:
            seen_signatures.add(profile.mutation_signature)
        for template in templates:
            candidate, vetoes = self._create_candidate(baseline_profile, template, realm_xvi_summary=realm_xvi_summary)
            if candidate.mutation_signature in seen_signatures:
                continue
            seen_signatures.add(candidate.mutation_signature)
            candidates.append(candidate)
            self._log(
                "[REALM-XVII-GENERATION]",
                candidate=candidate.name,
                strategy_id=candidate.strategy_id,
                parent=candidate.parent_strategy_id,
                regime=str((realm_xvi_summary or {}).get("dominant_regime") or (realm_xvi_summary or {}).get("regime_label") or "balanced"),
                status=candidate.status,
            )
            self._log(
                "[REALM-XVII-MUTATION]",
                candidate=candidate.name,
                strategy_id=candidate.strategy_id,
                mutation_signature=candidate.mutation_signature,
                overrides=_stable_json(candidate.parameter_overrides),
            )
            if len(candidates) >= self.max_candidates:
                break
        return candidates

    def _future_lookup(self, timestamps, snapshots, current_ts, min_delay_seconds):
        if current_ts is None:
            return None
        target_ts = current_ts + max(0.0, float(min_delay_seconds))
        index = bisect.bisect_right(timestamps, target_ts)
        if index >= len(snapshots):
            return None
        return snapshots[index]

    def _evaluate_entry(self, profile, snapshot, realm_xvi_summary=None, future_index=None):
        profile = profile if isinstance(profile, StrategyProfile) else self._seed_baseline_strategy()
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        hard_reason = str(snapshot.get("block_reason") or snapshot.get("skip_reason") or "").strip().lower()
        decision_result = str(snapshot.get("decision_result") or "SKIP").strip().upper()
        if hard_reason in HARD_GUARDIAN_REASONS or any(token in hard_reason for token in ("duplicate", "guardian", "armed", "balance", "liquidity", "spread", "closed", "invalid")):
            return {"decision": "BLOCK", "reason": hard_reason or "hard_block", "hard_block": True}
        if decision_result == "BLOCK":
            return {"decision": "BLOCK", "reason": hard_reason or "blocked", "hard_block": False}

        params = dict(BASELINE_PROFILE)
        params.update(profile.parameter_overrides or {})
        score = _safe_float(snapshot.get("score"), 0.0) or 0.0
        confidence = _safe_float(snapshot.get("confidence"), 0.0) or 0.0
        spread = _safe_float(snapshot.get("spread"), None)
        liquidity = _safe_float(snapshot.get("liquidity"), 0.0) or 0.0
        timing_state = str(snapshot.get("entry_timing_state") or "").strip().lower()
        regime_label = _current_environment_label(snapshot, realm_xvi_summary)
        momentum_bias = 0.0
        if timing_state in {"entry_ready", "target_window", "buy"}:
            momentum_bias += 0.20
        elif timing_state in {"soft_entry", "borderline"}:
            momentum_bias += 0.05
        elif timing_state in {"hold", "wait"}:
            momentum_bias -= 0.05
        if regime_label in {"trending"}:
            momentum_bias += 0.10 * _safe_float(params.get("momentum_weight"), 1.0)
        elif regime_label in {"high_volatility", "unstable_spread_conditions"}:
            momentum_bias -= 0.06 * _safe_float(params.get("volatility_penalty"), 1.0)
        elif regime_label in {"low_liquidity", "thin_overnight_markets"}:
            momentum_bias -= 0.04 * _safe_float(params.get("liquidity_weight"), 1.0)
        if realm_xvi_summary.get("strongest_alignment"):
            momentum_bias += 0.03 * _safe_float(params.get("correlation_weight"), 1.0)
        if realm_xvi_summary.get("largest_divergence"):
            momentum_bias -= 0.03 * _safe_float(params.get("divergence_penalty"), 1.0)
        adjusted_score = score + (confidence * 10.0) + momentum_bias
        threshold = _safe_float(params.get("min_score"), BASELINE_PROFILE["min_score"]) or BASELINE_PROFILE["min_score"]
        min_confidence = _safe_float(params.get("min_confidence"), BASELINE_PROFILE["min_confidence"]) or BASELINE_PROFILE["min_confidence"]
        max_spread = _safe_float(params.get("max_spread"), BASELINE_PROFILE["max_spread"]) or BASELINE_PROFILE["max_spread"]
        min_liquidity = _safe_float(params.get("min_liquidity"), BASELINE_PROFILE["min_liquidity"]) or BASELINE_PROFILE["min_liquidity"]
        if spread is not None and spread > max_spread:
            return {"decision": "SKIP", "reason": "wide_spread", "hard_block": False}
        if liquidity < min_liquidity:
            return {"decision": "SKIP", "reason": "low_liquidity", "hard_block": False}
        if confidence < min_confidence:
            return {"decision": "SKIP", "reason": "low_confidence", "hard_block": False}
        if adjusted_score < threshold:
            return {"decision": "SKIP", "reason": "score_too_low", "hard_block": False}
        selected_side = str(snapshot.get("selected_side") or "yes").strip().lower() or "yes"
        intended_price = _safe_float(snapshot.get("intended_entry_price"), None)
        if intended_price is None:
            intended_price = _safe_float(snapshot.get("yes_ask") if selected_side == "yes" else snapshot.get("no_ask"), None)
        if intended_price is None:
            return {"decision": "SKIP", "reason": "missing_entry_price", "hard_block": False}
        current_ts = _safe_float(snapshot.get("timestamp_ts"), None)
        future = None
        if future_index is not None and current_ts is not None:
            future = self._future_lookup(future_index["timestamps"], future_index["snapshots"], current_ts, params.get("exit_delay_seconds"))
        if future is None:
            return {
                "decision": "BUY",
                "reason": "insufficient_future_data",
                "hard_block": False,
                "entry_price": intended_price,
                "regime_label": regime_label,
            }
        exit_price = _safe_float(future.get("yes_bid") if selected_side == "yes" else future.get("no_bid"), None)
        if exit_price is None:
            exit_price = _safe_float(future.get("yes_ask") if selected_side == "yes" else future.get("no_ask"), None)
        if exit_price is None:
            return {"decision": "BUY", "reason": "insufficient_future_data", "hard_block": False, "entry_price": intended_price, "regime_label": regime_label}
        pnl_proxy = exit_price - intended_price if selected_side == "yes" else intended_price - exit_price
        return {
            "decision": "BUY",
            "reason": "estimated",
            "hard_block": False,
            "entry_price": intended_price,
            "exit_price": exit_price,
            "pnl_proxy": pnl_proxy,
            "hold_seconds": _safe_float(future.get("timestamp_ts"), None) - current_ts if current_ts is not None else None,
            "regime_label": regime_label,
        }

    def _build_snapshot_index(self, snapshots):
        grouped = defaultdict(list)
        for snapshot in snapshots or []:
            if not isinstance(snapshot, dict):
                continue
            ticker = str(snapshot.get("ticker") or "").strip()
            if not ticker:
                continue
            grouped[ticker].append(snapshot)
        indexed = {}
        for ticker, rows in grouped.items():
            rows.sort(key=lambda item: _safe_float(item.get("timestamp_ts"), 0.0) or 0.0)
            indexed[ticker] = {
                "snapshots": rows,
                "timestamps": [_safe_float(row.get("timestamp_ts"), 0.0) or 0.0 for row in rows],
            }
        return indexed

    def _aggregate_metrics(self, strategy, outcomes):
        entries = [item for item in outcomes if item.get("decision") == "BUY" and item.get("reason") != "insufficient_future_data"]
        samples = [item for item in outcomes if item.get("decision") == "BUY" and item.get("pnl_proxy") is not None]
        pnl_values = [float(item.get("pnl_proxy") or 0.0) for item in samples]
        pnl_total = sum(pnl_values)
        pnl_samples = len(pnl_values)
        wins = sum(1 for value in pnl_values if value > 0)
        losses = sum(1 for value in pnl_values if value < 0)
        insufficient_future_data = sum(1 for item in outcomes if item.get("reason") == "insufficient_future_data")
        skips = sum(1 for item in outcomes if item.get("decision") == "SKIP")
        blocks = sum(1 for item in outcomes if item.get("decision") == "BLOCK")
        avg_pnl = (pnl_total / pnl_samples) if pnl_samples else 0.0
        if pnl_values:
            cumulative = 0.0
            max_peak = 0.0
            max_drawdown = 0.0
            for value in pnl_values:
                cumulative += value
                max_peak = max(max_peak, cumulative)
                max_drawdown = max(max_drawdown, max_peak - cumulative)
        else:
            max_drawdown = 0.0
        high_risk_entries = sum(1 for item in outcomes if item.get("regime_label") in {"low_liquidity", "high_volatility", "unstable_spread_conditions", "thin_overnight_markets"})
        environment_counts = Counter(item.get("regime_label") for item in outcomes if item.get("regime_label"))
        best_environment = environment_counts.most_common(1)[0][0] if environment_counts else "balanced"
        weakest_environment = None
        environment_pnl = defaultdict(list)
        for item in outcomes:
            env = item.get("regime_label")
            if env and item.get("pnl_proxy") is not None:
                environment_pnl[env].append(float(item.get("pnl_proxy") or 0.0))
        if environment_pnl:
            weakest_environment = min(environment_pnl.items(), key=lambda pair: (sum(pair[1]) / len(pair[1]) if pair[1] else 0.0))[0]
        if weakest_environment is None:
            weakest_environment = "balanced"
        sample_count = len(samples)
        unique_envs = len({item.get("regime_label") for item in samples if item.get("regime_label")})
        win_rate = wins / sample_count if sample_count else 0.0
        high_risk_share = (high_risk_entries / max(len(entries), 1)) if entries else 0.0
        avg_abs_pnl = (sum(abs(value) for value in pnl_values) / sample_count) if sample_count else 0.0
        stability_score = _clamp(1.0 - min(1.0, avg_abs_pnl / 0.08), 0.0, 1.0)
        edge_score = _clamp(0.50 + (avg_pnl / 0.08), 0.0, 1.0)
        risk_score = _clamp(1.0 - min(1.0, high_risk_share * 0.70 + (max_drawdown / 0.15)), 0.0, 1.0)
        consistency_score = _clamp((win_rate * 0.60) + ((1.0 - min(1.0, max_drawdown / 0.12)) * 0.40), 0.0, 1.0)
        drawdown_score = _clamp(1.0 - min(1.0, max_drawdown / 0.15), 0.0, 1.0)
        confidence_score = _clamp((min(1.0, sample_count / 20.0) * 0.70) + (min(1.0, unique_envs / 4.0) * 0.30), 0.0, 1.0)
        overall_evolution_score = round(
            (
                stability_score * 0.20
                + edge_score * 0.25
                + risk_score * 0.20
                + consistency_score * 0.15
                + drawdown_score * 0.10
                + confidence_score * 0.10
            )
            * 100.0,
            4,
        )
        return {
            "entries": len(entries),
            "skips": skips,
            "blocks": blocks,
            "wins": wins,
            "losses": losses,
            "pnl_samples": pnl_samples,
            "pnl_total": round(pnl_total, 6),
            "avg_pnl": round(avg_pnl, 6),
            "max_drawdown_proxy": round(max_drawdown, 6),
            "high_risk_entry_share": round(high_risk_share, 6),
            "sample_count": sample_count,
            "best_environment": best_environment,
            "weakest_environment": weakest_environment,
            "stability_score": round(stability_score, 4),
            "edge_score": round(edge_score, 4),
            "risk_score": round(risk_score, 4),
            "consistency_score": round(consistency_score, 4),
            "drawdown_score": round(drawdown_score, 4),
            "confidence_score": round(confidence_score, 4),
            "overall_evolution_score": overall_evolution_score,
            "win_rate": round(win_rate, 4),
            "unique_environment_count": unique_envs,
            "insufficient_future_data": insufficient_future_data,
            "environment_counts": dict(environment_counts),
        }

    def _evaluate_strategy(self, strategy, snapshots, realm_xvi_summary=None, replay_mode="replay", baseline_strategy_id=None):
        strategy = strategy if isinstance(strategy, StrategyProfile) else self._seed_baseline_strategy()
        snapshots = [snapshot for snapshot in (snapshots or []) if isinstance(snapshot, dict)]
        snapshots = snapshots[: self.replay_sample_limit]
        indexed = self._build_snapshot_index(snapshots)
        outcomes = []
        for snapshot in snapshots:
            ticker = str(snapshot.get("ticker") or "").strip()
            future_index = indexed.get(ticker)
            outcome = self._evaluate_entry(strategy, snapshot, realm_xvi_summary=realm_xvi_summary, future_index=future_index)
            outcome["ticker"] = ticker
            outcome["family"] = str(snapshot.get("market_family") or snapshot.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
            outcome["category"] = str(snapshot.get("category") or "unknown").strip().lower()
            outcome["timestamp_ts"] = _safe_float(snapshot.get("timestamp_ts"), 0.0) or 0.0
            outcomes.append(outcome)
            self._log(
                "[REALM-XVII-SIMULATION]",
                strategy=strategy.name,
                ticker=ticker or "UNKNOWN",
                decision=outcome.get("decision"),
                reason=outcome.get("reason"),
                pnl_proxy=_round(outcome.get("pnl_proxy"), 4, None),
                regime=outcome.get("regime_label"),
            )
        metrics = self._aggregate_metrics(strategy, outcomes)
        status = strategy.status
        if metrics["blocks"] > 0 and any(outcome.get("hard_block") for outcome in outcomes):
            status = "rejected"
        metrics["baseline_strategy_id"] = baseline_strategy_id
        metrics["replay_mode"] = replay_mode
        metrics["strategy_status"] = status
        if baseline_strategy_id:
            metrics["baseline_comparison"] = {}
        strategy.performance_metrics = metrics
        strategy.specialization = {
            **(strategy.specialization or {}),
            "best_environment": metrics["best_environment"],
            "worst_environment": metrics["weakest_environment"],
            "regime_affinity": sorted({env for env in metrics["environment_counts"].keys()}),
        }
        return strategy, metrics, outcomes

    def _baseline_score(self, realm_xvi_summary=None, snapshots=None):
        baseline = self._seed_baseline_strategy()
        return self._evaluate_strategy(baseline, snapshots, realm_xvi_summary=realm_xvi_summary, replay_mode="baseline")

    def _apply_promotion_rules(self, strategy, metrics, baseline_metrics=None):
        baseline_metrics = baseline_metrics if isinstance(baseline_metrics, dict) else {}
        baseline_score = _safe_float(
            baseline_metrics.get("portfolio_adjusted_strategy_score")
            or baseline_metrics.get("risk_adjusted_candidate_score")
            or baseline_metrics.get("overall_evolution_score"),
            0.0,
        ) or 0.0
        score = _safe_float(
            metrics.get("portfolio_adjusted_strategy_score")
            or metrics.get("risk_adjusted_candidate_score")
            or metrics.get("overall_evolution_score"),
            0.0,
        ) or 0.0
        risk_score = _safe_float(metrics.get("risk_score"), 0.0) or 0.0
        drawdown_score = _safe_float(metrics.get("drawdown_score"), 0.0) or 0.0
        sample_count = _safe_int(metrics.get("sample_count"), 0) or 0
        delta = score - baseline_score
        promotion = "rejected"
        if metrics.get("blocks", 0) and metrics.get("insufficient_future_data", 0) == 0 and sample_count >= 3:
            promotion = "rejected"
        elif sample_count < 5:
            promotion = "experimental"
        elif score >= 82.0 and delta >= 5.0 and risk_score >= 0.60 and drawdown_score >= 0.60:
            promotion = "elite_candidate"
        elif score >= 68.0 and delta >= 2.0 and risk_score >= 0.50 and drawdown_score >= 0.50:
            promotion = "candidate"
        elif score >= 55.0:
            promotion = "experimental"
        else:
            promotion = "rejected"
        archived = promotion == "rejected" and score < 55.0
        if metrics.get("insufficient_future_data", 0) > 0 and promotion in {"candidate", "elite_candidate"}:
            promotion = "experimental"
        strategy.status = promotion
        if archived:
            strategy.archived_at = _now_iso()
            strategy.archived_reason = "weak_strategy"
        return promotion, archived

    def _save_report(self, report):
        try:
            _ensure_dir(self.export_file)
            with open(self.export_file, "w") as handle:
                json.dump(report, handle, sort_keys=True, indent=2)
        except Exception:
            pass

    def _ranked_summary(self, evaluations):
        ranked = sorted(evaluations, key=lambda item: (item.get("overall_evolution_score", 0.0), item.get("risk_score", 0.0), item.get("stability_score", 0.0)), reverse=True)
        return ranked

    def _best_mutation(self, evaluation_entries):
        if not evaluation_entries:
            return "none"
        best = max(evaluation_entries, key=lambda item: (item.get("overall_evolution_score", 0.0), item.get("edge_score", 0.0)))
        return best.get("mutation_name") or best.get("name") or "none"

    def _most_dangerous_mutation(self, evaluation_entries):
        if not evaluation_entries:
            return "none"
        worst = min(evaluation_entries, key=lambda item: (item.get("risk_score", 0.0), item.get("overall_evolution_score", 0.0)))
        return worst.get("mutation_name") or worst.get("name") or "none"

    def _store_evaluation(self, strategy, baseline_strategy_id, metrics, realm_xvi_summary, replay_mode, decision):
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO strategy_evaluations (
                        strategy_id, baseline_strategy_id, replay_mode, regime_label, metrics_json, environment_json, score_json, decision, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy.strategy_id,
                        baseline_strategy_id,
                        replay_mode,
                        str((realm_xvi_summary or {}).get("dominant_regime") or (realm_xvi_summary or {}).get("regime_label") or metrics.get("best_environment") or "balanced"),
                        _stable_json(metrics or {}),
                        _stable_json({
                            "realm_xvi_summary": realm_xvi_summary or {},
                            "specialization": strategy.specialization or {},
                        }),
                        _stable_json({
                            "overall_evolution_score": metrics.get("overall_evolution_score"),
                            "edge_score": metrics.get("edge_score"),
                            "risk_score": metrics.get("risk_score"),
                            "stability_score": metrics.get("stability_score"),
                            "drawdown_score": metrics.get("drawdown_score"),
                            "confidence_score": metrics.get("confidence_score"),
                        }),
                        decision,
                        _now_iso(),
                    ),
                )
                conn.commit()
        except Exception as exc:
            self._log("[REALM-XVII-ERROR]", strategy=strategy.name, reason=exc.__class__.__name__)

    def _update_strategy_metrics(self, strategy):
        self._upsert_strategy(strategy)

    def _emit_survival_log(self, strategy, metrics, baseline_metrics=None):
        baseline_metrics = baseline_metrics if isinstance(baseline_metrics, dict) else {}
        delta = _safe_float(metrics.get("overall_evolution_score"), 0.0) - _safe_float(baseline_metrics.get("overall_evolution_score"), 0.0)
        self._log(
            "[REALM-XVII-SURVIVAL]",
            strategy=strategy.name,
            status=strategy.status,
            overall_score=metrics.get("overall_evolution_score"),
            delta_vs_baseline=_round(delta, 4, 0.0),
            risk_score=metrics.get("risk_score"),
            drawdown_score=metrics.get("drawdown_score"),
            best_environment=metrics.get("best_environment"),
            weakest_environment=metrics.get("weakest_environment"),
        )

    def _lineage_trace(self, strategy):
        lineage = []
        current = strategy
        visited = set()
        while current is not None and current.strategy_id not in visited:
            visited.add(current.strategy_id)
            lineage.append(current.name)
            if not current.parent_strategy_id:
                break
            current = self.get_strategy(current.parent_strategy_id)
        return list(reversed(lineage))

    def run_replay_cycle(self, snapshots=None, realm_xvi_summary=None, baseline_strategy_id="baseline_v1"):
        if not self.enabled:
            self._log("[REALM-XVII-DEGRADED]", reason="disabled")
            return {"ok": False, "reason": "disabled"}
        snapshots = [snapshot for snapshot in (snapshots or []) if isinstance(snapshot, dict)]
        if not snapshots:
            self._log("[REALM-XVII-DEGRADED]", reason="no_snapshots")
            return {"ok": False, "reason": "no_snapshots"}
        snapshots = snapshots[: self.replay_sample_limit]
        baseline_profile = self.get_strategy(baseline_strategy_id) or self._seed_baseline_strategy()
        baseline_strategy, baseline_metrics, _baseline_outcomes = self._evaluate_strategy(
            baseline_profile,
            snapshots,
            realm_xvi_summary=realm_xvi_summary,
            replay_mode="baseline",
            baseline_strategy_id=None,
        )
        portfolio_state = None
        if self.portfolio_brain is not None and getattr(self.portfolio_brain, "enabled", False):
            try:
                portfolio_state = self.portfolio_brain.replay_portfolio_overlay(
                    snapshots=snapshots,
                    realm_xvi_summary=realm_xvi_summary,
                    realm_xvii_summary=baseline_metrics,
                )
            except Exception as exc:
                self._log("[REALM-XVIII-DEGRADED]", reason=exc.__class__.__name__)
        if portfolio_state:
            baseline_metrics = self.portfolio_brain.score_strategy_candidate(
                strategy_metrics=baseline_metrics,
                portfolio_state=portfolio_state,
                realm_xvi_summary=realm_xvi_summary,
                realm_xvii_summary=baseline_metrics,
            )
        baseline_strategy.performance_metrics = baseline_metrics
        self._upsert_strategy(baseline_strategy)
        candidates = self.generate_candidates(baseline_profile=baseline_strategy, realm_xvi_summary=realm_xvi_summary, snapshots=snapshots)
        evaluated_entries = []
        for candidate in candidates:
            self._upsert_strategy(candidate)
            strategy, metrics, outcomes = self._evaluate_strategy(
                candidate,
                snapshots,
                realm_xvi_summary=realm_xvi_summary,
                replay_mode="replay",
                baseline_strategy_id=baseline_strategy.strategy_id,
            )
            if portfolio_state:
                metrics = self.portfolio_brain.score_strategy_candidate(
                    strategy_metrics=metrics,
                    portfolio_state=portfolio_state,
                    realm_xvi_summary=realm_xvi_summary,
                    realm_xvii_summary=baseline_metrics,
                )
            promotion, archived = self._apply_promotion_rules(strategy, metrics, baseline_metrics=baseline_metrics)
            strategy.performance_metrics = metrics
            self._update_strategy_metrics(strategy)
            self._store_evaluation(strategy, baseline_strategy.strategy_id, metrics, realm_xvi_summary, "replay", promotion)
            self._append_telemetry("evaluation", {"strategy": strategy.name, "metrics": metrics, "status": promotion}, strategy_id=strategy.strategy_id)
            self._emit_survival_log(strategy, metrics, baseline_metrics=baseline_metrics)
            if promotion in {"candidate", "elite_candidate"}:
                self._log("[REALM-XVII-PROMOTED]", strategy=strategy.name, strategy_id=strategy.strategy_id, status=promotion, overall_score=metrics.get("overall_evolution_score"))
            elif archived:
                self._log("[REALM-XVII-ARCHIVED]", strategy=strategy.name, strategy_id=strategy.strategy_id, reason="weak_strategy")
            else:
                self._log("[REALM-XVII-REJECTED]", strategy=strategy.name, strategy_id=strategy.strategy_id, reason="below_promotion_threshold", overall_score=metrics.get("overall_evolution_score"))
            evaluated_entries.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "name": strategy.name,
                    "status": promotion,
                    "overall_evolution_score": metrics.get("overall_evolution_score"),
                    "edge_score": metrics.get("edge_score"),
                    "risk_score": metrics.get("risk_score"),
                    "stability_score": metrics.get("stability_score"),
                    "drawdown_score": metrics.get("drawdown_score"),
                    "confidence_score": metrics.get("confidence_score"),
                    "best_environment": metrics.get("best_environment"),
                    "weakest_environment": metrics.get("weakest_environment"),
                    "mutation_name": strategy.name,
                    "lineage": self._lineage_trace(strategy),
                    "performance_metrics": metrics,
                    "specialization": strategy.specialization,
                }
            )
        promoted = [entry for entry in evaluated_entries if entry["status"] in {"candidate", "elite_candidate"}]
        rejected = [entry for entry in evaluated_entries if entry["status"] == "rejected"]
        experimental = [entry for entry in evaluated_entries if entry["status"] == "experimental"]
        elite = [entry for entry in evaluated_entries if entry["status"] == "elite_candidate"]
        ranked = self._ranked_summary(evaluated_entries)
        best_environment = ranked[0]["best_environment"] if ranked else "balanced"
        weakest_environment = min(
            (entry["weakest_environment"] for entry in evaluated_entries if entry.get("weakest_environment")),
            default="balanced",
        )
        best_mutation = self._best_mutation(evaluated_entries)
        dangerous_mutation = self._most_dangerous_mutation(evaluated_entries)
        report = {
            "realm": "XVII",
            "report_title": "REALM XVII - STRATEGY EVOLUTION REPORT",
            "generated_at": _now_iso(),
            "strategies_tested": len(evaluated_entries),
            "rejected": len(rejected),
            "promoted": len(promoted),
            "elite_candidates": len(elite),
            "experimental": len(experimental),
            "best_performing_environment": best_environment,
            "weakest_environment": weakest_environment,
            "most_successful_mutation": best_mutation,
            "most_dangerous_mutation": dangerous_mutation,
            "current_recommendation": "continue testing volatility-aware variants",
            "baseline": {
                "strategy_id": baseline_strategy.strategy_id,
                "name": baseline_strategy.name,
                "overall_evolution_score": baseline_metrics.get("overall_evolution_score"),
                "portfolio_adjusted_strategy_score": baseline_metrics.get("portfolio_adjusted_strategy_score"),
                "risk_adjusted_candidate_score": baseline_metrics.get("risk_adjusted_candidate_score"),
                "edge_score": baseline_metrics.get("edge_score"),
                "risk_score": baseline_metrics.get("risk_score"),
                "stability_score": baseline_metrics.get("stability_score"),
                "drawdown_score": baseline_metrics.get("drawdown_score"),
                "confidence_score": baseline_metrics.get("confidence_score"),
            },
            "portfolio": portfolio_state or {},
            "strategies": ranked,
        }
        self.last_report = report
        self._save_report(report)
        self._append_telemetry("report", report)
        self._prune_storage()
        self._print_report(report)
        return report

    def _print_report(self, report):
        print()
        print("REALM XVII - STRATEGY EVOLUTION REPORT")
        print(f"Strategies Tested: {report.get('strategies_tested', 0)}")
        print(f"Rejected: {report.get('rejected', 0)}")
        print(f"Promoted: {report.get('promoted', 0)}")
        print(f"Elite Candidates: {report.get('elite_candidates', 0)}")
        print(f"Best Performing Environment: {report.get('best_performing_environment', 'balanced')}")
        print(f"Weakest Environment: {report.get('weakest_environment', 'balanced')}")
        print(f"Most Successful Mutation: {report.get('most_successful_mutation', 'none')}")
        print(f"Most Dangerous Mutation: {report.get('most_dangerous_mutation', 'none')}")
        print(f"Current Recommendation: {report.get('current_recommendation', 'continue testing volatility-aware variants')}")
        print()
        for strategy in report.get("strategies", [])[:10]:
            print(
                f"[REALM-XVII-EVALUATION] candidate={strategy.get('name')} "
                f"baseline_edge={report.get('baseline', {}).get('edge_score')} "
                f"candidate_edge={strategy.get('edge_score')} drawdown_proxy={strategy.get('drawdown_score')} "
                f"trend_regime_performance={strategy.get('best_environment')} divergence_performance={strategy.get('weakest_environment')} "
                f"result={strategy.get('status')}"
            )

    def _decision_for_live_quote(self, strategy, quote, market=None, realm_xvi_summary=None):
        market = market if isinstance(market, dict) else {}
        quote = quote if quote is not None else None
        if quote is None:
            return {"decision": "SKIP", "reason": "missing_quote"}
        spread = None
        yes_ask = _safe_float(getattr(quote, "yes_ask", None), None)
        yes_bid = _safe_float(getattr(quote, "yes_bid", None), None)
        if yes_ask is not None and yes_bid is not None:
            spread = yes_ask - yes_bid
        snapshot = {
            "ticker": getattr(quote, "ticker", None),
            "score": _safe_float(getattr(quote, "score", None), 0.0),
            "confidence": _safe_float(getattr(quote, "confidence", None), 0.0),
            "spread": spread,
            "liquidity": _safe_float(getattr(quote, "liquidity", None), 0.0) or 0.0,
            "yes_bid": getattr(quote, "yes_bid", None),
            "yes_ask": getattr(quote, "yes_ask", None),
            "no_bid": getattr(quote, "no_bid", None),
            "no_ask": getattr(quote, "no_ask", None),
            "selected_side": "yes",
            "entry_timing_state": market.get("entry_timing_state") or "live_shadow",
        }
        outcome = self._evaluate_entry(strategy, snapshot, realm_xvi_summary=realm_xvi_summary, future_index=None)
        return outcome

    def observe_shadow_cycle(self, markets=None, quotes=None, candidates=None, selected_quote=None, live_select_summary=None, realm_xvi_summary=None, portfolio_state=None, source="live"):
        if not self.enabled:
            self._log("[REALM-XVII-DEGRADED]", reason="disabled")
            return {"ok": False, "reason": "disabled"}
        now_ts = _now_ts()
        markets = [market for market in (markets or []) if isinstance(market, dict)]
        quotes = [quote for quote in (quotes or []) if quote is not None]
        candidates = candidates or []
        live_select_summary = live_select_summary if isinstance(live_select_summary, dict) else {}
        realm_xvi_summary = realm_xvi_summary if isinstance(realm_xvi_summary, dict) else {}
        portfolio_state = portfolio_state if isinstance(portfolio_state, dict) else {}
        selected_quote = selected_quote or (quotes[0] if quotes else None)
        if selected_quote is None:
            return {"ok": False, "reason": "no_live_quote"}
        regime_signature = "|".join(
            [
                str(realm_xvi_summary.get("dominant_regime") or realm_xvi_summary.get("regime_label") or "balanced"),
                str(live_select_summary.get("current_regime") or live_select_summary.get("regime") or "unknown"),
                str(getattr(selected_quote, "ticker", "") or "UNKNOWN"),
            ]
        )
        if self.last_shadow_signature == regime_signature and now_ts - self.last_shadow_ts < self.shadow_throttle_seconds:
            return {"ok": True, "reason": "throttled"}
        self.last_shadow_signature = regime_signature
        self.last_shadow_ts = now_ts
        active_strategies = self._load_active_strategies()
        if not active_strategies:
            active_strategies = [self._seed_baseline_strategy()]
        chosen_strategies = active_strategies[: min(3, len(active_strategies))]
        if len(chosen_strategies) < 3:
            chosen_strategies.extend(self.generate_candidates(active_strategies[0], realm_xvi_summary=realm_xvi_summary)[: 3 - len(chosen_strategies)])
        markets_by_ticker = {str((market or {}).get("ticker") or "").strip(): market for market in markets if isinstance(market, dict)}
        selected_ticker = str(getattr(selected_quote, "ticker", "") or "").strip()
        market = markets_by_ticker.get(selected_ticker, {})
        baseline = self.get_strategy("baseline_v1") or self._seed_baseline_strategy()
        baseline_outcome = self._decision_for_live_quote(baseline, selected_quote, market=market, realm_xvi_summary=realm_xvi_summary)
        baseline_portfolio = {}
        if portfolio_state and self.portfolio_brain is not None and getattr(self.portfolio_brain, "enabled", False):
            try:
                baseline_portfolio = self.portfolio_brain.score_strategy_candidate(
                    strategy_metrics=baseline.performance_metrics or {},
                    portfolio_state=portfolio_state,
                    realm_xvi_summary=realm_xvi_summary,
                    realm_xvii_summary=self.last_report or {},
                )
            except Exception as exc:
                self._log("[REALM-XVIII-DEGRADED]", reason=exc.__class__.__name__)
        self._log(
            "[REALM-XVII-SHADOW]",
            strategy=baseline.name,
            ticker=selected_ticker or "UNKNOWN",
            decision=baseline_outcome.get("decision"),
            reason=baseline_outcome.get("reason"),
            regime=baseline_outcome.get("regime_label"),
            source=source,
        )
        if portfolio_state:
            self._log(
                "[REALM-XVIII-SHADOW]",
                strategy=baseline.name,
                portfolio_health_score=portfolio_state.get("portfolio_health_score"),
                portfolio_mode=portfolio_state.get("portfolio_mode"),
                recommendation=portfolio_state.get("recommendation"),
            )
        comparisons = []
        for strategy in chosen_strategies:
            outcome = self._decision_for_live_quote(strategy, selected_quote, market=market, realm_xvi_summary=realm_xvi_summary)
            candidate_portfolio = {}
            if portfolio_state and self.portfolio_brain is not None and getattr(self.portfolio_brain, "enabled", False):
                try:
                    candidate_portfolio = self.portfolio_brain.score_strategy_candidate(
                        strategy_metrics=strategy.performance_metrics or {},
                        portfolio_state=portfolio_state,
                        realm_xvi_summary=realm_xvi_summary,
                        realm_xvii_summary=self.last_report or {},
                    )
                except Exception as exc:
                    self._log("[REALM-XVIII-DEGRADED]", reason=exc.__class__.__name__)
            compare = {
                "strategy_id": strategy.strategy_id,
                "name": strategy.name,
                "decision": outcome.get("decision"),
                "reason": outcome.get("reason"),
                "baseline_decision": baseline_outcome.get("decision"),
                "baseline_reason": baseline_outcome.get("reason"),
                "delta": 0 if outcome.get("decision") == baseline_outcome.get("decision") else (1 if outcome.get("decision") == "BUY" else -1),
                "portfolio_adjusted_strategy_score": candidate_portfolio.get("portfolio_adjusted_strategy_score"),
                "risk_adjusted_candidate_score": candidate_portfolio.get("risk_adjusted_candidate_score"),
            }
            comparisons.append(compare)
            self._log(
                "[REALM-XVII-VS-BASELINE]",
                strategy=strategy.name,
                baseline_decision=baseline_outcome.get("decision"),
                candidate_decision=outcome.get("decision"),
                delta=compare["delta"],
                regime=outcome.get("regime_label"),
            )
            self._log(
                "[REALM-XVII-CANDIDATE]",
                strategy=strategy.name,
                strategy_id=strategy.strategy_id,
                status=strategy.status,
                environment=_current_environment_label(market, realm_xvi_summary),
            )
            if candidate_portfolio:
                self._log(
                    "[REALM-XVIII-VS-BASELINE]",
                    strategy=strategy.name,
                    portfolio_adjusted_strategy_score=candidate_portfolio.get("portfolio_adjusted_strategy_score"),
                    risk_adjusted_candidate_score=candidate_portfolio.get("risk_adjusted_candidate_score"),
                    baseline_portfolio_adjusted_score=baseline_portfolio.get("portfolio_adjusted_strategy_score"),
                )
        self._append_telemetry(
            "shadow",
            {
                "source": source,
                "selected_ticker": selected_ticker,
                "baseline": baseline_outcome,
                "baseline_portfolio": baseline_portfolio,
                "comparisons": comparisons,
                "selected_market": market,
                "realm_xvi_summary": realm_xvi_summary,
                "portfolio_state": portfolio_state,
            },
            strategy_id=baseline.strategy_id,
        )
        return {"ok": True, "comparisons": comparisons, "baseline": baseline_outcome}

    def export_strategy_summary(self, strategy_id, output_path=None):
        profile = self.get_strategy(strategy_id)
        if profile is None:
            return {"ok": False, "reason": "missing_strategy"}
        payload = {
            "strategy_id": profile.strategy_id,
            "name": profile.name,
            "description": profile.description,
            "origin": profile.origin,
            "status": profile.status,
            "parent_strategy_id": profile.parent_strategy_id,
            "root_strategy_id": profile.root_strategy_id,
            "created_at": profile.created_at,
            "archived_at": profile.archived_at,
            "archived_reason": profile.archived_reason,
            "parameter_overrides": profile.parameter_overrides,
            "performance_metrics": profile.performance_metrics,
            "specialization": profile.specialization,
            "lineage": self._lineage_trace(profile),
        }
        path = output_path or f"{self.export_file}.strategy_{_slugify(profile.name)}.json"
        try:
            _ensure_dir(path)
            with open(path, "w") as handle:
                json.dump(payload, handle, sort_keys=True, indent=2)
        except Exception as exc:
            return {"ok": False, "reason": exc.__class__.__name__}
        return {"ok": True, "path": path, "payload": payload}

    def build_lineage_tree(self, limit=25):
        profiles = self._load_active_strategies()
        if not profiles:
            profiles = [self._seed_baseline_strategy()]
        tree = {}
        for profile in profiles[:limit]:
            lineage = self._lineage_trace(profile)
            tree[profile.strategy_id] = {
                "name": profile.name,
                "status": profile.status,
                "lineage": lineage,
                "best_environment": (profile.performance_metrics or {}).get("best_environment"),
                "overall_evolution_score": (profile.performance_metrics or {}).get("overall_evolution_score"),
            }
            self._log(
                "[REALM-XVII-LINEAGE]",
                strategy=profile.name,
                strategy_id=profile.strategy_id,
                parent=profile.parent_strategy_id or "none",
                status=profile.status,
                score=(profile.performance_metrics or {}).get("overall_evolution_score"),
                lineage=" -> ".join(lineage),
            )
        return tree

    def report_summary(self):
        report = dict(self.last_report or {})
        if not report:
            try:
                with open(self.export_file, "r") as handle:
                    report = json.load(handle)
            except Exception:
                report = {}
        if not report:
            report = {
                "strategies_tested": 0,
                "rejected": 0,
                "promoted": 0,
                "elite_candidates": 0,
                "best_performing_environment": "balanced",
                "weakest_environment": "balanced",
                "most_successful_mutation": "none",
                "most_dangerous_mutation": "none",
                "current_recommendation": "continue testing volatility-aware variants",
            }
        print()
        print("REALM XVII - STRATEGY EVOLUTION REPORT")
        print(f"Strategies Tested: {report.get('strategies_tested', 0)}")
        print(f"Rejected: {report.get('rejected', 0)}")
        print(f"Promoted: {report.get('promoted', 0)}")
        print(f"Elite Candidates: {report.get('elite_candidates', 0)}")
        print(f"Best Performing Environment: {report.get('best_performing_environment', 'balanced')}")
        print(f"Weakest Environment: {report.get('weakest_environment', 'balanced')}")
        print(f"Most Successful Mutation: {report.get('most_successful_mutation', 'none')}")
        print(f"Most Dangerous Mutation: {report.get('most_dangerous_mutation', 'none')}")
        print(f"Current Recommendation: {report.get('current_recommendation', 'continue testing volatility-aware variants')}")
        return report


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--snapshot-file", default="/opt/kalshi-bot/data/simulation_snapshots.jsonl")
    parser.add_argument("--limit", type=int, default=REALM_XVII_REPLAY_SAMPLE_LIMIT)
    args = parser.parse_args()
    engine = StrategyEvolutionEngine()
    if args.replay:
        if RealmXVSimulationWorlds is None:
            print("[REALM-XVII-ERROR] reason=realm_xv_module_unavailable")
            return
        sim = RealmXVSimulationWorlds(snapshot_path=args.snapshot_file, record_enabled=False)
        snapshots = sim.load_snapshots(limit=args.limit)
        realm_xvi_summary = {}
        if snapshots:
            try:
                from cross_market_intelligence import CrossMarketIntelligence

                xvi = CrossMarketIntelligence(enabled=True)
                realm_xvi_summary = xvi.analyze_cycle(realm_xv_snapshots=snapshots, source="realm_xvii_replay")
            except Exception:
                realm_xvi_summary = {}
        engine.run_replay_cycle(snapshots=snapshots, realm_xvi_summary=realm_xvi_summary)
        return
    if args.shadow:
        print("[REALM-XVII-DEGRADED] reason=shadow_mode_requires_live_context")
        return
    if args.report:
        engine.report_summary()
        return
    print("Use --replay to run Realm XVII evolution or --report to print the latest summary.")


if __name__ == "__main__":
    main()
