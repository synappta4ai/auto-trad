# AutoTrader — Configuration Status

## Estado actual

- `.env` cargado automáticamente por `app/config.py` (sin `python-dotenv`). Las
  variables ya definidas en el entorno tienen prioridad (Docker manda).
- Ejecución con **uv** sobre CPython 3.11: `uv run python -m app.main`.
- Motor en **bucle continuo** con arranque/parada por API (`POST /start`,
  `POST /stop`) y panel web en `http://127.0.0.1:8080/ui`.
- Arranque en un clic: `start-autotrader.bat` / `start-autotrader.ps1`.
- `MODE=paper` + `DATA_SOURCE=synthetic`: no requiere red ni claves.

## Configuración activa

| Variable | Valor | Nota |
| --- | --- | --- |
| `MODE` | `paper` | sin dinero real |
| `DATA_SOURCE` | `synthetic` | precios simulados |
| `SYMBOLS` | `BTC/USDT` | una sola moneda cotizada |
| `TIMEFRAME` | `1h` | |
| `HISTORY_BARS` | `3000` | mínimo `1500 + 3*300 + 200 = 2600` |
| `API_BIND` / `API_PORT` | `127.0.0.1` / `8080` | API + panel |
| `UI` | `web` | `desktop` = ventana PyQt6 |
| `AUTO_START` | `true` | el motor arranca con el proceso |
| `LEARNING_ENABLED` | `true` | walk-forward champion/challenger |
| `MIN_OOS_TRADES` | `8` | puerta de validación OOS |
| `MAX_DRAWDOWN_PCT` | `15` | kill switch |

## Por qué al principio no opera

Es el comportamiento correcto: el optimizador walk-forward exige ventaja real
en OOS (`MIN_OOS_TRADES`, `MIN_OOS_SHARPE`, `MIN_OOS_PROFIT_FACTOR`). Con datos
sintéticos aleatorios casi nunca hay ventaja, así que el símbolo queda **en
liquidez** y los eventos dicen `sin_ventaja`.

Para ver operaciones inmediatas en la demo (desactiva el filtro):

```env
LEARNING_ENABLED=false
FIXED_STRATEGY=ema_cross
ENTER_ON_START=true
```

Verificado: con esa configuración el motor abre, cierra por stop y actualiza
equity/trades (PnL real en la base SQLite).

## Comandos

```bash
uv run python -m app.main            # motor + panel web
# panel:  http://127.0.0.1:8080/ui
# salud:  curl http://127.0.0.1:8080/health

docker compose up -d --build         # misma app en contenedor (puerto 8080)
```

## API

Todas salvo `/health` y `/ui` requieren `X-API-Token: <API_TOKEN>`.

| Endpoint | Función |
| --- | --- |
| `GET /health` | estado básico (sin token) |
| `GET /ui` | panel web |
| `GET /status` | equity, cash, drawdown, posiciones, campeones |
| `GET /learning` | último reporte walk-forward |
| `GET /trades`, `/equity`, `/events` | historial |
| `GET /config` | configuración efectiva (secretos ocultos) |
| `POST /start` · `/stop` | arranca / detiene el motor |
| `POST /retrain` · `/halt` · `/resume` · `/close_all` | control manual |

## Limitaciones

- No se ha probado contra un exchange real (`MODE=live`).
- La GUI de escritorio (`UI=desktop`) necesita `uv pip install -r
  requirements-gui.txt`; la web no.
