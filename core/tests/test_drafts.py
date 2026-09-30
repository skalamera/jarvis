"""Draft safety: the model may only revise drafts JARVIS created; lookups don't spawn cards."""
import json

import pytest

from jarvis_core import visuals
from jarvis_google import server, store, tools


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    monkeypatch.setattr(store, "AUDIT_PATH", tmp_path / "actions.jsonl")
    monkeypatch.setattr(tools, "resolve_account", lambda a: a)


def test_update_refuses_drafts_jarvis_did_not_create(monkeypatch):
    called = []
    monkeypatch.setattr(tools, "gmail_update_draft", lambda **kw: called.append(kw) or {"status": "draft_updated"})
    out = json.loads(server.gmail_update_draft("work", "r-users-own-draft", "a@b.c", "Re: hi", "text"))
    assert "refused" in out["error"] and "gmail_create_draft" in out["error"]
    assert called == []  # Gmail never touched


def test_update_allowed_for_own_draft(monkeypatch):
    store.remember_own_draft("work", "r-jarvis-draft")
    monkeypatch.setattr(tools, "gmail_update_draft", lambda **kw: {"status": "draft_updated", **kw})
    out = json.loads(server.gmail_update_draft("work", "r-jarvis-draft", "a@b.c", "Re: hi", "text"))
    assert out["status"] == "draft_updated"
    # ownership is per account
    assert not store.is_own_draft("personal", "r-jarvis-draft")


def test_contacts_search_result_makes_no_card():
    # contacts_search is a lookup, not something to display
    item = {"tool": "contacts_search", "account": "work", "result": {"query": "pat", "contacts": []}}
    assert visuals.cards_from_feed(item) == []


def test_contacts_mail_fallback_is_silent(monkeypatch):
    class _Ppl:
        def searchContacts(self, **kw):
            return self
        def searchDirectoryPeople(self, **kw):
            return self
        def execute(self):
            return {}

    class _Svc:
        def people(self):
            return _Ppl()

    seen = []

    def fake_search(account, query, max_results=10, show=True):
        seen.append(show)
        return {"messages": [{"from_email": "Pat@Example.com", "from_name": "Pat"}]}

    monkeypatch.setattr(tools, "service", lambda *a: _Svc())
    monkeypatch.setattr(tools, "gmail_search", fake_search)
    out = tools.contacts_search("work", "pat")
    assert seen == [False]  # internal lookup must not put a search card on the HUD
    assert out["contacts"][0]["emails"] == ["Pat@Example.com"]
