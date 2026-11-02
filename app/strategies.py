"""Estrategias. Cada una recibe el DataFrame OHLCV y devuelve un array de posiciones {-1,0,1}
decididas al CIERRE de cada vela usando solo datos pasados (sin look-ahead)."""
import itertools

import numpy as np
import pandas as pd


# ---------- indicadores ----------
def ema(s, n):
    return s.ewm(span=int(n), adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn)).fillna(50)


def atr(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------- ema_cross ----------
def grid_ema(cfg):
    return [{"fast": f, "slow": s} for f, s in itertools.product(cfg.ema_fast, cfg.ema_slow) if f < s]


def pos_ema(df, p, cfg):
    c = df["close"]
    up = (ema(c, p["fast"]) > ema(c, p["slow"])).to_numpy()
    pos = np.where(up, 1, -1 if cfg.allow_short else 0).astype(np.int8)
    pos[: int(p["slow"])] = 0
    return pos


# ---------- rsi_reversion ----------
def grid_rsi(cfg):
    return [{"period": a, "low": b, "high": c, "exit": d}
            for a, b, c, d in itertools.product(cfg.rsi_period, cfg.rsi_low, cfg.rsi_high, cfg.rsi_exit) if b < d < c]


def pos_rsi(df, p, cfg):
    r = rsi(df["close"], p["period"]).to_numpy()
    n = len(r)
    out = np.zeros(n, dtype=np.int8)
    st = 0
    for i in range(int(p["period"]) * 3, n):
        x = r[i]
        if st == 0:
            if x < p["low"]:
                st = 1
            elif x > p["high"] and cfg.allow_short:
                st = -1
        elif st == 1 and x >= p["exit"]:
            st = 0
        elif st == -1 and x <= 100 - p["exit"]:
            st = 0
        out[i] = st
    return out


# ---------- breakout (Donchian) ----------
def grid_breakout(cfg):
    return [{"lookback": a, "exit_ratio": b} for a, b in itertools.product(cfg.breakout_lookback, cfg.breakout_exit_ratio)]


def pos_breakout(df, p, cfg):
    L = int(p["lookback"])
    m = max(2, int(L * p["exit_ratio"]))
    hh = df["high"].shift(1).rolling(L).max().to_numpy()
    ll = df["low"].shift(1).rolling(L).min().to_numpy()
    xl = df["low"].shift(1).rolling(m).min().to_numpy()
    xs = df["high"].shift(1).rolling(m).max().to_numpy()
    c = df["close"].to_numpy()
    n = len(c)
    out = np.zeros(n, dtype=np.int8)
    st = 0
    for i in range(L + 1, n):
        if st == 0:
            if c[i] > hh[i]:
                st = 1
            elif c[i] < ll[i] and cfg.allow_short:
                st = -1
        elif st == 1 and c[i] < xl[i]:
            st = 0
        elif st == -1 and c[i] > xs[i]:
            st = 0
        out[i] = st
    return out


# ---------- ml_logit: regresión logística con refit rodante ----------
def grid_ml(cfg):
    return [{"train_bars": a, "threshold": b} for a, b in itertools.product(cfg.ml_train_bars, cfg.ml_threshold)]


def _features(df):
    c = df["close"]
    lc = np.log(c)
    r1 = lc.diff()
    f = pd.DataFrame({
        "r1": r1, "r3": lc.diff(3), "r10": lc.diff(10),
        "rsi": rsi(c, 14) / 100 - 0.5,
        "ema": c / ema(c, 20) - 1,
        "vol": r1.rolling(20).std(),
        "atr": atr(df, 14) / c,
    })
    return f.to_numpy()


def _fit_logit(X, y, l2, iters=25):
    n, d = X.shape
    Xb = np.c_[np.ones(n), X]
    w = np.zeros(d + 1)
    reg = np.r_[0.0, np.ones(d)] * l2
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) / n + reg * w
        H = (Xb.T * (p * (1 - p) + 1e-6)) @ Xb / n + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w


def pos_ml(df, p, cfg):
    X = _features(df)
    c = df["close"].to_numpy()
    n = len(c)
    H = int(cfg.ml_horizon)
    fwd = np.full(n, np.nan)
    fwd[:-H] = np.log(c[H:] / c[:-H])
    y = (fwd > 0).astype(float)
    tb = int(p["train_bars"])
    thr = float(p["threshold"])
    out = np.zeros(n, dtype=np.int8)
    start = tb + 60
    for s in range(start, n, int(cfg.ml_refit_bars)):
        lo, hi = s - tb, s - H + 1  # etiquetas conocidas solo hasta s (sin look-ahead)
        Xt, yt = X[lo:hi], y[lo:hi]
        ok = ~(np.isnan(Xt).any(axis=1) | np.isnan(fwd[lo:hi]))
        if ok.sum() < 100:
            continue
        mu, sd = Xt[ok].mean(0), Xt[ok].std(0) + 1e-12
        w = _fit_logit((Xt[ok] - mu) / sd, yt[ok], cfg.ml_l2)
        e = min(s + int(cfg.ml_refit_bars), n)
        Z = (X[s:e] - mu) / sd
        prob = 1 / (1 + np.exp(-np.clip(np.c_[np.ones(len(Z)), Z] @ w, -30, 30)))
        sig = np.where(prob > 0.5 + thr, 1, np.where(prob < 0.5 - thr, -1 if cfg.allow_short else 0, 0))
        sig[np.isnan(Z).any(axis=1)] = 0
        out[s:e] = sig
    return out


REGISTRY = {
    "ema_cross": (grid_ema, pos_ema),
    "rsi_reversion": (grid_rsi, pos_rsi),
    "breakout": (grid_breakout, pos_breakout),
    "ml_logit": (grid_ml, pos_ml),
}


def candidates(cfg):
    out = []
    for name in cfg.strategies:
        out += [(name, p) for p in REGISTRY[name][0](cfg)]
    return out


def positions(name, df, params, cfg):
    return REGISTRY[name][1](df, params, cfg)
