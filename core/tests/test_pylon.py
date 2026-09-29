"""Pylon click-op safety rules, with the Pylon/Linear HTTP layer faked (no network)."""
import pytest

from jarvis_google import pylon as P

P.STEPHEN_ID = "00000000-0000-0000-0000-00000000cafe"  # fake "me" (the real id lives in ~/.hermes/.env)

ISSUE = {"id": "00000000-0000-0000-0000-000000000001", "number": 900001, "title": "Test", "state": "waiting_on_you",
         "source": "email", "team": {"id": "team-t3"}, "assignee": {"id": P.STEPHEN_ID},
         "requester": {"id": "contact-1"}, "account": {"id": "acct-1"}, "tags": [], "custom_fields": {"priority": {"value": "high"}}}


@pytest.fixture
def fake(monkeypatch):
    calls = []
    state = {"issue": dict(ISSUE)}

    def req(method, path, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "GET" and path.startswith("/issues/") and path.endswith("/messages"):
            return {"data": [{"id": "m1", "is_private": False, "timestamp": "2026-09-01T00:00:00Z", "author": {"contact": {}}},
                             {"id": "m2", "is_private": True, "timestamp": "2026-09-02T00:00:00Z", "author": {}}]}
        if method == "GET" and path.startswith("/issues/"):
            return {"data": state["issue"]}
        if method == "PATCH":
            body = kw["json"]
            if "state" in body:
                state["issue"]["state"] = body["state"]
            if "assignee_id" in body:
                state["issue"]["assignee"] = {"id": body["assignee_id"]} if body["assignee_id"] else None
            if "team_id" in body:
                state["issue"]["team"] = {"id": body["team_id"]} if body["team_id"] else None
        return {"data": {}}

    monkeypatch.setattr(P, "_req", req)
    monkeypatch.setattr(P, "_teams", lambda: [{"id": "team-t3", "name": "Tier 3"}, {"id": "team-am", "name": "Account Management"}])
    monkeypatch.setattr(P, "_users", lambda: [{"id": P.STEPHEN_ID, "name": "Stephen", "email": "stephen@example.com"},
                                              {"id": "user-2", "name": "Jordan", "email": "jordan@example.com"}])
    monkeypatch.setattr(P, "_statuses", lambda: [{"slug": s, "label": s, "category": s} for s in
                                                 ("new", "waiting_on_you", "waiting_on_customer", "on_hold", "closed")])
    monkeypatch.setattr(P, "_account", lambda aid: {"id": aid, "name": "Example Wealth"})
    monkeypatch.setattr(P, "_person", lambda pid: {"id": pid, "name": "Jordan Client", "email": "jordan@example.com"})
    monkeypatch.setattr(P.store, "_audit", lambda e: calls.append(("AUDIT", e["kind"], e["source"])))
    return calls


def writes(calls):
    return [c for c in calls if c[0] in ("PATCH", "POST")]


def test_assign_same_person_sends_nothing(fake):
    r = P.pylon_assign(ISSUE["id"], P.STEPHEN_ID.upper())  # case-insensitive compare
    assert writes(fake) == [] and "Already" in r["text"]


def test_assign_other_person_clears_team_first(fake):
    r = P.pylon_assign(ISSUE["id"], "user-2")
    w = writes(fake)
    assert w[0][2] == {"team_id": ""} and w[1][2] == {"assignee_id": "user-2"} and len(w) == 2
    assert r["undo"] == {"op": "pylon_assign", "args": {"issue_id": ISSUE["id"], "user_id": P.STEPHEN_ID}}
    assert ("AUDIT", "pylon_assign", "pylon_click") in fake


def test_status_change_and_undo_payload(fake):
    r = P.pylon_set_status(ISSUE["id"], "on_hold")
    assert writes(fake) == [("PATCH", f"/issues/{ISSUE['id']}", {"state": "on_hold"})]
    assert r["undo"]["args"]["state"] == "waiting_on_you"
    with pytest.raises(ValueError):
        P.pylon_set_status(ISSUE["id"], "made_up")


def test_same_status_is_noop(fake):
    P.pylon_set_status(ISSUE["id"], "waiting_on_you")
    assert writes(fake) == []


def test_reply_threads_on_latest_public_and_never_patches_status(fake):
    P.pylon_reply(ISSUE["id"], "Hi Jordan,\n\nAll set.\n\nThanks,\nStephen")
    w = writes(fake)
    assert len(w) == 1 and w[0][1].endswith("/reply")
    body = w[0][2]
    assert body["message_id"] == "m1"  # latest PUBLIC message, not the private note m2
    assert body["user_id"] == P.STEPHEN_ID and body["email_info"]["to_emails"] == ["jordan@example.com"]
    assert not any(c[0] == "PATCH" for c in fake)


def test_reply_blocks_em_dash_and_empty(fake):
    with pytest.raises(ValueError):
        P.pylon_reply(ISSUE["id"], "Hi — there")
    with pytest.raises(ValueError):
        P.pylon_reply(ISSUE["id"], "   ")
    assert writes(fake) == []


def test_note_is_internal_and_attributed(fake):
    P.pylon_note(ISSUE["id"], "line one\nline two")
    w = writes(fake)
    assert w[0][1].endswith("/note") and w[0][2]["user_id"] == P.STEPHEN_ID and "<br>" in w[0][2]["body_html"]


def test_snooze_rejects_past(fake):
    with pytest.raises(ValueError):
        P.pylon_snooze(ISSUE["id"], "2000-01-01T00:00:00Z")
    assert writes(fake) == []


def test_link_key_validation(fake):
    with pytest.raises(ValueError):
        P.pylon_linear_link(ISSUE["id"], "not a key")


def test_write_ops_are_not_mcp_tools():
    import inspect
    from jarvis_google import server
    src = inspect.getsource(server)
    for op in P.CLICK_WRITE:
        assert f"P.{op}" not in src, f"{op} must stay click-only"
