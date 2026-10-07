"""Kalshi autopilot decision rules (no network, no money: the market feed and order call are faked)."""
import time
from datetime import datetime, timezone

import pytest

from jarvis_google import kalshi as K
from jarvis_google import kalshi_auto as A


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "PATH", tmp_path / "auto.json")
    orders = []
    st = {"left": 200, "up": 0.85, "down": 0.16, "avg": 0.85, "worst": 0.86, "bal": 500.0}

    def market(series):
        close = datetime.fromtimestamp(time.time() + st["left"], tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {"ticker": "KXBTC15M-T1", "close_time": close, "yes_ask_dollars": str(st["up"]),
                "no_ask_dollars": str(st["down"]), "yes_sub_title": "Target Price: $1"}
    monkeypatch.setattr(A, "_current_market", market)
    monkeypatch.setattr(K, "quote", lambda t, o, a, dollars=0, shares=0: {"shares": 117, "avg_price": st["avg"], "limit": st["worst"], "cost": 99.9})
    monkeypatch.setattr(K, "account_summary", lambda: {"balance": st["bal"]})

    def order(t, o, a, dollars=0, shares=0, confirmed=False):
        assert confirmed
        orders.append((t, o, dollars))
        return {"fill_count": "117.00", "average_fill_price": str(st["avg"]), "order_id": "x"}
    monkeypatch.setattr(K, "quick_order", order)
    monkeypatch.setattr(A.store, "audit", lambda e: None)
    A.configure(enabled=True)
    return st, orders


def test_off_does_nothing(env):
    st, orders = env
    A.configure(enabled=False)
    assert A.tick()["action"] == "disabled" and not orders


def test_buys_up_at_80_in_window(env):
    st, orders = env
    r = A.tick()
    assert r["action"] == "traded" and orders == [("KXBTC15M-T1", "yes", 100.0)]
    assert A.tick()["action"] == "already_traded" and len(orders) == 1  # one trade per market


def test_buys_down(env):
    st, orders = env
    st.update(up=0.12, down=0.89, avg=0.89, worst=0.90)
    assert A.tick()["action"] == "traded" and orders[0][1] == "no"


@pytest.mark.parametrize("change,action", [
    ({"left": 600}, "waiting"), ({"left": 10}, "too_late"), ({"up": 0.7, "down": 0.31}, "below_threshold"),
    ({"avg": 0.78}, "price_out_of_range"), ({"worst": 0.99}, "price_out_of_range"), ({"bal": 50.0}, "low_balance"),
])
def test_guards(env, change, action):
    st, orders = env
    st.update(change)
    assert A.tick()["action"] == action and not orders


def test_daily_loss_limit(env):
    st, orders = env
    d = A._load()
    d["trades"].append({"t": time.time(), "ticker": "OLD", "outcome": "yes", "shares": 100, "cost": 300, "result": "lost", "pnl": -300})
    A._save(d)
    assert A.tick()["action"] == "loss_limit" and not orders
