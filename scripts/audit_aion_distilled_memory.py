#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from memory.aion_persistent_intelligence import AIONPersistentIntelligence  # noqa: E402
from runtime.paths import runtime_memory_file  # noqa: E402


SPORTS_PREFIXES = ("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXATP", "KXWTA", "KXUFC", "KXNCAA", "KXCFB", "KXCBB")


def _iso_from_ts(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _category_for_family(family):
    family = str(family or "").strip().upper()
    if family.startswith(SPORTS_PREFIXES):
        return "sports"
    if family.startswith("KXBTC") or "CRYPTO" in family:
        return "crypto"
    return "unknown"


def _hydrate_from_archive(engine, *, archive_path, limit):
    if not archive_path.exists():
        return {"archive_path": str(archive_path), "rows_seen": 0, "stored": 0, "skipped": 0, "reason": "missing"}
    rows = archive_path.read_text(encoding="utf-8").splitlines()
    if limit and len(rows) > limit:
        rows = rows[-int(limit):]
    stored = 0
    skipped = 0
    rows_seen = 0
    state_path = Path(engine.state_file)
    original_stat = None
    try:
        original_stat = state_path.stat() if state_path.exists() else None
    except Exception:
        original_stat = None
    original_persist = engine.persist
    engine.persist = lambda: {}
    try:
        for raw in rows:
            rows_seen += 1
            try:
                event = json.loads(raw)
            except Exception:
                skipped += 1
                continue
            if not isinstance(event, dict):
                skipped += 1
                continue
            event_type = str(event.get("event_type") or "").strip().lower()
            event.setdefault("category", _category_for_family(event.get("family")))
            if event_type in {"candidate_seen", "trade_opened"}:
                result = engine.record_distilled_observation(event, throttle_seconds=0)
            elif event_type == "trade_closed":
                result = engine.record_distilled_outcome(event)
            else:
                skipped += 1
                continue
            if result.get("ok") and not result.get("skipped"):
                stored += 1
            else:
                skipped += 1
    finally:
        engine.persist = original_persist
    original_persist()
    if original_stat is not None and hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(state_path, original_stat.st_uid, original_stat.st_gid)
            os.chmod(state_path, original_stat.st_mode & 0o7777)
        except Exception:
            pass
    return {"archive_path": str(archive_path), "rows_seen": rows_seen, "stored": stored, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit AION distilled learning memory.")
    parser.add_argument("--hydrate-from-archive", action="store_true", help="Import recent data/aion_learning_archive.jsonl events first.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum archive rows to hydrate when --hydrate-from-archive is used.")
    args = parser.parse_args()

    engine = AIONPersistentIntelligence(
        state_file=runtime_memory_file("aion_persistent_intelligence.json"),
        enabled=bool(args.hydrate_from_archive),
    )
    hydrate = None
    if args.hydrate_from_archive:
        hydrate = _hydrate_from_archive(
            engine,
            archive_path=REPO_ROOT / "data" / "aion_learning_archive.jsonl",
            limit=args.limit,
        )
    audit = engine.audit_distilled_learning()
    audit["oldest_timestamp"] = _iso_from_ts(audit.get("oldest_timestamp_ts"))
    audit["newest_timestamp"] = _iso_from_ts(audit.get("newest_timestamp_ts"))
    audit["sports_history_oldest_timestamp"] = _iso_from_ts(audit.get("sports_history_oldest_timestamp_ts"))
    audit["sports_history_newest_timestamp"] = _iso_from_ts(audit.get("sports_history_newest_timestamp_ts"))
    if hydrate is not None:
        audit["hydrate"] = hydrate
    print("[AION-MEMORY-AUDIT] " + json.dumps(audit, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
