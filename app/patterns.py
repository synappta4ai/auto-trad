"""Patrones de vela y condición de tendencia, medidos como el resto del laboratorio.

`binary.py` mide reglas construidas sobre retornos extremos. Este mide reglas
construidas sobre la geometría de las velas (cuerpo, mecha, secuencias) y sobre
la posición de dos medias móviles, con el mismo criterio de decisión:

  * la regla se elige en la ventana de **entrenamiento**;
  * se evalúa solo en la de **test**, sin haberla mirado al elegir;
  * el acierto se informa con intervalo de **Wilson**;
  * se compara contra el umbral ``1/(1+payout)``, que fija el bróker y no el
    modelo;
  * como aquí se prueban varias reglas a la vez, se informa el ajuste de
    **Benjamini-Hochberg** sobre los p-valores de test. Elegir la mejor de N
    celdas y creérsela es sesgo de selección, no ventaja: con 32 celdas nulas a
    alfa 0,05 se esperan ~1,6 falsos positivos y el máximo nulo ronda ya el
    56-58 % de acierto en ~1.000 apuestas.

Nada de esto opera: mide. Y una regla solo pasa si el IC 95 % inferior supera
el umbral **y** se repite en más de un símbolo, igual que en `binary.py`.
"""
import argparse
import json
import math

import numpy as np

from .binary import (breakeven, bets_to_prove, ev_per_bet, fit_windows, kelly,
                     wilson, _longest_streak, _max_drawdown)

MIN_TRAIN_BETS = 100
DEFAULT_ALPHA = 0.05


# --------------------------------------------------------------- patrones
def _base(close):
    """Rellena un vector de señal a cero con la misma longitud que `close`."""
    return np.zeros(len(close), dtype=np.int8)


def engulfing_bearish(open_, high, low, close):
    """Envuelve bajista: vela roja que engulle por completo el cuerpo verde previo.

    Señal **-1** en la vela que cierra el patrón.
    """
    o, c = np.asarray(open_, float), np.asarray(close, float)
    n = len(c)
    sig = _base(close)
    if n < 3:
        return sig
    prev, curr = slice(0, n - 1), slice(1, n)
    m = ((c[prev] > o[prev]) & (c[curr] < o[curr])
         & (o[curr] > c[prev]) & (c[curr] < o[prev]))
    sig[np.flatnonzero(m) + 1] = -1
    return sig


def engulfing_bullish(open_, high, low, close):
    """Envuelve alcista: vela verde que engulle por completo el cuerpo rojo previo.

    Señal **+1** en la vela que cierra el patrón.
    """
    o, c = np.asarray(open_, float), np.asarray(close, float)
    n = len(c)
    sig = _base(close)
    if n < 3:
        return sig
    prev, curr = slice(0, n - 1), slice(1, n)
    m = ((c[prev] < o[prev]) & (c[curr] > o[curr])
         & (o[curr] < c[prev]) & (c[curr] > o[prev]))
    sig[np.flatnonzero(m) + 1] = +1
    return sig


def _pin(open_, high, low, close, side):
    """Mecha larga: sombra de `side` al menos dos veces el cuerpo, otra sombra corta."""
    o, c = np.asarray(open_, float), np.asarray(close, float)
    h, l = np.asarray(high, float), np.asarray(low, float)
    body = np.abs(c - o)
    span = h - l
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    if side < 0:                       # estrella fugaz: mecha superior larga
        ok = (span > 0) & (body > 0) & (upper >= 2 * body) & (lower <= body)
    else:                              # martillo: mecha inferior larga
        ok = (span > 0) & (body > 0) & (lower >= 2 * body) & (upper <= body)
    sig = _base(close)
    sig[ok] = np.int8(side)
    return sig


def pin_bar_sell(open_, high, low, close):
    """Estrella fugaz: mecha superior >= 2 cuerpos. Señal **-1**."""
    return _pin(open_, high, low, close, -1)


def pin_bar_buy(open_, high, low, close):
    """Martillo: mecha inferior >= 2 cuerpos. Señal **+1**."""
    return _pin(open_, high, low, close, +1)


def three_green(open_, high, low, close):
    """Tres velas verdes seguidas, apuesta **contra** la secuencia (-1).

    Es el marco en el que se midió antes: la racha se toma como corrección que
    se deshace, no como continuación. Cambiarlo a +1 es una celda distinta y el
    ajuste de múltiples comparaciones la cubre igual.
    """
    o, c = np.asarray(open_, float), np.asarray(close, float)
    n = len(c)
    sig = _base(close)
    if n < 3:
        return sig
    bull = c > o
    m = bull[:-2] & bull[1:-1] & bull[2:]
    sig[np.flatnonzero(m) + 2] = -1
    return sig


def three_red(open_, high, low, close):
    """Tres velas rojas seguidas, apuesta **contra** la secuencia (+1)."""
    o, c = np.asarray(open_, float), np.asarray(close, float)
    n = len(c)
    sig = _base(close)
    if n < 3:
        return sig
    bear = c < o
    m = bear[:-2] & bear[1:-1] & bear[2:]
    sig[np.flatnonzero(m) + 2] = +1
    return sig


PATTERNS = {
    "engulfing_bajista": engulfing_bearish,
    "engulfing_alcista": engulfing_bullish,
    "pin_bar_venta": pin_bar_sell,
    "pin_bar_compra": pin_bar_buy,
    "3_verdes": three_green,
    "3_rojas": three_red,
}


def pattern_signals(open_, high, low, close):
    """Todas las señales de patrón, como ``{nombre: int8}`` con +1 / -1 / 0."""
    o = np.asarray(open_, float)
    h = np.asarray(high, float)
    l = np.asarray(low, float)
    c = np.asarray(close, float)
    if not (len(o) == len(h) == len(l) == len(c)):
        raise ValueError("open/high/low/close deben tener la misma longitud")
    return {name: fn(o, h, l, c) for name, fn in PATTERNS.items()}


# ---------------------------------------------------------------- tendencia
def sma(close, window):
    """Media móvil simple; NaN donde no caben `window` velas."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if window <= 0 or n < window:
        return out
    cs = np.concatenate(([0.0], np.cumsum(close)))
    out[window - 1:] = (cs[window:] - cs[:-window]) / window
    return out


def trend_direction(close, fast=10, slow=60):
    """+1 si la media rápida está sobre la lenta, -1 si está bajo, 0 si no hay datos.

    En 1m con 10/60 esto resultó **activo el 100 % del tiempo**: no filtra nada.
    Se deja medible en lugar de darlo por bueno, porque el coste de una
    condición inútil es el mismo que el de una útil: multiplica las celdas.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.zeros(n, dtype=np.int8)
    if fast <= 0 or slow <= fast or n < slow:
        return out
    f, s = sma(close, fast), sma(close, slow)
    ok = np.isfinite(f) & np.isfinite(s)
    out[ok & (f > s)] = 1
    out[ok & (f < s)] = -1
    return out


def gate_with_trend(signals, trend):
    """Señales que solo se emiten cuando coinciden con la tendencia."""
    trend = np.asarray(trend)
    out = {}
    for name, sig in signals.items():
        sig = np.asarray(sig)
        if sig.shape != trend.shape:
            raise ValueError(f"la señal {name} y la tendencia no coinciden")
        out[f"{name}@trend"] = np.where(sig == trend, sig, 0).astype(np.int8)
    return out


# -------------------------------------------------------------- evaluación
def _wins(sig, a, b, close, horizon):
    """Resultado booleano (acertó) de las apuestas de la señal en ``[a, b)``.

    El vencimiento tiene que caer dentro del propio tramo: si no, se estaría
    liquidando con precios de la ventana siguiente. Empate = pérdida, como en
    un bróker real de binarias.
    """
    idx = np.arange(a, b)
    idx = idx[sig[idx] != 0]
    if idx.size == 0:
        return np.empty(0, dtype=bool)
    idx = idx[idx + horizon <= b]
    if idx.size == 0:
        return np.empty(0, dtype=bool)
    move = np.sign(close[idx + horizon] - close[idx])
    return move == sig[idx]


def _p_above(k, n, p0):
    """``P(X >= k)`` con ``X ~ Bin(n, p0)``, aproximación normal con continuidad.

    Basta para ordenar celdas con n de cientos o miles; no hace falta scipy.
    """
    if n <= 0:
        return 1.0
    mu = n * p0
    sd = math.sqrt(n * p0 * (1.0 - p0))
    if sd <= 0:
        return 1.0
    z = (k - 0.5 - mu) / sd
    return max(0.0, min(1.0, 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))))


def benjamini_hochberg(pvals, alpha=DEFAULT_ALPHA):
    """Ajuste de Benjamini-Hochberg: ``([rechaza], [q])`` en el orden original.

    Controla la tasa de falsos positivos entre todas las celdas probadas, que es
    el problema real cuando se elige la mejor de N reglas. Más laxo que Bonferroni
    y con más potencia; asume independencia o correlación positiva, y las celdas
    de un mismo patrón en ventanas solapadas están correlacionadas, así que
    también se informa el número de pruebas para que el lector lo vea.
    """
    p = np.asarray(pvals, dtype=float)
    m = p.size
    if m == 0:
        return [], []
    order = np.argsort(p)
    ranked = p[order]
    scaled = ranked * m / np.arange(1, m + 1)
    scaled = np.minimum.accumulate(scaled[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.clip(scaled, 0.0, 1.0)
    return (q <= alpha).tolist(), q.tolist()


def evaluate_rules(close, signals, train_bars=20000, test_bars=4000, folds=5,
                   payout=0.82, horizon=1, alpha=DEFAULT_ALPHA):
    """Mide un juego de reglas con selección en train y evaluación en test.

    `signals` es ``{nombre: int8}`` con +1 (compra), -1 (venta) y 0 (sin señal).
    Devuelve por regla acierto de test con IC de Wilson, p-valor contra el
    umbral, q-value de BH, EV, drawdown, peor racha y desglose por fold; más la
    regla que habría elegido el entrenamiento y su resultado honesto en test.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    if not signals:
        raise ValueError("sin reglas que evaluar")
    for name, sig in signals.items():
        if np.asarray(sig).shape != (n,):
            raise ValueError(f"la señal {name} tiene {np.asarray(sig).shape} y hay {n} velas")
    train_bars, test_bars = fit_windows(n, horizon, train_bars, test_bars, folds)

    be = breakeven(payout)
    stats = {name: {"train_w": 0, "train_n": 0, "test_w": 0, "test_n": 0,
                    "ties": 0, "pnls": [], "folds": []} for name in signals}

    start, folds_used = 0, 0
    for k in range(int(folds)):
        tr_a, tr_b = start, start + train_bars
        te_a, te_b = tr_b, min(tr_b + test_bars, n - horizon)
        if tr_b > n or te_b - te_a < 50:
            break
        folds_used += 1
        for name, sig in signals.items():
            sig = np.asarray(sig)
            tw = _wins(sig, tr_a, tr_b, close, horizon)
            ew = _wins(sig, te_a, te_b, close, horizon)
            s = stats[name]
            s["train_w"] += int(tw.sum())
            s["train_n"] += int(tw.size)
            s["test_w"] += int(ew.sum())
            s["test_n"] += int(ew.size)
            if ew.size:
                s["pnls"].extend(np.where(ew, float(payout), -1.0).tolist())
                s["folds"].append({"fold": k + 1, "bets": int(ew.size),
                                   "wins": int(ew.sum()),
                                   "hit_pct": round(float(ew.mean()) * 100, 2)})
        start += test_bars

    if folds_used == 0:
        raise ValueError(f"sin folds utilizables con {n} velas "
                         f"(train={train_bars}, test={test_bars})")

    rules = []
    for name in signals:
        s = stats[name]
        bets, wins = s["test_n"], s["test_w"]
        if bets == 0:
            rules.append({"name": name, "bets": 0, "wins": 0, "verdict": "sin_datos"})
            continue
        p = wins / bets
        lo, hi = wilson(wins, bets)
        pnls = s["pnls"]
        rules.append({
            "name": name,
            "train_bets": s["train_n"],
            "train_hit_pct": round(s["train_w"] / s["train_n"] * 100, 3) if s["train_n"] else None,
            "bets": bets, "wins": wins, "ties": s["ties"],
            "hit_pct": round(p * 100, 3),
            "ci_low_pct": round(lo * 100, 3), "ci_high_pct": round(hi * 100, 3),
            "breakeven_pct": round(be * 100, 3),
            "ev_per_bet": round(ev_per_bet(p, payout), 4),
            "kelly_fraction": round(kelly(p, payout), 4),
            "net_stakes": round(float(np.sum(pnls)), 2),
            "max_drawdown_stakes": round(_max_drawdown(pnls), 2),
            "worst_loss_streak": _longest_streak(pnls),
            "bets_to_prove": bets_to_prove(p, payout),
            "p_value": round(_p_above(wins, bets, be), 6),
            "folds": s["folds"],
            "verdict": "ventaja" if lo > be else ("sin_ventaja" if hi < be else "indeterminado"),
        })

    # el ajuste solo tiene sentido sobre las celdas con apuestas
    idx = [i for i, r in enumerate(rules) if r.get("bets")]
    _, qs = benjamini_hochberg([rules[i]["p_value"] for i in idx], alpha)
    for i, q in zip(idx, qs):
        rules[i]["q_value"] = round(q, 6)
        rules[i]["fdr_reject"] = bool(q <= alpha)

    # selección honesta: la que habría elegido el entrenamiento, luego su test
    eligible = [r for r in rules
                if r.get("train_bets", 0) >= MIN_TRAIN_BETS and r.get("bets")]
    selected = None
    if eligible:
        best = max(eligible, key=lambda r: (r["train_hit_pct"], r["bets"]))
        selected = {"name": best["name"], "train_hit_pct": best["train_hit_pct"],
                    "test_hit_pct": best["hit_pct"], "test_ci_low_pct": best["ci_low_pct"],
                    "test_q_value": best.get("q_value"), "bets": best["bets"]}

    passed = [r["name"] for r in rules if r.get("fdr_reject") and r["ci_low_pct"] > be * 100]
    return {
        "payout": float(payout), "breakeven_pct": round(be * 100, 3),
        "horizon": int(horizon), "train_bars": train_bars, "test_bars": test_bars,
        "folds": int(folds), "folds_used": folds_used, "tests": len(idx), "alpha": float(alpha),
        "rules": rules, "selected": selected, "passed": passed,
        "verdict": ("ventaja" if passed else
                    ("indeterminado" if any(r.get("verdict") == "indeterminado" for r in rules)
                     else "sin_ventaja")),
    }


def summarize_rules(per_symbol, alpha=DEFAULT_ALPHA):
    """¿Qué regla se repite entre símbolos en vez de ser una celda afortunada?"""
    seen, passed = {}, {}
    for res in per_symbol:
        sym = res.get("symbol", "?")
        for r in res.get("rules", []):
            if not r.get("bets"):
                continue
            name = r["name"]
            seen.setdefault(name, []).append(sym)
            if r.get("fdr_reject") and r["ci_low_pct"] > res["breakeven_pct"]:
                passed.setdefault(name, []).append(sym)
    out = {}
    for name, syms in seen.items():
        winners = passed.get(name, [])
        out[name] = {"symbols_tested": len(syms), "symbols_passed": len(winners),
                     "passed_in": winners,
                     "replicable": len(winners) >= max(2, len(syms) // 2)}
    return {"rules": out,
            "verdict": ("repetible" if any(v["replicable"] for v in out.values())
                        else ("aislado" if any(v["symbols_passed"] for v in out.values())
                              else "sin_ventaja"))}


# -------------------------------------------------------------------- CLI
def main(argv=None):
    from .config import Config
    from .store import Store

    ap = argparse.ArgumentParser(description="Patrones de vela y tendencia (backtest, no operar)")
    ap.add_argument("--symbols", default="", help="BASE/QUOTE separados por coma")
    ap.add_argument("--timeframe", default="1m")
    ap.add_argument("--payout", type=float, default=0.82)
    ap.add_argument("--train", type=int, default=8000)
    ap.add_argument("--test", type=int, default=2000)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--trend", action="store_true",
                    help="añade la variante de cada patrón alineada con la tendencia")
    ap.add_argument("--fast", type=int, default=10)
    ap.add_argument("--slow", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    cfg = Config()
    store = Store(cfg.data_dir)
    symbols = [s.strip().upper()
               for s in (a.symbols or ",".join(cfg.symbols)).split(",") if s.strip()]

    results = []
    for sym in symbols:
        df = store.candles(sym, a.timeframe)
        if df is None or not len(df):
            print(f"{sym}: sin velas de {a.timeframe}")
            continue
        sigs = pattern_signals(df["open"].to_numpy(), df["high"].to_numpy(),
                               df["low"].to_numpy(), df["close"].to_numpy())
        if a.trend:
            sigs.update(gate_with_trend(
                sigs, trend_direction(df["close"].to_numpy(), a.fast, a.slow)))
        res = evaluate_rules(df["close"].to_numpy(), sigs, train_bars=a.train,
                             test_bars=a.test, folds=a.folds, payout=a.payout,
                             horizon=a.horizon)
        res["symbol"] = sym
        res["timeframe"] = a.timeframe
        results.append(res)

        print(f"\n{sym} {a.timeframe} · payout {a.payout} · umbral {res['breakeven_pct']:.2f}% "
              f"· {res['folds_used']} folds · {res['tests']} pruebas")
        for r in res["rules"]:
            if not r.get("bets"):
                print(f"  {r['name']:<20} sin apuestas")
                continue
            print(f"  {r['name']:<20} train {r['train_hit_pct']:6.2f}%  "
                  f"test {r['hit_pct']:6.2f}% [{r['ci_low_pct']:5.2f}, {r['ci_high_pct']:5.2f}] "
                  f"n={r['bets']:<5} q={r.get('q_value', float('nan')):.4f} "
                  f"-> {r['verdict']}")
        if res["selected"]:
            s = res["selected"]
            print(f"  elegida en train: {s['name']} (train {s['train_hit_pct']:.2f}%) "
                  f"-> test {s['test_hit_pct']:.2f}% "
                  f"[{s['test_ci_low_pct']:.2f}] q={s['test_q_value']}")
        print(f"  veredicto: {res['verdict']}")

    if results:
        print("\n" + json.dumps(summarize_rules(results), indent=2, ensure_ascii=False))
    if a.json:
        print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
