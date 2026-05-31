"""Best-effort Obsidian markdown export for passive Kalshi bot memory.

This module must never be required for scanning, selection, execution, exits,
or service startup. Every public function catches filesystem failures and
returns a status dictionary instead of raising.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OBSIDIAN_VAULT_PATH = "/opt/kalshi-brain/obsidian-vault"
OBSIDIAN_FOLDERS = (
    "Sessions",
    "Trades",
    "Families",
    "Patterns",
    "Hypotheses",
    "Telemetry",
    "Evolution",
    "Constellation",
)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_WHITESPACE_RE = re.compile(r"\s+")

_CANONICAL_CONCEPT_ALIASES = {
    "Adaptive Confidence": "Constellation/Adaptive Confidence",
    "Candidate Selection": "Constellation/Candidate Selection",
    "Entry Timing": "Constellation/Entry Timing",
    "Execution": "Constellation/Execution Intelligence",
    "Execution Intelligence": "Constellation/Execution Intelligence",
    "Execution Logic": "Constellation/Execution Intelligence",
    "Exit Logic": "Constellation/Exit Logic",
    "Family Memory": "Constellation/Family Memory",
    "Kalshi Bot": "Constellation/AION Core",
    "Liquidity": "Constellation/Liquidity",
    "Market Telemetry": "Constellation/Telemetry",
    "MLB": "Constellation/MLB",
    "Momentum": "Constellation/Momentum",
    "NBA": "Constellation/NBA",
    "Order Execution": "Constellation/Execution Intelligence",
    "Spread Dynamics": "Constellation/Spread Dynamics",
    "Sports": "Constellation/Sports",
    "Telemetry": "Constellation/Telemetry",
    "Trade Outcomes": "Constellation/Exit Logic",
}

_SPORTS_FAMILY_MARKERS = ("NBA", "MLB", "NCAAB", "NCAAF", "NFL", "NHL", "ATP", "WTA", "TENNIS")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _export_enabled():
    return (os.environ.get("OBSIDIAN_EXPORT_ENABLED", "1") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _vault_path(vault_path=None):
    return Path(str(vault_path or os.environ.get("OBSIDIAN_VAULT_PATH") or DEFAULT_OBSIDIAN_VAULT_PATH))


def _status(ok=False, reason="unknown", **fields):
    payload = {"ok": bool(ok), "reason": str(reason or "unknown")}
    payload.update(fields)
    return payload


def _log(marker, **fields):
    try:
        payload = {key: value for key, value in fields.items() if value is not None}
        print(f"{marker} " + json.dumps(payload, sort_keys=True, default=str))
    except Exception:
        try:
            print(marker)
        except Exception:
            pass


def sanitize_filename(value, fallback="untitled", max_length=120):
    try:
        text = str(value or "").strip()
        text = text.replace("/", "-").replace("\\", "-").replace(":", "-")
        text = _SAFE_FILENAME_RE.sub("", text)
        text = _WHITESPACE_RE.sub(" ", text).strip(" .-_")
        if not text:
            text = fallback
        if len(text) > max_length:
            text = text[:max_length].rstrip(" .-_") or fallback
        return text
    except Exception:
        return fallback


def _yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return json.dumps(text)


def _frontmatter(fields):
    lines = ["---"]
    for key, value in (fields or {}).items():
        safe_key = sanitize_filename(key, fallback="field").replace(" ", "_").lower()
        lines.append(f"{safe_key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _wikilink(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("[", "").replace("]", "")
    return f"[[{text}]]"


def _normalize_related_node(value):
    text = str(value or "").strip()
    if not text:
        return None
    direct = _CANONICAL_CONCEPT_ALIASES.get(text)
    if direct:
        return direct
    family_text = text.upper()
    if family_text.startswith("KX") and family_text.replace("_", "").replace("-", "").isalnum():
        return f"Constellation/{family_text} Family"
    return text


def _family_concept_nodes(family):
    family_text = str(family or "").strip().upper()
    nodes = []
    if any(marker in family_text for marker in _SPORTS_FAMILY_MARKERS):
        nodes.append("Sports")
    if "NBA" in family_text:
        nodes.append("NBA")
    if "MLB" in family_text:
        nodes.append("MLB")
    if "WNBA" in family_text:
        nodes.append("WNBA")
    if "NHL" in family_text:
        nodes.append("NHL")
    if "NCAAB" in family_text:
        nodes.append("NCAAB")
    if "ATP" in family_text or "ITF" in family_text or "TENNIS" in family_text:
        nodes.append("Tennis")
    return nodes


def _facts_contain(facts, needles):
    if not isinstance(facts, dict):
        return False
    haystack = " ".join(str(key).lower() for key in facts.keys())
    return any(needle in haystack for needle in needles)


def _canonical_related_nodes(related=None, facts=None, family=None, include=None):
    facts = facts if isinstance(facts, dict) else {}
    nodes = []

    def add(value):
        value = _normalize_related_node(value)
        if value and value not in nodes:
            nodes.append(value)

    for value in include or []:
        add(value)
    for value in related or []:
        add(value)
    for value in _family_concept_nodes(family or facts.get("family")):
        add(value)

    if _facts_contain(facts, ("liquidity", "top_book", "topbook", "top_size", "depth", "book")):
        add("Liquidity")
    if _facts_contain(facts, ("spread", "bid", "ask")):
        add("Spread Dynamics")
    if _facts_contain(facts, ("entry_timing", "timing")):
        add("Entry Timing")
    if _facts_contain(facts, ("exit", "pnl", "result", "hold")):
        add("Exit Logic")
    if _facts_contain(facts, ("candidate", "selection", "selected")):
        add("Candidate Selection")
    if _facts_contain(facts, ("confidence", "score", "ev", "probability", "conviction")):
        add("Adaptive Confidence")
    if _facts_contain(facts, ("momentum", "regime")):
        add("Momentum")
    if _facts_contain(facts, ("quote", "telemetry", "pattern", "maker", "imbalance", "survivability")):
        add("Telemetry")
    if _facts_contain(facts, ("action", "side", "price", "execution")):
        add("Execution Intelligence")
    return nodes


def _compact_fact_value(value):
    if value is None or value == "":
        return None
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        values = [_compact_fact_value(item) for item in value]
        values = [item for item in values if item is not None]
        return values[:12]
    if isinstance(value, dict):
        compact = {}
        for key, item in value.items():
            compact_value = _compact_fact_value(item)
            if compact_value is not None:
                compact[str(key)] = compact_value
        return compact
    return str(value)


def _compact_facts(facts=None, allowed_keys=None):
    facts = facts if isinstance(facts, dict) else {}
    allowed = set(allowed_keys or [])
    compact = {}
    for key, value in facts.items():
        if allowed and key not in allowed:
            continue
        compact_value = _compact_fact_value(value)
        if compact_value is not None:
            compact[str(key)] = compact_value
    return compact


def _facts_line(facts):
    facts = facts if isinstance(facts, dict) else {}
    parts = []
    for key in sorted(facts):
        value = facts.get(key)
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, sort_keys=True, default=str)
        else:
            value_text = str(value)
        parts.append(f"{key}={value_text}")
    return "; ".join(parts) if parts else "observation recorded"


def _resolve_note_path(path, vault_path=None):
    base = _vault_path(vault_path)
    try:
        base = base.resolve(strict=False)
        path_obj = Path(str(path))
        if not path_obj.is_absolute():
            path_obj = base / path_obj
        resolved = path_obj.resolve(strict=False)
        resolved.relative_to(base)
        return resolved, base
    except ValueError:
        return None, base
    except Exception as exc:
        try:
            _log("[OBSIDIAN-EXPORT-ERROR]", action="resolve_path", path=str(path), reason=exc.__class__.__name__)
        except Exception:
            pass
        return None, _vault_path(vault_path)


def ensure_obsidian_vault(vault_path=None):
    if not _export_enabled():
        _log("[OBSIDIAN-EXPORT-SKIP]", action="ensure_vault", reason="disabled")
        return _status(False, "disabled")
    root = _vault_path(vault_path)
    try:
        root.mkdir(parents=True, exist_ok=True)
        for folder in OBSIDIAN_FOLDERS:
            (root / folder).mkdir(parents=True, exist_ok=True)
        _log("[OBSIDIAN-EXPORT-OK]", action="ensure_vault", path=str(root))
        return _status(True, "ready", path=str(root))
    except Exception as exc:
        _log("[OBSIDIAN-EXPORT-SKIP]", action="ensure_vault", path=str(root), reason=exc.__class__.__name__)
        return _status(False, exc.__class__.__name__, path=str(root))


def safe_write_markdown(path, content, *, append=False, overwrite=False, vault_path=None):
    if not _export_enabled():
        _log("[OBSIDIAN-EXPORT-SKIP]", action="write", reason="disabled")
        return _status(False, "disabled")
    vault_status = ensure_obsidian_vault(vault_path=vault_path)
    if not vault_status.get("ok"):
        return _status(False, vault_status.get("reason", "vault_unavailable"), path=str(path))
    target, base = _resolve_note_path(path, vault_path=vault_path)
    if target is None:
        _log("[OBSIDIAN-EXPORT-SKIP]", action="write", path=str(path), reason="path_outside_vault")
        return _status(False, "path_outside_vault", path=str(path))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(str(content or ""))
            _log("[OBSIDIAN-EXPORT-OK]", action="append", path=str(target.relative_to(base)))
            return _status(True, "appended", path=str(target))
        if target.exists() and not overwrite:
            _log("[OBSIDIAN-EXPORT-SKIP]", action="write", path=str(target.relative_to(base)), reason="exists")
            return _status(False, "exists", path=str(target))
        mode = "w" if overwrite else "x"
        with target.open(mode, encoding="utf-8") as handle:
            handle.write(str(content or ""))
        _log("[OBSIDIAN-EXPORT-OK]", action="write", path=str(target.relative_to(base)))
        return _status(True, "written", path=str(target))
    except FileExistsError:
        _log("[OBSIDIAN-EXPORT-SKIP]", action="write", path=str(target), reason="exists")
        return _status(False, "exists", path=str(target))
    except Exception as exc:
        _log("[OBSIDIAN-EXPORT-ERROR]", action="write", path=str(target), reason=exc.__class__.__name__)
        return _status(False, exc.__class__.__name__, path=str(target))


def _ensure_note_then_append(relative_path, initial_content, append_content, vault_path=None, append_after_create=True):
    target, _base = _resolve_note_path(relative_path, vault_path=vault_path)
    if target is None:
        _log("[OBSIDIAN-EXPORT-SKIP]", action="append_note", path=str(relative_path), reason="path_outside_vault")
        return _status(False, "path_outside_vault")
    try:
        note_exists = target.exists()
    except Exception as exc:
        _log("[OBSIDIAN-EXPORT-ERROR]", action="exists", path=str(target), reason=exc.__class__.__name__)
        return _status(False, exc.__class__.__name__, path=str(target))
    if not note_exists:
        created = safe_write_markdown(relative_path, initial_content, vault_path=vault_path)
        if not created.get("ok") and created.get("reason") != "exists":
            return created
        if not append_after_create:
            return created
    return safe_write_markdown(relative_path, append_content, append=True, vault_path=vault_path)


def append_session_intelligence(event_type=None, facts=None, timestamp=None, source=None, related=None, vault_path=None):
    timestamp = str(timestamp or _utc_now())
    day = timestamp[:10] if len(timestamp) >= 10 else datetime.now(timezone.utc).date().isoformat()
    relative_path = Path("Sessions") / f"{sanitize_filename(day)} Session.md"
    facts = _compact_facts(facts)
    related_links = [_wikilink(item) for item in _canonical_related_nodes(related or ["Telemetry"], facts=facts)]
    related_links = [item for item in related_links if item]
    initial = (
        _frontmatter(
            {
                "type": "session_intelligence",
                "date": day,
                "created": timestamp,
                "status": "passive_observation",
            }
        )
        + f"# Session {day}\n\n"
        + "## Observations\n"
    )
    line = f"- {timestamp} - {event_type or 'session_event'}"
    if source:
        line += f" source={source}"
    line += f": {_facts_line(facts)}"
    if related_links:
        line += f" | related: {', '.join(related_links)}"
    line += "\n"
    return _ensure_note_then_append(relative_path, initial, line, vault_path=vault_path)


def record_family_observation(family, facts=None, observation=None, timestamp=None, related=None, vault_path=None):
    timestamp = str(timestamp or _utc_now())
    family = str(family or "UNKNOWN").strip().upper() or "UNKNOWN"
    facts = _compact_facts(facts)
    related_nodes = _canonical_related_nodes(
        related or ["Family Memory", "Entry Timing", "Liquidity"],
        facts=facts,
        family=family,
        include=["Family Memory"],
    )
    related_links = [_wikilink(item) for item in related_nodes]
    related_links = [item for item in related_links if item]
    relative_path = Path("Families") / f"{sanitize_filename(family)}.md"
    initial = (
        _frontmatter(
            {
                "type": "family_memory",
                "family": family,
                "last_updated": timestamp,
                "confidence": 0.0,
                "status": "passive_observation",
            }
        )
        + f"# {family}\n\n"
        + "## Current Observations\n"
        + f"- {timestamp} - {observation or _facts_line(facts)}\n\n"
        + "## Known Patterns\n"
        + "- Placeholder until real repeated evidence exists.\n\n"
        + "## Trade Outcomes\n"
        + "- Links to related trade notes.\n\n"
        + "## Related Nodes\n"
        + "".join(f"- {link}\n" for link in related_links)
    )
    append = f"\n## Current Observations\n- {timestamp} - {observation or _facts_line(facts)}\n"
    return _ensure_note_then_append(relative_path, initial, append, vault_path=vault_path, append_after_create=False)


def record_trade_outcome(
    ticker=None,
    family=None,
    timestamp=None,
    status="closed",
    action=None,
    side=None,
    price=None,
    entry_price=None,
    exit_price=None,
    confidence=None,
    ev=None,
    probability=None,
    spread=None,
    liquidity=None,
    top_book=None,
    exit_reason=None,
    pnl=None,
    result=None,
    hold_time=None,
    facts=None,
    vault_path=None,
):
    timestamp = str(timestamp or _utc_now())
    ticker = str(ticker or "UNKNOWN").strip() or "UNKNOWN"
    family = str(family or "UNKNOWN").strip().upper() or "UNKNOWN"
    facts = _compact_facts(facts)
    unique = sanitize_filename(f"{timestamp}-{ticker}-{result or status}", max_length=150)
    relative_path = Path("Trades") / f"{unique}.md"
    related = [
        _wikilink(item)
        for item in _canonical_related_nodes(
            [family, "Execution Intelligence", "Exit Logic"],
            facts=facts,
            family=family,
            include=["Liquidity", "Spread Dynamics"],
        )
    ]
    related = [item for item in related if item]
    content = (
        _frontmatter(
            {
                "type": "trade_outcome",
                "ticker": ticker,
                "family": family,
                "timestamp": timestamp,
                "status": status or "closed",
            }
        )
        + f"# Trade: {ticker}\n\n"
        + "## Entry\n"
        + f"- Action: {action or side or facts.get('action') or facts.get('side') or ''}\n"
        + f"- Price: {entry_price if entry_price is not None else price if price is not None else facts.get('entry_price', '')}\n"
        + f"- Confidence: {confidence if confidence is not None else facts.get('confidence', '')}\n"
        + f"- EV: {ev if ev is not None else facts.get('ev_score', facts.get('edge_after_cost', ''))}\n"
        + f"- Probability: {probability if probability is not None else facts.get('probability', facts.get('estimated_win_probability', ''))}\n"
        + f"- Spread: {spread if spread is not None else facts.get('spread', '')}\n"
        + f"- Liquidity: {liquidity if liquidity is not None else facts.get('liquidity', facts.get('liquidity_dollars', ''))}\n"
        + f"- Top book: {json.dumps(top_book, sort_keys=True, default=str) if top_book else facts.get('top_book', '')}\n\n"
        + "## Exit\n"
        + f"- Exit reason: {exit_reason or facts.get('exit_reason', '')}\n"
        + f"- Exit price: {exit_price if exit_price is not None else facts.get('exit_price', '')}\n"
        + f"- PnL: {pnl if pnl is not None else facts.get('pnl_dollars', facts.get('realized_pnl', ''))}\n"
        + f"- Result: {result or facts.get('result', '')}\n"
        + f"- Hold time: {hold_time if hold_time is not None else facts.get('hold_seconds', '')}\n\n"
        + "## Facts\n"
        + f"```json\n{json.dumps(facts, sort_keys=True, indent=2, default=str)}\n```\n\n"
        + "## Related Nodes\n"
        + "".join(f"- {link}\n" for link in related)
    )
    return safe_write_markdown(relative_path, content, overwrite=False, vault_path=vault_path)


def record_pattern_observation(pattern=None, facts=None, timestamp=None, family=None, ticker=None, related=None, vault_path=None):
    timestamp = str(timestamp or _utc_now())
    pattern = str(pattern or "Observed Pattern").strip() or "Observed Pattern"
    family = str(family or "").strip().upper()
    facts = _compact_facts(facts)
    relative_path = Path("Patterns") / f"{sanitize_filename(pattern)}.md"
    related_nodes = _canonical_related_nodes(
        list(related or []) + ([family] if family else []),
        facts=facts,
        family=family,
        include=["Telemetry", "Liquidity", "Spread Dynamics"],
    )
    links = [_wikilink(item) for item in dict.fromkeys(related_nodes)]
    links = [item for item in links if item]
    initial = (
        _frontmatter(
            {
                "type": "pattern_observation",
                "pattern": pattern,
                "created": timestamp,
                "status": "passive_observation",
            }
        )
        + f"# {pattern}\n\n"
        + "## Observations\n"
    )
    line = f"- {timestamp} - ticker={ticker or facts.get('ticker', '')}; family={family or facts.get('family', '')}; {_facts_line(facts)}"
    if links:
        line += f" | related: {', '.join(links)}"
    line += "\n"
    return _ensure_note_then_append(relative_path, initial, line, vault_path=vault_path)


def record_hypothesis(hypothesis=None, facts=None, timestamp=None, related=None, vault_path=None):
    timestamp = str(timestamp or _utc_now())
    hypothesis = str(hypothesis or "").strip()
    if not hypothesis:
        _log("[OBSIDIAN-EXPORT-SKIP]", action="record_hypothesis", reason="missing_hypothesis")
        return _status(False, "missing_hypothesis")
    facts = _compact_facts(facts)
    relative_path = Path("Hypotheses") / f"{sanitize_filename(hypothesis, max_length=100)}.md"
    links = [_wikilink(item) for item in _canonical_related_nodes(related or ["Telemetry"], facts=facts)]
    links = [item for item in links if item]
    initial = (
        _frontmatter(
            {
                "type": "hypothesis",
                "created": timestamp,
                "status": "passive_observation",
            }
        )
        + f"# {hypothesis}\n\n"
        + "## Evidence\n"
        + "\n"
        + "## Related Nodes\n"
        + "".join(f"- {link}\n" for link in links)
    )
    append = f"\n## Evidence\n- {timestamp} - {_facts_line(facts)}\n"
    return _ensure_note_then_append(relative_path, initial, append, vault_path=vault_path)
