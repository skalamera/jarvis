from jarvis_core import visuals as V
from jarvis_core.session import classify_confirmation


def test_extract_visuals_strips_blocks():
    text = ('You have two meetings, sir.\n```jarvis-visual\n{"type":"stats","items":[{"label":"Meetings","value":2}]}\n```'
            '\nAnything else?')
    clean, cards = V.extract_visuals(text)
    assert "jarvis-visual" not in clean and "two meetings" in clean
    assert cards[0]["kind"] == "visual.stats" and cards[0]["data"]["items"][0]["value"] == 2


def test_invalid_visual_dropped_and_unterminated_hidden():
    clean, cards = V.extract_visuals('Hi.\n```jarvis-visual\n{bad json}\n```\nBye.\n```jarvis-visual\n{"type":"ch')
    assert cards == [] and clean == "Hi.\n\nBye."
    assert V.partial_visible_text('Hello.\n```jarvis-visual\n{"type":') == "Hello.\n"


def test_speakable_removes_markup_and_urls():
    s = V.speakable("## Summary\n- **Three** unread from `Dhruv` — see https://x.com/a\n\n| a | b |\n|---|---|")
    assert "http" not in s and "#" not in s and "*" not in s and "|" not in s and "—" not in s
    assert "Three unread from Dhruv" in s


def test_sentence_stream_emits_complete_sentences_once():
    ss = V.SentenceStream()
    assert ss.feed("Good evening, sir. You have thr") == ["Good evening, sir."]
    assert ss.feed("Good evening, sir. You have three unread. And a meet") == ["You have three unread."]
    assert ss.feed("Good evening, sir. You have three unread. And a meeting at noon.", final=True) == [
        "And a meeting at noon."]


def test_sentence_stream_caps_spoken_length():
    ss = V.SentenceStream(max_spoken_chars=30)
    out = ss.feed("This sentence is long enough to count. Second sentence here. Third one.", final=True)
    assert len(out) == 1 and ss.truncated


def test_cards_from_feed():
    item = {"tool": "gmail_search", "account": "work",
            "result": {"email": "stephen@hadrius.com", "query": "is:unread", "messages": []}}
    [c] = V.cards_from_feed(item)
    assert c["kind"] == "email_list" and c["account"] == "work"
    assert V.cards_from_feed({"tool": "gmail_search", "account": "w", "result": {"error": "x"}}) == []


def test_tool_labels():
    assert V.tool_label("mcp__jarvis_google__gmail_search") == "GMAIL · SEARCH"
    assert V.tool_label("mcp__linear__list_issues") == "LINEAR · LIST ISSUES"
    assert V.tool_label("tool_describe") is None
    assert V.tool_label("web_search") == "WEB · SEARCH"


def test_confirmation_grammar():
    for t in ("confirm", "Confirm.", "yes, send it", "send it", "do it", "go ahead", "Jarvis, confirm", "approved"):
        assert classify_confirmation(t) == "confirm", t
    for t in ("cancel", "no", "never mind", "don't send", "stop", "cancel that", "Scratch that."):
        assert classify_confirmation(t) == "cancel", t
    for t in ("send an email to Bob about lunch", "what's on my calendar", "confirm my meeting with Dana tomorrow at 3pm please"):
        assert classify_confirmation(t) is None, t
