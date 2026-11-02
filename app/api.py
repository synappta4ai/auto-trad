"""API de monitoreo y control. Cabecera requerida en todo salvo /health:  X-API-Token: <API_TOKEN>"""
import hmac

from fastapi import Depends, FastAPI, Header, HTTPException


def create_app(engine, cfg, store, token, lifespan=None):
    app = FastAPI(title="AutoTrader", version="1.0", lifespan=lifespan)

    def auth(x_api_token: str = Header(default="")):
        if not hmac.compare_digest(x_api_token.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="X-API-Token inválido")

    @app.get("/health")
    def health():
        s = engine.S
        return {"status": "ok", "mode": cfg.mode, "halted": s["halted"], "last_train": s["last_train"]}

    @app.get("/status", dependencies=[Depends(auth)])
    def status():
        return engine.snapshot()

    @app.get("/learning", dependencies=[Depends(auth)])
    def learning():
        """Último reporte walk-forward por símbolo: folds, métricas OOS, decisión champion/challenger."""
        return {"champions": engine.S["champions"], "reports": engine.S["learning"]}

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

    @app.get("/ui", include_in_schema=False)
    def ui():
        token = cfg.api_token or ""
        html = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>AutoTrader - Panel de Control</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
                .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 20px; }}
                .status {{ padding: 15px; margin: 20px 0; border-radius: 4px; font-size: 1.2em; }}
                .running {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
                .stopped {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
                .controls {{ margin: 20px 0; }}
                .btn {{ padding: 10px 20px; margin: 5px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }}
                .btn-start {{ background: #28a745; color: white; }}
                .btn-stop {{ background: #dc3545; color: white; }}
                .btn-pause {{ background: #ffc107; color: #212529; }}
                .btn-disabled {{ background: #6c757d; color: #adb5bd; cursor: not-allowed; }}
                .endpoint {{ background: #e9ecef; padding: 8px; margin: 5px 0; border-radius: 4px; font-family: monospace; font-size: 0.85em; }}
                .log {{ height: 150px; overflow-y: auto; background: #f8f9fa; padding: 10px; border-radius: 4px; margin-top: 20px; }}
                .meta {{ color: #666; font-size: 0.9em; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 AutoTrader - Panel de Control</h1>
                
                <div class="status" id="status">Detenido</div>
                
                <div class="controls">
                    <button class="btn btn-start" onclick="startTrading()" disabled id="startBtn">▶ Arrancar Motor</button>
                    <button class="btn btn-stop" onclick="stopTrading()" disabled id="stopBtn">⏹ Detener</button>
                    <button class="btn btn-pause" onclick="pauseTrading()" disabled id="pauseBtn">⏸ Pausar</button>
                </div>
                
                <div class="endpoint" style="margin: 10px 0;">
                    <strong>Endpoint API:</strong> http://127.0.0.1:8080
                </div>
                
                <div class="meta">
                    <p>Modo: {{mode}} | Símbolos: {{symbols}} | Capital: {{capital}}</p>
                    <p>Presiona F5 para actualizar estado</p>
                </div>
                
                <div class="log" id="log"></div>
            </div>

            <script>
                const TOKEN = "{{token}}";
                const API_BASE = "http://127.0.0.1:8080";
                const statusEl = document.getElementById('status');
                const logEl = document.getElementById('log');
                const startBtn = document.getElementById('startBtn');
                const stopBtn = document.getElementById('stopBtn');
                const pauseBtn = document.getElementById('pauseBtn');
                
                // Actualizar estado cada 3 segundos
                async function actualizarEstado() {
                    try {{
                        const res = await fetch(`${API_BASE}/status`, {{
                            headers: {{'X-API-Token': TOKEN}}
                        }});
                        const data = await res.json();
                        const mode = data.mode || 'Desconocido';
                        const halted = data.halted ? ' (HALTED)' : '';
                        const equity = data.equity ? ` | Equity: $${Number(data.equity).toFixed(2)}` : '';
                        statusEl.innerHTML = `{{{mode}}}{{{halted}}}{{{equity}}}`;
                        
                        // Habilitar/deshabilitar botones según estado
                        startBtn.disabled = data.running || data.halted;
                        stopBtn.disabled = !data.running && !data.halted;
                        pauseBtn.disabled = !data.running;
                    }} catch (e) {{
                        // error silencioso
                    }}
                }
                
                // Función para iniciar motor
                async function startTrading() {
                    try {
                        const res = await fetch(`${API_BASE}/retrain`, {{
                            method: 'POST',
                            headers: {{'X-API-Token': TOKEN}}
                        }});
                        const data = await res.json();
                        if (data.ok) {
                            startBtn.disabled = true;
                            stopBtn.disabled = false;
                            pauseBtn.disabled = false;
                            actualizarEstado(); // Forzar actualización inmediata
                        }}
                    } catch (e) {{
                        logEl.innerHTML += '<div style="color: red;">Error al arrancar: ' + e.message + '</div>';
                    }}
                }
                
                // Función para detener motor
                async function stopTrading() {
                    try {
                        const res = await fetch(`${API_BASE}/halt`, {{
                            method: 'POST',
                            headers: {{'X-API-Token': TOKEN}}
                        }});
                        const data = await res.json();
                        if (data.ok) {
                            startBtn.disabled = false;
                            stopBtn.disabled = true;
                            pauseBtn.disabled = true;
                            actualizarEstado();
                        }}
                    } catch (e) {{
                        logEl.innerHTML += '<div style="color: red;">Error al detener: ' + e.message + '</div>';
                    }}
                }
                
                // Función para pausar/reanudar
                async function pauseTrading() {
                    try {
                        const res = await fetch(`${API_BASE}/resume', {{
                            method: 'POST',
                            headers: {{'X-API-Token': TOKEN}}
                        }});
                        const data = await res.json();
                        if (data.ok) {
                            actualizarEstado();
                        }}
                    } catch (e) {{
                        logEl.innerHTML += '<div style="color: red;">Error al pausar: ' + e.message + '</div>';
                    }}
                };
                
                // Cargar estado inicial
                actualizarEstado();
                
                // Log de eventos del motor
                setInterval(() => {{
                    fetch(`${API_BASE}/learning', {{headers: {{'X-API-Token': TOKEN}}}})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.champions && data.champions.length > 0) {{
                                const champ = data.champions[0];
                                logEl.innerHTML += '<div>> Reentrenamiento: ' + data.reports[0].reason + '</div>';
                            }}
                        }}).catch(() => {});
                }}, 5000);
            </script>
            </body>
            </html>
        """
        return html
