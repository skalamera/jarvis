"""'Morning, Jarvis': the unscripted briefing (trigger phrases, prompt, session flow). No network."""
import asyncio
import datetime as dt

import pytest

from jarvis_core import morning as M


@pytest.mark.parametrize("text,ok", [("Morning, Jarvis", True), ("Good morning, Jarvis.", True), ("Jarvis, good morning!", True),
                                     ("morning jervis", True), ("morning meeting at 9", False),
                                     ("Good afternoon, Jarvis", True), ("good evening jarvis", True),
                                     ("Jarvis, good evening.", True), ("Afternoon, Jarvis", True),
                                     ("good evening everyone", False), ("what's on this evening, jarvis?", False),
                                     ("Wake up, Daddy's home", False), ("what's on this morning, jarvis?", False)])
def test_is_hello(text, ok):
    assert M.is_hello(text) is ok


def test_sleep_word_hello_vs_cinematic():
    from jarvis_core.main import sleep_word
    assert sleep_word("Morning, Jarvis.") == "hello"
    assert sleep_word("Wake up, Daddy's home") == "morning"
    assert sleep_word("Hey Jarvis") == "wake"


def test_prompt_carries_data_rules_and_history():
    now = dt.datetime(2026, 10, 12, 7, 30, tzinfo=M._tz())  # a Monday
    p = M.build_prompt({"weather": {"today": {"hi": 64}}}, [{"date": "2026-10-11", "song": "Hooked on a Feeling"}], now)
    assert "Do not greet him again" in p and '"hi":64' in p and "Hooked on a Feeling" in p
    assert "Grand Central" in p and "[[VIDEO:" in p and "[[SONG:" in p and "Do NOT call youtube_video" in p
    assert "blank line between them" in p
    assert "at most two questions" in p
    weekend = M.build_prompt({}, [], dt.datetime(2026, 10, 11, 9, 0, tzinfo=M._tz()))
    assert "no train info" in weekend
    # the day's tone varies by date
    tones = {M.build_prompt({}, [], now + dt.timedelta(days=i)).split("Tone: ")[1].split(".")[0] for i in range(10)}
    assert len(tones) > 1
    assert "[MORNING BRIEFING]" in p and "from New Rochelle into Grand Central" in p and "start the day" in p


@pytest.mark.parametrize("hour,part", [(7, "morning"), (11, "morning"), (12, "afternoon"), (16, "afternoon"),
                                       (17, "evening"), (22, "evening")])
def test_part_of_day(hour, part):
    assert M.part_of_day(dt.datetime(2026, 10, 12, hour, 0, tzinfo=M._tz())) == part


def test_afternoon_and_evening_prompts_drop_the_morning_bits():
    aft = M.build_prompt({}, [], dt.datetime(2026, 10, 12, 14, 0, tzinfo=M._tz()))  # Monday afternoon
    eve = M.build_prompt({}, [], dt.datetime(2026, 10, 12, 19, 30, tzinfo=M._tz()))
    assert "[AFTERNOON BRIEFING]" in aft and '"Good afternoon, sir."' in aft and "LEFT of today" in aft
    assert "home from Grand Central to New Rochelle" in aft and "song for the afternoon" in aft
    assert "[EVENING BRIEFING]" in eve and '"Good evening, sir."' in eve and "TOMORROW's calendar" in eve
    assert "how the markets closed" in eve and "unwind" in eve
    for p in (aft, eve):
        assert "start the day" not in p and "RECENT MORNINGS" not in p and "into Grand Central" not in p
    assert M.train_leg("morning") == (M.NEW_ROCHELLE, M.GRAND_CENTRAL)
    assert M.train_leg("evening") == (M.GRAND_CENTRAL, M.NEW_ROCHELLE)
    assert set(M.GREETINGS) == set(M.FILLERS) == set(M.SONG_INTROS) == set(M.PARTS)


def test_history_from_feed(tmp_path, monkeypatch):
    from jarvis_google import store
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "j.db")
    monkeypatch.setattr(M, "LOG_PATH", tmp_path / "morning_log.json")
    M.begin_log(dt.datetime.now(M._tz()))
    store.record_result("music_play", None, {"query": "Mr. Blue Sky", "kind": "song"}, {"title": "Mr. Blue Sky"})
    store.record_result("youtube_video", None, {"query": "bears packers"}, {"results": [{"title": "Bears vs Packers"}]})
    h = M.recent_history()
    assert h and h[-1]["song"] == "Mr. Blue Sky" and h[-1]["video"] == "Bears vs Packers"


class FakeVoice:
    async def tts(self, text):
        return b"RIFF"


def test_start_morning_greets_first_then_hands_to_model(monkeypatch):
    from jarvis_core.session import Session
    sent, prompts = [], []

    async def send(m):
        sent.append(m)

    s = Session.__new__(Session)
    s.morning, s.last_reply, s.speak, s.voice, s.turn_task, s._state = M.Morning(), "", True, FakeVoice(), None, "idle"
    s.send = send

    async def state(x):
        s._state = x
    s.state = state

    async def cancel_turn():
        pass
    s.cancel_turn = cancel_turn

    async def turn(text):
        prompts.append(text)
        s.last_reply = "Shall I help you prep for the Acme demo?"
    s._turn = turn
    def fake_gather(weekday, progress=None, part="morning"):
        progress("cards", [{"id": "c1", "kind": "weather"}])
        progress("item", "weather", True)
        return {"weather": {"ok": 1}}, [{"id": "c1", "kind": "weather"}]
    monkeypatch.setattr(M, "gather", fake_gather)
    monkeypatch.setattr(M, "recent_history", lambda: [])
    monkeypatch.setattr(M, "begin_log", lambda now: None)
    monkeypatch.setattr(M, "part_of_day", lambda now: "evening")

    async def go():
        await s.start_morning()
        await s.turn_task
    asyncio.run(go())
    speech = [m for m in sent if m["type"] == "speech"]
    assert speech[0]["text"] == "Good evening, sir." and speech[1]["text"] in M.FILLERS["evening"]  # greeting, then a filler line
    scan = [m for m in sent if m["type"] == "morning_scan"]
    assert scan[0]["active"] and {"key": "weather", "label": "WEATHER"} in scan[0]["items"]
    assert {"type": "morning_scan", "item": "weather", "status": "ok"} in scan
    assert {"type": "morning_scan", "stage": "composing"} in scan and scan[-1] == {"type": "morning_scan", "active": False}
    assert any(m["type"] == "card" for m in sent)
    assert prompts and "[EVENING BRIEFING]" in prompts[0] and s.morning.song_intro == M.SONG_INTROS["evening"]
    assert s.morning.active and s.expects_answer()  # it asked something: the briefing waits for his answer


def test_media_held_until_speech_finishes(tmp_path, monkeypatch):
    """During the briefing a song / video the model starts is NOT played under the speech: it's released after."""
    from jarvis_google import store
    from jarvis_core.session import Session
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "j.db")
    sent = []

    async def send(m):
        sent.append(m)
    s = Session.__new__(Session)
    s.morning, s._held, s.send, s._feed_pos, s.speak = M.Morning(), [], send, store.feed_head(), True
    s.focus, s.notes, s._tour_pending, s.sleeping = None, [], False, False
    s._played, s._audio_out = asyncio.Event(), True
    s.voice = FakeVoice()

    async def state(x):
        pass
    s.state = state
    real_send = send

    async def send2(m):  # the HUD "plays" each intro line instantly
        await real_send(m)
        if m["type"] == "speech":
            s._audio_out = False
            asyncio.get_running_loop().call_later(0.02, s._played.set)
    s.morning.start()
    store.record_result("music_play", None, {"query": "Mr. Blue Sky", "kind": "song"},
                        {"title": "Mr. Blue Sky", "queue": [{"videoId": "x"}], "kind": "song"})
    store.record_result("youtube_video", None, {"query": "q"},
                        {"query": "q", "current": 0, "results": [{"video_id": "abcdefghijk", "title": "V"}]})
    monkeypatch.setattr(store, "pending_actions", lambda: [])

    async def go():
        await s._flush_feed("turn_1")
        assert not [m for m in sent if m["type"] == "card"]  # nothing plays yet
        rel = asyncio.create_task(s._release_held("turn_1", {"video": "Here's a video on the Bears.", "song": ""}))
        await asyncio.sleep(0.1)
        assert not [m for m in sent if m["type"] == "card"]  # still speaking
        s.send = send2
        s._audio_out = False
        s._played.set()  # the HUD finished the briefing's last sentence
        await rel
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    asyncio.run(go())
    seq = [("say:" + m["text"]) if m["type"] == "speech" else m["card"]["kind"] for m in sent if m["type"] in ("card", "speech")]
    assert seq == ["say:Here's a video on the Bears.", "video", "media_control", "say:" + M.SONG_INTRO, "music"]


def test_intro_tags_parsed_and_never_spoken():
    t = "Markets are up.\n[[VIDEO_INTRO: Here's a video on Starship.]]\n[[SONG_INTRO: And now, a little ELO.]]"
    assert M.parse_intros(t) == {"video": "Here's a video on Starship.", "song": "And now, a little ELO."}
    assert M.strip_intros(t) == "Markets are up."
    assert M.strip_intros("Markets are up.\n[") == "Markets are up."  # a tag that's still streaming
    assert M.video_intro({"data": {"query": "Bears Packers preview"}}).startswith("Here's a video on Bears Packers preview")


def _fast_sleep(real):
    async def f(t):
        await real(min(t, 0.01))
    return f


def test_closing_tags_and_pauses():
    from jarvis_core import visuals as V
    t = ("Rain later.\n\nMarkets closed up. BTC flat.\n[[VIDEO: Bears Packers preview | day]]\n"
         "[[VIDEO_INTRO: Here's a preview.]]\n[[SONG: ELO - Mr. Blue Sky]]\n[[SONG_INTRO: And now, ELO.]]")
    tags = M.parse_intros(t)
    assert tags == {"video_query": "Bears Packers preview", "video_recent": "day", "video": "Here's a preview.",
                    "song_query": "ELO - Mr. Blue Sky", "song": "And now, ELO."}
    spoken = V.SentenceStream(max_spoken_chars=8000).feed(M.with_pauses(M.strip_intros(t)), final=True)
    assert spoken == ["Rain later.", M.PAUSE_MARK, "Markets closed up.", "BTC flat."]
    assert M.parse_intros("[[VIDEO: x | any]]")["video_recent"] == ""
    assert len(M.silence_wav(0.5)) > 20000


def test_closing_waits_for_his_answer():
    m = M.Morning()
    m.start()
    tags = {"video_query": "v", "song_query": "s", "video": "Here's v.", "song": "And s."}
    assert m.closing_for(tags, asked=True) == {}          # asked a question: nothing plays yet
    assert m.closing_for({}, asked=False) == tags          # the briefing wraps up: the kept picks play now
    assert m.closing_for({}, asked=False) == {}            # only once
    new = {"song_query": "s2"}
    m.closing_for(tags, asked=True)
    assert m.closing_for(new, asked=False) == new          # fresh picks win
