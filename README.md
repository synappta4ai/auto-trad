# AutoTrader — sistema de trading automático con auto-ajuste

Bot de trading para correr 24/7, en local o en Docker. La configuración (puerto,
topes de riesgo, estrategias, frecuencia de reentrenamiento y reglas de
auto-ajuste) vive en la base SQLite y se edita desde el panel; `.env` solo trae
lo necesario para arrancar. Incluye un **panel web** (`/`) para iniciar, detener y
pausar el motor sin tocar la consola, con páginas aparte para resultados,
configuración y logs.

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

No hay dependencias de escritorio: el panel web lo sirve la propia API, así que
`pip install -r requirements.txt` es todo lo que hace falta.

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

- Panel de control: **http://127.0.0.1:8080/** (resultados en `/results`, configuración en `/config`, logs en `/logs`)
- Salud (sin token): `http://127.0.0.1:8080/health`
- Swagger: `http://127.0.0.1:8080/docs`

### Docker

```bash
copy .env.example .env        # edita al menos API_TOKEN
docker compose up -d --build
docker compose logs -f
```

El panel queda en `http://127.0.0.1:8080/`. El contenedor escucha en
`0.0.0.0:8080`; lo que se publica al host lo controla `API_HOST_BIND`
(por defecto `127.0.0.1`). Para exponerlo en red: `API_HOST_BIND=0.0.0.0`.

Por defecto arranca en **`MODE=paper` + `DATA_SOURCE=synthetic`**: precios
simulados con regímenes cambiantes, sin red ni dinero. Sirve para ver el ciclo
completo (entrenar → promover → operar → reentrenar). Para acelerar el
reentrenamiento en la demo: `RETRAIN_INTERVAL_HOURS=0.05` y `POLL_SECONDS=5`.

Para datos reales sin arriesgar nada: `DATA_SOURCE=exchange`, `MODE=paper`
(usa velas reales y opera en simulado).

Cambiar cualquier valor: hazlo en `/config` (lo que exija reinicio lo avisa) o
edita `.env` para lo de arranque y reinicia (`docker compose up -d` en Docker,
Ctrl+C y `uv run ...` en local). El estado persiste en SQLite.

### Panel web

- **Iniciar / Detener / Pausar**: controlan el bucle continuo del motor.
  *Iniciar* funciona siempre: limpia el kill switch y arranca, sin depender de
  la configuración previa.
- **Reentrenar**: lanza un walk-forward de inmediato (no espera al siguiente
  ciclo) y el panel sigue el progreso en vivo.
- **Aprender sin parar**: alterna aprendizaje programado ↔ continuo en caliente
  (no hace falta reiniciar).
- **Cerrar posiciones**: cierra todo a mercado.
- Muestra equity, caja, drawdown, posiciones abiertas, campeón por símbolo,
  multiplicador de riesgo y los últimos eventos.
- **Entrenamiento (walk-forward)**: barra de progreso, etapa (`backtest`,
  `fold`, `decision`), folds con su estrategia y Sharpe train/test, OOS agregado
  con los motivos de rechazo y cuenta atrás del próximo reentrenamiento.
- **Curvas**: dos gráficas en SVG, sin librerías externas. La equity en el tiempo
  con la línea del capital inicial, y el Sharpe OOS de cada walk-forward (punto
  verde = pasó las puertas, rojo = `sin_ventaja`). Se alimentan de `/api/equity`
  y `/api/learning/history`.
- **Actividad del motor**: número de ciclo, antigüedad del último tick,
  intervalo y precio actual — sirve para distinguir "no hay señal" de "está
  parado".
- **Logs** (`/logs`): registro del proceso en memoria (últimas 4000 líneas) con
  filtro por nivel y por texto, más los eventos del motor.

Las páginas están separadas para que cada una se lea sin ruido: `/` (control y
estado en vivo), `/results` (veredicto en lenguaje llano, métricas de operación,
reglas de aceptación, historial de entrenamientos y trades), `/config` (los 86
parámetros editables) y `/logs` (proceso y eventos). `/ui` redirige a `/` para no
romper enlaces antiguos.

Al arrancar, `app/config.py` lee `.env` y después `settings.load()` aplica los
parámetros guardados en la base, que **mandan sobre el entorno**. La tabla
`settings` es la fuente de verdad: el panel escribe ahí, no en `.env`.

| Variable | Default | Qué hace |
| --- | --- | --- |
| `AUTO_START` | `true` | arranca el motor al iniciar el proceso |
| `RETRAIN_ON_START` | `true` | entrena al arrancar (o al pulsar *Iniciar*) |
| `TRAIN_MODE` | `scheduled` | `continuous` = aprendizaje sin parar (ver 5.3) |
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

**Interfaz:** panel web en `/` (HTML + JS, sin build ni dependencias de front),
servido por la propia API. No hay capa de escritorio: un solo proceso.

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

La medición del OOS **simula la ejecución real** (`app/sim.py`): stop ATR,
sizing por riesgo, topes de posición/exposición, cooldown y costos del
`PaperBroker`, vela a vela. Así la puerta juzga el procedimiento completo y no
solo la señal: si el stop o el sizing destruyen la ventaja, el OOS lo muestra.
Entre la ventana de entrenamiento y la de test hay un **embargo** de
`ML_HORIZON` velas (`EMBARGO_BARS`) para que las etiquetas solapadas del modelo
no filtren información del test al train.

### 5.2 Puertas (gates) y champion/challenger

1. **Puerta de ventaja:** el OOS agregado debe cumplir `MIN_OOS_TRADES`, `MIN_OOS_SHARPE`, `MIN_OOS_PROFIT_FACTOR` y `MAX_OOS_DRAWDOWN_PCT` (Sharpe y drawdown por vela; profit factor, win rate y R medio **por operación**). Con `REQUIRE_BEAT_BENCHMARK=true` además debe superar a comprar y mantener el activo en el mismo tramo. Si falla → decisión `sin_ventaja`: no se promueve nada y, con `DEMOTE_ON_FAIL=true`, se **retira al campeón** actual (el símbolo queda en liquidez).
2. Si pasa, el **retador** es la mejor elección del último fold. Si no hay campeón, se promueve. Si hay uno, el retador debe superar su Sharpe en el último tramo OOS por `PROMOTE_MARGIN_PCT` (evita cambiar por ruido).
3. Decisiones posibles: `promovido_sin_campeon`, `retador_promovido`, `campeon_confirmado`, `campeon_se_mantiene`, `sin_ventaja`, `sin_ventaja_campeon_retirado`, `datos_insuficientes`.

**Cómo validar que el entrenamiento sirve.** Cada walk-forward queda en la tabla
`learning_reports` y se consulta con `GET /learning/history?symbol=BTC%2FUSDT&limit=100`
(el panel lo dibuja).

- Éxito = `decision` sin `sin_ventaja` y `gate_fails` vacío. Con datos reales, un
  `sin_ventaja` significa que el procedimiento no tiene ventaja en ese tramo, no
  que el entrenamiento falló.
- Sobreajuste: `train_sharpe` alto con el Sharpe de test plano o negativo fold a
  fold. Si el test acompaña al train, hay señal.
- Progreso: la serie de `wf_oos.sharpe`, `pf` y `excess_vs_bh_pct` sobre velas
  nuevas. Reentrenar sobre las mismas velas repite el mismo resultado: eso no es
  avance, es CPU.
- `DATA_SOURCE=synthetic` no valida nada (es un proceso autorregresivo, sirve
  para probar el sistema sin red). Para medir en serio: `DATA_SOURCE=exchange`,
  `HISTORY_BARS` que cubra las ventanas y `TRAIN_MODE=scheduled`.

Cuando cambia el campeón, el bot espera la siguiente señal nueva antes de abrir (no entra a mitad de movimiento).

### 5.3 Cuándo se reentrena

| Disparador                                                          | Control                                                   |
| ------------------------------------------------------------------- | --------------------------------------------------------- |
| Programado                                                          | `RETRAIN_INTERVAL_HOURS`                                  |
| Continuo (sin parar)                                                | `TRAIN_MODE=continuous` o botón *Aprender sin parar*      |
| Deriva de volatilidad (corta/larga ≥ `DRIFT_VOL_RATIO` o ≤ 1/ratio) | `RETRAIN_ON_DRIFT`, `DRIFT_SHORT_BARS`, `DRIFT_LONG_BARS` |
| Racha de pérdidas                                                   | `DRIFT_LOSS_STREAK`                                       |
| Mínimo entre reentrenos por deriva                                  | `DRIFT_MIN_HOURS`                                         |
| Manual                                                              | `POST /retrain` o botón *Reentrenar* del panel             |
| Arranque                                                            | al iniciar el motor (`AUTO_START=true` o botón *Iniciar*)  |

`LEARNING_ENABLED=false` desactiva todo esto y usa `FIXED_STRATEGY` con el primer valor de cada rejilla.

#### Aprendizaje continuo

`TRAIN_MODE=continuous` (o el botón **Aprender sin parar** del panel) encadena
walk-forwards sin esperar al programado:

| Variable                | Default     | Qué hace                                                             |
| ----------------------- | ----------- | -------------------------------------------------------------------- |
| `TRAIN_MODE`            | `scheduled` | `continuous` = reentrena en cuanto termina el anterior                |
| `TRAIN_COOLDOWN_SECONDS`| `0`         | espera entre entrenamientos; `0` = encadenar                         |
| `TRAIN_MIN_NEW_BARS`    | `1`         | velas nuevas necesarias antes de volver a entrenar; `0` = no esperar |

Dos avisos honestos:

- **Reentrenar sin datos nuevos repite el mismo cálculo.** Con
  `TRAIN_MIN_NEW_BARS=1` cada pasada usa velas que no había visto; con `0` el
  motor se queda en bucle caliente sin aprender nada nuevo.
- Lo que hace "intensivo" el aprendizaje no es la frecuencia sino el espacio de
  búsqueda: rejillas más amplias en `.env` (`EMA_FAST`, `EMA_SLOW`,
  `RSI_*`, `BREAKOUT_*`, `ML_*`) y `MAX_CANDIDATES`. Con la rejilla del repo,
  cada pasada evalúa ~260 combinaciones y tarda ~6-8 s por símbolo, porque
  ahora simula la ejecución completa (stop, sizing y costos).

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

| Endpoint                                                            | Función                                                                                       |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `GET /` · `/results` · `/config` · `/sweep` · `/logs`               | Páginas HTML: panel, resultados, configuración, barridos y logs (sin token: va embebido)        |
| `GET /api/health`                                                   | Estado básico (sin token; lo usa el healthcheck de Docker)                                     |
| `GET /api/status`                                                   | Equity, caja, drawdown, posiciones, campeones, multiplicadores de riesgo                      |
| `GET /api/learning`                                                 | Último reporte walk-forward por símbolo: folds, métricas OOS, decisión, motivos de rechazo    |
| `GET /api/learning/history?symbol=&limit=`                          | Historial de walk-forwards (decisiones, OOS y benchmark en el tiempo)                          |
| `GET /api/trades?limit=` · `/api/equity` · `/api/events`            | Historial de operaciones, curva de equity y eventos del motor                                  |
| `GET /api/logs?level=&q=&limit=`                                    | Registro del proceso en memoria (últimas 4000 líneas), filtrable por nivel y texto             |
| `GET /api/config`                                                   | Configuración efectiva (secretos ocultos)                                                     |
| `GET /api/settings` · `POST /api/settings`                          | Parámetros editables: lista con valores actuales y guardado en la base de datos                |
| `GET /api/presets` · `POST /api/presets/apply?pid=`               | Recetas de configuración con objetivo concreto (iterar rápido, ampliar la búsqueda, estricto…) |
| `GET /api/data` · `GET /api/lab`                                   | Velas guardadas en local y estado del laboratorio                                              |
| `POST /api/lab/fetch` · `POST /api/lab/train?symbol=&bars=`        | Descargar histórico / entrenar walk-forward sobre la caché, sin tocar al bot                    |
| `GET /api/sweep` · `POST /api/sweep/start`                         | Barrido símbolo × temporalidad: estado en curso y lanzamiento (JSON: `symbols`, `timeframes`)   |
| `GET /api/sweep/results?run_id=&limit=`                            | Intentos guardados en `experiments`, con su OOS y su exceso frente a comprar y mantener         |
| `POST /api/start` · `/api/stop`                                     | Arranca / detiene el bucle continuo del motor                                                 |
| `POST /api/retrain` · `/api/halt` · `/api/resume` · `/api/close_all` | Control manual                                                                               |
| `POST /api/train_mode?mode=scheduled\|continuous`                   | Cambia el modo de aprendizaje en caliente                                                     |

Las páginas `/`, `/results`, `/config`, `/sweep` y `/logs` piden la misma autenticación que la API: HTTP Basic (el navegador lo pregunta una vez) o `X-API-Token`. Los nombres antiguos de los endpoints (`/status`, `/learning`, `/trades`, `/equity`, `/events`, `/settings`, `/start`, `/stop`, `/retrain`, `/train_mode`, `/halt`, `/resume`, `/close_all`, `/health`) siguen respondiendo como alias sin documentar, pero **`/config` y `/logs` ahora son páginas**: sus datos están en `/api/config` y `/api/logs`.

### Recetas y laboratorio

La página `/config` empieza con dos bloques pensados para llegar antes a una conclusión:

- **Recetas**: paquetes de parámetros con un objetivo declarado («iterar en segundos», «que la rejilla opere»,
  «ampliar la búsqueda», «veredicto inmediato», «solo lo que aguanta», «dejarlo corriendo»). Cada tarjeta dice
  para qué sirve, lo que tarda, qué cambia y qué tiene de trampa; se aplican con `POST /api/presets/apply?pid=`,
  que valida, guarda en la base y entra en caliente (avisando de lo que exija reinicio).
- **Laboratorio**: las velas que descarga el bot se guardan en SQLite (`candles`). `POST /api/lab/fetch` baja
  histórico ahora y `POST /api/lab/train` lanza un walk-forward completo sobre esa caché en segundos, sin
  esperar al mercado ni al ciclo de 24 h y **sin tocar campeones, posiciones ni el estado del motor**. El
  reporte queda en el historial con `reason: laboratorio`.

Así se itera: descargar → entrenar → leer el veredicto → cambiar una receta o un parámetro → repetir.

### Barridos: símbolo × temporalidad

Con un solo símbolo no se distingue «no hay ventaja» de «mala suerte». La página `/sweep` lanza la misma
rejilla sobre una matriz de símbolos y temporalidades: cada celda es un walk-forward independiente con su
train/test, y **cada intento se guarda en la tabla `experiments`** con su Sharpe, profit factor, drawdown,
retorno OOS, benchmark y exceso en puntos porcentuales.

- **Ventaja repetida** (`repetible`): la celda pasa las puertas, bate a comprar y mantener, y lo hace en más de
  un símbolo o temporalidad. Es lo único que merece pasar a papel.
- **Una sola celda** (`aislado`): gana una de veinte con cientos de combinaciones probadas. Eso lo produce el
  azar; el contador de intentos del panel está ahí para no engañarse.
- **Sin ventaja**: no hay nada que operar; el bot sigue en liquidez.

Se puede usar sin abrir el panel: `uv run python -m app.sweep --symbols BTC/USDT,ETH/USDT --timeframes 1h,4h
--bars 44000 --folds 12`. Descarga lo que falte de la caché `candles`, entrena y escribe el veredicto; no toca
campeones, posiciones ni el estado del motor.

Documentación interactiva en `/docs` (Swagger). Por defecto el puerto solo escucha en `127.0.0.1`; para acceder remotamente usa un túnel SSH (`ssh -L 8080:127.0.0.1:8080 servidor`) o un reverse proxy con TLS. Si pones `API_BIND=0.0.0.0` sin proteger el puerto, cualquiera con tu token podría operar el bot.

---

## 8. Pasar a operar con dinero real

Checklist, en orden, sin saltarte pasos:

1. `DATA_SOURCE=exchange`, `MODE=paper`: deja correr **semanas**. Revisa `/results` (o `/api/learning`, `/api/trades`, `/api/equity`). ¿Hay ventaja o el bot casi no opera? Si casi no opera, es la señal de que no halló ventaja, no un fallo.
2. `MODE=live` + `EXCHANGE_SANDBOX=true` con claves del _testnet_ y `LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`: verifica que las órdenes, saldos y cierres cuadran.
3. Claves API del exchange real **solo con permiso de trading, sin retiros**, y restringidas por IP del servidor.
4. `EXCHANGE_SANDBOX=false`, `INITIAL_CAPITAL` pequeño (dinero que puedas perder entero) y topes conservadores.
5. Escala solo si el comportamiento real coincide con el simulado durante un periodo largo.

En live, `INITIAL_CAPITAL` es el capital asignado al bot: el motor nunca usa más que eso aunque la cuenta tenga más saldo.

### Protección de la posición en live

El motor **no deja una posición desnuda**: al abrir coloca un stop `stop_market` en el exchange (`NATIVE_STOPS=true`) que sobrevive a la caída del proceso, la red o el servidor. Si el exchange rechaza ese stop, la posición se cierra en el acto (`protect_failed`): es preferible no operar a operar sin protección. El stop por software que se revisa cada `POLL_SECONDS` sigue activo como respaldo y es el único que existe en paper.

Al arrancar, `RECONCILE_ON_START` pone el estado de acuerdo con el exchange antes de operar:

- cancela órdenes huérfanas que llevan nuestro `CLIENT_ID_PREFIX` (un stop viejo con otra cantidad podría cerrar la posición nueva);
- si el exchange tiene **menos** cantidad que el estado local, asume que el stop nativo se ejecutó y ajusta hacia abajo;
- recoloca el stop nativo si falta;
- **nunca inventa posiciones** a partir del saldo: el BTC de la cuenta puede ser tuyo, no del bot.

Si el exchange no responde durante la reconciliación, el motor arranca **haltado** en lugar de adivinar. El `watchdog` avisa (`/api/events`) cuando el ciclo deja de avanzar más de `WATCHDOG_STALL_SECONDS` y, solo si pones `WATCHDOG_FLATTEN=true`, cierra todo lo abierto.

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

### Configurar desde el panel

La página `/config` lista 86 parámetros (símbolos, riesgo, costes, ventanas de entrenamiento, rejillas de estrategias, protección y autoajuste) con su valor actual. Al guardar:

- se valida sobre una copia de la configuración, incluidas las comprobaciones cruzadas (por ejemplo `HISTORY_BARS` frente a las ventanas del walk-forward): si algo no cuadra no se guarda nada y el panel muestra **todos** los errores juntos;
- se guarda **solo la clave cambiada** en la tabla `settings` de la base (el resto de parámetros no se toca);
- se aplica al instante si el motor lo lee en cada ciclo (riesgo, costes, símbolos, aprendizaje, watchdog). Lo que se construye una vez al arrancar (modo, fuente de datos, exchange, temporalidad, puerto, capital inicial, prefijo de órdenes, activar el watchdog) lleva la etiqueta **reinicio** y entra en vigor al reiniciar el proceso;
- añadir un símbolo lo incorpora al feed y pide un reentrenamiento para que tenga campeón; quitarlo exige que no haya posición abierta en él.

`EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `API_TOKEN`, `PANEL_USER`/`PANEL_PASSWORD` y `LIVE_CONFIRM` no aparecen en el panel: se editan a mano en `.env`. Pasar a `MODE=live` desde el panel solo funciona si esas tres cosas ya están configuradas; si no, la validación lo rechaza y explica qué falta.

---

## 10. Limitaciones conocidas (léelas)

- **El backtest simula la ejecución, pero sigue siendo una aproximación**: replica stop ATR, sizing, topes, cooldown y costos sobre velas cerradas. No modela huecos de liquidez, latencia, funding ni la diferencia entre el precio de la vela y el fill real; y el stop se vigila por vela, no intrabar. Los fills usan el cierre (o el open si hay hueco), no el libro de órdenes.
- **Sharpe y drawdown se calculan por vela**; el profit factor, el win rate y el R medio sí son por operación. Son aproximaciones útiles para filtrar, no estadísticas finas.
- **Riesgo de sobreajuste por selección múltiple:** probar muchas combinaciones siempre puede producir alguna que parezca buena por azar. Las puertas OOS y el margen de promoción lo reducen, no lo eliminan. Mantén las rejillas pequeñas.
- **Los stops en live son órdenes nativas del exchange** (`NATIVE_STOPS=true`), así que siguen protegiendo si el proceso muere; el software solo actúa de respaldo y es el que cierra en paper. Lo que **no** está resuelto: el stop nativo no se reajusta si el ATR cambia (queda al nivel de la entrada) y un cierre parcial no se distingue de un stop ejecutado más allá de la cantidad.
- **Live es spot, long-only, órdenes de mercado, un solo exchange y una sola moneda cotizada.** La reconciliación es conservadora: corrige cantidades hacia abajo y cancela órdenes huérfanas, pero si alguien cierra la posición a mano, el estado la elimina y avisa (`reconcile_needed`) — el PnL de ese tramo no se reconstruye. Revisa `/logs`.
- El entrenamiento corre en el mismo hilo del motor: con rejillas muy grandes pausa el ciclo unos segundos. `MAX_CANDIDATES` lo acota, `PARALLEL_WORKERS` reparte los candidatos entre núcleos (0 = automático, 1 = en serie) y el walk-forward solo simula el tramo que después puntúa — el reporte lo dice en `warmup_bars` y `sim_bars`.
- Los datos `synthetic` son un generador de prueba, **no** son mercado real; sirven para validar el mecanismo, no la rentabilidad.
- Qué se probó y qué no: 69 tests automatizados (estrategias sin lookahead, sizing, simulador, puertas del walk-forward, client order ids, stops nativos, reconciliación, watchdog y el editor de configuración) y el ciclo completo en paper sintético (apertura → cierre por stop → cash/equity → trade en SQLite). **No** se construyó la imagen Docker en esta máquina ni se probó contra un exchange real: la capa live se validó con un exchange falso, así que el primer paso en `EXCHANGE_SANDBOX=true` es mirar `/events` y comprobar que `protect`, `reconcile` y los cierres cuadran.
- **El panel escribe en `.env`**, así que necesita permiso de escritura sobre ese archivo (con Docker, no lo montes como `:ro`). No hay historial de cambios más allá de `.env.bak`, que se sobrescribe en cada guardado. Un cambio guardado pero marcado «reinicio» **no** está activo hasta que reinicies: si algo parece no aplicarse, mira esa etiqueta.
- Las páginas (`/`, `/results`, `/config`, `/logs`) embeben el `API_TOKEN` en el HTML (por eso no piden cabecera). No lo publiques sin TLS ni autenticación delante: usa túnel SSH o un reverse proxy. Si el token no te sirve para eso, define uno propio y no expongas el puerto.

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
requirements.txt          dependencias base (pip, compatibilidad con Docker)
.env                      configuración activa (copia de .env.example)
CONFIGURATION_STATUS.md   estado actual de la configuración
README_UV.md              documentación de uv
CONTEXT_UV.md             contexto de la integración uv
app/
  config.py           arranque desde .env y valores por defecto
  data.py             feed sintético y feed ccxt
  strategies.py       indicadores y las 4 estrategias
  sizing.py           fórmulas de sizing y stop/tp (motor + simulador)
  sim.py              simulación de la ejecución bar a bar para el walk-forward
  optimizer.py        walk-forward con ejecución simulada + champion/challenger
  regime.py           deriva y multiplicador de riesgo
  engine.py           ciclo, entradas/salidas, topes, kill switch, start/stop
  broker.py           paper y ccxt (órdenes, stops nativos, client order id)
  reconcile.py        conciliación con el exchange al arrancar (solo live)
  watchdog.py         avisa si el ciclo del motor deja de avanzar
  settings.py         parámetros editables: validación, base de datos y aplicación en caliente
  sweep.py            barridos símbolo × temporalidad: matriz de intentos y veredicto del conjunto
  api.py              API FastAPI (/api/*) + páginas web (/, /results, /config, /sweep, /logs)
  logs.py             buffer en memoria del registro del proceso (página /logs)
  main.py             arranque: motor + uvicorn
tests/                suite de pytest (sin red ni .env)
```

---

## 12. Flujos de trabajo comunes

### uv (recomendado)

```powershell
uv sync                                  # instala/actualiza dependencias
uv run python -m app.main                # motor + API + panel
uv run python -c "from app.config import Config; print(Config().public())"
uv run --with pytest pytest -q           # tests (sin red; ignora el .env)
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
| `401 credenciales inválidas` | usa HTTP Basic (`PANEL_USER`/`PANEL_PASSWORD`) o un `X-API-Token` que coincida con `API_TOKEN` |
| El panel dice "sin conexión" | el servidor no está arriba o cambió `API_PORT`; revisa la consola |
| No hay operaciones | el walk-forward no halló ventaja (`sin_ventaja`); ver nota de demo en la sección 1 |
| `uv` no reconocido | instala uv y abre una consola nueva: `irm https://astral.sh/uv/install.ps1 \| iex` |

---

> **Python:** el proyecto requiere 3.9–3.12. En Windows `python` puede apuntar a
> 2.7; usá siempre `uv run python ...` para que corra con el intérprete del `.venv`.