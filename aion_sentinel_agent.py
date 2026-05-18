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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from runtime.paths import ensure_runtime_layout, runtime_state_file, safe_atomic_json_write


REPO_ROOT = Path("/opt/kalshi-bot").resolve()
RUNTIME_SENTINEL_ROOT = REPO_ROOT / "runtime" / "sentinel"
INCIDENTS_ROOT = RUNTIME_SENTINEL_ROOT / "incidents"
BACKUPS_ROOT = RUNTIME_SENTINEL_ROOT / "backups"
LOGS_ROOT = RUNTIME_SENTINEL_ROOT / "logs"
RECIPES_ROOT = RUNTIME_SENTINEL_ROOT / "recipes"
STATE_FILE = RUNTIME_SENTINEL_ROOT / "sentinel_state.json"
RECIPES_STATE_FILE = RECIPES_ROOT / "learned_recipes.json"

KALSHI_SERVICE = "kalshi-bot.service"
KALSHI_UNIT = "kalshi-bot.service"
GUARDIAN_CMD = [sys.executable, str(REPO_ROOT / "aion_guardian_report.py"), "--daily-integrity-audit"]
PY_COMPILE_CMD = [sys.executable, "-m", "py_compile", str(REPO_ROOT / "kalshi_web.py"), str(REPO_ROOT / "aion_guardian_report.py")]

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
STALE_BLOCKER_RE = re.compile(r"(unsafe_spread|unsafe_price|quote_missing|orderbook_not_usable|stale_blocker|phase55)", re.IGNORECASE)

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
        bool_tokens = ("pass", "ready", "allowed", "enabled", "present", "valid", "executable", "confirmed", "applied", "found", "ok", "exists", "tradeable", "relaxed", "blocked")
        list_tokens = ("_list", "_fields", "_reasons", "_history", "_keys", "_messages", "_hits", "_records", "_frames")
        dict_tokens = ("_state", "_record", "_payload", "_context", "_detail", "_details", "_diagnostic", "_mapping", "_bucket", "_summary")
        numeric_tokens = ("edge", "score", "prob", "confidence", "threshold", "price", "cost", "size", "liquidity", "margin", "count", "age", "volume", "spread")
        string_tokens = ("status", "reason", "blocker", "source", "mode", "decision", "branch", "label", "state")
        if name in {"price_bounds_pass", "quote_ready", "orderbook_ready", "payload_ready", "auth_ready", "final_submit_allowed_by_realm_vv", "selected_side_has_price", "selected_side_has_liquidity", "depth_available", "beast_spread_override_applied", "tradeable"} or any(token in lowered for token in bool_tokens):
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
        telegram_hits = []
        for source_name, blob in (("journal", journal_out), ("guardian", guardian_out), ("guardian_err", guardian_err), ("journal_err", journal_err)):
            if TELEGRAM_ERROR_RE.search(blob or ""):
                telegram_hits.append(source_name)
        return {
            "timestamp": _now_iso(),
            "journal": {"returncode": rc, "stdout": journal_out, "stderr": journal_err},
            "guardian": {"returncode": guardian_rc, "stdout": guardian_out, "stderr": guardian_err},
            "states": state_files,
            "telegram_hits": telegram_hits,
        }

    def detect_issue(self, evidence: Dict[str, Any]) -> Optional[SentinelIssue]:
        journal_text = evidence.get("journal", {}).get("stdout", "") or ""
        guardian_text = evidence.get("guardian", {}).get("stdout", "") or ""
        combined = "\n".join([journal_text, guardian_text, evidence.get("journal", {}).get("stderr", "") or "", evidence.get("guardian", {}).get("stderr", "") or ""])
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
        if stale_phase55 > 0 or compat_alias > 0:
            return SentinelIssue(
                issue_type="legacy_authority",
                severity="medium",
                autopatch_allowed=True,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                evidence={"guardian": guardian_state, "journal_excerpt": journal_text[-3000:], "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="legacy_authority", analysis=analysis), "failure_count": failure_count},
                patch_recipe="realm_vv_authority_guard",
                remaining_blocker="compatibility_alias_only",
            )
        if any(token in guardian_text for token in ("quote_not_usable", "orderbook_not_usable", "unsafe_spread")):
            return SentinelIssue(
                issue_type="stale_blocker_propagation",
                severity="medium",
                autopatch_allowed=True,
                file_path=str(REPO_ROOT / "kalshi_web.py"),
                evidence={"guardian_excerpt": guardian_text[-5000:], "journal_excerpt": journal_text[-3000:], "suggested_recipe_template": self.scope_handler.suggest_recipe_template(issue_type="stale_blocker_propagation", analysis=analysis), "failure_count": failure_count},
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
        return (
            "AION Sentinel\n"
            f"- tags: {', '.join(tags) or 'none'}\n"
            f"- issue_type: {issue.issue_type}\n"
            f"- variable: {issue.variable or 'unknown'}\n"
            f"- function_name: {issue.function_name or 'unknown'}\n"
            f"- recipe: {issue.patch_recipe or 'none'}\n"
            f"- patch_applied: {bool(patch_result.get('applied'))}\n"
            f"- patch_reason: {patch_result.get('reason') or 'none'}\n"
            f"- failure_count: {issue.evidence.get('failure_count') if isinstance(issue.evidence, dict) else 'unknown'}\n"
            f"- compile_ok: {bool(compile_result.get('ok'))}\n"
            f"- restart_ok: {bool(restart_result.get('ok'))}\n"
            f"- fresh_error_clean: {bool(verify_result.get('clean'))}\n"
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

    def _run_one_cycle(self, simulate_nameerror: bool = False) -> Dict[str, Any]:
        _ensure_dirs()
        evidence = self.collect_evidence()
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


def main() -> int:
    parser = argparse.ArgumentParser(description="AION Sentinel Auto-Patch Agent")
    parser.add_argument("--once", action="store_true", help="Run one detection/patch cycle and exit.")
    parser.add_argument("--daemon", action="store_true", help="Run forever, sleeping 30 seconds between cycles.")
    parser.add_argument("--simulate-nameerror", action="store_true", help="Run a safe dry simulation of a NameError-style incident.")
    parser.add_argument("--dry-run", action="store_true", help="Detect and bundle incidents without applying patches.")
    args = parser.parse_args()
    agent = SentinelAgent(dry_run=bool(args.dry_run), autopatch=bool(_env_bool("AION_SENTINEL_AUTOPATCH", True)))
    if args.daemon:
        poll_seconds = int(os.environ.get("AION_SENTINEL_POLL_SECONDS", "30") or 30)
        while True:
            agent._run_one_cycle(simulate_nameerror=args.simulate_nameerror)
            time.sleep(max(1, poll_seconds))
    result = agent._run_one_cycle(simulate_nameerror=args.simulate_nameerror)
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
