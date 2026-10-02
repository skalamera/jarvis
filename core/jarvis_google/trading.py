"""JARVIS trading desk on Kraken: portfolio (crypto + stocks), cost basis / P&L from the ledger, live quotes and
charts, investment intelligence (signals + model-written buy/hold/trim/sell reads), price alerts and monitoring, and
order entry. EVERY live order goes through a confirm card (spoken "confirm" or the Authorize click) via
store.propose -> store.execute_action; nothing here places an order on its own.

Credentials: KRAKEN_API_KEY / KRAKEN_API_SECRET in ~/.hermes/.env (never in the repo).
State: STATE_DIR/kraken_ledger.json (incremental ledger cache), trade_alerts.json, trade_insights.json.

Kraken notes (verified against this account):
- Crypto: standard spot pairs (XBTUSD...), AddOrder works; validate=true checks without placing.
- Stocks: balances show as "TSLA.EQ"; history shows pairs "TSLAZUSD" (aclass equity_pair) but the REST API rejects
  every equity order format ("Unknown asset pair") and xStocks are US-locked. Stock orders are still validated with
  Kraken first; if rejected, the desk says so and hands off to the Kraken app.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import store

API = "https://api.kraken.com"
LEDGER = store.STATE_DIR / "kraken_ledger.json"
ALERTS = store.STATE_DIR / "trade_alerts.json"
INSIGHTS = store.STATE_DIR / "trade_insights.json"
MONITOR = store.STATE_DIR / "trade_monitor.json"
DUST_USD = 0.50            # positions below this are hidden as dust
MAX_DRIFT = 0.03           # a market order aborts if the price moved >3% between preview and authorization
CRYPTO_PROXIES = {"MSTR", "MARA", "COIN", "CRCL", "ETHU", "RIOT", "CLSK", "HOOD", "BITO", "IBIT", "GLXY", "BMNR"}
_nonce_lock = threading.Lock()
_last_nonce = 0
_cache: dict[str, tuple[float, Any]] = {}
_clock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="trade")
DISPLAY = {"XBT": "BTC", "XDG": "DOGE", "XXBT": "BTC", "XETH": "ETH", "XXDG": "DOGE", "XXRP": "XRP", "XLTC": "LTC",
           "XXLM": "XLM", "XZEC": "ZEC", "XXMR": "XMR", "XETC": "ETC", "XREP": "REP", "XMLN": "MLN", "ZUSD": "USD"}
NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "USDC": "USD Coin", "DOGE": "Dogecoin", "XRP": "XRP",
         "ADA": "Cardano", "BABY": "Babylon", "ORCA": "Orca", "LINK": "Chainlink", "AVAX": "Avalanche"}


# ------------------------------------------------------------------ plumbing
def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in f.read_text().splitlines():
            m = re.match(rf"\s*(?:export\s+)?{name}\s*=\s*(.*)", line)
            if m and m[1].strip():
                return m[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def configured() -> bool:
    return bool(_env("KRAKEN_API_KEY") and _env("KRAKEN_API_SECRET"))


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _clock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _clock:
        _cache[key] = (now, val)
    return val


def _bust(*prefixes: str) -> None:
    with _clock:
        for k in list(_cache):
            if k.startswith(prefixes):
                _cache.pop(k, None)


def _nonce() -> str:
    global _last_nonce
    with _nonce_lock:
        n = max(time.time_ns() // 1000, _last_nonce + 1)
        _last_nonce = n
        return str(n)


_priv_lock = threading.Lock()


def _priv(method: str, data: dict | None = None) -> Any:
    key, secret = _env("KRAKEN_API_KEY"), _env("KRAKEN_API_SECRET")
    if not (key and secret):
        raise RuntimeError("Kraken isn't connected: KRAKEN_API_KEY / KRAKEN_API_SECRET are missing in ~/.hermes/.env.")
    path = f"/0/private/{method}"
    for attempt in range(4):
        with _priv_lock:  # serialize so nonces arrive in order
            body = {k: v for k, v in (data or {}).items() if v is not None}
            body["nonce"] = _nonce()
            post = urllib.parse.urlencode(body)
            sha = hashlib.sha256((body["nonce"] + post).encode()).digest()
            sig = base64.b64encode(hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512).digest()).decode()
            r = httpx.post(API + path, data=body, headers={"API-Key": key, "API-Sign": sig}, timeout=20)
        j = r.json()
        errs = j.get("error") or []
        if any("Rate limit" in e or "EAPI:Invalid nonce" in e for e in errs) and attempt < 3:
            time.sleep(2.5 * (attempt + 1))
            continue
        if errs:
            raise RuntimeError("Kraken: " + "; ".join(errs))
        return j.get("result")
    raise RuntimeError("Kraken rate limit; try again in a moment.")


def _pub(method: str, **params) -> Any:
    j = httpx.get(f"{API}/0/public/{method}", params=params, timeout=20).json()
    if j.get("error"):
        raise RuntimeError("Kraken: " + "; ".join(j["error"]))
    return j["result"]


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, nd)


# ------------------------------------------------------------------ assets / pairs
def _assets() -> dict:
    return _cached("assets", 86400, lambda: _pub("Assets"))


def _pairs() -> dict:
    return _cached("pairs", 86400, lambda: _pub("AssetPairs"))


def sym_of(asset: str) -> tuple[str, str]:
    """Kraken ledger / balance asset -> (kind, display symbol). kind: cash | crypto | stock."""
    if asset.endswith(".EQ"):
        return "stock", asset[:-3]
    base = asset.split(".")[0]  # SOL.F (earn), USD.HOLD, ETH2.S
    if base in ("ZUSD", "USD"):
        return "cash", "USD"
    if base == "ETH2":
        base = "XETH"
    alt = (_assets().get(base) or {}).get("altname") or base
    s = DISPLAY.get(base) or DISPLAY.get(alt) or alt
    return "crypto", s


def _crypto_pair(symbol: str) -> dict | None:
    """Display symbol (BTC) -> the USD spot pair {key, altname, wsname, lot_decimals, pair_decimals, ordermin, costmin}."""
    want = symbol.upper()
    kr = {"BTC": "XBT", "DOGE": "XDG"}.get(want, want)
    for k, p in _pairs().items():
        ws = p.get("wsname") or ""
        if ws in (f"{kr}/USD", f"{want}/USD") and p.get("status", "online") == "online":
            return {"key": k, **p}
    return None


# ------------------------------------------------------------------ prices
def crypto_tickers(symbols: list[str]) -> dict[str, dict]:
    pairs = {s: _crypto_pair(s) for s in symbols}
    keys = [p["key"] for p in pairs.values() if p]
    if not keys:
        return {}

    def fetch():
        return _pub("Ticker", pair=",".join(keys))
    t = _cached("tick:" + ",".join(sorted(keys)), 15, fetch)
    out = {}
    for s, p in pairs.items():
        if not p or p["key"] not in t:
            continue
        x = t[p["key"]]
        last, opn = _f(x["c"][0]), _f(x["o"])
        out[s] = {"price": last, "open": opn, "change_pct": (last / opn - 1) * 100 if opn else None,
                  "bid": _f(x["b"][0]), "ask": _f(x["a"][0]), "high": _f(x["h"][1]), "low": _f(x["l"][1]),
                  "volume": _f(x["v"][1]), "pair": p["key"], "wsname": p.get("wsname")}
    return out


def stock_quotes(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    from . import markets

    def one(s: str) -> tuple[str, dict]:
        def fetch():
            fi = markets._yf().Ticker(s).fast_info
            last, prev = _f(fi.get("lastPrice")), _f(fi.get("previousClose"))
            return {"price": last, "prev_close": prev, "change_pct": (last / prev - 1) * 100 if prev else None,
                    "high": _f(fi.get("dayHigh")), "low": _f(fi.get("dayLow")), "year_high": _f(fi.get("yearHigh")),
                    "year_low": _f(fi.get("yearLow")), "market_cap": _f(fi.get("marketCap"))}
        try:
            return s, _cached(f"sq:{s}", 30, fetch)
        except Exception:
            return s, {}
    return dict(_pool.map(one, symbols))


# ------------------------------------------------------------------ ledger -> cost basis
def _ledger(refresh: bool = True) -> list[dict]:
    """All ledger entries, oldest first; fetched incrementally and cached on disk."""
    try:
        cache = json.loads(LEDGER.read_text())
    except Exception:
        cache = {"entries": {}, "last": 0}
    if refresh and configured():
        def pull():
            start = max(0, cache["last"] - 1)
            ofs = 0
            while True:
                r = _priv("Ledgers", {"ofs": ofs, "start": start} if start else {"ofs": ofs})
                got = r.get("ledger") or {}
                cache["entries"].update(got)
                ofs += len(got)
                if not got or ofs >= int(r.get("count") or 0):
                    break
            if cache["entries"]:
                cache["last"] = max(_f(v["time"]) for v in cache["entries"].values())
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            LEDGER.write_text(json.dumps(cache))
            os.chmod(LEDGER, 0o600)
            return True
        _cached("ledger_pull", 120, pull)
    return sorted(({"id": k, **v} for k, v in cache["entries"].items()), key=lambda e: _f(e["time"]))


def cost_basis(entries: list[dict]) -> dict[str, dict]:
    """Average-cost basis per symbol from the ledger.

    Coins deposited from outside Kraken have an UNKNOWN basis: they're tracked as `unk` (unknown-basis quantity)
    and never treated as $0 cost, so realized / unrealized P&L only covers the known-basis part of a position.
    Returns {sym: {qty, cost, unk, realized, realized_unknown_qty, income_qty, kind, ...}} plus "__totals__"."""
    pos: dict[str, dict] = {}
    dividends = fees = 0.0

    def P(sym: str, kind: str) -> dict:
        return pos.setdefault(sym, {"qty": 0.0, "cost": 0.0, "unk": 0.0, "realized": 0.0, "realized_unknown_qty": 0.0,
                                    "income_qty": 0.0, "kind": kind, "first": None, "buys": 0, "sells": 0})

    def reduce(p: dict, q: float) -> tuple[float, float]:
        """Take q out of a position pro-rata between known- and unknown-basis lots. Returns (cost_removed, unknown_q)."""
        if p["qty"] <= 1e-12 or q <= 0:
            p["qty"] -= q
            return 0.0, 0.0
        take = min(q, p["qty"])
        frac = take / p["qty"]
        unk_q = p["unk"] * frac
        cost_out = p["cost"] * frac
        p["cost"] -= cost_out
        p["unk"] -= unk_q
        p["qty"] -= q
        return cost_out, unk_q

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        rid = e.get("refid") or e["id"]
        if rid not in groups:
            order.append(rid)
        groups.setdefault(rid, []).append(e)
    for rid in order:
        g = groups[rid]
        legs = []
        for e in g:
            kind, sym = ("stock", e["asset"]) if e.get("aclass") == "equity" else sym_of(e["asset"])
            legs.append((kind, sym, _f(e["amount"]), _f(e.get("fee")), e))
        typ = g[0]["type"]
        usd = [l for l in legs if l[0] == "cash"]
        assets = [l for l in legs if l[0] != "cash"]
        if typ == "dividend":
            dividends += sum(l[2] for l in usd)
            continue
        if typ == "equityfee":
            fees += -sum(l[2] for l in usd)
            continue
        if typ in ("transfer", "margin") or typ.startswith("fcm"):
            continue
        if typ == "corporateaction":  # splits change quantity, not cost
            for kind, sym, amt, fee, e in assets:
                p = P(sym, kind)
                if p["qty"] > 1e-12 and p["unk"]:
                    p["unk"] *= (p["qty"] + amt) / p["qty"]
                p["qty"] += amt
            continue
        if usd and len(assets) == 1:  # buy / sell against USD
            kind, sym, amt, fee, e = assets[0]
            p = P(sym, kind)
            p["first"] = p["first"] or _f(e["time"])
            cash = sum(l[2] for l in usd)
            cash_fee = sum(l[3] for l in usd)
            fees += cash_fee
            amt -= fee
            if amt > 0:
                p["qty"] += amt
                p["cost"] += -cash + cash_fee
                p["buys"] += 1
            elif amt < 0:
                q = -amt
                cost_out, unk_q = reduce(p, q)
                known = 1 - (unk_q / q if q else 0)
                p["realized"] += (cash - cash_fee) * known - cost_out
                p["realized_unknown_qty"] += unk_q
                p["sells"] += 1
            continue
        if not usd and len(assets) == 2 and (assets[0][2] < 0) != (assets[1][2] < 0):  # convert A -> B
            out = next(l for l in assets if l[2] < 0)
            inn = next(l for l in assets if l[2] > 0)
            a, b = P(out[1], out[0]), P(inn[1], inn[0])
            q = -out[2] + out[3]
            unk_frac = (a["unk"] / a["qty"]) if a["qty"] > 1e-12 else 1.0
            cost_out, _ = reduce(a, q)
            got = inn[2] - inn[3]
            b["qty"] += got
            b["cost"] += cost_out
            b["unk"] += got * min(max(unk_frac, 0), 1)
            b["first"] = b["first"] or _f(inn[4]["time"])
            continue
        for kind, sym, amt, fee, e in assets:  # deposits, withdrawals, staking, rewards, dust
            p = P(sym, kind)
            net = amt - fee
            if net > 0:
                p["qty"] += net
                if typ in ("staking", "reward", "earn", "airdrop"):
                    p["income_qty"] += net  # zero-cost income (taxable when received)
                elif typ == "deposit":
                    p["unk"] += net
            elif net < 0:
                reduce(p, -net)
    for p in pos.values():
        if p["qty"] < 1e-9:
            p["qty"] = max(p["qty"], 0.0)
            p["cost"] = p["unk"] = 0.0
        p["unk"] = min(max(p["unk"], 0.0), p["qty"])
    pos["__totals__"] = {"dividends": dividends, "fees": fees, "kind": "meta"}
    return pos


# ------------------------------------------------------------------ portfolio
def portfolio(record: bool = True) -> dict:
    """Holdings with live value, day change, cost basis and P&L; also opens / refreshes the trading desk."""
    def build():
        bal = _priv("Balance")
        held: dict[tuple[str, str], float] = {}
        for a, q in bal.items():
            kind, s = sym_of(a)
            held[(kind, s)] = held.get((kind, s), 0.0) + _f(q)
        crypto = [s for (k, s), q in held.items() if k == "crypto" and q > 0]
        stocks = [s for (k, s), q in held.items() if k == "stock" and q > 0]
        cf, sf = _pool.submit(crypto_tickers, crypto), _pool.submit(stock_quotes, stocks)
        basis_f = _pool.submit(lambda: cost_basis(_ledger()))
        cq, sq = cf.result(), sf.result()
        try:
            basis = basis_f.result()
        except Exception:
            basis = {}
        cash = held.get(("cash", "USD"), 0.0)
        rows = []
        for (kind, s), q in held.items():
            if kind == "cash" or q <= 0:
                continue
            quote = (cq if kind == "crypto" else sq).get(s) or {}
            px = quote.get("price")
            if kind == "crypto" and s in ("USDC", "USDT", "DAI", "PYUSD") and not px:
                px = 1.0
            val = q * px if px else None
            if val is not None and val < DUST_USD:
                continue
            b = basis.get(s) or {}
            known_q = (b.get("qty") or 0) - (b.get("unk") or 0)
            avg = b["cost"] / known_q if known_q > 1e-12 and b.get("cost", 0) > 0 else None
            unk_share = (b.get("unk") or 0) / b["qty"] if b.get("qty", 0) > 1e-12 else 0
            cost_q = q * (1 - unk_share)  # P&L only on the known-basis part
            cost = avg * cost_q if avg else None
            day_pct = quote.get("change_pct")
            rows.append({
                "symbol": s, "name": NAMES.get(s, s), "kind": kind, "qty": q, "price": _r(px, 6 if px and px < 1 else 2),
                "value": _r(val), "day_change_pct": _r(day_pct), "day_change": _r(val - val / (1 + day_pct / 100)) if val and day_pct is not None else None,
                "avg_cost": _r(avg, 6 if avg and avg < 1 else 2), "cost": _r(cost),
                "unrealized": _r(val * (1 - unk_share) - cost) if val is not None and cost else None,
                "unrealized_pct": _r((val * (1 - unk_share) / cost - 1) * 100) if val and cost else None,
                "realized": _r(b.get("realized")), "income_qty": b.get("income_qty") or 0,
                "basis_note": (None if unk_share < 0.02 else "basis unknown (deposited)" if unk_share > 0.98
                               else f"{unk_share * 100:.0f}% deposited, basis unknown"),
                "crypto_proxy": kind == "stock" and s in CRYPTO_PROXIES, "pair": quote.get("pair"),
                "high": quote.get("high"), "low": quote.get("low"),
                "year_high": quote.get("year_high"), "year_low": quote.get("year_low"),
                "tradable": kind == "crypto" and bool(_crypto_pair(s)),
            })
        rows.sort(key=lambda r: -(r["value"] or 0))
        invested = sum(r["value"] or 0 for r in rows)
        total = invested + cash
        for r in rows:
            r["weight"] = _r((r["value"] or 0) / total * 100) if total else None
        day = sum(r["day_change"] or 0 for r in rows)
        upl = sum(r["unrealized"] or 0 for r in rows if r["unrealized"] is not None)
        cost = sum(r["cost"] or 0 for r in rows if r["cost"])
        crypto_v = sum(r["value"] or 0 for r in rows if r["kind"] == "crypto")
        stock_v = sum(r["value"] or 0 for r in rows if r["kind"] == "stock")
        proxy_v = sum(r["value"] or 0 for r in rows if r["crypto_proxy"])
        realized_all = sum(_f(v.get("realized")) for k, v in basis.items() if k != "__totals__")
        realized_unknown = any(_f(v.get("realized_unknown_qty")) > 0 for k, v in basis.items() if k != "__totals__")
        meta = basis.get("__totals__") or {}
        return {
            "total": _r(total), "cash": _r(cash), "invested": _r(invested), "day_change": _r(day),
            "day_change_pct": _r(day / (total - day) * 100) if total - day else None,
            "unrealized": _r(upl), "unrealized_pct": _r(upl / cost * 100) if cost else None,
            "realized_all_time": _r(realized_all), "realized_note": ("excludes coins deposited from outside Kraken "
                                                                     "(their cost is unknown)") if realized_unknown else None,
            "dividends": _r(meta.get("dividends")), "fees": _r(meta.get("fees")),
            "allocation": {"crypto": _r(crypto_v), "stocks": _r(stock_v), "cash": _r(cash),
                           "crypto_exposure_pct": _r((crypto_v + proxy_v) / total * 100) if total else None},
            "positions": rows, "updated": time.time(),
        }
    p = _cached("portfolio", 20, build)
    if record:
        store.record_result("trade_desk", None, {}, {"key": "trade:desk", "kind": "trade_desk", **p,
                                                     "orders": open_orders(record=False),
                                                     "insights": _load(INSIGHTS, {}).get("result"),
                                                     "alerts": _load(ALERTS, [])})
    return p


def brief_portfolio(p: dict) -> dict:
    return {k: p.get(k) for k in ("total", "cash", "day_change", "day_change_pct", "unrealized", "unrealized_pct",
                                  "allocation")} | {
        "positions": [{k: r[k] for k in ("symbol", "kind", "qty", "price", "value", "weight", "day_change_pct",
                                          "avg_cost", "unrealized", "unrealized_pct")} for r in p["positions"]],
        "shown": "the trading desk is on screen"}


# ------------------------------------------------------------------ orders + history
def open_orders(record: bool = True) -> list[dict]:
    if not configured():
        return []

    def fetch():
        oo = (_priv("OpenOrders") or {}).get("open") or {}
        out = []
        for txid, o in oo.items():
            d = o.get("descr") or {}
            out.append({"txid": txid, "pair": d.get("pair"), "side": d.get("type"), "type": d.get("ordertype"),
                        "price": _f(d.get("price")) or None, "price2": _f(d.get("price2")) or None,
                        "volume": _f(o.get("vol")), "filled": _f(o.get("vol_exec")), "status": o.get("status"),
                        "opened": _f(o.get("opentm")), "description": d.get("order"), "aclass": d.get("aclass")})
        return sorted(out, key=lambda x: -x["opened"])
    rows = _cached("open_orders", 10, fetch)
    if record:
        store.record_result("trade_orders", None, {}, {"key": "trade:orders", "kind": "trade_orders", "orders": rows})
    return rows


def trade_history(limit: int = 60) -> list[dict]:
    def fetch():
        t = (_priv("TradesHistory") or {}).get("trades") or {}
        rows = []
        for txid, x in t.items():
            pair = x.get("pair") or ""
            rows.append({"txid": txid, "pair": pair, "symbol": _pair_symbol(pair, x.get("aclass")), "aclass": x.get("aclass"),
                         "side": x.get("type"), "type": x.get("ordertype"), "price": _f(x.get("price")),
                         "volume": _f(x.get("vol")), "cost": _f(x.get("cost")), "fee": _f(x.get("fee")),
                         "time": _f(x.get("time"))})
        return sorted(rows, key=lambda r: -r["time"])
    return _cached("history", 60, fetch)[:limit]


def _pair_symbol(pair: str, aclass: str | None) -> str:
    if aclass == "equity_pair":
        return re.sub(r"Z?USD$", "", pair)
    p = _pairs().get(pair) or next((v for v in _pairs().values() if v.get("altname") == pair), None)
    if p and p.get("wsname"):
        b = p["wsname"].split("/")[0]
        return DISPLAY.get(b, b)
    return re.sub(r"Z?USD$", "", pair)


# ------------------------------------------------------------------ charts (HUD rpc)
_OHLC = {"1D": (5, 86400), "5D": (30, 5 * 86400), "1M": (240, 31 * 86400), "6M": (1440, 183 * 86400),
         "1Y": (1440, 366 * 86400), "ALL": (10080, None)}


def chart(symbol: str, kind: str = "crypto", range: str = "1M") -> dict:
    rng = range.upper() if range.upper() in _OHLC else "1M"
    if kind == "stock":
        from . import markets
        return markets.market_chart(symbol, {"ALL": "5Y"}.get(rng, rng))
    p = _crypto_pair(symbol)
    if not p:
        raise ValueError(f"No USD market for {symbol} on Kraken.")
    interval, span = _OHLC[rng]

    def fetch():
        res = _pub("OHLC", pair=p["key"], interval=interval)
        rows = next(v for k, v in res.items() if k != "last")
        lo = time.time() - span if span else 0
        pts = [{"t": int(r[0]), "o": _f(r[1]), "h": _f(r[2]), "l": _f(r[3]), "c": _f(r[4]), "v": _f(r[6])}
               for r in rows if int(r[0]) >= lo]
        base = pts[0]["o"] if pts else None
        last = pts[-1]["c"] if pts else None
        return {"symbol": symbol, "range": rng, "points": pts, "base": base, "intraday": interval < 1440,
                "period_change_pct": _r((last / base - 1) * 100) if base and last else None}
    return _cached(f"ohlc:{p['key']}:{rng}", 60 if interval < 240 else 600, fetch)


def orderbook(symbol: str, depth: int = 12) -> dict:
    p = _crypto_pair(symbol)
    if not p:
        raise ValueError(f"No order book for {symbol}.")
    res = _cached(f"book:{p['key']}", 5, lambda: _pub("Depth", pair=p["key"], count=depth))
    b = next(iter(res.values()))
    return {"symbol": symbol, "asks": [[_f(a[0]), _f(a[1])] for a in b["asks"]], "bids": [[_f(x[0]), _f(x[1])] for x in b["bids"]]}


# ------------------------------------------------------------------ order entry (always confirm)
_TYPES = {"market": "market", "limit": "limit", "stop": "stop-loss", "stop-loss": "stop-loss", "stop_loss": "stop-loss",
          "take-profit": "take-profit", "take_profit": "take-profit", "stop-limit": "stop-loss-limit",
          "stop_limit": "stop-loss-limit", "trailing-stop": "trailing-stop"}


def _fee_rate(pair_key: str) -> float:
    def fetch():
        tv = _priv("TradeVolume", {"pair": pair_key})
        return _f(((tv.get("fees") or {}).get(pair_key) or {}).get("fee")) / 100
    try:
        return _cached(f"fee:{pair_key}", 3600, fetch)
    except Exception:
        return 0.004


def preview(symbol: str, side: str, amount_usd: float = 0, quantity: float = 0, order_type: str = "market",
            limit_price: float = 0, stop_price: float = 0, check: bool = True) -> dict:
    """Price an order without placing it (live quote, size, fees, Kraken validation)."""
    side = side.lower().strip()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    ot = _TYPES.get(order_type.lower().strip())
    if not ot:
        raise ValueError(f"Unsupported order type '{order_type}'. Use market, limit, stop-loss, take-profit or stop-limit.")
    sym = symbol.upper().strip().replace("/USD", "")
    kind = "crypto" if _crypto_pair(sym) else "stock"
    if kind == "crypto":
        p = _crypto_pair(sym)
        q = crypto_tickers([sym]).get(sym) or {}
        px = q.get("ask") if side == "buy" else q.get("bid")
        px = px or q.get("price")
        lot, pdec = int(p.get("lot_decimals") or 8), int(p.get("pair_decimals") or 2)
        ordermin, costmin = _f(p.get("ordermin")), _f(p.get("costmin"))
        pair = p["key"]
        fee_rate = _fee_rate(pair)
    else:
        q = stock_quotes([sym]).get(sym) or {}
        px = q.get("price")
        if not px:
            raise ValueError(f"Couldn't find {sym} as a Kraken crypto pair or a stock quote.")
        lot, pdec, ordermin, costmin, pair, fee_rate = 9, 2, 0.0, 1.0, f"{sym}ZUSD", 0.0
    ref = limit_price if ot in ("limit", "stop-loss-limit") and limit_price else (stop_price if ot in ("stop-loss", "take-profit") and stop_price else px)
    sell_all = side == "sell" and not (quantity and quantity > 0) and not (amount_usd and amount_usd > 0)
    if quantity and quantity > 0:
        qty = quantity
    elif amount_usd and amount_usd > 0:
        qty = amount_usd / ref
    elif sell_all:
        qty = float("inf")  # "sell my X": the whole position (clamped to what he holds below)
    else:
        raise ValueError("Give an amount in dollars or a quantity.")
    qty = qty if math.isinf(qty) else math.floor(qty * 10 ** lot) / 10 ** lot
    held = 0.0
    if side == "sell":
        try:
            held = next((r["qty"] for r in portfolio(record=False)["positions"] if r["symbol"] == sym), 0.0)
        except Exception:
            held = 0.0
        if qty > held:
            qty = math.floor(held * 10 ** lot) / 10 ** lot
    cost = qty * ref
    fee = cost * fee_rate
    warnings = []
    if ordermin and qty < ordermin:
        warnings.append(f"Kraken's minimum for {sym} is {ordermin:g}.")
    if costmin and cost < costmin:
        warnings.append(f"Kraken's minimum order is ${costmin:g}.")
    if side == "buy":
        cash = (portfolio(record=False) or {}).get("cash") or 0
        if cost + fee > cash + 0.01:
            warnings.append(f"That's more than your ${cash:,.2f} cash.")
    if side == "sell" and not held:
        warnings.append(f"You don't hold any {sym} on Kraken.")
    params = {"pair": pair, "type": side, "ordertype": ot, "volume": f"{qty:.{lot}f}"}
    if ot in ("limit",):
        params["price"] = f"{limit_price:.{pdec}f}"
    if ot in ("stop-loss", "take-profit", "trailing-stop"):
        params["price"] = f"{(stop_price or limit_price):.{pdec}f}"
    if ot == "stop-loss-limit":
        params["price"], params["price2"] = f"{stop_price:.{pdec}f}", f"{limit_price:.{pdec}f}"
    if kind == "stock":
        params["asset_class"] = "equity_pair"
    validation = None
    if check:
        try:
            _priv("AddOrder", {**params, "validate": "true"})
            validation = "ok"
        except Exception as e:
            validation = str(e)
    res = {"symbol": sym, "kind": kind, "side": side, "order_type": ot, "quantity": qty, "price": _r(px, pdec),
           "ref_price": _r(ref, pdec), "est_cost": _r(cost), "est_fee": _r(fee), "fee_rate_pct": _r(fee_rate * 100, 3),
           "est_total": _r(cost + fee if side == "buy" else cost - fee), "limit_price": limit_price or None,
           "stop_price": stop_price or None, "held": held, "warnings": warnings, "validation": validation,
           "kraken_params": params}
    if kind == "stock" and validation and validation != "ok":
        res["stock_blocked"] = ("Kraken's API doesn't accept stock orders for this account (it answered: "
                                f"{validation.replace('Kraken: ', '')}). Place stock trades in the Kraken app; "
                                "JARVIS still tracks and analyzes them.")
    return res


def order(symbol: str, side: str, amount_usd: float = 0, quantity: float = 0, order_type: str = "market",
          limit_price: float = 0, stop_price: float = 0, reason: str = "") -> dict:
    """Propose a live order. Returns awaiting_user_confirmation; it executes only after he authorizes."""
    pv = preview(symbol, side, amount_usd, quantity, order_type, limit_price, stop_price, check=True)
    if pv.get("stock_blocked"):
        raise RuntimeError(pv["stock_blocked"])
    if pv["validation"] != "ok":
        raise RuntimeError(f"Kraken rejected the order: {pv['validation']}")
    if any("more than your" in w or "don't hold" in w or "minimum" in w for w in pv["warnings"]):
        raise RuntimeError(" ".join(pv["warnings"]))
    verb = "Buy" if pv["side"] == "buy" else "Sell"
    lp, sp = pv.get("limit_price") or 0, pv.get("stop_price") or 0
    how = {"market": "at market", "limit": f"limit ${lp:,}", "stop-loss": f"stop ${sp:,}",
           "take-profit": f"take-profit ${sp or lp:,}",
           "stop-loss-limit": f"stop ${sp:,} / limit ${lp:,}"}[pv["order_type"]] if pv["order_type"] in (
        "market", "limit", "stop-loss", "take-profit", "stop-loss-limit") else pv["order_type"]
    summary = f"{verb} {pv['quantity']:g} {pv['symbol']} {how} (≈ ${pv['est_total']:,.2f})"
    preview_card = {"type": "trade", **{k: pv[k] for k in ("symbol", "kind", "side", "order_type", "quantity", "price",
                                                          "ref_price", "est_cost", "est_fee", "fee_rate_pct", "est_total",
                                                          "limit_price", "stop_price", "warnings")},
                    "reason": reason, "action_label": f"{verb} · ${pv['est_total']:,.2f}"}
    return store.propose("trade_order", "kraken", {"params": pv["kraken_params"], "preview_price": pv["price"],
                                                    "symbol": pv["symbol"], "side": pv["side"]}, summary, preview_card)


def _exec_order(account: str, params: dict, preview_price: float, symbol: str, side: str) -> dict:
    """Runs ONLY via store.execute_action after he authorized the confirm card."""
    if params.get("ordertype") == "market" and preview_price:
        now = (crypto_tickers([symbol]).get(symbol) or {}).get("price") if not params.get("asset_class") else \
            (stock_quotes([symbol]).get(symbol) or {}).get("price")
        if now and abs(now / preview_price - 1) > MAX_DRIFT:
            raise RuntimeError(f"{symbol} moved {abs(now / preview_price - 1) * 100:.1f}% since the preview "
                               f"(${preview_price:,.2f} -> ${now:,.2f}). Not placed; ask again for a fresh quote.")
    res = _priv("AddOrder", params)
    txids = res.get("txid") or []
    _bust("portfolio", "open_orders", "history", "ledger_pull")
    out = {"txid": txids[0] if txids else None, "description": (res.get("descr") or {}).get("order"),
           "symbol": symbol, "side": side, "placed_at": time.time()}
    status = None
    if txids:
        try:
            time.sleep(1.0)
            q = _priv("QueryOrders", {"txid": txids[0], "trades": "true"}).get(txids[0]) or {}
            status = q.get("status")
            out.update(status=status, filled=_f(q.get("vol_exec")), avg_price=_f(q.get("price")) or None,
                       cost=_f(q.get("cost")) or None, fee=_f(q.get("fee")) or None)
        except Exception:
            pass
    store.record_result("trade_order_placed", None, {}, {"key": f"trade:order:{out['txid']}", "kind": "trade_order", **out})
    try:
        portfolio(record=True)
    except Exception:
        pass
    return out


def cancel(txid: str) -> dict:
    """Cancel an open order (reduces risk, so no confirm card; audited)."""
    oo = {o["txid"]: o for o in open_orders(record=False)}
    if txid not in oo:
        raise ValueError("That order isn't open.")
    return store.run_now("trade_cancel", "kraken", {"txid": txid}, f"Cancel {oo[txid]['description']}",
                         {"type": "trade_cancel", **oo[txid]}, reason="cancel_order_reduces_risk")


def _exec_cancel(account: str, txid: str) -> dict:
    r = _priv("CancelOrder", {"txid": txid})
    _bust("open_orders", "portfolio")
    open_orders(record=True)
    return {"cancelled": int(r.get("count") or 0), "txid": txid}


def register_executors() -> None:
    from . import store as S
    S.EXECUTORS.update({"trade_order": _exec_order, "trade_cancel": _exec_cancel})


register_executors()


# ------------------------------------------------------------------ intelligence
def _load(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _save(p: Path, v: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, default=str, indent=1))


def _closes(sym: str, kind: str) -> list[float]:
    try:
        if kind == "stock":
            from . import markets
            pts = markets.market_chart(sym, "1Y")["points"]
        else:
            pts = chart(sym, "crypto", "1Y")["points"]
        return [p["c"] for p in pts if p.get("c")]
    except Exception:
        return []


def signals(closes: list[float]) -> dict:
    """Technical read from daily closes (oldest first)."""
    n = len(closes)
    if n < 15:
        return {}
    last = closes[-1]

    def ret(d):
        return (last / closes[-d - 1] - 1) * 100 if n > d else None

    def sma(d):
        return sum(closes[-d:]) / d if n >= d else None
    gains = losses = 0.0
    for a, b in zip(closes[-15:-1], closes[-14:]):
        ch = b - a
        gains += max(ch, 0)
        losses += max(-ch, 0)
    rsi = 100 - 100 / (1 + gains / losses) if losses else 100.0
    rets = [b / a - 1 for a, b in zip(closes[-31:-1], closes[-30:])] if n > 31 else []
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * math.sqrt(252) * 100 if rets else None
    hi = max(closes)
    s20, s50, s200 = sma(20), sma(50), sma(200)
    trend = ("up" if s50 and s20 and last > s20 > s50 else "down" if s50 and s20 and last < s20 < s50 else "mixed")
    return {"ret_1w": _r(ret(5)), "ret_1m": _r(ret(21)), "ret_3m": _r(ret(63)), "ret_1y": _r(ret(n - 1)),
            "rsi14": _r(rsi, 1), "sma20": _r(s20), "sma50": _r(s50), "sma200": _r(s200),
            "above_200d": (last > s200) if s200 else None, "from_high_pct": _r((last / hi - 1) * 100),
            "vol_annual_pct": _r(vol, 1), "trend": trend}


INSIGHT_PROMPT = """You are JARVIS, a sharp, sober portfolio analyst for Stephen. Give him a candid read of HIS Kraken
portfolio. Today is {today}. Use the numbers below (live prices, his cost basis, technicals, recent headlines).

PORTFOLIO: {portfolio}
PER-HOLDING DATA: {holdings}

Return JSON:
{{"headline": "one sentence on the state of the portfolio today",
 "risk": {{"level": "low|moderate|elevated|high", "summary": "concentration / correlation / volatility in 1-2 sentences"}},
 "observations": ["3-5 specific portfolio-level observations (e.g. how much is effectively a bet on crypto via MSTR/MARA/COIN/ETHU, cash drag, winners/losers vs cost)"],
 "holdings": [{{"symbol": "...", "rating": "BUY|ADD|HOLD|TRIM|SELL", "confidence": 0-100,
               "thesis": "2 sentences grounded in the data and his cost basis",
               "signals": ["short bullet facts, e.g. 'RSI 74 overbought', '+38% vs your cost', 'below 200-day'"],
               "levels": {{"support": number or null, "resistance": number or null, "stop_idea": number or null}},
               "catalysts": ["upcoming / recent catalysts from headlines, if any"]}}],
 "actions": [{{"title": "concrete suggested move", "detail": "why, sized for his portfolio", "symbol": "...",
              "side": "buy|sell|none", "amount_usd": number or null}}]}}
Rules: one entry per holding listed. Be decisive but honest about uncertainty; ratings must follow the evidence
(trend, momentum, valuation vs cost, concentration). Never invent prices or news not given. Sizes should respect his
cash ({cash}) and the portfolio's total."""


def insights(refresh: bool = False, record: bool = True) -> dict:
    """Buy / add / hold / trim / sell reads per holding plus portfolio-level observations (cached 30 min)."""
    cached = _load(INSIGHTS, {})
    if not refresh and cached.get("at", 0) > time.time() - 1800 and cached.get("result"):
        res = cached["result"]
    else:
        p = portfolio(record=False)
        from . import markets
        rows = [r for r in p["positions"] if (r["value"] or 0) >= 5]

        def one(r):
            sig = signals(_closes(r["symbol"], r["kind"]))
            news = []
            try:
                q = r["symbol"] if r["kind"] == "stock" else f"{r['symbol']}-USD"
                news = [n["title"] for n in markets._news(q, 4)]
            except Exception:
                pass
            return {"symbol": r["symbol"], "kind": r["kind"], "price": r["price"], "qty": r["qty"], "value": r["value"],
                    "weight_pct": r["weight"], "avg_cost": r["avg_cost"], "unrealized_pct": r["unrealized_pct"],
                    "day_change_pct": r["day_change_pct"], "crypto_proxy": r["crypto_proxy"], "technicals": sig,
                    "headlines": news}
        hold = list(_pool.map(one, rows))
        from .garage import _gemini
        res = _gemini([{"text": INSIGHT_PROMPT.format(
            today=dt.date.today().isoformat(), cash=p["cash"],
            portfolio=json.dumps({k: p[k] for k in ("total", "cash", "day_change_pct", "unrealized", "unrealized_pct",
                                                    "allocation", "realized_all_time", "dividends")}),
            holdings=json.dumps(hold))}], timeout=240)
        if isinstance(res, list):
            res = res[0] if res else {}
        tech = {h["symbol"]: h for h in hold}
        for h in res.get("holdings") or []:
            t = tech.get(h.get("symbol")) or {}
            h["technicals"] = t.get("technicals")
            h["headlines"] = t.get("headlines")
        res["generated_at"] = time.time()
        _save(INSIGHTS, {"at": time.time(), "result": res})
    if record:
        store.record_result("trade_insights", None, {}, {"key": "trade:insights", "kind": "trade_insights", **res})
    return {"headline": res.get("headline"), "risk": res.get("risk"), "observations": res.get("observations"),
            "ratings": {h["symbol"]: h.get("rating") for h in res.get("holdings") or []},
            "actions": [a.get("title") for a in res.get("actions") or []],
            "shown": "the intelligence display is on screen; speak the headline, the risk and the 1-2 most important calls"}


# ------------------------------------------------------------------ alerts + monitoring
def alert_set(symbol: str, above: float = 0, below: float = 0, note: str = "") -> dict:
    if not (above or below):
        raise ValueError("Give a price to watch: above or below.")
    al = _load(ALERTS, [])
    a = {"id": "al_" + uuid.uuid4().hex[:6], "symbol": symbol.upper(), "above": above or None, "below": below or None,
         "note": note, "created": time.time(), "active": True}
    al.append(a)
    _save(ALERTS, al)
    portfolio(record=True)
    return {"alert": a, "note": "JARVIS checks every few minutes and puts a card up when it triggers."}


def alert_remove(alert_id: str) -> dict:
    al = [a for a in _load(ALERTS, []) if a["id"] != alert_id]
    _save(ALERTS, al)
    return {"removed": alert_id, "remaining": len(al)}


def _price(sym: str) -> float | None:
    if _crypto_pair(sym):
        return (crypto_tickers([sym]).get(sym) or {}).get("price")
    return (stock_quotes([sym]).get(sym) or {}).get("price")


def monitor_tick() -> list[dict]:
    """Called by Core every few minutes: price alerts, big moves in his holdings, filled orders. Records alert cards."""
    if not configured():
        return []
    state = _load(MONITOR, {"moves": {}, "orders": {}})
    fired: list[dict] = []
    today = dt.date.today().isoformat()
    al = _load(ALERTS, [])
    for a in al:
        if not a.get("active"):
            continue
        px = _price(a["symbol"])
        if px is None:
            continue
        if (a.get("above") and px >= a["above"]) or (a.get("below") and px <= a["below"]):
            a["active"], a["fired_at"], a["fired_price"] = False, time.time(), px
            fired.append({"type": "price", "symbol": a["symbol"], "price": px,
                          "text": f"{a['symbol']} is at ${px:,.2f}, {'above' if a.get('above') else 'below'} your "
                                  f"${(a.get('above') or a.get('below')):,} alert." + (f" ({a['note']})" if a.get("note") else "")})
    _save(ALERTS, al)
    try:
        p = portfolio(record=False)
        for r in p["positions"]:
            ch = r.get("day_change_pct")
            if ch is None or (r["value"] or 0) < 25:
                continue
            band = int(abs(ch) // 5) * 5  # alert at 5%, 10%, 15%... once per day per band
            key = f"{today}:{r['symbol']}"
            if band >= 5 and state["moves"].get(key, 0) < band:
                state["moves"][key] = band
                fired.append({"type": "move", "symbol": r["symbol"], "price": r["price"], "change_pct": ch,
                              "text": f"{r['symbol']} is {'up' if ch > 0 else 'down'} {abs(ch):.1f}% today "
                                      f"(your {r['qty']:g} is worth ${r['value']:,.2f})."})
    except Exception:
        pass
    try:
        prev = state.get("open_txids") or []
        now = [o["txid"] for o in open_orders(record=False)]
        gone = [t for t in prev if t not in now]
        if gone:
            q = _priv("QueryOrders", {"txid": ",".join(gone[:20])})
            for t, o in q.items():
                if o.get("status") == "closed":
                    fired.append({"type": "fill", "txid": t, "text": f"Filled: {(o.get('descr') or {}).get('order')} "
                                                                    f"at ${_f(o.get('price')):,.2f}."})
        state["open_txids"] = now
    except Exception:
        pass
    state["moves"] = {k: v for k, v in state["moves"].items() if k.startswith(today)}
    _save(MONITOR, state)
    for f in fired:
        store.record_result("trade_alert", None, {}, {"key": f"trade:alert:{uuid.uuid4().hex[:6]}", "kind": "trade_alert",
                                                      "at": time.time(), **f})
    if fired:
        try:
            portfolio(record=True)
        except Exception:
            pass
    return fired
