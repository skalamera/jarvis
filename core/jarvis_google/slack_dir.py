"""Company directory from Slack (the Google Workspace directory API is not granted on the work account).

Uses the Slack bot token (users:read + users:read.email). The member list is cached in memory for 6 hours;
lookups are local fuzzy matches so misheard names ("Talman") still surface the nearest real people.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

TTL_S = 6 * 3600
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "people": [], "raw": []}


def env_token(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in f.read_text().splitlines():
            m = re.match(rf"\s*(?:export\s+)?{name}\s*=\s*(.*)", line)
            if m and m[1].strip():
                return m[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _token() -> str:
    return env_token("SLACK_BOT_TOKEN")


def _get(method: str, params: dict) -> dict:
    url = f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise RuntimeError(f"slack {method}: {d.get('error')}")
    return d


def _person(m: dict) -> dict:
    p = m.get("profile") or {}
    guest = bool(m.get("is_restricted") or m.get("is_ultra_restricted"))
    return {
        "name": m.get("real_name") or p.get("real_name") or p.get("display_name") or "",
        "display_name": p.get("display_name") or "",
        "email": p.get("email") or "",
        "title": p.get("title") or "",
        "phone": p.get("phone") or "",
        "tz": m.get("tz_label") or "",
        "guest": guest,
        "slack_id": m.get("id"),
        "avatar": p.get("image_192") or p.get("image_72") or "",
    }


def _load(force: bool = False) -> None:
    with _lock:
        if not force and _cache["raw"] and time.time() - _cache["at"] < TTL_S:
            return
    if not _token():
        return
    raw, cursor = [], ""
    for _ in range(20):
        d = _get("users.list", {"limit": 200, **({"cursor": cursor} if cursor else {})})
        raw += d.get("members", [])
        cursor = (d.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    people = [_person(m) for m in raw if not m.get("deleted") and not m.get("is_bot") and m.get("id") != "USLACKBOT"]
    with _lock:
        _cache.update(at=time.time(), people=people, raw=raw)


def members(force: bool = False) -> list[dict]:
    """Active human members (no bots, no deactivated accounts)."""
    _load(force)
    return _cache["people"]


def user_map() -> dict[str, dict]:
    """Every Slack user id (people, bots, deactivated) -> {name, avatar, bot}. For rendering messages."""
    _load()
    out = {}
    for m in _cache["raw"]:
        p = m.get("profile") or {}
        out[m["id"]] = {"id": m["id"], "handle": m.get("name") or "",
                        "name": m.get("real_name") or p.get("real_name") or p.get("display_name") or m.get("name") or "",
                        "avatar": p.get("image_72") or p.get("image_48") or "", "bot": bool(m.get("is_bot")),
                        "deleted": bool(m.get("deleted"))}
    return out


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _score(query: str, p: dict) -> float:
    q = query.lower().strip()
    if not q:
        return 0.0
    name_toks = _tokens(p["name"]) + _tokens(p["display_name"]) + _tokens(p["email"].split("@")[0])
    full = " ".join(_tokens(p["name"]))
    if q == full or q in (p["email"].lower(), p["display_name"].lower()):
        return 1.0
    q_toks = _tokens(q)
    if not q_toks or not name_toks:
        return 0.0
    # every query word must find its best partner among the person's name words
    per = []
    for qt in q_toks:
        best = 0.0
        for nt in name_toks:
            if nt == qt:
                s = 1.0
            elif nt.startswith(qt) and len(qt) >= 2:
                s = 0.9
            else:
                s = difflib.SequenceMatcher(None, qt, nt).ratio()
            best = max(best, s)
        per.append(best)
    return sum(per) / len(per)


def search(query: str, limit: int = 5, min_score: float = 0.72) -> list[dict]:
    """Best matches for a (possibly misheard) name, email or handle. Exact/prefix matches score >= 0.9."""
    ranked = sorted(((round(_score(query, p), 3), p) for p in members()), key=lambda x: -x[0])
    hits = [dict(p, match=s) for s, p in ranked if s >= min_score]
    if hits and hits[0]["match"] >= 0.9:  # a clear hit: drop the weak look-alikes behind it
        hits = [h for h in hits if h["match"] >= 0.9]
    return hits[:limit]


def nearest(query: str, limit: int = 3) -> list[dict]:
    """Closest names regardless of threshold (for 'did you mean' when nothing matches)."""
    ranked = sorted(((round(_score(query, p), 3), p) for p in members()), key=lambda x: -x[0])
    return [dict(p, match=s) for s, p in ranked[:limit]]
