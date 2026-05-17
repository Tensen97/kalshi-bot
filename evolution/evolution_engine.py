import argparse
import json
import os
import sqlite3
import time
from collections import Counter, deque
from datetime import datetime, timezone

try:
    from .evolution_summary import build_summary_text
    from .parameter_scoring import ParameterScorer
    from .pattern_memory import PatternMemory
    from .recommendation_engine import RecommendationEngine
    from .strategy_analysis import aggregate_outcomes, analyze_trade_row
except Exception:
    from evolution.evolution_summary import build_summary_text
    from evolution.parameter_scoring import ParameterScorer
    from evolution.pattern_memory import PatternMemory
    from evolution.recommendation_engine import RecommendationEngine
    from evolution.strategy_analysis import aggregate_outcomes, analyze_trade_row


REALM_XXVI_ENABLE = (os.environ.get("REALM_XXVI_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXVI_STATE_FILE = os.environ.get("REALM_XXVI_STATE_FILE", "/opt/kalshi-bot/data/realm_xxvi_evolution.json")
REALM_XXVI_HISTORY_LIMIT = int(float(os.environ.get("REALM_XXVI_HISTORY_LIMIT", "500") or 500))
REALM_XXVI_SUMMARY_INTERVAL_SECONDS = float(os.environ.get("REALM_XXVI_SUMMARY_INTERVAL_SECONDS", "60") or 60)
REALM_XXVI_RECOMMENDATION_CAP = int(float(os.environ.get("REALM_XXVI_RECOMMENDATION_CAP", "10") or 10))
REALM_XXVI_MEMORY_DB_PATH = os.environ.get("REALM_XXVI_MEMORY_DB_PATH", "/opt/kalshi-bot/seraph_memory.sqlite3")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


class EvolutionEngine:
    def __init__(
        self,
        state_file=REALM_XXVI_STATE_FILE,
        memory_db_path=REALM_XXVI_MEMORY_DB_PATH,
        enabled=REALM_XXVI_ENABLE,
        history_limit=REALM_XXVI_HISTORY_LIMIT,
        summary_interval_seconds=REALM_XXVI_SUMMARY_INTERVAL_SECONDS,
        recommendation_cap=REALM_XXVI_RECOMMENDATION_CAP,
    ):
        self.state_file = state_file
        self.memory_db_path = memory_db_path
        self.enabled = bool(enabled)
        self.history_limit = max(100, int(history_limit))
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds))
        self.recommendation_cap = max(3, int(recommendation_cap))
        self.started_at = time.time()
        self.last_update_ts = 0.0
        self.last_report = {}
        self.pattern_memory = PatternMemory(state_file=f"{self.state_file}.patterns.json", history_limit=self.history_limit)
        self.parameter_scorer = ParameterScorer()
        self.recommendation_engine = RecommendationEngine(recommendation_cap=self.recommendation_cap)
        self._state = self._load_state()
        self._history = deque(maxlen=self.history_limit)

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _default_state(self):
        return {
            "schema_version": 1,
            "updated_at": None,
            "enabled": bool(self.enabled),
            "analysis_summary": {
                "total_trades": 0,
                "win_rate": 0.0,
                "average_pnl": 0.0,
                "average_risk_adjusted_outcome": 0.5,
            },
            "parameter_scores": {"parameters": {}, "parameter_leaders": [], "parameter_laggards": []},
            "pattern_summary": {"patterns": {}, "history": [], "success_patterns": [], "failure_patterns": []},
            "recommendations": [],
            "health": {
                "health_score": 0.5,
                "health_state": "CAUTION",
                "stability_score": 0.5,
                "recommendation_count": 0,
                "parameter_leaders": 0,
                "parameter_laggards": 0,
                "strong_pattern_count": 0,
                "weak_pattern_count": 0,
            },
            "cross_realm": {},
            "last_summary_text": "",
            "history": [],
        }

    def _load_state(self):
        if not os.path.exists(self.state_file):
            return self._default_state()
        try:
            with open(self.state_file, "r") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return self._default_state()
        except Exception:
            return self._default_state()
        state = self._default_state()
        state.update(payload)
        if not isinstance(state.get("history"), list):
            state["history"] = []
        return state

    def _persist_state(self):
        try:
            _ensure_dir(self.state_file)
            self._state["updated_at"] = _now_iso()
            tmp_path = f"{self.state_file}.tmp"
            with open(tmp_path, "w") as handle:
                json.dump(_json_safe(self._state), handle, sort_keys=True, indent=2)
            os.replace(tmp_path, self.state_file)
            return True
        except Exception as exc:
            self._log("[REALM-XXVI-ERROR]", reason=exc.__class__.__name__, action="persist_state")
            return False

    def _connect(self):
        if not self.memory_db_path:
            return None
        if not os.path.exists(self.memory_db_path):
            return None
        conn = sqlite3.connect(self.memory_db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_trade_rows(self, limit=None):
        limit = max(1, int(limit or self.history_limit))
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """
                SELECT
                    id, timestamp, timestamp_ts, ticker, category, family, series_ticker, side,
                    entry_price, exit_price, realized_pnl_delta, hold_seconds, outcome, exit_reason,
                    realm_ix_awareness_score, realm_x_adaptive_score, realm_x_adaptive_mode,
                    realm_x_take_profit_mode, realm_x_stop_behavior_mode,
                    realm_xi_predictive_score, realm_xi_momentum_direction, realm_xi_momentum_strength,
                    realm_xi_breakout_state, realm_xii_position_score, realm_xii_position_health,
                    realm_xii_conviction_level, setup_signature, memory_tags_json, notes_json, trade_mode, created_at
                FROM seraph_trade_memory
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            self._log("[REALM-XXVI-ERROR]", reason=exc.__class__.__name__, action="load_trade_rows")
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _cross_realm_context(self, bot=None):
        bot = bot or None
        def _summary(attr_name, fallback=None):
            value = getattr(bot, attr_name, None) if bot is not None else None
            if isinstance(value, dict):
                return value
            return fallback or {}

        personality = _summary("last_realm_xxi_summary")
        probability = _summary("last_realm_xxiii_summary")
        risk = _summary("last_realm_xxiv_summary")
        external = _summary("last_realm_xxv_summary")
        capital_state = (risk.get("last_global_state", {}) or {}).get("capital_state") if isinstance(risk, dict) else None
        return {
            "personality": personality,
            "probability": probability,
            "risk": risk,
            "external": external,
            "capital_state": capital_state,
            "external_risk_level": str((external.get("event_risk", {}) or {}).get("risk_level") or "NONE").upper(),
        }

    def _health_from_state(self, analysis_summary, pattern_state, parameter_state, recommendations, cross_realm):
        win_rate = _safe_float(analysis_summary.get("win_rate"), 0.5)
        average_pnl = _safe_float(analysis_summary.get("average_pnl"), 0.0)
        risk_adjusted = _safe_float(analysis_summary.get("average_risk_adjusted_outcome"), 0.5)
        personality_alignment = _safe_float(analysis_summary.get("personality_alignment"), 0.5)
        strong_count = len(pattern_state.get("success_patterns", []) or [])
        weak_count = len(pattern_state.get("failure_patterns", []) or [])
        parameter_leaders = len(parameter_state.get("parameter_leaders", []) or [])
        parameter_laggards = len(parameter_state.get("parameter_laggards", []) or [])
        recommendation_count = len(recommendations or [])
        base = (
            0.18
            + win_rate * 0.28
            + max(0.0, average_pnl) * 1.2
            + risk_adjusted * 0.18
            + personality_alignment * 0.12
        )
        if average_pnl < 0.0:
            base += max(-0.20, average_pnl * 1.5)
        if weak_count > strong_count:
            base -= 0.08
        if parameter_laggards > parameter_leaders:
            base -= 0.05
        if any(r.get("risk_level") in {"high", "extreme"} for r in recommendations or []):
            base -= 0.08
        if cross_realm.get("external_risk_level") in {"HIGH", "EXTREME"}:
            base -= 0.06
        score = max(0.0, min(1.0, base))
        if score >= 0.80:
            state = "HEALTHY"
        elif score >= 0.65:
            state = "STABLE"
        elif score >= 0.50:
            state = "CAUTION"
        elif score >= 0.35:
            state = "DEGRADED"
        else:
            state = "UNSTABLE"
        return {
            "health_score": round(score, 4),
            "health_state": state,
            "stability_score": round(max(0.0, min(1.0, 0.5 + win_rate * 0.2 + risk_adjusted * 0.2)), 4),
            "recommendation_count": recommendation_count,
            "parameter_leaders": parameter_leaders,
            "parameter_laggards": parameter_laggards,
            "strong_pattern_count": strong_count,
            "weak_pattern_count": weak_count,
        }

    def evaluate(self, bot=None, source="live", force=False, live_select_summary=None):
        now_ts = time.time()
        if not self.enabled and not force:
            return self.get_state()
        if not force and self.last_update_ts and (now_ts - self.last_update_ts) < self.summary_interval_seconds:
            return self.get_state()
        trade_rows = self._load_trade_rows(limit=self.history_limit)
        analyses = [analyze_trade_row(row) for row in trade_rows]
        analysis_summary = aggregate_outcomes(analyses)
        pattern_state = self.pattern_memory.update(analyses)
        parameter_state = self.parameter_scorer.score(analyses)
        cross_realm = self._cross_realm_context(bot=bot)
        recommendations = self.recommendation_engine.generate(
            analysis_summary=analysis_summary,
            pattern_state=pattern_state,
            parameter_state=parameter_state,
            realm_context={
                **cross_realm,
                "live_select_summary": live_select_summary or {},
                "source": source,
                "external_context_count": len((cross_realm.get("external") or {}).get("active_catalysts", []) or []),
            },
        )
        health = self._health_from_state(analysis_summary, pattern_state, parameter_state, recommendations, cross_realm)
        ecosystem_state = {
            "enabled": bool(self.enabled),
            "source": source,
            "analysis_summary": analysis_summary,
            "pattern_summary": pattern_state,
            "parameter_scores": parameter_state,
            "recommendations": recommendations,
            "health": health,
            "cross_realm": cross_realm,
        }
        summary_text = build_summary_text({**ecosystem_state, "health": health})
        ecosystem_state["last_summary_text"] = summary_text
        self._state.update(ecosystem_state)
        self._state["history"] = (list(self._state.get("history", [])) + [
            {
                "updated_at": _now_iso(),
                "source": source,
                "health_score": health.get("health_score"),
                "recommendation_count": len(recommendations),
            }
        ])[-self.history_limit :]
        self.last_report = _json_safe(ecosystem_state)
        self.last_update_ts = now_ts
        self._persist_state()
        if analyses:
            strongest = pattern_state.get("success_patterns", [])[:3]
            weakest = pattern_state.get("failure_patterns", [])[:3]
            self._log(
                "[EVOLUTION-ANALYSIS]",
                source=source,
                trades=len(analyses),
                win_rate=analysis_summary.get("win_rate"),
                average_pnl=analysis_summary.get("average_pnl"),
                health_state=health.get("health_state"),
            )
            for row in analyses[:5]:
                self._log(
                    "[TRADE-OUTCOME]",
                    ticker=row.get("ticker"),
                    family=row.get("family"),
                    outcome=row.get("outcome"),
                    pnl=row.get("realized_pnl_delta"),
                    risk_adjusted=row.get("risk_adjusted_outcome"),
                )
            self._log(
                "[STRATEGY-EVALUATION]",
                total_trades=analysis_summary.get("total_trades"),
                personality_alignment=analysis_summary.get("personality_alignment"),
                conviction_accuracy=analysis_summary.get("conviction_accuracy"),
                edge_accuracy=analysis_summary.get("edge_accuracy"),
            )
            for pattern in strongest:
                self._log("[PATTERN-SUCCESS]", pattern_key=pattern.get("pattern_key"), count=pattern.get("count"), score=pattern.get("score"))
            for pattern in weakest:
                self._log("[PATTERN-FAILURE]", pattern_key=pattern.get("pattern_key"), count=pattern.get("count"), score=pattern.get("score"))
            self._log(
                "[PATTERN-MEMORY]",
                success_patterns=len(pattern_state.get("success_patterns", []) or []),
                failure_patterns=len(pattern_state.get("failure_patterns", []) or []),
            )
            for name, detail in list(parameter_state.get("parameters", {}).items())[:5]:
                self._log(
                    "[PARAMETER-SCORE]",
                    parameter_key=name,
                    score=detail.get("score"),
                    label=detail.get("label"),
                    count=detail.get("count"),
                )
                if detail.get("label") in {"degrading", "unstable"}:
                    self._log("[PARAMETER-WEAK]", parameter_key=name, score=detail.get("score"), label=detail.get("label"))
                elif detail.get("label") in {"improving", "highly_reliable"}:
                    self._log("[PARAMETER-STRONG]", parameter_key=name, score=detail.get("score"), label=detail.get("label"))
            for recommendation in recommendations[: self.recommendation_cap]:
                self._log(
                    "[RECOMMENDATION]",
                    title=recommendation.get("title"),
                    confidence=recommendation.get("confidence"),
                    evidence_count=recommendation.get("evidence_count"),
                    risk_level=recommendation.get("risk_level"),
                )
                if _safe_float(recommendation.get("confidence"), 0.0) >= 0.85:
                    self._log("[RECOMMENDATION-HIGH]", title=recommendation.get("title"), confidence=recommendation.get("confidence"))
                else:
                    self._log("[RECOMMENDATION-WEAK]", title=recommendation.get("title"), confidence=recommendation.get("confidence"))
            self._log(
                "[ECOSYSTEM-HEALTH]",
                health_score=health.get("health_score"),
                health_state=health.get("health_state"),
                stability_score=health.get("stability_score"),
            )
            self._log(
                "[STRATEGY-HEALTH]",
                recommendation_count=health.get("recommendation_count"),
                strong_pattern_count=health.get("strong_pattern_count"),
                weak_pattern_count=health.get("weak_pattern_count"),
            )
            self._log(
                "[EVOLUTION-STATE]",
                enabled="yes" if self.enabled else "no",
                health_state=health.get("health_state"),
                source=source,
            )
            self._log(
                "[REALM-INTEGRATION]",
                personality_state=(cross_realm.get("personality") or {}).get("summary_state") or (cross_realm.get("personality") or {}).get("personality_state"),
                probability_state=(cross_realm.get("probability") or {}).get("final_signal"),
                capital_state=(cross_realm.get("risk") or {}).get("last_global_state", {}).get("capital_state"),
                external_risk_level=cross_realm.get("external_risk_level"),
            )
            self._log(
                "[CROSS-REALM-ANALYSIS]",
                personality_alignment=analysis_summary.get("personality_alignment"),
                edge_accuracy=analysis_summary.get("edge_accuracy"),
                risk_adjusted_outcome=analysis_summary.get("average_risk_adjusted_outcome"),
            )
            for recommendation in recommendations[:3]:
                self._log(
                    "[SUGGESTED-ADJUSTMENT]",
                    title=recommendation.get("title"),
                    expected_impact=recommendation.get("expected_impact"),
                    risk_level=recommendation.get("risk_level"),
                )
            self._log(
                "[SAFE-EVOLUTION]",
                recommendation_count=len(recommendations),
                live_mutation="no",
                live_rewrite="no",
            )
            self._log(
                "[EVOLUTION-SUMMARY]",
                ecosystem_health=health.get("health_state"),
                recommendation_count=len(recommendations),
                strongest_patterns=len(pattern_state.get("success_patterns", []) or []),
                failing_patterns=len(pattern_state.get("failure_patterns", []) or []),
            )
            self._log(
                "[EVOLUTION-HEALTH]",
                health_score=health.get("health_score"),
                stability_score=health.get("stability_score"),
            )
            self._log(
                "[EVOLUTION-PATTERNS]",
                strong=len(pattern_state.get("success_patterns", []) or []),
                weak=len(pattern_state.get("failure_patterns", []) or []),
            )
            self._log(
                "[EVOLUTION-RECOMMENDATIONS]",
                count=len(recommendations),
            )
        return self.get_state()

    def maybe_refresh(self, bot=None, force=False, source="live", live_select_summary=None):
        if force:
            return self.evaluate(bot=bot, source=source, force=True, live_select_summary=live_select_summary)
        if not self.enabled:
            return self.get_state()
        now_ts = time.time()
        if (now_ts - float(self.last_update_ts or 0.0)) >= self.summary_interval_seconds:
            return self.evaluate(bot=bot, source=source, force=False, live_select_summary=live_select_summary)
        return self.get_state()

    def get_state(self):
        return _json_safe(self._state)

    def get_summary(self):
        return _json_safe({
            "analysis_summary": self._state.get("analysis_summary", {}),
            "recommendations": self._state.get("recommendations", []),
            "health": self._state.get("health", {}),
            "cross_realm": self._state.get("cross_realm", {}),
        })

    def get_recommendations(self):
        return _json_safe(self._state.get("recommendations", []))

    def get_patterns(self):
        return _json_safe(self._state.get("pattern_summary", {}))

    def get_health(self):
        return _json_safe(self._state.get("health", {}))

    def report_summary(self):
        state = self.get_state()
        print()
        print(build_summary_text(state))
        return state


def main():
    parser = argparse.ArgumentParser(description="Realm XXVI Evolution Engine")
    parser.add_argument("--report", action="store_true", help="Print the latest report")
    parser.add_argument("--state-file", default=REALM_XXVI_STATE_FILE)
    parser.add_argument("--memory-db", default=REALM_XXVI_MEMORY_DB_PATH)
    parser.add_argument("--history-limit", type=int, default=REALM_XXVI_HISTORY_LIMIT)
    parser.add_argument("--summary-interval-seconds", type=float, default=REALM_XXVI_SUMMARY_INTERVAL_SECONDS)
    args = parser.parse_args()
    engine = EvolutionEngine(
        state_file=args.state_file,
        memory_db_path=args.memory_db,
        history_limit=args.history_limit,
        summary_interval_seconds=args.summary_interval_seconds,
    )
    if args.report:
        engine.evaluate(force=True, source="cli_report")
        engine.report_summary()
    else:
        print(json.dumps(engine.get_state(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
