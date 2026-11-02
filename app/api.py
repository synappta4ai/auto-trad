"""API de monitoreo y control.

Cabecera requerida en todo salvo `/health` y `/ui`:   X-API-Token: <API_TOKEN>
"""
import hmac
import json

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# Interfaz web (se sirve tal cual; los __PLACEHOLDERS__ se reemplazan al vuelo)
# ---------------------------------------------------------------------------
_HTML_UI = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoTrader</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1e222b; --line: #2a2f3a;
    --text: #e6e9ef; --muted: #8b93a1; --green: #35c46a; --red: #e5484d;
    --amber: #e2a336; --blue: #4c8dff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 18px; background: var(--bg); color: var(--text);
         font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  .wrap { max-width: 1020px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  h1 { font-size: 17px; font-weight: 600; margin: 0; letter-spacing: .3px; }
  .pill { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--panel2);
          border: 1px solid var(--line); color: var(--muted); }
  .pill.on { color: #0d1b12; background: var(--green); border-color: transparent; font-weight: 600;
             animation: pulse 2.4s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .6; } }
  .pill.off { color: #2a0e10; background: var(--red); border-color: transparent; font-weight: 600; }
  .pill.warn { color: #241a05; background: var(--amber); border-color: transparent; font-weight: 600; }
  .spacer { flex: 1; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
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
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 13px; margin-bottom: 14px; }
  .panel h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .7px; color: var(--muted);
              margin: 0 0 9px; font-weight: 600; }
  .train-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .progress { height: 6px; background: var(--panel2); border: 1px solid var(--line); border-radius: 999px;
              overflow: hidden; margin-top: 9px; }
  .progress-fill { height: 100%; width: 0; background: var(--blue); transition: width .25s ease; }
  .progress-fill.active { background: linear-gradient(90deg, var(--blue), var(--green)); }
  .stat-row { display: flex; gap: 26px; flex-wrap: wrap; font-size: 13px; }
  .stat-row .dim { margin-right: 5px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--line);
           font-variant-numeric: tabular-nums; }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  tr:last-child td { border-bottom: none; }
  .pos { color: var(--green); } .neg { color: var(--red); } .dim { color: var(--muted); }
  #log { font-family: ui-monospace, "Consolas", monospace; font-size: 12px; max-height: 190px;
         overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  #log div { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,.03); }
  #log .t { color: var(--muted); margin-right: 6px; }
  .empty { color: var(--muted); font-size: 13px; }
  footer { color: var(--muted); font-size: 11.5px; text-align: center; margin-top: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AutoTrader</h1>
    <span class="pill" id="statePill">cargando...</span>
    <span class="pill" id="modePill">__MODE__</span>
    <span class="spacer"></span>
    <span class="pill" id="connPill">conectando</span>
  </header>

  <div class="bar">
    <button class="primary" id="btnStart" onclick="startEngine()">Iniciar</button>
    <button class="danger" id="btnStop" onclick="stopEngine()">Detener</button>
    <button class="warn" id="btnPause" onclick="togglePause()">Pausar</button>
    <button onclick="retrain()">Reentrenar</button>
    <button class="ghost" onclick="closeAll()">Cerrar posiciones</button>
  </div>

  <div class="grid">
    <div class="card"><div class="k">Equity</div><div class="v" id="mEquity">-</div></div>
    <div class="card"><div class="k">Cash</div><div class="v" id="mCash">-</div></div>
    <div class="card"><div class="k">Drawdown</div><div class="v" id="mDd">-</div></div>
    <div class="card"><div class="k">Posiciones</div><div class="v" id="mPos">-</div></div>
    <div class="card"><div class="k">Trades hoy</div><div class="v" id="mTrades">-</div></div>
  </div>

  <div class="panel">
    <h2>Posiciones abiertas</h2>
    <table>
      <thead><tr><th>Símbolo</th><th>Lado</th><th>Cant.</th><th>Entrada</th><th>Precio</th><th>PnL no real.</th></tr></thead>
      <tbody id="posBody"><tr><td colspan="6" class="empty">sin posiciones</td></tr></tbody>
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
    <h2>Entrenamiento (walk-forward)</h2>
    <div class="train-row">
      <span class="pill" id="trainPill">inactivo</span>
      <span id="trainText" class="dim">sin entrenamiento en curso</span>
      <span class="spacer"></span>
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
    <h2>Actividad del motor</h2>
    <div class="stat-row">
      <div><span class="dim">Ciclo</span><strong id="aTick">-</strong></div>
      <div><span class="dim">Último tick</span><strong id="aLast">-</strong></div>
      <div><span class="dim">Intervalo</span><strong id="aPoll">-</strong></div>
      <div><span class="dim">Precio</span><strong id="aPrice">-</strong></div>
    </div>
  </div>

  <div class="panel">
    <h2>Eventos</h2>
    <div id="log"><div class="empty">sin eventos</div></div>
  </div>

  <footer>__SYMBOLS__ · capital inicial __CAPITAL__ · API __BIND__</footer>
</div>

<script>
const TOKEN = __TOKEN__;
const $ = (id) => document.getElementById(id);
let paused = false;
let lastTrainStatus = "idle";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fmt(n, d) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toFixed(d === undefined ? 2 : d) : "-";
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
  if (!res.ok) throw new Error(path + " -> HTTP " + res.status);
  return res.json();
}

function setPill(el, text, cls) {
  el.textContent = text;
  el.className = "pill" + (cls ? " " + cls : "");
}

function renderStatus(s) {
  setPill($("connPill"), "online", "");
  setPill($("statePill"), s.running ? (s.halted ? "pausado" : "ejecutando") : "detenido",
          s.running ? (s.halted ? "warn" : "on") : "off");

  $("mEquity").textContent = fmt(s.equity);
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
  if (!pos.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">sin posiciones</td></tr>';
  } else {
    body.innerHTML = pos.map((sym) => {
      const p = s.positions[sym];
      const pnl = Number(p.unrealized);
      return "<tr><td>" + sym + "</td><td>" + (p.side > 0 ? "long" : "short") + "</td><td>" +
        fmt(p.qty, 6) + "</td><td>" + fmt(p.entry) + "</td><td>" + fmt(p.price) + "</td><td class='" +
        (pnl >= 0 ? "pos" : "neg") + "'>" + (pnl >= 0 ? "+" : "") + fmt(pnl) + "</td></tr>";
    }).join("");
  }

  const champs = Object.entries(s.champions || {});
  const cb = $("champBody");
  if (!champs.length) {
    cb.innerHTML = '<tr><td colspan="4" class="empty">sin campeón (en liquidez)</td></tr>';
  } else {
    cb.innerHTML = champs.map(([sym, c]) => {
      const params = c && c.params ? JSON.stringify(c.params) : "-";
      const risk = s.risk_mult && s.risk_mult[sym] !== undefined ? fmt(s.risk_mult[sym], 2) : "-";
      return "<tr><td>" + sym + "</td><td>" + ((c && c.strategy) || "-") + "</td><td class='dim'>" +
        params + "</td><td>" + risk + "</td></tr>";
    }).join("");
  }

  // --- entrenamiento en vivo ---
  const t = s.training || {};
  lastTrainStatus = t.status || "idle";
  const tp = $("trainPill");
  const bar = $("trainBar");
  if (t.status === "running") {
    setPill(tp, "entrenando", "warn");
    const total = Number(t.total) || 0;
    const done = Number(t.done) || 0;
    const pct = total > 0 ? Math.max(4, Math.round(done / total * 100)) : 10;
    bar.style.width = pct + "%";
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
    $("trainText").textContent = "el último entrenamiento falló (ver eventos)";
  } else {
    setPill(tp, "inactivo", "");
    bar.style.width = "0%";
    bar.className = "progress-fill";
    $("trainText").textContent = t.finished
      ? "último: " + (t.reason || "manual") + " · " + (t.seconds || 0) + " s · hace " +
        fmtDur(Date.now() / 1000 - t.finished)
      : "sin entrenamiento en curso";
  }

  if (!s.learning_enabled) {
    $("nextTrain").textContent = "aprendizaje desactivado (estrategia fija)";
  } else if (s.next_train) {
    const left = Number(s.next_train) - Date.now() / 1000;
    const every = s.retrain_interval_hours ? " · cada " + s.retrain_interval_hours + " h" : "";
    $("nextTrain").textContent = (left > 0 ? "próximo en " + fmtDur(left) : "reentrenamiento pendiente") + every;
  } else {
    $("nextTrain").textContent = s.last_train ? "reentrenamiento programado desactivado" : "aún sin entrenar";
  }

  // --- latido del motor ---
  $("aTick").textContent = s.tick || 0;
  $("aLast").textContent = s.last_tick ? "hace " + fmtDur(Date.now() / 1000 - s.last_tick) : "-";
  $("aPoll").textContent = (s.poll_seconds || 0) + " s";
  const px = Object.values(s.prices || {})[0];
  $("aPrice").textContent = px === undefined ? "-" : fmt(px, 4);
}

async function refresh() {
  try {
    renderStatus(await api("/status"));
  } catch (e) {
    setPill($("connPill"), "sin conexión", "off");
  }
}

async function refreshEvents() {
  try {
    const evs = await api("/events?limit=25");
    const log = $("log");
    if (!evs || !evs.length) { log.innerHTML = '<div class="empty">sin eventos</div>'; return; }
    log.innerHTML = evs.slice().reverse().map((e) => {
      const ts = e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : "";
      const data = e.data && Object.keys(e.data).length ? JSON.stringify(e.data) : "";
      return "<div><span class='t'>" + ts + "</span>" + (e.type || e.typ || "") + " " + data + "</div>";
    }).join("");
    log.scrollTop = log.scrollHeight;
  } catch (e) { /* silencioso */ }
}

async function refreshLearning() {
  try {
    const d = await api("/learning");
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
      if (r.at) s += " · " + new Date(r.at * 1000).toLocaleString();
      return "<div style='margin-bottom:3px'>" + s + "</div>";
    }).join("");

    const folds = reports[0][1].folds || [];
    const fb = $("foldsBody");
    if (!folds.length) {
      fb.innerHTML = '<tr><td colspan="6" class="empty">sin folds (datos insuficientes o aprendizaje desactivado)</td></tr>';
    } else {
      fb.innerHTML = folds.map((f, i) => {
        const m = f.test || {};
        return "<tr><td>" + (i + 1) + "</td><td>" + f.strategy + "</td><td class='dim'>" +
          JSON.stringify(f.params) + "</td><td>" + fmt(f.train_sharpe, 2) + "</td><td>" +
          fmt(m.sharpe, 2) + "</td><td>" + (m.trades === undefined ? "-" : m.trades) + "</td></tr>";
      }).join("");
    }
  } catch (e) { /* silencioso */ }
}

async function post(path) {
  await api(path, { method: "POST" });
  await refresh();
}

const startEngine = () => post("/start").catch((e) => alert(e.message));
const stopEngine = () => post("/stop").catch((e) => alert(e.message));
const closeAll = () => post("/close_all").catch((e) => alert(e.message));
const togglePause = () => post(paused ? "/resume" : "/halt").catch((e) => alert(e.message));

// Reentrenar y seguir el progreso de cerca mientras dura
async function retrain() {
  try {
    await api("/retrain", { method: "POST" });
    const t0 = Date.now();
    let sawRunning = false;
    while (Date.now() - t0 < 20000) {
      await refresh();
      await refreshLearning();
      if (lastTrainStatus === "running") sawRunning = true;
      if (sawRunning && lastTrainStatus !== "running") break;
      await sleep(150);
    }
  } catch (e) {
    alert(e.message);
  }
}

refresh();
refreshEvents();
refreshLearning();
setInterval(refresh, 1500);
setInterval(refreshEvents, 2000);
setInterval(refreshLearning, 3000);
</script>
</body>
</html>
"""


def create_app(engine, cfg, store, token, lifespan=None):
    app = FastAPI(title="AutoTrader", version="1.0", lifespan=lifespan)

    def auth(x_api_token: str = Header(default="")):
        if not hmac.compare_digest(x_api_token.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="X-API-Token inválido")

    # ------------------------------------------------------------------ lectura
    @app.get("/health")
    def health():
        return {"status": "ok", "mode": cfg.mode, "running": engine.running,
                "halted": engine.S["halted"], "last_train": engine.S["last_train"]}

    @app.get("/status", dependencies=[Depends(auth)])
    def status():
        return engine.snapshot()

    @app.get("/learning", dependencies=[Depends(auth)])
    def learning():
        """Último reporte walk-forward por símbolo: folds, métricas OOS, decisión, motivos de rechazo"""
        syms = set(cfg.symbols)
        return {"champions": {k: v for k, v in engine.S["champions"].items() if k in syms},
                "reports": {k: v for k, v in engine.S["learning"].items() if k in syms}}

    @app.get("/trades", dependencies=[Depends(auth)])
    def trades(limit: int = 100):
        return store.trades(min(limit, 1000))

    @app.get("/equity", dependencies=[Depends(auth)])
    def equity(limit: int = 500):
        return store.equity(min(limit, 5000))

    @app.get("/events", dependencies=[Depends(auth)])
    def events(limit: int = 100):
        return store.events(min(limit, 1000))

    @app.get("/config", dependencies=[Depends(auth)])
    def config():
        return cfg.public()

    # ------------------------------------------------------------------ control
    @app.post("/start", dependencies=[Depends(auth)])
    def start():
        started = engine.start()
        return {"ok": True, "started": started, "running": engine.running}

    @app.post("/stop", dependencies=[Depends(auth)])
    def stop():
        was_running = engine.stop()
        return {"ok": True, "was_running": was_running, "running": engine.running}

    @app.post("/retrain", dependencies=[Depends(auth)])
    def retrain():
        engine.request_retrain()
        return {"ok": True, "msg": "reentrenamiento en el próximo ciclo"}

    @app.post("/halt", dependencies=[Depends(auth)])
    def halt():
        engine.halt("manual (API)")
        return {"ok": True}

    @app.post("/resume", dependencies=[Depends(auth)])
    def resume():
        engine.resume()
        return {"ok": True, "msg": "kill switch liberado; el pico de equity se reinició al valor actual"}

    @app.post("/close_all", dependencies=[Depends(auth)])
    def close_all():
        engine.close_all()
        return {"ok": True}

    # ------------------------------------------------------------------ UI
    @app.get("/ui", include_in_schema=False, response_class=HTMLResponse)
    def ui():
        html = (_HTML_UI
                .replace("__TOKEN__", json.dumps(token))
                .replace("__MODE__", cfg.mode)
                .replace("__SYMBOLS__", ", ".join(cfg.symbols))
                .replace("__CAPITAL__", str(cfg.initial_capital))
                .replace("__BIND__", f"http://{cfg.api_bind}:{cfg.api_port}"))
        return html

    @app.get("/", include_in_schema=False)
    def root():
        return ui()

    return app
