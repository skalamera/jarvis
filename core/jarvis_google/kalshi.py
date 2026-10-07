"""Kalshi: prediction markets (events/markets) + perps (margin), public market data and his account.

Public data needs no auth (api.elections.kalshi.com). Account + trading need an API key:
  KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH (PEM file, Ed25519 or RSA) in ~/.hermes/.env, never in the repo.
Orders: the HUD shows a review step and only sends after he clicks Confirm (click op); voice orders go through a
confirm card (store.propose). Every order / cancel is audit-logged."""
from __future__ import annotations

import base64
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import store
from .eats import _env

PUB = "https://api.elections.kalshi.com/trade-api/v2"
API = "https://external-api.kalshi.com/trade-api/v2"
UA = {"User-Agent": "JARVIS/1.0", "Accept": "application/json"}
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get(path: str, params: dict | None = None, base: str = PUB) -> dict:
    r = httpx.get(base + path, params={k: v for k, v in (params or {}).items() if v not in (None, "")}, headers=UA,
                  timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Kalshi {r.status_code}: {r.text[:200]}")
    return r.json()


# ------------------------------------------------------------------ auth
def _key():
    kid, path = _env("KALSHI_API_KEY_ID"), _env("KALSHI_PRIVATE_KEY_PATH")
    if not kid or not path:
        return None, None
    from cryptography.hazmat.primitives import serialization
    pk = serialization.load_pem_private_key(Path(path).expanduser().read_bytes(), password=None)
    return kid, pk


def connected() -> bool:
    return bool(_env("KALSHI_API_KEY_ID") and _env("KALSHI_PRIVATE_KEY_PATH"))


def _sign(pk, text: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding
    if isinstance(pk, ed25519.Ed25519PrivateKey):
        return base64.b64encode(pk.sign(text.encode())).decode()
    sig = pk.sign(text.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                  hashes.SHA256())
    return base64.b64encode(sig).decode()


def _priv(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    kid, pk = _key()
    if not kid:
        raise PermissionError("Kalshi account not connected (add KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH to ~/.hermes/.env).")
    ts = str(int(time.time() * 1000))
    full = "/trade-api/v2" + path
    h = {**UA, "KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-TIMESTAMP": ts,
         "KALSHI-ACCESS-SIGNATURE": _sign(pk, ts + method.upper() + full)}
    r = httpx.request(method, API + path, params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                      json=body, headers=h, timeout=25)
    if r.status_code >= 300:
        raise RuntimeError(f"Kalshi {r.status_code}: {r.text[:240]}")
    return r.json() if r.content else {}


# ------------------------------------------------------------------ shaping
def _updown(m: dict) -> bool:
    """Kalshi labels short-window price markets (BTC 15 min, hourly ETH…) Up / Down instead of Yes / No."""
    t = (m.get("ticker") or "").upper()
    sub = (m.get("yes_sub_title") or "").lower()
    return "15M-" in t or t.startswith(("KXBTCD", "KXETHD", "KXSOLD")) and "target" in sub or "price up" in sub


def _mkt(m: dict) -> dict:
    yb, ya, last, prev = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars")), \
        _f(m.get("previous_price_dollars"))
    mid = (yb + ya) / 2 if yb and ya else last
    return {"ticker": m.get("ticker"), "event_ticker": m.get("event_ticker"), "title": m.get("title"),
            "label": m.get("yes_sub_title") or m.get("title"), "no_label": m.get("no_sub_title"),
            "yes_word": "Up" if _updown(m) else "Yes", "no_word": "Down" if _updown(m) else "No",
            "yes_bid": yb, "yes_ask": ya, "no_bid": _f(m.get("no_bid_dollars")), "no_ask": _f(m.get("no_ask_dollars")),
            "last": last, "prev": prev, "chance": round((last or mid) * 100, 1), "change": round((last - prev) * 100, 1) if prev else 0,
            "volume": _f(m.get("volume_fp")), "volume_24h": _f(m.get("volume_24h_fp")), "oi": _f(m.get("open_interest_fp")),
            "status": m.get("status"), "result": m.get("result") or "", "close_time": m.get("close_time"),
            "rules": m.get("rules_primary") or "", "rules2": m.get("rules_secondary") or ""}


def _evt(e: dict, n: int = 6) -> dict:
    ms = sorted((_mkt(m) for m in e.get("markets") or []), key=lambda m: -m["chance"])
    return {"event_ticker": e.get("event_ticker"), "series_ticker": e.get("series_ticker"), "title": e.get("title"),
            "sub_title": e.get("sub_title"), "category": e.get("category") or "Other", "mutually_exclusive": e.get("mutually_exclusive"),
            "volume_24h": sum(m["volume_24h"] for m in ms), "volume": sum(m["volume"] for m in ms),
            "n_markets": len(ms), "markets": ms[:n] if n else ms,
            "close_time": min((m["close_time"] for m in ms if m["close_time"]), default=None),
            "image": f"https://kalshi-public-docs.s3.amazonaws.com/series-images-webp/{e.get('series_ticker')}.webp"}


_ev_lock = __import__("threading").Lock()


def _fetch_events(pages: int = 80) -> list[dict]:
    evs, cur = [], ""
    for _ in range(pages):
        r = _get("/events", {"status": "open", "with_nested_markets": "true", "limit": 200, "cursor": cur})
        evs += r.get("events") or []
        cur = r.get("cursor") or ""
        if not cur:
            break
    return evs


def _all_events() -> list[dict]:
    """Whole open catalog (~10-16k events, ~10s). Cached; served stale while a background refresh runs."""
    hit = _cache.get("events")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    if hit:  # stale: refresh in the background, answer now
        if _ev_lock.acquire(blocking=False):
            def bg():
                try:
                    _cache["events"] = (time.time(), _fetch_events())
                finally:
                    _ev_lock.release()
            __import__("threading").Thread(target=bg, daemon=True).start()
        return hit[1]
    with _ev_lock:
        hit = _cache.get("events")
        if hit:
            return hit[1]
        evs = _fetch_events()
        _cache["events"] = (time.time(), evs)
        return evs


def categories() -> list[dict]:
    def run():
        t = _get("/search/tags_by_categories").get("tags_by_categories") or {}
        return [{"name": k, "tags": v or []} for k, v in t.items()]
    return _cached("cats", 3600, run)


def perps() -> list[dict]:
    def run():
        out = []
        for m in _get("/margin/markets", base=API).get("markets") or []:
            out.append({"ticker": m["ticker"], "title": m.get("title"), "asset_class": m.get("asset_class"),
                        "price": _f(m.get("price")), "bid": _f(m.get("bid")), "ask": _f(m.get("ask")),
                        "volume_24h": _f(m.get("volume_24h")), "volume_24h_usd": _f(m.get("volume_24h_notional_value_dollars")),
                        "oi_usd": _f(m.get("open_interest_notional_value_dollars")), "leverage": m.get("leverage_estimate"),
                        "status": m.get("status"), "underlying_multiplier": m.get("underlying_multiplier")})
        return sorted(out, key=lambda x: -x["volume_24h_usd"])
    return _cached("perps", 60, run)


# ------------------------------------------------------------------ public views
def overview(n: int = 24) -> dict:
    """Sidebar + expanded home: trending events, movers, closing soon, perps, account (when connected)."""
    with ThreadPoolExecutor(4) as ex:
        fe, fp, fc = ex.submit(_all_events), ex.submit(perps), ex.submit(categories)
        fa = ex.submit(account_summary) if connected() else None
        evs = fe.result()
        try:
            pp = fp.result()
        except Exception:
            pp = []
        cats = fc.result()
        acct = None
        if fa:
            try:
                acct = fa.result()
            except Exception as e:
                acct = {"error": str(e)[:200]}
    shaped = [_evt(e) for e in evs]
    trending = sorted(shaped, key=lambda e: -e["volume_24h"])[:n]
    mk = [m for e in shaped for m in e["markets"] if m["volume_24h"] > 50 and m["prev"]]
    movers = sorted(mk, key=lambda m: -abs(m["change"]))[:12]
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    soon = sorted((e for e in shaped if e["close_time"] and e["close_time"] > now and e["volume_24h"] > 100),
                  key=lambda e: e["close_time"])[:10]
    for m in movers:
        ev = next((e for e in shaped if e["event_ticker"] == m["event_ticker"]), None)
        m["event_title"] = ev["title"] if ev else ""
    return {"trending": trending, "movers": movers, "closing_soon": soon, "perps": pp[:30],
            "categories": [c["name"] for c in cats], "connected": connected(), "account": acct,
            "n_events": len(evs), "updated": time.time()}


def search(query: str = "", category: str = "", sort: str = "volume", limit: int = 40) -> dict:
    q = query.lower().strip()
    out = []
    for e in _all_events():
        if category and (e.get("category") or "").lower() != category.lower():
            continue
        if q and q not in f"{e.get('title')} {e.get('sub_title')} {e.get('event_ticker')} " \
                            f"{' '.join((m.get('yes_sub_title') or '') + ' ' + (m.get('title') or '') for m in e.get('markets') or [])}".lower():
            continue
        out.append(_evt(e))
    key = {"volume": lambda e: -e["volume_24h"], "total": lambda e: -e["volume"],
           "closing": lambda e: e["close_time"] or "9", "new": lambda e: e["event_ticker"]}.get(sort, lambda e: -e["volume_24h"])
    out.sort(key=key)
    return {"query": query, "category": category, "sort": sort, "events": out[:limit], "total": len(out)}


def event(event_ticker: str) -> dict:
    r = _get(f"/events/{event_ticker}", {"with_nested_markets": "true"})
    e = r.get("event") or {}
    if not e.get("markets") and r.get("markets"):
        e["markets"] = r["markets"]
    return _evt(e, n=0)


def market(ticker: str, period: str = "1w") -> dict:
    """Everything for the trade view: market, its event, order book, recent trades, price history."""
    m = _get(f"/markets/{ticker}").get("market") or {}
    ev = _get(f"/events/{m.get('event_ticker')}").get("event") or {}
    span, interval = {"1d": (86400, 1), "1w": (7 * 86400, 60), "1m": (30 * 86400, 60), "all": (365 * 86400, 1440)}.get(period, (7 * 86400, 60))
    now = int(time.time())

    def book():
        ob = (_get(f"/markets/{ticker}/orderbook", {"depth": 12}).get("orderbook_fp") or {})
        return {"yes": [[_f(p), _f(q)] for p, q in ob.get("yes_dollars") or []], "no": [[_f(p), _f(q)] for p, q in ob.get("no_dollars") or []]}

    def trades():
        return [{"t": t.get("created_time"), "price": _f(t.get("yes_price_dollars")), "count": _f(t.get("count_fp")),
                 "side": t.get("taker_side")} for t in _get("/markets/trades", {"ticker": ticker, "limit": 30}).get("trades") or []]

    def candles():
        cs = _get(f"/series/{ev.get('series_ticker')}/markets/{ticker}/candlesticks",
                  {"start_ts": now - span, "end_ts": now, "period_interval": interval}).get("candlesticks") or []
        out = []
        for c in cs:
            p = c.get("price") or {}
            v = _f(p.get("close_dollars")) or (_f((c.get("yes_bid") or {}).get("close_dollars")) + _f((c.get("yes_ask") or {}).get("close_dollars"))) / 2
            out.append({"t": c.get("end_period_ts"), "p": round(v * 100, 2), "v": _f(c.get("volume_fp"))})
        return out
    with ThreadPoolExecutor(3) as ex:
        fb, ft, fc = ex.submit(book), ex.submit(trades), ex.submit(candles)
        b, t = fb.result(), ft.result()
        try:
            c = fc.result()
        except Exception:
            c = []
    cur = _mkt(m)
    c.append({"t": int(time.time()), "p": cur["chance"], "v": 0})  # end the line at the live price
    pos = None
    if connected():
        try:
            pos = next((p for p in positions()["markets"] if p["ticker"] == ticker), None)
        except Exception:
            pos = None
    return {"market": _mkt(m), "event": {"title": ev.get("title"), "sub_title": ev.get("sub_title"), "category": ev.get("category"),
                                         "event_ticker": ev.get("event_ticker"), "series_ticker": ev.get("series_ticker")},
            "book": b, "trades": t, "candles": c, "period": period, "position": pos, "connected": connected()}


def perp(ticker: str, hours: int = 48) -> dict:
    now = int(time.time())
    m = _get(f"/margin/markets/{ticker}", base=API).get("market") or {}
    ob = _get(f"/margin/markets/{ticker}/orderbook", base=API).get("orderbook") or {}
    cs = _get(f"/margin/markets/{ticker}/candlesticks", {"start_ts": now - hours * 3600, "end_ts": now, "period_interval": 60},
              base=API).get("candlesticks") or []
    try:
        fund = _get("/margin/funding_rates/estimate", {"ticker": ticker}, base=API)
    except Exception:
        fund = {}
    return {"ticker": ticker, "title": m.get("title"), "price": _f(m.get("price")), "bid": _f(m.get("bid")), "ask": _f(m.get("ask")),
            "asset_class": m.get("asset_class"), "leverage": m.get("leverage_estimate"), "oi_usd": _f(m.get("open_interest_notional_value_dollars")),
            "volume_24h_usd": _f(m.get("volume_24h_notional_value_dollars")),
            "asks": sorted(([_f(p), _f(q)] for p, q in ob.get("asks") or []), key=lambda x: x[0])[:12][::-1],
            "bids": sorted(([_f(p), _f(q)] for p, q in ob.get("bids") or []), key=lambda x: -x[0])[:12],
            "candles": [{"t": c.get("end_period_ts"), "p": _f((c.get("price") or {}).get("close") or (c.get("ask") or {}).get("close"))} for c in cs],
            "funding_rate": fund.get("funding_rate"), "next_funding": fund.get("next_funding_time")}


# ------------------------------------------------------------------ account
def account_summary() -> dict:
    b = _priv("GET", "/portfolio/balance")
    return {"balance": _f(b.get("balance_dollars")) or b.get("balance", 0) / 100,
            "portfolio_value": (b.get("portfolio_value") or 0) / 100, "updated_ts": b.get("updated_ts")}


def positions() -> dict:
    r = _priv("GET", "/portfolio/positions", {"limit": 200, "count_filter": "position"})
    mk = [{"ticker": p["ticker"], "position": _f(p.get("position_fp")), "exposure": _f(p.get("market_exposure_dollars")),
           "realized_pnl": _f(p.get("realized_pnl_dollars")), "fees": _f(p.get("fees_paid_dollars")),
           "traded": _f(p.get("total_traded_dollars"))} for p in r.get("market_positions") or []]
    return {"markets": mk, "events": r.get("event_positions") or []}


def account() -> dict:
    """Balance, open positions (with live prices), resting orders, recent fills and settlements."""
    with ThreadPoolExecutor(5) as ex:
        fb, fp = ex.submit(account_summary), ex.submit(positions)
        fo = ex.submit(_priv, "GET", "/portfolio/orders", {"status": "resting", "limit": 100})
        ff = ex.submit(_priv, "GET", "/portfolio/fills", {"limit": 40})
        fs = ex.submit(_priv, "GET", "/portfolio/settlements", {"limit": 30})
        bal, pos, orders, fills, sets = fb.result(), fp.result(), fo.result(), ff.result(), fs.result()
    held = [p for p in pos["markets"] if p["position"]]
    with ThreadPoolExecutor(8) as ex:
        live = dict(zip([p["ticker"] for p in held], ex.map(lambda t: _get(f"/markets/{t}").get("market") or {}, [p["ticker"] for p in held])))
    for p in held:
        m = _mkt(live.get(p["ticker"]) or {})
        side = "yes" if p["position"] > 0 else "no"
        px = m["yes_bid"] if side == "yes" else m["no_bid"]
        p.update(side=side, contracts=abs(p["position"]), title=m["title"], label=m["label"], chance=m["chance"],
                 mark=px, value=round(abs(p["position"]) * px, 2), event_ticker=m["event_ticker"],
                 unrealized=round(abs(p["position"]) * px - p["exposure"], 2))
    return {**bal, "positions": held, "orders": orders.get("orders") or [], "fills": fills.get("fills") or [],
            "settlements": sets.get("settlements") or [], "connected": True}


# ------------------------------------------------------------------ orders
def _order_body(ticker: str, outcome: str, action: str, count: float, price: float, tif: str) -> dict:
    """Map buy/sell YES/NO at a price (in dollars for that outcome) onto the V2 YES book."""
    outcome, action = outcome.lower(), action.lower()
    if outcome not in ("yes", "no") or action not in ("buy", "sell"):
        raise ValueError("outcome must be yes/no and action buy/sell")
    if not (0.01 <= price <= 0.99):
        raise ValueError("Price must be between $0.01 and $0.99.")
    if count <= 0:
        raise ValueError("Contracts must be more than 0.")
    yes_price = price if outcome == "yes" else round(1 - price, 4)
    side = "bid" if (outcome == "yes") == (action == "buy") else "ask"
    return {"ticker": ticker, "client_order_id": str(uuid.uuid4()), "side": side, "count": f"{count:.2f}",
            "price": f"{yes_price:.4f}", "time_in_force": tif, "self_trade_prevention_type": "taker_at_cross",
            "reduce_only": action == "sell"}


def order_preview(ticker: str, outcome: str, action: str, count: float, price: float, tif: str = "good_till_canceled") -> dict:
    m = _mkt(_get(f"/markets/{ticker}").get("market") or {})
    body = _order_body(ticker, outcome, action, float(count), float(price), tif)
    cost = round(float(count) * float(price), 2)
    payout = round(float(count) * 1.0, 2)
    best = (m["yes_ask"] if outcome == "yes" else m["no_ask"]) if action == "buy" else (m["yes_bid"] if outcome == "yes" else m["no_bid"])
    return {"ticker": ticker, "title": m["title"], "label": m["label"], "outcome": outcome, "action": action, "count": count,
            "price": price, "cost": cost if action == "buy" else None, "proceeds": cost if action == "sell" else None,
            "max_payout": payout if action == "buy" else None, "profit_if_right": round(payout - cost, 2) if action == "buy" else None,
            "best_price": best, "crosses": (price >= best if action == "buy" else price <= best) if best else False,
            "tif": tif, "body": body, "connected": connected()}


def order_place(ticker: str, outcome: str, action: str, count: float, price: float, tif: str = "good_till_canceled",
                confirmed: bool = False) -> dict:
    """Place a real order. The HUD calls this only from the Confirm button of its review step (his click)."""
    if not confirmed:
        raise PermissionError("Orders need the Confirm click.")
    body = _order_body(ticker, outcome, action, float(count), float(price), tif)
    r = _priv("POST", "/portfolio/events/orders", body=body)
    store.audit({"event": "kalshi_order", "ticker": ticker, "outcome": outcome, "action": action, "count": count,
                 "price": price, "tif": tif, "result": r})
    return {"status": "placed", **r}


def order_cancel(order_id: str) -> dict:
    r = _priv("DELETE", f"/portfolio/events/orders/{order_id}")
    store.audit({"event": "kalshi_cancel", "order_id": order_id, "result": r})
    return {"status": "canceled", "order_id": order_id}


def _fee(count: float, price: float) -> float:
    """Kalshi taker fee: ceil(0.07 * C * P * (1-P)) to the cent."""
    import math
    return math.ceil(0.07 * count * price * (1 - price) * 100 - 1e-9) / 100


def quote(ticker: str, outcome: str, action: str, dollars: float = 0, shares: float = 0) -> dict:
    """Walk the live book: what a market-style buy of $X (or sell of N shares) of YES/NO fills at right now."""
    outcome, action = outcome.lower(), action.lower()
    ob = _get(f"/markets/{ticker}/orderbook", {"depth": 100}).get("orderbook_fp") or {}
    yes = sorted(([_f(p), _f(q)] for p, q in ob.get("yes_dollars") or []), key=lambda x: -x[0])
    no = sorted(([_f(p), _f(q)] for p, q in ob.get("no_dollars") or []), key=lambda x: -x[0])
    if action == "buy":  # buying YES lifts NO bids at 1-p; buying NO lifts YES bids at 1-p
        levels = [[round(1 - p, 4), q] for p, q in (no if outcome == "yes" else yes)]
    else:  # selling YES hits YES bids; selling NO hits NO bids
        levels = [[p, q] for p, q in (yes if outcome == "yes" else no)]
    budget, want = float(dollars or 0), float(shares or 0)
    fills: list[list[float]] = []
    got = cost = 0.0
    for px, qty in levels:
        if px <= 0:
            continue
        take = int(min(qty, (budget - cost) // px)) if action == "buy" else int(min(qty, want - got))
        if take <= 0:
            break
        fills.append([px, take])
        got += take
        cost += take * px
    while action == "buy" and got and cost + _fee(got, cost / got) > budget + 1e-9:  # fee must fit in the budget
        fills[-1][1] -= 1
        got -= 1
        cost -= fills[-1][0]
        if not fills[-1][1]:
            fills.pop()
    worst = fills[-1][0] if fills else None
    fee = _fee(got, cost / got) if got else 0
    avg = cost / got if got else (levels[0][0] if levels else 0)
    return {"ticker": ticker, "outcome": outcome, "action": action, "shares": got, "avg_price": round(avg, 4),
            "limit": worst, "cost": round(cost + fee, 2) if action == "buy" else None,
            "proceeds": round(cost - fee, 2) if action == "sell" else None, "fee": fee,
            "payout": got if action == "buy" else None, "odds": round(avg * 100, 1) if got else round((levels[0][0] if levels else 0) * 100, 1),
            "liquidity_ok": (action == "sell" and got >= want) or (action == "buy" and got > 0)}


def quick_order(ticker: str, outcome: str, action: str, dollars: float = 0, shares: float = 0, confirmed: bool = False) -> dict:
    """1-click (his click on the armed button): immediate-or-cancel at the worst price the quote walked to."""
    if not confirmed:
        raise PermissionError("Orders need his click.")
    q = quote(ticker, outcome, action, dollars, shares)
    if not q["shares"] or not q["limit"]:
        raise RuntimeError("Not enough liquidity at a sensible price right now.")
    body = _order_body(ticker, outcome, action, q["shares"], q["limit"], "immediate_or_cancel")
    r = _priv("POST", "/portfolio/events/orders", body=body)
    store.audit({"event": "kalshi_quick_order", "ticker": ticker, "outcome": outcome, "action": action, "dollars": dollars,
                 "shares": shares, "quote": q, "result": r})
    raw = _f(r.get("average_fill_price"))
    if raw:  # V2 reports the YES-book price; express it for the side he bought/sold
        r["average_fill_price"] = f"{(1 - raw) if outcome.lower() == 'no' else raw:.4f}"
    return {"status": "placed", "quote": q, **r}


def balance_and_position(ticker: str) -> dict:
    if not connected():
        return {"connected": False}
    b = account_summary()
    pos = next((p for p in positions()["markets"] if p["ticker"] == ticker), None)
    return {"connected": True, "balance": b["balance"],
            "position": {"side": "yes" if pos["position"] > 0 else "no", "shares": abs(pos["position"])} if pos and pos["position"] else None}


def propose_order(ticker: str, outcome: str, action: str, contracts: float, price: float) -> dict:
    pv = order_preview(ticker, outcome, action, contracts, price)
    if not pv["connected"]:
        raise PermissionError("Kalshi account not connected.")
    verb = f"{action.title()} {contracts:g} {outcome.upper()}"
    summary = f"Kalshi: {verb} on '{pv['label'] or pv['title']}' at ${price:.2f} (≈ ${contracts * price:,.2f})"
    card = {"type": "kalshi_order", **{k: pv[k] for k in ("ticker", "title", "label", "outcome", "action", "count", "price",
                                                         "cost", "max_payout", "profit_if_right", "best_price")},
            "action_label": f"{verb} · ${contracts * price:,.2f}"}
    return store.propose("kalshi_order", "kalshi", {"ticker": ticker, "outcome": outcome, "action": action,
                                                     "count": contracts, "price": price}, summary, card)


def register_executors() -> None:
    @store.executor("kalshi_order")
    def _x(account, ticker, outcome, action, count, price):  # runs only after he authorized the confirm card
        return order_place(ticker, outcome, action, count, price, confirmed=True)


CLICK_OPS = {"kalshi_overview": overview, "kalshi_search": search, "kalshi_event": event, "kalshi_market": market,
             "kalshi_perp": perp, "kalshi_account": account, "kalshi_order_preview": order_preview,
             "kalshi_order_place": order_place, "kalshi_order_cancel": order_cancel,
             "kalshi_quote": quote, "kalshi_quick_order": quick_order, "kalshi_wallet": balance_and_position, "kalshi_categories": categories}


def show(view: str = "home", query: str = "", ticker: str = "") -> dict:
    """MCP/voice: put the Kalshi display on screen (trending / a search / a market) and return a compact summary."""
    data = overview()
    if query:
        data["search"] = search(query)
    if ticker:
        data["focus"] = market(ticker)
    store.record_result("kalshi", None, {"view": view, "query": query, "ticker": ticker}, {"key": "kalshi", "kind": "kalshi", **data})
    top = (data.get("search") or {}).get("events") or data["trending"]
    return {"shown": True, "connected": data["connected"],
            "top": [{"event": e["title"], "markets": [(m["label"], f"{m['chance']}%") for m in e["markets"][:3]]} for e in top[:6]],
            "account": data.get("account")}
