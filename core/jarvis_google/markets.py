"""Markets for JARVIS: stocks / ETFs / indices / futures (Yahoo Finance via yfinance) and crypto (CoinGecko).

No API keys. Every public function returns a plain dict that the HUD renders as a rich card (price hero, interactive
chart, analyst targets, earnings, revenue, news, logos). Sub-requests run in parallel and are cached briefly so a
card appears in about a second and re-asking doesn't re-hit the network. Read-only: nothing here trades or writes.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from . import store

CG = "https://api.coingecko.com/api/v3"

# range tab -> (yfinance period, interval)
RANGES: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"), "5D": ("5d", "15m"), "1M": ("1mo", "60m"), "6M": ("6mo", "1d"),
    "YTD": ("ytd", "1d"), "1Y": ("1y", "1d"), "5Y": ("5y", "1wk"), "MAX": ("max", "1mo"),
}
CRYPTO_RANGES = {"1D": "1", "7D": "7", "1M": "30", "1Y": "365", "MAX": "max"}

ALIASES = {
    "s&p": "^GSPC", "s&p 500": "^GSPC", "s&p500": "^GSPC", "s and p 500": "^GSPC", "market": "^GSPC",
    "stock market": "^GSPC", "nasdaq composite index": "^IXIC", "dow jones industrial average": "^DJI", "sp500": "^GSPC", "s and p": "^GSPC", "the market": "^GSPC", "spx": "^GSPC",
    "nasdaq": "^IXIC", "nasdaq composite": "^IXIC", "nasdaq 100": "^NDX", "dow": "^DJI", "dow jones": "^DJI",
    "russell": "^RUT", "russell 2000": "^RUT", "vix": "^VIX", "10 year": "^TNX", "10-year": "^TNX",
    "10 year treasury": "^TNX", "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "crude": "CL=F", "crude oil": "CL=F",
    "natural gas": "NG=F", "euro": "EURUSD=X", "yen": "JPY=X", "dollar index": "DX-Y.NYB",
    "berkshire": "BRK-B", "google": "GOOGL", "alphabet": "GOOGL", "facebook": "META",
}
INDEX_LABELS = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "^RUT": "Russell 2000", "^VIX": "VIX",
                "^TNX": "10Y Yield", "GC=F": "Gold", "CL=F": "Crude Oil", "BTC-USD": "Bitcoin"}
SECTORS = [("XLK", "Technology"), ("XLC", "Communication"), ("XLY", "Consumer Disc."), ("XLF", "Financials"),
           ("XLV", "Health Care"), ("XLI", "Industrials"), ("XLE", "Energy"), ("XLP", "Staples"),
           ("XLU", "Utilities"), ("XLRE", "Real Estate"), ("XLB", "Materials")]

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="mkt")
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _yf():
    import yfinance as yf  # imported lazily: it pulls in pandas, which slows MCP server start

    return yf


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _r(v: Any, nd: int = 2) -> float | None:
    f = _num(v)
    return None if f is None else round(f, nd)


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


# ------------------------------------------------------------------ symbol resolution
_TICKERISH = re.compile(r"^[\^]?[A-Za-z0-9]{1,6}([.\-=][A-Za-z0-9]{1,6})?(=[A-Z])?$")


def looks_like_ticker(q: str) -> bool:
    q = q.strip()
    if not q or " " in q:
        return False
    if any(c in q for c in "^=.-") or any(c.isdigit() for c in q):
        return bool(_TICKERISH.match(q))
    return q.isupper() and len(q) <= 6


def resolve_symbol(q: str) -> str:
    """'Apple' / 'nvidia' / 'the S&P' / 'AAPL' -> a Yahoo symbol."""
    raw = q.strip().strip("$").strip()
    if not raw:
        raise ValueError("no symbol given")
    key = re.sub(r"^(the)\s+", "", raw.lower())
    key = re.sub(r"\s+(index|stock|shares|share price|price|etf|futures?)$", "", key).strip()
    alias = ALIASES.get(key) or ALIASES.get(raw.lower())
    if alias:
        return alias
    if looks_like_ticker(raw):
        sym = raw.upper()
        if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", sym):  # BRK.B / BF.B -> Yahoo's BRK-B
            sym = sym.replace(".", "-")
        return sym
    raw = key or raw

    def search() -> str:
        s = _yf().Search(raw, max_results=6, news_count=0)
        for qt in s.quotes or []:
            if qt.get("quoteType") in ("EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "FUTURE") and qt.get("symbol"):
                return qt["symbol"]
        raise ValueError(f"couldn't find a ticker for “{raw}”")

    return _cached(f"resolve:{raw.lower()}", 86400, search)


# ------------------------------------------------------------------ logos
def logo_urls(symbol: str, website: str = "", quote_type: str = "") -> list[str]:
    """Ordered candidates; the HUD tries each and falls back to a monogram."""
    out: list[str] = []
    sym = symbol.upper()
    if quote_type != "INDEX" and not sym.startswith("^") and "=" not in sym:
        base = sym.split("-USD")[0] if quote_type == "CRYPTOCURRENCY" else sym
        out.append(f"https://financialmodelingprep.com/image-stock/{base}.png")
        out.append(f"https://assets.parqet.com/logos/symbol/{base}?format=png")
    dom = urlparse(website).netloc.removeprefix("www.") if website else ""
    if dom:
        out.append(f"https://www.google.com/s2/favicons?domain={dom}&sz=128")
    return out


# ------------------------------------------------------------------ charts
def _points(df, with_ohlc: bool = True) -> list[dict]:
    pts = []
    for ts, row in df.iterrows():
        c = _num(row.get("Close"))
        if c is None:
            continue
        off = ts.utcoffset().total_seconds() if getattr(ts, "tzinfo", None) else 0
        p: dict[str, Any] = {"t": int(ts.timestamp() + off), "c": round(c, 4)}  # exchange-local wall time
        if with_ohlc:
            p.update(o=_r(row.get("Open"), 4), h=_r(row.get("High"), 4), l=_r(row.get("Low"), 4),
                     v=int(_num(row.get("Volume")) or 0))
        pts.append(p)
    # de-dup / sort (Yahoo occasionally repeats the live bar)
    seen: dict[int, dict] = {}
    for p in pts:
        seen[p["t"]] = p
    return [seen[k] for k in sorted(seen)]


def market_chart(symbol: str, range: str = "1D") -> dict:
    """Chart series for one symbol + range tab (also used by the HUD's range buttons)."""
    rng = range.upper() if range and range.upper() in RANGES else "1D"
    period, interval = RANGES[rng]
    sym = resolve_symbol(symbol)

    def fetch() -> dict:
        df = _yf().Ticker(sym).history(period=period, interval=interval, auto_adjust=False, prepost=False)
        if rng == "1D" and (df is None or df.empty):
            df = _yf().Ticker(sym).history(period="5d", interval="5m", auto_adjust=False)
            if not df.empty:
                last = df.index[-1].date()
                df = df[[d.date() == last for d in df.index]]
        pts = _points(df) if df is not None and not df.empty else []
        base = pts[0]["o"] if pts and rng != "1D" and pts[0].get("o") else (pts[0]["c"] if pts else None)
        last = pts[-1]["c"] if pts else None
        chg = (last - base) if (last is not None and base) else None
        return {"symbol": sym, "range": rng, "interval": interval, "points": pts, "base": _r(base, 4),
                "period_change": _r(chg, 4), "period_change_pct": _r(chg / base * 100 if chg is not None and base else None),
                "intraday": interval.endswith("m")}

    return _cached(f"chart:{sym}:{rng}", 60 if rng in ("1D", "5D") else 600, fetch)


# ------------------------------------------------------------------ news
def _news(symbol: str, n: int = 6) -> list[dict]:
    def fetch() -> list[dict]:
        s = _yf().Search(symbol, max_results=1, news_count=max(12, n * 2))
        items = []
        for it in s.news or []:
            rel = it.get("relatedTickers") or []
            thumb = ""
            for r in ((it.get("thumbnail") or {}).get("resolutions") or []):
                if r.get("tag") == "140x140" or not thumb:
                    thumb = r.get("url") or thumb
            items.append({"title": it.get("title", ""), "publisher": it.get("publisher", ""), "url": it.get("link", ""),
                          "ts": it.get("providerPublishTime"), "thumb": thumb, "related": rel[:6],
                          "_score": (0 if symbol in rel[:2] else 1 if symbol in rel else 2) + len(rel) / 40})
        items.sort(key=lambda x: (x["_score"], -(x["ts"] or 0)))
        for it in items:
            it.pop("_score", None)
        return items[:n]

    return _cached(f"news:{symbol}", 600, lambda: _safe(fetch, []))


# ------------------------------------------------------------------ one symbol: full card
def _ext_hours(i: dict) -> dict | None:
    st = (i.get("marketState") or "").upper()
    if st.startswith("PRE") and _num(i.get("preMarketPrice")):
        return {"label": "Pre-market", "price": _r(i["preMarketPrice"]), "change": _r(i.get("preMarketChange")),
                "change_pct": _r(i.get("preMarketChangePercent"))}
    if st in ("POST", "POSTPOST", "CLOSED", "PREPRE") and _num(i.get("postMarketPrice")):
        return {"label": "After hours", "price": _r(i["postMarketPrice"]), "change": _r(i.get("postMarketChange")),
                "change_pct": _r(i.get("postMarketChangePercent"))}
    return None


def _analyst(T, i: dict) -> dict | None:
    if not i.get("numberOfAnalystOpinions"):
        return None
    dist = None
    rec = _safe(lambda: T.recommendations)
    if rec is not None and not rec.empty:
        row = rec.iloc[0].to_dict()
        dist = {k: int(row.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    tgt = {k: _r(i.get(f"target{k.capitalize()}Price")) for k in ("low", "mean", "median", "high")}
    return {"key": (i.get("recommendationKey") or "").replace("_", " "), "mean": _r(i.get("recommendationMean")),
            "count": i.get("numberOfAnalystOpinions"), "dist": dist, "target": tgt}


def _earnings(T, i: dict) -> dict | None:
    hist = []
    eh = _safe(lambda: T.earnings_history)
    if eh is not None and not eh.empty:
        for q, row in eh.tail(6).iterrows():
            hist.append({"q": str(q)[:10], "actual": _r(row.get("epsActual")), "estimate": _r(row.get("epsEstimate")),
                         "surprise_pct": _r((_num(row.get("surprisePercent")) or 0) * 100, 1)})
    nxt = i.get("earningsTimestampStart") or i.get("earningsTimestamp")
    if nxt and nxt < time.time() - 86400:
        nxt = None
    est = None
    ee = _safe(lambda: T.earnings_estimate)
    if ee is not None and not ee.empty and "0q" in ee.index:
        r0 = ee.loc["0q"]
        est = {"avg": _r(r0.get("avg")), "low": _r(r0.get("low")), "high": _r(r0.get("high")),
               "year_ago": _r(r0.get("yearAgoEps")), "analysts": int(_num(r0.get("numberOfAnalysts")) or 0)}
    if not hist and not nxt:
        return None
    return {"next": nxt, "next_estimate": est, "history": hist}


def _financials(T) -> list[dict]:
    q = _safe(lambda: T.quarterly_income_stmt)
    if q is None or q.empty or "Total Revenue" not in q.index:
        return []
    out = []
    for col in list(q.columns)[:6][::-1]:
        rev = _num(q.at["Total Revenue", col])
        if rev is None:
            continue
        net = _num(q.at["Net Income", col]) if "Net Income" in q.index else None
        out.append({"q": str(col)[:10], "revenue": rev, "net_income": net})
    return out[-5:]


def _stock(sym: str, rng: str, record: bool) -> dict:
    yf = _yf()
    T = yf.Ticker(sym)
    info_f = _pool.submit(lambda: _cached(f"info:{sym}", 120, lambda: T.info or {}))
    chart_f = _pool.submit(market_chart, sym, rng)
    news_f = _pool.submit(_news, sym, 6)
    i = info_f.result()
    qt = (i.get("quoteType") or "").upper()
    if not i or (i.get("regularMarketPrice") is None and i.get("currentPrice") is None and not i.get("shortName")):
        raise ValueError(f"no market data for {sym}")
    equity = qt == "EQUITY"
    an_f = _pool.submit(_analyst, T, i) if equity else None
    ea_f = _pool.submit(_earnings, T, i) if equity else None
    fi_f = _pool.submit(_financials, T) if equity else None

    price = _num(i.get("regularMarketPrice")) or _num(i.get("currentPrice"))
    prev = _num(i.get("regularMarketPreviousClose")) or _num(i.get("previousClose"))
    chg = _num(i.get("regularMarketChange"))
    if chg is None and price is not None and prev:
        chg = price - prev
    pct = _num(i.get("regularMarketChangePercent"))
    if pct is None and chg is not None and prev:
        pct = chg / prev * 100
    website = i.get("website") or ""
    name = i.get("longName") or i.get("shortName") or sym
    if qt == "INDEX":
        name = INDEX_LABELS.get(sym, i.get("shortName") or name)
    res: dict[str, Any] = {
        "symbol": sym, "name": name, "short_name": i.get("shortName") or name, "type": qt or "EQUITY",
        "exchange": i.get("fullExchangeName") or i.get("exchange") or "", "currency": i.get("currency") or "USD",
        "price": _r(price, 4 if price and price < 2 else 2), "change": _r(chg, 4 if price and price < 2 else 2),
        "change_pct": _r(pct), "prev_close": _r(prev), "market_state": (i.get("marketState") or "").upper(),
        "ext": _ext_hours(i), "website": website, "logos": logo_urls(sym, website, qt),
        "stats": {
            "open": _r(i.get("regularMarketOpen") or i.get("open")), "day_low": _r(i.get("regularMarketDayLow") or i.get("dayLow")),
            "day_high": _r(i.get("regularMarketDayHigh") or i.get("dayHigh")), "year_low": _r(i.get("fiftyTwoWeekLow")),
            "year_high": _r(i.get("fiftyTwoWeekHigh")), "market_cap": _num(i.get("marketCap")),
            "pe": _r(i.get("trailingPE")), "fwd_pe": _r(i.get("forwardPE")), "eps": _r(i.get("trailingEps")),
            "div_yield": _r(i.get("dividendYield")), "beta": _r(i.get("beta")),
            "volume": _num(i.get("regularMarketVolume") or i.get("volume")),
            "avg_volume": _num(i.get("averageVolume")), "fifty_day": _r(i.get("fiftyDayAverage")),
            "two_hundred_day": _r(i.get("twoHundredDayAverage")),
            "profit_margin": _r((_num(i.get("profitMargins")) or 0) * 100, 1) if i.get("profitMargins") is not None else None,
            "revenue_growth": _r((_num(i.get("revenueGrowth")) or 0) * 100, 1) if i.get("revenueGrowth") is not None else None,
        },
        "profile": {
            "sector": i.get("sector") or i.get("category") or "", "industry": i.get("industry") or "",
            "employees": i.get("fullTimeEmployees"),
            "hq": ", ".join(x for x in (i.get("city"), i.get("state") or i.get("country")) if x),
            "summary": (i.get("longBusinessSummary") or "")[:1400],
        },
        "chart": chart_f.result(),
        "as_of": int(time.time()),
    }
    if qt in ("ETF", "MUTUALFUND"):
        res["fund"] = {"total_assets": _num(i.get("totalAssets")), "expense_ratio": _r(i.get("netExpenseRatio")),
                       "yield": _r((_num(i.get("yield")) or 0) * 100) if i.get("yield") is not None else None,
                       "category": i.get("category"), "ytd_return": _r(i.get("ytdReturn"))}
    if an_f:
        res["analyst"] = _safe(an_f.result)
        res["earnings"] = _safe(ea_f.result)
        res["financials"] = _safe(fi_f.result, [])
    res["news"] = _safe(news_f.result, [])
    if record:
        store.record_result("stock_quote", None, {"symbols": sym, "range": rng}, res)
    return res


def _compare(syms: list[str], rng: str, record: bool) -> dict:
    yf = _yf()

    def one(sym: str) -> dict:
        ch = market_chart(sym, rng)
        i = _safe(lambda: _cached(f"info:{sym}", 120, lambda: yf.Ticker(sym).info or {}), {}) or {}
        base = ch["points"][0]["c"] if ch["points"] else None
        pts = [{"t": p["t"], "v": round((p["c"] / base - 1) * 100, 3)} for p in ch["points"]] if base else []
        qt = (i.get("quoteType") or "").upper()
        return {"symbol": sym, "name": INDEX_LABELS.get(sym) or i.get("shortName") or sym, "type": qt,
                "price": _r(i.get("regularMarketPrice") or i.get("currentPrice")), "change_pct": _r(i.get("regularMarketChangePercent")),
                "period_pct": pts[-1]["v"] if pts else None, "market_cap": _num(i.get("marketCap")),
                "pe": _r(i.get("trailingPE")), "logos": logo_urls(sym, i.get("website") or "", qt), "points": pts}

    series = list(_pool.map(one, syms))
    res = {"range": rng, "series": series, "intraday": RANGES[rng][1].endswith("m"), "as_of": int(time.time())}
    if record:
        store.record_result("stock_quote", None, {"symbols": ",".join(syms), "range": rng}, res)
    return res


def stock_quote(symbols: str, range: str = "", record: bool = True) -> dict:
    """One symbol -> full stock card. 2-7 symbols (comma-separated) -> performance comparison card."""
    parts = [p.strip() for p in re.split(r"[,;/]| vs\.? | versus | and ", symbols or "", flags=re.I) if p.strip()]
    if not parts:
        raise ValueError("give at least one ticker or company name")
    syms: list[str] = []
    for s in _pool.map(resolve_symbol, parts[:7]):
        if s not in syms:
            syms.append(s)
    rng = (range or "").upper()
    if len(syms) == 1:
        return _stock(syms[0], rng if rng in RANGES else "1D", record)
    return _compare(syms, rng if rng in RANGES else "1Y", record)


# ------------------------------------------------------------------ what the model sees
# The HUD card gets the full result through the feed; the model only needs a few hundred characters to speak from.
# (Large tool outputs get spilled to a file by Hermes, costing extra read steps and ~10 s per answer.)
def _brief_stock(r: dict) -> dict:
    st, an, ea = r.get("stats") or {}, r.get("analyst") or {}, r.get("earnings") or {}
    out = {k: r.get(k) for k in ("symbol", "name", "type", "price", "change", "change_pct", "prev_close", "market_state", "currency")}
    out["after_hours"] = r.get("ext")
    out["stats"] = {k: st.get(k) for k in ("market_cap", "pe", "fwd_pe", "div_yield", "day_low", "day_high", "year_low",
                                            "year_high", "volume", "avg_volume", "revenue_growth", "profit_margin") if st.get(k) is not None}
    ch = r.get("chart") or {}
    out["chart"] = {"range": ch.get("range"), "period_change_pct": ch.get("period_change_pct")}
    if an:
        out["analyst"] = {"consensus": an.get("key"), "count": an.get("count"), "target": an.get("target"), "dist": an.get("dist")}
    if ea:
        out["earnings"] = {"next": dt.datetime.fromtimestamp(ea["next"]).strftime("%a %b %d %Y") if ea.get("next") else None,
                           "next_eps_estimate": (ea.get("next_estimate") or {}).get("avg"),
                           "last": (ea.get("history") or [])[-2:]}
    if r.get("fund"):
        out["fund"] = r["fund"]
    if r.get("financials"):
        out["revenue_last_quarters_b"] = [round(f["revenue"] / 1e9, 1) for f in r["financials"]]
    out["headlines"] = [n["title"] for n in (r.get("news") or [])[:3]]
    return out


def brief(tool: str, r: dict) -> dict:
    """Compact version of a markets result for the model (the card already has everything)."""
    if not isinstance(r, dict) or r.get("error"):
        return r
    if tool == "stock_quote" and r.get("series"):
        return {"range": r.get("range"), "series": [{k: s.get(k) for k in ("symbol", "name", "price", "change_pct",
                "period_pct", "market_cap", "pe")} for s in r["series"]], "card": "comparison chart shown"}
    if tool == "stock_quote":
        return {**_brief_stock(r), "card": "full stock card shown"}
    if tool == "market_overview" or r.get("indices") is not None:
        mv = r.get("movers") or {}
        slim = lambda xs: [{k: x.get(k) for k in ("symbol", "name", "price", "change_pct")} for x in (xs or [])[:4]]  # noqa: E731
        return {"market_state": r.get("market_state"), "focus": r.get("focus"),
                "indices": [{k: i.get(k) for k in ("label", "price", "change", "change_pct")} for i in r.get("indices") or []],
                "sectors": [{k: s.get(k) for k in ("label", "change_pct")} for s in r.get("sectors") or []],
                "gainers": slim(mv.get("gainers")), "losers": slim(mv.get("losers")), "most_active": slim(mv.get("active")),
                "crypto": [{k: c.get(k) for k in ("name", "symbol", "price", "change_24h", "change_7d")} for c in (r.get("crypto") or [])[:5]],
                "card": "market overview card shown"}
    if tool == "crypto_quote":
        return {**{k: r.get(k) for k in ("id", "name", "symbol", "rank", "price", "changes", "market_cap", "volume",
                                         "high_24h", "low_24h", "ath", "supply")},
                "chart": {"range": (r.get("chart") or {}).get("range"), "period_change_pct": (r.get("chart") or {}).get("period_change_pct")},
                "card": "full crypto card shown"}
    return r


def stock_open(symbol: str) -> dict:
    """HUD click (mover row, compare legend, news ticker): push a full stock card through the feed."""
    return stock_quote(symbol, "", record=True)


# ------------------------------------------------------------------ market overview
def _spark(df) -> list[float]:
    return [round(float(v), 4) for v in df["Close"].dropna().tolist()] if df is not None and not df.empty else []


def market_overview(focus: str = "", record: bool = True) -> dict:
    """Indices + sector heatmap + top movers + crypto: the 'how's the market' card."""
    yf = _yf()
    idx = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX", "GC=F", "CL=F", "BTC-USD"]

    def indices() -> list[dict]:
        d = yf.download(idx, period="1d", interval="5m", progress=False, group_by="ticker", threads=True, auto_adjust=False)
        out = []
        for s in idx:
            i = _safe(lambda s=s: _cached(f"info:{s}", 120, lambda: yf.Ticker(s).info or {}), {}) or {}
            sub = _safe(lambda s=s: d[s], None)
            sp = _spark(sub)
            price = _num(i.get("regularMarketPrice")) or (sp[-1] if sp else None)
            out.append({"symbol": s, "label": INDEX_LABELS.get(s, s), "price": _r(price),
                        "change": _r(i.get("regularMarketChange")), "change_pct": _r(i.get("regularMarketChangePercent")),
                        "prev_close": _r(i.get("regularMarketPreviousClose")), "spark": sp[-80:]})
        return out

    def sectors() -> list[dict]:
        d = yf.download([s for s, _ in SECTORS], period="5d", interval="1d", progress=False, group_by="ticker",
                        threads=True, auto_adjust=False)
        out = []
        for s, label in SECTORS:
            c = _safe(lambda s=s: d[s]["Close"].dropna().tolist(), [])
            pct = (c[-1] / c[-2] - 1) * 100 if len(c) >= 2 and c[-2] else None
            out.append({"symbol": s, "label": label, "change_pct": _r(pct)})
        return out

    def movers(kind: str) -> list[dict]:
        q = yf.screen(kind, count=8)
        out = []
        for x in (q or {}).get("quotes", [])[:8]:
            s = x.get("symbol", "")
            out.append({"symbol": s, "name": x.get("displayName") or x.get("shortName") or s,
                        "price": _r(x.get("regularMarketPrice")), "change_pct": _r(x.get("regularMarketChangePercent")),
                        "volume": _num(x.get("regularMarketVolume")), "market_cap": _num(x.get("marketCap")),
                        "logos": logo_urls(s, "", x.get("quoteType") or "")})
        return out

    f_idx = _pool.submit(lambda: _cached("ov:idx", 60, indices))
    f_sec = _pool.submit(lambda: _cached("ov:sec", 300, sectors))
    f_mv = {k: _pool.submit(lambda k=k: _cached(f"ov:{k}", 120, lambda: movers(k)))
            for k in ("day_gainers", "day_losers", "most_actives")}
    f_cr = _pool.submit(lambda: _safe(lambda: crypto_top(10), []))
    ix = _safe(f_idx.result, [])
    state = ""
    spx = _safe(lambda: _cached("info:^GSPC", 120, lambda: yf.Ticker("^GSPC").info or {}), {}) or {}
    state = (spx.get("marketState") or "").upper()
    res = {"focus": (focus or "").lower(), "market_state": state, "indices": ix, "sectors": _safe(f_sec.result, []),
           "movers": {"gainers": _safe(f_mv["day_gainers"].result, []), "losers": _safe(f_mv["day_losers"].result, []),
                      "active": _safe(f_mv["most_actives"].result, [])},
           "crypto": _safe(f_cr.result, []), "as_of": int(time.time())}
    if record:
        store.record_result("market_overview", None, {"focus": focus}, res)
    return res


# ------------------------------------------------------------------ crypto (CoinGecko)
_local = threading.local()


def _cg(path: str, **params) -> Any:
    c = getattr(_local, "cg", None)
    if c is None:
        c = _local.cg = httpx.Client(timeout=15, headers={"accept": "application/json", "user-agent": "jarvis-hud/0.1"})
    for attempt in range(3):
        r = c.get(CG + path, params=params)
        if r.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"CoinGecko {r.status_code}: {r.text[:160]}")
        return r.json()
    raise RuntimeError("CoinGecko rate limit, try again in a minute")


def resolve_coin(q: str) -> str:
    raw = q.strip().lower().removesuffix("-usd").removesuffix(" usd")
    common = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "ripple", "doge": "dogecoin",
              "ada": "cardano", "bnb": "binancecoin", "usdt": "tether", "usdc": "usd-coin", "ltc": "litecoin",
              "dot": "polkadot", "avax": "avalanche-2", "link": "chainlink", "matic": "matic-network",
              "shib": "shiba-inu", "trx": "tron", "ether": "ethereum", "bitcoin": "bitcoin", "ethereum": "ethereum"}
    if raw in common:
        return common[raw]

    def search() -> str:
        coins = _cg("/search", query=raw).get("coins") or []
        if not coins:
            raise ValueError(f"couldn't find a coin called “{q}”")
        exact = [c for c in coins if c.get("symbol", "").lower() == raw or c.get("name", "").lower() == raw]
        pick = sorted(exact or coins, key=lambda c: c.get("market_cap_rank") or 10**9)[0]
        return pick["id"]

    return _cached(f"coin:{raw}", 86400, search)


def crypto_top(n: int = 10) -> list[dict]:
    def fetch() -> list[dict]:
        rows = _cg("/coins/markets", vs_currency="usd", per_page=max(1, min(n, 50)), page=1, sparkline="true",
                   price_change_percentage="1h,24h,7d")
        out = []
        for c in rows:
            sp = ((c.get("sparkline_in_7d") or {}).get("price") or [])
            out.append({"id": c["id"], "symbol": (c.get("symbol") or "").upper(), "name": c.get("name"),
                        "logo": c.get("image"), "price": c.get("current_price"), "rank": c.get("market_cap_rank"),
                        "market_cap": c.get("market_cap"), "volume": c.get("total_volume"),
                        "change_1h": _r(c.get("price_change_percentage_1h_in_currency")),
                        "change_24h": _r(c.get("price_change_percentage_24h_in_currency")),
                        "change_7d": _r(c.get("price_change_percentage_7d_in_currency")),
                        "spark": [round(v, 6) for v in sp[::3]]})
        return out

    return _cached(f"cg:top:{n}", 90, fetch)


def crypto_chart(coin: str, range: str = "7D") -> dict:
    rng = range.upper() if range and range.upper() in CRYPTO_RANGES else "7D"
    cid = resolve_coin(coin)

    def fetch() -> dict:
        d = _cg(f"/coins/{cid}/market_chart", vs_currency="usd", days=CRYPTO_RANGES[rng])
        prices = d.get("prices") or []
        vols = {int(t // 1000): v for t, v in (d.get("total_volumes") or [])}
        step = max(1, len(prices) // 400)
        tz = dt.datetime.now().astimezone().utcoffset().total_seconds()
        pts = [{"t": int(t // 1000 + tz), "c": round(p, 8), "v": vols.get(int(t // 1000), 0)} for t, p in prices[::step]]
        if prices and pts and pts[-1]["t"] != int(prices[-1][0] // 1000 + tz):
            pts.append({"t": int(prices[-1][0] // 1000 + tz), "c": round(prices[-1][1], 8), "v": 0})
        base = pts[0]["c"] if pts else None
        last = pts[-1]["c"] if pts else None
        return {"id": cid, "range": rng, "points": pts, "base": base, "intraday": rng in ("1D", "7D"),
                "period_change_pct": _r((last / base - 1) * 100 if base and last else None)}

    return _cached(f"cg:chart:{cid}:{rng}", 120 if rng in ("1D", "7D") else 900, fetch)


def crypto_quote(coin: str = "", range: str = "", record: bool = True) -> dict:
    """One coin -> full crypto card. Empty coin -> the market overview with crypto in focus."""
    if not (coin or "").strip():
        return market_overview(focus="crypto", record=record)
    cid = resolve_coin(coin)
    rng = (range or "").upper()
    rng = rng if rng in CRYPTO_RANGES else "7D"
    f_chart = _pool.submit(crypto_chart, cid, rng)
    c = _cached(f"cg:coin:{cid}", 90, lambda: _cg(f"/coins/{cid}", localization="false", tickers="false",
                                                   community_data="false", developer_data="false", sparkline="false"))
    m = c.get("market_data") or {}
    usd = lambda k: (m.get(k) or {}).get("usd")  # noqa: E731
    desc = re.sub(r"<[^>]+>", "", ((c.get("description") or {}).get("en") or ""))
    desc = re.split(r"(?<=[.!?])\s+", desc.strip())
    res = {
        "id": cid, "symbol": (c.get("symbol") or "").upper(), "name": c.get("name"),
        "logo": (c.get("image") or {}).get("large"), "rank": c.get("market_cap_rank"),
        "price": usd("current_price"),
        "changes": {"1h": _r((m.get("price_change_percentage_1h_in_currency") or {}).get("usd")),
                    "24h": _r(m.get("price_change_percentage_24h")), "7d": _r(m.get("price_change_percentage_7d")),
                    "30d": _r(m.get("price_change_percentage_30d")), "1y": _r(m.get("price_change_percentage_1y"))},
        "change_24h": _r(usd("price_change_24h_in_currency") or m.get("price_change_24h")),
        "market_cap": usd("market_cap"), "fdv": usd("fully_diluted_valuation"), "volume": usd("total_volume"),
        "high_24h": usd("high_24h"), "low_24h": usd("low_24h"),
        "supply": {"circulating": m.get("circulating_supply"), "total": m.get("total_supply"), "max": m.get("max_supply")},
        "ath": {"price": usd("ath"), "pct": _r((m.get("ath_change_percentage") or {}).get("usd")),
                "date": ((m.get("ath_date") or {}).get("usd") or "")[:10]},
        "atl": {"price": usd("atl"), "date": ((m.get("atl_date") or {}).get("usd") or "")[:10]},
        "categories": [x for x in (c.get("categories") or []) if x][:4],
        "homepage": next((u for u in ((c.get("links") or {}).get("homepage") or []) if u), ""),
        "description": " ".join(desc[:3])[:700],
        "chart": f_chart.result(), "as_of": int(time.time()),
    }
    if record:
        store.record_result("crypto_quote", None, {"coin": cid, "range": rng}, res)
    return res


def crypto_open(coin: str) -> dict:
    return crypto_quote(coin, "", record=True)
