from __future__ import annotations

import builtins
import json
import os
from pathlib import Path


RUNTIME_ROOT = Path(os.environ.get("AION_RUNTIME_ROOT", "/opt/kalshi-bot/runtime")).resolve()
RUNTIME_MEMORY_DIR = RUNTIME_ROOT / "memory"
RUNTIME_CACHE_DIR = RUNTIME_ROOT / "cache"
RUNTIME_STATE_DIR = RUNTIME_ROOT / "state"
RUNTIME_TMP_DIR = RUNTIME_ROOT / "tmp"


def ensure_runtime_layout():
    for path in (RUNTIME_ROOT, RUNTIME_MEMORY_DIR, RUNTIME_CACHE_DIR, RUNTIME_STATE_DIR, RUNTIME_TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "root": str(RUNTIME_ROOT),
        "memory": str(RUNTIME_MEMORY_DIR),
        "cache": str(RUNTIME_CACHE_DIR),
        "state": str(RUNTIME_STATE_DIR),
        "tmp": str(RUNTIME_TMP_DIR),
    }


def runtime_path(*parts):
    return str(RUNTIME_ROOT.joinpath(*parts))


def runtime_memory_file(name):
    return str(RUNTIME_MEMORY_DIR.joinpath(name))


def runtime_cache_file(name):
    return str(RUNTIME_CACHE_DIR.joinpath(name))


def runtime_state_file(name):
    return str(RUNTIME_STATE_DIR.joinpath(name))


def runtime_tmp_file(name):
    return str(RUNTIME_TMP_DIR.joinpath(name))


def legacy_runtime_artifact_paths():
    return [
        "/opt/kalshi-bot/.trade_memory.json",
        "/opt/kalshi-bot/.trade_memory",
        "/opt/kalshi-bot/.long_term_intelligence_memory.json",
        "/opt/kalshi-bot/.long_term_intelligence_memory",
        "/opt/kalshi-bot/trade_memory.json",
        "/opt/kalshi-bot/trade_outcome_memory.json",
        "/opt/kalshi-bot/long_term_intelligence_memory.json",
    ]


def log_module_load(module_name, **fields):
    payload = {"module": module_name}
    payload.update({key: value for key, value in fields.items() if value is not None})
    builtins.print("[AION-MODULE-LOAD] " + json.dumps(payload, sort_keys=True, default=str))


ensure_runtime_layout()
log_module_load(
    "runtime.paths",
    root=str(RUNTIME_ROOT),
    memory=str(RUNTIME_MEMORY_DIR),
    cache=str(RUNTIME_CACHE_DIR),
    state=str(RUNTIME_STATE_DIR),
    tmp=str(RUNTIME_TMP_DIR),
)

