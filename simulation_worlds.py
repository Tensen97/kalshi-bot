import argparse
import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone


HARD_SAFETY_REASONS = {
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


PROFILE_CONFIGS = {
    "conservative": {
        "min_score": 18.0,
        "min_confidence": 0.70,
        "max_spread": 0.025,
        "min_liquidity": 75.0,
        "min_timing_bonus": 0.05,
        "exit_delay_seconds": 120,
        "risk_off_multiplier": 1.20,
    },
    "balanced": {
        "min_score": 14.0,
        "min_confidence": 0.55,
        "max_spread": 0.035,
        "min_liquidity": 50.0,
        "min_timing_bonus": 0.0,
        "exit_delay_seconds": 300,
        "risk_off_multiplier": 1.0,
    },
    "aggressive": {
        "min_score": 10.0,
        "min_confidence": 0.40,
        "max_spread": 0.045,
        "min_liquidity": 30.0,
        "min_timing_bonus": -0.05,
        "exit_delay_seconds": 600,
        "risk_off_multiplier": 0.85,
    },
}


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(value):
    side = str(value or "").strip().lower()
    if side in {"yes", "no"}:
        return side
    return "yes"


def _parse_ts(value):
    if value in (None, ""):
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
    if ts > 1e12:
        ts = ts / 1000.0
    return ts


@dataclass
class ReplayOutcome:
    profile: str
    decision: str
    result: str
    pnl_proxy: float | None
    hold_seconds: float | None
    reason: str


class RealmXVSimulationWorlds:
    def __init__(
        self,
        snapshot_path="/opt/kalshi-bot/data/simulation_snapshots.jsonl",
        record_enabled=False,
        max_snapshot_mb=50,
        max_snapshot_rows=50000,
        record_interval_seconds=10,
    ):
        self.snapshot_path = snapshot_path
        self.record_enabled = bool(record_enabled)
        self.max_snapshot_bytes = int(max_snapshot_mb * 1024 * 1024)
        self.max_snapshot_rows = int(max_snapshot_rows)
        self.record_interval_seconds = float(record_interval_seconds)
        self.last_recorded_ts_by_ticker = {}
        self.snapshot_count = 0
        self.recording_blocked = False
        self.recording_blocked_reason = None
        self.loaded_rows = 0
        self._initialise_runtime_state()

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _ensure_parent_dir(self):
        directory = os.path.dirname(self.snapshot_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def _initialise_runtime_state(self):
        if not os.path.exists(self.snapshot_path):
            self.loaded_rows = 0
            return
        rows = 0
        try:
            with open(self.snapshot_path, "r") as handle:
                for _line in handle:
                    rows += 1
        except OSError:
            rows = 0
        self.loaded_rows = rows
        self.snapshot_count = rows
        if self.record_enabled and rows >= self.max_snapshot_rows:
            self.recording_blocked = True
            self.recording_blocked_reason = "row_limit_preexisting"
            self._log(
                "[REALM-XV-DISK-GUARD]",
                path=self.snapshot_path,
                rows=rows,
                max_rows=self.max_snapshot_rows,
                action="stop_recording",
            )

    def _current_file_size_bytes(self):
        try:
            return os.path.getsize(self.snapshot_path)
        except OSError:
            return 0

    def _disk_guard_allows_write(self):
        if self.recording_blocked:
            return False, self.recording_blocked_reason or "recording_blocked"
        file_size = self._current_file_size_bytes()
        if file_size >= self.max_snapshot_bytes:
            self.recording_blocked = True
            self.recording_blocked_reason = "size_limit"
            self._log(
                "[REALM-XV-DISK-GUARD]",
                path=self.snapshot_path,
                size_mb=round(file_size / (1024 * 1024), 4),
                max_mb=round(self.max_snapshot_bytes / (1024 * 1024), 4),
                action="stop_recording",
            )
            return False, "size_limit"
        if self.snapshot_count >= self.max_snapshot_rows:
            self.recording_blocked = True
            self.recording_blocked_reason = "row_limit"
            self._log(
                "[REALM-XV-DISK-GUARD]",
                path=self.snapshot_path,
                rows=self.snapshot_count,
                max_rows=self.max_snapshot_rows,
                action="stop_recording",
            )
            return False, "row_limit"
        return True, None

    def record_snapshot(self, snapshot):
        if not self.record_enabled:
            self._log("[REALM-XV-RECORD-SKIP]", reason="disabled")
            return {"ok": False, "reason": "disabled"}
        if not isinstance(snapshot, dict):
            self._log("[REALM-XV-ERROR]", reason="snapshot_not_dict")
            return {"ok": False, "reason": "snapshot_not_dict"}
        ticker = str(snapshot.get("ticker") or "").strip()
        now_ts = _safe_float(snapshot.get("timestamp_ts"), None) or time.time()
        if ticker:
            last_ts = float(self.last_recorded_ts_by_ticker.get(ticker, 0.0) or 0.0)
            if now_ts - last_ts < self.record_interval_seconds:
                self._log(
                    "[REALM-XV-RECORD-SKIP]",
                    ticker=ticker,
                    reason="throttled",
                    seconds_remaining=round(self.record_interval_seconds - (now_ts - last_ts), 2),
                )
                return {"ok": False, "reason": "throttled"}
        allowed, reason = self._disk_guard_allows_write()
        if not allowed:
            self._log("[REALM-XV-RECORD-SKIP]", ticker=ticker or "UNKNOWN", reason=f"disk_guard_{reason}")
            return {"ok": False, "reason": reason}
        row = dict(snapshot)
        row.setdefault("timestamp", datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"))
        row.setdefault("timestamp_ts", now_ts)
        try:
            self._ensure_parent_dir()
            with open(self.snapshot_path, "a") as handle:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            self.snapshot_count += 1
            if ticker:
                self.last_recorded_ts_by_ticker[ticker] = now_ts
            self._log("[REALM-XV-RECORD]", ticker=ticker or "UNKNOWN", path=self.snapshot_path, count=self.snapshot_count)
            return {"ok": True, "reason": "recorded"}
        except Exception as exc:
            self._log("[REALM-XV-ERROR]", ticker=ticker or "UNKNOWN", reason=exc.__class__.__name__)
            return {"ok": False, "reason": exc.__class__.__name__}

    def load_snapshots(self, limit=5000):
        if not os.path.exists(self.snapshot_path):
            return []
        rows = deque(maxlen=limit if limit and limit > 0 else None)
        try:
            with open(self.snapshot_path, "r") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        snapshots = [row for row in rows if isinstance(row, dict)]
        snapshots.sort(key=lambda item: _safe_float(item.get("timestamp_ts"), 0.0) or 0.0)
        return snapshots

    def _entry_score(self, snapshot, profile_name):
        config = PROFILE_CONFIGS[profile_name]
        score = _safe_float(snapshot.get("score"), 0.0) or 0.0
        confidence = _safe_float(snapshot.get("confidence"), 0.0) or 0.0
        spread = _safe_float(snapshot.get("spread"), None)
        liquidity = _safe_float(snapshot.get("liquidity"), 0.0) or 0.0
        timing_state = str(snapshot.get("entry_timing_state") or "").strip().lower()
        timing_bonus = 0.0
        if timing_state in {"entry_ready", "target_window", "buy"}:
            timing_bonus = 0.2
        elif timing_state in {"soft_entry", "borderline"}:
            timing_bonus = 0.05
        elif timing_state in {"hold", "wait"}:
            timing_bonus = -0.05
        profile_bonus = 0.0
        if spread is not None:
            profile_bonus += max(0.0, config["max_spread"] - spread) * 100.0 * 0.05
        profile_bonus += max(0.0, liquidity - config["min_liquidity"]) * 0.01
        composite = score + (confidence * 10.0) + timing_bonus + profile_bonus
        if config["risk_off_multiplier"] > 1.0:
            composite -= max(0.0, 1.0 - confidence) * 1.0
        else:
            composite += max(0.0, confidence - 0.5) * 0.5
        return round(composite, 4)

    def _find_future_price(self, snapshot, side, min_delay_seconds=0):
        ticker = str(snapshot.get("ticker") or "").strip()
        if not ticker:
            return None
        current_ts = _safe_float(snapshot.get("timestamp_ts"), None)
        if current_ts is None:
            return None
        snapshots = self.load_snapshots(limit=self.max_snapshot_rows)
        future_candidates = [
            row for row in snapshots
            if str(row.get("ticker") or "").strip() == ticker
            and _safe_float(row.get("timestamp_ts"), 0.0) is not None
            and _safe_float(row.get("timestamp_ts"), 0.0) > current_ts + min_delay_seconds
        ]
        if not future_candidates:
            return None
        future_candidates.sort(key=lambda item: _safe_float(item.get("timestamp_ts"), 0.0) or 0.0)
        chosen = future_candidates[0]
        price_key = "yes_bid" if side == "yes" else "no_bid"
        price = _safe_float(chosen.get(price_key), None)
        if price is None:
            price_key = "yes_ask" if side == "yes" else "no_ask"
            price = _safe_float(chosen.get(price_key), None)
        if price is None:
            return None
        return {
            "price": price,
            "timestamp_ts": _safe_float(chosen.get("timestamp_ts"), None),
            "hold_seconds": _safe_float(chosen.get("timestamp_ts"), 0.0) - current_ts,
            "snapshot": chosen,
        }

    def _profile_decision(self, snapshot, profile_name):
        config = PROFILE_CONFIGS[profile_name]
        hard_reason = str(snapshot.get("block_reason") or snapshot.get("skip_reason") or "").strip().lower()
        decision_result = str(snapshot.get("decision_result") or "SKIP").strip().upper()
        if hard_reason in HARD_SAFETY_REASONS or any(token in hard_reason for token in ("duplicate", "guardian", "armed", "balance", "liquidity", "spread", "closed", "invalid")):
            return ReplayOutcome(profile_name, "BLOCK", "hard_block", None, None, hard_reason or "hard_block")
        if decision_result == "BLOCK":
            return ReplayOutcome(profile_name, "BLOCK", "block", None, None, hard_reason or "blocked")
        score = self._entry_score(snapshot, profile_name)
        confidence = _safe_float(snapshot.get("confidence"), 0.0) or 0.0
        spread = _safe_float(snapshot.get("spread"), None)
        liquidity = _safe_float(snapshot.get("liquidity"), 0.0) or 0.0
        timing_state = str(snapshot.get("entry_timing_state") or "").strip().lower()
        if spread is not None and spread > config["max_spread"]:
            return ReplayOutcome(profile_name, "SKIP", "wide_spread", None, None, "spread")
        if liquidity < config["min_liquidity"]:
            return ReplayOutcome(profile_name, "SKIP", "low_liquidity", None, None, "liquidity")
        if confidence < config["min_confidence"]:
            return ReplayOutcome(profile_name, "SKIP", "low_confidence", None, None, "confidence")
        if profile_name == "conservative" and timing_state not in {"entry_ready", "target_window", "buy"}:
            return ReplayOutcome(profile_name, "SKIP", "timing_too_early", None, None, "timing")
        if profile_name == "aggressive" and timing_state in {"market_closed", "blocked"}:
            return ReplayOutcome(profile_name, "BLOCK", "guardian_veto", None, None, "guardian_veto")
        if score < config["min_score"]:
            return ReplayOutcome(profile_name, "SKIP", "score_too_low", None, None, "score")
        selected_side = _normalize_side(snapshot.get("selected_side"))
        intended_price = _safe_float(snapshot.get("intended_entry_price"), None)
        if intended_price is None:
            intended_price = _safe_float(snapshot.get("yes_ask") if selected_side == "yes" else snapshot.get("no_ask"), None)
        if intended_price is None:
            return ReplayOutcome(profile_name, "SKIP", "missing_entry_price", None, None, "price")
        future = self._find_future_price(snapshot, selected_side, min_delay_seconds=config["exit_delay_seconds"])
        if future is None:
            return ReplayOutcome(profile_name, "BUY", "insufficient_future_data", None, None, "insufficient_future_data")
        exit_price = future["price"]
        hold_seconds = future["hold_seconds"]
        if selected_side == "yes":
            pnl_proxy = exit_price - intended_price
        else:
            pnl_proxy = intended_price - exit_price
        return ReplayOutcome(profile_name, "BUY", "estimated", round(pnl_proxy, 4), round(hold_seconds, 2), "estimated")

    def replay(self, limit=5000):
        snapshots = self.load_snapshots(limit=limit)
        self._log("[REALM-XV-REPLAY-START]", path=self.snapshot_path, snapshots=len(snapshots))
        if not snapshots:
            self._log("[REALM-XV-INSUFFICIENT-DATA]", reason="no_snapshots")
            return {"ok": False, "reason": "no_snapshots", "snapshots": 0}
        profiles = list(PROFILE_CONFIGS.keys())
        stats = {
            profile: {
                "entries": 0,
                "skips": 0,
                "blocks": 0,
                "wins": 0,
                "losses": 0,
                "insufficient_future_data": 0,
                "pnl_total": 0.0,
                "pnl_samples": 0,
                "tickers": set(),
                "families": set(),
                "blocked_reasons": defaultdict(int),
            }
            for profile in profiles
        }
        total_tickers = set()
        total_families = set()
        for snapshot in snapshots:
            ticker = str(snapshot.get("ticker") or "").strip()
            family = str(snapshot.get("market_family") or snapshot.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
            if ticker:
                total_tickers.add(ticker)
            if family:
                total_families.add(family)
            if str(snapshot.get("decision_result") or "").strip().upper() == "BLOCK" and str(snapshot.get("block_reason") or "").strip().lower() in HARD_SAFETY_REASONS:
                self._log(
                    "[REALM-XV-GUARDIAN-VETO]",
                    ticker=ticker or "UNKNOWN",
                    family=family,
                    reason=snapshot.get("block_reason"),
                )
            for profile in profiles:
                outcome = self._profile_decision(snapshot, profile)
                record = stats[profile]
                record["tickers"].add(ticker)
                record["families"].add(family)
                if outcome.decision == "BLOCK":
                    record["blocks"] += 1
                    record["blocked_reasons"][outcome.reason] += 1
                elif outcome.decision == "SKIP":
                    record["skips"] += 1
                elif outcome.decision == "BUY":
                    record["entries"] += 1
                    if outcome.result == "insufficient_future_data":
                        record["insufficient_future_data"] += 1
                    elif outcome.pnl_proxy is not None:
                        record["pnl_total"] += outcome.pnl_proxy
                        record["pnl_samples"] += 1
                        if outcome.pnl_proxy > 0:
                            record["wins"] += 1
                        elif outcome.pnl_proxy < 0:
                            record["losses"] += 1
                self._log(
                    "[REALM-XV-REPLAY-DECISION]",
                    profile=profile,
                    ticker=ticker or "UNKNOWN",
                    decision=outcome.decision,
                    result=outcome.result,
                    reason=outcome.reason,
                    pnl_proxy=outcome.pnl_proxy,
                    hold_seconds=outcome.hold_seconds,
                )
        summary_lines = []
        best_profile = None
        best_score = None
        for profile in profiles:
            record = stats[profile]
            avg_pnl = (record["pnl_total"] / record["pnl_samples"]) if record["pnl_samples"] else None
            enough_data = record["pnl_samples"] > 0
            if enough_data and (best_score is None or (avg_pnl is not None and avg_pnl > best_score)):
                best_score = avg_pnl
                best_profile = profile
            self._log(
                "[REALM-XV-REPLAY-PROFILE]",
                profile=profile,
                entries=record["entries"],
                skips=record["skips"],
                blocks=record["blocks"],
                wins=record["wins"],
                losses=record["losses"],
                pnl_samples=record["pnl_samples"],
                avg_pnl=round(avg_pnl, 4) if avg_pnl is not None else None,
                insufficient_future_data=record["insufficient_future_data"],
            )
            summary_lines.append(
                {
                    "profile": profile,
                    "entries": record["entries"],
                    "skips": record["skips"],
                    "blocks": record["blocks"],
                    "wins": record["wins"],
                    "losses": record["losses"],
                    "avg_pnl": round(avg_pnl, 4) if avg_pnl is not None else None,
                    "insufficient_future_data": record["insufficient_future_data"],
                    "blocked_reasons": dict(record["blocked_reasons"]),
                }
            )
        enough_data = any(item["avg_pnl"] is not None for item in summary_lines)
        if not enough_data:
            self._log("[REALM-XV-INSUFFICIENT-DATA]", reason="no_future_price_data")
        conclusion = {
            "best_profile": best_profile,
            "best_score": round(best_score, 4) if best_score is not None else None,
            "biggest_weakness": None,
        }
        if best_profile is None:
            conclusion["biggest_weakness"] = "insufficient_future_data"
        else:
            weakest = min(summary_lines, key=lambda item: (item["avg_pnl"] is None, item["avg_pnl"] if item["avg_pnl"] is not None else 0.0))
            conclusion["biggest_weakness"] = weakest["profile"]
        self._log(
            "[REALM-XV-REPLAY-SUMMARY]",
            snapshots=len(snapshots),
            tickers=len(total_tickers),
            families=len(total_families),
            best_profile=best_profile,
            best_score=round(best_score, 4) if best_score is not None else None,
        )
        report = {
            "snapshots": len(snapshots),
            "tickers": len(total_tickers),
            "families": len(total_families),
            "profiles": summary_lines,
            "conclusion": conclusion,
        }
        print("\nREALM XV — SIMULATION WORLDS REPORT")
        print(f"Snapshots analyzed: {report['snapshots']}")
        print(f"Markets analyzed: {report['tickers']}")
        print(f"Families analyzed: {report['families']}")
        print("Profiles tested: conservative, balanced, aggressive")
        for item in summary_lines:
            print(f"\n{item['profile'].capitalize()}:")
            print(f"- Entries: {item['entries']}")
            print(f"- Skips: {item['skips']}")
            print(f"- Blocks: {item['blocks']}")
            if item["avg_pnl"] is None:
                print("- Estimated outcome: insufficient_future_data")
            else:
                print(f"- Estimated outcome: avg_pnl_proxy={item['avg_pnl']}")
        print("\nConclusion:")
        print(f"- Best profile: {conclusion['best_profile'] or 'insufficient_data'}")
        print(f"- Biggest weakness: {conclusion['biggest_weakness'] or 'insufficient_data'}")
        print("- Recommended next experiment: broaden the snapshot window and compare entry timing thresholds")
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--snapshot-file", default="/opt/kalshi-bot/data/simulation_snapshots.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--record-interval", type=float, default=10.0)
    parser.add_argument("--max-mb", type=float, default=50.0)
    parser.add_argument("--max-rows", type=int, default=50000)
    args = parser.parse_args()
    sim = RealmXVSimulationWorlds(
        snapshot_path=args.snapshot_file,
        record_enabled=args.record,
        max_snapshot_mb=args.max_mb,
        max_snapshot_rows=args.max_rows,
        record_interval_seconds=args.record_interval,
    )
    if args.replay:
        sim.replay(limit=args.limit)
        return
    print("Use --replay to generate a Realm XV simulation report.")


if __name__ == "__main__":
    main()
