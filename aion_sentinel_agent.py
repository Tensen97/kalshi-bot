#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aion_patch_queue as patch_queue
from aion_memory_store import find_match as memory_find_match, latest_memory_summary, record_issue as memory_record_issue
from aion_telemetry import emit_compact_event, emit_event, emit_error_event, emit_once_per_key, emit_rate_limited, emit_state_transition, emit_summary_counter, flush_cycle_summary, redact_sensitive_fields, telemetry_print as print
from runtime.paths import ensure_runtime_layout, runtime_state_file, safe_atomic_json_write


REPO_ROOT = Path("/opt/kalshi-bot").resolve()
RUNTIME_SENTINEL_ROOT = REPO_ROOT / "runtime" / "sentinel"
INCIDENTS_ROOT = RUNTIME_SENTINEL_ROOT / "incidents"
BACKUPS_ROOT = RUNTIME_SENTINEL_ROOT / "backups"
LOGS_ROOT = RUNTIME_SENTINEL_ROOT / "logs"
RECIPES_ROOT = RUNTIME_SENTINEL_ROOT / "recipes"
SIMULATION_ROOT = REPO_ROOT / "runtime" / "simulation"
SIMULATION_ISSUES_ROOT = SIMULATION_ROOT / "issues"
STATE_FILE = RUNTIME_SENTINEL_ROOT / "sentinel_state.json"
RECIPES_STATE_FILE = RECIPES_ROOT / "learned_recipes.json"

KALSHI_SERVICE = "kalshi-bot.service"
KALSHI_UNIT = "kalshi-bot.service"
GUARDIAN_CMD = [sys.executable, str(REPO_ROOT / "aion_guardian_report.py"), "--daily-integrity-audit"]
PY_COMPILE_CMD = [sys.executable, "-m", "py_compile", str(REPO_ROOT / "kalshi_web.py"), str(REPO_ROOT / "aion_guardian_report.py")]
SIMULATION_CMD = [sys.executable, str(REPO_ROOT / "aion_execution_simulator.py"), "--full-execution"]

AI_AUTO_APPLY_DENYLIST = (
    "delete critical files",
    "remove risk guards",
    "disable telemetry",
    "disable phase gates",
    "disable duplicate protections",
    "live order execution changes",
    "automatic service restarts",
    "change credentials",
    "change secrets",
)

NAME_ERROR_RE = re.compile(
    r"(?:NameError|UnboundLocalError|local variable '([^']+)' referenced before assignment|name '([^']+)' is not defined|referenced before assignment)",
    re.IGNORECASE,
)
TRACEBACK_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in ([^\s]+)\s*$', re.MULTILINE)
TELEGRAM_ERROR_RE = re.compile(r"(telegram_send_failed|Telegram|TELEGRAM|🚨\s*ERROR)", re.IGNORECASE)
PHASE55_ACTIVE_RE = re.compile(
    r"(phase55_ready|phase55_status|phase55_live_execution_gate_by_ticker|placeholder_phase55_record|raw_phase55_status|entry_timing_pass)",
    re.IGNORECASE,
)
STALE_BLOCKER_RE = re.compile(r"(unsafe_spread|unsafe_price|quote_missing|orderbook_not_usable|stale_blocker|phase55|spread_wait|direct_fields|unknown)", re.IGNORECASE)
OBSOLETE_AUTHORITY_RE = re.compile(
    r"(phase55_reachability_bridge|placeholder_phase55|phase55_active|direct_fields|spread_wait|unknown blocker|legacy bridge|compatibility alias)",
    re.IGNORECASE,
)

DEFAULT_RECIPE_FILES = {
    "scope_bug_recipes.json": {
        "version": 1,
        "recipes": [
            {
                "name": "missing_boolean_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(.+_pass)' is not defined",
                    r"name '(.+_ready)' is not defined",
                    r"local variable '(.+_pass)' referenced before assignment",
                    r"local variable '(.+_ready)' referenced before assignment",
                ],
                "variable_pattern": r"(.+_pass|.+_ready)",
                "initializer_strategy": "false",
                "default_value": False,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe false initializer for boolean readiness flags; never defaults to True.",
            },
            {
                "name": "missing_relaxed_aggressive_boolean_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(.+_aggressive)' is not defined",
                    r"local variable '(.+_aggressive)' referenced before assignment",
                    r"name '(relaxed_.+)' is not defined",
                    r"local variable '(relaxed_.+)' referenced before assignment",
                ],
                "variable_pattern": r"(relaxed_.+|.+_aggressive)",
                "initializer_strategy": "false",
                "default_value": False,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe false initializer for relaxed/aggressive boolean gates; only promote from canonical evidence.",
            },
            {
                "name": "missing_status_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(.+_status)' is not defined",
                    r"local variable '(.+_status)' referenced before assignment",
                ],
                "variable_pattern": r"(.+_status)",
                "initializer_strategy": "string_unknown",
                "default_value": "unknown",
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe unknown status placeholder.",
            },
            {
                "name": "missing_reason_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(.+_reason)' is not defined",
                    r"local variable '(.+_reason)' referenced before assignment",
                ],
                "variable_pattern": r"(.+_reason)",
                "initializer_strategy": "string_unknown",
                "default_value": "unknown",
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe unknown reason placeholder.",
            },
            {
                "name": "missing_detail_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(.+_detail)' is not defined",
                    r"local variable '(.+_detail)' referenced before assignment",
                ],
                "variable_pattern": r"(.+_detail)",
                "initializer_strategy": "dict_empty",
                "default_value": {},
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe detail payload placeholder.",
            },
            {
                "name": "missing_score_or_edge_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(edge_.+|.+_score|.+_probability)' is not defined",
                    r"local variable '(edge_.+|.+_score|.+_probability)' referenced before assignment",
                ],
                "variable_pattern": r"(edge_.+|.+_score|.+_probability)",
                "initializer_strategy": "number_zero",
                "default_value": 0.0,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Safe numeric zero placeholder for edge/score/probability values.",
            },
            {
                "name": "missing_identity_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name '(ticker|profile|resolved_side|selected_side|side)' is not defined",
                    r"local variable '(ticker|profile|resolved_side|selected_side|side)' referenced before assignment",
                ],
                "variable_pattern": r"(ticker|profile|resolved_side|selected_side|side)",
                "initializer_strategy": "string_unknown",
                "default_value": "unknown",
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Identity-like values default to unknown; function-local canonical fallback may override.",
            },
            {
                "name": "missing_price_bounds_pass",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"name 'price_bounds_pass' is not defined",
                    r"local variable 'price_bounds_pass' referenced before assignment",
                ],
                "variable_pattern": r"price_bounds_pass",
                "initializer_strategy": "derive_selected_price",
                "default_value": False,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Boolean price bounds gate; may derive from selected_price when available, otherwise defaults False.",
            },
            {
                "name": "missing_canonical_allowed_initializer",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [
                    r"local variable '(.+)' referenced before assignment",
                    r"name '(.+)' is not defined",
                ],
                "variable_pattern": r"canonical_.+_allowed",
                "initializer_strategy": "false",
                "default_value": False,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Canonical allowed flags must initialize False first and only be promoted from canonical execution state fallbacks.",
            },
        ],
    },
    "blocker_recipes.json": {
        "version": 1,
        "recipes": [
            {
                "name": "canonical_blocker_override",
                "enabled": True,
                "issue_type": "stale_blocker_propagation",
                "matches": [r"unsafe_spread", r"unsafe_price", r"quote_missing", r"orderbook_not_usable"],
                "variable_pattern": r"(unsafe_spread|unsafe_price|quote_missing|orderbook_not_usable)",
                "initializer_strategy": "telemetry_only",
                "default_value": "unknown",
                "patch_location": "telemetry_only",
                "allowed": True,
                "requires_compile": False,
                "requires_restart": False,
                "rollback_on_failure": False,
                "notes": "Do not use stale blocker labels as active authority when canonical Realm V.V already approves the lane.",
            }
        ],
    },
    "telemetry_recipes.json": {
        "version": 1,
        "recipes": [
            {
                "name": "telemetry_scope_guard",
                "enabled": True,
                "issue_type": "telegram_runtime_alert",
                "matches": [r"telegram_send_failed", r"Telegram", r"TELEGRAM", r"🚨\s*ERROR"],
                "variable_pattern": r".+",
                "initializer_strategy": "telemetry_guard",
                "default_value": "unknown",
                "patch_location": "telemetry_only",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": "Telemetry-only alerts should never block live execution; they should be stabilized or downgraded.",
            }
        ],
    },
    "approved_patterns.json": {
        "version": 1,
        "patterns": [
            r".+_pass$",
            r".+_ready$",
            r".+_status$",
            r".+_reason$",
            r".+_detail$",
            r"edge_.+",
            r".+_score$",
            r".+_probability$",
            r"ticker",
            r"profile",
            r"resolved_side",
            r"selected_side",
            r"side",
        ],
    },
    "rejected_patterns.json": {
        "version": 1,
        "patterns": [
            r".*api_key.*",
            r".*private_key.*",
            r".*secret.*",
            r".*password.*",
            r".*token.*",
            r".*credential.*",
        ],
    },
    "learned_recipes.json": {
        "version": 1,
        "recipes": [],
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _set_runtime_sentinel_root(root: Path) -> None:
    global RUNTIME_SENTINEL_ROOT, INCIDENTS_ROOT, BACKUPS_ROOT, LOGS_ROOT, STATE_FILE
    RUNTIME_SENTINEL_ROOT = Path(root).expanduser().resolve()
    INCIDENTS_ROOT = RUNTIME_SENTINEL_ROOT / "incidents"
    BACKUPS_ROOT = RUNTIME_SENTINEL_ROOT / "backups"
    LOGS_ROOT = RUNTIME_SENTINEL_ROOT / "logs"
    STATE_FILE = RUNTIME_SENTINEL_ROOT / "sentinel_state.json"


def _ensure_dirs() -> None:
    def _can_write(directory: Path) -> bool:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / f".sentinel_write_test_{os.getpid()}_{int(time.time() * 1000)}"
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink()
            except Exception:
                pass
            return True
        except Exception:
            return False
    try:
        for path in (RUNTIME_SENTINEL_ROOT, INCIDENTS_ROOT, BACKUPS_ROOT, LOGS_ROOT):
            if not _can_write(path):
                raise PermissionError(f"unwritable:{path}")
        ensure_runtime_layout()
        return
    except Exception:
        fallback_root = Path(
            os.environ.get("AION_SENTINEL_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/sentinel")
        ).expanduser().resolve()
        if fallback_root == RUNTIME_SENTINEL_ROOT:
            raise
        _set_runtime_sentinel_root(fallback_root)
        for path in (RUNTIME_SENTINEL_ROOT, INCIDENTS_ROOT, BACKUPS_ROOT, LOGS_ROOT):
            if not _can_write(path):
                raise PermissionError(f"fallback_unwritable:{path}")
        ensure_runtime_layout()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_command(cmd: List[str], timeout: int = 120, allow_sudo_fallback: bool = False) -> Tuple[int, str, str]:
    attempts = [cmd]
    if allow_sudo_fallback and os.geteuid() != 0 and cmd and cmd[0] != "sudo":
        attempts.append(["sudo", *cmd])
    last_exc = None
    for attempt in attempts:
        try:
            proc = subprocess.run(attempt, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        except Exception as exc:
            last_exc = exc
    return 1, "", str(last_exc) if last_exc else "command_failed"


def _safe_json_load(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _load_state_files() -> Dict[str, Any]:
    candidates = [
        REPO_ROOT / "control_state.json",
        REPO_ROOT / "live_position_state.json",
        REPO_ROOT / "data" / "seraph_interface_state.json",
        Path(runtime_state_file("realm_xxix_live_state.json")),
        Path(runtime_state_file("aion_guardian_integrity_state.json")),
        Path(runtime_state_file("aion_guardian_report_state.json")),
        Path(runtime_state_file("aion_guardian_integrity_last.json")),
        Path(runtime_state_file("aion_guardian_report_last.json")),
    ]
    payload = {}
    for path in candidates:
        if path.exists():
            payload[str(path)] = _safe_json_load(_read_text(path))
    return payload


def _extract_variable(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"local variable '([^']+)' referenced before assignment", text)
    if m:
        return m.group(1)
    m = re.search(r"name '([^']+)' is not defined", text)
    if m:
        return m.group(1)
    m = re.search(r"UnboundLocalError.*?local variable '([^']+)'", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)
    return None


def _safe_unique(items: List[str]) -> List[str]:
    return list(dict.fromkeys([str(item) for item in items if str(item).strip()]))


@dataclass
class SentinelIssue:
    issue_type: str
    severity: str
    autopatch_allowed: bool
    variable: Optional[str] = None
    file_path: Optional[str] = None
    function_name: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    patch_recipe: Optional[str] = None
    remaining_blocker: Optional[str] = None
    traceback_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


class RuntimeScopeErrorHandler:
    def __init__(self, *, recipe_library: Optional[Dict[str, Any]] = None):
        self.recipe_library = recipe_library if isinstance(recipe_library, dict) else {}

    def analyze(self, text: str) -> Dict[str, Any]:
        text = text or ""
        frames = []
        for match in TRACEBACK_FRAME_RE.finditer(text):
            frames.append(
                {
                    "file_path": match.group(1),
                    "line": int(match.group(2)),
                    "function_name": match.group(3),
                }
            )
        variable = _extract_variable(text)
        lower = text.lower()
        exception_type = None
        if "unboundlocalerror" in lower:
            exception_type = "UnboundLocalError"
        elif "nameerror" in lower or "is not defined" in lower or "referenced before assignment" in lower:
            exception_type = "NameError"
        telemetry_branch = None
        for marker in ("final_submit", "final_gate", "readiness", "telemetry", "payload", "quote", "orderbook", "guardian", "telegram", "reconcile"):
            if marker in lower:
                telemetry_branch = marker
                break
        nearby_scope = None
        if frames:
            last = frames[-1]
            nearby_scope = {"file_path": last["file_path"], "line": last["line"], "function_name": last["function_name"]}
        return {
            "raw_text": text,
            "exception_type": exception_type,
            "variable": variable,
            "function_name": frames[-1]["function_name"] if frames else None,
            "file_path": frames[-1]["file_path"] if frames else None,
            "nearby_scope": nearby_scope,
            "telemetry_branch": telemetry_branch,
            "frames": frames[-8:],
            "ready_context": bool(re.search(r"\bready\b", lower)),
            "blocker_context": bool(re.search(r"\bblock", lower)),
            "final_gate_context": bool(re.search(r"final[-_ ]?gate|final[-_ ]?submit", lower)),
        }

    def _pattern_hits(self, recipe: Dict[str, Any], text: str, variable: Optional[str]) -> bool:
        if not recipe.get("enabled", True) or not recipe.get("allowed", True):
            return False
        matches = recipe.get("matches") or []
        if matches:
            if not any(re.search(pattern, text, re.IGNORECASE | re.MULTILINE) for pattern in matches):
                return False
        variable_pattern = str(recipe.get("variable_pattern") or "").strip()
        if variable_pattern and variable:
            if not re.search(variable_pattern, variable, re.IGNORECASE):
                return False
        elif variable_pattern and not variable and ".*" not in variable_pattern:
            return False
        return True

    def _pattern_list_matches(self, pattern_list: Any, value: Optional[str]) -> bool:
        if not value or not isinstance(pattern_list, list):
            return False
        return any(re.search(pattern, value, re.IGNORECASE) for pattern in pattern_list if isinstance(pattern, str))

    def _is_rejected_variable(self, variable: Optional[str]) -> bool:
        rejected = (self.recipe_library.get("rejected_patterns.json") or {}).get("patterns", [])
        return self._pattern_list_matches(rejected, variable)

    def _is_approved_variable(self, variable: Optional[str]) -> bool:
        approved = (self.recipe_library.get("approved_patterns.json") or {}).get("patterns", [])
        return self._pattern_list_matches(approved, variable)

    def _locate_variable_scopes(self, variable: Optional[str], file_path: Optional[str] = None) -> List[Dict[str, Any]]:
        variable = str(variable or "").strip()
        if not variable:
            return []
        target = Path(file_path) if file_path else REPO_ROOT / "kalshi_web.py"
        if not target.exists():
            return []
        try:
            text = target.read_text(encoding="utf-8")
        except Exception:
            return []
        scopes: List[Dict[str, Any]] = []
        pattern = re.compile(r"^([ \t]*)def ([A-Za-z_]\w*)\b", re.MULTILINE)
        for match in pattern.finditer(text):
            function_name = match.group(2)
            start = match.start()
            indent = match.group(1) or ""
            tail = text[match.end():]
            next_def = re.search(rf"^\n{re.escape(indent)}def \w+", tail, re.MULTILINE)
            end = match.end() + (next_def.start() if next_def else len(tail))
            snippet = text[start:end]
            if not re.search(rf"\b{re.escape(variable)}\b", snippet):
                continue
            hit_count = len(re.findall(rf"\b{re.escape(variable)}\b", snippet))
            first_hit = re.search(rf"\b{re.escape(variable)}\b", snippet)
            scopes.append(
                {
                    "file_path": str(target),
                    "function_name": function_name,
                    "start": start,
                    "end": end,
                    "references_found": hit_count,
                    "first_hit_offset": None if not first_hit else start + first_hit.start(),
                }
            )
        return scopes

    def _recipe_specificity(self, recipe: Dict[str, Any]) -> Tuple[int, int, int]:
        matches = recipe.get("matches") or []
        variable_pattern = str(recipe.get("variable_pattern") or "")
        issue_type = str(recipe.get("issue_type") or "")
        return (len(matches), len(variable_pattern), 1 if issue_type else 0)

    def match_recipe(self, *, issue_type: str, analysis: Dict[str, Any], extra_text: str = "") -> Optional[Dict[str, Any]]:
        text = "\n".join([analysis.get("raw_text") or "", extra_text or ""])
        variable = analysis.get("variable")
        if self._is_rejected_variable(variable):
            return None
        issue_type = str(issue_type or "").strip()
        candidates = []
        for file_name, payload in (self.recipe_library or {}).items():
            if not isinstance(payload, dict):
                continue
            for recipe in payload.get("recipes", []) or []:
                if not isinstance(recipe, dict):
                    continue
                if recipe.get("issue_type") and str(recipe.get("issue_type")) != issue_type:
                    continue
                if self._pattern_hits(recipe, text, variable):
                    recipe = dict(recipe)
                    recipe["recipe_file"] = file_name
                    candidates.append(recipe)
        if not candidates:
            for file_name, payload in (self.recipe_library or {}).items():
                if not isinstance(payload, dict):
                    continue
                for recipe in payload.get("recipes", []) or []:
                    if not isinstance(recipe, dict):
                        continue
                    if self._pattern_hits(recipe, text, variable):
                        recipe = dict(recipe)
                        recipe["recipe_file"] = file_name
                        candidates.append(recipe)
        if not candidates:
            return None
        candidates.sort(key=lambda recipe: (self._is_approved_variable(variable), *self._recipe_specificity(recipe)), reverse=True)
        return candidates[0]

    def suggest_recipe_template(self, *, issue_type: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
        variable = analysis.get("variable") or "unknown"
        function_name = analysis.get("function_name") or "unknown"
        telemetry_branch = analysis.get("telemetry_branch") or "unknown"
        return {
            "name": f"auto_suggested_{variable}",
            "enabled": False,
            "issue_type": issue_type,
            "matches": [analysis.get("raw_text", "")[-2000:]] if analysis.get("raw_text") else [],
            "variable_pattern": variable if variable != "unknown" else r".+",
            "initializer_strategy": "manual_review",
            "default_value": False,
            "patch_location": "function_top",
            "allowed": False,
            "requires_compile": True,
            "requires_restart": True,
            "rollback_on_failure": True,
            "notes": f"Suggested by Sentinel from telemetry_branch={telemetry_branch} function={function_name}.",
        }

    def derive_initializer(self, variable: str, recipe: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        variable = str(variable or "").strip()
        recipe = recipe if isinstance(recipe, dict) else {}
        context = context if isinstance(context, dict) else {}
        strategy = str(recipe.get("initializer_strategy") or "").strip().lower()
        default_value = recipe.get("default_value")
        if strategy == "false":
            return "False", "bool"
        if strategy in {"string_unknown", "unknown"}:
            return '"unknown"', "string"
        if strategy in {"dict_empty", "empty_dict"}:
            return "{}", "dict"
        if strategy in {"list_empty", "empty_list"}:
            return "[]", "list"
        if strategy in {"number_zero", "zero"}:
            return "0.0", "number"
        if strategy == "telemetry_guard":
            return '"unknown"', "string"
        if strategy == "derive_selected_price":
            return "False", "bool"
        if default_value is False:
            return "False", "bool"
        if default_value == 0.0:
            return "0.0", "number"
        if default_value == {}:
            return "{}", "dict"
        if default_value == []:
            return "[]", "list"
        if isinstance(default_value, str):
            if default_value.lower() == "unknown":
                return '"unknown"', "string"
            return repr(default_value), "string"
        return "False", "bool"


class SentinelAgent:
    def __init__(self, dry_run: bool = False, autopatch: bool = True):
        self.dry_run = dry_run
        self.autopatch = autopatch
        self.state = self._load_state()
        self.recipe_library = self._load_recipe_library()
        self.scope_handler = RuntimeScopeErrorHandler(recipe_library=self.recipe_library)

    def _load_state(self) -> Dict[str, Any]:
        if STATE_FILE.exists():
            payload = _safe_json_load(_read_text(STATE_FILE))
            if isinstance(payload, dict):
                return payload
        return {
            "patch_timestamps": [],
            "incidents": [],
            "learned_scope_initializers": {},
            "learned_scope_functions": {},
            "variable_failure_counts": {},
            "variable_failure_history": {},
        }

    def _ensure_recipe_files(self) -> None:
        RECIPES_ROOT.mkdir(parents=True, exist_ok=True)
        for file_name, payload in DEFAULT_RECIPE_FILES.items():
            path = RECIPES_ROOT / file_name
            if not path.exists():
                safe_atomic_json_write(path, payload, sort_keys=True, indent=2)
            try:
                os.chmod(path, 0o644)
            except Exception:
                pass
        if not RECIPES_STATE_FILE.exists():
            safe_atomic_json_write(RECIPES_STATE_FILE, DEFAULT_RECIPE_FILES["learned_recipes.json"], sort_keys=True, indent=2)
        try:
            os.chmod(RECIPES_STATE_FILE, 0o644)
        except Exception:
            pass

    def _validate_recipe(self, recipe: Dict[str, Any]) -> bool:
        if not isinstance(recipe, dict):
            return False
        required = ("name", "enabled", "issue_type", "matches", "variable_pattern", "initializer_strategy", "default_value", "patch_location", "allowed", "requires_compile", "requires_restart", "rollback_on_failure", "notes")
        if any(field not in recipe for field in required):
            return False
        if not isinstance(recipe.get("matches"), list):
            return False
        return True

    def _load_recipe_library(self) -> Dict[str, Any]:
        self._ensure_recipe_files()
        library: Dict[str, Any] = {}
        for path in sorted(RECIPES_ROOT.glob("*.json")):
            payload = _safe_json_load(_read_text(path))
            if not isinstance(payload, dict):
                continue
            if "recipes" in payload and isinstance(payload.get("recipes"), list):
                payload["recipes"] = [recipe for recipe in payload["recipes"] if self._validate_recipe(recipe)]
            elif "patterns" in payload and not isinstance(payload.get("patterns"), list):
                payload["patterns"] = []
            library[path.name] = payload
        return library

    def _save_state(self) -> None:
        safe_atomic_json_write(STATE_FILE, self.state, sort_keys=True, indent=2)

    def _trim_patch_history(self) -> List[float]:
        now = time.time()
        patch_times = [float(ts) for ts in self.state.get("patch_timestamps", []) if isinstance(ts, (int, float))]
        patch_times = [ts for ts in patch_times if now - ts <= 3600.0]
        self.state["patch_timestamps"] = patch_times
        return patch_times

    def _patch_budget_available(self, max_per_hour: int) -> bool:
        patch_times = self._trim_patch_history()
        return len(patch_times) < max(1, int(max_per_hour))

    def _record_patch(self) -> None:
        self.state.setdefault("patch_timestamps", []).append(time.time())
        self._save_state()

    def _increment_variable_failure_count(self, variable: Optional[str]) -> int:
        key = str(variable or "").strip()
        if not key:
            return 0
        counts = self.state.setdefault("variable_failure_counts", {})
        current = int(counts.get(key, 0) or 0) + 1
        counts[key] = current
        history = self.state.setdefault("variable_failure_history", {})
        bucket = history.setdefault(key, [])
        now = time.time()
        bucket.append(now)
        bucket[:] = [ts for ts in bucket if now - float(ts) <= 300.0]
        self._save_state()
        return current

    def _recent_variable_failure_count(self, variable: Optional[str], window_seconds: int = 300) -> int:
        key = str(variable or "").strip()
        if not key:
            return 0
        history = self.state.get("variable_failure_history", {})
        if not isinstance(history, dict):
            return 0
        bucket = history.get(key, [])
        if not isinstance(bucket, list):
            return 0
        now = time.time()
        return sum(1 for ts in bucket if now - float(ts) <= float(window_seconds))

    def _record_learning_pattern(self, *, variable: str, kind: str, initializer: str, function_name: Optional[str], file_path: Optional[str]) -> None:
        learned = self.state.setdefault("learned_scope_initializers", {})
        variable_key = str(variable or "").strip()
        if not variable_key:
            return
        learned[variable_key] = {
            "kind": kind,
            "initializer": initializer,
            "function_name": function_name,
            "file_path": file_path,
            "updated_at": _now_iso(),
        }
        self.state.setdefault("learned_scope_functions", {})[variable_key] = function_name
        if initializer == "False":
            learned_default_value: Any = False
        elif initializer == "0.0":
            learned_default_value = 0.0
        elif initializer == "{}":
            learned_default_value = {}
        elif initializer == "[]":
            learned_default_value = []
        elif initializer == '"unknown"':
            learned_default_value = "unknown"
        else:
            learned_default_value = initializer
        learned_recipes = self._load_json_list(RECIPES_STATE_FILE, "recipes")
        learned_recipes.append(
            {
                "name": f"learned_{variable_key}",
                "enabled": True,
                "issue_type": "scope_bug",
                "matches": [rf"name '{re.escape(variable_key)}' is not defined", rf"local variable '{re.escape(variable_key)}' referenced before assignment"],
                "variable_pattern": re.escape(variable_key),
                "initializer_strategy": kind,
                "default_value": learned_default_value,
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": True,
                "rollback_on_failure": True,
                "notes": f"Learned from {function_name or 'unknown'} in {file_path or 'unknown'}.",
            }
        )
        safe_atomic_json_write(RECIPES_STATE_FILE, {"version": 1, "recipes": learned_recipes}, sort_keys=True, indent=2)
        try:
            os.chmod(RECIPES_STATE_FILE, 0o644)
        except Exception:
            pass
        self._save_state()

    def _load_json_list(self, path: Path, key: str) -> List[Any]:
        payload = _safe_json_load(_read_text(path))
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return list(payload.get(key) or [])
        return []

    def _extract_traceback_context(self, text: str) -> Dict[str, Any]:
        frames = []
        if not text:
            return {"frames": frames}
        for match in TRACEBACK_FRAME_RE.finditer(text):
            frames.append(
                {
                    "file_path": match.group(1),
                    "line": int(match.group(2)),
                    "function_name": match.group(3),
                }
            )
        variable = _extract_variable(text)
        function_name = frames[-1]["function_name"] if frames else None
        file_path = frames[-1]["file_path"] if frames else None
        nearby_scope = None
        if function_name and file_path:
            nearby_scope = {"file_path": file_path, "function_name": function_name, "line": frames[-1]["line"]}
        telemetry_branch = None
        lower = text.lower()
        for marker in (
            "final_gate_preflight",
            "final_gate",
            "readiness",
            "telemetry",
            "payload",
            "quote",
            "orderbook",
            "guardian",
            "telegram",
            "reconcile",
        ):
            if marker in lower:
                telemetry_branch = marker
                break
        return {
            "variable": variable,
            "function_name": function_name,
            "file_path": file_path,
            "frames": frames[-8:],
            "nearby_scope": nearby_scope,
            "telemetry_branch": telemetry_branch,
            "raw_excerpt": text[-8000:],
        }

    def _classify_initializer(self, variable: str, context: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        name = str(variable or "").strip()
        lowered = name.lower()
        context = context if isinstance(context, dict) else {}
        explicit_kind = str(context.get("kind") or "").strip().lower() or None
        if explicit_kind:
            lowered = explicit_kind
        if lowered.startswith("canonical_") and lowered.endswith("_allowed"):
            return "False", "bool"
        bool_tokens = ("pass", "ready", "allowed", "enabled", "present", "valid", "executable", "confirmed", "applied", "found", "ok", "exists", "tradeable", "relaxed", "blocked", "aggressive")
        list_tokens = ("_list", "_fields", "_reasons", "_history", "_keys", "_messages", "_hits", "_records", "_frames")
        dict_tokens = ("_state", "_record", "_payload", "_context", "_detail", "_details", "_diagnostic", "_mapping", "_bucket", "_summary")
        numeric_tokens = ("edge", "score", "prob", "confidence", "threshold", "price", "cost", "size", "liquidity", "margin", "count", "age", "volume", "spread")
        string_tokens = ("status", "reason", "blocker", "source", "mode", "decision", "branch", "label", "state")
        if name in {"price_bounds_pass", "quote_ready", "orderbook_ready", "payload_ready", "auth_ready", "final_submit_allowed_by_realm_vv", "selected_side_has_price", "selected_side_has_liquidity", "depth_available", "beast_spread_override_applied", "tradeable", "relaxed_survivable_aggressive"} or any(token in lowered for token in bool_tokens) or lowered.startswith("relaxed_") or lowered.endswith("_aggressive"):
            return "False", "bool"
        if any(lowered.endswith(token) for token in list_tokens):
            return "[]", "list"
        if any(lowered.endswith(token) for token in dict_tokens):
            return "{}", "dict"
        if any(token in lowered for token in numeric_tokens):
            return "0.0", "number"
        if any(token in lowered for token in string_tokens):
            return '"unknown"', "string"
        return "False", "bool"

    def _find_function_block_for_variable(self, text: str, variable: str, function_name: Optional[str] = None) -> Optional[Tuple[str, Tuple[int, int]]]:
        if not text or not variable:
            return None
        if function_name:
            block = self._function_block(text, function_name)
            if block:
                return function_name, block
        pattern = re.compile(rf"^    def ([A-Za-z_]\w*)\b", re.MULTILINE)
        for match in pattern.finditer(text):
            candidate_name = match.group(1)
            block = self._function_block(text, candidate_name)
            if not block:
                continue
            snippet = text[block[0]:block[1]]
            if re.search(rf"\b{re.escape(variable)}\b", snippet):
                return candidate_name, block
        return None

    def _find_all_function_blocks_for_variable(self, text: str, variable: str, function_name: Optional[str] = None) -> List[Tuple[str, Tuple[int, int]]]:
        if not text or not variable:
            return []
        matches: List[Tuple[str, Tuple[int, int]]] = []
        seen = set()
        if function_name:
            block = self._function_block(text, function_name)
            if block:
                return [(function_name, block)]
        pattern = re.compile(r"^([ \t]*)def ([A-Za-z_]\w*)\b", re.MULTILINE)
        for match in pattern.finditer(text):
            candidate_name = match.group(2)
            block = self._function_block(text, candidate_name)
            if not block or candidate_name in seen:
                continue
            snippet = text[block[0]:block[1]]
            if re.search(rf"\b{re.escape(variable)}\b", snippet):
                seen.add(candidate_name)
                matches.append((candidate_name, block))
        return matches

    def _function_insertion_offset(self, function_text: str, function_indent: str = "") -> int:
        lines = function_text.splitlines(True)
        if len(lines) <= 1:
            return len(function_text)
        cursor = len(lines[0])
        idx = 1
        while idx < len(lines) and not lines[idx].strip():
            cursor += len(lines[idx])
            idx += 1
        if idx < len(lines):
            stripped = lines[idx].lstrip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = stripped[:3]
                cursor += len(lines[idx])
                idx += 1
                while idx < len(lines):
                    cursor += len(lines[idx])
                    if quote in lines[idx]:
                        return cursor
                    idx += 1
        return cursor

    def collect_evidence(self) -> Dict[str, Any]:
        journal_cmd = ["journalctl", "-u", KALSHI_UNIT, "--since", "10 minutes ago", "--no-pager", "-n", "1200"]
        rc, journal_out, journal_err = _run_command(journal_cmd, timeout=120, allow_sudo_fallback=True)
        guardian_rc, guardian_out, guardian_err = _run_command(GUARDIAN_CMD, timeout=240, allow_sudo_fallback=True)
        state_files = _load_state_files()
        paper30_summary = {}
        profit_engine_summary = {}
        family_heatmap_summary = {}
        try:
            paper30_candidates = [
                Path(REPO_ROOT / "runtime" / "simulation" / "paper30" / "latest_summary.json"),
                Path("/tmp") / "kalshi-bot" / "runtime" / "simulation" / "paper30" / "latest_summary.json",
            ]
            for paper30_path in paper30_candidates:
                if paper30_path.exists():
                    paper30_summary = json.loads(paper30_path.read_text(encoding="utf-8"))
                    break
        except Exception:
            paper30_summary = {}
        try:
            profit_candidates = [
                Path(REPO_ROOT / "runtime" / "profit_engine" / "latest_summary.json"),
                Path("/tmp") / "kalshi-bot" / "runtime" / "profit_engine" / "latest_summary.json",
            ]
            for profit_path in profit_candidates:
                if profit_path.exists():
                    profit_engine_summary = json.loads(profit_path.read_text(encoding="utf-8"))
                    break
        except Exception:
            profit_engine_summary = {}
        try:
            family_candidates = [
                Path(REPO_ROOT / "runtime" / "profit_engine" / "latest_family_heatmap.json"),
                Path("/tmp") / "kalshi-bot" / "runtime" / "profit_engine" / "latest_family_heatmap.json",
            ]
            for family_path in family_candidates:
                if family_path.exists():
                    family_heatmap_summary = json.loads(family_path.read_text(encoding="utf-8"))
                    break
        except Exception:
            family_heatmap_summary = {}
        try:
            memory_summary = latest_memory_summary()
        except Exception:
            memory_summary = {}
        try:
            patch_queue_summary = patch_queue.summarize_queue()
        except Exception:
            patch_queue_summary = {}
        telegram_hits = []
        for source_name, blob in (("journal", journal_out), ("guardian", guardian_out), ("guardian_err", guardian_err), ("journal_err", journal_err)):
            if TELEGRAM_ERROR_RE.search(blob or ""):
                telegram_hits.append(source_name)
        return {
            "timestamp": _now_iso(),
            "journal": {"returncode": rc, "stdout": journal_out, "stderr": journal_err},
            "guardian": {"returncode": guardian_rc, "stdout": guardian_out, "stderr": guardian_err},
            "states": state_files,
            "paper30": paper30_summary,
            "profit_engine": profit_engine_summary,
            "family_heatmap": family_heatmap_summary,
            "memory": memory_summary,
            "patch_queue": patch_queue_summary,
            "telegram_hits": telegram_hits,
        }

    def detect_issue(self, evidence: Dict[str, Any]) -> Optional[SentinelIssue]:
        journal_text = evidence.get("journal", {}).get("stdout", "") or ""
        guardian_text = evidence.get("guardian", {}).get("stdout", "") or ""
        simulation_text = evidence.get("simulation", {}).get("stdout", "") or ""
        simulation_err = evidence.get("simulation", {}).get("stderr", "") or ""
        combined = "\n".join([journal_text, guardian_text, simulation_text, evidence.get("journal", {}).get("stderr", "") or "", evidence.get("guardian", {}).get("stderr", "") or "", simulation_err])
        analysis = self.scope_handler.analyze(combined)
        variable = analysis.get("variable") or _extract_variable(combined)
        function_name = analysis.get("function_name")
        failure_count = self._increment_variable_failure_count(variable) if variable else 0
        recent_failure_count = self._recent_variable_failure_count(variable) if variable else 0
        candidate_scopes = self.scope_handler._locate_variable_scopes(variable, analysis.get("file_path")) if variable else []
        if candidate_scopes and not function_name:
            function_name = candidate_scopes[0].get("function_name")
        recipe_scope = self.scope_handler.match_recipe(issue_type="scope_bug", analysis=analysis, extra_text=combined)
        recipe_telemetry = self.scope_handler.match_recipe(issue_type="telegram_runtime_alert", analysis=analysis, extra_text=combined)
        traceback_context = {
            "frames": analysis.get("frames", []),
            "variable": variable,
            "function_name": function_name,
            "file_path": analysis.get("file_path"),
            "nearby_scope": analysis.get("nearby_scope"),
            "candidate_scopes": candidate_scopes,
            "telemetry_branch": analysis.get("telemetry_branch"),
            "ready_context": analysis.get("ready_context"),
            "blocker_context": analysis.get("blocker_context"),
            "final_gate_context": analysis.get("final_gate_context"),
            "raw_text": analysis.get("raw_text"),
        }
        if NAME_ERROR_RE.search(combined) or (variable and recipe_scope and evidence.get("telegram_hits")):
            return SentinelIssue(
                issue_type="scope_bug",
                severity="high",
                autopatch_allowed=True,
                variable=variable,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                function_name=function_name,
                evidence={"matches": ["NameError/UnboundLocalError"], "telegram_hits": evidence.get("telegram_hits", []), "journal_excerpt": journal_text[-5000:], "traceback_context": traceback_context, "matched_recipe": recipe_scope, "recipe_library": list(self.recipe_library.keys()), "recent_failure_count": recent_failure_count},
                patch_recipe=(recipe_scope or {}).get("name", "scope_initializer"),
                remaining_blocker="runtime_scope_crash",
                traceback_context=traceback_context,
            )
        if evidence.get("telegram_hits") and not variable and not re.search(r"(NameError|UnboundLocalError|referenced before assignment|is not defined)", combined, re.IGNORECASE):
            return None
        guardian_state = self._parse_guardian_audit(guardian_text)
        raw_blocker_samples = [
            line
            for line in "\n".join([journal_text, simulation_text]).splitlines()
            if any(
                token in line
                for token in (
                    "AION-RAW-BLOCKER-SAMPLE",
                    "AION-SELECTED-PRICE-REJECTED",
                    "AION-SELECTED-PRICE-RESOLVED",
                    "selected_price_above_0_99",
                    "payload_missing:selected_price_above_0_99",
                    "quote_not_usable",
                    "orderbook_not_usable",
                    "base_sports_threshold",
                    "spread_wait",
                    "direct_fields",
                    "unknown",
                    "all_guards_passed",
                    "entry_ready",
                    "tradeable",
                    "eligible",
                    "liquidity_soft_pass",
                    "targeted_any_size_present",
                    "priority_sports_family",
                    "controlled_sports_promotion",
                )
            )
        ]
        if evidence.get("telegram_hits"):
            return SentinelIssue(
                issue_type="telegram_runtime_alert",
                severity="high",
                autopatch_allowed=True,
                variable=variable,
                file_path=str(REPO_ROOT / "kalshi_web.py") if variable else None,
                function_name=function_name,
                evidence={
                    "telegram_hits": evidence.get("telegram_hits", []),
                    "journal_excerpt": journal_text[-5000:],
                    "guardian": guardian_state,
                    "traceback_context": traceback_context,
                    "matched_recipe": recipe_telemetry,
                    "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="telegram_runtime_alert", analysis=analysis),
                    "failure_count": failure_count,
                    "recent_failure_count": recent_failure_count,
                },
                patch_recipe=(recipe_telemetry or {}).get("name", "telemetry_scope_guard"),
                remaining_blocker="telegram_error_path",
                traceback_context=traceback_context,
            )
        stale_phase55 = int(guardian_state.get("stale_phase55_dependency_count", 0) or 0)
        compat_alias = int(guardian_state.get("phase55_compat_alias_count", 0) or 0)
        active_obsolete = int(guardian_state.get("active_obsolete_authority_count", 0) or 0)
        archive_bridge = int(guardian_state.get("phase55_reachability_bridge_count", 0) or 0)
        if active_obsolete > 0 or (archive_bridge > 0 and (stale_phase55 > 0 or compat_alias > 0)):
            return SentinelIssue(
                issue_type="obsolete_authority",
                severity="medium",
                autopatch_allowed=True,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                evidence={
                    "guardian": guardian_state,
                    "journal_excerpt": journal_text[-3000:],
                    "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="obsolete_authority", analysis=analysis),
                    "failure_count": failure_count,
                    "active_obsolete_authority_count": active_obsolete,
                    "archive_phase55_bridge_count": archive_bridge,
                },
                patch_recipe="realm_vv_authority_guard",
                remaining_blocker="obsolete_authority",
            )
        if OBSOLETE_AUTHORITY_RE.search(combined) and (active_obsolete > 0 or archive_bridge > 0):
            return SentinelIssue(
                issue_type="obsolete_authority",
                severity="medium",
                autopatch_allowed=True,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                evidence={
                    "guardian": guardian_state,
                    "journal_excerpt": journal_text[-3000:],
                    "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="obsolete_authority", analysis=analysis),
                    "failure_count": failure_count,
                    "active_obsolete_authority_count": active_obsolete,
                    "archive_phase55_bridge_count": archive_bridge,
                },
                patch_recipe="realm_vv_authority_guard",
                remaining_blocker="obsolete_authority",
            )
        if any(token in guardian_text for token in ("quote_not_usable", "orderbook_not_usable", "unsafe_spread", "spread_wait", "direct_fields", "unknown")):
            return SentinelIssue(
                issue_type="stale_blocker_propagation",
                severity="medium",
                autopatch_allowed=True,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                evidence={
                    "guardian_excerpt": guardian_text[-5000:],
                    "journal_excerpt": journal_text[-3000:],
                    "raw_blocker_samples": raw_blocker_samples[-25:],
                    "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="stale_blocker_propagation", analysis=analysis),
                    "failure_count": failure_count,
                },
                patch_recipe="canonical_blocker_override",
                remaining_blocker="stale_blocker_label",
            )
        return None

    def _parse_guardian_audit(self, text: str) -> Dict[str, Any]:
        if not text:
            return {}
        guardian_state = {}
        for line in text.splitlines():
            if line.startswith("[AION-DAILY-INTEGRITY-BUILD] "):
                blob = line.split("] ", 1)[1]
                data = _safe_json_load(blob)
                if isinstance(data, dict):
                    guardian_state.update(data)
        return guardian_state

    def _guardian_requires_simulation(self, guardian_state: Dict[str, Any], guardian_text: str) -> Tuple[bool, str]:
        guardian_state = guardian_state if isinstance(guardian_state, dict) else {}
        text = "\n".join([
            json.dumps(guardian_state, sort_keys=True, default=str),
            guardian_text or "",
        ])
        if any(bool(guardian_state.get(key)) for key in ("stale_blocker_labels", "dormant_pipeline")):
            return True, "guardian_state_trigger"
        for key in ("final_submit_preflight_count", "final_submit_call_count", "order_submitted_count"):
            try:
                if int(guardian_state.get(key, 0) or 0) == 0:
                    return True, f"{key}_zero"
            except Exception:
                continue
        if any(token in text.lower() for token in ("next_fix", "review raw blocker trace samples", "hidden blockers", "fake blockers", "payload rejection", "reachability collapse")):
            return True, "guardian_recommendation"
        return False, "none"

    def _ai_escalation_enabled(self) -> bool:
        if "AION_SENTINEL_AI_ENABLED" in os.environ:
            return _env_bool("AION_SENTINEL_AI_ENABLED", False)
        return False

    def _require_human_confirm_for_ai_patch(self) -> bool:
        if "AION_SENTINEL_REQUIRE_HUMAN_CONFIRM" in os.environ:
            return _env_bool("AION_SENTINEL_REQUIRE_HUMAN_CONFIRM", True)
        if "AION_SENTINEL_REQUIRE_HUMAN_CONFIRM_FOR_AI_PATCH" in os.environ:
            return _env_bool("AION_SENTINEL_REQUIRE_HUMAN_CONFIRM_FOR_AI_PATCH", True)
        return True

    def _ai_simulation_enabled(self) -> bool:
        return _env_bool("AION_SENTINEL_AI_SIMULATION", False)

    def _ai_model_name(self) -> str:
        return str(os.environ.get("AION_SENTINEL_AI_MODEL", "gpt-4.1") or "gpt-4.1").strip()

    def _ai_allowed_by_denylist(self, structured: Dict[str, Any]) -> Tuple[bool, List[str]]:
        text_blob = json.dumps(redact_sensitive_fields(structured or {}), sort_keys=True, default=str).lower()
        hits = [phrase for phrase in AI_AUTO_APPLY_DENYLIST if phrase in text_blob]
        return (not hits), hits

    def _score_ai_patch_proposal(self, *, issue: SentinelIssue, structured: Dict[str, Any], recipe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        structured = structured if isinstance(structured, dict) else {}
        recipe = recipe if isinstance(recipe, dict) else {}
        diagnosis = str(structured.get("diagnosis") or "").strip()
        root_cause = str(structured.get("root_cause") or "").strip()
        patch_plan = structured.get("patch_plan") if isinstance(structured.get("patch_plan"), list) else []
        verification_commands = structured.get("verification_commands") if isinstance(structured.get("verification_commands"), list) else []
        risk_level = str(structured.get("risk_level") or "unknown").strip().lower()
        allow_by_recipe = bool(recipe)
        allowed, deny_hits = self._ai_allowed_by_denylist(structured)
        confidence = 0.35
        if diagnosis and root_cause:
            confidence += 0.2
        if patch_plan:
            confidence += min(0.2, 0.05 * len(patch_plan))
        if verification_commands:
            confidence += min(0.15, 0.05 * len(verification_commands))
        if issue.variable:
            confidence += 0.1
        if issue.function_name:
            confidence += 0.05
        confidence = max(0.0, min(1.0, confidence))
        risk = 0.25
        if risk_level in {"high", "critical"}:
            risk += 0.45
        elif risk_level in {"medium"}:
            risk += 0.2
        if issue.issue_type in {"telegram_runtime_alert"}:
            risk += 0.1
        if not allow_by_recipe:
            risk += 0.15
        if deny_hits:
            risk += 0.5
        if not verification_commands:
            risk += 0.1
        safety = max(0.0, min(1.0, 1.0 - risk))
        approved = bool(allow_by_recipe and not deny_hits and confidence >= 0.5 and safety >= 0.5 and str(structured.get("action") or "").strip().lower() == "approve")
        return {
            "confidence_score": round(confidence, 4),
            "risk_score": round(risk, 4),
            "safety_score": round(safety, 4),
            "denylist_hits": deny_hits,
            "approved": approved,
            "auto_apply_allowed": False,
            "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
        }

    def _build_ai_escalation_package(self, *, issue: SentinelIssue, evidence: Dict[str, Any], incident_dir: Optional[Path], persist: bool = True) -> Dict[str, Any]:
        blocker_tags = _safe_unique(
            [str(item) for item in (evidence.get("telegram_hits") or []) if str(item).strip()]
            + [str(item) for item in (evidence.get("blocker_tags") or []) if str(item).strip()]
            + [str(item) for item in (evidence.get("raw_blockers") or []) if str(item).strip()]
        )
        telemetry_lines: List[str] = []
        for key in ("guardian", "journal", "simulation"):
            blob = evidence.get(key) if isinstance(evidence, dict) else None
            if isinstance(blob, dict):
                stdout = str(blob.get("stdout") or "")
                stderr = str(blob.get("stderr") or "")
                if stdout:
                    telemetry_lines.extend([line for line in stdout.splitlines() if line.strip()][-40:])
                if stderr:
                    telemetry_lines.extend([line for line in stderr.splitlines() if line.strip()][-20:])
        telemetry_lines = telemetry_lines[-80:]
        recent_patch_history = []
        for item in list((self.state or {}).get("recent_patches") or [])[-10:]:
            if isinstance(item, dict):
                recent_patch_history.append(redact_sensitive_fields(item))
        memory_summary = latest_memory_summary()
        memory_match = memory_find_match(issue.to_dict())
        patch_queue_summary = evidence.get("patch_queue") if isinstance(evidence, dict) else {}
        safety_state = {
            "dry_run": bool(self.dry_run),
            "autopatch_enabled": bool(self.autopatch),
            "ai_enabled": bool(self._ai_escalation_enabled()),
            "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
            "simulation": bool(self._ai_simulation_enabled()),
            "compile_required": _env_bool("AION_SENTINEL_REQUIRE_COMPILE", True),
            "restart_verify_required": _env_bool("AION_SENTINEL_REQUIRE_RESTART_VERIFY", True),
        }
        issue_package = {
            "timestamp": _now_iso(),
            "trace_id": incident_dir.name if incident_dir is not None else f"ai_{uuid.uuid4().hex[:12]}",
            "unresolved_issue_type": issue.issue_type,
            "issue": issue.to_dict(),
            "failing_blocker_tags": blocker_tags,
            "affected_files_functions": {
                "file_path": issue.file_path,
                "function_name": issue.function_name,
                "nearby_scope": issue.traceback_context or {},
            },
            "current_safety_state": safety_state,
            "recent_telemetry_lines": telemetry_lines,
            "patch_history": recent_patch_history,
            "memory_summary": memory_summary,
            "memory_match": memory_match,
            "patch_queue_summary": patch_queue_summary if isinstance(patch_queue_summary, dict) else {},
            "explicit_instruction": (
                "Produce diagnosis, root cause hypothesis, proposed patch plan, verification commands, and risk level. "
                "Return strict JSON only with no markdown."
            ),
        }
        prompt = (
            "You are AION Sentinel. Return STRICT JSON only.\n"
            "Goal: recommend a safe deterministic patch for a runtime issue.\n"
            "Constraints:\n"
            "- never relax hard safety\n"
            "- never change credentials or secrets\n"
            "- prefer safe initializers / scope reconciliation / telemetry-only fixes\n"
            "- if uncertain, set action to reject\n"
            "- auto_apply_allowed must always be false for now\n\n"
            f"issue_package: {json.dumps(redact_sensitive_fields(issue_package), sort_keys=True, default=str)[:18000]}\n\n"
            "Return JSON with keys:\n"
            "- diagnosis\n"
            "- root_cause\n"
            "- proposed_files\n"
            "- patch_plan\n"
            "- verification_commands\n"
            "- risk_level\n"
            "- action: approve|reject\n"
            "- patch_type: scope_initializer|telemetry|obsolete_authority|other\n"
            "- variable\n"
            "- function_name\n"
            "- file_path\n"
            "- initializer_strategy\n"
            "- default_value\n"
            "- notes\n"
            "- auto_apply_allowed\n"
            "- require_human_confirm\n"
        )
        package_path = None
        prompt_path = None
        if persist and incident_dir is not None:
            safe_atomic_json_write(
                incident_dir / "escalation_package.json",
                {
                    "timestamp": _now_iso(),
                    "issue": issue.to_dict(),
                    "issue_package": redact_sensitive_fields(issue_package),
                    "prompt_path": str(incident_dir / "codex_prompt.txt"),
                },
                sort_keys=True,
                indent=2,
            )
            _write_text(incident_dir / "codex_prompt.txt", prompt)
            prompt_path = str(incident_dir / "codex_prompt.txt")
            package_path = str(incident_dir / "escalation_package.json")
        return {"prompt": prompt, "prompt_path": prompt_path, "package_path": package_path, "issue_package": issue_package}

    def _extract_ai_response_text(self, response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            for key in ("output_text", "text", "content"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            try:
                for output_item in response.get("output", []) or []:
                    if not isinstance(output_item, dict):
                        continue
                    for content_item in output_item.get("content", []) or []:
                        if not isinstance(content_item, dict):
                            continue
                        text = content_item.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
            except Exception:
                pass
        for attr in ("output_text", "text", "content"):
            try:
                value = getattr(response, attr, None)
                if isinstance(value, str) and value.strip():
                    return value
            except Exception:
                continue
        try:
            if hasattr(response, "model_dump"):
                payload = response.model_dump()
            elif hasattr(response, "dict"):
                payload = response.dict()
            else:
                payload = None
            if isinstance(payload, dict):
                return self._extract_ai_response_text(payload)
        except Exception:
            pass
        return ""

    def _parse_ai_response_bundle(self, raw: str, *, issue: SentinelIssue, simulated: bool = False) -> Dict[str, Any]:
        parsed = _safe_json_load(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        structured = {
            "diagnosis": str(parsed.get("diagnosis") or parsed.get("reason") or parsed.get("notes") or "unknown"),
            "root_cause": str(parsed.get("root_cause") or parsed.get("cause") or parsed.get("diagnosis") or "unknown"),
            "proposed_files": parsed.get("proposed_files") if isinstance(parsed.get("proposed_files"), list) else ([parsed.get("file_path")] if parsed.get("file_path") else []),
            "patch_plan": parsed.get("patch_plan") if isinstance(parsed.get("patch_plan"), list) else ([parsed.get("initializer_strategy")] if parsed.get("initializer_strategy") else []),
            "verification_commands": parsed.get("verification_commands") if isinstance(parsed.get("verification_commands"), list) else [],
            "risk_level": str(parsed.get("risk_level") or "unknown"),
            "auto_apply_allowed": False,
            "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
            "action": str(parsed.get("action") or "reject"),
            "patch_type": str(parsed.get("patch_type") or "other"),
            "variable": str(parsed.get("variable") or issue.variable or ""),
            "function_name": str(parsed.get("function_name") or issue.function_name or ""),
            "file_path": str(parsed.get("file_path") or issue.file_path or ""),
            "initializer_strategy": str(parsed.get("initializer_strategy") or "unknown"),
            "default_value": parsed.get("default_value"),
            "notes": str(parsed.get("notes") or parsed.get("reason") or ""),
            "raw": raw,
            "simulated": bool(simulated),
        }
        if structured["default_value"] is None:
            strategy = structured["initializer_strategy"]
            if strategy in {"false"}:
                structured["default_value"] = False
            elif strategy in {"string_unknown", "unknown"}:
                structured["default_value"] = "unknown"
            elif strategy in {"dict_empty", "empty_dict"}:
                structured["default_value"] = {}
            elif strategy in {"list_empty", "empty_list"}:
                structured["default_value"] = []
            elif strategy in {"number_zero", "zero"}:
                structured["default_value"] = 0.0
        return structured

    def _openai_responses_create(self, *, prompt: str, issue: SentinelIssue, incident_dir: Optional[Path] = None, simulate: bool = False) -> Dict[str, Any]:
        event_data = {
            "issue_type": issue.issue_type,
            "variable": issue.variable,
            "function_name": issue.function_name,
            "model": self._ai_model_name(),
            "simulation": bool(simulate),
            "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
        }
        if simulate:
            emit_compact_event("AION_SENTINEL_AI_ESCALATION", "AI escalation simulation started", event_data, component="sentinel", actionable=True)
            emit_compact_event("AION_SENTINEL_AI_REQUEST", "AI request prepared for simulation", event_data | {"prompt_excerpt": prompt[:1200]}, component="sentinel", actionable=True)
            mock_payload = {
                "diagnosis": "scope_initializer_missing",
                "root_cause": f"{issue.variable or 'unknown'} referenced before assignment in {issue.function_name or 'unknown'}",
                "proposed_files": [issue.file_path or str(REPO_ROOT / "kalshi_web.py")],
                "patch_plan": [
                    "initialize the variable at function entry",
                    "keep canonical fallback order",
                    "preserve hard safety and telemetry",
                ],
                "verification_commands": [
                    "python3 -m py_compile /opt/kalshi-bot/aion_sentinel_agent.py",
                    "python3 /opt/kalshi-bot/aion_sentinel_agent.py --simulate-ai-escalation",
                ],
                "risk_level": "low",
                "action": "reject" if self._require_human_confirm_for_ai_patch() else "approve",
                "patch_type": "scope_initializer",
                "variable": issue.variable or "unknown",
                "function_name": issue.function_name or "unknown",
                "file_path": issue.file_path or str(REPO_ROOT / "kalshi_web.py"),
                "initializer_strategy": "false",
                "default_value": False,
                "notes": "simulation_only",
                "auto_apply_allowed": False,
                "require_human_confirm": True,
            }
            raw = json.dumps(mock_payload, sort_keys=True, default=str)
            if incident_dir is not None:
                _write_text(incident_dir / "ai_response.txt", raw)
            emit_compact_event("AION_SENTINEL_AI_RESPONSE", "AI simulation response generated", {"simulation": True, "response_keys": sorted(mock_payload.keys())}, component="sentinel", actionable=True)
            emit_compact_event("AION_SENTINEL_AI_SIMULATION", "AI simulation path exercised", {"simulation": True, "mode": "mock_response"}, component="sentinel", actionable=True)
            if self._require_human_confirm_for_ai_patch():
                emit_compact_event("AION_SENTINEL_AI_REQUIRE_HUMAN_CONFIRM", "AI result requires human confirmation", {"simulation": True, "reason": "env_true"}, component="sentinel", level="WARN")
            return {"enabled": True, "ok": True, "simulation": True, "raw": raw, "parsed": mock_payload, "structured": self._parse_ai_response_bundle(raw, issue=issue, simulated=True)}

        if not self._ai_escalation_enabled():
            emit_compact_event("AION_SENTINEL_AI_DISABLED", "AI bridge disabled by environment", event_data | {"reason": "disabled_by_env"}, component="sentinel", level="WARN")
            return {"enabled": False, "reason": "disabled", "structured": {"auto_apply_allowed": False}}
        api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            emit_compact_event("AION_SENTINEL_AI_KEY_MISSING", "AI bridge missing OPENAI_API_KEY", event_data | {"reason": "missing_api_key"}, component="sentinel", level="WARN")
            emit_compact_event("AION_SENTINEL_AI_ERROR", "AI bridge cannot start without OPENAI_API_KEY", event_data | {"reason": "missing_api_key"}, component="sentinel", level="ERROR")
            emit_compact_event("AION_SENTINEL_AI_DISABLED", "AI bridge disabled because API key is missing", event_data | {"reason": "missing_api_key"}, component="sentinel", level="WARN")
            return {"enabled": True, "reason": "missing_api_key", "structured": {"auto_apply_allowed": False}}
        emit_compact_event("AION_SENTINEL_AI_ESCALATION", "AI escalation bridge called", event_data, component="sentinel", actionable=True)
        emit_compact_event("AION_SENTINEL_AI_REQUEST", "AI request prepared", event_data | {"prompt_excerpt": prompt[:1200]}, component="sentinel", actionable=True)
        payload = {"model": self._ai_model_name(), "input": prompt}
        try:
            response_obj = None
            raw = ""
            try:
                from openai import OpenAI  # type: ignore
            except Exception:
                OpenAI = None  # type: ignore
            if OpenAI is not None:
                client = OpenAI(api_key=api_key)
                response_obj = client.responses.create(model=self._ai_model_name(), input=prompt)
                if hasattr(response_obj, "model_dump_json"):
                    raw = response_obj.model_dump_json()
                elif hasattr(response_obj, "model_dump"):
                    raw = json.dumps(response_obj.model_dump(), sort_keys=True, default=str)
                else:
                    raw = json.dumps({"output_text": self._extract_ai_response_text(response_obj)}, sort_keys=True, default=str)
            else:
                request = urllib.request.Request(
                    "https://api.openai.com/v1/responses",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=60) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            if incident_dir is not None:
                _write_text(incident_dir / "ai_response.txt", raw)
            response_text = self._extract_ai_response_text(response_obj) if response_obj is not None else ""
            response_payload = _safe_json_load(raw) or {}
            if not isinstance(response_payload, dict):
                response_payload = {}
            if not response_text:
                response_text = self._extract_ai_response_text(response_payload)
            emit_compact_event("AION_SENTINEL_AI_RESPONSE", "AI response received", {"simulation": False, "response_keys": sorted(response_payload.keys()) if isinstance(response_payload, dict) else [], "response_text_found": bool(response_text)}, component="sentinel", actionable=True)
            return {"enabled": True, "ok": True, "raw": raw, "parsed": response_payload, "structured": self._parse_ai_response_bundle(response_text or raw, issue=issue, simulated=False)}
        except Exception as exc:
            raw = json.dumps({"error": exc.__class__.__name__, "reason": str(exc)}, sort_keys=True, default=str)
            if incident_dir is not None:
                _write_text(incident_dir / "ai_response.txt", raw)
            emit_compact_event("AION_SENTINEL_AI_ERROR", "AI bridge request failed", event_data | {"reason": exc.__class__.__name__}, component="sentinel", level="ERROR")
            return {"enabled": True, "ok": False, "reason": exc.__class__.__name__, "error": str(exc), "raw": raw, "structured": {"auto_apply_allowed": False}}

    def _ai_patch_to_recipe(self, response: Dict[str, Any], issue: SentinelIssue) -> Optional[Dict[str, Any]]:
        parsed = response.get("parsed") if isinstance(response, dict) else {}
        if not isinstance(parsed, dict):
            return None
        if str(parsed.get("action") or "").strip().lower() != "approve":
            return None
        variable = str(parsed.get("variable") or issue.variable or "").strip()
        if not variable:
            return None
        strategy = str(parsed.get("initializer_strategy") or "").strip().lower()
        default_value = parsed.get("default_value")
        if strategy not in {"false", "string_unknown", "unknown", "dict_empty", "empty_dict", "list_empty", "empty_list", "number_zero", "zero", "derive_selected_price", "telemetry_guard"}:
            return None
        return {
            "name": f"ai_bridge_{re.sub(r'[^A-Za-z0-9_.-]+', '_', variable) or 'unknown'}",
            "enabled": True,
            "issue_type": issue.issue_type,
            "matches": [rf"name '{re.escape(variable)}' is not defined", rf"local variable '{re.escape(variable)}' referenced before assignment"],
            "variable_pattern": re.escape(variable),
            "initializer_strategy": strategy,
            "default_value": default_value if default_value is not None else False,
            "patch_location": "function_top",
            "allowed": True,
            "requires_compile": True,
            "requires_restart": True,
            "rollback_on_failure": True,
            "notes": str(parsed.get("notes") or parsed.get("reason") or "AI bridge suggestion"),
        }

    def _structured_ai_proposal_seed(self, issue: SentinelIssue, structured: Dict[str, Any]) -> Dict[str, Any]:
        structured = structured if isinstance(structured, dict) else {}
        variable = str(structured.get("variable") or issue.variable or "").strip()
        return {
            "name": structured.get("name") or f"ai_bridge_{re.sub(r'[^A-Za-z0-9_.-]+', '_', variable or 'unknown')}",
            "patch_type": str(structured.get("patch_type") or "scope_initializer").strip().lower(),
            "variable": variable,
            "function_name": str(structured.get("function_name") or issue.function_name or "").strip(),
            "file_path": str(structured.get("file_path") or issue.file_path or "").strip(),
            "initializer_strategy": str(structured.get("initializer_strategy") or "false").strip().lower(),
            "default_value": structured.get("default_value", False),
            "notes": str(structured.get("notes") or structured.get("reason") or "AI bridge suggestion"),
            "proposed_files": structured.get("proposed_files") if isinstance(structured.get("proposed_files"), list) else ([issue.file_path] if issue.file_path else []),
            "verification_commands": structured.get("verification_commands") if isinstance(structured.get("verification_commands"), list) else [],
            "rollback_plan": structured.get("rollback_plan") or "restore backup and re-verify",
            "risk_level": str(structured.get("risk_level") or "unknown"),
        }

    def _maybe_run_ai_bridge(self, issue: SentinelIssue, evidence: Dict[str, Any], incident_dir: Path) -> Dict[str, Any]:
        package = self._build_ai_escalation_package(issue=issue, evidence=evidence, incident_dir=incident_dir)
        if self._ai_simulation_enabled():
            response = self._openai_responses_create(prompt=package["prompt"], issue=issue, incident_dir=None, simulate=True)
            structured = response.get("structured") if isinstance(response, dict) else {}
            proposal = self._ai_patch_to_recipe(response, issue)
            verdict = self._score_ai_patch_proposal(issue=issue, structured=structured, recipe=proposal)
            proposal_seed = proposal or self._structured_ai_proposal_seed(issue, structured)
            patch_preview, preview_kind = self._build_scope_initializer_preview(
                Path(issue.file_path) if issue.file_path else Path(REPO_ROOT / "kalshi_web.py"),
                issue.variable,
                function_name=issue.function_name,
                context=issue.traceback_context,
                recipe=proposal_seed,
            )
            queue_record = self._create_patch_queue_record(
                issue=issue,
                ai_bridge_result={
                    **response,
                    "package": package,
                    "proposal": proposal_seed,
                },
                verification=verdict,
                incident_dir=incident_dir,
                patch_preview=patch_preview or proposal_seed.get("notes", ""),
                unified_diff_patch=patch_preview,
                source="ai_bridge_simulation",
            )
            emit_compact_event(
                "AION_SENTINEL_AI_PATCH_PROPOSAL",
                "AI patch proposal prepared",
                {
                    "issue_type": issue.issue_type,
                    "variable": issue.variable or "unknown",
                    "function_name": issue.function_name or "unknown",
                    "proposal": proposal_seed,
                    "risk_score": verdict["risk_score"],
                    "confidence_score": verdict["confidence_score"],
                    "safety_score": verdict["safety_score"],
                    "queue_status": queue_record.get("status"),
                },
                component="sentinel",
                actionable=True,
            )
            emit_compact_event(
                "AION_SENTINEL_AI_VERIFICATION",
                "AI patch verification completed",
                {
                    "verification_commands": structured.get("verification_commands") if isinstance(structured, dict) else [],
                    "risk_score": verdict["risk_score"],
                    "confidence_score": verdict["confidence_score"],
                    "safety_score": verdict["safety_score"],
                    "denylist_hits": verdict.get("denylist_hits", []),
                    "auto_apply_allowed": False,
                    "require_human_confirm": True,
                },
                component="sentinel",
                actionable=True,
            )
            approved = bool(verdict.get("approved"))
            emit_compact_event(
                "AION_SENTINEL_AI_APPROVED" if approved else "AION_SENTINEL_AI_REJECTED",
                "AI patch proposal approved" if approved else "AI patch proposal rejected",
                {
                    "issue_type": issue.issue_type,
                    "variable": issue.variable or "unknown",
                    "function_name": issue.function_name or "unknown",
                    "risk_score": verdict["risk_score"],
                    "confidence_score": verdict["confidence_score"],
                    "safety_score": verdict["safety_score"],
                    "auto_apply_allowed": False,
                    "require_human_confirm": True,
                    "denylist_hits": verdict.get("denylist_hits", []),
                },
                component="sentinel",
                level="WARN" if not approved else "INFO",
                actionable=True,
            )
            if self._require_human_confirm_for_ai_patch():
                emit_compact_event(
                    "AION_SENTINEL_AI_REQUIRE_HUMAN_CONFIRM",
                    "AI patch requires human confirmation",
                    {"issue_type": issue.issue_type, "variable": issue.variable, "function_name": issue.function_name},
                    component="sentinel",
                    level="WARN",
                )
            return {
                "enabled": True,
                "simulation": True,
                "reason": "simulation",
                "package": package,
                "response": response,
                "structured": structured,
                "proposal": proposal_seed,
                "verification": verdict,
                "patch_preview": patch_preview,
                "queue_record": queue_record,
                "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
                "auto_apply_allowed": False,
                "applied": False,
            }
        response = self._openai_responses_create(prompt=package["prompt"], issue=issue, incident_dir=incident_dir, simulate=False)
        if not response.get("ok"):
            return {
                "enabled": bool(self._ai_escalation_enabled()),
                "reason": response.get("reason") or "ai_request_failed",
                "package": package,
                "response": response,
                "auto_apply_allowed": False,
                "applied": False,
            }
        structured = response.get("structured") if isinstance(response, dict) else {}
        proposal = self._ai_patch_to_recipe(response, issue)
        verdict = self._score_ai_patch_proposal(issue=issue, structured=structured, recipe=proposal)
        proposal_seed = proposal or self._structured_ai_proposal_seed(issue, structured)
        patch_preview, preview_kind = self._build_scope_initializer_preview(
            Path(issue.file_path) if issue.file_path else Path(REPO_ROOT / "kalshi_web.py"),
            issue.variable,
            function_name=issue.function_name,
            context=issue.traceback_context,
            recipe=proposal_seed,
        )
        queue_record = self._create_patch_queue_record(
            issue=issue,
            ai_bridge_result={
                **response,
                "package": package,
                "proposal": proposal_seed,
            },
            verification=verdict,
            incident_dir=incident_dir,
            patch_preview=patch_preview or proposal_seed.get("notes", ""),
            unified_diff_patch=patch_preview,
            source="ai_bridge",
        )
        emit_compact_event(
            "AION_SENTINEL_AI_PATCH_PROPOSAL",
            "AI patch proposal prepared",
            {
                "issue_type": issue.issue_type,
                "variable": issue.variable or "unknown",
                "function_name": issue.function_name or "unknown",
                "proposal": proposal_seed,
                "risk_score": verdict["risk_score"],
                "confidence_score": verdict["confidence_score"],
                "safety_score": verdict["safety_score"],
                "queue_status": queue_record.get("status"),
            },
            component="sentinel",
            actionable=True,
        )
        emit_compact_event(
            "AION_SENTINEL_AI_VERIFICATION",
            "AI patch verification completed",
            {
                "verification_commands": structured.get("verification_commands") if isinstance(structured, dict) else [],
                "risk_score": verdict["risk_score"],
                "confidence_score": verdict["confidence_score"],
                "safety_score": verdict["safety_score"],
                "denylist_hits": verdict.get("denylist_hits", []),
                "auto_apply_allowed": False,
                "require_human_confirm": True,
            },
            component="sentinel",
            actionable=True,
        )
        approved = bool(verdict.get("approved"))
        emit_compact_event(
            "AION_SENTINEL_AI_APPROVED" if approved else "AION_SENTINEL_AI_REJECTED",
            "AI patch proposal approved" if approved else "AI patch proposal rejected",
            {
                "issue_type": issue.issue_type,
                "variable": issue.variable or "unknown",
                "function_name": issue.function_name or "unknown",
                "risk_score": verdict["risk_score"],
                "confidence_score": verdict["confidence_score"],
                "safety_score": verdict["safety_score"],
                "auto_apply_allowed": False,
                "require_human_confirm": True,
                "denylist_hits": verdict.get("denylist_hits", []),
            },
            component="sentinel",
            level="WARN" if not approved else "INFO",
            actionable=True,
        )
        if self._require_human_confirm_for_ai_patch():
            emit_compact_event(
                "AION_SENTINEL_AI_REQUIRE_HUMAN_CONFIRM",
                "AI patch requires human confirmation",
                {"issue_type": issue.issue_type, "variable": issue.variable, "function_name": issue.function_name},
                component="sentinel",
                level="WARN",
            )
        return {
            "enabled": True,
            "reason": "require_human_confirm" if self._require_human_confirm_for_ai_patch() else "proposed",
            "package": package,
            "response": response,
            "structured": structured,
            "proposal": proposal_seed,
            "verification": verdict,
            "patch_preview": patch_preview,
            "queue_record": queue_record,
            "require_human_confirm": bool(self._require_human_confirm_for_ai_patch()),
            "auto_apply_allowed": False,
            "applied": False,
        }

    def _maybe_emit_profit_recommendation(self, evidence: Dict[str, Any], guardian_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        guardian_state = guardian_state if isinstance(guardian_state, dict) else {}
        profit = evidence.get("profit_engine") if isinstance(evidence, dict) else {}
        profit = profit if isinstance(profit, dict) else {}
        family_heatmap = evidence.get("family_heatmap") if isinstance(evidence, dict) else {}
        family_heatmap = family_heatmap if isinstance(family_heatmap, dict) else {}
        if not profit:
            return None
        profitable_rules = list(profit.get("profitable_loosened_rules") or [])
        current_state = str(profit.get("current_profitability_state") or "").strip().lower()
        avg_ev = float(profit.get("average_expected_value") or profit.get("total_expected_value") or 0.0)
        avg_conf = float(profit.get("average_confidence") or 0.0)
        execution_rate = float(profit.get("execution_rate") or 0.0)
        profitable_families = list(family_heatmap.get("profitable_families") or [])
        execution_too_low = any(
            bool(guardian_state.get(key)) for key in ("dormant_pipeline",)
        ) or any(
            int(guardian_state.get(key, 0) or 0) == 0 for key in ("final_submit_preflight_count", "final_submit_call_count", "order_submitted_count")
        )
        if not execution_too_low or (not profitable_rules and not profitable_families) or avg_ev <= 0.0 or avg_conf < 0.60:
            return None
        recommendation = {
            "timestamp": _now_iso(),
            "execution_too_low": True,
            "current_profitability_state": current_state or "unknown",
            "average_expected_value": avg_ev,
            "average_confidence": avg_conf,
            "execution_rate": execution_rate,
            "profitable_rules": profitable_rules[:10],
            "dangerous_rules": list(profit.get("dangerous_loosened_rules") or [])[:10],
            "profitable_families": profitable_families[:20],
            "recommended_live_policy": profit.get("recommended_live_policy") or {},
            "paper_only_rules": profit.get("paper_only_rules") or [],
        }
        print("[SENTINEL-PROFIT-POLICY-RECOMMENDATION] " + json.dumps(recommendation, sort_keys=True, default=str))
        self._send_telegram_summary({
            "telegram_text": (
                "AION Sentinel\n"
                f"- tags: SENTINEL-PROFIT-POLICY-RECOMMENDATION\n"
                f"- current_profitability_state: {current_state or 'unknown'}\n"
                f"- average_expected_value: {avg_ev}\n"
                f"- average_confidence: {avg_conf}\n"
                f"- execution_rate: {execution_rate}\n"
                f"- profitable_rules: {', '.join(profitable_rules[:10]) or 'none'}\n"
                f"- profitable_families: {', '.join(profitable_families[:20]) or 'none'}\n"
                f"- dangerous_rules: {', '.join((profit.get('dangerous_loosened_rules') or [])[:10]) or 'none'}\n"
                f"- recommended_live_policy: {json.dumps(profit.get('recommended_live_policy') or {}, sort_keys=True, default=str)}"
            )
        })
        return recommendation

    def _backup_files(self, files: List[Path], incident_dir: Path) -> Path:
        backup_dir = BACKUPS_ROOT / incident_dir.name
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            if path.exists():
                shutil.copy2(path, backup_dir / path.name)
        return backup_dir

    def _restore_backup_dir(self, backup_dir: Path, files: List[Path]) -> Dict[str, Any]:
        restored = []
        missing = []
        for path in files:
            backup_path = backup_dir / path.name
            if backup_path.exists():
                shutil.copy2(backup_path, path)
                restored.append(str(path))
            else:
                missing.append(str(path))
        return {"restored": restored, "missing": missing, "ok": bool(restored)}

    def _function_block(self, text: str, function_name: str) -> Optional[Tuple[int, int]]:
        pattern = re.compile(rf"^([ \t]*)def {re.escape(function_name)}\b", re.MULTILINE)
        match = pattern.search(text)
        if not match:
            return None
        start = match.start()
        indent = match.group(1) or ""
        tail = text[match.end():]
        next_def = re.search(rf"^\n{re.escape(indent)}def \w+", tail, re.MULTILINE)
        end = match.end() + (next_def.start() if next_def else len(tail))
        return start, end

    def _insert_after_line(self, text: str, needle: str, insertion: str) -> Tuple[str, bool]:
        if insertion.strip() in text:
            return text, False
        idx = text.find(needle)
        if idx < 0:
            return text, False
        insert_at = idx + len(needle)
        return text[:insert_at] + insertion + text[insert_at:], True

    def _apply_scope_initializer_patch(self, path: Path, variable: Optional[str], function_name: Optional[str] = None, context: Optional[Dict[str, Any]] = None, recipe: Optional[Dict[str, Any]] = None, patch_all: bool = False) -> Tuple[bool, str, List[str]]:
        if not path.exists() or not variable:
            return False, "no_matching_recipe", []
        text = path.read_text(encoding="utf-8")
        blocks = self._find_all_function_blocks_for_variable(text, variable, function_name=function_name)
        if not blocks:
            return False, "function_not_found", []
        if not patch_all and function_name:
            blocks = blocks[:1]
        initializer_expr, initializer_kind = self.scope_handler.derive_initializer(variable, recipe=recipe, context=context)
        if recipe is None or str(recipe.get("initializer_strategy") or "").strip().lower() == "derive_selected_price":
            fallback_expr, fallback_kind = self._classify_initializer(variable, context=context)
            if initializer_expr == "False" and fallback_expr not in ("False", '"unknown"', "{}", "[]", "0.0"):
                initializer_expr, initializer_kind = fallback_expr, fallback_kind
        changed_functions: List[str] = []
        for resolved_function_name, block in sorted(blocks, key=lambda item: item[1][0], reverse=True):
            block_text = text[block[0]:block[1]]
            function_indent_match = re.match(r"^([ \t]*)def\s+", block_text)
            function_indent = function_indent_match.group(1) if function_indent_match else ""
            body_indent = f"{function_indent}    "
            initializer_line = f"{body_indent}{variable} = {initializer_expr}"
            insertion_at = block[0] + self._function_insertion_offset(block_text, function_indent=function_indent)
            top_scope = block_text[: max(0, insertion_at - block[0])]
            if re.search(rf"^\s*{re.escape(variable)}\s*=\s*{re.escape(initializer_expr)}\s*$", top_scope, re.MULTILINE):
                continue
            text = text[:insertion_at] + ("\n" if not text[:insertion_at].endswith("\n") else "") + initializer_line + "\n" + text[insertion_at:]
            changed_functions.append(resolved_function_name)
        if not changed_functions:
            return False, "already_patched", []
        path.write_text(text, encoding="utf-8")
        self._record_learning_pattern(variable=variable, kind=initializer_kind, initializer=initializer_expr, function_name=changed_functions[0], file_path=str(path))
        return True, f"inserted_{variable}_initializer", changed_functions

    def _apply_patch_recipe(self, issue: SentinelIssue, incident_dir: Path) -> Dict[str, Any]:
        if not issue.autopatch_allowed or not self.autopatch:
            return {"applied": False, "reason": "autopatch_disabled"}
        recipe = self._find_recipe_for_issue(issue)
        if issue.issue_type in {"scope_bug", "telegram_runtime_alert"} and issue.file_path:
            if recipe is None and not issue.variable:
                suggested = issue.evidence.get("suggested_recipe_template") if isinstance(issue.evidence, dict) else None
                return {"applied": False, "reason": "no_recipe", "files_changed": [], "suggested_recipe": suggested}
            recent_failure_count = int((issue.evidence or {}).get("recent_failure_count", 0) or 0)
            candidate_scopes = (issue.traceback_context or {}).get("candidate_scopes") if isinstance(issue.traceback_context, dict) else []
            patch_all = bool(
                recent_failure_count >= 2
                or issue.function_name is None
                or (
                    isinstance(issue.variable, str)
                    and issue.variable.startswith("canonical_")
                    and issue.variable.endswith("_allowed")
                )
                or (isinstance(candidate_scopes, list) and len(candidate_scopes) > 1)
            )
            changed, reason, changed_functions = self._apply_scope_initializer_patch(
                Path(issue.file_path),
                issue.variable,
                function_name=issue.function_name,
                context=issue.traceback_context,
                recipe=recipe,
                patch_all=patch_all,
            )
            payload = {
                "applied": changed,
                "reason": "AUTO-PATCH-ESCALATED" if patch_all and changed else reason,
                "files_changed": [issue.file_path] if changed else [],
                "functions_patched": changed_functions if changed else [],
            }
            if patch_all:
                payload["scope_reconciled"] = True
                payload["recent_failure_count"] = recent_failure_count
            if recipe is not None:
                payload["recipe"] = recipe
            if not changed and issue.evidence.get("suggested_recipe_template"):
                payload["suggested_recipe"] = issue.evidence.get("suggested_recipe_template")
            return payload
        if issue.issue_type == "legacy_authority":
            return {"applied": False, "reason": "compatibility_only_no_live_patch_needed", "files_changed": []}
        if issue.issue_type == "stale_blocker_propagation":
            return {"applied": False, "reason": "already_converted_to_telemetry_only", "files_changed": []}
        if recipe is None:
            return {
                "applied": False,
                "reason": "no_recipe",
                "files_changed": [],
                "suggested_recipe": issue.evidence.get("suggested_recipe_template") if isinstance(issue.evidence, dict) else None,
            }
        return {"applied": False, "reason": "recipe_matched_but_not_applicable", "files_changed": [], "recipe": recipe}

    def _find_recipe_for_issue(self, issue: SentinelIssue) -> Optional[Dict[str, Any]]:
        issue_type = str(issue.issue_type or "").strip()
        variable = str(issue.variable or "").strip()
        text = "\n".join([
            json.dumps(issue.evidence or {}, sort_keys=True, default=str),
            issue.evidence.get("journal_excerpt", "") if isinstance(issue.evidence, dict) else "",
        ])
        for payload in (self.recipe_library or {}).values():
            if not isinstance(payload, dict):
                continue
            recipes = payload.get("recipes", []) or []
            for recipe in recipes:
                if not isinstance(recipe, dict):
                    continue
                if not recipe.get("enabled", True) or not recipe.get("allowed", True):
                    continue
                recipe_issue_type = str(recipe.get("issue_type") or "").strip()
                if recipe_issue_type and recipe_issue_type != issue_type:
                    continue
                if recipe.get("name") == issue.patch_recipe:
                    return dict(recipe)
                if self.scope_handler._pattern_hits(recipe, text, variable):
                    return dict(recipe)
        return None

    def _run_compile(self) -> Dict[str, Any]:
        rc, out, err = _run_command(PY_COMPILE_CMD, timeout=240, allow_sudo_fallback=False)
        return {"returncode": rc, "stdout": out, "stderr": err, "ok": rc == 0}

    def _run_restart(self) -> Dict[str, Any]:
        rc, out, err = _run_command(["systemctl", "restart", KALSHI_SERVICE], timeout=120, allow_sudo_fallback=True)
        return {"returncode": rc, "stdout": out, "stderr": err, "ok": rc == 0}

    def _run_guardian(self) -> Dict[str, Any]:
        rc, out, err = _run_command(GUARDIAN_CMD, timeout=240, allow_sudo_fallback=True)
        return {"returncode": rc, "stdout": out, "stderr": err, "ok": rc == 0}

    def _verify_fresh(self) -> Dict[str, Any]:
        cmd = [
            "journalctl",
            "-u",
            KALSHI_UNIT,
            "--since",
            "5 minutes ago",
            "--no-pager",
            "-n",
            "1200",
        ]
        rc, out, err = _run_command(cmd, timeout=120, allow_sudo_fallback=True)
        matches = re.findall(
            r"NameError|UnboundLocalError|referenced before assignment|Telegram|TELEGRAM|AION-FINAL-SUBMIT|LIVE-EXECUTION-ORDER-SUBMITTED|LIVE-EXECUTION-ORDER-ERROR|Guardian|current_blocker",
            out + "\n" + err,
            flags=re.IGNORECASE,
        )
        return {"returncode": rc, "stdout": out, "stderr": err, "matches": matches, "clean": rc == 0 and not matches}

    def _send_telegram_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from aion_guardian_report import _send_telegram_message  # local import to avoid import-time side effects

            text = summary.get("telegram_text") or json.dumps(summary, sort_keys=True, default=str)
            sent = _send_telegram_message(text)
            return {"sent": bool(sent), "reason": "ok" if sent else "telegram_send_failed"}
        except Exception as exc:
            return {"sent": False, "reason": exc.__class__.__name__}

    def _render_telegram_summary_text(self, *, issue: SentinelIssue, patch_result: Dict[str, Any], compile_result: Dict[str, Any], restart_result: Dict[str, Any], verify_result: Dict[str, Any], reverted: bool = False) -> str:
        tags = [
            "SENTINEL-TELEGRAM-ERROR-DETECTED" if issue.issue_type in {"scope_bug", "telegram_runtime_alert"} else None,
            "AUTO-PATCH-APPLIED" if patch_result.get("applied") and not reverted else "AUTO-PATCH-FAILED" if not patch_result.get("applied") else "AUTO-PATCH-REVERTED",
            "VARIABLE-STABILIZED" if patch_result.get("applied") and not reverted else None,
            "SENTINEL-LEARNING-NEW-CLASS" if issue.variable or issue.evidence.get("suggested_recipe_template") else None,
            "AUTO-PATCH-ESCALATED" if patch_result.get("reason") == "AUTO-PATCH-ESCALATED" else None,
        ]
        tags = [tag for tag in tags if tag]
        suggested_recipe = issue.evidence.get("suggested_recipe_template") if isinstance(issue.evidence, dict) else None
        ai_bridge = issue.evidence.get("ai_bridge") if isinstance(issue.evidence, dict) else {}
        ai_verification = ai_bridge.get("verification") if isinstance(ai_bridge, dict) else {}
        ai_proposal = ai_bridge.get("proposal") if isinstance(ai_bridge, dict) else {}
        return (
            "AION Sentinel\n"
            f"- tags: {', '.join(tags) or 'none'}\n"
            f"- issue_type: {issue.issue_type}\n"
            f"- severity: {issue.severity}\n"
            f"- variable: {issue.variable or 'unknown'}\n"
            f"- function_name: {issue.function_name or 'unknown'}\n"
            f"- root_cause: {issue.evidence.get('journal_excerpt', 'unknown')[:240] if isinstance(issue.evidence, dict) else 'unknown'}\n"
            f"- affected_files: {', '.join([p for p in [issue.file_path] if p]) or 'unknown'}\n"
            f"- recipe: {issue.patch_recipe or 'none'}\n"
            f"- patch_applied: {bool(patch_result.get('applied'))}\n"
            f"- patch_reason: {patch_result.get('reason') or 'none'}\n"
            f"- ai_risk_score: {ai_verification.get('risk_score', 'n/a')}\n"
            f"- ai_confidence_score: {ai_verification.get('confidence_score', 'n/a')}\n"
            f"- ai_safety_score: {ai_verification.get('safety_score', 'n/a')}\n"
            f"- ai_proposed_files: {', '.join(ai_proposal.get('proposed_files') or []) if isinstance(ai_proposal, dict) else 'none'}\n"
            f"- failure_count: {issue.evidence.get('failure_count') if isinstance(issue.evidence, dict) else 'unknown'}\n"
            f"- compile_ok: {bool(compile_result.get('ok'))}\n"
            f"- restart_ok: {bool(restart_result.get('ok'))}\n"
            f"- fresh_error_clean: {bool(verify_result.get('clean'))}\n"
            f"- human_action_required: {'yes' if not verify_result.get('clean') or not compile_result.get('ok') else 'maybe'}\n"
            f"- remaining_blocker: {issue.remaining_blocker or 'none'}\n"
            f"- suggested_recipe: {json.dumps(suggested_recipe, sort_keys=True, default=str) if suggested_recipe else 'none'}"
        )

    def _incident_bundle(self, issue: SentinelIssue, evidence: Dict[str, Any], patch_result: Dict[str, Any], compile_result: Dict[str, Any], restart_result: Dict[str, Any], guardian_result: Dict[str, Any], verify_result: Dict[str, Any], incident_dir: Path) -> Dict[str, Any]:
        bundle = {
            "timestamp": _now_iso(),
            "issue": issue.to_dict(),
            "evidence": evidence,
            "patch_result": patch_result,
            "compile_result": compile_result,
            "restart_result": restart_result,
            "guardian_result": guardian_result,
            "verify_result": verify_result,
        }
        safe_atomic_json_write(incident_dir / "incident.json", bundle, sort_keys=True, indent=2)
        _write_text(incident_dir / "journal_excerpt.txt", evidence.get("journal", {}).get("stdout", "") or "")
        _write_text(incident_dir / "guardian_output.txt", evidence.get("guardian", {}).get("stdout", "") or "")
        _write_text(incident_dir / "guardian_error.txt", evidence.get("guardian", {}).get("stderr", "") or "")
        _write_text(incident_dir / "journal_error.txt", evidence.get("journal", {}).get("stderr", "") or "")
        _write_text(incident_dir / "patch.diff", patch_result.get("diff", ""))
        _write_text(incident_dir / "compile.txt", json.dumps(compile_result, sort_keys=True, default=str, indent=2))
        _write_text(incident_dir / "restart.txt", json.dumps(restart_result, sort_keys=True, default=str, indent=2))
        _write_text(incident_dir / "verify.txt", json.dumps(verify_result, sort_keys=True, default=str, indent=2))
        return bundle

    def _diff_for_files(self, before_text: str, after_text: str, file_path: str) -> str:
        return "".join(
            difflib.unified_diff(
                before_text.splitlines(True),
                after_text.splitlines(True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )

    def _build_scope_initializer_preview(
        self,
        path: Path,
        variable: Optional[str],
        *,
        function_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        recipe: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        if not path.exists() or not variable:
            return "", "no_preview_available"
        try:
            before_text = path.read_text(encoding="utf-8")
        except Exception:
            return "", "preview_read_failed"
        blocks = self._find_all_function_blocks_for_variable(before_text, variable, function_name=function_name)
        if not blocks:
            return "", "function_not_found"
        resolved_function_name, block = blocks[0]
        initializer_expr, initializer_kind = self.scope_handler.derive_initializer(variable, recipe=recipe, context=context)
        if recipe is None or str(recipe.get("initializer_strategy") or "").strip().lower() == "derive_selected_price":
            fallback_expr, fallback_kind = self._classify_initializer(variable, context=context)
            if initializer_expr == "False" and fallback_expr not in ("False", '"unknown"', "{}", "[]", "0.0"):
                initializer_expr, initializer_kind = fallback_expr, fallback_kind
        block_text = before_text[block[0]:block[1]]
        function_indent_match = re.match(r"^([ \t]*)def\s+", block_text)
        function_indent = function_indent_match.group(1) if function_indent_match else ""
        body_indent = f"{function_indent}    "
        insertion_at = block[0] + self._function_insertion_offset(block_text, function_indent=function_indent)
        before_preview = before_text
        after_preview = before_text[:insertion_at] + ("\n" if not before_text[:insertion_at].endswith("\n") else "") + f"{body_indent}{variable} = {initializer_expr}\n" + before_text[insertion_at:]
        preview = self._diff_for_files(before_preview, after_preview, str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path))
        preview_header = (
            f"Initialize {variable} at function top in {resolved_function_name} using {initializer_expr} ({initializer_kind}).\n"
            f"Patch location: function_top\n"
        )
        return preview_header + preview, initializer_kind

    def _run_py_compile_paths(self, paths: List[Path]) -> Dict[str, Any]:
        unique_paths: List[str] = []
        for path in paths:
            if path is None:
                continue
            resolved = str(path)
            if resolved not in unique_paths and resolved:
                unique_paths.append(resolved)
        if not unique_paths:
            return {"ok": False, "reason": "no_paths", "returncode": None, "stdout": "", "stderr": ""}
        rc, out, err = _run_command([sys.executable, "-m", "py_compile", *unique_paths], timeout=300, allow_sudo_fallback=False)
        return {"ok": rc == 0, "returncode": rc, "stdout": out, "stderr": err, "paths": unique_paths}

    def _create_patch_queue_record(
        self,
        *,
        issue: SentinelIssue,
        ai_bridge_result: Dict[str, Any],
        verification: Dict[str, Any],
        incident_dir: Optional[Path],
        patch_preview: str,
        unified_diff_patch: str,
        source: str,
    ) -> Dict[str, Any]:
        package = ai_bridge_result.get("package") if isinstance(ai_bridge_result, dict) else {}
        structured = ai_bridge_result.get("structured") if isinstance(ai_bridge_result, dict) else {}
        proposal = ai_bridge_result.get("proposal") if isinstance(ai_bridge_result, dict) else {}
        current_safety_state = (package or {}).get("issue_package", {}).get("current_safety_state") if isinstance(package, dict) else {}
        queue_record = patch_queue.create_proposal(
            issue=issue.to_dict(),
            ai_bridge=ai_bridge_result,
            structured=structured if isinstance(structured, dict) else {},
            proposal=proposal if isinstance(proposal, dict) else {},
            verification=verification if isinstance(verification, dict) else {},
            unified_diff_patch=unified_diff_patch,
            patch_preview=patch_preview,
            source=source,
            status="pending",
            incident_dir=incident_dir,
            current_safety_state=current_safety_state if isinstance(current_safety_state, dict) else issue.evidence.get("safety_state", {}),
        )
        issue.evidence["patch_queue_record"] = queue_record
        return queue_record

    def _load_patch_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        return patch_queue.load_proposal(proposal_id)

    def _apply_patch_proposal(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self._load_patch_proposal(proposal_id)
        if not proposal:
            return {"applied": False, "reason": "proposal_not_found", "proposal_id": proposal_id}
        if str(proposal.get("status") or "").lower() not in {"approved"}:
            return {"applied": False, "reason": "proposal_not_approved", "proposal_id": proposal_id, "status": proposal.get("status")}
        deny_hits = patch_queue.check_denylist(proposal)
        if deny_hits:
            patch_queue.update_proposal_status(
                proposal_id,
                "rejected",
                actor="sentinel",
                reason="denylist_block",
                details={"denylist_hits": deny_hits},
            )
            emit_compact_event(
                "AION_PATCH_DENYLIST_BLOCK",
                "patch proposal rejected by denylist",
                {"proposal_id": proposal_id, "denylist_hits": deny_hits, "status": proposal.get("status")},
                component="sentinel",
                level="ERROR",
                actionable=True,
            )
            return {"applied": False, "reason": "denylist_block", "denylist_hits": deny_hits, "proposal_id": proposal_id}
        files = [Path(path) for path in _safe_unique([str(path) for path in (proposal.get("affected_files") or []) if str(path).strip()])]
        if not files and proposal.get("file_path"):
            files = [Path(str(proposal.get("file_path")))]
        files = [path for path in files if path.exists()]
        if not files:
            patch_queue.update_proposal_status(proposal_id, "failed", actor="sentinel", reason="missing_affected_files")
            return {"applied": False, "reason": "missing_affected_files", "proposal_id": proposal_id}
        incident_dir = BACKUPS_ROOT / f"proposal_{proposal_id}"
        incident_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = self._backup_files(files, incident_dir)
        before_snapshots = {str(path): _read_text(path) for path in files}
        patch_type = str(proposal.get("patch_type") or "").strip().lower()
        applied = False
        apply_reason = "unsupported_patch_type"
        changed_functions: List[str] = []
        diff_text = ""
        if patch_type == "scope_initializer":
            target_path = files[0]
            variable = str(proposal.get("variable") or "").strip()
            function_name = str(proposal.get("function_name") or "").strip() or None
            context = {
                "kind": proposal.get("initializer_strategy"),
                "selected_price": proposal.get("default_value"),
                "selected_price_cents": proposal.get("default_value"),
            }
            recipe = {
                "name": proposal.get("proposal_id") or "approved_patch",
                "enabled": True,
                "issue_type": proposal.get("issue_type") or "scope_bug",
                "matches": [rf"name '{re.escape(variable)}' is not defined", rf"local variable '{re.escape(variable)}' referenced before assignment"] if variable else [],
                "variable_pattern": re.escape(variable) if variable else r".+",
                "initializer_strategy": proposal.get("initializer_strategy") or "false",
                "default_value": proposal.get("default_value"),
                "patch_location": "function_top",
                "allowed": True,
                "requires_compile": True,
                "requires_restart": bool(proposal.get("requires_restart", True)),
                "rollback_on_failure": True,
                "notes": proposal.get("notes") or "approved patch proposal",
            }
            applied, apply_reason, changed_functions = self._apply_scope_initializer_patch(
                target_path,
                variable,
                function_name=function_name,
                context=context,
                recipe=recipe,
                patch_all=bool(proposal.get("scope_reconciled")),
            )
        elif patch_type == "telemetry":
            applied = False
            apply_reason = "telemetry_only"
        else:
            applied = False
            apply_reason = "unsupported_patch_type"
        after_snapshots = {str(path): _read_text(path) for path in files}
        if applied:
            diffs = []
            for path_str, before_text in before_snapshots.items():
                after_text = after_snapshots.get(path_str, "")
                if before_text != after_text:
                    diffs.append(self._diff_for_files(before_text, after_text, str(Path(path_str).relative_to(REPO_ROOT)) if Path(path_str).is_relative_to(REPO_ROOT) else str(path_str)))
            diff_text = "\n".join(diffs)
        compile_result = {"ok": False, "reason": "not_run"}
        replay_result = {"ok": False, "reason": "not_run"}
        restart_result = {"ok": False, "reason": "not_run"}
        guardian_result = {"ok": False, "reason": "not_run"}
        verify_result = {"clean": False, "matches": [], "reason": "not_run"}
        if not applied:
            patch_queue.update_proposal_status(proposal_id, "failed", actor="sentinel", reason=apply_reason, details={"backup_dir": str(backup_dir), "patch_type": patch_type, "changed_functions": changed_functions})
            return {"applied": False, "reason": apply_reason, "proposal_id": proposal_id, "backup_dir": str(backup_dir)}
        emit_compact_event(
            "AION_PATCH_APPLY_START",
            "patch application started",
            {"proposal_id": proposal_id, "files": [str(path) for path in files], "patch_type": patch_type, "changed_functions": changed_functions},
            component="sentinel",
            actionable=True,
        )
        patch_queue.update_proposal_status(
            proposal_id,
            "applied",
            actor="sentinel",
            reason="patch_applied",
            details={
                "backup_dir": str(backup_dir),
                "patch_type": patch_type,
                "changed_functions": changed_functions,
                "diff": diff_text,
            },
        )
        compile_result = self._run_py_compile_paths(files)
        if not compile_result.get("ok"):
            restore_result = self._restore_backup_dir(backup_dir, files)
            patch_queue.update_proposal_status(proposal_id, "rolled_back", actor="sentinel", reason="compile_failed", details={"restore_result": restore_result, "compile_result": compile_result})
            emit_compact_event(
                "AION_PATCH_APPLY_FAILED",
                "patch application failed during compile",
                {"proposal_id": proposal_id, "reason": "compile_failed", "compile_ok": False},
                component="sentinel",
                level="ERROR",
                actionable=True,
            )
            emit_compact_event(
                "AION_PATCH_ROLLBACK",
                "patch rollback completed after compile failure",
                {"proposal_id": proposal_id, "restore_result": restore_result, "reason": "compile_failed"},
                component="sentinel",
                level="WARN",
                actionable=True,
            )
            return {
                "applied": True,
                "reason": "compile_failed",
                "proposal_id": proposal_id,
                "backup_dir": str(backup_dir),
                "compile_result": compile_result,
                "restore_result": restore_result,
                "rolled_back": True,
            }
        replay_cmd = [sys.executable, str(REPO_ROOT / "aion_execution_simulator.py"), "--replay-latest-issue"]
        replay_rc, replay_out, replay_err = _run_command(replay_cmd, timeout=900, allow_sudo_fallback=False)
        replay_result = {"ok": replay_rc == 0, "returncode": replay_rc, "stdout": replay_out, "stderr": replay_err}
        if bool(proposal.get("requires_restart", True)):
            restart_result = self._run_restart()
            if not restart_result.get("ok"):
                restore_result = self._restore_backup_dir(backup_dir, files)
                patch_queue.update_proposal_status(
                    proposal_id,
                    "rolled_back",
                    actor="sentinel",
                    reason="restart_failed",
                    details={"restore_result": restore_result, "compile_result": compile_result, "restart_result": restart_result, "replay_result": replay_result},
                )
                emit_compact_event(
                    "AION_PATCH_APPLY_FAILED",
                    "patch application failed during restart",
                    {"proposal_id": proposal_id, "reason": "restart_failed", "restart_ok": False},
                    component="sentinel",
                    level="ERROR",
                    actionable=True,
                )
                emit_compact_event(
                    "AION_PATCH_ROLLBACK",
                    "patch rollback completed after restart failure",
                    {"proposal_id": proposal_id, "restore_result": restore_result, "reason": "restart_failed"},
                    component="sentinel",
                    level="WARN",
                    actionable=True,
                )
                return {
                    "applied": True,
                    "reason": "restart_failed",
                    "proposal_id": proposal_id,
                    "backup_dir": str(backup_dir),
                    "compile_result": compile_result,
                    "restart_result": restart_result,
                    "replay_result": replay_result,
                    "restore_result": restore_result,
                    "rolled_back": True,
                }
            guardian_result = self._run_guardian()
            verify_result = self._verify_fresh()
            if not guardian_result.get("ok") or not verify_result.get("clean"):
                restore_result = self._restore_backup_dir(backup_dir, files)
                patch_queue.update_proposal_status(
                    proposal_id,
                    "rolled_back",
                    actor="sentinel",
                    reason="verification_failed",
                    details={
                        "restore_result": restore_result,
                        "compile_result": compile_result,
                        "restart_result": restart_result,
                        "guardian_result": guardian_result,
                        "verify_result": verify_result,
                        "replay_result": replay_result,
                    },
                )
                emit_compact_event(
                    "AION_PATCH_APPLY_FAILED",
                    "patch application failed during verification",
                    {"proposal_id": proposal_id, "reason": "verification_failed", "guardian_ok": bool(guardian_result.get("ok")), "fresh_error_clean": bool(verify_result.get("clean"))},
                    component="sentinel",
                    level="ERROR",
                    actionable=True,
                )
                emit_compact_event(
                    "AION_PATCH_ROLLBACK",
                    "patch rollback completed after verification failure",
                    {"proposal_id": proposal_id, "restore_result": restore_result, "reason": "verification_failed"},
                    component="sentinel",
                    level="WARN",
                    actionable=True,
                )
                return {
                    "applied": True,
                    "reason": "verification_failed",
                    "proposal_id": proposal_id,
                    "backup_dir": str(backup_dir),
                    "compile_result": compile_result,
                    "restart_result": restart_result,
                    "guardian_result": guardian_result,
                    "verify_result": verify_result,
                    "replay_result": replay_result,
                    "restore_result": restore_result,
                    "rolled_back": True,
                }
            patch_queue.update_proposal_status(
                proposal_id,
                "verified",
                actor="sentinel",
                reason="verification_passed",
                details={
                    "compile_result": compile_result,
                    "restart_result": restart_result,
                    "guardian_result": guardian_result,
                    "verify_result": verify_result,
                    "replay_result": replay_result,
                },
            )
            emit_compact_event(
                "AION_PATCH_VERIFIED",
                "patch verification completed",
                {"proposal_id": proposal_id, "compile_ok": True, "restart_ok": True, "guardian_ok": bool(guardian_result.get("ok")), "fresh_error_clean": bool(verify_result.get("clean"))},
                component="sentinel",
                actionable=True,
            )
        else:
            patch_queue.update_proposal_status(
                proposal_id,
                "verified",
                actor="sentinel",
                reason="verification_skipped_no_restart",
                details={"compile_result": compile_result, "replay_result": replay_result},
            )
            emit_compact_event(
                "AION_PATCH_VERIFIED",
                "patch verification completed",
                {"proposal_id": proposal_id, "compile_ok": True, "restart_ok": False, "guardian_ok": False, "fresh_error_clean": None, "replay_ok": bool(replay_result.get("ok"))},
                component="sentinel",
                actionable=True,
            )
        return {
            "applied": True,
            "reason": "applied",
            "proposal_id": proposal_id,
            "backup_dir": str(backup_dir),
            "compile_result": compile_result,
            "restart_result": restart_result,
            "guardian_result": guardian_result,
            "verify_result": verify_result,
            "replay_result": replay_result,
            "diff": diff_text,
            "changed_functions": changed_functions,
            "rolled_back": False,
        }

    def _rollback_patch_proposal(self, proposal_id: str, *, actor: str = "sentinel", reason: str = "rollback_requested") -> Dict[str, Any]:
        proposal = self._load_patch_proposal(proposal_id)
        if not proposal:
            return {"rolled_back": False, "reason": "proposal_not_found", "proposal_id": proposal_id}
        backup_dir = proposal.get("backup_dir")
        if not backup_dir:
            return {"rolled_back": False, "reason": "missing_backup_dir", "proposal_id": proposal_id}
        files = [Path(path) for path in _safe_unique([str(path) for path in (proposal.get("affected_files") or []) if str(path).strip()])]
        if not files and proposal.get("file_path"):
            files = [Path(str(proposal.get("file_path")))]
        files = [path for path in files if path.exists()]
        if not files:
            return {"rolled_back": False, "reason": "missing_affected_files", "proposal_id": proposal_id}
        backup_path = Path(str(backup_dir))
        restore_result = self._restore_backup_dir(backup_path, files)
        compile_result = self._run_py_compile_paths(files)
        patch_queue.update_proposal_status(
            proposal_id,
            "rolled_back",
            actor=actor,
            reason=reason,
            details={"restore_result": restore_result, "compile_result": compile_result},
        )
        emit_compact_event(
            "AION_PATCH_ROLLBACK",
            "patch rollback executed",
            {"proposal_id": proposal_id, "restore_result": restore_result, "compile_ok": bool(compile_result.get("ok"))},
            component="sentinel",
            level="WARN",
            actionable=True,
        )
        return {
            "rolled_back": True,
            "proposal_id": proposal_id,
            "reason": reason,
            "restore_result": restore_result,
            "compile_result": compile_result,
        }

    def _run_one_cycle(self, simulate_nameerror: bool = False) -> Dict[str, Any]:
        _ensure_dirs()
        evidence = self.collect_evidence()
        guardian_state = self._parse_guardian_audit(evidence.get("guardian", {}).get("stdout", "") or "")
        should_simulate, simulation_reason = self._guardian_requires_simulation(guardian_state, evidence.get("guardian", {}).get("stdout", "") or "")
        if should_simulate:
            print(
                "[SENTINEL-SIMULATION-TRIGGERED] "
                + json.dumps(
                    {
                        "reason": simulation_reason,
                        "guardian_state": guardian_state,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            sim_rc, sim_out, sim_err = _run_command(SIMULATION_CMD, timeout=600, allow_sudo_fallback=True)
            evidence["simulation"] = {
                "returncode": sim_rc,
                "stdout": sim_out,
                "stderr": sim_err,
            }
        self._maybe_emit_profit_recommendation(evidence, guardian_state)
        if simulate_nameerror:
            return self._run_simulated_nameerror_cycle()
        issue = self.detect_issue(evidence)
        if issue is None:
            summary = {
                "timestamp": _now_iso(),
                "issue_detected": False,
                "patch_applied": False,
                "remaining_blocker": None,
                "fresh_error_status": "clean",
            }
            safe_atomic_json_write(STATE_FILE, self.state, sort_keys=True, indent=2)
            print("[SENTINEL-FRESH-ERROR-CLEAN] " + json.dumps(summary, sort_keys=True, default=str))
            print("[AION-SENTINEL-IDLE] " + json.dumps(summary, sort_keys=True, default=str))
            return summary

        memory_match = memory_find_match(issue.to_dict(), emit=False)
        memory_record = memory_record_issue(
            issue.to_dict(),
            verification={
                "guardian": guardian_state,
                "fresh_error_clean": False,
            },
            proposal={
                "patch_recipe": issue.patch_recipe,
                "remaining_blocker": issue.remaining_blocker,
                "file_path": issue.file_path,
                "function_name": issue.function_name,
            },
        )
        issue.evidence["memory_record"] = memory_record
        issue.evidence["memory_summary"] = latest_memory_summary()
        if memory_match is not None:
            issue.evidence["memory_match"] = memory_match

        incident_dir = INCIDENTS_ROOT / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"
        incident_dir.mkdir(parents=True, exist_ok=True)
        files_to_backup = [REPO_ROOT / "kalshi_web.py", REPO_ROOT / "aion_guardian_report.py"]
        backup_dir = self._backup_files(files_to_backup, incident_dir)
        issue.evidence["backup_dir"] = str(backup_dir)
        before_snapshots = {str(path): _read_text(path) for path in files_to_backup if path.exists()}
        print(
            "[SENTINEL-AUTO-PATCH-START] "
            + json.dumps(
                {
                    "issue_type": issue.issue_type,
                    "variable": issue.variable or "unknown",
                    "function_name": issue.function_name or "unknown",
                    "file_path": issue.file_path or "unknown",
                    "telegram_hits": issue.evidence.get("telegram_hits", []),
                    "recent_failure_count": issue.evidence.get("recent_failure_count"),
                },
                sort_keys=True,
                default=str,
            )
        )
        if issue.evidence.get("telegram_hits"):
            print(
                "[SENTINEL-TELEGRAM-ERROR-DETECTED] "
                + json.dumps(
                    {
                        "issue_type": issue.issue_type,
                        "variable": issue.variable or "unknown",
                        "function_name": issue.function_name or "unknown",
                        "traceback_context": issue.traceback_context,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        patch_result = self._apply_patch_recipe(issue, incident_dir)
        ai_bridge_result = None
        if not patch_result.get("applied") and issue.autopatch_allowed:
            ai_bridge_result = self._maybe_run_ai_bridge(issue, evidence, incident_dir)
            if ai_bridge_result.get("applied") and isinstance(ai_bridge_result.get("patch_result"), tuple):
                changed, reason, changed_functions = ai_bridge_result["patch_result"]
                patch_result = {
                    "applied": bool(changed),
                    "reason": reason,
                    "files_changed": [issue.file_path] if changed else [],
                    "functions_patched": changed_functions if changed else [],
                    "recipe": ai_bridge_result.get("recipe"),
                }
                if ai_bridge_result.get("reason") == "human_confirmation_required":
                    patch_result["applied"] = False
                    patch_result["reason"] = "human_confirmation_required"
        after_snapshots = {str(path): _read_text(path) for path in files_to_backup if path.exists()}
        if patch_result.get("applied"):
            diffs = []
            for path_str, before_text in before_snapshots.items():
                after_text = after_snapshots.get(path_str, "")
                if before_text != after_text:
                    diffs.append(self._diff_for_files(before_text, after_text, str(Path(path_str).relative_to(REPO_ROOT))))
            patch_result["diff"] = "\n".join(diffs)
        else:
            patch_result["diff"] = ""
        compile_result = {"ok": False, "returncode": None}
        restart_result = {"ok": False, "returncode": None}
        guardian_result = {"ok": False, "returncode": None}
        verify_result = {"clean": False, "matches": []}
        if patch_result.get("applied"):
            self._record_patch()
            compile_result = self._run_compile()
            if not compile_result.get("ok"):
                restore_result = self._restore_backup_dir(backup_dir, files_to_backup)
                patch_result["reverted"] = True
                patch_result["revert_reason"] = "compile_failed"
                patch_result["restore_result"] = restore_result
                compile_result["restore_ok"] = restore_result.get("ok")
                restart_result = {"ok": False, "reason": "compile_failed", "reverted": True}
                guardian_result = {"ok": False, "reason": "compile_failed"}
                verify_result = {"clean": False, "matches": [], "reason": "compile_failed"}
                print(
                    "[SENTINEL-AUTO-PATCH-REVERTED] "
                    + json.dumps(
                        {
                            "reason": "compile_failed",
                            "issue_type": issue.issue_type,
                            "variable": issue.variable or "unknown",
                            "function_name": issue.function_name or "unknown",
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
            else:
                restart_result = self._run_restart() if compile_result.get("ok") or not bool(os.environ.get("AION_SENTINEL_REQUIRE_COMPILE", "true").lower() == "true") else {"ok": False, "reason": "compile_failed"}
                if not restart_result.get("ok"):
                    restore_result = self._restore_backup_dir(backup_dir, files_to_backup)
                    patch_result["reverted"] = True
                    patch_result["revert_reason"] = "restart_failed"
                    patch_result["restore_result"] = restore_result
                    guardian_result = {"ok": False, "reason": "restart_failed"}
                    verify_result = {"clean": False, "matches": [], "reason": "restart_failed"}
                    print(
                        "[SENTINEL-AUTO-PATCH-REVERTED] "
                        + json.dumps(
                            {
                                "reason": "restart_failed",
                                "issue_type": issue.issue_type,
                                "variable": issue.variable or "unknown",
                                "function_name": issue.function_name or "unknown",
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                else:
                    guardian_result = self._run_guardian() if restart_result.get("ok") or not bool(os.environ.get("AION_SENTINEL_REQUIRE_RESTART_VERIFY", "true").lower() == "true") else {"ok": False, "reason": "restart_failed"}
                    verify_result = self._verify_fresh() if restart_result.get("ok") else {"clean": False, "matches": [], "reason": "restart_failed"}
            if patch_result.get("scope_reconciled"):
                print(
                    "[SENTINEL-SCOPE-ROOT-FOUND] "
                    + json.dumps(
                        {
                            "variable": issue.variable or "unknown",
                            "functions_patched": patch_result.get("functions_patched", []),
                            "canonical_source_used": issue.patch_recipe,
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                print(
                    "[AION-SCOPE-RECONCILED] "
                    + json.dumps(
                        {
                            "variable": issue.variable,
                            "functions_patched": patch_result.get("functions_patched"),
                            "unresolved_paths": issue.evidence.get("suggested_recipe_template") if isinstance(issue.evidence, dict) else None,
                            "canonical_source_used": issue.patch_recipe,
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                print(
                    "[SENTINEL-SCOPE-RECONCILED] "
                    + json.dumps(
                        {
                            "variable": issue.variable or "unknown",
                            "functions_patched": patch_result.get("functions_patched", []),
                            "unresolved_paths": issue.evidence.get("suggested_recipe_template") if isinstance(issue.evidence, dict) else None,
                            "canonical_source_used": issue.patch_recipe,
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                if len(patch_result.get("functions_patched", [])) > 1:
                    print(
                        "[SENTINEL-DUPLICATE-INITIALIZER-COLLAPSED] "
                        + json.dumps(
                            {
                                "variable": issue.variable or "unknown",
                                "functions_patched": patch_result.get("functions_patched", []),
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                print(
                    "[SENTINEL-CANONICAL-ROOT-MOVED] "
                    + json.dumps(
                        {
                            "variable": issue.variable or "unknown",
                            "canonical_source_used": issue.patch_recipe,
                            "functions_patched": patch_result.get("functions_patched", []),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
            if patch_result.get("applied") and not patch_result.get("reverted"):
                print(
                    "[SENTINEL-AUTO-PATCH-APPLIED] "
                    + json.dumps(
                        {
                            "issue_type": issue.issue_type,
                            "variable": issue.variable or "unknown",
                            "function_name": issue.function_name or "unknown",
                            "recipe": patch_result.get("recipe", {}).get("name") if isinstance(patch_result.get("recipe"), dict) else patch_result.get("reason"),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                print(
                    "[VARIABLE-STABILIZED] "
                    + json.dumps(
                        {
                            "variable": issue.variable or "unknown",
                            "function_name": issue.function_name or "unknown",
                            "files_changed": patch_result.get("files_changed", []),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
            elif not patch_result.get("applied"):
                print(
                    "[SENTINEL-AUTO-PATCH-FAILED] "
                    + json.dumps(
                        {
                            "issue_type": issue.issue_type,
                            "variable": issue.variable or "unknown",
                            "function_name": issue.function_name or "unknown",
                            "reason": patch_result.get("reason"),
                            "suggested_recipe": patch_result.get("suggested_recipe"),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
        if ai_bridge_result is not None:
            evidence["ai_bridge"] = ai_bridge_result
        bundle = self._incident_bundle(issue, evidence, patch_result, compile_result, restart_result, guardian_result, verify_result, incident_dir)
        sent = self._send_telegram_summary(
            {
                "issue_detected": True,
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "patch_applied": bool(patch_result.get("applied")),
                "files_changed": patch_result.get("files_changed", []),
                "compile_ok": bool(compile_result.get("ok")),
                "restart_ok": bool(restart_result.get("ok")),
                "fresh_error_clean": bool(verify_result.get("clean")),
                "remaining_blocker": issue.remaining_blocker,
                "telegram_text": self._render_telegram_summary_text(
                    issue=issue,
                    patch_result=patch_result,
                    compile_result=compile_result,
                    restart_result=restart_result,
                    verify_result=verify_result,
                    reverted=bool(patch_result.get("reverted")),
                ),
            }
        )
        bundle["telegram_result"] = sent
        safe_atomic_json_write(incident_dir / "incident.json", bundle, sort_keys=True, indent=2)
        return bundle

    def _run_simulated_nameerror_cycle(self) -> Dict[str, Any]:
        _ensure_dirs()
        incident_dir = INCIDENTS_ROOT / f"simulation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"
        incident_dir.mkdir(parents=True, exist_ok=True)
        sample_path = incident_dir / "sample.py"
        before = """def demo():\n    if price_bounds_pass and profile == 'beast':\n        return True\n    return False\n"""
        sample_path.write_text(before, encoding="utf-8")
        issue = SentinelIssue(
            issue_type="scope_bug",
            severity="high",
            autopatch_allowed=True,
            variable="price_bounds_pass",
            file_path=str(sample_path),
            function_name="demo",
            evidence={"simulation": True, "traceback_context": {"function_name": "demo", "variable": "price_bounds_pass"}},
            patch_recipe="scope_initializer",
            remaining_blocker="runtime_scope_crash",
        )
        patch_result = {"applied": False, "files_changed": [], "reason": "simulation"}
        recipe = self.scope_handler.match_recipe(issue_type="scope_bug", analysis={"raw_text": "name 'price_bounds_pass' is not defined", "variable": "price_bounds_pass", "function_name": "demo"}, extra_text="name 'price_bounds_pass' is not defined")
        changed, reason, changed_functions = self._apply_scope_initializer_patch(sample_path, "price_bounds_pass", function_name="demo", context=issue.traceback_context, recipe=recipe)
        if changed:
            patch_result = {"applied": True, "files_changed": [str(sample_path)], "functions_patched": changed_functions, "reason": reason, "recipe": recipe}
        compile_rc, compile_out, compile_err = _run_command([sys.executable, "-m", "py_compile", str(sample_path)], timeout=60)
        verify_ok = compile_rc == 0 and "NameError" not in (compile_out + compile_err) and "UnboundLocalError" not in (compile_out + compile_err)
        bundle = {
            "timestamp": _now_iso(),
            "issue": issue.to_dict(),
            "patch_result": patch_result,
            "compile_result": {"returncode": compile_rc, "stdout": compile_out, "stderr": compile_err, "ok": compile_rc == 0},
            "verify_result": {"clean": verify_ok},
        }
        safe_atomic_json_write(incident_dir / "incident.json", bundle, sort_keys=True, indent=2)
        _write_text(incident_dir / "patch.diff", self._diff_for_files(before, sample_path.read_text(encoding="utf-8"), str(sample_path.relative_to(REPO_ROOT)) if sample_path.is_relative_to(REPO_ROOT) else str(sample_path)))
        print("[AION-SENTINEL-SIMULATION] " + json.dumps(bundle, sort_keys=True, default=str))
        return bundle

    def _run_simulated_unresolved_issue_cycle(self) -> Dict[str, Any]:
        return self._run_simulated_ai_escalation_cycle()

    def _run_simulated_ai_escalation_cycle(self) -> Dict[str, Any]:
        _ensure_dirs()
        issue = SentinelIssue(
            issue_type="scope_bug",
            severity="high",
            autopatch_allowed=False,
            variable="canonical_final_submit_allowed",
            file_path=str(REPO_ROOT / "kalshi_web.py"),
            function_name="maybe_emit_summary",
            evidence={
                "simulation": True,
                "blocker_tags": ["runtime_scope_crash"],
                "telegram_hits": ["local variable 'canonical_final_submit_allowed' referenced before assignment"],
                "journal_excerpt": "local variable 'canonical_final_submit_allowed' referenced before assignment",
            },
            patch_recipe="learned_canonical_tradeability",
            remaining_blocker="runtime_scope_crash",
            traceback_context={"function_name": "maybe_emit_summary", "variable": "canonical_final_submit_allowed"},
        )
        package = self._build_ai_escalation_package(issue=issue, evidence=issue.evidence, incident_dir=None, persist=False)
        response = self._openai_responses_create(prompt=package["prompt"], issue=issue, incident_dir=None, simulate=True)
        structured = response.get("structured") if isinstance(response, dict) else {}
        proposal_seed = self._structured_ai_proposal_seed(issue, structured)
        verdict = self._score_ai_patch_proposal(issue=issue, structured=structured if isinstance(structured, dict) else {}, recipe=proposal_seed)
        patch_preview, preview_kind = self._build_scope_initializer_preview(
            Path(issue.file_path) if issue.file_path else Path(REPO_ROOT / "kalshi_web.py"),
            issue.variable,
            function_name=issue.function_name,
            context=issue.traceback_context,
            recipe=proposal_seed,
        )
        queue_record = patch_queue.create_proposal(
            issue=issue.to_dict(),
            ai_bridge={
                **response,
                "package": package,
                "proposal": proposal_seed,
            },
            structured=structured if isinstance(structured, dict) else {},
            proposal=proposal_seed,
            verification=verdict,
            unified_diff_patch=patch_preview,
            patch_preview=patch_preview or proposal_seed.get("notes", ""),
            source="ai_bridge_simulation",
            status="pending",
            current_safety_state=issue.evidence.get("safety_state", {}),
        )
        bundle = {
            "timestamp": _now_iso(),
            "issue": issue.to_dict(),
            "package": package,
            "enabled": response.get("enabled"),
            "called": bool(response.get("response") or response.get("raw")),
            "applied": False,
            "simulation": True,
            "structured": response.get("structured"),
            "queue_record": queue_record,
            "verification": verdict,
            "patch_preview": patch_preview,
        }
        emit_event("AION_SENTINEL_AI_SIMULATION", {"bundle": bundle}, component="sentinel", actionable=True)
        return bundle

    def _latest_simulation_issue_path(self) -> Optional[Path]:
        if not SIMULATION_ISSUES_ROOT.exists():
            return None
        try:
            issue_files = sorted(
                [path for path in SIMULATION_ISSUES_ROOT.glob("*.json") if path.is_file()],
                key=lambda path: path.stat().st_mtime,
            )
        except Exception:
            return None
        return issue_files[-1] if issue_files else None

    def _simulation_issue_to_sentinel_issue(self, issue_data: Dict[str, Any]) -> SentinelIssue:
        issue_data = issue_data if isinstance(issue_data, dict) else {}
        bug_type = str(issue_data.get("bug_type") or "").strip()
        failed_phase = str(issue_data.get("failed_phase") or "").strip()
        blocker_label = str(issue_data.get("blocker_label") or issue_data.get("reason") or failed_phase or "simulation_failure").strip()
        variable = issue_data.get("variable")
        if not variable and isinstance(issue_data.get("raw_candidate"), dict):
            variable = issue_data["raw_candidate"].get("selected_price_source_used") or issue_data["raw_candidate"].get("selected_side")
        issue_type = "simulation_failure"
        if bug_type in {"NameError", "UnboundLocalError", "AttributeError", "TypeError", "ValueError", "SyntaxError"}:
            issue_type = "scope_bug"
        elif any(token in blocker_label for token in ("unsafe_spread", "unsafe_price", "quote_missing", "orderbook_not_usable", "phase55")):
            issue_type = "stale_blocker_propagation"
        elif "telegram" in str(issue_data.get("issue_type") or "").lower():
            issue_type = "telegram_runtime_alert"
        return SentinelIssue(
            issue_type=issue_type,
            severity=str(issue_data.get("severity") or "high"),
            autopatch_allowed=True,
            variable=variable,
            file_path=issue_data.get("file") or str(REPO_ROOT / "kalshi_web.py"),
            function_name=issue_data.get("function") or issue_data.get("function_name"),
            evidence={
                "simulation_issue": issue_data,
                "raw_candidate": issue_data.get("raw_candidate"),
                "canonical_execution_state": issue_data.get("canonical_execution_state"),
                "payload": issue_data.get("payload"),
                "stacktrace": issue_data.get("stacktrace"),
                "suggested_fix": issue_data.get("suggested_fix"),
                "repeat_count": issue_data.get("repeat_count"),
            },
            patch_recipe=issue_data.get("patch_recipe") or issue_data.get("suggested_recipe") or (
                "missing_price_bounds_pass" if str(variable or "").strip() == "price_bounds_pass" else None
            ),
            remaining_blocker=blocker_label,
            traceback_context={
                "variable": variable,
                "function_name": issue_data.get("function") or issue_data.get("function_name"),
                "file_path": issue_data.get("file"),
                "failed_phase": failed_phase,
            },
        )

    def _run_simulation_remediation(self, *, max_attempts: int = 5) -> Dict[str, Any]:
        _ensure_dirs()
        results: List[Dict[str, Any]] = []
        latest_issue_path = self._latest_simulation_issue_path()
        issue_data = None
        if latest_issue_path and latest_issue_path.exists():
            try:
                issue_data = json.loads(latest_issue_path.read_text(encoding="utf-8"))
            except Exception:
                issue_data = None
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            sim_rc, sim_out, sim_err = _run_command(SIMULATION_CMD, timeout=900, allow_sudo_fallback=True)
            sim_pass = sim_rc == 0 and "AION-SIMULATION-PASS" in (sim_out or "")
            result = {
                "attempt": attempt,
                "simulation": {"returncode": sim_rc, "stdout": sim_out[-10000:], "stderr": sim_err[-5000:]},
                "simulation_pass": sim_pass,
            }
            if sim_pass:
                result["status"] = "pass"
                results.append(result)
                break
            latest_issue_path = self._latest_simulation_issue_path()
            if latest_issue_path and latest_issue_path.exists():
                try:
                    issue_data = json.loads(latest_issue_path.read_text(encoding="utf-8"))
                except Exception:
                    issue_data = issue_data or {}
            if not isinstance(issue_data, dict):
                issue_data = {}
            issue = self._simulation_issue_to_sentinel_issue(issue_data)
            issue.evidence.setdefault("simulation", result["simulation"])
            issue.evidence.setdefault("telegram_hits", [])
            patch_result = {"applied": False, "files_changed": [], "reason": "no_recipe"}
            if self.autopatch and not self.dry_run and issue.autopatch_allowed:
                incident_dir = INCIDENTS_ROOT / f"simulation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"
                incident_dir.mkdir(parents=True, exist_ok=True)
                files_to_backup = [REPO_ROOT / "kalshi_web.py", REPO_ROOT / "aion_guardian_report.py"]
                backup_dir = self._backup_files(files_to_backup, incident_dir)
                issue.evidence["backup_dir"] = str(backup_dir)
                before_snapshots = {str(path): _read_text(path) for path in files_to_backup if path.exists()}
                patch_result = self._apply_patch_recipe(issue, incident_dir)
                after_snapshots = {str(path): _read_text(path) for path in files_to_backup if path.exists()}
                if patch_result.get("applied"):
                    diffs = []
                    for path_str, before_text in before_snapshots.items():
                        after_text = after_snapshots.get(path_str, "")
                        if before_text != after_text:
                            diffs.append(self._diff_for_files(before_text, after_text, str(Path(path_str).relative_to(REPO_ROOT))))
                    patch_result["diff"] = "\n".join(diffs)
                    compile_result = self._run_compile()
                    if compile_result.get("ok"):
                        restart_result = self._run_restart()
                        guardian_result = self._run_guardian() if restart_result.get("ok") else {"ok": False, "reason": "restart_failed"}
                        verify_result = self._verify_fresh() if restart_result.get("ok") else {"clean": False, "matches": [], "reason": "restart_failed"}
                    else:
                        restart_result = {"ok": False, "reason": "compile_failed"}
                        guardian_result = {"ok": False, "reason": "compile_failed"}
                        verify_result = {"clean": False, "matches": [], "reason": "compile_failed"}
                else:
                    patch_result["diff"] = ""
                    compile_result = {"ok": False, "returncode": None}
                    restart_result = {"ok": False, "returncode": None}
                    guardian_result = {"ok": False, "returncode": None}
                    verify_result = {"clean": False, "matches": []}
            result.update(
                {
                    "issue": issue.to_dict(),
                    "patch": patch_result,
                    "status": "failed",
                }
            )
            results.append(result)
            if issue_data.get("repeat_count", 0) and int(issue_data.get("repeat_count") or 0) >= int(os.environ.get("AION_SENTINEL_ESCALATE_AFTER_ATTEMPTS", "3") or 3):
                print(
                    "[AUTO-PATCH-ESCALATED] "
                    + json.dumps(
                        {
                            "issue_type": issue.issue_type,
                            "variable": issue.variable,
                            "function_name": issue.function_name,
                            "repeat_count": issue_data.get("repeat_count"),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                break
        payload = {"attempts": results, "passed": bool(results and results[-1].get("simulation_pass"))}
        print("[SENTINEL-SIMULATION-REMEDIATION] " + json.dumps(payload, sort_keys=True, default=str))
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="AION Sentinel Auto-Patch Agent")
    parser.add_argument("--once", action="store_true", help="Run one detection/patch cycle and exit.")
    parser.add_argument("--daemon", action="store_true", help="Run forever, sleeping 30 seconds between cycles.")
    parser.add_argument("--simulate-nameerror", action="store_true", help="Run a safe dry simulation of a NameError-style incident.")
    parser.add_argument("--simulate-unresolved-issue", action="store_true", help="Run a safe dry simulation of an unresolved issue through the AI escalation bridge.")
    parser.add_argument("--simulate-ai-escalation", action="store_true", help="Run a safe dry simulation of the AI escalation bridge without modifying files or restarting services.")
    parser.add_argument("--run-simulation-remediation", action="store_true", help="Run the simulator remediation loop until the simulation passes or escalation is required.")
    parser.add_argument("--dry-run", action="store_true", help="Detect and bundle incidents without applying patches.")
    args = parser.parse_args()
    agent = SentinelAgent(dry_run=bool(args.dry_run), autopatch=bool(_env_bool("AION_SENTINEL_AUTOPATCH", True)))
    if args.simulate_ai_escalation or _env_bool("AION_SENTINEL_AI_SIMULATION", False):
        result = agent._run_simulated_ai_escalation_cycle()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    if args.simulate_unresolved_issue:
        result = agent._run_simulated_ai_escalation_cycle()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    if args.run_simulation_remediation:
        remediation_result = agent._run_simulation_remediation()
        flush_cycle_summary(
            component="sentinel",
            summary={"run_simulation_remediation": remediation_result},
            human_summary="\n".join([
                "AION SENTINEL SUMMARY",
                f"- simulation_remediation_passed: {'yes' if bool(remediation_result.get('passed')) else 'no'}",
                f"- issue_type: {remediation_result.get('issue_type') or 'none'}",
                f"- patched: {remediation_result.get('patched')}",
            ]),
            codex_diagnostic=json.dumps(remediation_result, sort_keys=True, default=str),
        )
        return 0 if bool(remediation_result.get("passed")) else 1
    if args.daemon:
        poll_seconds = int(os.environ.get("AION_SENTINEL_POLL_SECONDS", "30") or 30)
        while True:
            agent._run_one_cycle(simulate_nameerror=args.simulate_nameerror)
            time.sleep(max(1, poll_seconds))
    result = agent._run_one_cycle(simulate_nameerror=args.simulate_nameerror)
    flush_cycle_summary(
        component="sentinel",
        summary=result if isinstance(result, dict) else {"result": result},
        human_summary="\n".join([
            "AION SENTINEL SUMMARY",
            f"- issue_detected: {'yes' if result.get('issue_detected') else 'no'}" if isinstance(result, dict) else "- issue_detected: unknown",
            f"- patch_applied: {result.get('patch_applied')}" if isinstance(result, dict) else "- patch_applied: unknown",
            f"- fresh_error_clean: {result.get('fresh_error_clean')}" if isinstance(result, dict) else "- fresh_error_clean: unknown",
        ]),
        codex_diagnostic=json.dumps(result, sort_keys=True, default=str) if isinstance(result, dict) else str(result),
    )
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


if __name__ == "__main__":
    raise SystemExit(main())
