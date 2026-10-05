"""HUD launcher + in-card search bars. Click-only, read-only: every function returns the CARD DATA for a display
(the HUD builds / refreshes the card itself), so nothing goes through the model."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from . import store


# ------------------------------------------------------------------ YouTube (his account)
def _cookiefile() -> str | None:
    """His Google session (linked from the music card) as a Netscape cookie file for yt-dlp."""
    from . import media as M
    ap = M._auth_path()
    if not ap.exists():
        return None
    cookie = (json.loads(ap.read_text()).get("cookie") or "").strip()
    if not cookie:
        return None
    exp = str(int(time.time()) + 30 * 86400)
    lines = ["# Netscape HTTP Cookie File"]
    for kv in cookie.split(";"):
        if "=" in kv:
            k, v = kv.strip().split("=", 1)
            lines.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", exp, k, v]))
    p = Path(store.STATE_DIR) / "yt_cookies.txt"
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)
    return str(p)


def youtube_home(n: int = 24) -> dict:
    """Latest videos from the channels he subscribes to (his YouTube home); search results when signed out."""
    from . import media as M
    import yt_dlp

    def run() -> dict:
        cf = _cookiefile()
        if cf:
            try:
                opts: Any = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
                             "cookiefile": cf, "playlistend": n}
                with yt_dlp.YoutubeDL(opts) as y:
                    info: Any = y.extract_info("https://www.youtube.com/feed/subscriptions", download=False)
                rows = [M._video_row(e) for e in (info or {}).get("entries") or [] if e and e.get("id")]
                if rows:
                    return {"query": "", "home": "subscriptions", "results": rows, "current": 0, "paused": True}
            except Exception:
                pass
        return {"query": "", "home": "trending", "results": M.youtube_search("trending today", n), "current": 0,
                "paused": True}
    return M._cached("ythome", 600, run)


def youtube_find(query: str) -> dict:
    from . import media as M
    q = (query or "").strip()
    if not q:
        return youtube_home()
    return {"query": q, "results": M.youtube_search(q, 16), "current": 0}


# ------------------------------------------------------------------ YouTube Music (his library)
def music_home() -> dict:
    from . import media as M
    lib = M.music_library()
    queue = (lib.get("recent") or []) + (lib.get("liked") or [])
    seen, q2 = set(), []
    for t in queue:
        if t and t.get("video_id") not in seen:
            seen.add(t["video_id"])
            q2.append(t)
    return {"mode": "library", "title": "Your library", "subtitle": "YouTube Music", "queue": q2[:60],
            "start_view": "library", "paused": True}


# ------------------------------------------------------------------ Resy
def resy_find(query: str = "", date: str = "", time_from: str = "", time_to: str = "", party_size: int = 2,
              near: str = "") -> dict:
    from . import travel as TR
    if not (time_from or time_to):
        time_from, time_to = "00:00", "23:59"  # anytime
    return _cap("restaurants_search", lambda: TR.restaurants_search(
        query=query, near=near, date=date or today(), party_size=party_size, max_results=12, time_from=time_from,
        time_to=time_to))


# ------------------------------------------------------------------ Uber Eats / Places / Flights
def eats_find(query: str = "") -> dict:
    from . import eats as E
    return _cap("eats_search", lambda: E.search(query, limit=30))


def places_find(query: str = "", near: str = "") -> dict:
    from . import places as PL
    return _cap("places_search", lambda: PL.places_search(query.strip() or "popular places", near=near, limit=10))


def flights_find(destination: str, depart_date: str, return_date: str = "", origin: str = "", adults: int = 1) -> dict:
    from . import travel as TR
    return _cap("flights_search", lambda: TR.flights_search(destination=destination, depart_date=depart_date,
                                                            return_date=return_date, origin=origin, adults=adults))


def _cap(tool: str, fn) -> dict:
    """Run a search WITHOUT publishing a new card: return the card data it would have shown."""
    with store.capture() as items:
        fn()
    hit = [x["result"] for x in items if x["tool"] == tool]
    if not hit:
        raise RuntimeError("nothing found")
    return hit[-1]


def today() -> str:
    return dt.date.today().isoformat()


def files(limit: int = 200) -> dict:
    from . import artifacts as AR
    return AR.file_list(limit)


RPC = {"file_list": files, "youtube_home": youtube_home, "youtube_find": youtube_find, "music_home": music_home, "resy_find": resy_find,
       "eats_find": eats_find, "places_find": places_find, "flights_find": flights_find}
