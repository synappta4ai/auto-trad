# AutoTrader — sistema de trading automático con auto-ajuste

Bot de trading para correr 24/7, en local o en Docker. Todo se configura desde
`.env`: puerto, topes de riesgo, estrategias, frecuencia de reentrenamiento y
reglas de auto-ajuste. Incluye un **panel web** (`/ui`) para iniciar, detener y
pausar el motor sin tocar la consola.

> **Lee esto primero.** Ningún software garantiza ganancias, y la mayoría de los sistemas automáticos retail pierden dinero tras costos. Este proyecto está diseñado para **no operar cuando no encuentra una ventaja demostrable fuera de muestra** (queda en liquidez) y para limitar pérdidas con topes duros. Eso reduce el riesgo, no lo elimina. Empieza siempre en `MODE=paper`, luego testnet del exchange y solo después, con capital que puedas perder, `live`. No es asesoría financiera.

---

## 📦 Instalación rápida

Requisito: [uv](https://astral.sh/uv) (recomendado) o Python 3.9–3.12.

### Opción 1: uv (recomendado)

```powershell
.\install-with-uv.ps1          # crea .venv, instala dependencias y verifica la config
# o manualmente:
uv sync
uv run python -m app.main
```

### Opción 2: pip tradicional

```bash
python -m venv .venv
. .venv\Scripts\Activate.ps1   # Windows
source .venv/bin/activate      # Linux/macOS
pip install -r requirements.txt
python -m app.main
```

La GUI de escritorio es opcional y **no** hace falta para el panel web:
`uv pip install -r requirements-gui.txt` (o `pip install -r requirements-gui.txt`).

---

## 1. Inicio rápido

### Local (recomendado)

Windows, un clic: doble clic en **`start-autotrader.bat`** (o
`.\start-autotrader.ps1`). El script instala dependencias si faltan, arranca el
motor y abre el navegador en el panel.

Manual:

```bash
copy .env.example .env        # Linux/macOS: cp .env.example .env
uv run python -m app.main
```

- Panel de control: **http://127.0.0.1:8080/ui**
- Salud (sin token): `http://127.0.0.1:8080/health`
- Swagger: `http://127.0.0.1:8080/docs`

### Docker

```bash
copy .env.example .env        # edita al menos API_TOKEN
docker compose up -d --build
docker compose logs -f
```

El panel queda en `http://127.0.0.1:8080/ui`. El contenedor escucha en
`0.0.0.0:8080`; lo que se publica al host lo controla `API_HOST_BIND`
(por defecto `127.0.0.1`). Para exponerlo en red: `API_HOST_BIND=0.0.0.0`.

Por defecto arranca en **`MODE=paper` + `DATA_SOURCE=synthetic`**: precios
simulados con regímenes cambiantes, sin red ni dinero. Sirve para ver el ciclo
completo (entrenar → promover → operar → reentrenar). Para acelerar el
reentrenamiento en la demo: `RETRAIN_INTERVAL_HOURS=0.05` y `POLL_SECONDS=5`.

Para datos reales sin arriesgar nada: `DATA_SOURCE=exchange`, `MODE=paper`
(usa velas reales y opera en simulado).

Cambiar cualquier valor: edita `.env` y reinicia (`docker compose up -d` en
Docker, Ctrl+C y `uv run ...` en local). El estado persiste en SQLite.

### Panel web

- **Iniciar / Detener / Pausar**: controlan el bucle continuo del motor.
  *Iniciar* funciona siempre: limpia el kill switch y arranca, sin depender de
  la configuración previa.
- **Reentrenar**: lanza un walk-forward de inmediato (no espera al siguiente
  ciclo) y el panel sigue el progreso en vivo.
- **Cerrar posiciones**: cierra todo a mercado.
- Muestra equity, caja, drawdown, posiciones abiertas, campeón por símbolo,
  multiplicador de riesgo y los últimos eventos.
- **Entrenamiento (walk-forward)**: barra de progreso, etapa (`backtest`,
  `fold`, `decision`), folds con su estrategia y Sharpe train/test, OOS agregado
  con los motivos de rechazo y cuenta atrás del próximo reentrenamiento.
- **Actividad del motor**: número de ciclo, antigüedad del último tick,
  intervalo y precio actual — sirve para distinguir "no hay señal" de "está
  parado".

`.env` se carga automáticamente al ejecutar desde la raíz del proyecto. Las
variables ya definidas en el entorno tienen prioridad (así manda Docker).

| Variable | Default | Qué hace |
| --- | --- | --- |
| `UI` | `web` | `web` = panel en el navegador · `desktop` = ventana PyQt6 |
| `AUTO_START` | `true` | arranca el motor al iniciar el proceso |
| `RETRAIN_ON_START` | `true` | entrena al arrancar (o al pulsar *Iniciar*) |
| `API_BIND` / `API_PORT` | `127.0.0.1` / `8080` | dónde escucha la API y el panel |
| `DATA_DIR` | `./data` (`/data` en Docker) | carpeta de la base SQLite |

Para ver actividad constante en la demo: `POLL_SECONDS=5` y
`RETRAIN_INTERVAL_HOURS=0.25` (entrena cada 15 min).

> **¿No abre operaciones?** Es lo esperado: el walk-forward rechaza campeones
> sin ventaja en OOS (evento `sin_ventaja`). Para ver la demo operando ya mismo:
> `LEARNING_ENABLED=false`, `FIXED_STRATEGY=ema_cross`, `ENTER_ON_START=true`.

---

## 2. Arquitectura

```
                        ┌────────────── .env (todos los parámetros) ──────────────┐
                        ▼                                                          │
   ┌──────────┐   ┌─────────────┐   ┌──────────────┐   ┌───────────┐   ┌─────────┐ │
   │ Feed     │──▶│ Estrategia  │──▶│ Motor        │──▶│ Riesgo    │──▶│ Broker  │ │
   │ synthetic│   │ campeón por │   │ (engine.py)  │   │ topes,    │   │ paper / │ │
   │ /ccxt    │   │ símbolo     │   │ ciclo c/POLL │   │ kill sw.  │   │ ccxt    │ │
   └──────────┘   └──────▲──────┘   └──────┬───────┘   └───────────┘   └─────────┘ │
                        │ promueve/retira │                                       │
                 ┌──────┴───────┐   ┌─────▼──────┐   ┌───────────────┐            │
                 │ Optimizador  │◀──│ Programador│   │ SQLite (/data)│◀───────────┘
                 │ walk-forward │   │ tiempo +   │   │ estado,trades,│
                 │ champion/    │   │ deriva +   │   │ equity,eventos│
                 │ challenger   │   │ manual     │   └───────────────┘
                 └──────────────┘   └────────────┘        ▲
                                                   API FastAPI (puerto API_PORT)
```

Un solo proceso: un hilo con el motor y un servidor HTTP (FastAPI) que sirve la
API y el panel web. Estado persistente en SQLite (`DATA_DIR`, `/data` en
Docker), por lo que un reinicio retoma posiciones, campeones y contadores.

**Interfaz:** panel web `/ui` (HTML + JS, sin build ni dependencias de front) y,
opcionalmente, GUI de escritorio PyQt6 con `UI=desktop`
(`requirements-gui.txt`). Ambas usan los mismos endpoints.

**Lenguaje:** Python (numpy/pandas vectorizados). El cuello de botella real en velas de 5m o más es la red del exchange, no el cálculo; un entrenamiento completo de ~20 combinaciones por símbolo tarda del orden de décimas de segundo. Si necesitaras latencia de milisegundos (HFT) este diseño no es el adecuado.

---

## 3. Qué hace en cada ciclo (cada `POLL_SECONDS`)

1. **Datos:** obtiene velas cerradas y el último precio de cada símbolo.
2. **Salidas:** revisa stop-loss y take-profit con el máximo/mínimo de _todas_ las velas desde la entrada. Si ambos caen en la misma vela, asume el **stop primero** (conservador).
3. **Riesgo:** actualiza pico de equity, drawdown, pérdida diaria/semanal y el multiplicador de riesgo por símbolo. Si el drawdown supera `MAX_DRAWDOWN_PCT` activa el **kill switch**.
4. **Aprendizaje:** si toca (programado, deriva o manual) reentrena.
5. **Entradas (solo en vela nueva):** calcula la señal del _campeón_ del símbolo. Si la señal difiere de la posición actual, cierra; si hay nueva señal y los topes lo permiten, abre con tamaño calculado por riesgo.
6. Guarda estado, trades, equity y eventos.

**Tamaño de posición:** `qty = equity × RISK_PER_TRADE_PCT × multiplicador / (ATR × STOP_ATR_MULT)`, recortado por `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, caja disponible (spot) y `MIN_NOTIONAL`. Si salta el stop pierdes aproximadamente el % de riesgo configurado.

**Entradas con señal nueva:** el bot solo entra cuando la señal _cambia_ (de 0 a 1, etc.), no a mitad de una tendencia ya en curso al arrancar (`ENTER_ON_START=false`) ni inmediatamente después de un stop (`REENTRY_AFTER_STOP_REQUIRES_RESET=true`).

---

## 4. Estrategias incluidas

| Nombre          | Idea                                                                           | Parámetros que se optimizan                     |
| --------------- | ------------------------------------------------------------------------------ | ----------------------------------------------- |
| `ema_cross`     | Tendencia: largo si EMA rápida > lente                                         | `EMA_FAST`, `EMA_SLOW`                          |
| `rsi_reversion` | Reversión: compra sobreventa, sale al volver al nivel de salida                | `RSI_PERIOD`, `RSI_LOW`, `RSI_HIGH`, `RSI_EXIT` |
| `breakout`      | Ruptura de canal Donchian con salida en canal más corto                       | `BREAKOUT_LOOKBACK`, `BREAKOUT_EXIT_RATIO`      |
| `ml_logit`      | Regresión logística (numpy) sobre retornos, RSI, distancia a EMA y volatilidad; **se reajusta cada `ML_REFIT_BARS`** usando solo datos pasados | `ML_TRAIN_BARS`, `ML_THRESHOLD`                 |

Cada variable de rejilla acepta varios valores separados por coma. El optimizador prueba todas las combinaciones (hasta `MAX_CANDIDATES`). Para añadir una estrategia: una función `grid_x(cfg)` y otra `pos_x(df, params, cfg)` en `app/strategies.py` y registrarla en `REGISTRY`.

Todas las estrategias se comprueban sin _look-ahead_: la posición en la vela _t_ solo usa datos hasta _t_.

---

## 5. Cómo se "auto-ajusta" y aprende

### 5.1 Reentrenamiento walk-forward (por símbolo)

Con `TRAIN_WINDOW_BARS=1500`, `TEST_WINDOW_BARS=300`, `WF_FOLDS=3`:

```
fold 1:  [── entrena (1500) ──][─ test 300 ─]
fold 2:        [── entrena (1500) ──][─ test 300 ─]
fold 3:              [── entrena (1500) ──][─ test 300 ─]   ← más reciente
```

En cada fold se elige la mejor combinación (estrategia + parámetros) **solo con la ventana de entrenamiento** y se mide en el test siguiente, que esa elección nunca vio. Los tres tests concatenados (OOS) estiman si _el procedimiento_ tiene ventaja real, no si una combinación se ajustó al pasado.

### 5.2 Puertas (gates) y champion/challenger

1. **Puerta de ventaja:** el OOS agregado debe cumplir `MIN_OOS_TRADES`, `MIN_OOS_SHARPE`, `MIN_OOS_PROFIT_FACTOR` y `MAX_OOS_DRAWDOWN_PCT`. Si falla → decisión `sin_ventaja`: no se promueve nada y, con `DEMOTE_ON_FAIL=true`, se **retira al campeón** actual (el símbolo queda en liquidez).
2. Si pasa, el **retador** es la mejor elección del último fold. Si no hay campeón, se promueve. Si hay uno, el retador debe superar su Sharpe en el último tramo OOS por `PROMOTE_MARGIN_PCT` (evita cambiar por ruido).
3. Decisiones posibles: `promovido_sin_campeon`, `retador_promovido`, `campeon_confirmado`, `campeon_se_mantiene`, `sin_ventaja`, `sin_ventaja_campeon_retirado`, `datos_insuficientes`.

Cuando cambia el campeón, el bot espera la siguiente señal nueva antes de abrir (no entra a mitad de movimiento).

### 5.3 Cuándo se reentrena

| Disparador                                                          | Control                                                   |
| ------------------------------------------------------------------- | --------------------------------------------------------- |
| Programado                                                          | `RETRAIN_INTERVAL_HOURS`                                  |
| Deriva de volatilidad (corta/larga ≥ `DRIFT_VOL_RATIO` o ≤ 1/ratio) | `RETRAIN_ON_DRIFT`, `DRIFT_SHORT_BARS`, `DRIFT_LONG_BARS` |
| Racha de pérdidas                                                   | `DRIFT_LOSS_STREAK`                                       |
| Mínimo entre reentrenos por deriva                                  | `DRIFT_MIN_HOURS`                                         |
| Manual                                                              | `POST /retrain` o botón *Reentrenar* del panel             |
| Arranque                                                            | al iniciar el motor (`AUTO_START=true` o botón *Iniciar*)  |

`LEARNING_ENABLED=false` desactiva todo esto y usa `FIXED_STRATEGY` con el primer valor de cada rejilla.

### 5.4 Ajuste automático del riesgo (sin reentrenar)

El multiplicador de riesgo por operación (entre `RISK_MULT_MIN` y `RISK_MULT_MAX`) baja solo cuando:

- **`VOL_SCALING_ENABLED`**: la volatilidad reciente supera a la normal (si es 2× la normal, riesgo ÷ 2).
- **`DERISK_ENABLED`**: el drawdown pasa de `DERISK_START_DD_PCT`; se reduce linealmente hasta `DERISK_MIN_MULT` al acercarse a `MAX_DRAWDOWN_PCT`.

Los topes duros (secciones siguientes) **nunca** los modifica el aprendizaje: solo cambian si editas `.env`.

---

## 6. Topes y protecciones de riesgo

| Variable                                        | Efecto                                                                                                                                                     |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RISK_PER_TRADE_PCT`                            | % de equity arriesgado hasta el stop                                                                                                                       |
| `MAX_POSITION_PCT` / `MAX_TOTAL_EXPOSURE_PCT`   | Nocional máximo por posición / total                                                                                                                       |
| `MAX_OPEN_POSITIONS`, `MAX_TRADES_PER_DAY`      | Límites de cantidad                                                                                                                                        |
| `STOP_ATR_MULT`, `ATR_PERIOD`, `TAKE_PROFIT_RR` | Stop dinámico por ATR; take profit opcional como múltiplo del riesgo                                                                                       |
| `COOLDOWN_BARS`                                 | Espera tras una operación perdedora                                                                                                                        |
| `MAX_DAILY_LOSS_PCT`, `MAX_WEEKLY_LOSS_PCT`     | Pausa **entradas** hasta el día/semana UTC siguiente (las posiciones siguen con su stop)                                                                   |
| `MAX_DRAWDOWN_PCT`                              | **Kill switch**: bloquea entradas y (con `KILL_SWITCH_CLOSE_POSITIONS`) cierra todo. Se libera con `POST /resume` o el botón *Iniciar* (reinicia el pico al equity actual) |
| `MIN_NOTIONAL`                                  | Evita órdenes diminutas                                                                                                                                    |

---

## 7. API (cabecera `X-API-Token`)

| Endpoint                                             | Función                                                                                    |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `GET /health`                                        | Estado básico (sin token; lo usa el healthcheck de Docker)                                 |
| `GET /ui`                                            | Panel web de control (sin token: el token va embebido en la página)                        |
| `GET /status`                                        | Equity, caja, drawdown, posiciones, campeones, multiplicadores de riesgo                   |
| `GET /learning`                                      | Último reporte walk-forward por símbolo: folds, métricas OOS, decisión, motivos de rechazo |
| `GET /trades?limit=` · `GET /equity` · `GET /events` | Historial                                                                                  |
| `GET /config`                                        | Configuración efectiva (secretos ocultos)                                                  |
| `POST /start` · `/stop`                              | Arranca / detiene el bucle continuo del motor                                              |
| `POST /retrain` · `/halt` · `/resume` · `/close_all` | Control manual                                                                             |

Documentación interactiva en `/docs` (Swagger). Por defecto el puerto solo escucha en `127.0.0.1`; para acceder remotamente usa un túnel SSH (`ssh -L 8080:127.0.0.1:8080 servidor`) o un reverse proxy con TLS. Si pones `API_BIND=0.0.0.0` sin proteger el puerto, cualquiera con tu token podría operar el bot.

---

## 8. Pasar a operar con dinero real

Checklist, en orden, sin saltarte pasos:

1. `DATA_SOURCE=exchange`, `MODE=paper`: deja correr **semanas**. Revisa `/learning`, `/trades`, `/equity`. ¿Hay ventaja o el bot casi no opera? Si casi no opera, es la señal de que no halló ventaja, no un fallo.
2. `MODE=live` + `EXCHANGE_SANDBOX=true` con claves del _testnet_ y `LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`: verifica que las órdenes, saldos y cierres cuadran.
3. Claves API del exchange real **solo con permiso de trading, sin retiros**, y restringidas por IP del servidor.
4. `EXCHANGE_SANDBOX=false`, `INITIAL_CAPITAL` pequeño (dinero que puedas perder entero) y topes conservadores.
5. Escala solo si el comportamiento real coincide con el simulado durante un periodo largo.

En live, `INITIAL_CAPITAL` es el capital asignado al bot: el motor nunca usa más que eso aunque la cuenta tenga más saldo.

---

## 9. Operación

### Local

```powershell
uv run python -m app.main              # motor + API + panel (Ctrl+C para parar)
.\start-autotrader.ps1                 # lo mismo, en otra ventana y abre el navegador
curl.exe http://127.0.0.1:8080/health
curl.exe -H "X-API-Token: TU_TOKEN" http://127.0.0.1:8080/status
```

En PowerShell `curl` es un alias de `Invoke-WebRequest`; usá `curl.exe` (o
`Invoke-RestMethod`). El motor corre en bucle hasta que pulses **Detener** o
`POST /stop`; el panel refresca estado cada 1,5 s, eventos cada 2 s y el
reporte de entrenamiento cada 3 s (durante un reentrenamiento manual, cada
150 ms hasta que termina).

### Docker

```bash
docker compose logs -f --tail 200            # logs
docker compose up -d --build                 # aplicar cambios de .env o de código
docker compose down                          # parar (conserva el volumen)
docker compose down -v                       # parar y BORRAR estado/historial
# backup de la base:
docker run --rm -v ${VOLUME_NAME:-autotrader_data}:/data -v "$PWD":/backup alpine tar czf /backup/autotrader-data.tgz -C /data .
```

Para reiniciar desde cero el capital simulado: `docker compose down -v` y
levantar de nuevo (en local basta con borrar `data/autotrader.db` con el motor
detenido).

---

## 10. Limitaciones conocidas (léelas)

- **El backtest del optimizador es vectorizado y no incluye el stop ATR ni el dimensionamiento**: mide la calidad de la _señal_ con costos (comisión, slippage, spread) por cambio de posición. El stop y el sizing actúan en vivo como capa de riesgo, por lo que el resultado real difiere del backtest.
- **Sharpe y profit factor se calculan por vela**, no por operación: son aproximaciones útiles para filtrar, no estadísticas finas.
- **Riesgo de sobreajuste por selección múltiple:** probar muchas combinaciones siempre puede producir alguna que parezca buena por azar. Las puertas OOS y el margen de promoción lo reducen, no lo eliminan. Mantén las rejillas pequeñas.
- **Los stops en live los vigila el software** (cada `POLL_SECONDS`), no son órdenes nativas del exchange. Si el servidor o la red caen, la posición queda sin protección. Para producción seria añade stops nativos del exchange.
- **Live es spot, long-only, órdenes de mercado, un solo exchange y una sola moneda cotizada.** La conciliación con el exchange es mínima: se limita la venta al saldo disponible y se registran eventos `partial_close` o `reconcile_needed` si algo no cuadra. Revisa `/events`.
- El entrenamiento corre en el mismo hilo del motor; con rejillas muy grandes pausa el ciclo unos segundos (`MAX_CANDIDATES` lo acota).
- Los datos `synthetic` son un generador de prueba, **no** son mercado real; sirven para validar el mecanismo, no la rentabilidad.
- Qué se probó y qué no: motor y bucle continuo, `start/stop/pause` por API y panel, walk-forward, stops, contabilidad y el panel web renderizado en navegador, además de `docker compose config`. Se verificó el ciclo completo de trading en paper sintético (apertura → cierre por stop → actualización de cash/equity y trade en SQLite). **No** se construyó la imagen Docker en esta máquina ni se probó contra un exchange real.
- El panel web embebe el `API_TOKEN` en el HTML (por eso `/ui` no pide cabecera). No lo publiques sin TLS ni autenticación delante: usa túnel SSH o un reverse proxy. Si el token no te sirve para eso, define uno propio y no expongas el puerto.

---

## 11. Estructura

```
.env.example          todos los parámetros documentados
pyproject.toml        configuración uv (metadatos, dependencias, scripts)
uv.lock               lockfile reproducible de uv
docker-compose.yml    contenedor, puerto, volumen, límites (todo desde .env)
Dockerfile
start-autotrader.bat      arranque en un clic (Windows) + abre el panel
start-autotrader.ps1      ídem en PowerShell
install-with-uv.ps1       instalación/verificación con uv
requirements.txt          dependencias base (pip)
requirements-gui.txt      PyQt6 opcional para UI=desktop
.env                      configuración activa (copia de .env.example)
CONFIGURATION_STATUS.md   estado actual de la configuración
README_UV.md              documentación de uv
CONTEXT_UV.md             contexto de la integración uv
app/
  config.py           carga de .env, valores y validación
  data.py             feed sintético y feed ccxt
  strategies.py       indicadores y las 4 estrategias
  optimizer.py        backtest vectorizado + walk-forward + champion/challenger
  regime.py           deriva y multiplicador de riesgo
  engine.py           ciclo, entradas/salidas, topes, kill switch, start/stop
  broker.py           paper y ccxt
  api.py              API FastAPI + panel web en /ui
  gui.py              dashboard PyQt6 opcional (UI=desktop)
  main.py             arranque: motor + uvicorn (+ GUI opcional)
```

---

## 12. Flujos de trabajo comunes

### uv (recomendado)

```powershell
uv sync                                  # instala/actualiza dependencias
uv run python -m app.main                # motor + API + panel
uv run python -c "from app.config import Config; print(Config().public())"
uv add <paquete>                         # nueva dependencia (pyproject + uv.lock)
```

### pip

```bash
pip install -r requirements.txt
python -m app.main
```

### Docker

```bash
docker compose up -d --build     # aplicar cambios de .env o código
docker compose logs -f           # logs en tiempo real
docker compose down              # parar (conserva el volumen)
docker compose down -v           # parar y borrar estado/historial
```

### Problemas típicos

| Síntoma | Causa / solución |
| --- | --- |
| `[Errno 10048]` al arrancar | otro proceso ocupa el puerto: ciérralo o cambia `API_PORT` |
| `401 X-API-Token inválido` | el header no coincide con `API_TOKEN` cargado desde `.env` |
| El panel dice "sin conexión" | el servidor no está arriba o cambió `API_PORT`; revisa la consola |
| No hay operaciones | el walk-forward no halló ventaja (`sin_ventaja`); ver nota de demo en la sección 1 |
| `uv` no reconocido | instala uv y abre una consola nueva: `irm https://astral.sh/uv/install.ps1 \| iex` |

---

> **Python:** el proyecto requiere 3.9–3.12. En Windows `python` puede apuntar a
> 2.7; usá siempre `uv run python ...` para que corra con el intérprete del `.venv`.