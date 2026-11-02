"""API de monitoreo y control.

Autenticación en todo salvo `/api/health`, incluido el panel:
    HTTP Basic con PANEL_USER / PANEL_PASSWORD (lo pide el navegador una vez)
    o la cabecera `X-API-Token: <API_TOKEN>` para clientes y scripts.

Paginas:      /  (panel)   /results (resultados)   /config (configuracion)   /logs
Endpoints:    /api/<recurso>  (los nombres antiguos siguen respondiendo como alias)
"""
import base64
import hmac
import json

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from . import presets, settings
from .lab import Lab
from .logs import entries as log_entries

# ---------------------------------------------------------------------------
# Interfaz web. Las paginas comparten CSS y un nucleo de JS; los __PLACEHOLDERS__
# se reemplazan al vuelo en _render_page().
# ---------------------------------------------------------------------------
_CSS = """
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1e222b; --line: #2a2f3a;
    --text: #e6e9ef; --muted: #8b93a1; --green: #35c46a; --red: #e5484d;
    --amber: #e2a336; --blue: #4c8dff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 18px; background: var(--bg); color: var(--text);
         font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  .wrap { max-width: 1040px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  h1 { font-size: 17px; font-weight: 600; margin: 0; letter-spacing: .3px; }
  nav { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  nav a { font-size: 12.5px; padding: 5px 11px; border-radius: 7px; text-decoration: none;
          color: var(--muted); border: 1px solid var(--line); background: var(--panel); }
  nav a:hover { color: var(--text); border-color: #3a4150; }
  nav a.on { color: var(--text); border-color: var(--blue); background: var(--panel2); }
  .pill { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--panel2);
          border: 1px solid var(--line); color: var(--muted); }
  .pill.on { color: #0d1b12; background: var(--green); border-color: transparent; font-weight: 600;
             animation: pulse 2.4s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .6; } }
  .pill.off { color: #2a0e10; background: var(--red); border-color: transparent; font-weight: 600; }
  .pill.warn { color: #241a05; background: var(--amber); border-color: transparent; font-weight: 600; }
  a.pill { text-decoration: none; }
  a.pill:hover { color: var(--text); border-color: var(--blue); }
  .spacer { flex: 1; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  button { font: inherit; font-size: 13px; padding: 6px 13px; border-radius: 7px; cursor: pointer;
           border: 1px solid var(--line); background: var(--panel2); color: var(--text);
           transition: background .12s, border-color .12s, opacity .12s; }
  button:hover:not(:disabled) { background: #262b36; border-color: #3a4150; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  button.primary { background: var(--green); border-color: transparent; color: #0b1a11; font-weight: 600; }
  button.primary:hover:not(:disabled) { background: #3ed577; }
  button.danger { background: var(--red); border-color: transparent; color: #fff; font-weight: 600; }
  button.danger:hover:not(:disabled) { background: #f05a5f; }
  button.warn { background: var(--amber); border-color: transparent; color: #241a05; font-weight: 600; }
  button.ghost { background: transparent; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 14px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 11px 13px; }
  .card .k { font-size: 11px; text-transform: uppercase; letter-spacing: .7px; color: var(--muted); }
  .card .v { font-size: 19px; font-weight: 600; margin-top: 3px; font-variant-numeric: tabular-nums; }
  .card .s { font-size: 11.5px; color: var(--muted); margin-top: 2px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 13px; margin-bottom: 14px; }
  .panel h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .7px; color: var(--muted);
              margin: 0 0 9px; font-weight: 600; }
  .train-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .progress { height: 6px; background: var(--panel2); border: 1px solid var(--line); border-radius: 999px;
              overflow: hidden; margin-top: 9px; }
  .progress-fill { height: 100%; width: 0; background: var(--blue); transition: width .25s ease; }
  .progress-fill.active { background: linear-gradient(90deg, var(--blue), var(--green)); }
  .stat-row { display: flex; gap: 26px; flex-wrap: wrap; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--line);
           font-variant-numeric: tabular-nums; }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  tr:last-child td { border-bottom: none; }
  .pos { color: var(--green); } .neg { color: var(--red); } .dim { color: var(--muted); }
  .empty { color: var(--muted); font-size: 13px; }
  footer { color: var(--muted); font-size: 11.5px; text-align: center; margin-top: 6px; }
  .cfg-note { color: var(--muted); font-size: 12px; margin-bottom: 10px; line-height: 1.5; }
  .cfg-body { display: flex; flex-direction: column; gap: 14px; }
  .cfg-group h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .7px; color: var(--blue);
                  margin: 0 0 6px; font-weight: 600; }
  .cfg-row { display: grid; grid-template-columns: 220px 1fr; gap: 10px; align-items: start;
             padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,.03); }
  .cfg-row label { font-size: 13px; padding-top: 5px; }
  .cfg-in input[type=text], .cfg-in input[type=number], .cfg-in select {
    font: inherit; font-size: 13px; width: 100%; max-width: 420px; padding: 5px 8px;
    background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: 6px; }
  .cfg-in input[type=checkbox] { width: 16px; height: 16px; margin-top: 7px; accent-color: var(--green); }
  .cfg-in input.dirty, .cfg-in select.dirty { border-color: var(--amber); }
  .cfg-help { color: var(--muted); font-size: 11.5px; margin-top: 3px; }
  .cfg-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
  .tag { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; padding: 1px 6px;
         border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); color: var(--muted); }
  .tag.risk { background: transparent; border-color: var(--red); color: var(--red); }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .chart { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px 4px; }
  .chart-head { display: flex; justify-content: space-between; gap: 8px; font-size: 11.5px;
                text-transform: uppercase; letter-spacing: .6px; color: var(--muted); margin-bottom: 4px; }
  .chart-head .info { text-transform: none; letter-spacing: 0; }
  .chart-box { min-height: 150px; }
  .chart-box svg { display: block; width: 100%; height: auto; }
  @media (max-width: 780px) { .charts { grid-template-columns: 1fr; } }
  code { background: var(--panel2); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .verdict { display: flex; gap: 12px; align-items: flex-start; padding: 13px 15px; border-radius: 9px;
             border: 1px solid var(--line); background: var(--panel2); }
  .verdict .dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 6px; flex: 0 0 auto; background: var(--muted); }
  .verdict h3 { margin: 0 0 5px; font-size: 15px; font-weight: 600; }
  .verdict p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
  .verdict ul { margin: 8px 0 0; padding-left: 18px; color: var(--muted); font-size: 12.5px; line-height: 1.6; }
  .verdict.ok { border-color: rgba(53,196,106,.45); } .verdict.ok .dot { background: var(--green); }
  .verdict.bad { border-color: rgba(229,72,77,.4); } .verdict.bad .dot { background: var(--red); }
  .verdict.idle { border-color: rgba(226,163,54,.4); } .verdict.idle .dot { background: var(--amber); }
  .gates { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }
  .gate { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
  .gate .name { color: var(--muted); font-size: 10.5px; text-transform: uppercase; letter-spacing: .6px; }
  .gate .val { font-variant-numeric: tabular-nums; margin-top: 3px; font-size: 14px; font-weight: 600; }
  .logbox { font-family: ui-monospace, "Consolas", monospace; font-size: 11.5px; line-height: 1.55;
            max-height: 430px; overflow-y: auto; background: #12151b; border: 1px solid var(--line);
            border-radius: 8px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; }
  .logbox .row { border-bottom: 1px solid rgba(255,255,255,.03); padding: 1px 0; }
  .logbox .ts { color: #6b7280; margin-right: 7px; }
  .logbox .lvl { display: inline-block; min-width: 46px; font-weight: 600; }
  .logbox .INF { color: #6ea8fe; } .logbox .WAR { color: var(--amber); }
  .logbox .ERR { color: var(--red); } .logbox .DEB { color: #6b7280; }
  .logbox .src { color: #8b93a1; margin-right: 6px; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 9px; }
  .filters select, .filters input[type=text] { font: inherit; font-size: 12.5px; padding: 5px 8px;
    border-radius: 6px; background: var(--panel2); color: var(--text); border: 1px solid var(--line); }
  .filters input[type=text] { min-width: 210px; }
  label.chk { font-size: 12.5px; color: var(--muted); display: flex; align-items: center; gap: 5px; }
  @media (max-width: 620px) { .cfg-row { grid-template-columns: 1fr; gap: 2px; } }
"""

_JS_CORE = """
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CAPITAL = __CAPITAL__;

function fmt(n, d) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toFixed(d === undefined ? 2 : d) : "-";
}

function signed(n, d) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  return (v >= 0 ? "+" : "") + v.toFixed(d === undefined ? 2 : d);
}

function fmtDur(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  if (sec < 60) return sec + " s";
  const m = Math.floor(sec / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d > 0) return d + "d " + (h % 24) + "h";
  if (h > 0) return h + "h " + (m % 60) + "m";
  return m + "m";
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({ "X-API-Token": TOKEN }, opts.headers || {});
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let msg = path + " -> HTTP " + res.status;
    try {
      const d = await res.json();
      if (d && d.detail) msg = Array.isArray(d.detail) ? d.detail.join(" · ") : String(d.detail);
    } catch (e) { /* respuesta sin JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function setPill(el, text, cls) {
  if (!el) return;
  el.textContent = text;
  el.className = "pill" + (cls ? " " + cls : "");
}

function tsLabel(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" }) + " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function tsFull(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "-";
}

// ---------------------------------------------------------------- gráficas (SVG sin dependencias)
function svgChart(ys, opt) {
  opt = opt || {};
  const W = 620, H = 170, PL = 48, PR = 12, PT = 10, PB = 18;
  const iw = W - PL - PR, ih = H - PT - PB;
  const dg = opt.digits === undefined ? 2 : opt.digits;
  const vals = ys.filter((v) => Number.isFinite(v));
  if (!vals.length) return "";
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if (opt.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (opt.base !== undefined) { lo = Math.min(lo, opt.base); hi = Math.max(hi, opt.base); }
  if (!(hi > lo)) { const m = Math.abs(lo) || 1; lo -= m * 0.01; hi += m * 0.01; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const X = (i) => PL + (ys.length > 1 ? i / (ys.length - 1) * iw : iw / 2);
  const Y = (v) => PT + ih - (v - lo) / (hi - lo) * ih;

  let out = "";
  for (let k = 0; k <= 3; k++) {
    const v = lo + (hi - lo) * k / 3, y = Y(v).toFixed(1);
    out += '<line x1="' + PL + '" y1="' + y + '" x2="' + (W - PR) + '" y2="' + y +
      '" stroke="#2a2f3a" stroke-width="1"/>' +
      '<text x="' + (PL - 6) + '" y="' + (Y(v) + 3.5).toFixed(1) +
      '" fill="#8b93a1" font-size="10" text-anchor="end">' + fmt(v, dg) + "</text>";
  }
  if (opt.base !== undefined) {
    const y = Y(opt.base).toFixed(1);
    out += '<line x1="' + PL + '" y1="' + y + '" x2="' + (W - PR) + '" y2="' + y +
      '" stroke="#8b93a1" stroke-width="1" stroke-dasharray="4 4" opacity=".5"/>';
  }
  let d = "", open = false;
  ys.forEach((v, i) => {
    if (!Number.isFinite(v)) { open = false; return; }
    d += (open ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
    open = true;
  });
  if (opt.area) {
    let first = -1, last = -1;
    ys.forEach((v, i) => { if (Number.isFinite(v)) { if (first < 0) first = i; last = i; } });
    if (first >= 0 && last > first) {
      d += "L" + X(last).toFixed(1) + " " + (PT + ih) + " L" + X(first).toFixed(1) + " " + (PT + ih) + " Z";
    }
  }
  out += '<path d="' + d + '" fill="' + (opt.area ? "rgba(76,141,255,.13)" : "none") +
    '" stroke="' + (opt.color || "#4c8dff") + '" stroke-width="1.8" stroke-linejoin="round"/>';
  if (opt.dots && opt.dots.length === ys.length) {
    ys.forEach((v, i) => {
      if (!Number.isFinite(v)) return;
      out += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(v).toFixed(1) + '" r="2.1" fill="' +
        (opt.dots[i] || opt.color || "#4c8dff") + '"/>';
    });
  } else {
    for (let i = ys.length - 1; i >= 0; i--) {
      if (!Number.isFinite(ys[i])) continue;
      out += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(ys[i]).toFixed(1) + '" r="2.6" fill="' +
        (opt.color || "#4c8dff") + '"/>';
      break;
    }
  }
  if (opt.left) out += '<text x="' + PL + '" y="' + (H - 4) + '" fill="#8b93a1" font-size="10">' + opt.left + "</text>";
  if (opt.right) out += '<text x="' + (W - PR) + '" y="' + (H - 4) +
    '" fill="#8b93a1" font-size="10" text-anchor="end">' + opt.right + "</text>";
  return '<svg viewBox="0 0 ' + W + " " + H + '" role="img">' + out + "</svg>";
}

async function refreshCharts() {
  if (!$("chartEquity") && !$("chartOos")) return;
  try {
    const rows = await api("/api/equity?limit=1440");
    const ys = (rows || []).map((r) => Number(r.equity));
    if (!ys.length) {
      $("chartEquity").innerHTML = '<span class="empty">todavía sin puntos de equity</span>';
      $("eqInfo").textContent = "";
    } else {
      $("chartEquity").innerHTML = svgChart(ys, { digits: 2, color: "#4c8dff", area: true,
        base: Number(CAPITAL) || undefined, left: tsLabel(rows[0].ts), right: tsLabel(rows[rows.length - 1].ts) });
      const lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys);
      $("eqInfo").textContent = "último " + fmt(ys[ys.length - 1]) + " · mín " + fmt(lo) + " · máx " + fmt(hi) +
        " · " + ys.length + " pts";
    }
  } catch (e) { /* silencioso */ }

  if (!$("chartOos")) return;
  try {
    const hist = await api("/api/learning/history?limit=120");
    if (!hist || !hist.length) {
      $("chartOos").innerHTML = '<span class="empty">sin historial todavía: se graba en cada walk-forward</span>';
      $("oosInfo").textContent = "";
      return;
    }
    const ys = hist.map((r) => {
      const wf = (r.report && r.report.wf_oos) || {};
      return wf.sharpe === undefined || wf.sharpe === null ? null : Number(wf.sharpe);
    });
    const ok = hist.map((r) => !String(r.decision || "").startsWith("sin_ventaja"));
    $("chartOos").innerHTML = svgChart(ys, { digits: 2, color: "#35c46a", zero: true,
      dots: ok.map((o) => (o ? "#35c46a" : "#e5484d")),
      left: tsLabel(hist[0].ts), right: tsLabel(hist[hist.length - 1].ts) });
    const pass = ok.filter(Boolean).length;
    $("oosInfo").textContent = hist.length + " · pasan " + pass + " · último " + String(hist[hist.length - 1].decision || "-");
  } catch (e) { /* silencioso */ }
}

function statePills(s) {
  setPill($("connPill"), "online", "");
  setPill($("statePill"), s.running ? (s.halted ? "pausado" : "ejecutando") : "detenido",
          s.running ? (s.halted ? "warn" : "on") : "off");
  const mp = $("modePill");
  if (mp) mp.textContent = s.mode + (s.learning_enabled ? " · aprendizaje" : " · estrategia fija");
}

async function refreshPills() {
  try {
    const s = await api("/api/status");
    statePills(s);
    return s;
  } catch (e) {
    setPill($("connPill"), "sin conexión", "off");
    return null;
  }
}
"""

_BASE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · AutoTrader</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AutoTrader</h1>
    <span class="pill" id="statePill">-</span>
    <span class="pill" id="modePill">__MODE__</span>
    <span class="spacer"></span>
    <span class="pill" id="connPill">conectando</span>
  </header>
  <nav>
    <a href="/" __NAV_DASH__>Panel</a>
    <a href="/results" __NAV_RESULTS__>Resultados</a>
    <a href="/config" __NAV_CONFIG__>Configuración</a>
    <a href="/logs" __NAV_LOGS__>Logs</a>
    <a href="/docs" target="_blank" rel="noopener">API</a>
  </nav>
__BODY__
  <footer>__SYMBOLS__ · capital inicial __CAPITAL__ · __BIND__ · token en <code>.env</code></footer>
</div>
<script>
const TOKEN = __TOKEN__;
__CORE__
__SCRIPT__
</script>
</body>
</html>
"""

# ---------------------------------------------------------------- panel (dashboard)
_BODY_DASH = """
  <div class="bar">
    <button class="primary" id="btnStart" onclick="startEngine()">Iniciar</button>
    <button class="danger" id="btnStop" onclick="stopEngine()">Detener</button>
    <button class="warn" id="btnPause" onclick="togglePause()">Pausar</button>
    <button onclick="retrain()">Reentrenar</button>
    <button class="ghost" onclick="closeAll()">Cerrar posiciones</button>
    <span class="spacer"></span>
    <a class="pill" href="/results">resultados →</a>
  </div>

  <div class="panel">
    <h2>Resumen</h2>
    <div id="miniVerdict" class="empty">cargando...</div>
  </div>

  <div class="grid">
    <div class="card"><div class="k">Equity</div><div class="v" id="mEquity">-</div><div class="s" id="mPnl">-</div></div>
    <div class="card"><div class="k">Cash</div><div class="v" id="mCash">-</div></div>
    <div class="card"><div class="k">Drawdown</div><div class="v" id="mDd">-</div></div>
    <div class="card"><div class="k">Posiciones</div><div class="v" id="mPos">-</div></div>
    <div class="card"><div class="k">Trades hoy</div><div class="v" id="mTrades">-</div></div>
  </div>

  <div class="panel">
    <h2>Curvas</h2>
    <div class="charts">
      <div class="chart">
        <div class="chart-head"><span>Equity</span><span class="info dim" id="eqInfo">-</span></div>
        <div id="chartEquity" class="chart-box"><span class="empty">cargando...</span></div>
      </div>
      <div class="chart">
        <div class="chart-head"><span>Sharpe OOS por entrenamiento</span><span class="info dim" id="oosInfo">-</span></div>
        <div id="chartOos" class="chart-box"><span class="empty">cargando...</span></div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>Entrenamiento (walk-forward)</h2>
    <div class="train-row">
      <span class="pill" id="trainPill">inactivo</span>
      <span id="trainText" class="dim">sin entrenamiento en curso</span>
      <span class="spacer"></span>
      <span id="trainCount" class="dim"></span>
      <button id="btnTrainMode" class="ghost" onclick="toggleTrainMode()">Aprender sin parar</button>
      <span id="nextTrain" class="dim">-</span>
    </div>
    <div class="progress"><div class="progress-fill" id="trainBar"></div></div>
    <div id="lastDecision" class="dim" style="margin-top:9px;">todavía no hay reporte</div>
    <table style="margin-top:10px;">
      <thead><tr><th>Fold</th><th>Estrategia</th><th>Parámetros</th><th>Sharpe train</th><th>Sharpe test</th><th>Trades test</th></tr></thead>
      <tbody id="foldsBody"><tr><td colspan="6" class="empty">sin datos</td></tr></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Campeones por símbolo</h2>
    <table>
      <thead><tr><th>Símbolo</th><th>Estrategia</th><th>Parámetros</th><th>Riesgo</th></tr></thead>
      <tbody id="champBody"><tr><td colspan="4" class="empty">sin campeón (en liquidez)</td></tr></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Posiciones abiertas</h2>
    <table>
      <thead><tr><th>Símbolo</th><th>Lado</th><th>Cant.</th><th>Entrada</th><th>Precio</th><th>PnL no real.</th></tr></thead>
      <tbody id="posBody"><tr><td colspan="6" class="empty">sin posiciones</td></tr></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Actividad del motor</h2>
    <div class="stat-row">
      <div><span class="dim">Ciclo</span> <strong id="aTick">-</strong></div>
      <div><span class="dim">Último tick</span> <strong id="aLast">-</strong></div>
      <div><span class="dim">Intervalo</span> <strong id="aPoll">-</strong></div>
      <div><span class="dim">Precio</span> <strong id="aPrice">-</strong></div>
      <div><span class="dim">Próximo entrenamiento</span> <strong id="aNext">-</strong></div>
    </div>
  </div>
"""

_JS_DASH = """
let paused = false, lastTrainStatus = "idle", trainMode = "scheduled";

function renderStatus(s) {
  statePills(s);

  $("mEquity").textContent = fmt(s.equity);
  const pnl = Number(s.equity) - Number(CAPITAL);
  const pct = Number(CAPITAL) ? pnl / Number(CAPITAL) * 100 : 0;
  $("mPnl").innerHTML = "<span class='" + (pnl >= 0 ? "pos" : "neg") + "'>" + signed(pnl) +
    " (" + signed(pct, 2) + " %)</span>";
  $("mCash").textContent = fmt(s.cash);
  const dd = Number(s.drawdown_pct || 0);
  $("mDd").textContent = fmt(dd) + " %";
  $("mDd").className = "v " + (dd < 5 ? "pos" : dd < 10 ? "dim" : "neg");
  const pos = Object.keys(s.positions || {});
  $("mPos").textContent = pos.length;
  $("mTrades").textContent = s.trades_today;

  $("btnStart").disabled = !!s.running;
  $("btnStop").disabled = !s.running;
  $("btnPause").disabled = !s.running;
  paused = !!s.halted;
  $("btnPause").textContent = paused ? "Reanudar" : "Pausar";

  const body = $("posBody");
  body.innerHTML = pos.length
    ? pos.map((sym) => {
        const p = s.positions[sym];
        const pnl = Number(p.unrealized);
        return "<tr><td>" + sym + "</td><td>" + (p.side > 0 ? "long" : "short") + "</td><td>" +
          fmt(p.qty, 6) + "</td><td>" + fmt(p.entry) + "</td><td>" + fmt(p.price) + "</td><td class='" +
          (pnl >= 0 ? "pos" : "neg") + "'>" + signed(pnl) + "</td></tr>";
      }).join("")
    : '<tr><td colspan="6" class="empty">sin posiciones</td></tr>';

  const champs = Object.entries(s.champions || {}).filter(([, c]) => c);
  $("champBody").innerHTML = champs.length
    ? champs.map(([sym, c]) => {
        const params = c && c.params ? JSON.stringify(c.params) : "-";
        const risk = s.risk_mult && s.risk_mult[sym] !== undefined ? fmt(s.risk_mult[sym], 2) : "-";
        return "<tr><td>" + sym + "</td><td>" + ((c && c.strategy) || "-") + "</td><td class='dim'>" +
          params + "</td><td>" + risk + "</td></tr>";
      }).join("")
    : '<tr><td colspan="4" class="empty">sin campeón (en liquidez)</td></tr>';

  const last = String(s.last_train_reason || "");
  $("miniVerdict").innerHTML = !s.running
    ? "El motor está detenido: no hay ciclos ni datos nuevos. <a href='/results'>ver resultados →</a>"
    : s.halted
      ? "Pausado por el kill switch: " + (s.halt_reason || "motivo no registrado") + ". <a href='/results'>ver resultados →</a>"
      : champs.length
        ? "Operando con campeón · " + champs.map(([k, c]) => k + " (" + (c.strategy || "-") + ")").join(", ") +
          ". <a href='/results'>ver resultados →</a>"
        : "En liquidez: ningún walk-forward pasa las puertas, así que no abre posiciones. <a href='/results'>ver por qué →</a>";

  // --- entrenamiento en vivo ---
  const t = s.training || {};
  lastTrainStatus = t.status || "idle";
  const tp = $("trainPill"), bar = $("trainBar");
  if (t.status === "running") {
    setPill(tp, "entrenando", "warn");
    const total = Number(t.total) || 0, done = Number(t.done) || 0;
    bar.style.width = (total > 0 ? Math.max(4, Math.round(done / total * 100)) : 10) + "%";
    bar.className = "progress-fill active";
    let txt = t.stage || "entrenando";
    if (t.symbol) txt += " · " + t.symbol;
    if (t.fold) txt += " · fold " + t.fold + "/" + t.folds;
    if (total) txt += " · " + done + "/" + total;
    if (t.reason) txt += " · " + t.reason;
    $("trainText").textContent = txt;
  } else if (t.status === "error") {
    setPill(tp, "error", "off");
    bar.style.width = "100%";
    bar.className = "progress-fill";
    $("trainText").textContent = "el último entrenamiento falló (ver Logs)";
  } else {
    setPill(tp, "inactivo", "");
    bar.style.width = "0%";
    bar.className = "progress-fill";
    $("trainText").textContent = t.finished
      ? "último: " + (t.reason || "manual") + " · " + (t.seconds || 0) + " s · hace " +
        fmtDur(Date.now() / 1000 - t.finished)
      : "sin entrenamiento en curso";
  }

  let next = "-";
  if (!s.learning_enabled) {
    next = "aprendizaje desactivado (estrategia fija)";
  } else if (s.train_mode === "continuous") {
    next = "modo continuo · sin espera" + (s.train_min_new_bars ? " · necesita " + s.train_min_new_bars + " vela(s)" : "");
  } else if (s.next_train) {
    const left = Number(s.next_train) - Date.now() / 1000;
    next = (left > 0 ? "en " + fmtDur(left) : "pendiente") + (s.retrain_interval_hours ? " · cada " + s.retrain_interval_hours + " h" : "");
  } else {
    next = s.last_train ? "reentrenamiento programado desactivado" : "aún sin entrenar";
  }
  $("nextTrain").textContent = next;
  $("aNext").textContent = next;

  trainMode = s.train_mode || "scheduled";
  const on = trainMode === "continuous";
  const mb = $("btnTrainMode");
  mb.textContent = on ? "Continuo: ON · parar" : "Aprender sin parar";
  mb.className = on ? "warn" : "ghost";
  let cnt = (s.trainings || 0) + " entrenamientos";
  if (on && s.bars_since_train !== undefined) cnt += " · " + s.bars_since_train + " velas nuevas";
  $("trainCount").textContent = s.learning_enabled ? cnt : "";

  $("aTick").textContent = s.tick || 0;
  $("aLast").textContent = s.last_tick ? "hace " + fmtDur(Date.now() / 1000 - s.last_tick) : "-";
  $("aPoll").textContent = (s.poll_seconds || 0) + " s";
  const px = Object.values(s.prices || {})[0];
  $("aPrice").textContent = px === undefined ? "-" : fmt(px, 4);
}

async function refresh() {
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    setPill($("connPill"), "sin conexión", "off");
  }
}

async function refreshLearning() {
  try {
    const d = await api("/api/learning");
    const reports = Object.entries(d.reports || {});
    if (!reports.length) {
      $("lastDecision").textContent = "todavía no hay reporte (el primer entrenamiento corre al arrancar)";
      $("foldsBody").innerHTML = '<tr><td colspan="6" class="empty">sin datos</td></tr>';
      return;
    }
    $("lastDecision").innerHTML = reports.map(([sym, r]) => {
      const wf = r.wf_oos || {};
      let s = "<strong>" + sym + "</strong> · " + (r.decision || r.status);
      if (r.gate_fails && r.gate_fails.length) {
        s += " · <span class='neg'>" + r.gate_fails.join(", ") + "</span>";
      }
      if (wf.trades !== undefined) {
        s += " · OOS: trades " + wf.trades + ", sharpe " + wf.sharpe + ", pf " + wf.pf + ", dd " + wf.max_dd_pct + "%";
      }
      if (wf.excess_vs_bh_pct !== undefined) {
        s += " · vs buy&hold: " + signed(wf.excess_vs_bh_pct) + " pp";
      }
      if (r.at) s += " · " + tsFull(r.at);
      return "<div style='margin-bottom:3px'>" + s + "</div>";
    }).join("");

    const folds = (reports[0][1] || {}).folds || [];
    $("foldsBody").innerHTML = folds.length
      ? folds.map((f, i) => {
          const m = f.test || {};
          return "<tr><td>" + (i + 1) + "</td><td>" + f.strategy + "</td><td class='dim'>" +
            JSON.stringify(f.params) + "</td><td>" + fmt(f.train_sharpe, 2) + "</td><td class='" +
            (Number(m.sharpe) >= 0 ? "pos" : "neg") + "'>" + fmt(m.sharpe, 2) + "</td><td>" +
            (m.trades === undefined ? "-" : m.trades) + "</td></tr>";
        }).join("")
      : '<tr><td colspan="6" class="empty">sin folds (datos insuficientes o aprendizaje desactivado)</td></tr>';
  } catch (e) { /* silencioso */ }
}

async function post(path) {
  await api(path, { method: "POST" });
  await refresh();
}

const startEngine = () => post("/api/start").catch((e) => alert(e.message));
const stopEngine = () => post("/api/stop").catch((e) => alert(e.message));
const closeAll = () => post("/api/close_all").catch((e) => alert(e.message));
const togglePause = () => post(paused ? "/api/resume" : "/api/halt").catch((e) => alert(e.message));

async function toggleTrainMode() {
  const next = trainMode === "continuous" ? "scheduled" : "continuous";
  try {
    await post("/api/train_mode?mode=" + next);
    await refreshLearning();
  } catch (e) { alert(e.message); }
}

async function retrain() {
  try {
    await api("/api/retrain", { method: "POST" });
    const t0 = Date.now();
    let sawRunning = false;
    while (Date.now() - t0 < 20000) {
      await refresh();
      await refreshLearning();
      if (lastTrainStatus === "running") sawRunning = true;
      if (sawRunning && lastTrainStatus !== "running") break;
      await sleep(150);
    }
  } catch (e) { alert(e.message); }
}

refresh();
refreshLearning();
refreshCharts();
setInterval(refresh, 1500);
setInterval(refreshLearning, 3000);
setInterval(refreshCharts, 10000);
"""

# ---------------------------------------------------------------- resultados
_BODY_RESULTS = """
  <div class="panel">
    <h2>Qué está pasando</h2>
    <div id="verdict" class="verdict idle">
      <div class="dot"></div>
      <div><h3>cargando...</h3><p></p><ul></ul></div>
    </div>
  </div>

  <div class="grid">
    <div class="card"><div class="k">PnL total</div><div class="v" id="rPnl">-</div><div class="s" id="rPnlPct">-</div></div>
    <div class="card"><div class="k">Equity</div><div class="v" id="rEquity">-</div><div class="s" id="rPeak">-</div></div>
    <div class="card"><div class="k">Trades cerrados</div><div class="v" id="rTrades">-</div><div class="s" id="rWins">-</div></div>
    <div class="card"><div class="k">Winrate</div><div class="v" id="rWinrate">-</div></div>
    <div class="card"><div class="k">Profit factor</div><div class="v" id="rPf">-</div><div class="s" id="rBestWorst">-</div></div>
    <div class="card"><div class="k">Drawdown</div><div class="v" id="rDd">-</div><div class="s">máximo histórico</div></div>
  </div>

  <div class="panel">
    <h2>Curvas</h2>
    <div class="charts">
      <div class="chart">
        <div class="chart-head"><span>Equity</span><span class="info dim" id="eqInfo">-</span></div>
        <div id="chartEquity" class="chart-box"><span class="empty">cargando...</span></div>
      </div>
      <div class="chart">
        <div class="chart-head"><span>Sharpe OOS por entrenamiento</span><span class="info dim" id="oosInfo">-</span></div>
        <div id="chartOos" class="chart-box"><span class="empty">cargando...</span></div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>Reglas para aceptar un campeón</h2>
    <div class="gates" id="gates"><span class="empty">cargando...</span></div>
    <div class="cfg-note" style="margin:9px 0 0">
      Un entrenamiento solo se convierte en campeón si cumple <strong>todas</strong> las reglas a la vez,
      medidas sobre las ventanas fuera de muestra (OOS). Si no las cumple, el bot sigue en liquidez:
      no perder dinero también es un resultado. Los umbrales se cambian en <a href="/config">Configuración</a>.
    </div>
  </div>

  <div class="panel">
    <h2>Historial de entrenamientos</h2>
    <div class="cfg-note">Cada fila es un walk-forward completo sobre las velas disponibles en ese momento.</div>
    <table>
      <thead><tr><th>Fecha</th><th>Símbolo</th><th>Decisión</th><th>Sharpe OOS</th><th>PF</th>
        <th>Trades</th><th>DD %</th><th>vs B&amp;H</th><th>Motivos</th></tr></thead>
      <tbody id="histBody"><tr><td colspan="9" class="empty">sin historial</td></tr></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Trades cerrados</h2>
    <table>
      <thead><tr><th>Fecha</th><th>Símbolo</th><th>Lado</th><th>Entrada</th><th>Salida</th>
        <th>PnL</th><th>Motivo</th><th>Estrategia</th></tr></thead>
      <tbody id="tradesBody"><tr><td colspan="8" class="empty">sin trades</td></tr></tbody>
    </table>
  </div>
"""

_JS_RESULTS = """
function renderVerdict(s, learning, hist) {
  const box = $("verdict");
  const reports = Object.entries(learning.reports || {});
  const champs = Object.entries(s.champions || {}).filter(([, c]) => c && c.strategy);
  const last = reports.map(([sym, r]) => ({ sym, r })).sort((a, b) => (b.r.at || 0) - (a.r.at || 0))[0];
  let cls = "idle", title = "", text = "";
  const why = [];

  if (!s.running) {
    title = "El motor está detenido";
    text = "No se evalúan velas nuevas ni se entrena, así que el estado no cambia. Pulsa Iniciar en el panel.";
  } else if (s.halted) {
    cls = "bad";
    title = "Pausado por el kill switch";
    text = (s.halt_reason || "motivo no registrado") +
      ". El bot no abre posiciones hasta que lo reanudes desde el panel; revisa Logs para ver qué lo disparó.";
  } else if (!champs.length) {
    title = "En liquidez: todavía no hay campeón";
    text = "El bot revisa el mercado cada " + (s.poll_seconds || "?") + " s y no abre nada porque ningún " +
      "walk-forward pasó las reglas. No es un fallo: sin ventaja medida sobre datos que no entrenó, " +
      "no arriesga capital. Cada " + (s.retrain_interval_hours || "?") + " h vuelve a intentarlo.";
    if (last) {
      title += " · último: " + (last.r.decision || "sin decisión");
      (last.r.gate_fails || []).forEach((f) => why.push(last.sym + " · " + f));
      const wf = last.r.wf_oos || {};
      if (wf.sharpe !== undefined) {
        why.push(last.sym + " · OOS sobre " + (wf.trades === undefined ? "?" : wf.trades) + " trades, " +
          (last.r.folds ? last.r.folds.length : "?") + " folds, dd " + fmt(wf.max_dd_pct) + " %");
      }
    } else {
      why.push("Sin reportes todavía: el primero corre al arrancar.");
    }
  } else {
    cls = "ok";
    title = "Operando con campeón";
    text = champs.map(([k, c]) => k + " · " + c.strategy).join(" · ") +
      ". Cada ciclo evalúa la señal del campeón y abre posición si aparece. Los trades aparecen abajo.";
    const wf = (last && last.r.wf_oos) || {};
    if (wf.sharpe !== undefined) why.push("OOS del campeón: sharpe " + fmt(wf.sharpe) + ", pf " + fmt(wf.pf));
  }

  if (s.running && !s.halted) {
    if (s.train_mode === "continuous") why.push("Aprendizaje continuo: reentrena con velas nuevas sin esperar.");
    else if (s.next_train) {
      const left = Number(s.next_train) - Date.now() / 1000;
      why.push("Próximo walk-forward " + (left > 0 ? "en " + fmtDur(left) : "pendiente") +
        (s.retrain_interval_hours ? " (cada " + s.retrain_interval_hours + " h)" : ""));
    }
  }
  why.push((s.trainings || 0) + " entrenamientos acumulados · " + (hist ? hist.length : 0) + " en el historial");

  box.className = "verdict " + cls;
  box.innerHTML = '<div class="dot"></div><div><h3>' + title + "</h3><p>" + text + "</p>" +
    (why.length ? "<ul>" + why.map((w) => "<li>" + w + "</li>").join("") + "</ul>" : "") + "</div>";
}

function renderMetrics(s, trades) {
  const pnl = Number(s.equity) - Number(CAPITAL);
  const pct = Number(CAPITAL) ? pnl / Number(CAPITAL) * 100 : 0;
  $("rPnl").textContent = signed(pnl);
  $("rPnl").className = "v " + (pnl >= 0 ? "pos" : "neg");
  $("rPnlPct").textContent = signed(pct, 2) + " % sobre " + fmt(CAPITAL);
  $("rEquity").textContent = fmt(s.equity);
  $("rPeak").textContent = "pico " + fmt(s.peak);
  $("rDd").textContent = fmt(s.drawdown_pct) + " %";

  const closed = (trades || []).filter((t) => t && t.exit !== null && t.exit !== undefined);
  const wins = closed.filter((t) => Number(t.pnl) > 0);
  const losses = closed.filter((t) => Number(t.pnl) <= 0);
  const gross = wins.reduce((a, t) => a + Number(t.pnl), 0);
  const grossLoss = Math.abs(losses.reduce((a, t) => a + Number(t.pnl), 0));
  $("rTrades").textContent = closed.length;
  $("rWins").textContent = wins.length + " ganan · " + losses.length + " pierden";
  $("rWinrate").textContent = closed.length ? fmt(wins.length / closed.length * 100, 1) + " %" : "-";
  $("rPf").textContent = closed.length ? (grossLoss > 0 ? fmt(gross / grossLoss) : (gross > 0 ? "∞" : "0.00")) : "-";
  if (closed.length) {
    const best = Math.max.apply(null, closed.map((t) => Number(t.pnl)));
    const worst = Math.min.apply(null, closed.map((t) => Number(t.pnl)));
    $("rBestWorst").textContent = "mejor " + signed(best) + " · peor " + signed(worst);
  } else {
    $("rBestWorst").textContent = "";
  }
}

function renderGates(c) {
  const rows = [
    ["Trades OOS mínimos", "≥ " + (c.min_oos_trades === undefined ? "?" : c.min_oos_trades)],
    ["Sharpe OOS mínimo", "≥ " + fmt(c.min_oos_sharpe)],
    ["Profit factor OOS mínimo", "≥ " + fmt(c.min_oos_profit_factor)],
    ["Drawdown OOS máximo", "≤ " + fmt(c.max_oos_drawdown_pct) + " %"],
    ["Batir buy & hold", c.require_beat_benchmark ? "obligatorio" : "no exigido"],
    ["Ventanas", (c.wf_folds || "?") + " folds · train " + (c.train_window_bars || "?") +
      " · test " + (c.test_window_bars || "?") + " velas"],
  ];
  $("gates").innerHTML = rows.map(([name, val]) =>
    '<div class="gate"><div class="name">' + name + '</div><div class="val">' + val + "</div></div>").join("");
}

function renderHistory(hist) {
  const body = $("histBody");
  if (!hist || !hist.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty">sin historial todavía</td></tr>';
    return;
  }
  body.innerHTML = hist.slice().reverse().map((r) => {
    const rep = r.report || {};
    const wf = rep.wf_oos || {};
    const dec = String(r.decision || "");
    const bad = dec.startsWith("sin_ventaja") || dec === "datos_insuficientes";
    const exc = wf.excess_vs_bh_pct === undefined ? rep.excess_oos_pct : wf.excess_vs_bh_pct;
    return "<tr><td class='dim'>" + tsFull(r.ts) + "</td><td>" + r.symbol + "</td><td class='" +
      (bad ? "neg" : "pos") + "'>" + dec + "</td><td>" + fmt(wf.sharpe, 2) + "</td><td>" + fmt(wf.pf, 2) +
      "</td><td>" + (wf.trades === undefined ? "-" : wf.trades) + "</td><td>" + fmt(wf.max_dd_pct, 2) +
      "</td><td class='" + (Number(exc) >= 0 ? "pos" : "neg") + "'>" + (exc === undefined ? "-" : signed(exc)) +
      "</td><td class='dim'>" + ((rep.gate_fails || []).join(", ") || "—") + "</td></tr>";
  }).join("");
}

function renderTrades(trades) {
  const body = $("tradesBody");
  const rows = (trades || []).slice(0, 100);
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">sin trades cerrados (el bot solo opera con campeón)</td></tr>';
    return;
  }
  body.innerHTML = rows.map((t) => {
    const pnl = Number(t.pnl);
    return "<tr><td class='dim'>" + tsFull(t.ts) + "</td><td>" + t.symbol + "</td><td>" +
      (t.side > 0 ? "long" : "short") + "</td><td>" + fmt(t.entry, 4) + "</td><td>" + fmt(t.exit, 4) +
      "</td><td class='" + (pnl >= 0 ? "pos" : "neg") + "'>" + signed(pnl) + "</td><td class='dim'>" +
      (t.reason || "-") + "</td><td class='dim'>" + (t.strategy || "-") + "</td></tr>";
  }).join("");
}

async function loadResults() {
  const s = await refreshPills();
  if (!s) return;
  let learning = { reports: {}, champions: {} }, hist = [], trades = [], conf = {};
  try { learning = await api("/api/learning"); } catch (e) { /* sin reportes */ }
  try { hist = await api("/api/learning/history?limit=200"); } catch (e) { /* sin historial */ }
  try { trades = await api("/api/trades?limit=500"); } catch (e) { /* sin trades */ }
  try { conf = await api("/api/config"); } catch (e) { /* sin config */ }
  renderVerdict(s, learning, hist);
  renderMetrics(s, trades);
  renderGates(conf);
  renderHistory(hist);
  renderTrades(trades);
}

loadResults();
refreshCharts();
setInterval(loadResults, 5000);
setInterval(refreshCharts, 10000);
"""

# ---------------------------------------------------------------- configuración
_BODY_CONFIG = """
  <style>
    .presets { display:grid; grid-template-columns:repeat(auto-fill,minmax(235px,1fr)); gap:10px; }
    .preset { border:1px solid var(--line); border-radius:10px; padding:10px 12px;
              display:flex; flex-direction:column; gap:6px; }
    .preset.on { border-color:var(--green); }
    .preset-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
    .preset-name { font-weight:600; }
    .preset-goal { font-size:12px; line-height:1.35; color:var(--muted); }
    .preset-meta { font-size:11px; color:var(--muted); }
    .preset-warn { font-size:11px; line-height:1.35; color:var(--amber); }
    .preset-keys { font-size:10px; color:var(--muted); word-break:break-word; opacity:.8; }
    .preset .btn-row { margin-top:2px; }
    .labtbl { width:100%; border-collapse:collapse; font-size:13px; }
    .labtbl th, .labtbl td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }
    .labtbl th { color:var(--muted); font-weight:500; font-size:12px; }
    .lab-item { border-top:1px solid var(--line); padding:8px 0; font-size:13px; }
    .lab-item:first-child { border-top:0; padding-top:2px; }
  </style>
  <div class="panel">
    <h2>Recetas</h2>
    <div class="cfg-note">Paquetes de ajustes con un objetivo concreto. Se aplican igual que un cambio a mano:
      validan, se guardan en la base de datos y entran en caliente; lo que necesite reinicio se avisa.</div>
    <div id="presetGrid" class="presets"><span class="empty">cargando...</span></div>
    <div id="presetMsg" class="dim"></div>
  </div>
  <div class="panel">
    <h2>Laboratorio · datos guardados</h2>
    <div class="cfg-note">Las velas que descarga el bot se guardan en local. Aquí puedes bajar histórico del exchange
      (datos reales de internet) y lanzar un walk-forward completo sobre esa caché —sin esperar al mercado ni al
      ciclo de 24 h—. No cambia campeones ni posiciones: es un experimento. Ojo: el walk-forward solo usa las
      últimas <code>ventana + folds×test + 200</code> velas, así que bajar más solo cambia el resultado si
      también amplías las ventanas.</div>
    <div id="dataBody" class="cfg-body"><span class="empty">cargando...</span></div>
    <div id="dataNote" class="cfg-note"></div>
    <div class="cfg-actions">
      <label class="chk">velas <input type="number" id="labBars" min="200" step="100"
        style="width:92px" title="cuántas velas descargar y con cuántas entrenar"></label>
      <button class="ghost" onclick="labFetch()">Descargar historial</button>
      <button class="primary" onclick="labTrain()">Entrenar con estos datos</button>
      <span id="labMsg" class="dim"></span>
    </div>
    <div id="labResult"></div>
  </div>
  <div class="panel">
    <h2>Parámetros</h2>
    <div class="cfg-note">Cada valor se guarda en la base de datos al pulsar Guardar; <code>.env</code> queda
      solo para el arranque. Lo marcado <span class="tag">reinicio</span> se aplica al reiniciar el proceso;
      el resto entra en el siguiente ciclo. <code>EXCHANGE_API_KEY</code>, <code>API_TOKEN</code>,
      <code>PANEL_USER</code>/<code>PANEL_PASSWORD</code> y <code>LIVE_CONFIRM</code> solo se editan en
      <code>.env</code>.</div>
    <div id="cfgBody" class="cfg-body"><span class="empty">cargando...</span></div>
    <div class="cfg-actions">
      <button class="primary" id="btnCfgSave" onclick="saveConfig()">Guardar cambios</button>
      <button class="ghost" onclick="loadConfig(true)">Descartar</button>
      <span id="cfgMsg" class="dim"></span>
    </div>
  </div>
"""

_JS_CONFIG = """
let cfgFields = [];

function cfgInput(f) {
  const attrs = 'id="cfg_' + f.env + '" data-env="' + f.env + '"';
  if (f.kind === "bool") {
    return '<input type="checkbox" ' + attrs + (f.value === "true" ? " checked" : "") + ">";
  }
  if (f.kind === "choice") {
    return "<select " + attrs + ">" + f.options.map((o) =>
      "<option" + (o === f.value ? " selected" : "") + ">" + o + "</option>").join("") + "</select>";
  }
  const numeric = f.kind === "int" || f.kind === "float";
  const step = f.kind === "float" ? ' step="any"' : (f.kind === "int" ? ' step="1"' : "");
  const value = String(f.value).replace(/"/g, "&quot;");
  return '<input type="' + (numeric ? "number" : "text") + '"' + step +
    ' value="' + value + '" ' + attrs + ">";
}

async function loadConfig(reset) {
  try {
    const d = await api("/api/settings");
    cfgFields = d.fields || [];
    const order = [], by = {};
    cfgFields.forEach((f) => {
      if (!by[f.group]) { by[f.group] = []; order.push(f.group); }
      by[f.group].push(f);
    });
    $("cfgBody").innerHTML = order.map((g) => {
      const rows = by[g].map((f) => {
        const id = "cfg_" + f.env;
        const tags = (f.hot ? "" : ' <span class="tag">reinicio</span>') +
          (f.danger ? ' <span class="tag risk">riesgo</span>' : "");
        return '<div class="cfg-row"><label for="' + id + '">' + f.label + tags +
          '</label><div class="cfg-in">' + cfgInput(f) +
          (f.help ? '<div class="cfg-help">' + f.help + "</div>" : "") + "</div></div>";
      }).join("");
      return '<div class="cfg-group"><h3>' + g + "</h3>" + rows + "</div>";
    }).join("");
    $("cfgBody").querySelectorAll("[data-env]").forEach((el) => {
      const mark = () => el.classList.add("dirty");
      el.addEventListener("input", mark);
      el.addEventListener("change", mark);
    });
    if (reset !== false) $("cfgMsg").textContent = "";
  } catch (e) {
    $("cfgBody").innerHTML = '<span class="empty">no se pudo cargar la configuración: ' + e.message + "</span>";
  }
}

function cfgValue(el) {
  return el.type === "checkbox" ? (el.checked ? "true" : "false") : el.value;
}

async function saveConfig() {
  const values = {};
  let n = 0, risky = false;
  $("cfgBody").querySelectorAll("[data-env]").forEach((el) => {
    if (!el.classList.contains("dirty")) return;
    values[el.dataset.env] = cfgValue(el);
    n++;
    const f = cfgFields.find((x) => x.env === el.dataset.env);
    if (f && f.danger) risky = true;
  });
  if (!n) { $("cfgMsg").textContent = "sin cambios"; return; }
  if (risky && !confirm("Vas a cambiar el modo de operación del bot. ¿Seguro?")) return;
  const btn = $("btnCfgSave");
  btn.disabled = true;
  $("cfgMsg").textContent = "guardando...";
  try {
    const res = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values })
    });
    const restart = res.restart_required || [];
    $("cfgMsg").innerHTML = "guardado · aplicado ahora: " + (res.applied || []).length +
      (restart.length ? " · <span class='neg'>al reiniciar: " + restart.join(", ") + "</span>" : "");
    await loadConfig(false);
  } catch (e) {
    $("cfgMsg").innerHTML = "<span class='neg'>" + e.message + "</span>";
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- recetas
let presetsData = [];
let labTimer = null;

async function loadPresets() {
  try {
    const d = await api("/api/presets");
    presetsData = d.presets || [];
    if (!presetsData.length) { $("presetGrid").innerHTML = '<span class="empty">sin recetas</span>'; return; }
    $("presetGrid").innerHTML = presetsData.map((p) => {
      const tag = p.active ? ' <span class="tag">activa</span>'
        : (p.matches ? ' <span class="dim">' + p.matches + "/" + p.changes + " iguales</span>" : "");
      return '<div class="preset' + (p.active ? " on" : "") + '">' +
        '<div class="preset-head"><span class="preset-name">' + p.name + "</span>" + tag + "</div>" +
        '<div class="preset-goal">' + p.goal + "</div>" +
        '<div class="preset-meta">' + p.eta + " · " + p.changes + " parámetros</div>" +
        (p.warn ? '<div class="preset-warn">' + p.warn + "</div>" : "") +
        (p.next ? '<div class="preset-goal">' + p.next + "</div>" : "") +
        '<div class="preset-keys">' + Object.keys(p.values).join(" · ") + "</div>" +
        '<div class="btn-row"><button class="' + (p.active ? "ghost" : "primary") + '"' +
          (p.active ? " disabled" : ' data-pid="' + p.id + '"') + ">" +
          (p.active ? "aplicada" : "aplicar") + "</button></div></div>";
    }).join("");
    $("presetGrid").querySelectorAll("[data-pid]").forEach((b) =>
      b.addEventListener("click", () => applyPreset(b.dataset.pid)));
  } catch (e) {
    $("presetGrid").innerHTML = '<span class="empty">no se pudieron cargar: ' + e.message + "</span>";
  }
}

async function applyPreset(pid) {
  if (!confirm("Se aplica la receta y se guarda en .env. ¿Seguimos?")) return;
  $("presetMsg").textContent = "aplicando...";
  try {
    const r = await api("/api/presets/apply?pid=" + encodeURIComponent(pid), { method: "POST" });
    const restart = r.restart_required || [];
    $("presetMsg").innerHTML = "«" + ((r.preset || {}).name || pid) + "» aplicada · " +
      (r.applied || []).length + " parámetros en caliente" +
      (restart.length ? ' · <span class="neg">al reiniciar: ' + restart.join(", ") + "</span>" : "");
    await loadConfig(false);
    await loadPresets();
    await loadData();
  } catch (e) {
    $("presetMsg").innerHTML = '<span class="neg">' + e.message + "</span>";
  }
}

// ---------------------------------------------------------------- laboratorio
async function loadData() {
  try {
    const d = await api("/api/data");
    const rows = d.rows || [];
    $("dataNote").textContent = d.note + " · fuente: " + d.source;
    const box = $("labBars");
    if (box && document.activeElement !== box) box.value = d.history_bars;
    if (!rows.length) {
      $("dataBody").innerHTML = '<span class="empty">sin velas guardadas todavía</span>';
      return;
    }
    $("dataBody").innerHTML = '<table class="labtbl"><thead><tr><th>Símbolo</th><th>TF</th>' +
      "<th>Velas</th><th>Desde</th><th>Hasta</th><th>Estado</th></tr></thead><tbody>" +
      rows.map((r) => "<tr><td>" + r.symbol + "</td><td>" + r.timeframe + "</td><td>" + r.bars +
        "</td><td>" + (r.first ? tsLabel(r.first / 1000) : "-") + "</td><td>" +
        (r.last ? tsLabel(r.last / 1000) : "-") + "</td><td class='" +
        (r.ready ? "pos" : "neg") + "'>" +
        (r.ready ? "listo" : (r.bars ? "faltan " + (d.needed - r.bars) : "vacío")) +
        "</td></tr>").join("") + "</tbody></table>";
  } catch (e) {
    $("dataBody").innerHTML = '<span class="empty">no se pudo leer la caché: ' + e.message + "</span>";
  }
}

function labBarsValue() {
  const v = Number(($("labBars") || {}).value);
  return Number.isFinite(v) && v > 0 ? Math.round(v) : 0;
}

async function labFetch() {
  $("labMsg").textContent = "descargando...";
  $("labResult").innerHTML = "";
  try {
    await api("/api/lab/fetch?bars=" + labBarsValue(), { method: "POST" });
    startLabPoll();
  } catch (e) {
    $("labMsg").innerHTML = '<span class="neg">' + e.message + "</span>";
  }
}

async function labTrain() {
  $("labMsg").textContent = "entrenando...";
  $("labResult").innerHTML = "";
  try {
    await api("/api/lab/train?bars=" + labBarsValue(), { method: "POST" });
    startLabPoll();
  } catch (e) {
    $("labMsg").innerHTML = '<span class="neg">' + e.message + "</span>";
  }
}

function startLabPoll() {
  if (!labTimer) labTimer = setInterval(pollLab, 1000);
  pollLab();
}

async function pollLab() {
  try {
    const d = await api("/api/lab");
    renderLab(d);
    if (d.status !== "running" && labTimer) { clearInterval(labTimer); labTimer = null; }
  } catch (e) {
    if (labTimer) { clearInterval(labTimer); labTimer = null; }
  }
}

function renderLab(d) {
  const sym = (d.progress || {}).symbol || d.symbol;
  if (d.status === "running") {
    const p = d.progress || {};
    let t = (d.task || "trabajando") + (sym ? " · " + sym : "");
    if (p.stage) t += " · " + p.stage + " " + (p.done || 0) + "/" + (p.total || 0);
    t += " · " + fmtDur(Date.now() / 1000 - d.started);
    $("labMsg").textContent = t;
    return;
  }
  if (d.status === "error") { $("labMsg").innerHTML = '<span class="neg">' + d.error + "</span>"; return; }
  if (d.status === "done") {
    $("labMsg").textContent = (d.task || "") + " · " + fmtDur(d.finished - d.started);
    renderLabReport(d.task, d.report);
  }
}

function renderLabReport(task, rep) {
  if (!rep) return;
  if (task === "descargar") { loadData(); return; }
  const body = rep.bars || rep;
  const label = { promovido_sin_campeon: "pasaría las puertas", sin_ventaja: "sin ventaja",
                  sin_ventaja_campeon_retirado: "sin ventaja", datos_insuficientes: "faltan velas" };
  $("labResult").innerHTML = Object.keys(body).map((s) => {
    const r = body[s];
    const head = '<div class="lab-item"><div><b>' + s + "</b> · " +
      (label[r.decision || r.status] || r.decision || r.status) + "</div>";
    if (!r.wf_oos) {
      return head + '<div class="dim">hay ' + r.bars + " velas, hacen falta " + r.needed + "</div></div>";
    }
    const wf = r.wf_oos;
    return head + '<div class="dim">trades ' + wf.trades + " · sharpe " + fmt(wf.sharpe, 3) +
      " · pf " + fmt(wf.pf, 2) + " · dd " + fmt(wf.max_dd_pct, 2) + "% · vs buy&hold " +
      signed(wf.excess_vs_bh_pct) + "pp · " + fmt(r.seconds, 1) + "s</div>" +
      ((r.gate_fails || []).length
        ? '<div class="neg">falla: ' + r.gate_fails.join(" · ") + "</div>"
        : '<div class="pos">pasa todas las puertas</div>') + "</div>";
  }).join("");
}

refreshPills();
loadConfig();
loadPresets();
loadData();
pollLab();
setInterval(refreshPills, 5000);
"""

# ---------------------------------------------------------------- logs
_BODY_LOGS = """
  <div class="panel">
    <h2>Registro del proceso</h2>
    <div class="filters">
      <select id="lvSel" onchange="refreshProc()">
        <option value="">todos los niveles</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
      <input type="text" id="qIn" placeholder="filtrar por texto o módulo..." oninput="queueProc()">
      <label class="chk"><input type="checkbox" id="followChk" checked> seguir al final</label>
      <label class="chk"><input type="checkbox" id="pauseChk"> pausar</label>
      <span class="spacer"></span>
      <span class="dim" id="procInfo">-</span>
      <button class="ghost" onclick="clearView()">limpiar vista</button>
    </div>
    <div class="logbox" id="procLog"><div class="empty">sin registros todavía</div></div>
    <div class="cfg-note" style="margin:8px 0 0">Últimas 4000 líneas en memoria del proceso.
      Para el histórico completo usa la salida por consola del bot.</div>
  </div>

  <div class="panel">
    <h2>Eventos del motor</h2>
    <div class="filters">
      <span class="dim">aperturas, cierres, entrenamientos, avisos del watchdog</span>
      <span class="spacer"></span>
      <span class="dim" id="evInfo">-</span>
    </div>
    <div class="logbox" id="log" style="max-height:340px"><div class="empty">sin eventos</div></div>
  </div>
"""

_JS_LOGS = """
let procTimer = null;

function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function lvlClass(l) {
  if (l === "ERROR" || l === "CRITICAL") return "ERR";
  if (l === "WARNING") return "WAR";
  if (l === "DEBUG") return "DEB";
  return "INF";
}

function queueProc() {
  clearTimeout(procTimer);
  procTimer = setTimeout(refreshProc, 250);
}

async function refreshProc() {
  if ($("pauseChk").checked) return;
  const lv = $("lvSel").value, q = $("qIn").value.trim();
  const qs = "?limit=600" + (lv ? "&level=" + encodeURIComponent(lv) : "") +
    (q ? "&q=" + encodeURIComponent(q) : "");
  let rows = [];
  try {
    rows = await api("/api/logs" + qs);
  } catch (e) {
    $("procInfo").textContent = "sin conexión";
    return;
  }
  const box = $("procLog");
  if (!rows || !rows.length) {
    box.innerHTML = '<div class="empty">sin registros con este filtro</div>';
    $("procInfo").textContent = "0 líneas";
    return;
  }
  box.innerHTML = rows.map((r) => {
    const time = new Date(r.ts * 1000).toLocaleTimeString();
    return "<div class='row'><span class='ts'>" + time + "</span><span class='lvl " + lvlClass(r.level) +
      "'>" + r.level.slice(0, 3) + "</span><span class='src'>" + esc(r.name) + "</span>" + esc(r.msg) + "</div>";
  }).join("");
  $("procInfo").textContent = rows.length + " líneas";
  if ($("followChk").checked) box.scrollTop = box.scrollHeight;
}

function clearView() {
  $("procLog").innerHTML = '<div class="empty">vista limpiada (los registros siguen en memoria)</div>';
}

async function refreshEvents() {
  try {
    const evs = await api("/api/events?limit=100");
    const log = $("log");
    if (!evs || !evs.length) {
      log.innerHTML = '<div class="empty">sin eventos</div>';
      $("evInfo").textContent = "0";
      return;
    }
    log.innerHTML = evs.map((e) => {
      const time = e.ts ? new Date(e.ts * 1000).toLocaleString() : "";
      const data = e.data && Object.keys(e.data).length ? JSON.stringify(e.data) : "";
      return "<div class='row'><span class='ts'>" + time + "</span><span class='src'>" +
        esc(e.type || e.typ || "") + "</span>" + esc(data) + "</div>";
    }).join("");
    $("evInfo").textContent = evs.length + " eventos";
    log.scrollTop = 0;
  } catch (e) { /* silencioso */ }
}

refreshPills();
refreshProc();
refreshEvents();
setInterval(refreshProc, 2000);
setInterval(refreshEvents, 5000);
setInterval(refreshPills, 5000);
"""

_PAGES = {
    "dash": ("Panel", _BODY_DASH, _JS_DASH),
    "results": ("Resultados", _BODY_RESULTS, _JS_RESULTS),
    "config": ("Configuración", _BODY_CONFIG, _JS_CONFIG),
    "logs": ("Logs", _BODY_LOGS, _JS_LOGS),
}


def _render_page(key, token, cfg):
    title, body, script = _PAGES[key]
    html = (_BASE.replace("__CSS__", _CSS)
                  .replace("__CORE__", _JS_CORE)
                  .replace("__SCRIPT__", script)
                  .replace("__BODY__", body)
                  .replace("__TITLE__", title))
    for nav_key in _PAGES:
        html = html.replace("__NAV_" + nav_key.upper() + "__", " class='on'" if nav_key == key else "")
    return (html.replace("__TOKEN__", json.dumps(token))
                .replace("__MODE__", cfg.mode)
                .replace("__SYMBOLS__", ", ".join(cfg.symbols))
                .replace("__CAPITAL__", str(cfg.initial_capital))
                .replace("__BIND__", f"http://{cfg.api_bind}:{cfg.api_port}"))


def create_app(engine, cfg, store, token, lifespan=None):
    app = FastAPI(title="AutoTrader", version="1.1", lifespan=lifespan)
    lab = Lab(cfg, store, engine)

    def auth(authorization: str = Header(default=""), x_api_token: str = Header(default="")):
        """Token para scripts o HTTP Basic con el usuario/contraseña del panel."""
        if token and hmac.compare_digest(x_api_token.encode(), token.encode()):
            return
        if authorization.lower().startswith("basic ") and cfg.panel_user and cfg.panel_password:
            try:
                raw = base64.b64decode(authorization[6:].strip()).decode("utf-8")
            except Exception:
                raw = ""
            user, _, pwd = raw.partition(":")
            if (hmac.compare_digest(user.encode(), cfg.panel_user.encode())
                    and hmac.compare_digest(pwd.encode(), cfg.panel_password.encode())):
                return
        raise HTTPException(status_code=401, detail="credenciales inválidas",
                            headers={"WWW-Authenticate": 'Basic realm="AutoTrader"'})

    def add(path, fn, methods=("GET",), secure=True, legacy=True):
        """Registra `/api/<path>` y, si procede, el alias antiguo sin documentar."""
        deps = [Depends(auth)] if secure else []
        app.add_api_route("/api" + path, fn, methods=list(methods), dependencies=deps)
        if legacy:
            app.add_api_route(path, fn, methods=list(methods), dependencies=deps,
                              include_in_schema=False, deprecated=True)

    # ------------------------------------------------------------------ lectura
    def health():
        return {"status": "ok", "mode": cfg.mode, "running": engine.running,
                "halted": engine.S["halted"], "last_train": engine.S["last_train"]}

    def status():
        return engine.snapshot()

    def learning():
        """Último reporte walk-forward por símbolo: folds, métricas OOS, decisión, motivos de rechazo"""
        syms = set(cfg.symbols)
        return {"champions": {k: v for k, v in engine.S["champions"].items() if k in syms},
                "reports": {k: v for k, v in engine.S["learning"].items() if k in syms}}

    def learning_history(symbol: str = "", limit: int = 50):
        """Evolución de los walk-forwards: decisiones, OOS y benchmark en el tiempo."""
        return store.learning_history(symbol or None, min(limit, 500))

    def trades(limit: int = 100):
        return store.trades(min(limit, 1000))

    def equity(limit: int = 500):
        return store.equity(min(limit, 5000))

    def events(limit: int = 100):
        return store.events(min(limit, 1000))

    def config():
        return cfg.public()

    def logs(limit: int = 300, level: str = "", q: str = ""):
        """Últimas líneas del registro del proceso (buffer en memoria)."""
        return log_entries(min(max(int(limit), 1), 2000), level or None, q or None)

    def settings_list():
        """Parámetros editables desde el panel (sin claves ni secretos)."""
        return {"fields": settings.fields(cfg), "secret_env": list(settings.SECRET_ENV),
                "mode": cfg.mode}

    def presets_list():
        """Recetas de configuración: objetivo, parámetros y si ya están aplicadas."""
        return presets.listing(cfg)

    def presets_apply(pid: str):
        """Aplica una receta (valida, guarda en la base y entra en caliente)."""
        try:
            return presets.apply(engine, cfg, pid)
        except settings.Busy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def data_info():
        """Velas guardadas en local y cuántas hacen falta para el walk-forward."""
        return lab.data_info()

    def lab_state():
        """Estado del laboratorio y reporte de la última ejecución."""
        return lab.snapshot()

    def lab_fetch(symbol: str = "", bars: int = 0):
        """Descarga histórico del exchange a la caché local."""
        try:
            return lab.fetch(symbol or None, bars or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    def lab_train(symbol: str = "", bars: int = 0):
        """Walk-forward sobre las velas guardadas (no toca campeones ni posiciones)."""
        try:
            return lab.train(symbol or None, bars or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    def settings_save(payload: dict):
        """Valida, guarda en la base de datos y aplica en caliente lo que se puede."""
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict) or not values:
            raise HTTPException(status_code=400, detail="falta 'values' con los cambios")
        try:
            return settings.update(engine, cfg, values)
        except settings.Busy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ------------------------------------------------------------------ control
    def start():
        started = engine.start()
        return {"ok": True, "started": started, "running": engine.running}

    def stop():
        was_running = engine.stop()
        return {"ok": True, "was_running": was_running, "running": engine.running}

    def retrain():
        engine.request_retrain()
        return {"ok": True, "msg": "reentrenamiento en el próximo ciclo"}

    def train_mode(mode: str = "continuous"):
        """Aprendizaje programado (`scheduled`) o sin parar (`continuous`)."""
        try:
            applied = engine.set_train_mode(mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "train_mode": applied,
                "msg": "aprendizaje continuo activado" if applied == "continuous" else "aprendizaje programado"}

    def halt():
        engine.halt("manual (API)")
        return {"ok": True}

    def resume():
        engine.resume()
        return {"ok": True, "msg": "kill switch liberado; el pico de equity se reinició al valor actual"}

    def close_all():
        engine.close_all()
        return {"ok": True}

    add("/health", health, secure=False)
    add("/status", status)
    add("/learning", learning)
    add("/learning/history", learning_history)
    add("/trades", trades)
    add("/equity", equity)
    add("/events", events)
    add("/logs", logs, legacy=False)          # /logs es la página HTML
    add("/config", config, legacy=False)      # /config es la página HTML
    add("/settings", settings_list)
    add("/settings", settings_save, methods=("POST",))
    add("/start", start, methods=("POST",))
    add("/stop", stop, methods=("POST",))
    add("/retrain", retrain, methods=("POST",))
    add("/train_mode", train_mode, methods=("POST",))
    add("/halt", halt, methods=("POST",))
    add("/resume", resume, methods=("POST",))
    add("/close_all", close_all, methods=("POST",))
    add("/presets", presets_list, legacy=False)
    add("/presets/apply", presets_apply, methods=("POST",), legacy=False)
    add("/data", data_info, legacy=False)
    add("/lab", lab_state, legacy=False)
    add("/lab/fetch", lab_fetch, methods=("POST",), legacy=False)
    add("/lab/train", lab_train, methods=("POST",), legacy=False)

    # ------------------------------------------------------------------ páginas
    def page(key):
        def handler():
            return HTMLResponse(_render_page(key, token, cfg))
        handler.__name__ = f"page_{key}"
        return handler

    for key, path in (("dash", "/"), ("results", "/results"), ("config", "/config"), ("logs", "/logs")):
        app.add_api_route(path, page(key), methods=["GET"], response_class=HTMLResponse,
                          dependencies=[Depends(auth)], include_in_schema=False)

    @app.get("/ui", include_in_schema=False, dependencies=[Depends(auth)])
    def ui():
        """/ui era el panel; ahora el panel está en la raíz."""
        return RedirectResponse("/", status_code=307)

    return app
