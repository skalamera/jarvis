"""Backtest BTC 15-min rules on real settled Kalshi markets.

Data: settled KXBTC15M markets (strike = floor_strike, result), 1-minute Kalshi candles (yes bid/ask close per
minute), and 1-minute BTC from Coinbase (proxy for CF BRTI). Each strategy sees, at each minute m (1..14) into a
market: up_ask = yes_ask, down_ask = 1 - yes_bid, BTC now, strike, minutes left. It buys at most once per market at
the ask of that minute (conservative: no better fills, taker fee charged). Cached to state/kalshi_bt/."""
from __future__ import annotations

import json
import math
import statistics as st
import time
from datetime import datetime
from typing import Any, Callable

import httpx

from . import store
from .kalshi import PUB, _f, _fee

DIR = store.STATE_DIR / "kalshi_bt"
DIR.mkdir(parents=True, exist_ok=True)


def _iso(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def settled(days: int) -> list[dict]:
    p = DIR / f"markets_{days}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 6 * 3600:
        return json.loads(p.read_text())
    out, cur, cutoff = [], "", time.time() - days * 86400
    with httpx.Client(timeout=30) as h:
        while True:
            r = h.get(f"{PUB}/markets", params={"series_ticker": "KXBTC15M", "status": "settled", "limit": 1000,
                                                **({"cursor": cur} if cur else {})}).json()
            ms = r.get("markets") or []
            for m in ms:
                if m.get("result") in ("yes", "no") and m.get("floor_strike"):
                    out.append({"ticker": m["ticker"], "open": _iso(m["open_time"]), "close": _iso(m["close_time"]),
                                "strike": float(m["floor_strike"]), "result": m["result"]})
            cur = r.get("cursor")
            if not cur or not ms or min(_iso(m["close_time"]) for m in ms) < cutoff:
                break
    out = sorted((m for m in out if m["close"] >= cutoff), key=lambda m: m["close"])
    p.write_text(json.dumps(out))
    return out


def candles(markets: list[dict]) -> dict[str, dict[int, tuple[float, float]]]:
    """ticker -> {minute_ts: (yes_bid, yes_ask)}"""
    p = DIR / "candles.json"
    have: dict = json.loads(p.read_text()) if p.exists() else {}
    need = [m for m in markets if m["ticker"] not in have]
    with httpx.Client(timeout=40) as h:
        for i in range(0, len(need), 20):
            chunk = need[i:i + 20]
            r = None
            for _ in range(6):
                r = h.get(f"{PUB}/markets/candlesticks", params={
                    "market_tickers": ",".join(m["ticker"] for m in chunk), "period_interval": 1,
                    "start_ts": min(m["open"] for m in chunk), "end_ts": max(m["close"] for m in chunk)})
                if r.status_code == 200:
                    break
                time.sleep(2)
            if r is None or r.status_code != 200:
                continue  # retry these next run
            for g in r.json().get("markets") or []:
                rows = {}
                for c in g.get("candlesticks") or []:
                    b, a = _f((c.get("yes_bid") or {}).get("close_dollars")), _f((c.get("yes_ask") or {}).get("close_dollars"))
                    if a:
                        rows[str(c["end_period_ts"])] = [b, a]
                if rows:
                    have[g["market_ticker"]] = rows
            time.sleep(0.2)
    p.write_text(json.dumps(have))
    return have


def btc(start: int, end: int) -> dict[int, float]:
    p = DIR / "btc.json"
    have: dict = json.loads(p.read_text()) if p.exists() else {}
    t = start - start % 60
    with httpx.Client(timeout=30, headers={"User-Agent": "jarvis"}) as h:
        while t < end:
            if str(t) in have and str(min(end, t + 17940) - min(end, t + 17940) % 60) in have:
                t += 18000
                continue
            r = h.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                      params={"granularity": 60, "start": datetime.utcfromtimestamp(t).isoformat(),
                              "end": datetime.utcfromtimestamp(min(end, t + 18000)).isoformat()})
            if r.status_code == 429:
                time.sleep(1.5)
                continue
            for row in r.json() if r.status_code == 200 else []:
                have[str(row[0] + 60)] = row[4]  # close at end of the minute
            t += 18000
            time.sleep(0.35)
    p.write_text(json.dumps(have))
    return {int(k): v for k, v in have.items()}


def _vol(px: dict[int, float], ts: int, n: int = 60) -> float:
    """Std-dev of 1-minute log returns over the previous n minutes."""
    xs = [px.get(ts - 60 * i) for i in range(n + 1)]
    xs = [x for x in xs if x]
    rets = [math.log(xs[i] / xs[i + 1]) for i in range(len(xs) - 1)]
    return st.pstdev(rets) if len(rets) > 10 else 0.0


def _z(px, m, ts) -> float | None:
    now, v = px.get(ts), _vol(px, ts)
    left = (m["close"] - ts) / 60
    if not now or not v or left <= 0:
        return None
    return math.log(now / m["strike"]) / (v * math.sqrt(left))


Strategy = Callable[[dict, int, float, float, float | None], tuple[str, float] | None]


def STRATS() -> dict[str, tuple[str, Strategy]]:
    def current(m, left, up, dn, z):  # his live rule: last 5 min, side >= 80%, max 97¢
        if left <= 5:
            for side, p in (("yes", up), ("no", dn)):
                if 0.80 <= p <= 0.97:
                    return side, p
    def band(lo, hi, last):
        def f(m, left, up, dn, z):
            if left <= last:
                for side, p in (("yes", up), ("no", dn)):
                    if lo <= p <= hi:
                        return side, p
        return f
    def zconf(zmin, lo, hi, last):
        def f(m, left, up, dn, z):
            if left <= last and z is not None:
                if z >= zmin and lo <= up <= hi:
                    return "yes", up
                if z <= -zmin and lo <= dn <= hi:
                    return "no", dn
        return f
    def value(edge, last):  # buy when model prob beats price by `edge`
        def f(m, left, up, dn, z):
            if left <= last and z is not None:
                pu = 0.5 * (1 + math.erf(z / math.sqrt(2)))
                if pu - up >= edge and up <= 0.95:
                    return "yes", up
                if (1 - pu) - dn >= edge and dn <= 0.95:
                    return "no", dn
        return f
    return {
        "current": ("Your rule: last 5m, ≥80¢ (≤97¢)", current),
        "band_80_92_3m": ("Last 3m, 80–92¢ only", band(0.80, 0.92, 3)),
        "band_85_95_2m": ("Last 2m, 85–95¢", band(0.85, 0.95, 2)),
        "z15_80_95_5m": ("BTC ≥1.5σ past target + 80–95¢, last 5m", zconf(1.5, 0.80, 0.95, 5)),
        "z2_70_95_5m": ("BTC ≥2σ past target + 70–95¢, last 5m", zconf(2.0, 0.70, 0.95, 5)),
        "value_8_6m": ("Model edge ≥8¢ vs price, last 6m", value(0.08, 6)),
        "value_12_8m": ("Model edge ≥12¢ vs price, last 8m", value(0.12, 8)),
    }


def run(days: int = 21, stake: float = 100.0) -> dict:
    ms = settled(days)
    cs = candles(ms)
    px = btc(ms[0]["open"] - 3600, ms[-1]["close"] + 60) if ms else {}
    res = {}
    for key, (label, fn) in STRATS().items():
        trades = []
        for m in ms:
            rows = cs.get(m["ticker"]) or {}
            for ts in range(m["open"] + 60, m["close"], 60):
                r = rows.get(str(ts))
                if not r:
                    continue
                yb, ya = r
                up, dn = ya, round(1 - yb, 4) if yb else 0
                if not (0 < up < 1 and 0 < dn < 1):
                    continue
                pick = fn(m, (m["close"] - ts) / 60, up, dn, _z(px, m, ts))
                if pick:
                    side, p = pick
                    n = int(stake // p)
                    while n and n * p + _fee(n, p) > stake:
                        n -= 1
                    if not n:
                        break
                    cost = n * p + _fee(n, p)
                    won = side == m["result"]
                    trades.append({"t": ts, "ticker": m["ticker"], "side": side, "price": p, "pnl": round((n if won else 0) - cost, 2), "won": won})
                    break
        pnl = [t["pnl"] for t in trades]
        eq, peak, dd, streak, worst = 0.0, 0.0, 0.0, 0, 0
        for x in pnl:
            eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
            streak = streak + 1 if x < 0 else 0; worst = max(worst, streak)
        wins = sum(t["won"] for t in trades)
        res[key] = {"label": label, "trades": len(trades), "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
                    "avg_price": round(st.mean(t["price"] for t in trades) * 100, 1) if trades else 0,
                    "pnl": round(sum(pnl), 2), "per_trade": round(sum(pnl) / len(trades), 2) if trades else 0,
                    "max_drawdown": round(dd, 2), "worst_streak": worst, "per_day": round(sum(pnl) / days, 2),
                    "curve": [round(sum(pnl[:i + 1]), 2) for i in range(0, len(pnl), max(1, len(pnl) // 120))]}
    out = {"days": days, "markets": len(ms), "with_candles": sum(1 for m in ms if cs.get(m["ticker"])), "stake": stake,
           "from": ms[0]["open"] if ms else None, "to": ms[-1]["close"] if ms else None, "results": res, "ran": time.time()}
    (DIR / "last.json").write_text(json.dumps(out))
    return out


def last() -> dict:
    p = DIR / "last.json"
    return json.loads(p.read_text()) if p.exists() else {}


CLICK_OPS = {"kalshi_backtest": run, "kalshi_backtest_last": last}
