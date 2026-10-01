"""The showcase ("Hey Jarvis, what do you have for me today?" / ⌘⇧D) and the narrated support-model tour.

The daily briefing is a preset sequence of beats. Each beat pulls REAL data: weather, email, Slack, Pylon, the
calendar, markets and headlines, sports, the train into the city, a suggested video, then music. Everything is fetched up front in parallel with the feed captured (store.capture), so
nothing appears until its cue. Each narration line is written from the data it describes, synthesized ahead of time,
and played as its display lands. It runs as the session's turn task, so Esc or a new request cancels it like any
other turn.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo

from jarvis_google import store
from . import visuals as V
from .config import settings
from .voice import b64

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session

log = logging.getLogger("jarvis.showcase")

STATE_PATH = store.STATE_DIR / "showcase.json"

_TRIGGER = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay|good morning|morning)[\s,]+)?(?:jarvis[\s,.!]+)?(?:so[\s,]+)?"
    r"(?:what (?:do|have) you (?:got|have) (?:for me )?today|what do you have for me(?: today)?|what have you got for me(?: today)?"
    r"|(?:tell me about|brief me on|walk me through|run me through) (?:my|the) day|how(?:'s| is| does) my day (?:look(?:ing)?|shaping up)"
    r"|(?:give me|run|start) (?:my|the) (?:daily |morning )?briefing|(?:my |the )?(?:daily|morning) briefing"
    r"|(?:run|start|show me|give me|do) (?:the |a |your )?(?:demo|showcase))\b",
    re.I)
_TOUR = re.compile(
    r"\b(explain|walk me through|walk through|run me through|how does|how do(?:es)?|tell me about|give me (?:an |the )?"
    r"overview|overview of|break down|show me|describe|what is)\b.*\b((?:3|three)[- ]?tier(?:ed)?|support (?:model|system|"
    r"operation|stack|setup|pipeline|architecture|workflow)|tiered support)\b", re.I)
_SPECIFIC = re.compile(r"\b(jamie|hadrian|tier ?(?:1|2|3|one|two|three)\b(?![- ]?tier)|draft[- ]reply|feature request|"
                       r"loop guard|takeover|sybill|budget|payload|on hold|webhook|linear)\b", re.I)


POWER_ON_S = 4.0  # length of the HUD's power-on sequence; the briefing gathers its data meanwhile
_MORNING_RE = re.compile(r"^\W*(?:good\s+)?morning[\s,.!]*jarvis\W*$|^\W*jarvis[\s,.!]+(?:good\s+)?morning\W*$", re.I)
_SLEEP_RE = re.compile(r"^\W*(?:(?:ok|okay|alright|right|thanks)[\s,]+)?(?:jarvis[\s,]+)?(?:go to sleep|goodnight|good night|"
                       r"sleep mode|power down|that'?s all for (?:now|tonight))(?:[\s,.!]+jarvis)?\W*$", re.I)


def is_morning(text: str) -> bool:
    return bool(_MORNING_RE.search(text or ""))


def is_sleep_request(text: str) -> bool:
    return bool(_SLEEP_RE.search(text or ""))


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.search(text or ""))


def is_tour_request(text: str) -> bool:
    """Broad 'explain the 3-tier support model' asks get the instant narrated tour; specific questions go to the model."""
    t = text or ""
    return bool(_TOUR.search(t)) and not _SPECIFIC.search(t.replace("3 tier", "").replace("three tier", ""))


# ---------------------------------------------------------------- small helpers
def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return max(1.5, len(data) / 48000)


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(s))


def _grab(fn: Callable, *a, **kw) -> tuple[Any, list[dict]]:
    """Run a tool with the feed captured: (result, the feed items it would have published)."""
    with store.capture() as items:
        r = fn(*a, **kw)
    return r, list(items)


def _pct(x: float | None) -> str:
    if x is None:
        return "flat"
    if abs(x) < 0.05:
        return "flat"
    return f"{'up' if x > 0 else 'down'} {abs(x):.1f}%"


def _units(t: str) -> str:
    """'24 min' / '1 hr 5 min' / '17 mi' -> words the voice reads naturally."""
    t = re.sub(r"\b(\d+)\s*hrs?\b", lambda m: m.group(1) + (" hour" if m.group(1) == "1" else " hours"), t or "")
    t = re.sub(r"\b(\d+)\s*mins?\b", lambda m: m.group(1) + (" minute" if m.group(1) == "1" else " minutes"), t)
    return re.sub(r"\b([\d.]+)\s*mi\b", lambda m: m.group(1) + (" mile" if m.group(1) == "1" else " miles"), t)


def _co(name: str) -> str:
    return re.sub(r",?\s+(Inc\.?|Corporation|Corp\.?|Incorporated|Company|Co\.|plc|Ltd\.?|Holdings?)$", "", name or "").strip()


def _clock(iso: str) -> str:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_tz())
        return d.strftime("%-I:%M %p").replace(":00 ", " ")
    except Exception:
        return ""


def chunk_text(text: str, limit: int = 260) -> list[str]:
    """Sentence groups of <= limit chars: short TTS requests (fast) that still sound natural."""
    sents = V.split_sentences(text)
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- beats
@dataclass
class Beat:
    label: str
    line: str
    cards: list[dict] = field(default_factory=list)
    expand: str | None = None          # card id to open in the workbench (None = leave it, "" = close)
    after_s: float = 0.6               # pause after the line before the next beat
    ui: list[dict] = field(default_factory=list)
    audio: "asyncio.Future[bytes | None] | None" = None


def _cards(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        out.extend(V.cards_from_feed(it))
    return out


def _join(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _first(name: str) -> str:
    """A sender as you'd say it: a person's first name ("Jordan Talbot" -> "Jordan"), a company as-is ("Vercel")."""
    n = re.sub(r"\s*<.*?>|\s*\(.*?\)", "", name or "").strip().strip('"')
    if not n or "@" in n:
        return ""
    words = n.split()
    person = 2 <= len(words) <= 3 and all(w[:1].isupper() and w.replace("-", "").replace("'", "").isalpha() for w in words) \
        and not re.search(r"team|support|notification|billing|inc|llc|group|news", n, re.I)
    return words[0] if person else n


def _sentence(t: str) -> str:
    t = (t or "").strip().rstrip(".")
    t = re.sub(r"\s*(?:on|for)?\s*(?:account|acct)\.?\s*#?\d{6,}", "", t, flags=re.I)  # long ids aren't speech
    t = re.sub(r"\s*#?\d{8,}", "", t)
    return t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t


def _due(d: str) -> str:
    """'October 3, 2026' -> 'Saturday' (this week) / 'October 3rd'."""
    for fmt in ("%B %d, %Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            day = dt.datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
        delta = (day - dt.datetime.now(_tz()).date()).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "tomorrow"
        if 1 < delta < 7:
            return day.strftime("%A")
        return day.strftime("%B ") + str(day.day)
    return d


def _weather() -> Beat | None:
    from jarvis_google import tools as T
    r, items = _grab(T.weather_lookup, "", 5, "")
    if not isinstance(r, dict) or r.get("error"):
        return None
    cur, today, loc = r.get("current") or {}, r.get("today") or {}, r.get("location") or {}
    cond = str(cur.get("condition", "")).lower()
    line = f"It's {round(cur.get('temp', 0))} and {cond} in {loc.get('name', 'town')} right now, with a high of {round(today.get('hi', 0))}"
    pop = today.get("pop") or 0
    line += f" and a {pop} percent chance of rain, so you may want an umbrella." if pop >= 40 else (
        f" and a slight chance of showers." if pop >= 20 else ". A dry one, by the look of it.")
    return Beat("WEATHER", line, _cards(items))


_NOISE_SENDERS = re.compile(r"no-?reply|notifications?@|mailer|newsletter|digest|marketing|updates@|news@|info@", re.I)


def _email() -> Beat | None:
    """The briefing's to-dos and priority mail (already triaged by the inbox briefing), else unread non-bulk mail."""
    from .briefing import briefing
    from jarvis_google import tools as T
    data = briefing.data or {}
    todos, prio = data.get("todos") or [], data.get("priority") or []
    cards, items = [], []
    for acct in (() if (todos or prio) else ("work", "personal")):
        try:
            r, it = _grab(T.gmail_search, acct, "is:unread in:inbox -category:promotions -category:social -category:updates "
                          "-category:forums newer_than:2d", 8)
            items += it
        except Exception as e:
            log.info("showcase email %s: %s", acct, e)
    msgs = [m for x in items for m in (x["result"].get("messages") or [])
            if not _NOISE_SENDERS.search(m.get("from_email") or m.get("from") or "")]
    if todos or prio:  # show what he's being told about: the triaged to-dos and priority mail, not raw unread
        rows = []
        for t in (todos + prio)[:8]:
            m0 = (t.get("messages") or [{}])[0]
            sender = m0.get("from_name") or ""
            meta = " · ".join(x for x in ((t.get("urgency") or "").upper() if t in todos else "PRIORITY",
                                          f"due {t['due']}" if t.get("due") else "", m0.get("email", "")) if x)
            rows.append({"title": t.get("title", ""), "subtitle": f"{sender}: {t.get('detail', '')}".strip(": "),
                         "meta": meta})
        cards = [V.card("visual.list", "Inbox · needs your attention", {"type": "list", "items": rows})]
    else:
        cards = [c for c in _cards(items) if (c.get("data") or {}).get("messages")]
    bits = []
    urgent = [t for t in todos if (t.get("urgency") or "") in ("high", "urgent")]
    if todos:
        lead = (urgent or todos)[0]
        n = len(todos)
        bits.append(f"Starting with your inbox: {n} thing{'s' if n != 1 else ''} need{'' if n != 1 else 's'} your attention."
                    f" The most pressing: {_sentence(lead['title'])}" + (f", by {_due(lead['due'])}" if lead.get("due") else "") + ".")
        rest = [t for t in todos if t is not lead][:1]
        if rest:
            bits.append(f"After that, {_sentence(rest[0]['title'])}.")
    if prio:
        p0 = prio[0]
        who = _first(((p0.get("messages") or [{}])[0]).get("from_name") or "")
        bits.append(f"{'There is also' if todos else 'In your inbox, there is'} a priority email"
                    + (f" from {who}" if who else "") + (f" about {p0.get('title', '').rstrip('.')}" if p0.get("title") else "") + ".")
    if not bits:
        if msgs:
            senders = _join(list(dict.fromkeys(_first(m.get("from_name") or "") for m in msgs if _first(m.get("from_name") or "")))[:3])
            bits.append(f"Starting with your inbox: {len(msgs)} new email{'s' if len(msgs) != 1 else ''} worth a look"
                        + (f", from {senders}" if senders else "") + ".")
        else:
            bits.append("Your inbox is quiet: nothing new that needs you.")
    return Beat("EMAIL", " ".join(bits), cards[:2])


def _calendar() -> Beat | None:
    from jarvis_google import tools as T
    tz = _tz()
    now = dt.datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = start + dt.timedelta(days=1)
    events, items = [], []
    for acct in ("work", "personal"):
        try:
            r, it = _grab(T.calendar_list, acct, start.isoformat(), end_day.isoformat(), "", 50)
        except Exception as e:
            log.info("showcase calendar %s: %s", acct, e)
            continue
        events += [e for e in r.get("events", []) if e.get("my_status") != "declined" and not e.get("all_day")]
        items += it
    upcoming = sorted([e for e in events if (e.get("end") or "") > now.isoformat()], key=lambda e: e.get("start") or "")
    cards = [c for c in _cards(items) if (c.get("data") or {}).get("events")] or _cards(items)[:1]
    if not events:
        line = "Your calendar is clear today."
    elif not upcoming:
        line = "Your meetings are all behind you for today."
    else:
        nxt = upcoming[0]
        if len(upcoming) == 1:
            line = (f"Your calendar is light today: just {nxt.get('summary', 'one meeting')} at {_clock(nxt.get('start', ''))}.")
        else:
            last = upcoming[-1]
            line = (f"On the calendar, you have {len(upcoming)} meetings today, starting with {nxt.get('summary', 'a meeting')} "
                    f"at {_clock(nxt.get('start', ''))}, and you're done after {last.get('summary', 'your last one')} at "
                    f"{_clock(last.get('start', ''))}.")
    return Beat("CALENDAR", line, cards)


def _slack() -> Beat | None:
    from jarvis_google import slack as SL
    if not SL.available():
        return None
    d = SL.gather()
    convs = [c for c in d.get("conversations", []) if not c.get("bot")]
    mentions, posts = d.get("mentions", []), d.get("channels", [])
    card = V.card("slack", "Slack · Hadrius", d)
    if convs:
        who = _join([c.get("title", "") for c in convs[:3]])
        line = f"On Slack, {who} {'have' if len(convs) > 1 else 'has'} written to you"
        if mentions:
            line += f", and you were mentioned {len(mentions)} time{'s' if len(mentions) != 1 else ''}"
        line += "."
    elif mentions:
        m = next((x for x in mentions if not x.get("bot")), None)
        if m:
            line = (f"On Slack, {m.get('user', 'a colleague')} mentioned you in {m.get('channel', 'a channel')}"
                    + (f", plus {len(mentions) - 1} other mention{'s' if len(mentions) > 2 else ''}" if len(mentions) > 1 else "") + ".")
        else:
            line = (f"Slack is quiet: no messages from people, just {len(mentions)} automated "
                    f"notification{'s' if len(mentions) != 1 else ''} in {mentions[0].get('channel', 'a channel')}.")
    else:
        line = "Slack is quiet: no messages waiting for you."
    return Beat("SLACK", line, [card])


def _pylon() -> Beat | None:
    from jarvis_google import pylon as P
    r, items = _grab(P.pylon_tickets, "", True, ["waiting_on_you", "new"], 12)
    n = r.get("total_matched", 0)
    if not n:
        return Beat("PYLON", "And in Pylon, nothing is waiting on you.", _cards(items))
    tk = r.get("tickets") or []
    acct = next(((t.get("account") or {}).get("name") for t in tk if (t.get("account") or {}).get("name")), "")
    line = f"In Pylon, {n} ticket{'s are' if n != 1 else ' is'} waiting on you"
    line += f", the most recent from {acct}." if acct else "."
    return Beat("PYLON", line, _cards(items))


def _markets() -> Beat | None:
    from jarvis_google import markets as MK
    r, items = _grab(MK.market_overview, "")
    idx = {i.get("label"): i for i in r.get("indices") or []}
    sp, nq = idx.get("S&P 500") or {}, idx.get("Nasdaq") or idx.get("NASDAQ") or idx.get("Nasdaq Composite") or {}
    state = (r.get("market_state") or "").lower()
    when = "" if state in ("regular", "open") else " at the last close"
    line = f"In the markets, the S&P 500 is {_pct(sp.get('change_pct'))}{when}"
    if nq:
        line += f" and the Nasdaq {_pct(nq.get('change_pct'))}"
    line += "."
    secs = sorted(r.get("sectors") or [], key=lambda s: s.get("change_pct") or 0)
    if len(secs) >= 2:
        line += f" {secs[-1].get('label')} led, while {secs[0].get('label').lower()} lagged."
    g = ((r.get("movers") or {}).get("gainers") or [{}])[0]
    if g.get("name") and (g.get("change_pct") or 0) >= 8:
        line += f" The standout is {_co(g['name'])}, {_pct(g.get('change_pct'))}."
    cards = _cards(items)
    try:  # the day's headlines (market news): the S&P 500 card carries Yahoo's news feed
        q, qi = _grab(MK.stock_quote, "SPY", "")
        news = [n for n in (q.get("news") or []) if n.get("title")][:5]
        if news:
            cards.append(V.card("visual.list", "Market headlines", {"type": "list", "items": [
                {"title": n["title"], "subtitle": n.get("publisher", ""), "url": n.get("url", "")} for n in news]}))
            h = re.sub(r"^(EXCLUSIVE|BREAKING|UPDATE|WATCH|VIDEO)\s*[:\-]\s*", "", news[0]["title"], flags=re.I).rstrip(".")
            h = re.split(r",\s*(?:says|according to)\b", h, flags=re.I)[0]
            line += f" In the news: {h}."
    except Exception as e:
        log.info("showcase market news: %s", e)
    return Beat("MARKETS", line, cards)


def _crypto() -> Beat | None:
    from jarvis_google import markets as MK
    cards, coins = [], []
    for coin in ("bitcoin", "ethereum"):
        try:
            c, it = _grab(MK.crypto_quote, coin, "1M")
        except Exception as e:
            log.info("showcase crypto %s: %s", coin, e)
            continue
        if isinstance(c, dict) and not c.get("error"):
            coins.append(c)
            cards += _cards(it)
    if not coins:
        return None

    def say(c: dict) -> str:
        ch = c.get("changes") or {}
        price = c.get("price") or 0
        p = f"{round(price / 1000, 1):g} thousand" if price >= 10000 else f"{price:,.0f}"
        return f"{(c.get('symbol') or c.get('name') or '').upper()} is at {p} dollars, {_pct(ch.get('24h'))} today"
    line = "On the crypto side, " + say(coins[0])
    if len(coins) > 1:
        line += f", and {say(coins[1])}"
    m = (coins[0].get("changes") or {}).get("30d")
    if m is not None:
        line += f". Over the past month, {(coins[0].get('symbol') or coins[0].get('name') or '').upper()} is {_pct(m)}"
    return Beat("CRYPTO", line + ".", cards[::-1])


def _cars() -> Beat | None:
    from jarvis_google import cars as CA
    r, items = _grab(CA.cars_nearby, "Mercedes-Benz", "CLS-Class", "CLS 550 4MATIC")
    if not isinstance(r, dict) or r.get("error") or not r.get("count"):
        return None
    cars = r["cars"]
    near = cars[0]
    best = min((c for c in cars if c.get("price") and c.get("miles")), key=lambda c: c["miles"], default=None)
    line = (f"On the car front, there are {r['count']} CLS 550 4MATICs for sale in the area, from "
            f"{r['price_min']:,} to {r['price_max']:,} dollars. The closest is a {near['year']} in "
            f"{near['location'].split(',')[0]}, {near.get('distance_mi')} miles away, at {near['price']:,} dollars with "
            f"{round(near['miles'] / 1000)} thousand on the clock.")
    if best and best is not near:
        line += (f" The lowest mileage is a {best['year']} in {best['location'].split(',')[0]}, "
                 f"{round(best['miles'] / 1000)} thousand, at {best['price']:,}.")
    return Beat("CARS", line, _cards(items))


def _sports() -> Beat | None:
    from jarvis_google import sports as SP
    lines, cards = [], []
    r, items = _grab(SP.sports_game, "nfl", "Bears", "", "", "")
    if isinstance(r, dict) and not r.get("error") and r.get("teams"):
        st, t = r.get("status") or {}, r["teams"]
        if st.get("state") == "post" and len(t) == 2:
            w = next((x for x in t if x.get("winner")), max(t, key=lambda x: int(x.get("score") or 0)))
            l = t[1] if t[0] is w else t[0]
            day = ""
            try:
                day = " on " + dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00")).astimezone(_tz()).strftime("%A")
            except Exception:
                pass
            bears_won = "bears" in (w.get("name") or "").lower()
            lines.append(f"In sports, the Bears {'beat' if bears_won else 'lost to'} the {(l if bears_won else w).get('short') or ''}"
                         f"{day}, {w.get('score')} to {l.get('score')}.")
        elif st.get("state") == "in":
            lines.append(f"In sports, the Bears are playing right now: {t[0].get('short')} {t[0].get('score')}, "
                         f"{t[1].get('short')} {t[1].get('score')}.")
        cards += _cards(items)
    for lg in ("mlb", "nfl", "nba", "nhl"):  # tonight's marquee game
        try:
            sb = SP.scoreboard(lg, dt.date.today())
        except Exception:
            continue
        g = next((g for g in sb.get("games", []) if g["status"]["state"] in ("pre", "in") and g.get("broadcast")), None)
        if g:
            when = g["status"].get("short", "").split(" - ")[-1].replace(" EDT", "").replace(" EST", "").replace(":00 ", " ")
            lines.append(f"Tonight, it's the {g['name'].replace(' at ', ' at the ')}" + (f" at {when}" if when else "")
                         + (f" on {g['broadcast'].split(',')[0]}" if g.get("broadcast") else "") + ".")
            sbr, sbi = _grab(SP.sports_game, lg, "", "", "", "")
            cards += [c for c in _cards(sbi) if c["kind"] == "sports_scoreboard"][:1]
            break
    if not lines:
        return None
    if lines[0].startswith("Tonight"):
        lines[0] = "In sports, t" + lines[0][1:]
    return Beat("SPORTS", " ".join(lines), cards)


def _commute() -> Beat | None:
    from jarvis_google import routes as RT
    r, items = _grab(RT.directions, "Grand Central Terminal, New York, NY", "", "transit")
    rt = (r.get("routes") or [{}])[0] if isinstance(r, dict) else {}
    legs = [s["transit"] for s in rt.get("steps") or [] if s.get("transit")]
    if not legs:
        return None
    tr = legs[0]
    dep, arr = (tr.get("depart") or "").replace(":00 ", " "), (legs[-1].get("arrive") or "").replace(":00 ", " ")
    line = (f"If you're heading into the city, the next {tr.get('line_name') or tr.get('line')} train leaves "
            f"{tr.get('from')} at {dep} and gets into {legs[-1].get('to')} at {arr}, about "
            f"{_units(rt.get('duration', ''))} door to door.")
    return Beat("COMMUTE", line, _cards(items))


def _video() -> Beat | None:
    from jarvis_google import media as MD
    r, items = _grab(MD.youtube_video, "SpaceX Starship flight test launch")
    cards = _cards(items)
    if not cards:
        return None
    return Beat("VIDEO", "And if you have a few minutes later, the latest Starship flight is worth a watch. I've queued it up.",
                cards, after_s=7.0, ui=[{"op": "after", "card": V.card("media_control", "", {"action": "pause"})}])


def _music() -> Beat | None:
    from jarvis_google import media as MD
    r, items = _grab(MD.music_play, "Come and Get Your Love Re-Recorded Guardians of the Galaxy Redbone", "song")
    cards = _cards(items)
    if not cards:
        return None
    return Beat("MUSIC", "That's your day, sir. Something to start it with.", cards, after_s=0.2)


BRIEFING = [_weather, _email, _slack, _pylon, _calendar, _markets, _crypto, _sports, _commute, _cars, _video, _music]


# ---------------------------------------------------------------- runner
async def _synth(session: "Session", text: str) -> bytes | None:
    if not session.speak:
        return None
    try:
        return await session.voice.tts(text)
    except Exception as e:
        log.warning("showcase tts failed: %s", e)
        return None


class Narrator:
    """Plays lines in order: one clip at a time, each awaited until the HUD reports playback done."""

    def __init__(self, session: "Session", turn_id: str):
        self.s, self.turn_id, self.seq = session, turn_id, 0

    async def play(self, text: str, audio: bytes | None) -> None:
        s = self.s
        await s.send({"type": "assistant_final", "turn_id": f"{self.turn_id}_{self.seq}", "text": text})
        if audio is None:
            await s.send({"type": "system_caption", "text": text})
            await asyncio.sleep(min(9.0, 1.2 + len(text) / 17))
            self.seq += 1
            return
        secs = wav_seconds(audio)
        s._played.clear()
        await s.state("speaking")
        await s.send({"type": "speech", "turn_id": self.turn_id, "seq": self.seq, "text": text, "audio": b64(audio),
                       "mime": "audio/wav"})
        self.seq += 1
        t0 = time.monotonic()
        while True:
            left = secs + 6 - (time.monotonic() - t0)
            if left <= 0:
                break
            try:
                await asyncio.wait_for(s._played.wait(), timeout=left)
            except asyncio.TimeoutError:
                break
            if time.monotonic() - t0 >= secs * 0.85:
                break
            s._played.clear()  # a stale "done" from an earlier clip


async def _ui(session: "Session", op: dict) -> None:
    await session.send({"type": "ui", **op})


async def run(session: "Session", intro_delay: float = 0.0) -> None:
    """The daily briefing. Cancelled like any turn (Esc / new request). intro_delay: the power-on sequence plays first
    (the data is fetched meanwhile)."""
    turn_id = "demo_" + time.strftime("%H%M%S")
    narr = Narrator(session, turn_id)
    hour = dt.datetime.now(_tz()).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    builders: list[Callable[[], Any]] = list(BRIEFING)
    await session.state("thinking")
    await session.send({"type": "showcase", "active": True, "step": 0, "total": len(builders), "label": "GATHERING"})
    await _ui(session, {"op": "clear_cards"})
    prep = [asyncio.create_task(asyncio.to_thread(b)) for b in builders]
    if intro_delay:
        await asyncio.sleep(intro_delay)
    try:
        greeting = f"Good {part}, sir. Here's your day."
        await narr.play(greeting, await _synth(session, greeting))
        beats: list[Beat] = []
        for t in prep:
            try:
                r = await t
            except Exception as e:
                log.warning("showcase beat failed: %s", e)
                continue
            for b in (r if isinstance(r, list) else [r]):
                if b:
                    beats.append(b)

        async def synth_all():  # synthesize ahead, in order (VoiceStudio serializes anyway)
            for b in beats:
                a = await _synth(session, b.line)
                if b.audio is not None and not b.audio.done():
                    b.audio.set_result(a)

        loop = asyncio.get_running_loop()
        for b in beats:
            b.audio = loop.create_future()
        synth = asyncio.create_task(synth_all())
        try:
            for i, b in enumerate(beats, 1):
                await session.send({"type": "showcase", "active": True, "step": i, "total": len(beats), "label": b.label})
                for c in b.cards:
                    await session.send({"type": "card", "turn_id": turn_id, "card": c})
                    await asyncio.sleep(0.12)
                for op in b.ui:
                    if op.get("op") != "after":
                        await _ui(session, op)
                if b.expand is not None:
                    await _ui(session, {"op": "expand", "card_id": b.expand or None})
                await narr.play(b.line, await b.audio if b.audio is not None else None)
                await asyncio.sleep(b.after_s)
                for op in b.ui:
                    if op.get("op") == "after":
                        await session.send({"type": "card", "turn_id": turn_id, "card": op["card"]})
        finally:
            synth.cancel()
    finally:
        for t in prep:
            t.cancel()
        await session.send({"type": "showcase", "active": False})
        await session.state("idle")


async def support_tour(session: "Session", intro: str | None = "Allow me to walk you through it, sir.",
                       show_card: bool = True) -> None:
    """Narrated walkthrough of the support blueprint: each paragraph switches the display to the matching tab."""
    from jarvis_google import support as SU
    turn_id = "tour_" + time.strftime("%H%M%S")
    narr = Narrator(session, turn_id)
    paras = await asyncio.to_thread(SU.narration)
    if show_card:
        bp, items = await asyncio.to_thread(_grab, SU.show, "overview", True)
        cards = _cards(items)
        for c in cards:
            await session.send({"type": "card", "turn_id": turn_id, "card": c})
        if cards:
            await _ui(session, {"op": "expand", "card_id": cards[0]["id"]})
    else:  # the model's support_model call already put the card up
        await _ui(session, {"op": "expand_key", "key": "support:blueprint"})
    chunks: list[tuple[dict | None, str]] = []
    if intro:
        chunks.append((None, intro))
    for p in paras:
        views = p.get("views") or [{"tab": p["tab"], "tier": p.get("tier")}]
        parts = chunk_text(p["text"], max(110, min(220, len(p["text"]) // len(views) + 20)))
        for j, ch in enumerate(parts):
            # spread the paragraph's views across its spoken chunks: the display moves with the narration
            vi = min(len(views) - 1, (j * len(views)) // max(1, len(parts)))
            prev = min(len(views) - 1, ((j - 1) * len(views)) // max(1, len(parts))) if j else -1
            op = {"op": "support_tab", **views[vi], "para": True if j == 0 else False} if vi != prev else None
            chunks.append((op, ch))
    chunks.append(({"op": "support_tab", "tab": "overview"}, "That's the whole picture, sir. The blueprint stays on "
                   "screen, and you can ask me about any part of it."))
    await session.send({"type": "showcase", "active": True, "step": 0, "total": len(paras), "label": "SUPPORT MODEL",
                        "mode": "tour"})
    futs: list[asyncio.Future] = []
    loop = asyncio.get_running_loop()
    futs = [loop.create_future() for _ in chunks]

    async def synth_all():
        for f, (_, text) in zip(futs, chunks):
            f.set_result(await _synth(session, text))

    synth = asyncio.create_task(synth_all())
    step = 0
    try:
        for f, (op, text) in zip(futs, chunks):
            if op:
                if op.pop("para", False):
                    step += 1
                await _ui(session, op)
                label = {"architecture": "BLUEPRINT", "flows": "WORKFLOW"}.get(op.get("tab", ""), str(op.get("tab", "")).upper())
                if op.get("tier"):
                    label = f"TIER {op['tier']}"
                await session.send({"type": "showcase", "active": True, "step": min(step, len(paras)),
                                    "total": len(paras), "label": label, "mode": "tour"})
            await narr.play(text, await f)
    finally:
        synth.cancel()
        await session.send({"type": "showcase", "active": False})
        await session.state("idle")
