"""'Morning / Afternoon / Evening, Jarvis': the unscripted briefing, shaped by the time of day.

Nothing here is a script. Core greets him instantly ("Good morning / afternoon / evening, sir.", by the clock), gathers
the real data in parallel (weather, weekday trains: into Manhattan in the morning, home to New Rochelle later, both
calendars incl. yesterday and tomorrow, what needs his attention, markets, BTC / ETH, the day's big games), shows the
displays, and hands everything to the model with a brief: talk it through in Jarvis's voice, decide for itself which
quips and which (if any) questions fit, then close with a topical recent YouTube video and a Guardians-of-the-Galaxy-
style song. A small log of past briefings keeps the songs, videos and questions from repeating.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutTimeout
from typing import Any, Callable
from zoneinfo import ZoneInfo

from jarvis_google import store

from .config import settings

log = logging.getLogger("jarvis.morning")

PARTS = ("morning", "afternoon", "evening")


def part_of_day(now: dt.datetime) -> str:
    """'morning' before noon, 'afternoon' until 5 pm, then 'evening' (same split as quick.py)."""
    return "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"


GREETINGS = {p: f"Good {p}, sir." for p in PARTS}
GREETING = GREETINGS["morning"]
# Said right after the greeting while the data loads (never data itself, so nothing is repeated later).
FILLERS = {
    "morning": [
        "Bear with me a moment while I pull your day together.",
        "One moment, sir. I'm just going through your calendars and the overnight news.",
        "Just reviewing what happened while you slept, sir.",
        "Give me a few seconds. The markets and your inbox both had a busy night.",
        "Compiling everything now, sir. Coffee, perhaps, in the meantime?",
        "Right. Let me see what the world has been up to.",
    ],
    "afternoon": [
        "Bear with me a moment while I pull the rest of your day together.",
        "One moment, sir. I'm just going through your calendars and the morning's news.",
        "Just catching up on what happened while you were busy, sir.",
        "Give me a few seconds. The markets and your inbox have both had a busy morning.",
        "Compiling everything now, sir. A late lunch, perhaps, in the meantime?",
        "Right. Let me see what the world has been up to since this morning.",
    ],
    "evening": [
        "Bear with me a moment while I wrap up your day.",
        "One moment, sir. I'm just going through your calendars and today's news.",
        "Just reviewing how the day went, sir.",
        "Give me a few seconds. The markets and your inbox both had a busy day.",
        "Compiling everything now, sir. A drink, perhaps, in the meantime?",
        "Right. Let me see what the world got up to today.",
    ],
}
TRAIN_KEY = "trains"
# What the HUD's loading panel lists (key -> label), in display order.
SCAN_ITEMS = [("weather", "WEATHER"), (TRAIN_KEY, "TRAINS"), ("calendar", "CALENDARS"),
              ("needs_attention", "INBOX"), ("markets", "MARKETS"), ("crypto", "CRYPTO"), ("sports", "SPORTS")]
SONG_INTROS = {"morning": "And here's something to start your day, sir.",
               "afternoon": "And here's something to carry you through the afternoon, sir.",
               "evening": "And here's something to unwind with, sir."}
SONG_INTRO = SONG_INTROS["morning"]
NEW_ROCHELLE = "New Rochelle Station, New Rochelle, NY"
GRAND_CENTRAL = "Grand Central Terminal, New York, NY"


def train_leg(part: str) -> tuple[str, str]:
    """(origin, destination): into Manhattan in the morning, home to New Rochelle in the afternoon / evening."""
    return (NEW_ROCHELLE, GRAND_CENTRAL) if part == "morning" else (GRAND_CENTRAL, NEW_ROCHELLE)
_TAG = re.compile(r"\[\[\s*(VIDEO|SONG)(_INTRO)?\s*:\s*(.*?)\s*\]\]", re.I | re.S)
PAUSE_MARK = "PAUSEMARK."      # a paragraph break in the briefing -> a beat of silence between sections
PAUSE_S = 0.75
_PARA = re.compile(r"\n[ \t]*\n+")


def parse_intros(text: str) -> dict:
    """The model's closing tags -> {"video": intro, "song": intro, "video_query", "video_recent", "song_query"}."""
    out: dict[str, str] = {}
    for m in _TAG.finditer(text or ""):
        kind, intro, val = m.group(1).lower(), bool(m.group(2)), m.group(3).strip().strip('"')
        if not val:
            continue
        if intro:
            out[kind] = val
        elif kind == "video":
            q, _, rec = val.partition("|")
            out["video_query"] = q.strip().strip('"')
            rec = rec.strip().lower()
            out["video_recent"] = rec if rec in ("day", "week", "month") else ("" if rec in ("any", "all") else "week")
        else:
            out["song_query"] = val
    return out


def with_pauses(text: str) -> str:
    """Mark section breaks (blank lines) so the speech pipeline leaves a short silence there."""
    return _PARA.sub(f"\n{PAUSE_MARK} ", (text or "").strip())


def silence_wav(seconds: float = PAUSE_S, rate: int = 24000) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def fetch_closing(tags: dict) -> list[dict]:
    """Search the video and the song the model picked, as HUD cards (held until the words have played)."""
    from jarvis_google import media as MD
    from . import visuals as V
    cards: list[dict] = []
    if tags.get("video_query"):
        try:
            r = MD.youtube_video(tags["video_query"], record=False, recent=tags.get("video_recent", ""))
            if not r.get("error"):
                cards += V.cards_from_feed({"seq": 0, "ts": time.time(), "tool": "youtube_video", "account": None,
                                            "args": {"query": tags["video_query"]}, "result": r})
                note_played(video=((r.get("results") or [{}])[0]).get("title") or tags["video_query"])
        except Exception as e:
            log.warning("morning video %r: %s", tags["video_query"], e)
    if tags.get("song_query"):
        try:
            r = MD.music_play(tags["song_query"], "song", record=False)
            if not r.get("error"):
                r = {**r, "query": tags["song_query"], "started_at": time.time()}
                cards += V.cards_from_feed({"seq": 0, "ts": time.time(), "tool": "music_play", "account": None,
                                            "args": {"query": tags["song_query"], "kind": "song"}, "result": r})
                note_played(song=r.get("title") or tags["song_query"])
        except Exception as e:
            log.warning("morning song %r: %s", tags["song_query"], e)
    return cards


def note_played(song: str | None = None, video: str | None = None) -> None:
    entries = _load_log()
    if not entries:
        return
    if song:
        entries[-1]["song"] = song
    if video:
        entries[-1]["video"] = video
    _save_log(entries)


def strip_intros(text: str) -> str:
    """Drop the closing tags (and anything after the first '[[', so a half-streamed tag is never spoken)."""
    return (text or "").split("[[", 1)[0].rstrip().rstrip("[").rstrip()


def video_intro(card: dict) -> str:
    q = ((card.get("data") or {}).get("query") or "").strip()
    return f"Here's a video on {q}, queued up for later." if q else "Here's something I've queued up for later, sir."


MEDIA_TOOLS = {"youtube_video", "music_play", "media_control"}  # held until the briefing's words have played
LOG_PATH = store.STATE_DIR / "morning_log.json"
GATHER_TIMEOUT_S = 14.0
ACTIVE_FOR_S = 25 * 60  # a briefing (with his answers in between) is over after this long, whatever happened

_TOD = r"(?:morning|afternoon|evening)"
_HELLO = re.compile(r"^\W*(?:(?:good|top of the)\s+)?" + _TOD + r"[\s,.!]*(?:jarvis|jarvas|jervis|travis)\W*$"
                    r"|^\W*(?:jarvis|jervis)[\s,.!]+(?:good\s+)?" + _TOD + r"\W*$", re.I)
_HELLO_LOOSE = re.compile(r"\b(?:good\s+)?" + _TOD + r"[\s,.!]*(?:jarvis|jarvas|jervis|travis)\b"
                          r"|\b(?:jarvis|jervis)[\s,.!]+(?:good\s+)?" + _TOD + r"\b", re.I)


def is_hello(text: str) -> bool:
    """'Morning, Jarvis' / 'Good afternoon, Jarvis' / 'Evening, Jarvis' on its own (typed or said while awake).
    Which briefing runs is decided by the clock, not the word he used."""
    return bool(_HELLO.search(text or ""))


def heard_hello(text: str) -> bool:
    """Looser match for speech heard around the wake word (Whisper may add or drop a word)."""
    return bool(_HELLO_LOOSE.search(text or ""))


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


# ---------------------------------------------------------------- gathering (each part fails on its own)
def _weather() -> dict:
    from jarvis_google import tools as T
    r = T.weather_lookup("", 3, "")
    cur, today = r.get("current") or {}, r.get("today") or {}
    daily = r.get("daily") or []
    hourly = [{"time": h.get("time", "")[11:16], "temp": h.get("temp"), "pop": h.get("pop"), "condition": h.get("condition")}
              for h in (r.get("hourly") or [])[:14:2]]
    return {"location": (r.get("location") or {}).get("name"), "now": {k: cur.get(k) for k in ("temp", "condition", "feels_like", "wind")},
            "today": {k: today.get(k) for k in ("hi", "lo", "condition", "pop", "precip", "wind_max", "sunset")},
            "next_hours": hourly, "tomorrow": {k: (daily[1] if len(daily) > 1 else {}).get(k) for k in ("hi", "lo", "condition", "pop")}}


def _trains(part: str = "morning") -> dict:
    from jarvis_google import routes as RT
    origin, dest = train_leg(part)
    r = RT.directions(dest, origin, "transit")
    out = []
    for rt in (r.get("routes") or [])[:3]:
        legs = [s["transit"] for s in rt.get("steps") or [] if s.get("transit")]
        if legs:
            out.append({"line": legs[0].get("line_name") or legs[0].get("line"), "departs": legs[0].get("depart"),
                        "from": legs[0].get("from"), "arrives": legs[-1].get("arrive"), "to": legs[-1].get("to"),
                        "door_to_door": rt.get("duration"), "transfers": max(0, len(legs) - 1)})
    names = {NEW_ROCHELLE: "New Rochelle", GRAND_CENTRAL: "Grand Central"}
    return {"from": names[origin], "to": names[dest], "next_trains": out}


def _events(acct: str, a: dt.datetime, b: dt.datetime) -> list[dict]:
    from jarvis_google import tools as T
    r = T.calendar_list(acct, a.isoformat(), b.isoformat(), "", 60)
    out = []
    for e in r.get("events") or []:
        if e.get("my_status") == "declined":
            continue
        att = e.get("attendees") or []
        ext = sorted({x["email"].split("@")[-1] for x in att if x.get("email") and "hadrius.com" not in x["email"]
                      and "gmail.com" not in x["email"] and "group.calendar" not in x["email"]})
        out.append({"title": e.get("summary") or "(untitled)", "start": e.get("start"), "end": e.get("end"),
                    "all_day": e.get("all_day"), "location": e.get("location") or None,
                    "attendees": len(att), "external_domains": ext or None,
                    "my_status": e.get("my_status"), "notes": (e.get("description") or "")[:240] or None})
    return out


def _calendar() -> dict:
    now = dt.datetime.now(_tz())
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    win = {"yesterday": (day0 - dt.timedelta(days=1), day0), "today": (day0, day0 + dt.timedelta(days=1)),
           "tomorrow": (day0 + dt.timedelta(days=1), day0 + dt.timedelta(days=2))}
    out: dict[str, Any] = {}
    for acct in ("personal", "work"):
        try:
            evs = _events(acct, win["yesterday"][0], win["tomorrow"][1])
        except Exception as e:
            out[acct] = {"error": str(e)[:120]}
            continue
        buckets: dict[str, list] = {k: [] for k in win}
        for e in evs:
            try:
                s = dt.datetime.fromisoformat(e["start"]) if "T" in (e["start"] or "") else \
                    dt.datetime.combine(dt.date.fromisoformat(e["start"]), dt.time(), _tz())
                s = s.astimezone(_tz()) if s.tzinfo else s.replace(tzinfo=_tz())
            except Exception:
                continue
            for k, (a, b) in win.items():
                if a <= s < b:
                    buckets[k].append(e)
        out[acct] = buckets
    return out


def _attention() -> dict:
    from .briefing import briefing
    data = briefing.data or {}
    todos = [{"title": t.get("title"), "detail": (t.get("detail") or "")[:200], "urgency": t.get("urgency"),
              "due": t.get("due"), "from": ((t.get("messages") or [{}])[0]).get("from_name"),
              "account": ((t.get("messages") or [{}])[0]).get("email")} for t in (data.get("todos") or [])[:8]]
    prio = [{"title": p.get("title"), "detail": (p.get("detail") or "")[:200],
             "from": ((p.get("messages") or [{}])[0]).get("from_name")} for p in (data.get("priority") or [])[:5]]
    age = round((time.time() - data["generated_at"]) / 3600, 1) if data.get("generated_at") else None
    return {"email_todos": todos, "priority_emails": prio, "triaged_hours_ago": age,
            "note": "Already triaged by the inbox briefing; urgency 'high'/'urgent' or a due date of today means "
                    "it needs him now. Anything else is not urgent."}


def _markets() -> dict:
    from jarvis_google import markets as MK
    r = MK.market_overview("")
    idx = [{"name": i.get("label"), "price": i.get("price"), "change_pct": i.get("change_pct")} for i in r.get("indices") or []]
    secs = sorted(r.get("sectors") or [], key=lambda s: s.get("change_pct") or 0)
    mv = r.get("movers") or {}
    out = {"market_state": r.get("market_state"), "indices": idx,
           "best_sector": secs[-1] if secs else None, "worst_sector": secs[0] if secs else None,
           "top_gainers": [{"name": g.get("name"), "change_pct": g.get("change_pct")} for g in (mv.get("gainers") or [])[:3]],
           "top_losers": [{"name": g.get("name"), "change_pct": g.get("change_pct")} for g in (mv.get("losers") or [])[:3]]}
    try:
        q = MK.stock_quote("SPY", "1M")
        out["spy_1m_pct"] = q.get("period_pct")
        out["headlines"] = [n.get("title") for n in (q.get("news") or [])[:5] if n.get("title")]
    except Exception as e:
        log.info("morning spy: %s", e)
    return out


def _crypto() -> dict:
    from jarvis_google import markets as MK
    out = {}
    for coin, sym in (("bitcoin", "BTC"), ("ethereum", "ETH")):
        try:
            c = MK.crypto_quote(coin, "1M")
            if not isinstance(c, dict) or c.get("error") or not c.get("price"):
                raise RuntimeError((c or {}).get("error") if isinstance(c, dict) else "no data")
            ch = c.get("changes") or {}
            out[sym] = {"price": c.get("price"), "change_24h_pct": ch.get("24h"),
                        "change_7d_pct": ch.get("7d"), "change_30d_pct": ch.get("30d")}
        except Exception as e:  # CoinGecko rate-limits: fall back to Yahoo's quote
            log.info("morning %s via CoinGecko failed (%s); using Yahoo", sym, str(e)[:80])
            try:
                q = MK.stock_quote(f"{sym}-USD", "1M")
                out[sym] = {"price": q.get("price"), "change_24h_pct": q.get("change_pct"),
                            "change_30d_pct": q.get("period_pct")}
            except Exception as e2:
                out[sym] = {"error": str(e2)[:100]}
    return out


def _sports() -> dict:
    from jarvis_google import sports as SP
    games = []
    for lg in ("nfl", "mlb", "nba", "nhl", "soccer"):
        try:
            sb = SP.scoreboard(lg, dt.date.today())
        except Exception:
            continue
        for g in sb.get("games", []):
            if g.get("broadcast") and g["status"]["state"] in ("pre", "in"):
                games.append({"league": lg.upper(), "game": g.get("name"), "when": g["status"].get("short"),
                              "tv": (g.get("broadcast") or "").split(",")[0]})
    out: dict[str, Any] = {"big_games_today": games[:6]}
    today = next((g for g in games if "bears" in (g.get("game") or "").lower()), None)
    if today:
        out["bears_play_TODAY"] = today
    try:
        r = SP.sports_game("nfl", "Bears", "", "", "")
        if isinstance(r, dict) and r.get("teams") and (r.get("status") or {}).get("state") == "post":
            out["bears_previous_game_result"] = {
                "date": r.get("date"), "teams": [{"name": t.get("short") or t.get("name"), "score": t.get("score")}
                                                 for t in r["teams"]]}
    except Exception:
        pass
    return out


def _cards(fn, *a) -> list[dict]:
    """Run a tool with the feed captured and turn what it would have shown into HUD cards."""
    from . import visuals as V
    with store.capture() as items:
        fn(*a)
    out = []
    for it in items:
        out.extend(V.cards_from_feed(it))
    return out


def gather(weekday: bool, progress: Callable[..., None] | None = None, part: str = "morning") -> tuple[dict, list[dict]]:
    """Today's data (each section independently; a failure becomes {'error': ...}) + displays for the HUD.
    progress("item", name, ok) as each section lands; progress("cards", [cards]) as each display is ready."""
    note = progress or (lambda *a: None)
    from jarvis_google import markets as MK
    from jarvis_google import routes as RT
    from jarvis_google import tools as T
    parts = {"weather": _weather, "calendar": _calendar, "needs_attention": _attention, "markets": _markets,
             "crypto": _crypto, "sports": _sports}
    if weekday:
        parts[TRAIN_KEY] = lambda: _trains(part)
    shows = [(T.weather_lookup, "", 5, ""), (MK.crypto_quote, "bitcoin", "1M")]
    if weekday:
        origin, dest = train_leg(part)
        shows.insert(1, (RT.directions, dest, origin, "transit"))
    data: dict[str, Any] = {}
    cards: list[dict] = []
    with ThreadPoolExecutor(len(parts) + len(shows)) as ex:
        futs = {ex.submit(fn): name for name, fn in parts.items()}
        cfuts = {ex.submit(_cards, *s): i for i, s in enumerate(shows)}
        try:
            for f in as_completed(list(futs) + list(cfuts), timeout=GATHER_TIMEOUT_S):
                if f in cfuts:
                    if not f.exception() and f.result():
                        cards += f.result()
                        note("cards", f.result())
                    continue
                name = futs[f]
                try:
                    data[name] = f.result()
                    ok = not (isinstance(data[name], dict) and data[name].get("error"))
                except Exception as e:
                    log.warning("morning %s failed: %s", name, e)
                    data[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
                    ok = False
                note("item", name, ok)
        except FutTimeout:
            pass
        for name in parts:
            if name not in data:
                data[name] = {"error": "timed out"}
                note("item", name, False)
    return data, cards


# ---------------------------------------------------------------- memory of past mornings
def _load_log() -> list[dict]:
    try:
        return json.loads(LOG_PATH.read_text())
    except Exception:
        return []


def _save_log(entries: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(entries[-30:], default=str))


def finalize_previous(entries: list[dict]) -> list[dict]:
    """Fill in what the earlier mornings actually played (song / video), from the tool feed."""
    for e in entries:
        if e.get("song") is not None or e.get("feed_head") is None:
            continue
        try:
            rows = store.feed_since(int(e["feed_head"]))
        except Exception:
            continue
        rows = [r for r in rows if r["ts"] <= e["ts"] + ACTIVE_FOR_S]
        songs = [r["result"].get("title") or r["args"].get("query") for r in rows if r["tool"] == "music_play"]
        vids = [((r["result"].get("results") or [{}])[0]).get("title") or r["args"].get("query")
                for r in rows if r["tool"] == "youtube_video"]
        e["song"], e["video"] = (songs[0] if songs else ""), (vids[0] if vids else "")
    return entries


def history_for_prompt(entries: list[dict]) -> list[dict]:
    return [{"date": e.get("date"), "song": e.get("song") or None, "video": e.get("video") or None}
            for e in entries[-10:] if e.get("song") or e.get("video")]


# ---------------------------------------------------------------- the brief for the model
_MOODS = ["brisk and efficient, a touch dry", "warm and a little chatty", "extra dry, deadpan wit",
          "upbeat, like a man who's had his coffee", "understated, quietly amused"]


# Everything in the brief that depends on the time of day.
_TOD_BRIEF = {
    "morning": {
        "said": "Morning, Jarvis", "tag": "MORNING BRIEFING", "go": "go straight into the day",
        "trains": "the next trains from New Rochelle into Grand Central (just the useful one or two)",
        "weather": "the weather (what to wear / umbrella if it matters)",
        "calendar": "a summary of today on BOTH calendars (personal and work), merged",
        "markets": "the markets (major indices and anything notable)",
        "look_back": "If YESTERDAY had a notable PERSONAL event (a date, dinner, party, trip, concert, game, family "
                     "visit, appointment), you may casually ask how it went.",
        "look_ahead": "Tomorrow's notable events can be a light heads-up.",
        "prep": "If a work meeting today looks genuinely important",
        "song": "a song to start the day", "song_eg": "And here's something to start your day, sir.",
        "recent": "RECENT MORNINGS",
    },
    "afternoon": {
        "said": "Good afternoon, Jarvis", "tag": "AFTERNOON BRIEFING", "go": "go straight into the rest of the day",
        "trains": "the next trains home from Grand Central to New Rochelle (just the useful one or two)",
        "weather": "the weather for the rest of the afternoon and this evening (umbrella or a jacket if it matters)",
        "calendar": "what's LEFT of today on BOTH calendars (personal and work), merged, skipping anything already over",
        "markets": "the markets so far today (major indices mid-session and anything notable; if they're closed, how "
                   "they finished)",
        "look_back": "If something notable and PERSONAL happened earlier TODAY or YESTERDAY (a date, lunch, party, "
                     "trip, game, family visit, appointment), you may casually ask how it went.",
        "look_ahead": "This evening's and tomorrow's notable events can be a light heads-up.",
        "prep": "If a work meeting still to come today looks genuinely important",
        "song": "a song for the afternoon", "song_eg": "And here's something to carry you through the afternoon, sir.",
        "recent": "RECENT BRIEFINGS",
    },
    "evening": {
        "said": "Good evening, Jarvis", "tag": "EVENING BRIEFING", "go": "go straight into the wrap-up",
        "trains": "the next trains home from Grand Central to New Rochelle, only if it's still a sensible hour to be "
                  "commuting (otherwise skip them)",
        "weather": "the weather tonight and tomorrow (what to expect in the morning)",
        "calendar": "anything still to come tonight on BOTH calendars (personal and work), then a preview of "
                    "TOMORROW's calendar, merged",
        "markets": "how the markets closed today (major indices and anything notable)",
        "look_back": "If something notable and PERSONAL happened TODAY (a date, dinner, party, trip, game, family "
                     "visit, appointment), you may casually ask how it went.",
        "look_ahead": "Tomorrow gets more weight than usual: anything early or important deserves a clear heads-up.",
        "prep": "If a work meeting tomorrow looks genuinely important",
        "song": "a song to unwind with", "song_eg": "And here's something to unwind with, sir.",
        "recent": "RECENT BRIEFINGS",
    },
}


def build_prompt(data: dict, history: list[dict], now: dt.datetime, part: str | None = None) -> str:
    part = part or part_of_day(now)
    b = _TOD_BRIEF[part]
    seed = int(hashlib.sha256(now.strftime("%Y-%m-%d").encode()).hexdigest(), 16)
    mood = _MOODS[(seed + PARTS.index(part)) % len(_MOODS)]
    weekday = now.weekday() < 5
    payload = json.dumps(data, default=str, separators=(",", ":"))
    return f"""[{b["tag"]}] Stephen just said "{b["said"]}". You ALREADY greeted him out loud with "{GREETINGS[part]}" Do not greet him again: {b["go"]}.

It is {now.strftime("%A, %B")} {now.day}, {now.strftime("%Y")}, {now.strftime("%I:%M %p").lstrip("0")} in New Rochelle, NY ({"a weekday" if weekday else "the weekend"}), so this is the {part} briefing. Tone: {mood}.

DATA (fetched seconds ago from his real accounts and live feeds; the HUD is already showing weather{", trains" if weekday else ""} and Bitcoin):
{payload}

{b["recent"]} (do not repeat these songs or videos): {json.dumps(history, default=str)}

HOW TO RUN IT
- This is spoken aloud. Plain sentences, no lists, no markdown, no emojis. Put each part (weather, trains, calendar, inbox, markets, crypto, ...) in its own short paragraph with a blank line between them: Jarvis pauses briefly at each break.
- Write the whole briefing straight away. Do NOT call any tools for the briefing (everything is in DATA; the video and song are handled by tags, below), so he isn't kept waiting. Round numbers ("about 64", "up 1.2 percent", "BTC at 82.8 thousand"). Keep each part to a sentence or two; the whole thing should feel like a quick, natural chat, not a report.
- It's the {part}: everything you say should fit this time of day (no "start your day" in the evening, no "overnight" in the afternoon).
- Cover, in a natural order: {b["weather"]}; {b["trains"] if weekday else "no train info, it's the weekend"}; {b["calendar"]}, highlighting what matters and skipping noise like optional office hours; anything that needs his immediate attention (if nothing does, say so in a few words); {b["markets"]}; BTC and ETH.
- Be aware of the surrounding days. {b["look_back"]} Ignore routine work events. Most days there is nothing to mention; never invent anything. {b["look_ahead"]}
- {b["prep"]} (external attendees / client domains, interviews, exec or board meetings, demos, presentations, reviews), you may offer to help him prep.
- Quips: one to three dry, British, Jarvis-style asides tied to the actual data (the weather, a market move, crypto, his calendar). Never forced, never the same joke twice in a row.
- Questions: decide for yourself. Ask zero, one, or at most two questions over the whole briefing, only where useful or natural (how a recent event went, offer to prep a meeting, offer to draft a reply to an urgent email, a clarifying question). Some days ask none. When you ask, END your message on the question and stop there (no closing tags in that message); he will answer. Then react briefly (a quip if it fits), help if he said yes (or say you'll get to it right after the briefing), and carry on with the parts you haven't covered yet. If he didn't answer, just carry on.
- Closing (always last, after everything else): 1) pick ONE YouTube video relevant to today specifically: a big event today (a final, a launch, an election), a strong market or crypto move, a major headline, or failing that his interests (cars, especially his Mercedes CL600 and V12s; SpaceX and Starship; crypto and markets; AI and tech; the Chicago Bears). 2) Pick {b["song"]} in the spirit of the Guardians of the Galaxy Awesome Mix (60s-80s feel-good pop, rock, soul, funk; suited to the {part}; not one of the soundtrack's own songs every time, and not one from {b["recent"]}). Do NOT call youtube_video or music_play, and do NOT introduce them in your spoken text. Instead, end that final message with these four tags, each on its own line, as the very last thing:
  [[VIDEO: a specific YouTube search query | week]]   (use "| day" for breaking news, "| any" for something evergreen)
  [[VIDEO_INTRO: one short spoken sentence introducing it and why it fits today, e.g. "Here's a video on the Bears and Packers rivalry ahead of Sunday, for later."]]
  [[SONG: Artist - Song title]]
  [[SONG_INTRO: one short line before the song, e.g. "{b["song_eg"]}" (vary it; you may name the song)]]
  When you've finished speaking, Jarvis says the video line, shows the video (paused for later), says the song line and starts the song.
- Accuracy matters more than flourish: read dates and labels in DATA carefully (e.g. a game "TODAY" vs a previous result) and never state anything DATA doesn't support.
- Use the DATA above; don't re-fetch it. Only call tools if he asks for something or you're helping him with a task. If a section has an "error", skip it quietly (or mention it in a few words if it matters).
"""


# ---------------------------------------------------------------- runner
class Morning:
    """Per-session state: is a briefing (morning / afternoon / evening) in progress (so a question gets a longer listen, and silence after
    a question continues the briefing)."""

    def __init__(self) -> None:
        self.active_until = 0.0
        self._greet_audio: dict[str, bytes] = {}
        self._fillers: dict[str, bytes] = {}
        self.part = "morning"

    @property
    def active(self) -> bool:
        return time.time() < self.active_until

    def finish(self) -> None:
        self.active_until = 0.0

    async def filler_audio(self, voice, part: str | None = None) -> tuple[str, bytes | None]:
        line = random.choice(FILLERS[part or self.part])
        if line not in self._fillers:
            try:
                self._fillers[line] = await voice.tts(line)
            except Exception as e:
                log.warning("filler tts: %s", e)
                return line, None
        return line, self._fillers[line]

    @property
    def greeting(self) -> str:
        return GREETINGS[self.part]

    @property
    def song_intro(self) -> str:
        return SONG_INTROS[self.part]

    async def greeting_audio(self, voice, part: str | None = None) -> bytes | None:
        part = part or self.part
        if part not in self._greet_audio:
            try:
                self._greet_audio[part] = await voice.tts(GREETINGS[part])
            except Exception as e:
                log.warning("greeting tts: %s", e)
                return None
        return self._greet_audio[part]

    def start(self, part: str = "morning") -> None:
        self.part = part if part in PARTS else "morning"
        self.active_until = time.time() + ACTIVE_FOR_S
        self.deferred = {}

    deferred: dict = {}  # closing tags written in a message that ended on a question: used when the briefing ends

    def closing_for(self, tags: dict, asked: bool) -> dict:
        """The video/song to play after this message: none while waiting on his answer (kept for later), else this
        message's picks or the ones kept from earlier."""
        has = bool(tags.get("video_query") or tags.get("song_query"))
        if asked:
            if has:
                self.deferred = tags
            return {}
        if has:
            self.deferred = {}
            return tags
        kept, self.deferred = self.deferred, {}
        return kept


def begin_log(now: dt.datetime) -> None:
    entries = finalize_previous(_load_log())
    entries.append({"date": now.strftime("%Y-%m-%d"), "ts": time.time(), "feed_head": store.feed_head(),
                    "song": None, "video": None})
    _save_log(entries)


def recent_history() -> list[dict]:
    entries = finalize_previous(_load_log())
    _save_log(entries)
    return history_for_prompt(entries)
