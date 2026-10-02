"""Rewrite text the way a person would say it, before it goes to the TTS engine.

TTS engines misread written forms: "$3,506.99 on 9/29" came back as "3,569 on 929", "#24680" as
"18,133", "2:15 PM" as "215 pm", "Wed" as "WAD". Deterministic, no dependencies, never raises.
"""
from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_MON_ABBR = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "jun": "June", "jul": "July",
             "aug": "August", "sep": "September", "sept": "September", "oct": "October", "nov": "November",
             "dec": "December"}
_DAY_ABBR = {"mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
             "thur": "Thursday", "thurs": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_SUFFIX = {"k": "thousand", "m": "million", "mm": "million", "b": "billion", "bn": "billion", "t": "trillion",
           "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}
_DIGIT = {str(i): _ONES[i] for i in range(10)}


def int_words(n: int) -> str:
    if n < 0:
        return "minus " + int_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + (" " + int_words(r) if r else "")
    for size, name in _SCALES:
        if n >= size:
            q, r = divmod(n, size)
            return int_words(q) + " " + name + (" " + int_words(r) if r else "")
    return str(n)


def ordinal_words(n: int) -> str:
    w = int_words(n)
    head, _, last = w.rpartition(" ")
    pre, dash, tail = last.rpartition("-")
    irregular = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
                 "nine": "ninth", "twelve": "twelfth"}
    if tail in irregular:
        tail = irregular[tail]
    elif tail.endswith("y"):
        tail = tail[:-1] + "ieth"
    else:
        tail += "th"
    last = pre + dash + tail
    return (head + " " + last) if head else last


def digits_words(s: str) -> str:
    return " ".join(_DIGIT[c] for c in s if c.isdigit())


def number_words(s: str) -> str:
    """'6,512' -> six thousand five hundred twelve; '1.25' -> one point two five; long ids digit by digit."""
    s = s.replace(",", "")
    neg = s.startswith(("-", "−"))
    s = s.lstrip("-−+")
    whole, _, frac = s.partition(".")
    if not whole:
        whole = "0"
    if len(whole) > 12:
        out = digits_words(whole)
    else:
        out = int_words(int(whole))
    if frac:
        out += " point " + digits_words(frac)
    return ("minus " + out) if neg else out


def year_words(y: int) -> str:
    if 2000 <= y <= 2009:
        return int_words(y)
    hi, lo = divmod(y, 100)
    if lo == 0:
        return int_words(hi) + " hundred"
    return int_words(hi) + " " + (("oh " + _ONES[lo]) if lo < 10 else int_words(lo))


# ------------------------------------------------------------------ rules (order matters)
_NUM = r"\d{1,3}(?:,\d{3})+|\d+"


def _money(m: re.Match) -> str:
    whole, cents, suffix = m[1].replace(",", ""), m[2], (m[3] or "").strip().lower()
    if suffix:
        amount = whole + ("." + cents if cents else "")
        return f"{number_words(amount)} {_SUFFIX.get(suffix, suffix)} dollars"
    d = int(whole)
    c = int(cents.ljust(2, "0")) if cents else 0
    cents_w = f"{int_words(c)} cent{'' if c == 1 else 's'}"
    if d == 0 and c:
        return cents_w
    out = f"{int_words(d)} dollar{'' if d == 1 else 's'}"
    return out + (f" and {cents_w}" if c else "")


def _percent(m: re.Match) -> str:
    sign = {"+": "plus ", "-": "minus ", "−": "minus "}.get(m[1] or "", "")
    return f"{sign}{number_words(m[2])} percent"


def _time(m: re.Match) -> str:
    h, mm, ap = int(m[1]), m[2], (m[3] or "").lower()
    if h > 23 or int(mm) > 59:
        return m[0]
    if mm == "00":
        mins = "" if ap else " o'clock"
    elif mm.startswith("0"):
        mins = " oh " + _ONES[int(mm)]
    else:
        mins = " " + int_words(int(mm))
    suffix = {"a": " A M", "p": " P M"}.get(ap[:1], "")
    return f"{int_words(h)}{mins}{suffix}"


def _date(m: re.Match) -> str:
    mo, d, y = int(m[1]), int(m[2]), m[3]
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m[0]
    out = f"{_MONTHS[mo - 1]} {ordinal_words(d)}"
    if y:
        yy = int(y)
        out += ", " + year_words(yy + 2000 if yy < 100 else yy)
    return out


def _iso_date(m: re.Match) -> str:
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m[0]
    return f"{_MONTHS[mo - 1]} {ordinal_words(d)}, {year_words(y)}"


def _hash_number(m: re.Match) -> str:
    word, num = m[1], m[2]
    spoken = digits_words(num) if len(num) >= 4 else int_words(int(num))
    return f"{word} {spoken}" if word else f"number {spoken}"


def _phone(m: re.Match) -> str:
    return ", ".join(digits_words(g) for g in m.groups() if g)


def _mon(m: re.Match) -> str:
    return _MON_ABBR[m[1].lower()]


def _day(m: re.Match) -> str:
    return _DAY_ABBR[m[1].lower()]


RULES: list[tuple[re.Pattern, object]] = [
    (re.compile(r"(?:\+?1[\s.\-])?\(?\b(\d{3})\)?[\s.\-](\d{3})[\s.\-](\d{4})\b"), _phone),
    (re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b"), _iso_date),
    (re.compile(rf"\$({_NUM})(?:\.(\d{{1,2}}))?(\s?(?:[kKmMbBtT]|mm|MM|bn|BN|thousand|million|billion|trillion)\b)?"), _money),
    (re.compile(r"([+\-−])?(\d+(?:\.\d+)?)\s?%"), _percent),
    (re.compile(r"\b(\d{1,2}):(\d{2})(?:\s?([AaPp])\.?\s?[Mm]\.?\b)?"), _time),
    (re.compile(r"\b(\d{1,2})\s?([AaPp])\.?[Mm]\.?\b"), lambda m: f"{int_words(int(m[1]))} {m[2].upper()} M"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b"), _date),
    (re.compile(r"(?:\b(ticket|issue|number|no\.|PR|order|case|item)\s*)?#\s?(\d+)\b", re.I), _hash_number),
    (re.compile(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?(?=\s+\d)"), _mon),
    (re.compile(r"\b(Mon|Tues|Tue|Wed|Thurs|Thur|Thu|Fri|Sat|Sun)\.?(?=,|\s+(?:[A-Z][a-z]{2}|\d))"), _day),
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"), lambda m: ordinal_words(int(m[1]))),
    (re.compile(r"(-?\d+(?:\.\d+)?)\s?°\s?([FC])\b"), lambda m: f"{number_words(m[1])} degrees"),
    (re.compile(r"(-?\d+(?:\.\d+)?)\s?°"), lambda m: f"{number_words(m[1])} degrees"),
    (re.compile(r"\b(?:19|20)\d{2}\b(?![,.]\d)"), lambda m: year_words(int(m[0]))),
    (re.compile(r"(?<![\w.\-])([+\-−])?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\w.]*\d)(?![\w\-])"),
     lambda m: ({"+": "plus ", "-": "minus ", "−": "minus "}.get(m[1] or "", "")
                + number_words(m[2] + (m[3] or "")))),
]

_ABBREV = [
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"), (re.compile(r"\be\.g\.,?", re.I), "for example"),
    (re.compile(r"\bi\.e\.,?", re.I), "that is"), (re.compile(r"\betc\.", re.I), "et cetera"),
    (re.compile(r"\bapprox\.", re.I), "approximately"), (re.compile(r"\bmph\b"), "miles per hour"),
    (re.compile(r"\bw/o\b"), "without"), (re.compile(r"\bw/(?=\s)"), "with"),
    (re.compile(r"\bQ([1-4])\b"), lambda m: "Q " + _ONES[int(m[1])]),
    (re.compile(r"\best\.(?=\s|$)"), "estimate"),
    (re.compile(r"\s+/\s+"), " or "),
    # brand names the voice mispronounces (spoken text only; captions keep the real spelling)
    (re.compile(r"\bResy\b", re.I), "Rezzy"),
]


def spoken(text: str) -> str:
    """Rewrite numbers, money, dates, times, ids and common abbreviations as words."""
    try:
        t = text
        for rx, rep in _ABBREV:
            t = rx.sub(rep, t)
        for rx, rep in RULES:
            t = rx.sub(rep, t)
        return re.sub(r"\s{2,}", " ", t)
    except Exception:  # never block speech on a normalization bug
        return text
