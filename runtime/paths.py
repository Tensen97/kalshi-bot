from __future__ import annotations

import builtins
import json
import os
import shutil
import tempfile
import threading
import time
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


def runtime_env_path(env_name, fallback_path):
    candidate = str(os.environ.get(env_name, "") or "").strip()
    if candidate:
        try:
            resolved = Path(candidate).expanduser().resolve()
            if resolved == RUNTIME_ROOT or RUNTIME_ROOT in resolved.parents:
                return str(resolved)
        except Exception:
            pass
    return str(fallback_path)


def _ensure_parent_dir(path):
    directory = Path(path).expanduser().resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_atomic_json_write(path, payload, *, indent=2, sort_keys=True, default=str, encoding="utf-8"):
    path = str(path)
    try:
        directory = _ensure_parent_dir(path)
        fd, tmp_path = tempfile.mkstemp(prefix=f".{Path(path).name}.", dir=str(directory))
        try:
            with os.fdopen(fd, "w", encoding=encoding) as handle:
                json.dump(payload, handle, indent=indent, sort_keys=sort_keys, default=default)
            os.replace(tmp_path, path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
        return True
    except Exception:
        return False


_PERSIST_LOCK = threading.RLock()
_PERSIST_STATE = {
    "last_success_emit": {},
    "last_error_emit": {},
    "suppressed_success": {},
    "suppressed_errors": {},
    "last_runtime_paths_emit": 0.0,
}


def _persist_key(subsystem, action, path):
    return f"{str(subsystem or 'unknown').strip()}|{str(action or 'persist_state').strip()}|{str(path or 'unknown').strip()}"


def _emit_persist(tag, payload):
    builtins.print(f"[{tag}] " + json.dumps(payload, sort_keys=True, default=str))


def record_persist_event(*, subsystem, path, action="persist_state", success=True, reason=None, cooldown_seconds=300.0, summary_threshold=5, details=None):
    key = _persist_key(subsystem, action, path)
    now = time.time()
    payload = {
        "subsystem": subsystem,
        "path": path,
        "action": action,
        "success": bool(success),
        "reason": reason,
    }
    if isinstance(details, dict):
        payload.update({str(k): v for k, v in details.items() if v is not None})
    with _PERSIST_LOCK:
        state_key = "last_success_emit" if success else "last_error_emit"
        suppressed_key = "suppressed_success" if success else "suppressed_errors"
        last_emit = float(_PERSIST_STATE[state_key].get(key, 0.0) or 0.0)
        if (now - last_emit) >= float(cooldown_seconds or 0.0):
            _PERSIST_STATE[state_key][key] = now
            suppressed = int(_PERSIST_STATE[suppressed_key].get(key, 0) or 0)
            if suppressed:
                payload["suppressed_count"] = suppressed
                _PERSIST_STATE[suppressed_key][key] = 0
            _emit_persist("AION-PERSIST-STATE" if success else "AION-PERSIST-ERROR", payload)
            return payload
        _PERSIST_STATE[suppressed_key][key] = int(_PERSIST_STATE[suppressed_key].get(key, 0) or 0) + 1
        if not success and _PERSIST_STATE[suppressed_key][key] >= max(1, int(summary_threshold or 5)):
            summary_payload = dict(payload)
            summary_payload["suppressed_count"] = _PERSIST_STATE[suppressed_key][key]
            summary_payload["suppression_mode"] = True
            _emit_persist("AION-PERSIST-ERROR", summary_payload)
            _PERSIST_STATE[suppressed_key][key] = 0
            _PERSIST_STATE[state_key][key] = now
            return summary_payload
    return payload


def verify_runtime_paths(*, extra_paths=None):
    ensure_runtime_layout()
    disk_usage_percent = None
    try:
        usage = shutil.disk_usage(str(RUNTIME_ROOT))
        disk_usage_percent = round((usage.used / float(usage.total or 1)) * 100.0, 2)
    except Exception:
        pass
    writable = {}
    verified_paths = []
    for directory in (RUNTIME_ROOT, RUNTIME_MEMORY_DIR, RUNTIME_CACHE_DIR, RUNTIME_STATE_DIR, RUNTIME_TMP_DIR):
        ok = False
        try:
            directory.mkdir(parents=True, exist_ok=True)
            test_file = directory / f".write_test_{os.getpid()}_{int(time.time() * 1000)}"
            with open(test_file, "w", encoding="utf-8") as handle:
                handle.write("ok")
            try:
                test_file.unlink()
            except Exception:
                pass
            ok = True
        except Exception:
            ok = False
        writable[str(directory)] = ok
        verified_paths.append(str(directory))
    for path in extra_paths or []:
        if not path:
            continue
        try:
            candidate = Path(path).expanduser().resolve()
            target_dir = candidate if candidate.suffix == "" else candidate.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            writable[str(target_dir)] = True
            verified_paths.append(str(target_dir))
        except Exception:
            writable[str(path)] = False
    payload = {
        "runtime_root": str(RUNTIME_ROOT),
        "memory_dir": str(RUNTIME_MEMORY_DIR),
        "cache_dir": str(RUNTIME_CACHE_DIR),
        "state_dir": str(RUNTIME_STATE_DIR),
        "tmp_dir": str(RUNTIME_TMP_DIR),
        "disk_usage_percent": disk_usage_percent,
        "writable": writable,
        "verified_paths": verified_paths,
    }
    now = time.time()
    with _PERSIST_LOCK:
        if (now - float(_PERSIST_STATE["last_runtime_paths_emit"] or 0.0)) >= 300.0:
            _PERSIST_STATE["last_runtime_paths_emit"] = now
            _emit_persist("AION-RUNTIME-PATHS", payload)
    return payload


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
