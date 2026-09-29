import importlib
import time

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    from jarvis_google import store as s
    importlib.reload(s)
    calls = []
    s.EXECUTORS["test_send"] = lambda account, **p: calls.append((account, p)) or {"sent": True}
    s._calls = calls
    return s


def test_propose_does_not_execute(store):
    r = store.propose("test_send", "personal", {"to": "a@b.c"}, "Send to a@b.c", {"type": "email_send"})
    assert r["status"] == "awaiting_user_confirmation"
    assert store._calls == []
    assert [a["id"] for a in store.pending_actions()] == [r["action_id"]]


def test_execute_once_only(store):
    aid = store.propose("test_send", "work", {"to": "x@y.z"}, "s", {})["action_id"]
    assert store.execute_action(aid, "click") == {"ok": True, "result": {"sent": True}}
    again = store.execute_action(aid, "click")
    assert again["ok"] is False and "executed" in again["error"]
    assert len(store._calls) == 1


def test_cancel_blocks_execution(store):
    aid = store.propose("test_send", "work", {}, "s", {})["action_id"]
    assert store.cancel_action(aid)
    assert store.execute_action(aid, "voice")["ok"] is False
    assert store._calls == []


def test_expired_actions_cannot_execute(store, monkeypatch):
    aid = store.propose("test_send", "work", {}, "s", {})["action_id"]
    real = time.time
    monkeypatch.setattr(store.time, "time", lambda: real() + store.ACTION_TTL_S + 5)
    assert store.pending_actions() == []
    assert store.execute_action(aid, "click")["ok"] is False
    assert store._calls == []


def test_executor_failure_is_reported(store):
    store.EXECUTORS["boom"] = lambda account, **p: (_ for _ in ()).throw(RuntimeError("quota"))
    aid = store.propose("boom", "work", {}, "s", {})["action_id"]
    out = store.execute_action(aid, "click")
    assert out == {"ok": False, "error": "RuntimeError: quota"}
    assert store.get_action(aid)["status"] == "failed"


def test_audit_log_written(store):
    aid = store.propose("test_send", "work", {}, "Send it", {})["action_id"]
    store.execute_action(aid, "voice")
    lines = store.AUDIT_PATH.read_text().strip().splitlines()
    assert '"proposed"' in lines[0] and '"executed"' in lines[1] and '"voice"' in lines[1]


def test_feed_roundtrip(store):
    head = store.feed_head()
    store.record_result("gmail_search", "work", {"q": "x"}, {"messages": [1]})
    items = store.feed_since(head)
    assert items[0]["tool"] == "gmail_search" and items[0]["result"] == {"messages": [1]}


def test_account_resolution():
    from jarvis_google.accounts import AccountError, resolve_account
    assert resolve_account("Personal") == "personal"
    assert resolve_account("hadrius") == "work"
    assert resolve_account("someone@hadrius.com") == "work"
    with pytest.raises(AccountError):
        resolve_account("")
    with pytest.raises(AccountError):
        resolve_account("yahoo")


def test_mcp_has_no_confirm_tool():
    """The model must not be able to confirm its own proposals."""
    import asyncio
    from jarvis_google.server import mcp
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "gmail_send" in names and "gmail_search" in names
    assert not any(k in n for n in names for k in ("confirm", "execute", "approve"))
