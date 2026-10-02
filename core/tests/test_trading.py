"""Trading desk: cost basis from the ledger, order sizing + guards, confirm-only execution, drift guard, monitor."""
import json

import pytest

from jarvis_google import store, trading as T


def L(i, refid, typ, asset, amount, fee="0", t=0, aclass="currency", sub=""):
    return {"id": f"L{i}", "refid": refid, "type": typ, "subtype": sub, "asset": asset, "aclass": aclass,
            "amount": str(amount), "fee": str(fee), "time": t or i}


@pytest.fixture(autouse=True)
def assets(monkeypatch):
    monkeypatch.setattr(T, "_assets", lambda: {"XXBT": {"altname": "XBT"}, "XETH": {"altname": "ETH"}, "SOL": {"altname": "SOL"}})
    monkeypatch.setattr(T, "_pairs", lambda: {"XXBTZUSD": {"wsname": "XBT/USD", "altname": "XBTUSD", "lot_decimals": 8,
                                                           "pair_decimals": 1, "ordermin": "0.00005", "costmin": "0.5"}})
    T._cache.clear()


def test_cost_basis_average_cost_and_realized():
    e = [L(1, "a", "trade", "ZUSD", -1000, sub="tradespot"), L(2, "a", "trade", "XXBT", 0.02, sub="tradespot"),
         L(3, "b", "trade", "ZUSD", -1500, sub="tradespot"), L(4, "b", "trade", "XXBT", 0.01, sub="tradespot"),
         L(5, "c", "trade", "ZUSD", 1200, fee=2, sub="tradespot"), L(6, "c", "trade", "XXBT", -0.01, sub="tradespot")]
    b = T.cost_basis(e)["BTC"]
    assert round(b["qty"], 8) == 0.02
    assert round(b["cost"], 2) == round(2500 * 2 / 3, 2)               # average cost carries over
    assert round(b["realized"], 2) == round(1198 - 2500 / 3, 2)         # proceeds net of fee minus avg cost


def test_deposits_have_unknown_basis_not_zero_cost():
    e = [L(1, "d", "deposit", "XETH", 2), L(2, "s", "trade", "ZUSD", 6000, sub="tradespot"), L(3, "s", "trade", "XETH", -2, sub="tradespot")]
    b = T.cost_basis(e)["ETH"]
    assert b["realized"] == 0 and round(b["realized_unknown_qty"], 6) == 2  # never booked as a $6,000 gain


def test_stock_trades_and_dividends():
    e = [L(1, "x", "trade", "ZUSD", -400, sub="tradeequities"), L(2, "x", "trade", "TSLA", 1, aclass="equity", sub="tradeequities"),
         L(3, "dv", "dividend", "ZUSD", 2.5, sub="cashdividend")]
    cb = T.cost_basis(e)
    assert cb["TSLA"]["cost"] == 400 and cb["TSLA"]["kind"] == "stock"
    assert cb["__totals__"]["dividends"] == 2.5


@pytest.fixture()
def acct(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    monkeypatch.setattr(T, "ALERTS", tmp_path / "alerts.json")
    monkeypatch.setattr(T, "MONITOR", tmp_path / "mon.json")
    monkeypatch.setattr(T, "configured", lambda: True)
    monkeypatch.setattr(T, "crypto_tickers", lambda s: {"BTC": {"price": 80000, "bid": 79990, "ask": 80010, "change_pct": 1.0,
                                                                 "pair": "XXBTZUSD"}})
    monkeypatch.setattr(T, "_fee_rate", lambda k: 0.004)
    monkeypatch.setattr(T, "portfolio", lambda record=True: {"cash": 400.0, "positions": [
        {"symbol": "BTC", "kind": "crypto", "qty": 0.01, "value": 800, "day_change_pct": 6.2, "price": 80000}]})
    calls = []

    def priv(method, data=None):
        calls.append((method, dict(data or {})))
        if method == "AddOrder" and (data or {}).get("validate"):
            return {"descr": {"order": "validated"}}
        if method == "AddOrder":
            return {"txid": ["OABC-123"], "descr": {"order": "buy 0.00125 XBTUSD @ market"}}
        if method == "QueryOrders":
            return {"OABC-123": {"status": "closed", "vol_exec": "0.00125", "price": "80010", "cost": "100.01", "fee": "0.4"}}
        if method == "OpenOrders":
            return {"open": {}}
        raise AssertionError(method)
    monkeypatch.setattr(T, "_priv", priv)
    store.EXECUTORS.update({"trade_order": T._exec_order, "trade_cancel": T._exec_cancel})
    return calls


def test_preview_sizes_with_fees_and_lot_decimals(acct):
    p = T.preview("btc", "buy", amount_usd=100)
    assert p["quantity"] == round(100 / 80010, 8) and p["kraken_params"]["pair"] == "XXBTZUSD"
    assert p["est_fee"] == 0.4 and p["est_total"] == round(p["est_cost"] + 0.4, 2) and p["validation"] == "ok"
    assert any(c[0] == "AddOrder" and c[1].get("validate") == "true" for c in acct)


def test_sell_all_clamps_to_holding_and_buy_checks_cash(acct):
    assert T.preview("BTC", "sell", check=False)["quantity"] == 0.01
    assert any("more than your" in w for w in T.preview("BTC", "buy", amount_usd=5000, check=False)["warnings"])
    with pytest.raises(RuntimeError, match="more than your"):
        T.order("BTC", "buy", amount_usd=5000)


def test_order_only_proposes_then_executes_on_confirm(acct):
    r = T.order("BTC", "buy", amount_usd=100, reason="test")
    assert r["status"] == "awaiting_user_confirmation"
    assert not [c for c in acct if c[0] == "AddOrder" and not c[1].get("validate")]  # nothing placed yet
    a = store.get_action(r["action_id"])
    assert a["kind"] == "trade_order" and a["preview"]["type"] == "trade" and a["preview"]["action_label"].startswith("Buy")
    out = store.execute_action(r["action_id"], "voice")
    assert out["ok"] and out["result"]["txid"] == "OABC-123" and out["result"]["status"] == "closed"
    placed = [c for c in acct if c[0] == "AddOrder" and not c[1].get("validate")]
    assert len(placed) == 1 and placed[0][1]["ordertype"] == "market"
    assert store.execute_action(r["action_id"], "voice")["ok"] is False  # can't be replayed


def test_market_order_aborts_if_price_drifted(acct, monkeypatch):
    r = T.order("BTC", "buy", amount_usd=100)
    monkeypatch.setattr(T, "crypto_tickers", lambda s: {"BTC": {"price": 86000, "bid": 85990, "ask": 86010}})
    out = store.execute_action(r["action_id"], "click")
    assert out["ok"] is False and "moved" in out["error"]
    assert not [c for c in acct if c[0] == "AddOrder" and not c[1].get("validate")]


def test_stock_order_blocked_is_reported(acct, monkeypatch):
    monkeypatch.setattr(T, "stock_quotes", lambda s: {"TSLA": {"price": 370.0}})

    def priv(method, data=None):
        raise RuntimeError("Kraken: EQuery:Unknown asset pair")
    monkeypatch.setattr(T, "_priv", priv)
    with pytest.raises(RuntimeError, match="Kraken app"):
        T.order("TSLA", "buy", amount_usd=50)


def test_monitor_fires_alerts_and_big_moves_once(acct):
    T.alert_set.__globals__["portfolio"] = T.portfolio  # alert_set refreshes the desk; patched portfolio is fine
    T.alert_set("BTC", below=85000, note="buy zone")
    fired = T.monitor_tick()
    kinds = sorted(f["type"] for f in fired)
    assert kinds == ["move", "price"]
    assert T.monitor_tick() == []  # same day, same band: no repeat
    assert json.loads(T.ALERTS.read_text())[0]["active"] is False


def test_signals():
    s = T.signals([100 + i for i in range(260)])
    assert s["trend"] == "up" and s["above_200d"] and s["rsi14"] == 100.0
