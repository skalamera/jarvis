"""Support blueprint tool, feed capture, showcase triggers and pacing (no network, synthetic data only)."""
import asyncio
import json

import pytest

from jarvis_core import showcase as S
from jarvis_core import visuals as V
from jarvis_google import store, support


BP = {
    "version": 1, "title": "Hadrius Support Model", "subtitle": "test", "summary": "Three layers, three tiers.",
    "narrative": "Intro paragraph about the model.\n\nTier 1 Jamie answers how-to tickets.\n\nOn Hold tickets bump after a timer.",
    "stats": [{"label": "Tiers", "value": "3"}],
    "layers": [{"id": "pylon", "name": "Pylon triggers", "owns": ["routing"]}],
    "architecture": {"groups": [{"id": "g", "label": "G"}], "nodes": [{"id": "a", "label": "A", "group": "g"}], "edges": []},
    "tiers": [{"tier": "1", "name": "Front line", "agent": "Jamie", "mission": "m", "does": ["x"]}],
    "flows": [{"id": "f", "title": "Intake", "lanes": [{"id": "l", "label": "L"}],
               "steps": [{"id": "s1", "lane": "l", "label": "Start", "kind": "start", "next": []}]}],
    "routing": [{"when": "feature request", "route": "Tier 3 On Hold"}],
    "lifecycle": {"states": [{"id": "new", "label": "New"}], "transitions": []},
    "faq": [{"q": "Who replies?", "a": "Jamie."}], "glossary": [{"term": "On You", "def": "waiting on us"}],
}


@pytest.fixture
def blueprint(tmp_path, monkeypatch):
    p = tmp_path / "support_blueprint.json"
    p.write_text(json.dumps(BP))
    monkeypatch.setattr(support, "PATH", p)
    support._cache.update({"mtime": None, "data": None})
    return p


def test_capture_holds_feed_items(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "j.db")
    head = store.feed_head()
    with store.capture() as items:
        store.record_result("weather", None, {}, {"x": 1})
    assert [i["tool"] for i in items] == ["weather"]
    assert store.feed_head() == head  # nothing published
    store.record_result("weather", None, {}, {"x": 2})
    assert store.feed_head() == head + 1


def test_capture_follows_to_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "j.db")

    async def go():
        with store.capture() as items:
            await asyncio.to_thread(store.record_result, "music_play", None, {}, {"title": "t"})
        return items
    assert len(asyncio.run(go())) == 1


def test_support_model_tool_and_card(blueprint, monkeypatch):
    with store.capture() as items:
        out = support.support_model("routing")
    assert out["routing"] and out["narrative"] and out["faq"]
    assert items and items[0]["tool"] == "support_model" and items[0]["result"]["tab"] == "routing"
    cards = V.cards_from_feed(items[0])
    assert cards[0]["kind"] == "support_blueprint" and cards[0]["data"]["key"] == "support:blueprint"
    with store.capture():
        free = support.support_model("feature request")
    assert free["matches"]["routing"]
    with store.capture() as items:
        t = support.support_model("", tour=True)
    assert "tour" in t and items[0]["args"]["tour"] is True


def test_narration_tabs(blueprint):
    paras = support.narration()
    assert [p["tab"] for p in paras][0] == "overview"
    assert paras[1]["tier"] == "1" and paras[1]["tab"] == "tiers"
    assert paras[2]["tab"] == "lifecycle"


def test_missing_blueprint(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "PATH", tmp_path / "nope.json")
    support._cache.update({"mtime": None, "data": None})
    assert not support.available()


@pytest.mark.parametrize("text", ["Hey Jarvis, what do you have for me today?", "what do you have for me today",
                                  "Jarvis, what have you got for me today?", "run the demo", "tell me about my day",
                                  "Good morning Jarvis, how's my day looking?", "give me my briefing", "brief me on the day"])
def test_showcase_triggers(text):
    assert S.is_trigger(text)


@pytest.mark.parametrize("text", ["what do you have on my calendar today", "what's on today", "demo day is Friday",
                                  "do you have the report"])
def test_showcase_not_triggered(text):
    assert not S.is_trigger(text)


def test_tour_routing():
    assert S.is_tour_request("explain how the 3 tier support system works at Hadrius")
    assert S.is_tour_request("walk me through our support model")
    assert S.is_tour_request("give me an overview of the three-tier support model")
    assert not S.is_tour_request("what does Hadrian do in the 3 tier model")  # specific: the model answers
    assert not S.is_tour_request("how does a feature request get routed in the support model")
    assert not S.is_tour_request("explain this spreadsheet")


def test_chunk_text_and_units():
    chunks = S.chunk_text("One. " * 120, 100)
    assert all(len(c) <= 100 for c in chunks) and len(chunks) > 3
    assert S._units("1 hr 5 min, 17 mi") == "1 hour 5 minutes, 17 miles"
    assert S._co("Apple Inc.") == "Apple" and S._co("Microsoft Corporation") == "Microsoft"


class FakeVoice:
    async def tts(self, text):
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\0\0" * 1600)  # 0.1 s
        return buf.getvalue()


class FakeSession:
    def __init__(self, answer: str | None = "no"):
        self.sent, self.speak, self.voice, self._played = [], True, FakeVoice(), asyncio.Event()
        self.briefing_question, self.pending_input, self.answer, self.listened = None, None, answer, []

    async def send(self, m):
        self.sent.append(m)
        if m.get("type") == "speech":  # the HUD finishes the clip and reports playback done
            asyncio.get_running_loop().call_later(0.12, self._played.set)

    async def state(self, s):
        self.sent.append({"type": "state", "state": s})

    async def listen_for_answer(self, timeout_s):  # he answers a moment after the question
        self.listened.append(timeout_s)
        if self.answer is not None:
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, lambda: self.briefing_question and not self.briefing_question.done()
                            and self.briefing_question.set_result(self.answer))


def test_support_tour_drives_tabs(blueprint):
    s = FakeSession()
    asyncio.run(S.support_tour(s))
    ui = [m for m in s.sent if m["type"] == "ui"]
    tabs = [m["tab"] for m in ui if m["op"] == "support_tab"]
    assert ui[0]["op"] == "expand" and tabs[:3] == ["overview", "tiers", "lifecycle"] and tabs[-1] == "overview"
    speech = [m for m in s.sent if m["type"] == "speech"]
    assert len(speech) >= 5 and [m["seq"] for m in speech] == list(range(len(speech)))
    assert s.sent[-1] == {"type": "state", "state": "idle"}
    assert any(m["type"] == "showcase" and not m["active"] for m in s.sent)


def _fake_beats(monkeypatch):
    def mk(label):
        def b():
            return S.Beat(label, f"{label} line.", [V.card("notice", label, {"text": label})], after_s=0)
        return b
    names = ["_weather", "_commute", "_calendar", "_email", "_mag7", "_btc"]
    fakes = {n: mk(n.strip("_").upper()) for n in names}
    fakes["_calendar"] = lambda: None  # a beat can opt out
    fakes["_email"] = lambda: (_ for _ in ()).throw(RuntimeError("gmail down"))  # and fail
    monkeypatch.setattr(S, "BRIEFING", [fakes[n] for n in names])
    monkeypatch.setattr(S, "CLOSING", [mk("VIDEO"), mk("MUSIC")])


def _spoken(s):
    return [m["text"] for m in s.sent if m["type"] == "speech"]


def test_briefing_order_question_then_closing(monkeypatch):
    _fake_beats(monkeypatch)
    s = FakeSession(answer="No, not yet")
    asyncio.run(S.run(s))
    lines = _spoken(s)
    assert lines[0].startswith("Good ") and "your day" not in lines[0].lower()
    assert lines[1:5] == ["WEATHER line.", "COMMUTE line.", "MAG7 line.", "BTC line."]
    assert lines[5] == S.ASK_PREP and lines[6] in S._DECLINE_QUIPS
    assert lines[7:] == ["VIDEO line.", "MUSIC line."]
    assert s.listened == [S.ASK_PREP_LISTEN_S] and s.pending_input is None
    order = [m["type"] for m in s.sent if m["type"] in ("card", "speech")]
    assert order[1:3] == ["card", "speech"]  # each beat's display lands before its line
    assert s.sent[-1] == {"type": "state", "state": "idle"}


def test_briefing_yes_hands_off_to_model(monkeypatch):
    _fake_beats(monkeypatch)
    s = FakeSession(answer="Yes please")
    asyncio.run(S.run(s))
    lines = _spoken(s)
    assert lines[-1] == S.ASK_PREP and "VIDEO line." not in lines
    assert "prepare for my upcoming meetings" in s.pending_input


def test_briefing_silence_counts_as_no(monkeypatch):
    _fake_beats(monkeypatch)
    monkeypatch.setattr(S, "ASK_PREP_LISTEN_S", 0.05)
    s = FakeSession(answer="")
    asyncio.run(S.run(s))
    assert _spoken(s)[-2:] == ["VIDEO line.", "MUSIC line."]


@pytest.mark.parametrize("text,kind", [("no", "no"), ("No, not yet.", "no"), ("not yet", "no"), ("nope", "no"),
                                       ("no thanks", "no"), ("maybe later", "no"), ("yes", "yes"),
                                       ("Sure, the 12 o'clock", "yes"), ("what's on at noon?", "")])
def test_prep_answer(text, kind):
    assert S.prep_answer(text) == kind


def test_btc_quip_varies_with_move():
    calm, rally, crash = S._btc_quip(1, 0.2, 0), S._btc_quip(1, 7, 0), S._btc_quip(1, -8, 0)
    assert len({calm, rally, crash}) == 3


def test_showcase_cancel_is_clean(monkeypatch):
    def slow():
        import time
        time.sleep(0.05)
        return S.Beat("X", "x.", [])
    monkeypatch.setattr(S, "BRIEFING", [slow] * 6)
    s = FakeSession()

    async def go():
        t = asyncio.create_task(S.run(s))
        await asyncio.sleep(0.25)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert any(m["type"] == "showcase" and not m["active"] for m in s.sent)


def test_speech_helpers():
    assert S._first("Jordan Talbot") == "Jordan" and S._first("Vercel") == "Vercel" and S._first("Example Wealth Team") == "Example Wealth Team"
    assert S._sentence("Update default payment method on AWS Account 123456789012") == "update default payment method on AWS"
    assert S._join(["a", "b", "c"]) == "a, b and c" and S._join(["a"]) == "a"


def test_car_listing_parse():
    from jarvis_google import cars
    card = ('<div data-x="1" class=" article-search-result search-result clearfix" data-listing-url="https://example.com/l?a=1&amp;b=2">'
            '<img src="https://t2.iseecars.com/img/abc.jpg"/><h3 class="listing-price">$13,290</h3>'
            '<h5 class="srp-listing-title">2013 Mercedes-Benz CLS-Class CLS 550 4MATIC</h5><ul><li>Sedan</li>'
            '<li>63,937 Miles</li><li>Yonkers, NY</li></ul><div>GREAT DEAL $3,245 Below market</div>'
            '<script type="application/ld+json">{"@context":"http://schema.org/","@type":"Vehicle",'
            '"vehicleIdentificationNumber":"WDDLJ9BB0DA000001","color":"White"}</script></div>')
    other = card.replace("CLS 550 4MATIC", "CLS 450 4MATIC").replace("000001", "000002")
    rows = cars.parse("<html>" + card + other + "</html>")
    assert len(rows) == 2
    r = rows[0]
    assert (r["year"], r["price"], r["miles"], r["location"], r["color"], r["deal"], r["below_market"]) == \
        (2013, 13290, 63937, "Yonkers, NY", "White", "Great deal", 3245)
    assert r["url"] == "https://example.com/l?a=1&b=2" and r["photo"].endswith("abc.jpg")
    assert cars._slug("CLS 550 4MATIC") in cars._slug(r["title"]) and cars._slug("CLS 550 4MATIC") not in cars._slug(rows[1]["title"])


def test_home_location_overrides_ip(monkeypatch):
    from jarvis_google import weather as W
    monkeypatch.setattr(W, "HOME", "40.9115,-73.7824")
    monkeypatch.setattr(W, "_home_cache", None)
    h = W._here(None)  # no network: a lat,lon home needs no lookup
    assert h["source"] == "home" and abs(h["lat"] - 40.9115) < 1e-6


@pytest.mark.parametrize("text,kind", [("Morning, Jarvis.", "morning"), ("Good morning Jarvis!", "morning"),
                                       ("morning jervis", "morning"), ("Jarvis, good morning.", "morning"),
                                       ("Hey Jarvis", "wake"), ("hey jarvis what time is it", "wake"),
                                       ("morning everyone", None), ("pass the salt", None), ("", None)])
def test_sleep_word(text, kind):
    from jarvis_core.main import sleep_word
    assert sleep_word(text) == kind


def test_morning_and_sleep_phrases():
    assert S.is_morning("Morning, Jarvis") and S.is_morning("good morning jarvis.") and not S.is_morning("morning meeting at 9")
    for x in ["Jarvis, go to sleep", "goodnight jarvis", "Okay, go to sleep.", "power down"]:
        assert S.is_sleep_request(x), x
    assert not S.is_sleep_request("how did I sleep last night") and not S.is_sleep_request("schedule power down maintenance")


class SleepSession(FakeSession):
    """FakeSession + the real Session sleep/power-on methods."""
    from jarvis_core.session import Session as _S
    go_to_sleep, wake_up, power_on = _S.go_to_sleep, _S.wake_up, _S.power_on

    def __init__(self):
        super().__init__()
        self.sleeping, self._state, self.mic, self.started = False, "idle", type("M", (), {"sleeping": False})(), []

    async def cancel_turn(self):
        pass

    async def say_line(self, text):
        self.sent.append({"type": "system_line", "text": text})
        self._played.set()

    async def start_showcase(self, which, intro_delay=0.0):
        self.started.append((which, intro_delay))

    async def state(self, s):
        self._state = s
        self.sent.append({"type": "state", "state": s})


def test_sleep_then_morning_powers_on():
    s = SleepSession()
    asyncio.run(s.go_to_sleep("Goodnight, sir."))
    assert s.sleeping and s.mic.sleeping and {"type": "sleep", "asleep": True} in s.sent and s._state == "sleep"
    asyncio.run(s.power_on())
    assert not s.sleeping and not s.mic.sleeping
    kinds = [m["type"] for m in s.sent]
    assert kinds.index("power_on") < len(kinds) and s.started == [("demo", S.POWER_ON_S)]


def test_hey_jarvis_wakes_without_cinematic():
    s = SleepSession()
    asyncio.run(s.go_to_sleep())
    asyncio.run(s.wake_up())
    assert not s.sleeping and s._state == "idle" and not any(m["type"] == "power_on" for m in s.sent) and not s.started


def test_after_wake_word():
    from jarvis_core.main import after_wake_word
    assert after_wake_word("Hey Jarvis, what's the weather?") == "what's the weather?"
    assert after_wake_word("Hey Jarvis.") == "" and after_wake_word("Hey Jarvis, um") == ""
