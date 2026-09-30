"""Slack inbox: mrkdwn rendering, unread detection, briefing dismiss/undo. Offline (all Slack calls faked)."""
import asyncio

import pytest

from jarvis_google import slack as S

ME = {"user_id": "UME", "team_id": "T1", "url": "https://acme.slack.com", "team": "Acme"}
USERS = {
    "UME": {"id": "UME", "handle": "stephen", "name": "Stephen S", "avatar": "", "bot": False},
    "UBEN": {"id": "UBEN", "handle": "ben", "name": "Ben Carter", "avatar": "https://img/ben", "bot": False},
    "UPY": {"id": "UPY", "handle": "pylon", "name": "Pylon", "avatar": "https://img/py", "bot": True},
}


def test_render_text_mentions_links_dates_emoji():
    S._cache["me"] = ME
    t, links = S.render_text(
        "<@UME> can you check <https://x.com/doc|the doc>? cc <@UBEN> <!here> :tada: *bold* "
        "<!date^1790740800^{date_short}|Sep 30> :some-custom-emoji: &amp; done", USERS)
    assert "@you" in t and "@Ben Carter" in t and "@here" in t
    assert "the doc" in t and links == [{"url": "https://x.com/doc", "label": "the doc"}]
    assert "🎉" in t and "Sep 30" in t and "bold" in t and "*" not in t
    assert ":some-custom-emoji:" not in t and "&" in t


def test_bot_message_uses_blocks_when_text_empty():
    m = {"text": "", "bot_id": "B1", "user": "UPY", "blocks": [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Issue #1* - Printer on fire"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "New customer message"}]}]}
    assert "Printer on fire" in S._msg_text(m) and "New customer message" in S._msg_text(m)


def _hit(cid, ts, user, text, im=True):
    return {"channel": {"id": cid, "is_im": im, "is_mpim": False, "name": cid}, "ts": ts, "user": user, "text": text,
            "permalink": f"https://p/{cid}/{ts}"}


def test_gather_counts_only_messages_after_last_read(monkeypatch):
    S._cache["me"] = ME
    monkeypatch.setattr(S, "me", lambda: ME)
    monkeypatch.setattr(S, "available", lambda: True)
    monkeypatch.setattr(S.slack_dir, "user_map", lambda: USERS)
    monkeypatch.setattr(S, "_important_channel_posts", lambda *a: [])
    hits = [_hit("D1", "100.0", "UBEN", "old, already read"), _hit("D1", "200.0", "UBEN", "new one"),
            _hit("D1", "300.0", "UBEN", "another new"), _hit("D2", "150.0", "UPY", "bot ping"),
            _hit("D3", "120.0", "UBEN", "all read here")]
    infos = {"D1": {"id": "D1", "is_im": True, "user": "UBEN", "last_read": "150.0"},
             "D2": {"id": "D2", "is_im": True, "user": "UPY", "last_read": "0"},
             "D3": {"id": "D3", "is_im": True, "user": "UBEN", "last_read": "999.0"}}

    def fake_get(method, **p):
        if method == "search.messages":
            q = p["query"]
            return {"messages": {"matches": hits if q.startswith("to:me") else [], "paging": {"pages": 1}}}
        raise AssertionError(method)

    monkeypatch.setattr(S, "_get", fake_get)
    monkeypatch.setattr(S, "_conv_info", lambda cid: infos[cid])
    d = S.gather()
    by = {c["id"]: c for c in d["conversations"]}
    assert set(by) == {"D1", "D2"}  # D3 fully read -> not shown
    assert by["D1"]["unread"] == 2 and [m["text"] for m in by["D1"]["messages"]] == ["new one", "another new"]
    assert by["D1"]["title"] == "Ben Carter" and not by["D1"]["bot"] and by["D2"]["bot"]
    assert d["conversations"][0]["id"] == "D1"  # people before bots
    assert by["D1"]["url"].startswith("slack://channel?team=T1&id=D1")


def test_briefing_slack_dismiss_and_undo(monkeypatch, tmp_path):
    from jarvis_core import briefing as B
    monkeypatch.setattr(B, "STATE_FILE", tmp_path / "b.json")
    b = B.Briefing()
    b.slack = {"available": True, "conversations": [{"id": "D1", "latest_ts": "200.0", "bot": False, "unread": 1}],
               "mentions": [{"id": "C1:5.0"}], "channels": []}

    async def run():
        r = await b.act("slack:D1:200.0", "dismiss")
        assert r["ok"] and b.slack_view()["conversations"] == []
        # a NEW message from the same person brings the conversation back
        b.slack["conversations"][0]["latest_ts"] = "300.0"
        assert len(b.slack_view()["conversations"]) == 1
        b.slack["conversations"][0]["latest_ts"] = "200.0"
        r = await b.act("slack:D1:200.0", "undo")
        assert r["ok"] and len(b.slack_view()["conversations"]) == 1
        assert (await b.act("slack:C1:5.0", "trash"))["ok"] is False  # Slack is read-only
    asyncio.run(run())
