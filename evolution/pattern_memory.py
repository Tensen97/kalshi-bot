import json
import os
import time
from collections import defaultdict, deque
from runtime.paths import record_persist_event, runtime_state_file, safe_atomic_json_write, ensure_runtime_layout


def _now_iso():
    from datetime import datetime, timezone

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


class PatternMemory:
    def __init__(self, state_file=None, history_limit=500):
        self.state_file = str(state_file or runtime_state_file("pattern_memory.json"))
        ensure_runtime_layout()
        self.history_limit = max(50, int(history_limit))
        self._state = self._load_state()

    def _default_state(self):
        return {
            "updated_at": None,
            "patterns": {},
            "history": [],
            "success_patterns": [],
            "failure_patterns": [],
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
        if not isinstance(state.get("patterns"), dict):
            state["patterns"] = {}
        if not isinstance(state.get("history"), list):
            state["history"] = []
        if not isinstance(state.get("success_patterns"), list):
            state["success_patterns"] = []
        if not isinstance(state.get("failure_patterns"), list):
            state["failure_patterns"] = []
        return state

    def persist(self):
        try:
            _ensure_dir(self.state_file)
            self._state["updated_at"] = _now_iso()
            ok = safe_atomic_json_write(self.state_file, _json_safe(self._state), sort_keys=True, indent=2)
            record_persist_event(subsystem="REALM-XXVI-PATTERN", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
            return bool(ok)
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXVI-PATTERN", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            return False

    def update(self, analyses):
        analyses = list(analyses or [])
        if not analyses:
            return self.get_state()
        self._state["history"] = (_json_safe(list(self._state.get("history", []))) + analyses)[-self.history_limit :]
        grouped = defaultdict(list)
        for item in analyses:
            grouped[str(item.get("setup_signature") or "unknown")].append(item)
            grouped[f"family:{str(item.get('family') or 'unknown').strip().upper()}"].append(item)
            grouped[f"category:{str(item.get('category') or 'unknown').strip().lower()}"].append(item)
            grouped[f"mode:{str(item.get('realm_x_adaptive_mode') or 'unknown').strip().lower()}"].append(item)
            grouped[f"breakout:{str(item.get('realm_xi_breakout_state') or 'unknown').strip().lower()}"].append(item)
        patterns = {}
        success_patterns = []
        failure_patterns = []
        for key, records in grouped.items():
            count = len(records)
            win_rate = sum(1 for row in records if row.get("outcome") == "win") / max(1, count)
            average_pnl = sum(_safe_float(row.get("realized_pnl_delta"), 0.0) for row in records) / max(1, count)
            average_risk = sum(_safe_float(row.get("risk_adjusted_outcome"), 0.0) for row in records) / max(1, count)
            score = (average_pnl * 100.0) + (win_rate * 25.0) + (average_risk * 10.0)
            pattern = {
                "pattern_key": key,
                "count": count,
                "win_rate": round(win_rate, 4),
                "average_pnl": round(average_pnl, 4),
                "average_risk_adjusted_outcome": round(average_risk, 4),
                "score": round(score, 4),
                "last_seen_at": _now_iso(),
            }
            patterns[key] = pattern
            if score >= 8.0:
                success_patterns.append(pattern)
            if score <= -2.0:
                failure_patterns.append(pattern)
        success_patterns = sorted(success_patterns, key=lambda item: (item["score"], item["count"]), reverse=True)[:20]
        failure_patterns = sorted(failure_patterns, key=lambda item: (item["score"], item["count"]))[:20]
        self._state["patterns"] = patterns
        self._state["success_patterns"] = success_patterns
        self._state["failure_patterns"] = failure_patterns
        self.persist()
        return self.get_state()

    def get_state(self):
        return _json_safe(self._state)

    def get_success_patterns(self, limit=10):
        return _json_safe(self._state.get("success_patterns", [])[: max(0, int(limit))])

    def get_failure_patterns(self, limit=10):
        return _json_safe(self._state.get("failure_patterns", [])[: max(0, int(limit))])

