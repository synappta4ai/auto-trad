# AutoTrader — sistema de trading automático con auto-ajuste (Docker)

Bot de trading para correr 24/7 en tu propio servidor. Todo es configurable desde `.env`: puerto, nombre del contenedor, topes de riesgo, estrategias, frecuencia de reentrenamiento y reglas de auto-ajuste.

> **Lee esto primero.** Ningún software garantiza ganancias, y la mayoría de los sistemas automáticos retail pierden dinero tras costos. Este proyecto está diseñado para **no operar cuando no encuentra una ventaja demostrable fuera de muestra** (queda en liquidez) y para limitar pérdidas con topes duros. Eso reduce el riesgo, no lo elimina. Empieza siempre en `MODE=paper`, luego testnet del exchange y solo después, con capital que puedas perder, `live`. No es asesoría financiera.

---

## 📦 Instalación Rápida

### Opción 1: Con uv (recomendado — instalación 10-100x más rápida)

```bash
# Windows PowerShell
.\install-with-uv.ps1

# O manualmente:
uv venv                      # crea .venv con Python 3.7+
. .venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
uv run python -m app.main
```

### Opción 2: Con pip tradicional

```bash
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

---

## 1. Inicio rápido

```bash
cp .env.example .env          # edita API_TOKEN, CONTAINER_NAME, API_PORT, etc.
# Si usas uv: .\install-with-uv.ps1  (o pasos manuales arriba)
docker compose up -d --build
docker compose logs -f        # ver qué hace
curl http://127.0.0.1:8080/health
curl -H "X-API-Token: TU_TOKEN" http://127.0.0.1:8080/status
```

Por defecto arranca en **`MODE=paper` + `DATA_SOURCE=synthetic`**: precios simulados con regímenes cambiantes, sin red ni dinero. Sirve para ver el ciclo completo (entrenar → promover → operar → reentrenar). Para ver el reentrenamiento en la demo pon `RETRAIN_INTERVAL_HOURS=0.05` y `POLL_SECONDS=5`.

Para datos reales sin arriesgar nada: `DATA_SOURCE=exchange`, `MODE=paper` (usa velas reales y opera en simulado).

Cambiar cualquier valor: edita `.env` y ejecuta `docker compose up -d` (se recrea el contenedor; el estado persiste en el volumen).

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

Un solo contenedor: un hilo con el motor y un servidor HTTP para monitoreo/control. Estado persistente en SQLite dentro del volumen `/data`, por lo que un reinicio retoma posiciones, campeones y contadores.

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
| Manual                                                              | `POST /retrain`                                           |
| Arranque                                                            | siempre una vez                                           |

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
| `MAX_DRAWDOWN_PCT`                              | **Kill switch**: bloquea entradas y (con `KILL_SWITCH_CLOSE_POSITIONS`) cierra todo. Se libera solo con `POST /resume` (reinicia el pico al equity actual) |
| `MIN_NOTIONAL`                                  | Evita órdenes diminutas                                                                                                                                    |

---

## 7. API (cabecera `X-API-Token`)

| Endpoint                                             | Función                                                                                    |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `GET /health`                                        | Estado básico (sin token; lo usa el healthcheck de Docker)                                 |
| `GET /status`                                        | Equity, caja, drawdown, posiciones, campeones, multiplicadores de riesgo                   |
| `GET /learning`                                      | Último reporte walk-forward por símbolo: folds, métricas OOS, decisión, motivos de rechazo |
| `GET /trades?limit=` · `GET /equity` · `GET /events` | Historial                                                                                  |
| `GET /config`                                        | Configuración efectiva (secretos ocultos)                                                  |
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

```bash
docker compose logs -f --tail 200            # logs
docker compose up -d --build                 # aplicar cambios de .env o de código
docker compose down                          # parar (conserva el volumen)
docker compose down -v                       # parar y BORRAR estado/historial
# backup de la base:
docker run --rm -v ${VOLUME_NAME:-autotrader_data}:/data -v "$PWD":/backup alpine tar czf /backup/autotrader-data.tgz -C /data .
```

Para reiniciar desde cero el capital simulado: `docker compose down -v` y levantar de nuevo.

---

## 10. Limitaciones conocidas (léelas)

- **El backtest del optimizador es vectorizado y no incluye el stop ATR ni el dimensionamiento**: mide la calidad de la _señal_ con costos (comisión, slippage, spread) por cambio de posición. El stop y el sizing actúan en vivo como capa de riesgo, por lo que el resultado real difiere del backtest.
- **Sharpe y profit factor se calculan por vela**, no por operación: son aproximaciones útiles para filtrar, no estadísticas finas.
- **Riesgo de sobreajuste por selección múltiple:** probar muchas combinaciones siempre puede producir alguna que parezca buena por azar. Las puertas OOS y el margen de promoción lo reducen, no lo eliminan. Mantén las rejillas pequeñas.
- **Los stops en live los vigila el software** (cada `POLL_SECONDS`), no son órdenes nativas del exchange. Si el servidor o la red caen, la posición queda sin protección. Para producción seria añade stops nativos del exchange.
- **Live es spot, long-only, órdenes de mercado, un solo exchange y una sola moneda cotizada.** La conciliación con el exchange es mínima: se limita la venta al saldo disponible y se registran eventos `partial_close` o `reconcile_needed` si algo no cuadra. Revisa `/events`.
- El entrenamiento corre en el mismo hilo del motor; con rejillas muy grandes pausa el ciclo unos segundos (`MAX_CANDIDATES` lo acota).
- Los datos `synthetic` son un generador de prueba, **no** son mercado real; sirven para validar el mecanismo, no la rentabilidad.
- Qué se probó y qué no: el motor, las estrategias, el optimizador, el kill switch, la contabilidad y la ruta live (con un exchange _simulado_) se probaron con datos sintéticos. **No** se construyó la imagen Docker, **no** se ejecutó la API FastAPI (solo se verificó que compila) y **no** se probó contra un exchange real. Espera encontrar pequeños ajustes en el primer `docker compose up --build`; revisa `docker compose logs`.

---

## 11. Estructura

```
.env.example          todos los parámetros documentados
pyproject.toml        configuración uv (metadatos, dependencias, scripts)
docker-compose.yml    contenedor, puerto, volumen, límites (todo desde .env)
Dockerfile
CONFIGURATION_STATUS.md   resumen de configuración actual
CONTEXT_UV.md           contexto de integración uv
README_UV.md            documentación completa de uv
install-with-uv.ps1     script PowerShell para instalar con uv
.env                    configuración activa (copia de .env.example)
requirements.txt        dependencias pip (compatibilidad tradicional)
app/
  config.py           lectura y validación de variables de entorno
  data.py             feed sintético y feed ccxt
  strategies.py       indicadores y las 4 estrategias
  optimizer.py        backtest vectorizado + walk-forward + champion/challenger
  regime.py           deriva y multiplicador de riesgo
  engine.py           ciclo, entradas/salidas, topes, kill switch, programador
  broker.py           paper y ccxt
  api.py / main.py    API FastAPI y arranque
```

---

## 🔧 Nuevas adiciones

### Integración uv

Se ha añadido soporte moderno de paquetes con [uv](https://astral.sh/uv), un instalador de Python escrito en Rust que es 10-100x más rápido que pip.

**Nuevos archivos:**
- `pyproject.toml` - Metadatos del proyecto, dependencias y scripts uv
- `README_UV.md` - Documentación completa de uv
- `CONTEXT_UV.md` - Este contexto de integración
- `install-with-uv.ps1` - Script PowerShell para instalación automática

**Beneficios:**
- Instalaciones 10-100x más rápidas
- Lockfile automático (`uv.lock`) para instancias reproducibles
- `uv sync` para instalar todas las dependencias con un comando
- `uv add <paquete>` para añadir nuevas dependencias

### Configuración .env

El archivo `.env` ha sido configurado para modo **synthetic** (pruebas sin necesidad de exchange):
- `MODE=paper` - trading simulado
- `DATA_SOURCE=synthetic` - datos generados localmente
- `API_TOKEN=autotrader-secret-token-2026` - token de autenticación API
- Todos los parámetros de riesgo, estrategias y aprendizaje pre-configurados

Ver `.env` y `.env.example` para detalles completos.

### Documentación adicional

- `CONFIGURATION_STATUS.md` - Resumen del estado actual de la configuración
- `README_UV.md` - Documentación detallada sobre cómo usar uv con este proyecto

---

## 📋 Flujos de trabajo comunes

### Con uv (rápido y recomendado)

```powershell
# 1. Instalar
.\install-with-uv.ps1

# 2. Ejecutar
uv run python -m app.main

# 3. Probar API
curl http://127.0.0.1:8080/health
curl -H "X-API-Token: autotrader-secret-token-2026" http://127.0.0.1:8080/status
```

### Con pip (tradicional)

```bash
python3 -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

### Añadir una nueva dependencia

```powershell
# Con uv
uv add nombre-del-paquete

# Con pip
pip install nombre-del-paquete
# Luego actualiza requirements.txt si es necesario
```

### Con Docker

```bash
docker compose up -d --build     # aplicar cambios de .env o código
docker compose logs -f           # ver logs en tiempo real
docker compose down              # parar (conserva volumen en /data)
docker compose down -v           # parar y borrar estado/historial
```

---

> **Importante:** Este proyecto requiere **Python 3.7+**. El sistema actual tiene Python 2.7, por lo que necesitas Python 3.x para ejecutarlo. Consulta la sección "Python Version Required" en CONFIGURATION_STATUS.md para más detalles.