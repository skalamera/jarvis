"""Instant local answers for questions that don't need the model (no Hermes round trip).

Deliberately narrow: only whole-utterance questions about the current time/date. Anything with more in it
("what time is my meeting", "what's the date of the demo") goes to Hermes as usual.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

_PRE = r"^(?:(?:hey |ok |okay )?jarvis[,.!]?\s*)?(?:(?:can|could) you tell me\s+|do you know\s+)?"
_POST = r"(?:\s*(?:,\s*)?(?:jarvis|sir|please))*[\s?.!]*$"
_TIME = re.compile(_PRE + r"(?:what(?:'s| is) the time|what time is it(?: now| right now)?|what time it is"
                   r"|(?:the )?time(?: please)?)(?: right now| now)?" + _POST, re.I)
_DATE = re.compile(_PRE + r"(?:what(?:'s| is) (?:today's date|the date(?: today)?|today)|what date is it(?: today)?"
                   r"|what day is (?:it|today)(?: today)?|what's the day(?: today)?)" + _POST, re.I)


def _clock(now: dt.datetime) -> str:
    h, m = now.hour % 12 or 12, now.minute
    ampm = "at night" if now.hour < 5 else "in the morning" if now.hour < 12 else "in the afternoon" if now.hour < 17 else "in the evening"
    if m == 0:
        return f"{h} o'clock {ampm}"
    return f"{h}:{m:02d} {ampm}"


def _day(now: dt.datetime) -> str:
    return f"{now:%A}, {now:%B} {now.day}"


def quick_answer(text: str, tz: str, now: dt.datetime | None = None) -> str | None:
    """Return a spoken reply if ``text`` is a plain time/date question, else None."""
    t = (text or "").strip()
    if len(t) > 60:
        return None
    now = now or dt.datetime.now(ZoneInfo(tz))
    if _TIME.match(t):
        if 1 <= now.hour < 5:  # the one hour of the day that warrants a remark
            return f"It's {_clock(now)}, sir. Rather late, even by your standards."
        return f"It's {_clock(now)}, sir."
    if _DATE.match(t):
        return f"It's {_day(now)}, sir."
    return None
