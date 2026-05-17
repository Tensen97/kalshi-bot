import argparse
import json
import os
import threading
import time
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_env_path, runtime_memory_file, runtime_state_file, safe_atomic_json_write, verify_runtime_paths
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from distributed_intelligence import DistributedIntelligenceCouncil
except Exception:
    DistributedIntelligenceCouncil = None

try:
    from telemetry.health_snapshot import compute_global_health_snapshot
    from telemetry.seraph_hydration import build_hydrated_seraph_state
    from telemetry.state_registry import get_live_state_registry
    from telemetry.telemetry_exporter import export_live_telemetry_snapshot
except Exception:
    compute_global_health_snapshot = None
    build_hydrated_seraph_state = None
    get_live_state_registry = None
    export_live_telemetry_snapshot = None

try:
    from command_center.seraph_bridge import SeraphCommandCenterBridge
except Exception:
    SeraphCommandCenterBridge = None

try:
    from consciousness.narrative_engine import NarrativeEngine
except Exception:
    NarrativeEngine = None


REALM_XIX_ENABLE = (os.environ.get("REALM_XIX_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XIX_API_ENABLE = (os.environ.get("REALM_XIX_API_ENABLE", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
REALM_XIX_UI_ENABLE = (os.environ.get("REALM_XIX_UI_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XIX_ALERT_HISTORY_LIMIT = int(float(os.environ.get("REALM_XIX_ALERT_HISTORY_LIMIT", "100") or 100))
REALM_XIX_TELEMETRY_INTERVAL_SECONDS = float(os.environ.get("REALM_XIX_TELEMETRY_INTERVAL_SECONDS", "15") or 15)
REALM_XIX_API_HOST = os.environ.get("REALM_XIX_API_HOST", "0.0.0.0")
REALM_XIX_API_PORT = int(float(os.environ.get("REALM_XIX_API_PORT", "3002") or 3002))
REALM_XIX_STATE_FILE = runtime_env_path("REALM_XIX_STATE_FILE", runtime_state_file("seraph_interface_state.json"))
REALM_XXVIII_ENABLE = (os.environ.get("REALM_XXVIII_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXVII_ENABLE = (os.environ.get("REALM_XXVII_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXVII_STATE_FILE = runtime_env_path("REALM_XXVII_STATE_FILE", runtime_state_file("realm_xxvii_command_center.json"))
REALM_XXVII_SUMMARY_INTERVAL_SECONDS = float(os.environ.get("REALM_XXVII_SUMMARY_INTERVAL_SECONDS", "60") or 60)
REALM_XXX_ENABLE = (os.environ.get("REALM_XXX_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXIX_ENABLE = (os.environ.get("REALM_XXIX_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXIX_STATE_FILE = runtime_env_path("REALM_XXIX_STATE_FILE", runtime_state_file("realm_xxix_live_state.json"))
REALM_XXIX_HISTORY_LIMIT = int(float(os.environ.get("REALM_XXIX_HISTORY_LIMIT", "120") or 120))


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _round(value, digits=4, default=None):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            return str(value)
    if hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            return _json_safe(vars(value))
        except Exception:
            return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _stable_json(payload):
    return json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"))


def _compact_list(values, limit=5):
    values = list(values or [])
    return values[: max(0, int(limit))]


def _family_names(values, limit=3):
    if isinstance(values, dict):
        iterable = values.values()
    elif isinstance(values, (list, tuple, set, deque)):
        iterable = values
    elif values is None:
        iterable = []
    else:
        iterable = [values]
    names = []
    max_items = max(0, int(limit))
    for item in iterable:
        if isinstance(item, dict):
            name = item.get("family") or item.get("name") or item.get("ticker") or item.get("id")
        else:
            name = item
        if name is None:
            continue
        text = str(name).strip()
        if text:
            names.append(text)
        if len(names) >= max_items:
            break
    return names


@dataclass
class EcosystemState:
    ecosystem_name: str = "AION"
    interface_entity: str = "SERAPH"
    realm_name: str = "Realm XIX - Interface Layer"
    generated_at: str = field(default_factory=_now_iso)
    uptime_seconds: float = 0.0
    active_realms: list = field(default_factory=list)
    portfolio: dict = field(default_factory=dict)
    market: dict = field(default_factory=dict)
    strategies: dict = field(default_factory=dict)
    simulation: dict = field(default_factory=dict)
    guardian: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    health: dict = field(default_factory=dict)
    identity: dict = field(default_factory=dict)
    telemetry: dict = field(default_factory=dict)
    websocket: dict = field(default_factory=dict)
    replay_results: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    external: dict = field(default_factory=dict)
    evolution: dict = field(default_factory=dict)
    command_center: dict = field(default_factory=dict)
    consciousness: dict = field(default_factory=dict)
    operational_summary: str = ""

    def to_dict(self):
        return {
            "ecosystem_name": self.ecosystem_name,
            "interface_entity": self.interface_entity,
            "realm_name": self.realm_name,
            "generated_at": self.generated_at,
            "uptime_seconds": _round(self.uptime_seconds, 4, 0.0),
            "active_realms": list(self.active_realms or []),
            "portfolio": _json_safe(self.portfolio or {}),
            "market": _json_safe(self.market or {}),
            "strategies": _json_safe(self.strategies or {}),
            "simulation": _json_safe(self.simulation or {}),
            "guardian": _json_safe(self.guardian or {}),
            "alerts": _json_safe(self.alerts or []),
            "health": _json_safe(self.health or {}),
            "identity": _json_safe(self.identity or {}),
            "telemetry": _json_safe(self.telemetry or {}),
            "websocket": _json_safe(self.websocket or {}),
            "replay_results": _json_safe(self.replay_results or {}),
            "risk": _json_safe(self.risk or {}),
            "external": _json_safe(self.external or {}),
            "evolution": _json_safe(self.evolution or {}),
            "command_center": _json_safe(self.command_center or {}),
            "consciousness": _json_safe(self.consciousness or {}),
            "operational_summary": self.operational_summary,
        }


SERAPH_UI_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SERAPH Interface</title>
  <style>
    body { font-family: monospace; background: #07111f; color: #dbe7ff; margin: 0; padding: 20px; }
    .card { border: 1px solid #27405f; border-radius: 10px; padding: 16px; margin-bottom: 12px; background: rgba(10,18,32,0.82); }
    .muted { color: #8ea6c8; }
    pre { white-space: pre-wrap; word-break: break-word; }
  </style>
</head>
<body>
  <div class="card">
    <div class="muted">SERAPH</div>
    <h1>Realm XIX Interface Layer</h1>
    <p id="status">Loading state...</p>
  </div>
  <div class="card"><h2>State</h2><pre id="state"></pre></div>
  <script>
    async function refresh() {
      try {
        const response = await fetch('/seraph/state');
        const payload = await response.json();
        document.getElementById('status').textContent = payload.health?.summary || 'ok';
        document.getElementById('state').textContent = JSON.stringify(payload, null, 2);
      } catch (error) {
        document.getElementById('status').textContent = 'Unavailable';
      }
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""


SERAPH_UI_TEMPLATE_XVIII = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="dark" />
  <title>SERAPH Live Intelligence UI</title>
  <style>
    :root {
      --bg-0: #040814;
      --bg-1: #07111f;
      --bg-2: #0c1629;
      --panel: rgba(10, 18, 34, 0.84);
      --panel-strong: rgba(13, 24, 44, 0.94);
      --border: rgba(136, 170, 255, 0.18);
      --text: #e7f0ff;
      --muted: #98abc8;
      --gold: #f7d37a;
      --violet: #9f8cff;
      --blue: #61c5ff;
      --green: #6cffb2;
      --amber: #ffc766;
      --red: #ff7685;
      --shadow: 0 28px 80px rgba(0, 0, 0, 0.45);
      --radius: 18px;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(100, 80, 255, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(50, 180, 255, 0.14), transparent 24%),
        radial-gradient(circle at bottom left, rgba(255, 190, 90, 0.08), transparent 22%),
        linear-gradient(180deg, var(--bg-0), var(--bg-1) 42%, var(--bg-2));
      overflow-x: hidden;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
      background-size: 64px 64px;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.28), transparent 80%);
      opacity: 0.28;
    }
    .app {
      position: relative;
      max-width: 1600px;
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .brand, .status-rail, .panel, .summary-shell {
      background: linear-gradient(180deg, rgba(17, 29, 52, 0.92), rgba(8, 15, 27, 0.86));
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      border-radius: var(--radius);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }
    .brand {
      padding: 22px 24px;
      position: relative;
      overflow: hidden;
    }
    .brand::after {
      content: "";
      position: absolute;
      inset: auto -10% -40% auto;
      width: 220px;
      height: 220px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(159, 140, 255, 0.24), transparent 68%);
      filter: blur(8px);
      pointer-events: none;
    }
    .eyebrow {
      text-transform: uppercase;
      letter-spacing: 0.28em;
      color: var(--gold);
      font-size: 11px;
      margin-bottom: 10px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 {
      font-size: clamp(30px, 4vw, 56px);
      line-height: 0.98;
      margin-bottom: 12px;
    }
    .lede {
      max-width: 72ch;
      color: #d7e4ff;
      line-height: 1.6;
      font-size: 15px;
    }
    .chip-row, .action-row, .metrics, .mini-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .chip, .pill, .metric {
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(255,255,255,0.03);
      color: var(--text);
      font-size: 12px;
      line-height: 1;
    }
    .chip strong, .metric strong, .pill strong { color: var(--gold); font-weight: 700; }
    .status-rail {
      padding: 18px;
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .status-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
    }
    .status-light {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 18px rgba(108, 255, 178, 0.8);
    }
    .dot.warn { background: var(--amber); box-shadow: 0 0 18px rgba(255, 199, 102, 0.8); }
    .dot.bad { background: var(--red); box-shadow: 0 0 18px rgba(255, 118, 133, 0.8); }
    .rail-metric {
      display: grid;
      gap: 4px;
      padding: 14px 15px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
      border: 1px solid rgba(255,255,255,0.08);
    }
    .rail-metric .label { color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }
    .rail-metric .value { font-size: 18px; font-weight: 700; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
      justify-content: space-between;
      align-items: center;
    }
    .toolbar-left, .toolbar-right {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .btn {
      appearance: none;
      border: 1px solid rgba(255,255,255,0.14);
      background: linear-gradient(180deg, rgba(98, 197, 255, 0.12), rgba(98, 197, 255, 0.04));
      color: var(--text);
      border-radius: 999px;
      padding: 10px 14px;
      font-size: 13px;
      cursor: pointer;
      transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease;
    }
    .btn:hover { transform: translateY(-1px); border-color: rgba(97, 197, 255, 0.42); }
    .btn.ghost { background: rgba(255,255,255,0.03); }
    .btn.gold { background: linear-gradient(180deg, rgba(247, 211, 122, 0.18), rgba(247, 211, 122, 0.05)); }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .summary-shell { grid-column: span 12; padding: 18px 18px 20px; }
    .panel {
      padding: 16px 16px 14px;
      min-height: 180px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      grid-column: span 4;
      overflow: hidden;
    }
    .panel.wide { grid-column: span 8; }
    .panel.tall { grid-column: span 6; }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }
    .panel-head h3 {
      font-size: 16px;
      margin-bottom: 4px;
    }
    .panel-head .sub {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .status-pill {
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      border: 1px solid rgba(255,255,255,0.12);
      white-space: nowrap;
    }
    .status-pill.ok { color: var(--green); background: rgba(108,255,178,0.08); }
    .status-pill.warn { color: var(--amber); background: rgba(255,199,102,0.08); }
    .status-pill.bad { color: var(--red); background: rgba(255,118,133,0.08); }
    .status-pill.info { color: var(--blue); background: rgba(97,197,255,0.08); }
    .status-pill.muted { color: var(--muted); background: rgba(255,255,255,0.04); }
    .content {
      display: grid;
      gap: 10px;
      color: #d7e4ff;
      font-size: 13px;
      line-height: 1.55;
      min-height: 0;
    }
    .kv {
      display: grid;
      gap: 8px;
    }
    .kv-row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.06);
    }
    .kv-row span:first-child { color: var(--muted); }
    .kv-row span:last-child { color: var(--text); text-align: right; }
    .list {
      margin: 0;
      padding-left: 18px;
      color: #dce9ff;
    }
    .list li { margin-bottom: 6px; }
    .summary {
      font-size: 15px;
      line-height: 1.75;
      color: #edf3ff;
      white-space: pre-wrap;
    }
    .summary-callout {
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
      color: #dfe9ff;
      line-height: 1.6;
    }
    .footer-note {
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    details.panel {
      padding: 0;
    }
    details.panel > summary {
      list-style: none;
      cursor: pointer;
      padding: 16px 16px 12px;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    details.panel > summary::-webkit-details-marker { display: none; }
    details.panel[open] > summary {
      background: linear-gradient(180deg, rgba(255,255,255,0.04), transparent);
    }
    details.panel > .content {
      padding: 0 16px 16px;
    }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    .muted { color: var(--muted); }
    .loading {
      display: inline-flex;
      align-items: center;
      gap: 10px;
    }
    .spinner {
      width: 12px;
      height: 12px;
      border: 2px solid rgba(255,255,255,0.15);
      border-top-color: var(--blue);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (max-width: 1100px) {
      .hero { grid-template-columns: 1fr; }
      .panel.wide, .panel.tall, .panel { grid-column: span 12; }
    }
    @media (max-width: 720px) {
      .app { padding: 12px; }
      h1 { font-size: 28px; }
      .toolbar, .status-title, .panel-head { align-items: stretch; flex-direction: column; }
      .toolbar-left, .toolbar-right { width: 100%; }
      .btn { width: 100%; justify-content: center; }
      .kv-row { flex-direction: column; }
      .kv-row span:last-child { text-align: left; }
    }
  </style>
</head>
<body>
  <div class="app">
    <section class="hero">
      <div class="brand">
        <div class="eyebrow">SERAPH / AION Live Intelligence</div>
        <h1>Command Center</h1>
        <p class="lede" id="heroSummary">Waiting for AION data...</p>
        <div class="chip-row" id="heroChips">
          <span class="chip"><strong>UI</strong> live intelligence display</span>
          <span class="chip"><strong>Mode</strong> read-only</span>
          <span class="chip"><strong>Feed</strong> polling</span>
        </div>
      </div>
      <div class="status-rail">
        <div class="status-title">
          <div>
            <div class="eyebrow" style="margin-bottom:6px;">AION Status</div>
            <div class="status-light" id="overallState"><span class="dot warn"></span><span>waiting for AION data</span></div>
          </div>
          <span class="pill mono" id="lastUpdated">last update: --</span>
        </div>
        <div class="rail-metric"><div class="label">Global Health</div><div class="value" id="globalHealthValue">--</div></div>
        <div class="rail-metric"><div class="label">Execution Posture</div><div class="value" id="executionPostureValue">--</div></div>
        <div class="rail-metric"><div class="label">WebSocket</div><div class="value" id="websocketValue">--</div></div>
      </div>
    </section>

    <div class="toolbar">
      <div class="toolbar-left">
        <button class="btn gold" id="refreshBtn">Refresh</button>
        <button class="btn ghost" id="showAllBtn">Show Details</button>
        <button class="btn ghost" id="hideAllBtn">Collapse Panels</button>
        <button class="btn ghost" id="copyBtn">Copy Status Summary</button>
      </div>
      <div class="toolbar-right">
        <span class="pill mono" id="feedState">polling every 7s</span>
        <span class="pill mono" id="backendState">backend unknown</span>
      </div>
    </div>

    <section class="summary-shell">
      <div class="panel-head">
        <div>
          <h3>SERAPH Narrative Summary</h3>
          <div class="sub">Human-readable overview of the current AION state.</div>
        </div>
        <span class="status-pill muted" id="summaryState">waiting</span>
      </div>
      <div class="summary mono" id="narrativeSummary">Waiting for AION data...</div>
    </section>

    <section class="grid" id="panels"></section>

    <div class="footer-note">
      <span>Read-only command center. No trading controls are exposed.</span>
      <span class="mono">SERAPH / Realm XXVIII</span>
    </div>
  </div>

  <script>
    const ENDPOINTS = {
      aionStatus: '/api/aion/status',
      commandCenter: '/seraph/command-center',
      ecosystem: '/seraph/ecosystem',
      summary: '/seraph/summary',
      health: '/seraph/health',
      live: '/seraph/live',
      recommendations: '/seraph/recommendations',
      risk: '/seraph/risk',
      external: '/seraph/external',
      personality: '/seraph/personality',
      probability: '/seraph/probability',
    };

    const PANEL_ORDER = [
      'aion',
      'websocket',
      'risk',
      'personality',
      'probability',
      'external',
      'evolution',
      'command',
    ];

    const PANEL_DEFS = {
      aion: { title: 'AION Live Status', subtitle: 'online state, health, and current posture', span: 'wide' },
      websocket: { title: 'WebSocket Nervous System', subtitle: 'stream health, subscriptions, staleness, and latency', span: '' },
      risk: { title: 'Risk / Capital Intelligence', subtitle: 'risk posture, aggression, drawdown, exposure', span: '' },
      personality: { title: 'Personality Intelligence', subtitle: 'market behaviors and regime context', span: 'tall' },
      probability: { title: 'Probability / Edge', subtitle: 'fair value, conviction, mispricing state', span: 'tall' },
      external: { title: 'External Awareness', subtitle: 'catalysts, macro, sports, and event risk', span: '' },
      evolution: { title: 'Evolution / Recommendations', subtitle: 'ecosystem health, recommendations, and patterns', span: 'wide' },
      command: { title: 'Command Center', subtitle: 'command-state aggregate and suggested actions', span: '' },
    };

    const state = {
      loading: true,
      backendOnline: false,
      lastData: {},
      detailsOpen: true,
      refreshTimer: null,
    };

    function log(level, message, details) {
      const prefix = `[AION-FRONTEND-${level}]`;
      if (details !== undefined) {
        console[level === 'ERROR' ? 'error' : level === 'OFFLINE' ? 'warn' : 'log'](prefix, message, details);
      } else {
        console[level === 'ERROR' ? 'error' : level === 'OFFLINE' ? 'warn' : 'log'](prefix, message);
      }
    }

    function getAionStatus(data) {
      return data.aionStatus?.data || data.aionStatus || {};
    }

    function escapeText(value) {
      return String(value ?? '—');
    }

    function toArray(value) {
      if (Array.isArray(value)) return value;
      if (value && typeof value === 'object') return Object.values(value);
      return [];
    }

    function formatNumber(value, digits = 2, fallback = '—') {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(digits) : fallback;
    }

    function getSeverityClass(label) {
      const text = String(label || '').toLowerCase();
      if (['critical', 'risk_off', 'offline', 'bad', 'danger', 'degraded', 'unstable'].some((item) => text.includes(item))) return 'bad';
      if (['warning', 'caution', 'defensive', 'watch', 'stale'].some((item) => text.includes(item))) return 'warn';
      if (['healthy', 'normal', 'optimal', 'ok', 'stable', 'online', 'good', 'active', 'live', 'connected'].some((item) => text.includes(item))) return 'ok';
      return 'info';
    }

    function makeCard({ key, title, subtitle, span }, bodyHtml, pillText, pillClass) {
      return `
        <details class="panel ${span || ''}" data-panel="${key}" open>
          <summary>
            <div>
              <div class="panel-head">
                <div>
                  <h3>${title}</h3>
                  <div class="sub">${subtitle}</div>
                </div>
              </div>
            </div>
            <span class="status-pill ${pillClass || 'muted'}">${escapeText(pillText || 'waiting')}</span>
          </summary>
          <div class="content">
            ${bodyHtml}
          </div>
        </details>
      `;
    }

    function kvRows(rows) {
      if (!rows || !rows.length) {
        return `<div class="muted">waiting for AION data</div>`;
      }
      return `<div class="kv">${rows.map(([label, value]) => `<div class="kv-row"><span>${label}</span><span>${value}</span></div>`).join('')}</div>`;
    }

    function listItems(items, emptyText = 'waiting for AION data') {
      if (!items || !items.length) return `<div class="muted">${emptyText}</div>`;
      return `<ul class="list">${items.map((item) => `<li>${item}</li>`).join('')}</ul>`;
    }

    function formatBullet(value) {
      if (value === null || value === undefined || value === '') return null;
      if (typeof value === 'string') return value;
      if (typeof value === 'number') return String(value);
      if (typeof value === 'boolean') return value ? 'yes' : 'no';
      return JSON.stringify(value);
    }

    function summarizeTop(entries, key = 'family', metric = 'score', limit = 3) {
      return toArray(entries).slice(0, limit).map((item) => {
        if (!item || typeof item !== 'object') return null;
        const name = item[key] || item.name || item.ticker || item.id || 'unknown';
        const score = item[metric] ?? item.health_score ?? item.conviction ?? item.edge_score ?? item.recommendation_score;
        const extra = item.status || item.regime || item.risk_level || item.mode || '';
        return `${name}${score !== undefined ? ` (${formatNumber(score, 3)})` : ''}${extra ? ` - ${extra}` : ''}`;
      }).filter(Boolean);
    }

    function buildNarrative(data) {
      const aionStatus = getAionStatus(data);
      const ecosystem = data.ecosystem?.data || data.ecosystem || {};
      const telemetry = aionStatus.telemetry || ecosystem.telemetry || data.live?.data?.telemetry || data.commandCenter?.data?.telemetry || {};
      const consciousness = aionStatus.consciousness || ecosystem.consciousness || data.summary?.data?.consciousness || data.commandCenter?.data?.consciousness || data.health?.data?.consciousness || {};
      const operationalSummary = consciousness.summary || aionStatus.operational_summary || ecosystem.operational_summary || data.summary?.data?.operational_summary || '';
      if (operationalSummary) {
        return operationalSummary;
      }
      const health = aionStatus.health || aionStatus.global_health || data.health?.data?.health || data.health?.data || {};
      const live = aionStatus.status ? aionStatus : data.live?.data || {};
      const risk = data.risk?.data?.last_global_state || data.risk?.data || {};
      const external = data.external?.data || {};
      const recommendations = data.recommendations?.data?.recommendations || [];
      const commandCenter = aionStatus.command_center || data.commandCenter?.data || data.commandCenter || {};
      const sentences = [];
      const online = health.summary || ecosystem.health?.summary || telemetry.status || aionStatus.status || 'unknown';
      sentences.push(`AION is ${aionStatus.status === 'live' || telemetry.status === 'live' ? 'live' : online}.`);
      if (telemetry.last_updated || aionStatus.timestamp) {
        sentences.push(`Telemetry last updated at ${telemetry.last_updated || aionStatus.timestamp}.`);
      }
      if (live.execution_posture) {
        const posture = typeof live.execution_posture === 'string' ? live.execution_posture : live.execution_posture?.posture || live.execution_posture?.mode || 'unknown';
        sentences.push(`Execution posture is ${posture}.`);
      }
      if (risk.capital_state || risk.aggression_level !== undefined) {
        const capitalState = risk.capital_state || 'unknown';
        const aggression = risk.aggression_level !== undefined ? formatNumber(risk.aggression_level, 2) : 'unknown';
        sentences.push(`Capital state is ${capitalState} with aggression ${aggression}.`);
      }
      if (external.event_risk?.risk_level || external.external_awareness_state) {
        const riskLevel = external.event_risk?.risk_level || 'NONE';
        const extState = external.external_awareness_state || 'unknown';
        sentences.push(`External context is ${extState} with event risk ${riskLevel}.`);
      }
      if (recommendations.length) {
        const top = recommendations[0];
        const label = top.title || top.name || top.summary || 'priority recommendation';
        sentences.push(`Current recommendation focus: ${label}.`);
      }
      if (commandCenter.global_health?.global_health_state) {
        sentences.push(`Command center reads ${commandCenter.global_health.global_health_state.toLowerCase()}.`);
      }
      return sentences.join(' ');
    }

    function renderAion(data) {
      const aionStatus = getAionStatus(data);
      const ecosystem = data.ecosystem?.data || data.ecosystem || {};
      const telemetry = aionStatus.telemetry || ecosystem.telemetry || data.live?.data?.telemetry || data.commandCenter?.data?.telemetry || {};
      const health = aionStatus.health || aionStatus.global_health || data.health?.data?.health || data.health?.data || {};
      const live = aionStatus.status ? aionStatus : data.live?.data || {};
      const commandCenter = aionStatus.command_center || data.commandCenter?.data || data.commandCenter || {};
      const consciousness = aionStatus.consciousness || ecosystem.consciousness || data.summary?.data?.consciousness || commandCenter.consciousness || {};
      const operationalSummary = consciousness.summary || aionStatus.operational_summary || ecosystem.operational_summary || data.summary?.data?.operational_summary || '';
      const stateRows = [
        ['Online', telemetry.status === 'live' || aionStatus.status === 'live' ? 'AION live' : (health.summary || ecosystem.health?.summary || telemetry.status || aionStatus.status || 'waiting')],
        ['Live mode', live.live_mode === true ? 'yes' : live.live_mode === false ? 'no' : (aionStatus.live_mode === true ? 'yes' : aionStatus.live_mode === false ? 'no' : (telemetry.live_mode?.active === true ? 'yes' : telemetry.live_mode?.active === false ? 'no' : 'waiting'))],
        ['Health', health.portfolio_health_score !== undefined ? formatNumber(health.portfolio_health_score, 3) : (telemetry.health?.portfolio_health_score !== undefined ? formatNumber(telemetry.health.portfolio_health_score, 3) : (ecosystem.health?.portfolio_health_score !== undefined ? formatNumber(ecosystem.health.portfolio_health_score, 3) : 'waiting'))],
        ['Timestamp', telemetry.last_updated || aionStatus.timestamp || ecosystem.generated_at || live.generated_at || health.generated_at || 'waiting'],
      ];
      const chips = [
        ['AION', telemetry.status === 'live' || aionStatus.status === 'live' ? 'live' : (health.summary || ecosystem.health?.summary || 'waiting')],
        ['Mode', live.live_mode === true ? 'live' : (aionStatus.live_mode === true ? 'live' : (telemetry.live_mode?.active ? 'live' : 'standby'))],
        ['Realm XXVIII', state.backendOnline ? 'connected' : 'waiting'],
        ['Telemetry', telemetry.status || aionStatus.status || 'waiting'],
      ];
      return [
        kvRows(stateRows),
        `<div class="metrics">${chips.map(([k, v]) => `<span class="metric"><strong>${k}</strong> ${v}</span>`).join('')}</div>`,
        `<div class="mini-grid">${kvRows([
          ['Command center health', commandCenter.global_health?.global_health_state || aionStatus.global_health_state || telemetry.health?.global_health_state || 'waiting'],
          ['Execution posture', live.execution_posture?.posture || live.execution_posture?.mode || aionStatus.execution_posture?.posture || aionStatus.execution_posture?.mode || telemetry.execution_posture?.posture || telemetry.execution_posture?.mode || 'waiting'],
          ['Active realms', (ecosystem.active_realms || aionStatus.telemetry?.active_realms || telemetry.active_realms || []).join(', ') || 'waiting'],
        ])}</div>`,
        operationalSummary ? `<div class="summary-callout mono">${operationalSummary}</div>` : '',
      ].join('');
    }

    function renderWebSocket(data) {
      const aionStatus = getAionStatus(data);
      const ecosystem = data.ecosystem?.data || data.ecosystem || {};
      const telemetry = aionStatus.telemetry || ecosystem.telemetry || data.live?.data?.telemetry || {};
      const websocket = aionStatus.websocket || ecosystem.websocket || telemetry.websocket_status || data.live?.data?.websocket || {};
      const rows = [
        ['Connection', websocket.connection_state || websocket.connection || websocket.status || aionStatus.websocket_state || telemetry.websocket_status?.connection_state || 'waiting'],
        ['Subscriptions', websocket.subscription_count ?? websocket.active_subscriptions?.length ?? telemetry.websocket_subscription_count ?? 'waiting'],
        ['Stale feed', websocket.stale_feed_seconds !== undefined ? `${formatNumber(websocket.stale_feed_seconds, 1)}s` : (websocket.stale_feed ? 'yes' : (telemetry.websocket_stale_feed ? 'yes' : 'waiting'))],
        ['Latency', websocket.latency_ms !== undefined ? `${formatNumber(websocket.latency_ms, 1)}ms` : (telemetry.websocket_latency !== undefined ? `${formatNumber(telemetry.websocket_latency, 1)}ms` : 'waiting')],
      ];
      const warnings = toArray(websocket.warnings || websocket.alerts || websocket.stale_markets).map((item) => formatBullet(item)).filter(Boolean);
      return kvRows(rows) + listItems(warnings, 'waiting for AION data');
    }

    function renderRisk(data) {
      const risk = data.risk?.data?.last_global_state || data.risk?.data || {};
      const portfolio = data.ecosystem?.data?.portfolio || {};
      const telemetry = data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const telemetryRisk = telemetry.latest_risk_state || {};
      const rows = [
        ['Risk state', risk.capital_state || telemetryRisk.capital_state || portfolio.mode || 'waiting'],
        ['Aggression', risk.aggression_level !== undefined ? formatNumber(risk.aggression_level, 2) : (telemetryRisk.aggression_level !== undefined ? formatNumber(telemetryRisk.aggression_level, 2) : 'waiting')],
        ['Drawdown', risk.drawdown_pct !== undefined ? `${formatNumber(risk.drawdown_pct * 100, 2)}%` : (telemetryRisk.drawdown_pct !== undefined ? `${formatNumber(telemetryRisk.drawdown_pct * 100, 2)}%` : 'waiting')],
        ['Exposure', portfolio.total_exposure_dollars !== undefined ? `$${formatNumber(portfolio.total_exposure_dollars, 2)}` : (telemetryRisk.total_exposure_dollars !== undefined ? `$${formatNumber(telemetryRisk.total_exposure_dollars, 2)}` : 'waiting')],
      ];
      const warnings = toArray(portfolio.warnings || risk.warnings || telemetryRisk.warnings || []).map((item) => formatBullet(item)).filter(Boolean);
      return kvRows(rows) + listItems(warnings, 'waiting for AION data');
    }

    function renderPersonality(data) {
      const personality = data.personality?.data || data.personality || {};
      const telemetry = data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const telemetryPersonality = telemetry.latest_personality_summary || {};
      const top = summarizeTop(personality.top_families || telemetryPersonality.top_families, 'family', 'health_score');
      const weak = summarizeTop(personality.weak_families || telemetryPersonality.weak_families, 'family', 'health_score');
      const regimes = toArray(personality.current_regimes || personality.regimes || telemetryPersonality.current_regimes || telemetryPersonality.regimes).map(formatBullet).filter(Boolean);
      return [
        kvRows([
          ['Top family', personality.top_families?.[0]?.family || personality.top_families?.[0]?.name || telemetryPersonality.top_families?.[0]?.family || telemetryPersonality.top_families?.[0]?.name || 'waiting'],
          ['Weak family', personality.weak_families?.[0]?.family || personality.weak_families?.[0]?.name || telemetryPersonality.weak_families?.[0]?.family || telemetryPersonality.weak_families?.[0]?.name || 'waiting'],
          ['Top regime', personality.current_regimes?.[0] || personality.primary_regime || telemetryPersonality.current_regimes?.[0] || telemetryPersonality.primary_regime || 'waiting'],
          ['Reliability', personality.personality_confidence !== undefined ? formatNumber(personality.personality_confidence, 3) : (telemetryPersonality.personality_confidence !== undefined ? formatNumber(telemetryPersonality.personality_confidence, 3) : 'waiting')],
        ]),
        `<div><div class="muted" style="margin-bottom:6px;">Top personalities</div>${listItems(top, 'waiting for AION data')}</div>`,
        `<div><div class="muted" style="margin-bottom:6px;">Deprioritized personalities</div>${listItems(weak, 'waiting for AION data')}</div>`,
        `<div><div class="muted" style="margin-bottom:6px;">Current regimes</div>${listItems(regimes, 'waiting for AION data')}</div>`,
      ].join('');
    }

    function renderProbability(data) {
      const probability = data.probability?.data || data.probability || {};
      const telemetry = data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const telemetryProbability = telemetry.latest_probability_summary || {};
      const rows = [
        ['Fair value', probability.fair_value_state || probability.fair_value?.state || telemetryProbability.fair_value_state || telemetryProbability.fair_value?.state || 'waiting'],
        ['Conviction', probability.conviction_score !== undefined ? formatNumber(probability.conviction_score, 3) : (telemetryProbability.conviction_score !== undefined ? formatNumber(telemetryProbability.conviction_score, 3) : 'waiting')],
        ['Edge', probability.edge_state || probability.edge_strength || telemetryProbability.edge_state || telemetryProbability.edge_strength || 'waiting'],
        ['Mispricing', probability.mispricing_state || probability.mispricing?.state || telemetryProbability.mispricing_state || telemetryProbability.mispricing?.state || 'waiting'],
      ];
      const top = summarizeTop(probability.top_families || telemetryProbability.top_families, 'family', 'conviction_score');
      const weak = summarizeTop(probability.weak_families || telemetryProbability.weak_families, 'family', 'conviction_score');
      const notes = toArray(probability.notes || probability.edge_notes || telemetryProbability.notes || telemetryProbability.edge_notes || []).map(formatBullet).filter(Boolean);
      return [
        kvRows(rows),
        `<div><div class="muted" style="margin-bottom:6px;">Top conviction families</div>${listItems(top, 'waiting for AION data')}</div>`,
        `<div><div class="muted" style="margin-bottom:6px;">Weak edge environments</div>${listItems(weak, 'waiting for AION data')}</div>`,
        `<div><div class="muted" style="margin-bottom:6px;">Mispricing notes</div>${listItems(notes, 'waiting for AION data')}</div>`,
      ].join('');
    }

    function renderExternal(data) {
      const external = data.external?.data || data.external || {};
      const telemetry = data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const telemetryExternal = telemetry.latest_external_summary || {};
      const catalysts = toArray(external.active_catalysts || external.catalysts).map((item) => {
        if (!item || typeof item !== 'object') return formatBullet(item);
        return `${item.catalyst_type || item.type || 'unknown'}${item.affected_family ? ` · ${item.affected_family}` : ''}${item.risk_level ? ` · ${item.risk_level}` : ''}`;
      }).filter(Boolean);
      const contextRows = [
        ['State', external.external_awareness_state || external.state || telemetryExternal.external_awareness_state || telemetryExternal.state || 'waiting'],
        ['Event risk', external.event_risk?.risk_level || external.event_risk_level || telemetryExternal.event_risk?.risk_level || telemetryExternal.event_risk_level || 'waiting'],
        ['Confidence', external.event_risk?.confidence !== undefined ? formatNumber(external.event_risk.confidence, 3) : (telemetryExternal.event_risk?.confidence !== undefined ? formatNumber(telemetryExternal.event_risk.confidence, 3) : 'waiting')],
        ['Affected families', (external.affected_families || telemetryExternal.affected_families || []).join(', ') || 'waiting'],
      ];
      return kvRows(contextRows) + listItems(catalysts, 'waiting for AION data');
    }

    function renderEvolution(data) {
      const evolution = data.evolution?.data || data.evolution || {};
      const telemetry = data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const telemetryEvolution = telemetry.latest_evolution_summary || {};
      const health = evolution.health || {};
      const recommendations = toArray(data.recommendations?.data?.recommendations || evolution.recommendations || telemetry.latest_recommendations || telemetryEvolution.recommendations || []).map((item) => {
        if (!item || typeof item !== 'object') return formatBullet(item);
        const title = item.title || item.name || item.summary || 'recommendation';
        const risk = item.risk_level || item.risk || '';
        return `${title}${risk ? ` · ${risk}` : ''}${item.confidence !== undefined ? ` · ${formatNumber(item.confidence, 3)}` : ''}`;
      }).filter(Boolean);
      const patterns = toArray(evolution.pattern_summary?.success_patterns || telemetryEvolution.pattern_summary?.success_patterns || []).map(formatBullet).filter(Boolean);
      const rows = [
        ['Ecosystem health', health.health_state || telemetryEvolution.health?.health_state || 'waiting'],
        ['Health score', health.health_score !== undefined ? formatNumber(health.health_score, 3) : (telemetryEvolution.health?.health_score !== undefined ? formatNumber(telemetryEvolution.health.health_score, 3) : 'waiting')],
        ['Recommendations', recommendations.length || 'waiting'],
        ['Pattern memory', patterns.length || 'waiting'],
      ];
      return [
        kvRows(rows),
        `<div><div class="muted" style="margin-bottom:6px;">Top recommendations</div>${listItems(recommendations, 'waiting for AION data')}</div>`,
        `<div><div class="muted" style="margin-bottom:6px;">Pattern memory highlights</div>${listItems(patterns, 'waiting for AION data')}</div>`,
      ].join('');
    }

    function renderCommandCenter(data) {
      const aionStatus = getAionStatus(data);
      const commandCenter = data.commandCenter?.data || data.commandCenter || {};
      const telemetry = aionStatus.telemetry || data.ecosystem?.data?.telemetry || data.live?.data?.telemetry || {};
      const health = aionStatus.global_health || data.health?.data?.command_center || data.health?.data?.global_health || commandCenter.health || commandCenter.global_health || telemetry.health || {};
      const rows = [
        ['Global health', health.global_health_state || aionStatus.global_health_state || telemetry.health?.global_health_state || 'waiting'],
        ['Global score', health.global_health_score !== undefined ? formatNumber(health.global_health_score, 3) : (aionStatus.global_health_score !== undefined ? formatNumber(aionStatus.global_health_score, 3) : (telemetry.health?.global_health_score !== undefined ? formatNumber(telemetry.health.global_health_score, 3) : 'waiting'))],
        ['Status map', commandCenter.realm_status_map ? `${Object.keys(commandCenter.realm_status_map).length} realms` : (aionStatus.telemetry?.active_realms ? `${aionStatus.telemetry.active_realms.length} realms` : (telemetry.active_realms ? `${telemetry.active_realms.length} realms` : 'waiting'))],
        ['Suggested next actions', (data.ecosystem?.data?.suggested_next_actions || commandCenter.suggested_next_actions || telemetry.suggested_next_actions || []).join(', ') || 'waiting'],
      ];
      const warnings = toArray(commandCenter.active_warnings || commandCenter.warnings || telemetry.health_reason || []).map(formatBullet).filter(Boolean);
      return kvRows(rows) + listItems(warnings, 'waiting for AION data');
    }

    function buildPanelData(data) {
      return {
        aion: makeCard(PANEL_DEFS.aion, renderAion(data), data.health?.data?.health?.summary || data.ecosystem?.data?.health?.summary || 'waiting', getSeverityClass(data.health?.data?.health?.summary || data.ecosystem?.data?.health?.summary)),
        websocket: makeCard(PANEL_DEFS.websocket, renderWebSocket(data), data.ecosystem?.data?.websocket?.connection_state || data.live?.data?.websocket?.connection_state || 'waiting', getSeverityClass(data.ecosystem?.data?.websocket?.connection_state || data.live?.data?.websocket?.connection_state)),
        risk: makeCard(PANEL_DEFS.risk, renderRisk(data), data.risk?.data?.last_global_state?.capital_state || data.ecosystem?.data?.portfolio?.mode || 'waiting', getSeverityClass(data.risk?.data?.last_global_state?.capital_state || data.ecosystem?.data?.portfolio?.mode)),
        personality: makeCard(PANEL_DEFS.personality, renderPersonality(data), data.personality?.data?.personality_state || data.personality?.data?.state || 'waiting', getSeverityClass(data.personality?.data?.personality_state || data.personality?.data?.state)),
        probability: makeCard(PANEL_DEFS.probability, renderProbability(data), data.probability?.data?.edge_state || data.probability?.data?.fair_value_state || 'waiting', getSeverityClass(data.probability?.data?.edge_state || data.probability?.data?.fair_value_state)),
        external: makeCard(PANEL_DEFS.external, renderExternal(data), data.external?.data?.event_risk?.risk_level || data.external?.data?.external_awareness_state || 'waiting', getSeverityClass(data.external?.data?.event_risk?.risk_level || data.external?.data?.external_awareness_state)),
        evolution: makeCard(PANEL_DEFS.evolution, renderEvolution(data), data.evolution?.data?.health?.health_state || 'waiting', getSeverityClass(data.evolution?.data?.health?.health_state)),
        command: makeCard(PANEL_DEFS.command, renderCommandCenter(data), data.commandCenter?.data?.health?.global_health_state || 'waiting', getSeverityClass(data.commandCenter?.data?.health?.global_health_state)),
      };
    }

    async function fetchJson(url) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 4500);
      try {
        const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
        const text = await response.text();
        let payload = null;
        try {
          payload = text ? JSON.parse(text) : null;
        } catch (parseError) {
          payload = { ok: false, reason: 'invalid_json', raw: text };
        }
        if (!response.ok) {
          const error = new Error(payload?.reason || response.statusText || 'request_failed');
          error.status = response.status;
          error.payload = payload;
          throw error;
        }
        return payload;
      } finally {
        clearTimeout(timeout);
      }
    }

    async function refresh() {
      log('REFRESH', 'polling backend');
      state.loading = true;
      document.body.dataset.loading = 'true';
      try {
        const entries = Object.entries(ENDPOINTS);
        const results = await Promise.allSettled(entries.map(([key, url]) => fetchJson(url).then((payload) => [key, payload])));
        const nextData = {};
        let successCount = 0;
        results.forEach((result, index) => {
          const key = entries[index][0];
          if (result.status === 'fulfilled') {
            successCount += 1;
            nextData[key] = result.value[1] || { ok: true, data: null };
          } else {
            nextData[key] = { ok: false, reason: result.reason?.message || 'unavailable' };
            log('ERROR', `fetch failed for ${key}`, result.reason);
          }
        });
        state.lastData = nextData;
        state.backendOnline = successCount > 0;
        const anyHealthy = successCount > 0;
        document.getElementById('backendState').textContent = anyHealthy ? `backend online · ${successCount}/${entries.length}` : 'backend offline';
        document.getElementById('backendState').className = `pill mono ${anyHealthy ? '' : 'warn'}`;
        document.getElementById('feedState').textContent = `polling every 7s · ${successCount}/${entries.length} endpoints`;
        document.getElementById('overallState').innerHTML = anyHealthy
          ? '<span class="dot"></span><span>AION data available</span>'
          : '<span class="dot warn"></span><span>waiting for AION data</span>';
        document.getElementById('lastUpdated').textContent = `last update: ${new Date().toLocaleTimeString()}`;
        const narrative = buildNarrative(nextData);
        document.getElementById('narrativeSummary').textContent = narrative || 'Waiting for AION data...';
        document.getElementById('heroSummary').textContent = narrative || 'Waiting for AION data...';
        document.getElementById('summaryState').textContent = anyHealthy ? 'live' : 'waiting';
        document.getElementById('summaryState').className = `status-pill ${anyHealthy ? 'ok' : 'warn'}`;
        const panelHtml = buildPanelData(nextData);
        document.getElementById('panels').innerHTML = PANEL_ORDER.map((key) => panelHtml[key]).join('');
        const health = nextData.health?.data?.health || nextData.ecosystem?.data?.health || {};
        const ws = nextData.ecosystem?.data?.websocket || nextData.live?.data?.websocket || {};
        document.getElementById('globalHealthValue').textContent = health.summary || (health.portfolio_health_score !== undefined ? formatNumber(health.portfolio_health_score, 3) : 'waiting');
        document.getElementById('executionPostureValue').textContent = nextData.live?.data?.execution_posture?.posture || nextData.live?.data?.execution_posture?.mode || nextData.commandCenter?.data?.execution_posture?.posture || 'waiting';
        document.getElementById('websocketValue').textContent = ws.connection_state || ws.status || 'waiting';
        log('FETCH', 'backend refreshed', { successCount, total: entries.length });
        if (successCount > 0 && successCount < entries.length) {
          log('PARTIAL-DATA', 'some endpoints unavailable', { successCount, total: entries.length });
        }
        if (!anyHealthy) {
          log('OFFLINE', 'backend unavailable; showing fallback cards');
        }
      } catch (error) {
        log('ERROR', 'refresh failed', error);
        state.backendOnline = false;
        document.getElementById('backendState').textContent = 'backend offline';
        document.getElementById('backendState').className = 'pill mono warn';
        document.getElementById('overallState').innerHTML = '<span class="dot bad"></span><span>waiting for AION data</span>';
        document.getElementById('feedState').textContent = 'backend unavailable';
        document.getElementById('summaryState').textContent = 'waiting';
        document.getElementById('summaryState').className = 'status-pill warn';
        document.getElementById('narrativeSummary').textContent = 'Waiting for AION data...';
        document.getElementById('heroSummary').textContent = 'Waiting for AION data...';
        document.getElementById('panels').innerHTML = PANEL_ORDER.map((key) => makeCard(PANEL_DEFS[key], '<div class="muted">waiting for AION data</div>', 'offline', 'bad')).join('');
      } finally {
        state.loading = false;
        document.body.dataset.loading = 'false';
      }
    }

    function setDetails(open) {
      document.querySelectorAll('details.panel').forEach((panel) => {
        panel.open = open;
      });
      state.detailsOpen = open;
    }

    async function copySummary() {
      try {
        const text = document.getElementById('narrativeSummary').textContent || '';
        await navigator.clipboard.writeText(text);
        log('FETCH', 'summary copied');
      } catch (error) {
        log('ERROR', 'copy summary failed', error);
      }
    }

    document.getElementById('refreshBtn').addEventListener('click', refresh);
    document.getElementById('showAllBtn').addEventListener('click', () => setDetails(true));
    document.getElementById('hideAllBtn').addEventListener('click', () => setDetails(false));
    document.getElementById('copyBtn').addEventListener('click', copySummary);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        refresh();
      }
    });

    log('LOAD', 'SERAPH live intelligence UI booted');
    refresh();
    state.refreshTimer = setInterval(refresh, 7000);
  </script>
</body>
</html>
"""


class SeraphInterfaceLayer:
    def __init__(
        self,
        bot=None,
        enabled=REALM_XIX_ENABLE,
        api_enable=REALM_XIX_API_ENABLE,
        ui_enable=REALM_XIX_UI_ENABLE,
        realm_xxviii_enable=REALM_XXVIII_ENABLE,
        alert_history_limit=REALM_XIX_ALERT_HISTORY_LIMIT,
        telemetry_interval_seconds=REALM_XIX_TELEMETRY_INTERVAL_SECONDS,
        api_host=REALM_XIX_API_HOST,
        api_port=REALM_XIX_API_PORT,
        state_file=REALM_XIX_STATE_FILE,
    ):
        self.bot = bot
        self.enabled = bool(enabled)
        self.api_enable = bool(api_enable)
        self.ui_enable = bool(ui_enable)
        self.realm_xxviii_enable = bool(realm_xxviii_enable)
        self.alert_history_limit = max(10, int(alert_history_limit))
        self.telemetry_interval_seconds = max(5.0, float(telemetry_interval_seconds))
        self.api_host = str(api_host or "0.0.0.0")
        self.api_port = int(api_port or 3002)
        self.state_file = state_file
        ensure_runtime_layout()
        verify_runtime_paths(extra_paths=[self.state_file])
        self.started_at = time.time()
        self.last_update_ts = 0.0
        self.last_state = EcosystemState().to_dict()
        self.last_summary_text = ""
        self.recent_alerts = deque(maxlen=self.alert_history_limit)
        self.recent_telemetry = deque(maxlen=self.alert_history_limit)
        self._alert_last_seen = {}
        self._lock = threading.RLock()
        self._server = None
        self._server_thread = None
        self._last_serialized_ts = 0.0
        self.live_telemetry_registry = None
        self.consciousness_engine = None
        if get_live_state_registry is not None:
            try:
                self.live_telemetry_registry = get_live_state_registry(
                    enabled=bool(REALM_XXIX_ENABLE),
                    state_file=REALM_XXIX_STATE_FILE,
                    history_limit=REALM_XXIX_HISTORY_LIMIT,
                )
            except Exception as exc:
                self._log("[REALM-XXIX-ERROR]", reason=exc.__class__.__name__)
        if NarrativeEngine is not None:
            try:
                self.consciousness_engine = NarrativeEngine(enabled=bool(REALM_XXX_ENABLE))
            except Exception as exc:
                self._log("[REALM-XXX-ERROR]", reason=exc.__class__.__name__)
                self.consciousness_engine = None
        self.distributed_council = None
        self.command_center_bridge = None
        self.ui_effective_enable = bool(self.ui_enable or self.realm_xxviii_enable)
        if DistributedIntelligenceCouncil is not None:
            try:
                self.distributed_council = DistributedIntelligenceCouncil()
            except Exception as exc:
                self._log("[REALM-XX-ERROR]", reason=exc.__class__.__name__)
        if SeraphCommandCenterBridge is not None:
            try:
                self.command_center_bridge = SeraphCommandCenterBridge(
                    bot=self.bot,
                    interface=self,
                    enabled=REALM_XXVII_ENABLE,
                    state_file=REALM_XXVII_STATE_FILE,
                    summary_interval_seconds=REALM_XXVII_SUMMARY_INTERVAL_SECONDS,
                )
                if self.bot is not None:
                    setattr(self.bot, "realm_xxvii_engine", self.command_center_bridge)
                    setattr(self.bot, "last_realm_xxvii_summary", self.command_center_bridge.get_state())
            except Exception as exc:
                self._log("[REALM-XXVII-ERROR]", reason=exc.__class__.__name__)
                self.command_center_bridge = None
        self._write_state_file(self.last_state)
        if (self.enabled or self.ui_effective_enable or self.api_enable) and self.api_enable:
            self.start_api_server()

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _write_state_file(self, state):
        try:
            _ensure_dir(self.state_file)
            ok = safe_atomic_json_write(self.state_file, _json_safe(state), sort_keys=True, indent=2)
            record_persist_event(subsystem="REALM-XIX", path=self.state_file, action="write_state_file", success=bool(ok), reason=None if ok else "write_failed")
            if not ok:
                self._log("[REALM-XIX-DEGRADED]", reason="write_failed", action="write_state_file")
        except Exception as exc:
            record_persist_event(subsystem="REALM-XIX", path=self.state_file, action="write_state_file", success=False, reason=exc.__class__.__name__)
            self._log("[REALM-XIX-DEGRADED]", reason=exc.__class__.__name__, action="write_state_file")

    def _realm_flags(self, bot):
        flags = []
        mapping = [
            ("IX", True),
            ("X", True),
            ("XI", True),
            ("XII", True),
            ("XIII", True),
            ("XIV", True),
            ("XV", getattr(bot, "realm_xv_simulator", None) is not None),
            ("XVI", getattr(bot, "realm_xvi_engine", None) is not None and getattr(bot.realm_xvi_engine, "enabled", False)),
            ("XVII", getattr(bot, "realm_xvii_engine", None) is not None and getattr(bot.realm_xvii_engine, "enabled", False)),
            ("XVIII", getattr(bot, "realm_xviii_engine", None) is not None and getattr(bot.realm_xviii_engine, "enabled", False)),
            ("XX", getattr(bot, "realm_xx_engine", None) is not None and getattr(bot.realm_xx_engine, "enabled", False)),
            ("XXI", getattr(bot, "realm_xxi_engine", None) is not None and getattr(bot.realm_xxi_engine, "enabled", False)),
            ("XXIII", getattr(bot, "realm_xxiii_engine", None) is not None and getattr(bot.realm_xxiii_engine, "enabled", False)),
            ("XXIV", getattr(bot, "realm_xxiv_engine", None) is not None and getattr(bot.realm_xxiv_engine, "enabled", False)),
            ("XXV", getattr(bot, "realm_xxv_engine", None) is not None and getattr(bot.realm_xxv_engine, "enabled", False)),
            ("XXVI", getattr(bot, "realm_xxvi_engine", None) is not None and getattr(bot.realm_xxvi_engine, "enabled", False)),
            ("XXVII", getattr(bot, "realm_xxvii_engine", None) is not None and getattr(bot.realm_xxvii_engine, "enabled", False)),
            ("XXX", bool(REALM_XXX_ENABLE)),
            ("XIX", self.enabled),
        ]
        for label, active in mapping:
            if active:
                flags.append(label)
        return flags

    def _extract_positions(self, bot):
        positions = []
        try:
            positions = list(bot._get_open_live_positions() or [])
        except Exception:
            positions = []
        normalized = []
        for position in positions:
            if not isinstance(position, dict):
                continue
            try:
                normalized.append(bot.extract_position_summary(position))
            except Exception:
                normalized.append(position)
        return [position for position in normalized if isinstance(position, dict)]

    def _latest_realm_summary(self, bot, attr_name, engine_attr=None):
        summary = getattr(bot, attr_name, None)
        if isinstance(summary, dict) and summary:
            return summary
        if engine_attr:
            engine = getattr(bot, engine_attr, None)
            if engine is not None and isinstance(getattr(engine, "last_report", None), dict):
                return engine.last_report
        return {}

    def _synthesize_consciousness(self, state, source="live"):
        state = state or {}
        consciousness = {}
        engine = self.consciousness_engine
        if engine is not None and getattr(engine, "enabled", False):
            try:
                consciousness = engine.build(state=state, source=source) or {}
            except Exception as exc:
                self._log("[REALM-XXX-ERROR]", reason=exc.__class__.__name__)
                consciousness = {}
        if isinstance(consciousness, dict) and consciousness:
            return consciousness
        try:
            fallback_engine = NarrativeEngine(enabled=False)
            fallback = fallback_engine.build(state=state, source=source) or {}
        except Exception:
            fallback = {}
        if not isinstance(fallback, dict):
            fallback = {}
        summary = fallback.get("summary") or state.get("operational_summary") or self.build_summary_text(state) or self.last_summary_text or ""
        if not summary:
            return {}
        consciousness = {
            "enabled": False,
            "status": state.get("status", "waiting"),
            "summary": summary,
            "narrative": summary,
            "operational_summary": summary,
            "sections": fallback.get("sections") if isinstance(fallback.get("sections"), dict) else {},
            "awareness": fallback.get("awareness") if isinstance(fallback.get("awareness"), dict) else {},
            "readiness": fallback.get("readiness", "monitoring"),
            "health_state": fallback.get("health_state", state.get("health", {}).get("global_health_state", "unknown")),
            "health_score": fallback.get("health_score", state.get("health", {}).get("global_health_score")),
            "source": "telemetry_fallback",
        }
        self._log("[REALM-XXX-STATE]", enabled="fallback", readiness=consciousness.get("readiness"), health_state=consciousness.get("health_state"))
        return consciousness

    def _build_aion_status_payload(self, state, telemetry, status, path):
        state = state if isinstance(state, dict) else {}
        telemetry = telemetry if isinstance(telemetry, dict) else {}
        bot = self.bot
        bot_live_select = dict(getattr(bot, "last_live_select_summary", {}) or {}) if bot is not None else {}
        bot_websocket = dict(getattr(bot, "last_websocket_summary", {}) or {}) if bot is not None else {}
        bot_command_center = dict(getattr(bot, "last_realm_xxvii_summary", {}) or {}) if bot is not None else {}
        telemetry = dict(state.get("telemetry") or telemetry or {})
        if not telemetry.get("websocket_status") and bot_websocket:
            telemetry["websocket_status"] = bot_websocket
        if not telemetry.get("live_mode"):
            telemetry["live_mode"] = state.get("live_mode") or {
                "active": bool(state.get("status") == "live" or state.get("identity", {}).get("portfolio_brain_active")),
                "armed": bool(getattr(bot, "live_armed", False)) if bot is not None else False,
                "env_confirmed": bool(getattr(bot, "live_env_confirmed", False)) if bot is not None else False,
            }
        if not telemetry.get("execution_posture"):
            telemetry["execution_posture"] = state.get("execution_posture") or bot_live_select.get("execution_posture") or {}
        if not telemetry.get("latest_selected_ticker"):
            telemetry["latest_selected_ticker"] = state.get("latest_selected_ticker") or bot_live_select.get("selected_ticker")
        if not telemetry.get("latest_entry_timing_status"):
            telemetry["latest_entry_timing_status"] = state.get("latest_entry_timing_status") or bot_live_select.get("entry_timing_status") or {}
        if not telemetry.get("service_status"):
            telemetry["service_status"] = state.get("service_status") or {
                "service_active": bool(bot is not None),
                "bot_attached": bool(bot is not None),
                "source": "api",
            }
        if not telemetry.get("command_center") and bot_command_center:
            telemetry["command_center"] = bot_command_center
        command_center = dict(state.get("command_center") or telemetry.get("command_center") or {})
        command_center_health = dict(
            state.get("command_center_health")
            or command_center.get("health")
            or command_center.get("global_health")
            or state.get("health")
            or telemetry.get("health")
            or {}
        )
        global_health = dict(state.get("health") or telemetry.get("health") or command_center_health or {})
        websocket = dict(state.get("websocket") or telemetry.get("websocket_status") or {})
        live_mode = dict(state.get("live_mode") or telemetry.get("live_mode") or {})
        execution_posture = dict(
            state.get("execution_posture")
            or telemetry.get("execution_posture")
            or command_center.get("execution_posture")
            or {}
        )
        latest_selected_ticker = (
            state.get("latest_selected_ticker")
            or telemetry.get("latest_selected_ticker")
            or execution_posture.get("latest_selected_ticker")
            or (telemetry.get("latest_market_scan") or {}).get("selected_ticker")
        )
        latest_entry_timing_status = dict(state.get("latest_entry_timing_status") or telemetry.get("latest_entry_timing_status") or {})
        service_status = dict(state.get("service_status") or telemetry.get("service_status") or {})
        live_mode_active = bool(live_mode.get("active", state.get("status") == "live" or state.get("identity", {}).get("portfolio_brain_active")))
        websocket_state = (
            websocket.get("connection_state")
            or websocket.get("connection")
            or websocket.get("status")
            or websocket.get("health")
            or telemetry.get("websocket_status", {}).get("health")
            or "unknown"
        )
        self._log("[AION-API]", path=path, status=status, telemetry="yes" if telemetry else "no")
        payload_data = {
            "status": status,
            "live_mode": live_mode_active,
            "live_mode_state": live_mode,
            "service_status": service_status,
            "health": global_health,
            "global_health": global_health,
            "global_health_state": global_health.get("global_health_state") or command_center_health.get("global_health_state"),
            "global_health_score": global_health.get("global_health_score") or command_center_health.get("global_health_score"),
            "websocket": websocket,
            "websocket_state": websocket_state,
            "websocket_status": websocket,
            "execution_posture": execution_posture,
            "latest_selected_ticker": latest_selected_ticker,
            "latest_entry_timing_status": latest_entry_timing_status,
            "latest_market_scan": state.get("latest_market_scan") or telemetry.get("latest_market_scan", {}),
            "latest_execution_decision": state.get("latest_execution_decision") or telemetry.get("latest_execution_decision", {}),
            "latest_execution_block_reason": state.get("latest_execution_block_reason") or telemetry.get("latest_execution_block_reason"),
            "latest_risk_state": state.get("latest_risk_state") or telemetry.get("latest_risk_state", {}),
            "latest_aggression_state": state.get("latest_aggression_state") or telemetry.get("latest_aggression_state", {}),
            "latest_drawdown_state": state.get("latest_drawdown_state") or telemetry.get("latest_drawdown_state", {}),
            "latest_exposure_state": state.get("latest_exposure_state") or telemetry.get("latest_exposure_state", {}),
            "latest_personality_summary": state.get("latest_personality_summary") or telemetry.get("latest_personality_summary", {}),
            "latest_probability_summary": state.get("latest_probability_summary") or telemetry.get("latest_probability_summary", {}),
            "latest_external_summary": state.get("latest_external_summary") or telemetry.get("latest_external_summary", {}),
            "latest_evolution_summary": state.get("latest_evolution_summary") or telemetry.get("latest_evolution_summary", {}),
            "latest_recommendations": state.get("latest_recommendations") or telemetry.get("latest_recommendations", []),
            "command_center": command_center,
            "consciousness": state.get("consciousness", {}),
            "operational_summary": state.get("operational_summary", ""),
            "current_phase": state.get("current_phase") or telemetry.get("current_phase") or execution_posture.get("phase") or execution_posture.get("current_phase") or "live_cycle",
            "timestamp": state.get("generated_at") or telemetry.get("last_updated"),
            "telemetry": telemetry,
            "path": path,
        }
        self._log(
            "[AION-TELEMETRY]",
            path=path,
            status=status,
            live_mode="yes" if live_mode_active else "no",
            websocket_state=websocket_state,
            global_health_state=payload_data["global_health_state"] or "unknown",
            selected_ticker=latest_selected_ticker or "none",
            entry_timing="present" if latest_entry_timing_status else "missing",
        )
        return {"ok": True, "path": path, "status": status, "data": payload_data}

    def _live_telemetry_snapshot(self, bot=None):
        bot = bot or self.bot
        registry = getattr(bot, "realm_xxix_engine", None) if bot is not None else None
        if registry is None:
            registry = getattr(self, "live_telemetry_registry", None)
        if registry is None:
            self._log("[LIVE-STATE-MISSING]", field="registry", source="seraph")
            return {}
        try:
            maybe_emit_summary = getattr(registry, "maybe_emit_summary", None)
            snapshot_fn = getattr(registry, "snapshot", None)
            if callable(maybe_emit_summary):
                snapshot = maybe_emit_summary(force=False, source="seraph") or {}
            elif callable(snapshot_fn):
                snapshot = snapshot_fn() or {}
            else:
                snapshot = {}
            if (
                not snapshot
                or snapshot.get("status") == "waiting"
                or not snapshot.get("latest_selected_ticker")
                or not snapshot.get("latest_market_scan")
            ):
                if export_live_telemetry_snapshot is not None and bot is not None:
                    try:
                        snapshot = export_live_telemetry_snapshot(
                            bot=bot,
                            source="seraph",
                            registry=registry,
                            markets=None,
                            quotes=None,
                            candidates=None,
                            selected_quote=None,
                            live_select_summary=getattr(bot, "last_live_select_summary", {}) or {},
                            execution_block_reason=(getattr(bot, "last_live_select_summary", {}) or {}).get("execution_block_reason")
                            or (getattr(bot, "last_live_select_summary", {}) or {}).get("block_reason"),
                            entry_timing_status=(getattr(bot, "last_live_select_summary", {}) or {}).get("entry_timing_status"),
                        ) or snapshot
                    except Exception as exc:
                        self._log("[REALM-XXIX-DEGRADED]", reason=exc.__class__.__name__, action="live_telemetry_export")
                if (
                    (not snapshot or snapshot.get("status") == "waiting" or not snapshot.get("latest_selected_ticker"))
                    and bot is not None
                ):
                    snapshot = self._synthesize_live_telemetry_snapshot(bot=bot, source="seraph")
            if not snapshot:
                self._log("[LIVE-STATE-MISSING]", field="snapshot", source="seraph")
            return snapshot
        except Exception as exc:
            self._log("[REALM-XXIX-ERROR]", reason=exc.__class__.__name__, action="live_snapshot")
        return {}

    def _synthesize_live_telemetry_snapshot(self, bot=None, source="live"):
        bot = bot or self.bot
        if bot is None:
            return {}

        def _engine_summary(*attr_names):
            for attr_name in attr_names:
                engine = getattr(bot, attr_name, None)
                if engine is None:
                    continue
                for method_name in ("get_summary", "get_state", "get_global_state"):
                    method = getattr(engine, method_name, None)
                    if callable(method):
                        try:
                            value = method()
                            if isinstance(value, dict) and value:
                                return dict(value)
                        except Exception:
                            continue
                last_report = getattr(engine, "last_report", None)
                if isinstance(last_report, dict) and last_report:
                    return dict(last_report)
            return {}

        live_select_summary = dict(getattr(bot, "last_live_select_summary", {}) or {})
        websocket_summary = dict(getattr(bot, "last_websocket_summary", {}) or {})
        personality_summary = dict(getattr(bot, "last_realm_xxi_summary", {}) or {}) or _engine_summary("realm_xxi_engine")
        probability_summary = dict(getattr(bot, "last_realm_xxiii_summary", {}) or {}) or _engine_summary("realm_xxiii_engine")
        risk_summary = dict(getattr(bot, "last_realm_xxiv_summary", {}) or {}) or _engine_summary("realm_xxiv_engine")
        external_summary = dict(getattr(bot, "last_realm_xxv_summary", {}) or {}) or _engine_summary("realm_xxv_engine")
        evolution_summary = dict(getattr(bot, "last_realm_xxvi_summary", {}) or {}) or _engine_summary("realm_xxvi_engine")
        command_center_summary = dict(getattr(bot, "last_realm_xxvii_summary", {}) or {}) or _engine_summary("realm_xxvii_engine")
        if not websocket_summary:
            websocket_manager = getattr(bot, "websocket_manager", None)
            if websocket_manager is not None:
                try:
                    websocket_summary = websocket_manager.maybe_emit_summary(force=False, source=source) or {}
                except Exception:
                    websocket_summary = {}
        execution_posture = dict(live_select_summary.get("execution_posture") or {})
        if not execution_posture:
            execution_posture = {
                "posture": "WAITING_FOR_SIGNAL" if bool(getattr(bot, "live_mode", False)) else "OFFLINE",
                "phase": live_select_summary.get("phase") or live_select_summary.get("current_phase") or "live_cycle",
            }
        latest_market_scan = {
            "market_count": live_select_summary.get("original_market_count")
            or live_select_summary.get("market_count")
            or len(getattr(bot, "last_live_markets", []) or []),
            "candidate_count": live_select_summary.get("candidate_count")
            or live_select_summary.get("selected_candidate_count")
            or (evolution_summary.get("candidate_count") if isinstance(evolution_summary, dict) else None)
            or 0,
            "selected_ticker": live_select_summary.get("selected_ticker"),
            "selected_family": live_select_summary.get("selected_family") or live_select_summary.get("family"),
            "selected_score": live_select_summary.get("selected_score") or live_select_summary.get("candidate_score"),
        }
        return {
            "service_status": {
                "service_active": True,
                "bot_attached": True,
                "source": source,
            },
            "live_mode": {
                "active": bool(getattr(bot, "live_mode", False)),
                "armed": bool(getattr(bot, "live_armed", False)),
                "env_confirmed": bool(getattr(bot, "live_env_confirmed", False)),
                "posture": execution_posture.get("posture") or execution_posture.get("mode") or "WAITING_FOR_SIGNAL",
            },
            "armed_live": bool(getattr(bot, "live_armed", False)),
            "execution_posture": execution_posture,
            "current_phase": live_select_summary.get("phase") or live_select_summary.get("current_phase") or "live_cycle",
            "websocket_status": websocket_summary,
            "websocket_subscription_count": websocket_summary.get("subscription_count") or websocket_summary.get("active_subscriptions") or 0,
            "websocket_latency": websocket_summary.get("latency_ms") or websocket_summary.get("latency"),
            "websocket_stale_feed": bool(websocket_summary.get("stale_feed") or websocket_summary.get("feed_stale")),
            "latest_market_scan": latest_market_scan,
            "latest_candidate_count": latest_market_scan.get("candidate_count", 0),
            "latest_selected_ticker": latest_market_scan.get("selected_ticker"),
            "latest_execution_decision": {
                "decision": live_select_summary.get("decision") or live_select_summary.get("final_decision") or live_select_summary.get("decision_result"),
                "reason": live_select_summary.get("decision_reason") or live_select_summary.get("reason"),
                "confidence": live_select_summary.get("confidence") or live_select_summary.get("decision_confidence"),
            },
            "latest_execution_block_reason": live_select_summary.get("execution_block_reason") or live_select_summary.get("block_reason"),
            "latest_entry_timing_status": dict(live_select_summary.get("entry_timing_status") or {}),
            "latest_risk_state": dict(risk_summary.get("last_global_state") or risk_summary or {}),
            "latest_aggression_state": {
                "aggression_level": (risk_summary.get("last_global_state") or risk_summary or {}).get("aggression_level"),
                "capital_state": (risk_summary.get("last_global_state") or risk_summary or {}).get("capital_state"),
            },
            "latest_drawdown_state": {
                "drawdown_pct": (risk_summary.get("last_global_state") or risk_summary or {}).get("drawdown_pct"),
                "soft_circuit_active": (risk_summary.get("last_global_state") or risk_summary or {}).get("soft_circuit_active"),
            },
            "latest_exposure_state": {
                "total_exposure_dollars": (risk_summary.get("last_global_state") or risk_summary or {}).get("total_exposure_dollars"),
                "family_concentration": (risk_summary.get("last_global_state") or risk_summary or {}).get("family_concentration"),
                "correlated_concentration": (risk_summary.get("last_global_state") or risk_summary or {}).get("correlated_concentration"),
            },
            "latest_personality_summary": personality_summary,
            "latest_probability_summary": probability_summary,
            "latest_external_summary": external_summary,
            "latest_evolution_summary": evolution_summary,
            "latest_recommendations": list((evolution_summary.get("recommendations") or []) if isinstance(evolution_summary, dict) else []),
            "active_realms": self._realm_flags(bot),
            "command_center": command_center_summary,
            "health": {},
            "status": "live" if bool(getattr(bot, "live_mode", False)) else "waiting",
            "last_updated": live_select_summary.get("timestamp") or live_select_summary.get("created_at") or _now_iso(),
            "last_source": source,
        }

    def _build_alert(self, category, title, message, severity="info", details=None, source="seraph"):
        payload = {
            "id": f"{category}:{title}:{message}",
            "category": category,
            "title": title,
            "message": message,
            "severity": severity,
            "source": source,
            "details": _json_safe(details or {}),
            "created_at": _now_iso(),
        }
        return payload

    def record_alert(self, category, title, message, severity="info", details=None, source="seraph"):
        alert = self._build_alert(category, title, message, severity=severity, details=details, source=source)
        signature = alert["id"]
        now_ts = time.time()
        last_seen = self._alert_last_seen.get(signature, 0.0)
        if now_ts - last_seen < self.telemetry_interval_seconds:
            return None
        self._alert_last_seen[signature] = now_ts
        with self._lock:
            self.recent_alerts.appendleft(alert)
            self.recent_telemetry.appendleft(
                {
                    "event_type": "alert",
                    "created_at": alert["created_at"],
                    "payload": alert,
                }
            )
        self._log(
            "[REALM-XIX-ALERT]",
            category=category,
            severity=severity,
            title=title,
            message=message,
        )
        if severity in {"warning", "critical"}:
            self._log("[REALM-XIX-WARNING]", category=category, title=title, message=message)
        if category == "guardian":
            self._log("[REALM-XIX-GUARDIAN]", title=title, message=message)
        return alert

    def _derive_alerts(self, state):
        alerts = []
        portfolio = state.get("portfolio", {}) if isinstance(state, dict) else {}
        guardian = state.get("guardian", {}) if isinstance(state, dict) else {}
        market = state.get("market", {}) if isinstance(state, dict) else {}
        strategies = state.get("strategies", {}) if isinstance(state, dict) else {}
        health = _safe_float((state.get("health") or {}).get("portfolio_health_score"), None)
        if health is not None:
            if health < 0.40:
                alerts.append(self._build_alert("portfolio", "risk_off", "Portfolio health below 0.40", severity="critical", details=portfolio))
            elif health < 0.55:
                alerts.append(self._build_alert("portfolio", "defensive", "Portfolio health below 0.55", severity="warning", details=portfolio))
        if _safe_float(portfolio.get("family_concentration"), 0.0) >= 0.60:
            alerts.append(self._build_alert("portfolio", "family_concentration", "Family concentration elevated", severity="warning", details=portfolio))
        if _safe_float(portfolio.get("correlated_concentration"), 0.0) >= 0.55:
            alerts.append(self._build_alert("portfolio", "correlated_exposure", "Correlated exposure elevated", severity="warning", details=portfolio))
        if str(portfolio.get("portfolio_mode") or "").lower() == "risk_off":
            alerts.append(self._build_alert("portfolio", "risk_off_mode", "Portfolio is in risk-off mode", severity="warning", details=portfolio))
        if guardian.get("last_veto_reason"):
            alerts.append(self._build_alert("guardian", "veto", f"Guardian veto: {guardian.get('last_veto_reason')}", severity="warning", details=guardian))
        if guardian.get("blocked_conditions"):
            alerts.append(self._build_alert("guardian", "blocked_conditions", "Guardian blocked conditions active", severity="warning", details=guardian))
        if market.get("regime_label") in {"high_volatility", "unstable_spread_conditions", "choppy"}:
            alerts.append(self._build_alert("market", "volatility", f"Market regime: {market.get('regime_label')}", severity="warning", details=market))
        if strategies.get("promoted", 0) or strategies.get("elite_candidates", 0):
            alerts.append(self._build_alert("strategy", "promotion", "Strategy candidates promoted", severity="info", details=strategies))
        if (state.get("simulation") or {}).get("last_replay_status") in {"failed", "degraded"}:
            alerts.append(self._build_alert("replay", "failure", "Replay reported a degraded result", severity="warning", details=state.get("simulation")))
        return alerts

    def collect_state(self, bot=None, source="live"):
        bot = bot or self.bot
        if bot is None:
            state = EcosystemState(
                generated_at=_now_iso(),
                active_realms=["XIX"] if self.enabled else [],
                identity={"ecosystem_name": "AION", "interface_entity": "SERAPH", "mode": "standalone"},
            ).to_dict()
            state["personality"] = {}
            return state
        uptime_seconds = max(0.0, time.time() - float(getattr(bot, "bot_started_at", self.started_at) or self.started_at))
        telemetry_snapshot = self._live_telemetry_snapshot(bot)
        portfolio_summary = dict(self._latest_realm_summary(bot, "last_realm_xviii_summary", "realm_xviii_engine") or {})
        xvi_summary = dict(self._latest_realm_summary(bot, "last_realm_xvi_summary", "realm_xvi_engine") or {})
        xvii_summary = dict(self._latest_realm_summary(bot, "last_realm_xvii_summary", "realm_xvii_engine") or {})
        xx_summary = dict(self._latest_realm_summary(bot, "last_realm_xx_summary", "realm_xx_engine") or {})
        xv_summary = dict(getattr(bot, "last_realm_xv_summary", {}) or {})
        live_mode_state = dict(telemetry_snapshot.get("live_mode") or {})
        websocket_snapshot = dict(telemetry_snapshot.get("websocket_status") or {})
        personality_snapshot = dict(telemetry_snapshot.get("latest_personality_summary") or {})
        probability_snapshot = dict(telemetry_snapshot.get("latest_probability_summary") or {})
        risk_snapshot = dict(telemetry_snapshot.get("latest_risk_state") or {})
        external_snapshot = dict(telemetry_snapshot.get("latest_external_summary") or {})
        evolution_snapshot = dict(telemetry_snapshot.get("latest_evolution_summary") or {})
        command_center_snapshot = dict(telemetry_snapshot.get("command_center") or {})
        telemetry_health = dict(telemetry_snapshot.get("health") or {})
        consciousness_snapshot = dict(telemetry_snapshot.get("consciousness") or {})
        open_positions = self._extract_positions(bot)
        active_families = sorted({str(position.get("family") or position.get("market_family") or "UNKNOWN").strip().upper() for position in open_positions if str(position.get("family") or position.get("market_family") or "").strip()})
        active_tickers = sorted({str(position.get("ticker") or "").strip() for position in open_positions if str(position.get("ticker") or "").strip()})
        strongest_correlations = []
        relationships = xvi_summary.get("relationships") if isinstance(xvi_summary, dict) else {}
        if isinstance(relationships, dict):
            for record in relationships.values():
                if not isinstance(record, dict):
                    continue
                positive_strength = _safe_float(record.get("positive_strength"), 0.0) or 0.0
                confidence = _safe_float(record.get("confidence"), 0.0) or 0.0
                if positive_strength < 0.45 or confidence < 0.35:
                    continue
                strongest_correlations.append(
                    {
                        "family_a": record.get("family_a"),
                        "family_b": record.get("family_b"),
                        "positive_strength": _round(positive_strength, 4, 0.0),
                        "confidence": _round(confidence, 4, 0.0),
                    }
                )
        strongest_correlations = sorted(
            strongest_correlations,
            key=lambda item: (item.get("positive_strength", 0.0), item.get("confidence", 0.0)),
            reverse=True,
        )
        portfolio = {
            "health_score": portfolio_summary.get("portfolio_health_score", 1.0),
            "mode": portfolio_summary.get("portfolio_mode", "normal"),
            "total_exposure_dollars": portfolio_summary.get("total_exposure_dollars", 0.0),
            "available_balance": portfolio_summary.get("available_balance"),
            "realized_daily_pnl": portfolio_summary.get("realized_daily_pnl", 0.0),
            "unrealized_pnl": portfolio_summary.get("unrealized_pnl"),
            "total_open_positions": portfolio_summary.get("total_open_positions", len(open_positions)),
            "active_tickers": active_tickers,
            "active_families": active_families,
            "family_exposure": portfolio_summary.get("exposure_by_family", {}),
            "category_exposure": portfolio_summary.get("exposure_by_category", {}),
            "side_exposure": portfolio_summary.get("exposure_by_side", {}),
            "correlated_exposure_groups": portfolio_summary.get("correlated_exposure_groups", {}),
            "family_concentration": portfolio_summary.get("family_concentration", 0.0),
            "correlated_concentration": portfolio_summary.get("correlated_concentration", 0.0),
            "risk_budget_used": portfolio_summary.get("max_daily_risk_used", 0.0),
            "remaining_risk_budget": portfolio_summary.get("remaining_risk_budget", 0.0),
            "capital_preservation_state": portfolio_summary.get("recommendation", "continue normal trading"),
            "daily_objective": {
                "realized_pnl": portfolio_summary.get("realized_daily_pnl", 0.0),
                "risk_budget_used": portfolio_summary.get("max_daily_risk_used", 0.0),
                "remaining_risk_budget": portfolio_summary.get("remaining_risk_budget", 0.0),
                "loss_streak": portfolio_summary.get("loss_streak", 0),
            },
        }
        strategy_candidates = {
            "candidate_count": len([item for item in (xvii_summary.get("strategies") or []) if str(item.get("status") or "").strip().lower() in {"candidate", "elite_candidate", "experimental"}]),
            "elite_candidates": xvii_summary.get("elite_candidates", 0),
            "promoted": xvii_summary.get("promoted", 0),
            "rejected": xvii_summary.get("rejected", 0),
            "lineage": _compact_list((item.get("lineage") for item in (xvii_summary.get("strategies") or []) if item.get("lineage")), limit=10),
            "specializations": _compact_list((item.get("specialization") for item in (xvii_summary.get("strategies") or []) if item.get("specialization")), limit=10),
            "portfolio_adjusted_scores": _compact_list(
                (
                    {
                        "name": item.get("name"),
                        "portfolio_adjusted_strategy_score": item.get("performance_metrics", {}).get("portfolio_adjusted_strategy_score"),
                        "risk_adjusted_candidate_score": item.get("performance_metrics", {}).get("risk_adjusted_candidate_score"),
                    }
                    for item in (xvii_summary.get("strategies") or [])
                ),
                limit=10,
            ),
        }
        simulation = {
            "last_replay_timestamp": getattr(bot, "last_replay_timestamp", None),
            "last_replay_summary": xv_summary,
            "portfolio_overlay": portfolio_summary,
            "realm_xvii_report": xvii_summary,
        }
        guardian = {
            "live_mode": bool(getattr(bot, "live_mode", False)),
            "live_armed": bool(getattr(bot, "live_armed", False) if hasattr(bot, "live_armed") else False),
            "live_env_confirmed": bool(getattr(bot, "live_env_confirmed", False) if hasattr(bot, "live_env_confirmed") else False),
            "trading_halted": bool(getattr(bot, "trading_halted", False)),
            "last_veto_reason": getattr(bot, "last_safety_alert_reason", None),
            "active_protections": [
                "duplicate_position_protection",
                "live_arming_requirement",
                "max_daily_trades",
                "max_concurrent_positions",
                "liquidity_protection",
                "spread_protection",
                "do_not_trade_system",
                "guardian_veto",
            ],
            "blocked_conditions": [reason for reason in [getattr(bot, "last_safety_alert_reason", None)] if reason],
            "risk_mode": portfolio_summary.get("portfolio_mode", "normal"),
        }
        market = {
            "regime_label": xvi_summary.get("regime_label", "unknown"),
            "dominant_regime": xvi_summary.get("dominant_regime", "unknown"),
            "overall_volatility": xvi_summary.get("overall_volatility", "unknown"),
            "volatility_state": xvi_summary.get("overall_volatility", xvi_summary.get("regime_label", "unknown")),
            "strongest_correlations": strongest_correlations[:5],
            "divergence_warnings": _compact_list(
                (
                    item
                    for item in [
                        xvi_summary.get("largest_divergence"),
                        xvi_summary.get("divergence_warning"),
                    ]
                    if item
                ),
                limit=5,
            ),
            "family_profiles": xvi_summary.get("family_profiles", {}),
            "relationships": xvi_summary.get("relationships", {}),
        }
        replay_results = {
            "realm_xv": xv_summary,
            "realm_xvii": xvii_summary,
            "realm_xviii": portfolio_summary,
            "realm_xx": xx_summary,
        }
        websocket_manager = getattr(bot, "websocket_manager", None)
        websocket_summary = websocket_snapshot or {}
        if websocket_manager is not None:
            try:
                websocket_summary = websocket_summary or websocket_manager.maybe_emit_summary(force=False, source=source) or {}
            except Exception as exc:
                self._log("[REALM-XX-DEGRADED]", reason=exc.__class__.__name__)
                websocket_summary = websocket_summary or {}
        personality_summary = personality_snapshot or {}
        personality_engine = getattr(bot, "realm_xxi_engine", None)
        if personality_engine is not None:
            try:
                personality_summary = personality_summary or personality_engine.get_summary() or {}
            except Exception as exc:
                self._log("[REALM-XXI-DEGRADED]", reason=exc.__class__.__name__)
                personality_summary = personality_summary or {}
        probability_summary = probability_snapshot or {}
        probability_engine = getattr(bot, "realm_xxiii_engine", None)
        if probability_engine is not None:
            try:
                probability_summary = probability_summary or probability_engine.get_summary() or {}
            except Exception as exc:
                self._log("[REALM-XXIII-DEGRADED]", reason=exc.__class__.__name__)
                probability_summary = probability_summary or {}
        risk_summary = risk_snapshot or {}
        risk_engine = getattr(bot, "realm_xxiv_engine", None)
        if risk_engine is not None:
            try:
                risk_summary = risk_summary or risk_engine.get_summary() or {}
                if not risk_summary.get("last_global_state"):
                    risk_summary["last_global_state"] = risk_engine.get_global_state() or {}
            except Exception as exc:
                self._log("[REALM-XXIV-DEGRADED]", reason=exc.__class__.__name__)
                risk_summary = risk_summary or {}
        external_summary = external_snapshot or {}
        external_engine = getattr(bot, "realm_xxv_engine", None)
        if external_engine is not None:
            try:
                external_summary = external_summary or external_engine.get_summary() or {}
                if not external_summary.get("summary"):
                    external_summary["summary"] = external_engine.get_state().get("summary")
            except Exception as exc:
                self._log("[REALM-XXV-DEGRADED]", reason=exc.__class__.__name__)
                external_summary = external_summary or {}
        evolution_summary = evolution_snapshot or {}
        evolution_engine = getattr(bot, "realm_xxvi_engine", None)
        if evolution_engine is not None:
            try:
                evolution_summary = evolution_summary or evolution_engine.get_summary() or {}
                if not evolution_summary.get("analysis_summary"):
                    evolution_summary["analysis_summary"] = evolution_engine.get_state().get("analysis_summary", {})
            except Exception as exc:
                self._log("[REALM-XXVI-DEGRADED]", reason=exc.__class__.__name__)
                evolution_summary = evolution_summary or {}
        command_center_summary = command_center_snapshot or {}
        if self.command_center_bridge is not None:
            try:
                command_center_summary = command_center_summary or self.command_center_bridge.maybe_emit_summary(force=False, source=source) or {}
            except Exception as exc:
                self._log("[REALM-XXVII-DEGRADED]", reason=exc.__class__.__name__)
                command_center_summary = command_center_summary or {}
        alerts = []
        alerts.extend(self._derive_alerts({
            "portfolio": portfolio,
            "guardian": guardian,
            "market": market,
            "strategies": strategy_candidates,
            "simulation": simulation,
            "health": portfolio_summary,
        }))
        state = EcosystemState(
            generated_at=_now_iso(),
            uptime_seconds=uptime_seconds,
            active_realms=self._realm_flags(bot),
            portfolio=portfolio,
            market=market,
            strategies=strategy_candidates,
            simulation=simulation,
            guardian=guardian,
            alerts=alerts,
            health={
                "portfolio_health_score": telemetry_health.get("portfolio_health_score", portfolio.get("health_score", 1.0)),
                "summary": telemetry_health.get("global_health_state") or (
                    "healthy"
                    if _safe_float(portfolio.get("health_score"), 1.0) >= 0.75
                    else "cautious"
                    if _safe_float(portfolio.get("health_score"), 1.0) >= 0.60
                    else "defensive"
                    if _safe_float(portfolio.get("health_score"), 1.0) >= 0.40
                    else "risk_off"
                ),
                "global_health_state": telemetry_health.get("global_health_state"),
                "global_health_score": telemetry_health.get("global_health_score"),
                "reasons": telemetry_health.get("reasons", []),
                "alert_count": len(self.recent_alerts) + len(alerts),
                "api_ready": bool(self._server is not None),
                "telemetry_ready": True,
            },
            identity={
                "ecosystem_name": "AION",
                "interface_entity": "SERAPH",
                "portfolio_brain_active": bool(getattr(bot, "realm_xviii_engine", None) is not None and getattr(bot.realm_xviii_engine, "enabled", False)),
                "simulation_worlds_active": bool(getattr(bot, "realm_xv_simulator", None) is not None),
                "strategy_evolution_active": bool(getattr(bot, "realm_xvii_engine", None) is not None and getattr(bot.realm_xvii_engine, "enabled", False)),
                "cross_market_active": bool(getattr(bot, "realm_xvi_engine", None) is not None and getattr(bot.realm_xvi_engine, "enabled", False)),
                "distributed_intelligence_active": bool(getattr(bot, "realm_xx_engine", None) is not None and getattr(bot.realm_xx_engine, "enabled", False)),
                "market_personality_active": bool(getattr(bot, "realm_xxi_engine", None) is not None and getattr(bot.realm_xxi_engine, "enabled", False)),
                "probability_engine_active": bool(getattr(bot, "realm_xxiii_engine", None) is not None and getattr(bot.realm_xxiii_engine, "enabled", False)),
                "capital_intelligence_active": bool(getattr(bot, "realm_xxiv_engine", None) is not None and getattr(bot.realm_xxiv_engine, "enabled", False)),
                "external_awareness_active": bool(getattr(bot, "realm_xxv_engine", None) is not None and getattr(bot.realm_xxv_engine, "enabled", False)),
                "autonomous_evolution_active": bool(getattr(bot, "realm_xxvi_engine", None) is not None and getattr(bot.realm_xxvi_engine, "enabled", False)),
                "command_center_active": bool(self.command_center_bridge is not None and getattr(self.command_center_bridge, "enabled", False)),
                "websocket_infrastructure_active": bool(getattr(bot, "websocket_manager", None) is not None),
                "live_intelligence_ui_active": bool(self.ui_effective_enable),
                "active_realms": self._realm_flags(bot),
            },
            telemetry={
                "status": telemetry_snapshot.get("status", "waiting"),
                "last_updated": telemetry_snapshot.get("last_updated") or _now_iso(),
                "health": telemetry_health,
                "service_status": telemetry_snapshot.get("service_status", {}),
                "live_mode": live_mode_state,
                "websocket_status": websocket_snapshot,
                "current_phase": telemetry_snapshot.get("current_phase", "live_cycle"),
                "latest_market_scan": telemetry_snapshot.get("latest_market_scan", {}),
                "latest_candidate_count": telemetry_snapshot.get("latest_candidate_count", len([item for item in (xvii_summary.get("strategies") or []) if str(item.get("status") or "").strip().lower() in {"candidate", "elite_candidate", "experimental"}])),
                "latest_selected_ticker": telemetry_snapshot.get("latest_selected_ticker"),
                "latest_execution_decision": telemetry_snapshot.get("latest_execution_decision", {}),
                "latest_execution_block_reason": telemetry_snapshot.get("latest_execution_block_reason"),
                "latest_entry_timing_status": telemetry_snapshot.get("latest_entry_timing_status", {}),
                "latest_risk_state": telemetry_snapshot.get("latest_risk_state", {}),
                "latest_aggression_state": telemetry_snapshot.get("latest_aggression_state", {}),
                "latest_drawdown_state": telemetry_snapshot.get("latest_drawdown_state", {}),
                "latest_exposure_state": telemetry_snapshot.get("latest_exposure_state", {}),
                "latest_personality_summary": personality_summary,
                "latest_probability_summary": probability_summary,
                "latest_external_summary": external_summary,
                "latest_evolution_summary": evolution_summary,
                "latest_recommendations": telemetry_snapshot.get("latest_recommendations", []),
                "consciousness": consciousness_snapshot,
                "operational_summary": consciousness_snapshot.get("summary", ""),
                "command_center": command_center_snapshot,
                "last_update_ts": self.last_update_ts,
                "recent_alert_count": len(self.recent_alerts),
                "recent_telemetry_count": len(self.recent_telemetry),
                "last_event_types": _compact_list((entry.get("event_type") for entry in self.recent_telemetry), limit=10),
            },
            websocket=websocket_summary,
            risk=risk_summary,
            external=external_summary,
            evolution=evolution_summary,
            command_center=command_center_summary,
            consciousness=consciousness_snapshot,
            operational_summary=(consciousness_snapshot or {}).get("summary") or "",
            replay_results=replay_results,
        ).to_dict()
        state["probability"] = probability_summary
        state["personality"] = personality_summary
        consciousness = self._synthesize_consciousness(state, source=source)
        if isinstance(consciousness, dict) and consciousness:
            state["consciousness"] = consciousness
            state["operational_summary"] = consciousness.get("operational_summary") or consciousness.get("summary") or state.get("operational_summary", "")
            state["narrative_summary"] = consciousness.get("narrative") or consciousness.get("summary") or state.get("narrative_summary", "")
            state.setdefault("realm_status_map", {})
            state["realm_status_map"]["XXX"] = {
                "enabled": bool(getattr(self.consciousness_engine, "enabled", False)),
                "readiness": consciousness.get("readiness"),
                "health_state": consciousness.get("health_state"),
                "health_score": consciousness.get("health_score"),
            }
            if getattr(self.consciousness_engine, "enabled", False):
                self._log("[REALM-XXX-STATE]", enabled="yes", readiness=consciousness.get("readiness"), health_state=consciousness.get("health_state"))
        state["telemetry"] = {
            "status": telemetry_snapshot.get("status", "waiting"),
            "last_updated": telemetry_snapshot.get("last_updated") or state.get("generated_at"),
            "health": telemetry_health,
            "service_status": telemetry_snapshot.get("service_status", {}),
            "live_mode": live_mode_state,
            "websocket_status": websocket_snapshot,
            "current_phase": telemetry_snapshot.get("current_phase", "live_cycle"),
            "latest_market_scan": telemetry_snapshot.get("latest_market_scan", {}),
            "latest_candidate_count": telemetry_snapshot.get("latest_candidate_count", 0),
            "latest_selected_ticker": telemetry_snapshot.get("latest_selected_ticker"),
            "latest_execution_decision": telemetry_snapshot.get("latest_execution_decision", {}),
            "latest_execution_block_reason": telemetry_snapshot.get("latest_execution_block_reason"),
            "latest_entry_timing_status": telemetry_snapshot.get("latest_entry_timing_status", {}),
            "latest_risk_state": telemetry_snapshot.get("latest_risk_state", {}),
            "latest_aggression_state": telemetry_snapshot.get("latest_aggression_state", {}),
            "latest_drawdown_state": telemetry_snapshot.get("latest_drawdown_state", {}),
            "latest_exposure_state": telemetry_snapshot.get("latest_exposure_state", {}),
            "latest_personality_summary": personality_summary,
            "latest_probability_summary": probability_summary,
            "latest_external_summary": external_summary,
            "latest_evolution_summary": evolution_summary,
            "latest_recommendations": telemetry_snapshot.get("latest_recommendations", []),
            "consciousness": state.get("consciousness", consciousness_snapshot),
            "operational_summary": state.get("operational_summary", ""),
            "command_center": command_center_snapshot,
            "active_realms": telemetry_snapshot.get("active_realms", self._realm_flags(bot)),
            "execution_posture": telemetry_snapshot.get("execution_posture", {}),
        }
        state["status"] = telemetry_snapshot.get("status", "waiting")
        state["health_reason"] = telemetry_health.get("reasons", [])
        state["command_center_health"] = (command_center_summary or {}).get("health", {}) or telemetry_health
        state["command_center"]["telemetry"] = state["telemetry"]
        if self.distributed_council is not None and getattr(self.distributed_council, "enabled", False):
            try:
                council = self.distributed_council.maybe_refresh(bot=bot, force=False, source=source)
                state["council"] = council or {}
                state["distributed"] = {
                    "agent_registry": self.distributed_council.get_registry(),
                    "council": council or {},
                }
            except Exception as exc:
                self._log("[REALM-XX-DEGRADED]", reason=exc.__class__.__name__)
        return state

    def serialize_state(self, state):
        serialized = _json_safe(state)
        self._log(
            "[REALM-XIX-SERIALIZE]",
            keys=len(serialized.keys()) if isinstance(serialized, dict) else 0,
            timestamp=_now_iso(),
        )
        return serialized

    def _append_telemetry(self, event_type, payload):
        entry = {
            "event_type": event_type,
            "created_at": _now_iso(),
            "payload": _json_safe(payload),
        }
        with self._lock:
            self.recent_telemetry.appendleft(entry)
        self._log("[REALM-XIX-TELEMETRY]", event_type=event_type, created_at=entry["created_at"])
        return entry

    def update_from_bot(self, bot=None, source="live"):
        if not (self.enabled or self.api_enable or self.ui_effective_enable):
            self._log("[REALM-XIX-DEGRADED]", reason="disabled")
            return self.last_state
        bot = bot or self.bot
        if bot is None:
            self._log("[REALM-XIX-DEGRADED]", reason="missing_bot")
            return self.last_state
        try:
            state = self.collect_state(bot=bot, source=source)
            telemetry_section = state.get("telemetry", {}) if isinstance(state, dict) else {}
            if (
                build_hydrated_seraph_state is not None
                and isinstance(state, dict)
                and (
                    state.get("status") == "waiting"
                    or not telemetry_section
                    or not telemetry_section.get("latest_selected_ticker")
                )
            ):
                hydrated_state = build_hydrated_seraph_state(bot=bot, interface=self, source=source)
                if isinstance(hydrated_state, dict) and (
                    hydrated_state.get("telemetry")
                    or hydrated_state.get("status") != "waiting"
                    or hydrated_state.get("health")
                ):
                    self._log(
                        "[SERAPH-ENDPOINT-FALLBACK]",
                        path="/seraph/state",
                        reason="hydrated_live_registry",
                        source=source,
                    )
                    state = hydrated_state
            consciousness = self._synthesize_consciousness(state, source=source)
            if isinstance(consciousness, dict) and consciousness:
                state["consciousness"] = consciousness
                state["operational_summary"] = consciousness.get("operational_summary") or consciousness.get("summary") or state.get("operational_summary", "")
                state["narrative_summary"] = consciousness.get("narrative") or consciousness.get("summary") or state.get("narrative_summary", "")
                state.setdefault("realm_status_map", {})
                state["realm_status_map"]["XXX"] = {
                    "enabled": bool(getattr(self.consciousness_engine, "enabled", False)),
                    "readiness": consciousness.get("readiness"),
                    "health_state": consciousness.get("health_state"),
                    "health_score": consciousness.get("health_score"),
                }
            state = self.serialize_state(state)
            with self._lock:
                self.last_state = state
                self.last_update_ts = time.time()
                self.last_summary_text = self.build_summary_text(state)
            if bot is not None and isinstance(state.get("consciousness"), dict):
                setattr(bot, "last_realm_xxx_summary", state.get("consciousness", {}))
            self._append_telemetry("state_update", {"source": source, "portfolio_health": state.get("portfolio", {}).get("health_score"), "portfolio_mode": state.get("portfolio", {}).get("mode")})
            self._write_state_file(state)
            for alert in state.get("alerts", []) or []:
                if isinstance(alert, dict):
                    self.record_alert(
                        alert.get("category", "general"),
                        alert.get("title", "state_alert"),
                        alert.get("message", ""),
                        severity=alert.get("severity", "info"),
                        details=alert.get("details", {}),
                        source=alert.get("source", "seraph"),
                    )
            self._log(
                "[REALM-XIX-STATE]",
                portfolio_health=state.get("portfolio", {}).get("health_score"),
                portfolio_mode=state.get("portfolio", {}).get("mode"),
                active_positions=state.get("portfolio", {}).get("total_open_positions"),
                active_strategies=state.get("strategies", {}).get("candidate_count"),
                active_realms=len(state.get("active_realms") or []),
            )
            self._log(
                "[REALM-XIX-GUARDIAN-STATE]",
                last_veto_reason=state.get("guardian", {}).get("last_veto_reason"),
                risk_mode=state.get("guardian", {}).get("risk_mode"),
                blocked_conditions=state.get("guardian", {}).get("blocked_conditions"),
            )
            self._log(
                "[REALM-XX-SERAPH-EXPORT]",
                council_mode=state.get("council", {}).get("council_mode"),
                council_confidence=state.get("council", {}).get("council_confidence"),
                active_agents=len((state.get("distributed", {}) or {}).get("agent_registry", {}) or {}),
            )
            self._log(
                "[REALM-XXIII-SERAPH-EXPORT]",
                edge_summary_count=len((state.get("probability", {}) or {}).get("top_families", []) or []),
                active_probabilities=len((state.get("probability", {}) or {}).get("family_stats", {}) or {}),
            )
            self._log(
                "[REALM-XXIV-SERAPH-EXPORT]",
                capital_state=(state.get("risk", {}) or {}).get("last_global_state", {}).get("capital_state"),
                aggression_level=(state.get("risk", {}) or {}).get("last_global_state", {}).get("aggression_level"),
                soft_circuit_active=(state.get("risk", {}) or {}).get("last_global_state", {}).get("soft_circuit_active"),
            )
            self._log(
                "[REALM-XXV-SERAPH-EXPORT]",
                external_state=(state.get("external", {}) or {}).get("external_awareness_state"),
                active_catalysts=len((state.get("external", {}) or {}).get("active_catalysts", []) or []),
                event_risk=(state.get("external", {}) or {}).get("event_risk", {}).get("risk_level"),
            )
            self._log(
                "[REALM-XXVI-SERAPH-EXPORT]",
                ecosystem_health=(state.get("evolution", {}) or {}).get("health", {}).get("health_state"),
                recommendation_count=len((state.get("evolution", {}) or {}).get("recommendations", []) or []),
                strong_patterns=len((state.get("evolution", {}) or {}).get("pattern_summary", {}).get("success_patterns", []) or []),
            )
            self._log(
                "[REALM-XXVII-SERAPH-ENDPOINT]",
                path="/seraph/state",
                command="state",
                bridge="yes" if self.command_center_bridge is not None else "no",
            )
            self._log(
                "[SERAPH-COMMAND-CENTER-EXPORT]",
                command="state",
                ok=True,
                reason="aggregate_state",
            )
            return state
        except Exception as exc:
            self._log("[REALM-XIX-ERROR]", reason=exc.__class__.__name__, error=str(exc), source=source)
            self._log("[REALM-XIX-ERROR-TRACE]", trace=traceback.format_exc(limit=3).strip().replace("\n", " | "))
            if build_hydrated_seraph_state is not None:
                try:
                    state = build_hydrated_seraph_state(bot=bot, interface=self, source=source)
                    state = self.serialize_state(state)
                    with self._lock:
                        self.last_state = state
                        self.last_update_ts = time.time()
                        self.last_summary_text = self.build_summary_text(state)
                    if bot is not None and isinstance(state.get("consciousness"), dict):
                        setattr(bot, "last_realm_xxx_summary", state.get("consciousness", {}))
                    self._append_telemetry(
                        "state_update",
                        {"source": source, "portfolio_health": state.get("portfolio", {}).get("health_score"), "portfolio_mode": state.get("portfolio", {}).get("mode")},
                    )
                    self._write_state_file(state)
                    state.setdefault("personality", {})
                    self._log(
                        "[SERAPH-ENDPOINT-FALLBACK]",
                        path="/seraph/state",
                        reason="hydrated_live_registry",
                        source=source,
                    )
                    self._log(
                        "[REALM-XIX-STATE]",
                        portfolio_health=state.get("portfolio", {}).get("health_score"),
                        portfolio_mode=state.get("portfolio", {}).get("mode"),
                        active_positions=state.get("portfolio", {}).get("total_open_positions"),
                        active_strategies=state.get("strategies", {}).get("candidate_count"),
                        active_realms=len(state.get("active_realms") or []),
                    )
                    return state
                except Exception as fallback_exc:
                    self._log("[REALM-XXIX-ERROR]", reason=fallback_exc.__class__.__name__, action="hydrate_fallback")
            return self.last_state

    def maybe_refresh(self, bot=None, force=False, source="live"):
        now_ts = time.time()
        if force or (now_ts - float(self.last_update_ts or 0.0) >= self.telemetry_interval_seconds):
            return self.update_from_bot(bot=bot, source=source)
        return self.last_state

    def build_summary_text(self, state=None):
        state = state or self.last_state or {}
        consciousness = state.get("consciousness", {}) if isinstance(state, dict) else {}
        operational_summary = state.get("operational_summary") or consciousness.get("summary") or state.get("summary_text")
        portfolio = state.get("portfolio", {}) if isinstance(state, dict) else {}
        market = state.get("market", {}) if isinstance(state, dict) else {}
        strategies = state.get("strategies", {}) if isinstance(state, dict) else {}
        guardian = state.get("guardian", {}) if isinstance(state, dict) else {}
        council = state.get("council", {}) if isinstance(state, dict) else {}
        personality = state.get("personality", {}) if isinstance(state, dict) else {}
        probability = state.get("probability", {}) if isinstance(state, dict) else {}
        risk = state.get("risk", {}) if isinstance(state, dict) else {}
        risk_state = (risk.get("last_global_state", {}) or {}) if isinstance(risk, dict) else {}
        external = state.get("external", {}) if isinstance(state, dict) else {}
        external_state = external.get("event_risk", {}) if isinstance(external, dict) else {}
        evolution = state.get("evolution", {}) if isinstance(state, dict) else {}
        evolution_health = evolution.get("health", {}) if isinstance(evolution, dict) else {}
        evolution_recommendations = evolution.get("recommendations", []) if isinstance(evolution, dict) else []
        command_center = state.get("command_center", {}) if isinstance(state, dict) else {}
        if isinstance(command_center, dict):
            command_center_health = command_center.get("health", {}) or command_center.get("global_health", {}) or {}
        else:
            command_center_health = {}
        lines = [
            "SERAPH ECOSYSTEM STATUS",
            "",
            operational_summary or "AION operational awareness is available.",
            "",
            "Portfolio:",
            f"- Health: {portfolio.get('health_score', 'unknown')}",
            f"- Mode: {str(portfolio.get('mode') or 'unknown').capitalize()}",
            f"- Exposure: ${_round(portfolio.get('total_exposure_dollars'), 2, 0.0)}",
            f"- Drawdown: {_round(risk_state.get('drawdown_pct'), 4, 0.0)}",
            f"- Aggression: {_round(risk_state.get('aggression_level'), 4, 0.0)}",
            "",
            "Council:",
            f"- Mode: {council.get('council_mode', 'unknown')}",
            f"- Confidence: {council.get('council_confidence', 0.0)}",
            "",
            "Market Personality:",
            f"- Top families: {', '.join(_family_names(personality.get('top_families'), 3)) or 'none'}",
            f"- Weak families: {', '.join(_family_names(personality.get('weak_families'), 3)) or 'none'}",
            "",
            "Probability:",
            f"- Top families: {', '.join(_family_names(probability.get('top_families'), 3)) or 'none'}",
            f"- Weak families: {', '.join(_family_names(probability.get('weak_families'), 3)) or 'none'}",
            "",
            "Capital Intelligence:",
            f"- State: {risk_state.get('capital_state', 'unknown')}",
            f"- Soft circuit: {'yes' if risk_state.get('soft_circuit_active') else 'no'}",
            f"- Risk multiplier: {_round(risk_state.get('risk_multiplier'), 4, 1.0)}",
            "",
            "External Awareness:",
            f"- State: {external.get('external_awareness_state', 'unknown')}",
            f"- Event risk: {external_state.get('risk_level', 'NONE')}",
            f"- Affected families: {', '.join(external.get('affected_families') or []) or 'none'}",
            "",
            "Evolution:",
            f"- Health: {evolution_health.get('health_state', 'unknown')}",
            f"- Score: {evolution_health.get('health_score', 0.0)}",
            f"- Recommendations: {len(evolution_recommendations or [])}",
            "",
            "Command Center:",
            f"- Health: {command_center_health.get('global_health_state', 'unknown')}",
            f"- Score: {command_center_health.get('global_health_score', 0.0)}",
            f"- Next actions: {', '.join(state.get('suggested_next_actions') or []) or 'none'}",
            "",
            "Market Climate:",
            f"- Regime: {market.get('regime_label', 'unknown')}",
            f"- Volatility: {market.get('volatility_state', 'unknown')}",
            "",
            "Strategy Evolution:",
            f"- Candidate strategies: {strategies.get('candidate_count', 0)}",
            f"- Elite candidates: {strategies.get('elite_candidates', 0)}",
            "",
            "Guardian:",
            f"- Last veto: {guardian.get('last_veto_reason') or 'none'}",
            f"- Risk mode: {guardian.get('risk_mode', 'normal')}",
        ]
        return "\n".join(lines)

    def get_summary_text(self):
        return self.last_summary_text or self.build_summary_text(self.last_state)

    def report_summary(self):
        state = dict(self.last_state or {})
        print()
        print("SERAPH ECOSYSTEM STATUS")
        print("Portfolio:")
        print(f"- Health: {state.get('portfolio', {}).get('health_score', 'unknown')}")
        print(f"- Mode: {str(state.get('portfolio', {}).get('mode') or 'unknown').capitalize()}")
        print(f"- Exposure: ${_round(state.get('portfolio', {}).get('total_exposure_dollars'), 2, 0.0)}")
        risk_state = ((state.get("risk", {}) or {}).get("last_global_state", {}) or {}) if isinstance(state, dict) else {}
        print(f"- Drawdown: {_round(risk_state.get('drawdown_pct'), 4, 0.0)}")
        print(f"- Aggression: {_round(risk_state.get('aggression_level'), 4, 0.0)}")
        print("Council:")
        print(f"- Mode: {state.get('council', {}).get('council_mode', 'unknown')}")
        print(f"- Confidence: {state.get('council', {}).get('council_confidence', 0.0)}")
        print("Market Personality:")
        print(f"- Top families: {', '.join(_family_names((state.get('personality', {}) or {}).get('top_families'), 3)) or 'none'}")
        print(f"- Weak families: {', '.join(_family_names((state.get('personality', {}) or {}).get('weak_families'), 3)) or 'none'}")
        print("Probability:")
        print(f"- Top families: {', '.join(_family_names((state.get('probability', {}) or {}).get('top_families'), 3)) or 'none'}")
        print(f"- Weak families: {', '.join(_family_names((state.get('probability', {}) or {}).get('weak_families'), 3)) or 'none'}")
        print("Capital Intelligence:")
        print(f"- State: {risk_state.get('capital_state', 'unknown')}")
        print(f"- Soft circuit: {'yes' if risk_state.get('soft_circuit_active') else 'no'}")
        print(f"- Risk multiplier: {_round(risk_state.get('risk_multiplier'), 4, 1.0)}")
        print("External Awareness:")
        print(f"- State: {state.get('external', {}).get('external_awareness_state', 'unknown')}")
        print(f"- Event risk: {(state.get('external', {}).get('event_risk', {}) or {}).get('risk_level', 'NONE')}")
        print(f"- Affected families: {', '.join(state.get('external', {}).get('affected_families') or []) or 'none'}")
        print("Evolution:")
        print(f"- Health: {((state.get('evolution', {}) or {}).get('health', {}) or {}).get('health_state', 'unknown')}")
        print(f"- Score: {((state.get('evolution', {}) or {}).get('health', {}) or {}).get('health_score', 0.0)}")
        print(f"- Recommendations: {len((state.get('evolution', {}) or {}).get('recommendations', []) or [])}")
        print("Command Center:")
        command_center_state = (state.get("command_center", {}) or {})
        command_center_health = (command_center_state.get("health", {}) or command_center_state.get("global_health", {}) or {})
        print(f"- Health: {command_center_health.get('global_health_state', 'unknown')}")
        print(f"- Score: {command_center_health.get('global_health_score', 0.0)}")
        print(f"- Next actions: {', '.join(state.get('suggested_next_actions') or []) or 'none'}")
        print("Market Climate:")
        print(f"- Regime: {state.get('market', {}).get('regime_label', 'unknown')}")
        print(f"- Volatility: {state.get('market', {}).get('volatility_state', 'unknown')}")
        print("Strategy Evolution:")
        print(f"- Candidate strategies: {state.get('strategies', {}).get('candidate_count', 0)}")
        print(f"- Elite candidates: {state.get('strategies', {}).get('elite_candidates', 0)}")
        print("Guardian:")
        print(f"- Last veto: {state.get('guardian', {}).get('last_veto_reason') or 'none'}")
        print(f"- Risk mode: {state.get('guardian', {}).get('risk_mode', 'normal')}")
        return state

    def get_response_payload(self, path):
        state = self.maybe_refresh(source="api")
        if isinstance(state, dict):
            consciousness = self._synthesize_consciousness(state, source="api")
            if isinstance(consciousness, dict) and consciousness and not state.get("consciousness"):
                state = dict(state)
                state["consciousness"] = consciousness
                state["operational_summary"] = consciousness.get("operational_summary") or consciousness.get("summary") or state.get("operational_summary", "")
                state["narrative_summary"] = consciousness.get("narrative") or consciousness.get("summary") or state.get("narrative_summary", "")
                state.setdefault("realm_status_map", {})
                state["realm_status_map"]["XXX"] = {
                    "enabled": bool(getattr(self.consciousness_engine, "enabled", False)),
                    "readiness": consciousness.get("readiness"),
                    "health_state": consciousness.get("health_state"),
                    "health_score": consciousness.get("health_score"),
                }
        telemetry = state.get("telemetry", {}) if isinstance(state, dict) else {}
        status = telemetry.get("status") or state.get("status") or ("live" if state.get("health", {}).get("summary") else "waiting")
        raw_path = str(path or "/seraph/state").strip()
        parsed = urlparse(raw_path)
        path = (parsed.path or "/seraph/state").strip().lower()
        query = parse_qs(parsed.query or "")
        if path == "/api/aion/status":
            self._log("[AION-ROUTE]", path=path, alias="/seraph/live", status=status)
            return self._build_aion_status_payload(state, telemetry, status, path)
        if path == "/seraph/live":
            self._log("[AION-ROUTE]", path=path, alias="/api/aion/status", status=status)
        self._log("[SERAPH-HYDRATED]", path=path, status=status, telemetry="yes" if telemetry else "no")
        if status == "waiting":
            self._log("[SERAPH-ENDPOINT-FALLBACK]", path=path, reason="telemetry_not_ready")
        if path in {"/seraph/state"}:
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return {"ok": True, "path": path, "status": status, "data": state}
        if path == "/seraph/portfolio":
            return {"ok": True, "path": path, "status": status, "data": state.get("portfolio", {})}
        if path == "/seraph/strategies":
            return {"ok": True, "path": path, "status": status, "data": state.get("strategies", {})}
        if path == "/seraph/replays":
            return {"ok": True, "path": path, "status": status, "data": state.get("replay_results", {})}
        if path == "/seraph/health":
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return {"ok": True, "path": path, "status": status, "data": {"health": state.get("health", {}), "command_center": state.get("command_center", {}), "global_health": state.get("command_center_health", {}), "status": status}}
        if path == "/seraph/evolution":
            return {"ok": True, "path": path, "status": status, "data": state.get("evolution", {})}
        if path == "/seraph/recommendations":
            return {"ok": True, "path": path, "status": status, "data": {"recommendations": (state.get("evolution", {}) or {}).get("recommendations", []), "health": (state.get("evolution", {}) or {}).get("health", {})}}
        if path == "/seraph/patterns":
            return {"ok": True, "path": path, "status": status, "data": (state.get("evolution", {}) or {}).get("pattern_summary", {})}
        if path == "/seraph/command-center":
            command = str((query.get("command") or [None])[0] or "").strip().lower()
            if command:
                payload = self.command_center_bridge.route_command(command, context={"path": raw_path}) if self.command_center_bridge is not None else {"ok": False, "reason": "bridge_unavailable"}
                self._log("[REALM-XXVII-SERAPH-ENDPOINT]", path=path, command=command, bridge="yes" if self.command_center_bridge is not None else "no")
                self._log("[SERAPH-COMMAND-CENTER-EXPORT]", command=command, ok=payload.get("ok"), reason=payload.get("reason"))
                return {"ok": bool(payload.get("ok")), "path": path, "status": status, "data": payload}
            self._log("[REALM-XXVII-SERAPH-ENDPOINT]", path=path, command="state", bridge="yes" if self.command_center_bridge is not None else "no")
            self._log("[SERAPH-COMMAND-CENTER-EXPORT]", command="state", ok=True, reason="summary")
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return {"ok": True, "path": path, "status": status, "data": state.get("command_center", {})}
        if path == "/seraph/ecosystem":
            self._log("[REALM-XXVII-SERAPH-ENDPOINT]", path=path, command="ecosystem", bridge="yes" if self.command_center_bridge is not None else "no")
            self._log("[SERAPH-COMMAND-CENTER-EXPORT]", command="ecosystem", ok=True, reason="ecosystem_state")
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return {"ok": True, "path": path, "status": status, "data": state}
        if path == "/seraph/summary":
            self._log("[REALM-XXVII-SERAPH-ENDPOINT]", path=path, command="summary", bridge="yes" if self.command_center_bridge is not None else "no")
            self._log("[SERAPH-COMMAND-CENTER-EXPORT]", command="summary", ok=True, reason="summary_text")
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return {"ok": True, "path": path, "status": status, "data": {"summary": self.last_summary_text, "operational_summary": state.get("operational_summary", ""), "consciousness": state.get("consciousness", {}), "command_center": state.get("command_center", {}), "health": state.get("health", {}), "telemetry": telemetry, "timestamp": state.get("generated_at")}}
        if path == "/seraph/live":
            self._log("[REALM-XXVII-SERAPH-ENDPOINT]", path=path, command="live", bridge="yes" if self.command_center_bridge is not None else "no")
            self._log("[SERAPH-COMMAND-CENTER-EXPORT]", command="live", ok=True, reason="live_state")
            self._log("[SERAPH-ENDPOINT-LIVE]", path=path, status=status)
            return self._build_aion_status_payload(state, telemetry, status, path)
        if path == "/seraph/alerts":
            return {"ok": True, "path": path, "status": status, "data": {"alerts": list(self.recent_alerts), "latest": state.get("alerts", [])}}
        if path == "/seraph/telemetry":
            return {"ok": True, "path": path, "status": status, "data": list(self.recent_telemetry)}
        if path == "/seraph/risk":
            return {"ok": True, "path": path, "status": status, "data": state.get("risk", {})}
        if path in {"/seraph/external", "/seraph/context", "/seraph/catalysts"}:
            return {"ok": True, "path": path, "status": status, "data": state.get("external", {})}
        if path == "/seraph/personality":
            return {"ok": True, "path": path, "status": status, "data": state.get("personality", {})}
        if path == "/seraph/probability":
            return {"ok": True, "path": path, "status": status, "data": state.get("probability", {})}
        if path == "/seraph/council":
            return {"ok": True, "path": path, "status": status, "data": state.get("council", {})}
        if path == "/seraph/agents":
            return {"ok": True, "path": path, "status": status, "data": (state.get("distributed", {}) or {}).get("agent_registry", {})}
        if path == "/seraph/distributed":
            return {"ok": True, "path": path, "status": status, "data": state.get("distributed", {})}
        if path in {"/seraph", "/seraph/", "/seraph/ui"}:
            template_mode = "realm_xxviii" if self.realm_xxviii_enable else "legacy" if self.ui_enable else "disabled"
            self._log(
                "[REALM-XXVIII-SERAPH-UI]",
                enabled="yes" if self.ui_effective_enable else "no",
                mode=template_mode,
                path=path,
            )
            self._log(
                "[SERAPH-LIVE-UI-STATE]",
                mode=template_mode,
                enabled="yes" if self.ui_effective_enable else "no",
                live_mode=state.get("identity", {}).get("portfolio_brain_active"),
                websocket_state=(state.get("websocket", {}) or {}).get("connection_state"),
                risk_state=(state.get("risk", {}) or {}).get("last_global_state", {}).get("capital_state"),
            )
            if not self.ui_effective_enable:
                return {"ok": True, "path": path, "status": status, "data": "ui_disabled"}
            return {"ok": True, "path": path, "status": status, "data": SERAPH_UI_TEMPLATE_XVIII if self.realm_xxviii_enable else SERAPH_UI_TEMPLATE}
        return {"ok": False, "path": path, "status": status, "reason": "not_found"}

    def start_api_server(self):
        if self._server is not None:
            return self._server
        if not (self.enabled or self.ui_effective_enable or self.api_enable) or not self.api_enable:
            self._log("[REALM-XIX-DEGRADED]", reason="api_disabled")
            return None

        interface = self

        class SeraphRequestHandler(BaseHTTPRequestHandler):
            def _write_json(self, payload, status=200):
                body = json.dumps(_json_safe(payload), sort_keys=True, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _write_html(self, payload, status=200):
                body = str(payload or "").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                try:
                    payload = interface.get_response_payload(self.path)
                    if self.path in {"/seraph", "/seraph/", "/seraph/ui"} and interface.ui_effective_enable:
                        self._write_html(payload.get("data", SERAPH_UI_TEMPLATE), status=200)
                        return
                    if payload.get("ok"):
                        self._write_json(payload, status=200)
                    else:
                        self._write_json(payload, status=404)
                except Exception as exc:
                    interface._log("[REALM-XIX-ERROR]", reason=exc.__class__.__name__, path=self.path)
                    self._write_json({"ok": False, "reason": exc.__class__.__name__}, status=500)

            def log_message(self, format, *args):
                return None

        try:
            self._server = ThreadingHTTPServer((self.api_host, self.api_port), SeraphRequestHandler)
            self._server.daemon_threads = True
            thread = threading.Thread(target=self._server.serve_forever, name="seraph-interface-api", daemon=True)
            thread.start()
            self._server_thread = thread
            self._log("[REALM-XIX-FEED]", host=self.api_host, port=self.api_port, ui_enabled="yes" if self.ui_effective_enable else "no")
            return self._server
        except Exception as exc:
            self._server = None
            self._log("[REALM-XIX-ERROR]", reason=exc.__class__.__name__, action="start_api_server")
            return None

    def shutdown(self):
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass
        self._server = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=REALM_XIX_API_PORT)
    parser.add_argument("--host", default=REALM_XIX_API_HOST)
    args = parser.parse_args()
    interface = SeraphInterfaceLayer(api_port=args.port, api_host=args.host, enabled=REALM_XIX_ENABLE, api_enable=args.serve or REALM_XIX_API_ENABLE)
    if args.summary:
        interface.report_summary()
        return
    if args.serve:
        interface.start_api_server()
        print(f"SERAPH interface serving on {args.host}:{args.port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            interface.shutdown()
        return
    interface.report_summary()


if __name__ == "__main__":
    main()
