"""Briefing: model output validation + click actions/undo (Gmail calls faked)."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def B(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    import jarvis_core.briefing as mod
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "briefing.json")
    return mod


def mail(i, acct="personal", **kw):
    return {"account": acct, "email": f"{acct}@x.com", "id": f"m{i}", "threadId": f"t{i}", "from": f"S{i} <s{i}@y.com>",
            "from_name": f"S{i}", "subject": f"Sub {i}", "internalDate": 1000 + i, "unread": True,
            "important": False, "snippet": f"snip {i}", "body": "", **kw}


def test_shape_drops_hallucinated_refs_and_dedupes(B):
    mails = [mail(1), mail(2), mail(3, "work")]
    raw = {"todos": [{"title": "Pay", "detail": "d", "refs": ["e1", "e99", "m2"], "urgency": "high", "kind": "pay"},
                     {"title": "Ghost", "detail": "d", "refs": ["e42"], "urgency": "high", "kind": "task"}],
           "priority": [{"ref": "e1", "reason": "dup of todo"}, {"ref": "[e3]", "reason": "real"}],
           "topics": [{"title": "T", "emoji": "x", "items": [{"ref": "e2", "headline": "h", "summary": "s"},
                                                             {"ref": "e3", "headline": "dup", "summary": "s"}]}]}
    s = B.Briefing._shape(raw, mails)
    assert [t["title"] for t in s["todos"]] == ["Pay"]                   # ghost todo (only bad refs) dropped
    assert [m["id"] for m in s["todos"][0]["messages"]] == ["m1"]         # e99 / raw id ignored
    assert [p["messages"][0]["id"] for p in s["priority"]] == ["m3"]      # e1 already used -> skipped
    assert [i["messages"][0]["id"] for i in s["topics"][0]["items"]] == ["m2"]
    all_ids = [m["id"] for it in B.Briefing._all_items(s) for m in it["messages"]]
    assert len(all_ids) == len(set(all_ids))


def test_actions_and_undo(B, monkeypatch):
    calls = []
    g = B.gtools
    monkeypatch.setattr(g, "gmail_trash_now", lambda a, ids: calls.append(("trash", a, ids)) or {})
    monkeypatch.setattr(g, "gmail_untrash", lambda a, ids: calls.append(("untrash", a, ids)) or {})
    monkeypatch.setattr(g, "gmail_labels_now", lambda a, ids, add=None, remove=None:
                        calls.append(("labels", a, ids, add, remove)) or {})
    monkeypatch.setattr(g, "gmail_reply_now", lambda a, mid, body, ra=False: calls.append(("reply", a, mid, body)) or {})

    b = B.Briefing()
    mails = [mail(1), mail(2, "work"), mail(3)]
    raw = {"todos": [{"title": "A", "detail": "", "refs": ["e1", "e2"], "urgency": "high", "kind": "reply"}],
           "priority": [{"ref": "e3", "reason": "r"}], "topics": []}
    b.data = B.Briefing._shape(raw, mails)
    todo, prio = b.data["todos"][0]["id"], b.data["priority"][0]["id"]

    async def go():
        r = await b.act(todo, "trash")
        assert r["ok"] and not b.data["todos"]
        assert ("trash", "personal", ["m1"]) in calls and ("trash", "work", ["m2"]) in calls
        assert {"m1", "m2"} <= set(b.handled)
        r = await b.act(todo, "undo")
        assert r["ok"] and b.data["todos"][0]["id"] == todo
        assert ("untrash", "personal", ["m1"]) in calls and "m1" not in b.handled
        assert ("labels", "personal", ["m1"], ["INBOX"], None) in calls  # untrash alone doesn't restore INBOX
        # reply goes to the LATEST message of the item; empty body refused
        assert not (await b.act(todo, "reply_send", ""))["ok"]
        r = await b.act(todo, "reply_send", "Thanks, Stephen")
        assert r["ok"] and ("reply", "work", "m2", "Thanks, Stephen") in calls
        assert (await b.act(prio, "archive"))["ok"]
        assert ("labels", "personal", ["m3"], None, ["INBOX"]) in calls
        assert not (await b.act("nope", "trash"))["ok"]
        assert not (await b.act(prio, "delete_everything"))["ok"]
        assert (await b.act("zzz", "undo"))["ok"] is False

    asyncio.run(go())


def test_dismiss_touches_no_gmail(B, monkeypatch):
    for name in ("gmail_trash_now", "gmail_untrash", "gmail_labels_now", "gmail_reply_now"):
        monkeypatch.setattr(B.gtools, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("gmail touched")))
    b = B.Briefing()
    b.data = B.Briefing._shape({"todos": [], "priority": [{"ref": "e1", "reason": "r"}], "topics": []}, [mail(1)])
    r = asyncio.run(b.act(b.data["priority"][0]["id"], "dismiss"))
    assert r["ok"] and not b.data["priority"] and "m1" in b.handled
