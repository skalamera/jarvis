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


def _flat(url: str, n: int) -> list[dict]:
    import yt_dlp
    cf = _cookiefile()
    opts: Any = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
                 "playlistend": n, **({"cookiefile": cf} if cf else {})}
    with yt_dlp.YoutubeDL(opts) as y:
        info: Any = y.extract_info(url, download=False)
    return [e for e in (info or {}).get("entries") or [] if e]


def youtube_profile() -> dict:
    """His YouTube: channel, subscriptions, recently watched, liked, watch later, his uploads (signed-in session)."""
    from concurrent.futures import ThreadPoolExecutor
    from . import media as M

    def run() -> dict:
        acct = M.music_auth_status()
        if not acct.get("signed_in"):
            return {"signed_in": False}
        h = acct.get("handle") or ""
        h = h if h.startswith("@") else "@" + h if h else ""

        def vids(url: str, n: int = 12) -> list[dict]:
            try:
                return [M._video_row(e) for e in _flat(url, n) if e.get("id")]
            except Exception:
                return []

        def subs() -> list[dict]:
            try:
                out = []
                for e in _flat("https://www.youtube.com/feed/channels", 60):
                    th = sorted(e.get("thumbnails") or [], key=lambda t: t.get("width") or 0)
                    out.append({"name": e.get("channel") or e.get("title"), "url": e.get("channel_url") or e.get("url"),
                                "followers": e.get("channel_follower_count"),
                                "thumb": ("https:" + th[-1]["url"]) if th and th[-1]["url"].startswith("//") else (th[-1]["url"] if th else "")})
                return out
            except Exception:
                return []
        with ThreadPoolExecutor(6) as ex:
            fs = {"history": ex.submit(vids, "https://www.youtube.com/feed/history"),
                  "liked": ex.submit(vids, "https://www.youtube.com/playlist?list=LL"),
                  "watch_later": ex.submit(vids, "https://www.youtube.com/playlist?list=WL"),
                  "mine": ex.submit(vids, f"https://www.youtube.com/{h}/videos") if h else None,
                  "subscriptions": ex.submit(subs)}
            res = {k: (f.result() if f else []) for k, f in fs.items()}
        return {"signed_in": True, "name": acct.get("name"), "handle": h, "photo": acct.get("photo"),
                "channel_url": f"https://www.youtube.com/{h}" if h else "", **res}
    return M._cached("ytprofile", 600, run)


def youtube_channel(url: str) -> dict:
    from . import media as M
    u = url.rstrip("/") + ("" if url.rstrip("/").endswith("/videos") else "/videos")
    return {"results": [M._video_row(e) for e in _flat(u, 24) if e.get("id")]}


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


def weather_at(location: str = "") -> dict:
    from . import weather as W
    return W.weather(location=location, days=10)  # its return value is the card data


RPC = {"weather_at": weather_at, "youtube_profile": youtube_profile, "youtube_channel": youtube_channel, "file_list": files, "youtube_home": youtube_home, "youtube_find": youtube_find, "music_home": music_home, "resy_find": resy_find,
       "eats_find": eats_find, "places_find": places_find, "flights_find": flights_find}
