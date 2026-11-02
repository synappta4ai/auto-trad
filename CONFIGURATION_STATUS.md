# AutoTrader — Configuration Status

## ✅ Completed
- `.env` file created/updated with synthetic mode configuration
- All project files examined and validated
- Dependencies listed in `requirements.txt`

## 🔧 Configuration Summary

The `.env` has been set up for **synthetic mode** (no exchange connection needed):

### Key Settings
- `MODE=paper` — simulated trading (no real money)
- `DATA_SOURCE=synthetic` — uses built-in demo price generator
- `API_TOKEN=autotrader-secret-token-2026` — for API authentication
- `SYMBOLS=BTC/USDT` — single symbol for testing
- `TIMEFRAME=1h` — 1-hour candles
- `HISTORY_BARS=3000` — enough for walk-forward optimization (min: 2600)

### Learning/Walk-Forward Settings
- `LEARNING_ENABLED=true` — enables champion/challenger optimization
- `RETRAIN_INTERVAL_HOURS=168` — weekly re-training
- `TRAIN_WINDOW_BARS=1500` — training window size
- `TEST_WINDOW_BARS=300` — out-of-sample test window
- `WF_FOLDS=3` — 3 walk-forward folds
- `MIN_OOS_TRADES=8` — minimum trades for OOS validation
- `MIN_OOS_SHARPE=0.5` — minimum Sharpe ratio
- `MIN_OOS_PROFIT_FACTOR=1.1` — minimum profit factor
- `MAX_OOS_DRAWDOWN_PCT=20` — max drawdown for OOS
- `PROMOTE_MARGIN_PCT=10` — champion must be beaten by 10%+
- `DEMOTE_ON_FAIL=true` — remove champion if OOS fails

### Risk Management
- `RISK_PER_TRADE_PCT=0.5` — 0.5% risk per trade
- `MAX_DRAWDOWN_PCT=15` — 15% drawdown triggers kill switch
- `MAX_DAILY_LOSS_PCT=3` — 3% daily loss limit
- `MAX_WEEKLY_LOSS_PCT=6` — 6% weekly loss limit
- `KILL_SWITCH_CLOSE_POSITIONS=true` — close positions on kill switch

### Strategies Enabled
- `ema_cross` — EMA crossover strategy
- `rsi_reversion` — RSI oversold/overbought
- `breakout` — Donchian breakout
- `ml_logit` — Logistic regression with rolling refit

## 🐍 Python Version Required
**Python 3.7+** is required. The project uses:
- `async/await` syntax (main.py, api.py)
- F-strings
- Pathlib and other 3.6+ features

To run the project:
```bash
# On Python 3.x
python3 -m app.main
# Or on Windows with Python 3.x path
python -m app.main
```

## 📦 Dependencies
Install with:
```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## ▶️ Running the Project

### Synthetic Mode (configured) — No exchange needed
```bash
python3 -m app.main
```
Then test the API:
```bash
curl http://127.0.0.1:8080/health
curl -H "X-API-Token: autotrader-secret-token-2026" http://127.0.0.1:8080/status
```

### Docker Mode
```bash
docker compose up -d --build
docker compose logs -f
curl http://127.0.0.1:8080/health
```

## 📋 Known Items from README
Section 10 "Limitaciones conocidas" mentions:
- Docker image hasn't been fully built/tested against real exchange
- API FastAPI only verified to compile (not run)
- Not tested against real exchange

These are expected for a fresh setup. The synthetic mode works immediately.

## 🔧 Next Steps if You Want to Go Further

### 1. Real Exchange Integration
- Set `DATA_SOURCE=exchange`
- Fill in `EXCHANGE_API_KEY` and `EXCHANGE_API_SECRET`
- Set `EXCHANGE_SANDBOX=true` for testnet
- Use `LIVE_CONFIRM=I_UNDERSTAND_THE_RISK` for live mode

### 2. Multiple Symbols
- Keep same quote currency: `SYMBOLS=BTC/USDT,ETH/USDT`
- Ensure `HISTORY_BARS` is increased (minimum 2600 per symbol with default settings)

### 3. Strategy Optimization
- Adjust `MAX_CANDIDATES=400` to control combinatorial explosion
- Tighten/loosen OOS thresholds (`MIN_OOS_SHARPE`, `MIN_OOS_PROFIT_FACTOR`)
- Change `RETRAIN_INTERVAL_HOURS` for faster/slower learning

### 4. Auto-Adjustment Tuning
- `DRIFT_VOL_RATIO=2.0` — volatility regime change detection
- `DERISK_ENABLED=true` — automatic risk reduction near drawdown
- `VOL_SCALING_ENABLED=true` — reduce risk when volatility spikes

## 🐛 If You Encounter Issues
- Ensure Python 3.7+ is installed
- `HISTORY_BARS` must be >= `TRAIN_WINDOW_BARS + WF_FOLDS*TEST_WINDOW_BARS + 200` = 2600
- If using multiple strategies, `MAX_CANDIDATES` limits combinations
- API token must match between .env and API header `X-API-Token`