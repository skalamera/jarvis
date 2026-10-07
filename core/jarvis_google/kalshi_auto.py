"""Kalshi autopilot: BTC 15-minute Up/Down rule trader (his rules, his money; OFF until he turns it on).

Rule (default): in the last 5 minutes of each 15-minute BTC market, if either side is priced at >= 80% (the price
he would actually pay, from the live book), buy $100 of that side. At most one trade per market.
Guards: skip if balance < stake, stop for the day after a net loss limit, never trade in the final 20 seconds,
never pay above the max price. Every check and trade is logged to state/kalshi_auto.json and shown in the HUD.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import kalshi as K
from . import store

log = logging.getLogger("jarvis.kalshi_auto")
PATH = store.STATE_DIR / "kalshi_auto.json"
DEFAULTS: dict[str, Any] = {
    "enabled": False, "series": "KXBTC15M", "stake": 100.0, "threshold": 0.80, "window_s": 300, "last_s": 20,
    "max_price": 0.97, "daily_loss_limit": 300.0, "poll_s": 15,
}
_lock = threading.Lock()
_started = False


def _load() -> dict:
    try:
        d = json.loads(PATH.read_text())
    except Exception:
        d = {}
    d.setdefault("config", {})
    d["config"] = {**DEFAULTS, **d["config"]}
    d.setdefault("trades", [])
    d.setdefault("log", [])
    return d


def _save(d: dict) -> None:
    d["log"] = d["log"][-300:]
    d["trades"] = d["trades"][-500:]
    PATH.write_text(json.dumps(d, indent=1, default=str))


def _note(d: dict, msg: str, **kw) -> None:
    d["log"].append({"t": time.time(), "msg": msg, **kw})
    log.info("kalshi-auto: %s", msg)


def _today(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _settle(d: dict) -> None:
    """Fill in win/loss for finished trades (market result)."""
    for t in d["trades"]:
        if t.get("result") or not t.get("shares"):
            continue
        if time.time() < t.get("close_ts", 0) + 30:
            continue
        try:
            m = K._get(f"/markets/{t['ticker']}").get("market") or {}
        except Exception:
            continue
        res = m.get("result")
        if res in ("yes", "no"):
            won = res == t["outcome"]
            t["result"] = "won" if won else "lost"
            t["pnl"] = round((t["shares"] if won else 0) - t["cost"], 2)


def today_pnl(d: dict) -> float:
    day = _today(time.time())
    return round(sum(t.get("pnl") or 0 for t in d["trades"] if _today(t["t"]) == day), 2)


def _current_market(series: str) -> dict | None:
    ms = K._get("/markets", {"series_ticker": series, "status": "open", "limit": 5}).get("markets") or []
    ms = [m for m in ms if m.get("close_time")]
    if not ms:
        return None
    return min(ms, key=lambda m: m["close_time"])


def tick() -> dict:
    """One check. Safe to call any time; trades only when every rule passes."""
    with _lock:
        d = _load()
        c = d["config"]
        _settle(d)
        out: dict[str, Any] = {"action": "none"}
        try:
            if not c["enabled"]:
                return {"action": "disabled"}
            m = _current_market(c["series"])
            if not m:
                return {"action": "no_market"}
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
            left = close - time.time()
            out.update(ticker=m["ticker"], seconds_left=int(left))
            if left > c["window_s"]:
                return {**out, "action": "waiting"}
            if left < c["last_s"]:
                return {**out, "action": "too_late"}
            if any(t["ticker"] == m["ticker"] for t in d["trades"]):
                return {**out, "action": "already_traded"}
            if today_pnl(d) <= -abs(c["daily_loss_limit"]):
                _note(d, f"Daily loss limit hit ({today_pnl(d)}); skipping {m['ticker']}")
                return {**out, "action": "loss_limit"}
            up, down = K._f(m.get("yes_ask_dollars")), K._f(m.get("no_ask_dollars"))
            side = "yes" if up >= c["threshold"] else "no" if down >= c["threshold"] else None
            out.update(up=up, down=down)
            if not side:
                return {**out, "action": "below_threshold"}
            q = K.quote(m["ticker"], side, "buy", dollars=c["stake"])
            if not q["shares"] or q["avg_price"] < c["threshold"] or (q["limit"] or 1) > c["max_price"]:
                _note(d, f"{m['ticker']}: {('UP' if side == 'yes' else 'DOWN')} at {up if side == 'yes' else down:.2f} but fill "
                         f"would be avg {q['avg_price']:.2f} / worst {q['limit']}; skipped (max price {c['max_price']})")
                _save(d)
                return {**out, "action": "price_out_of_range", "quote": q}
            bal = K.account_summary()["balance"]
            if bal < q["cost"]:
                _note(d, f"Balance ${bal:.2f} < ${q['cost']:.2f}; skipped {m['ticker']}")
                _save(d)
                return {**out, "action": "low_balance"}
            r = K.quick_order(m["ticker"], side, "buy", dollars=c["stake"], confirmed=True)
            filled = K._f(r.get("fill_count"))
            # quick_order already converts the V2 (YES-book) fill price to the side bought
            avg = K._f(r.get("average_fill_price")) or q["avg_price"]
            trade = {"t": time.time(), "ticker": m["ticker"], "outcome": side, "direction": "UP" if side == "yes" else "DOWN",
                     "shares": filled, "avg_price": avg, "cost": round(filled * avg + K._fee(filled, avg) if filled else 0, 2),
                     "close_ts": close, "order_id": r.get("order_id"), "target": m.get("yes_sub_title")}
            d["trades"].append(trade)
            _note(d, f"BOUGHT {filled:g} {trade['direction']} on {m['ticker']} @ {avg:.2f} (${trade['cost']:.2f}), "
                     f"{int(left)}s left", trade=True)
            store.audit({"event": "kalshi_autopilot_trade", **trade})
            out.update(action="traded", trade=trade)
            return out
        except Exception as e:
            _note(d, f"error: {e}")
            out.update(action="error", error=str(e)[:200])
            return out
        finally:
            _save(d)


def _loop() -> None:
    while True:
        try:
            c = _load()["config"]
            if c["enabled"]:
                r = tick()
                left = r.get("seconds_left")
                # sleep until the window opens, then poll every poll_s inside it
                if r["action"] in ("waiting",) and left:
                    time.sleep(max(5, min(60, left - c["window_s"] + 1)))
                    continue
                time.sleep(c["poll_s"] if r["action"] in ("below_threshold", "price_out_of_range") else 20)
            else:
                time.sleep(30)
        except Exception as e:  # never die
            log.warning("kalshi-auto loop: %s", e)
            time.sleep(30)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="kalshi-autopilot", daemon=True).start()


# ------------------------------------------------------------------ HUD ops
def status() -> dict:
    with _lock:
        d = _load()
        _settle(d)
        _save(d)
    tr = d["trades"]
    done = [t for t in tr if t.get("result")]
    return {"config": d["config"], "trades": list(reversed(tr[-50:])), "log": list(reversed(d["log"][-60:])),
            "today_pnl": today_pnl(d), "wins": sum(t["result"] == "won" for t in done), "losses": sum(t["result"] == "lost" for t in done),
            "total_pnl": round(sum(t.get("pnl") or 0 for t in done), 2), "connected": K.connected()}


def configure(**changes) -> dict:
    allowed = {"enabled": bool, "stake": float, "threshold": float, "window_s": int, "max_price": float,
               "daily_loss_limit": float}
    with _lock:
        d = _load()
        for k, v in changes.items():
            if k in allowed:
                d["config"][k] = allowed[k](v)
        c = d["config"]
        c["stake"] = max(1.0, min(c["stake"], 1000.0))
        c["threshold"] = max(0.5, min(c["threshold"], 0.99))
        c["window_s"] = max(30, min(c["window_s"], 840))
        _note(d, f"settings: {'ON' if c['enabled'] else 'OFF'} · ${c['stake']:g} at ≥{c['threshold'] * 100:.0f}% · last "
                 f"{c['window_s'] // 60}m · max price {c['max_price']:.2f} · daily loss limit ${c['daily_loss_limit']:g}")
        store.audit({"event": "kalshi_autopilot_config", **c})
        _save(d)
    return status()


def live() -> dict | None:
    m = _current_market(_load()["config"]["series"])
    return K._mkt(m) if m else None


CLICK_OPS = {"kalshi_auto_live": live, "kalshi_auto_status": status, "kalshi_auto_config": configure, "kalshi_auto_tick": tick}
