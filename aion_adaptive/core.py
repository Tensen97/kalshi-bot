"""Five offline adaptive layers for AION. Standard library only.

No broker calls, no production parameter writes, and no automatic code deployment.
Input is a sequence of closed, reconciled trades. All performance numbers are NET.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import hashlib
import json
import math


def _finite(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError("invalid numeric value")
    if not math.isfinite(number):
        raise ValueError("non-finite numeric value")
    return number


class TradeMemory:
    """Append-only JSONL journal with idempotency and strict closed-trade validation."""
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        ids = [str(x["trade_id"]) for x in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate trade IDs in memory")
        return rows

    def append(self, trade):
        required = ("trade_id", "closed_at", "strategy", "family", "side", "net_pnl")
        if not isinstance(trade, dict) or any(not str(trade.get(k, "")).strip() for k in required):
            raise ValueError("closed trade missing required fields")
        if str(trade["side"]).lower() not in ("yes", "no"):
            raise ValueError("invalid side")
        row = dict(trade)
        row["net_pnl"] = _finite(row["net_pnl"])
        if "model_probability" in row:
            p = _finite(row["model_probability"])
            if not 0 <= p <= 1:
                raise ValueError("probability outside [0,1]")
        if "outcome" in row and row["outcome"] not in (0, 1):
            raise ValueError("outcome must be 0 or 1")
        datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
        previous = self.read()
        if any(str(t["trade_id"]) == str(row["trade_id"]) for t in previous):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        return True


def diagnose(trades, *, min_samples=20):
    """Self-diagnosis: report weak segments; never place orders or change limits."""
    buckets = defaultdict(list)
    for t in trades:
        buckets[(str(t["family"]), str(t["side"]), str(t["strategy"]))].append(_finite(t["net_pnl"]))
    report = []
    for (family, side, strategy), pnls in sorted(buckets.items()):
        n = len(pnls)
        report.append({"family": family, "side": side, "strategy": strategy,
                       "trades": n, "net_pnl": round(sum(pnls), 4),
                       "avg_net_pnl": round(sum(pnls)/n, 4),
                       "flag": "insufficient_data" if n < min_samples else ("negative_expectancy" if sum(pnls) < 0 else "monitor")})
    return report


@dataclass(frozen=True)
class Candidate:
    name: str
    config: dict
    rationale: str


def assess_candidate(baseline, challenger, *, min_samples=30, min_improvement=0.0):
    """Conservative out-of-sample comparison of equal-window net trade outcomes.

    Requires a sufficient count for each group. A candidate can be eligible for
    human review, NEVER automatically deployed based on this simple metric.
    """
    a, b = [_finite(x) for x in baseline], [_finite(x) for x in challenger]
    if len(a) < min_samples or len(b) < min_samples:
        return {"eligible_for_review": False, "reason": "insufficient_samples"}
    avg_a, avg_b = sum(a)/len(a), sum(b)/len(b)
    return {"eligible_for_review": avg_b > avg_a + min_improvement and sum(b) > 0,
            "reason": "candidate_better" if avg_b > avg_a + min_improvement and sum(b) > 0 else "no_demonstrated_improvement",
            "baseline_avg_net": round(avg_a, 6), "candidate_avg_net": round(avg_b, 6),
            "baseline_samples": len(a), "candidate_samples": len(b)}


class AdaptiveLab:
    """Research sandbox: proposals, walk-forward evaluations, model-version records."""
    def __init__(self, root):
        self.root = Path(root)

    def propose(self, name, config, rationale):
        if not name or not isinstance(config, dict) or not rationale:
            raise ValueError("invalid proposal")
        return Candidate(name, dict(config), rationale)

    def evaluate(self, candidate, baseline, challenger, *, min_samples=30):
        result = assess_candidate(baseline, challenger, min_samples=min_samples)
        result["candidate"] = candidate.name
        result["config_hash"] = hashlib.sha256(json.dumps(candidate.config, sort_keys=True).encode()).hexdigest()
        return result

    def record_experiment(self, candidate, result):
        """Append immutable experiment evidence; does not modify bot config."""
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now(timezone.utc).isoformat(), "name": candidate.name,
                  "config": candidate.config, "rationale": candidate.rationale, "result": result,
                  "deployment": "never_automatic"}
        with (self.root / "experiments.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        return record

    def register_model(self, name, version, metrics, *, validation_window):
        """Layer 3 model registry; registration never activates a model."""
        if not name or not version or not validation_window:
            raise ValueError("model metadata required")
        clean = {str(k): _finite(v) for k, v in metrics.items()}
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {"name": name, "version": version, "metrics": clean,
                 "validation_window": validation_window, "active": False}
        with (self.root / "model_registry.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def code_change_proposal(self, title, rationale, files, tests):
        """Layer 5: reviewable development proposal; never edits live code."""
        if not title or not rationale or not files or not tests:
            raise ValueError("proposal requires title, rationale, files, tests")
        self.root.mkdir(parents=True, exist_ok=True)
        proposal = {"title": title, "rationale": rationale, "files": list(files),
                    "tests": list(tests), "status": "human_review_required"}
        with (self.root / "code_proposals.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(proposal, sort_keys=True) + "\n")
        return proposal
