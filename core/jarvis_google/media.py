"""YouTube Music + YouTube video lookups for JARVIS (no API key: ytmusicapi and yt-dlp's search).

``music_play`` resolves a request to a play queue (a song plus its radio, an album, a playlist, or an artist's top
songs); ``youtube_video`` resolves a request to one video plus related results. Both return plain dicts the HUD
renders as a MUSIC or VIDEO card that plays through YouTube's embedded player. ``media_control`` only records a
command (pause, next, volume...) for the HUD's player. Read-only: nothing here touches his YouTube account.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from . import store

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_yt = None

CONTROL_ACTIONS = {"pause", "resume", "next", "previous", "stop", "volume_up", "volume_down", "set_volume", "mute", "unmute"}


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _ytm():
    global _yt
    if _yt is None:
        from ytmusicapi import YTMusic
        _yt = YTMusic()
    return _yt


def _thumb(thumbs: list[dict] | None, big: bool = True) -> str:
    if not thumbs:
        return ""
    url = (thumbs[-1] if big else thumbs[0]).get("url", "")
    # Google image URLs carry a size suffix; ask for a crisp square for album art.
    return re.sub(r"=w\d+-h\d+(-[a-z0-9-]+)?$", "=w544-h544-l90-rj", url) if big else url


def _secs(v: Any) -> int | None:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\d+(:\d{2}){1,2}", v):
        n = 0
        for part in v.split(":"):
            n = n * 60 + int(part)
        return n
    return None


def _track(t: dict) -> dict | None:
    vid = t.get("videoId")
    if not vid:
        return None
    artists = ", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name"))
    album = t.get("album")
    return {"video_id": vid, "title": t.get("title", ""), "artist": artists,
            "album": album.get("name", "") if isinstance(album, dict) else (album or ""),
            "duration": _secs(t.get("duration_seconds") or t.get("duration") or t.get("length")),
            "thumb": _thumb(t.get("thumbnails") or t.get("thumbnail"))}


def _dedupe(tracks: list[dict | None]) -> list[dict]:
    seen, out = set(), []
    for t in tracks:
        if t and t["video_id"] not in seen:
            seen.add(t["video_id"])
            out.append(t)
    return out


# ------------------------------------------------------------------ YouTube Music
def _song_radio(song: dict, n: int = 25) -> list[dict]:
    w: Any = _ytm().get_watch_playlist(videoId=song["video_id"], limit=n)
    rest = [_track(t) for t in (w.get("tracks") or []) if isinstance(t, dict)]
    first = {**song}
    if rest and rest[0] and rest[0]["video_id"] == song["video_id"]:
        first = {**rest[0], **{k: v for k, v in song.items() if v}}
        rest = rest[1:]
    return _dedupe([first, *rest])


def music_search(query: str, kind: str = "") -> dict:
    """Resolve `query` to a play queue. kind: song (default: song + radio), album, playlist, artist."""
    q = (query or "").strip()
    if not q:
        return {"error": "Say what to play."}
    kind = (kind or "song").lower().rstrip("s")
    yt: Any = _ytm()
    tracks: list[dict | None]
    if kind == "album":
        hits = yt.search(q, filter="albums", limit=3)
        if not hits:
            return {"error": f"No album matching '{q}'."}
        a = yt.get_album(hits[0]["browseId"])
        art = _thumb(a.get("thumbnails"))
        tracks = _dedupe([_track({**t, "thumbnails": t.get("thumbnails") or a.get("thumbnails"),
                                  "album": {"name": a.get("title", "")}}) for t in a.get("tracks", [])])
        artist = ", ".join(x.get("name", "") for x in a.get("artists") or [])
        return {"mode": "album", "title": a.get("title", ""), "subtitle": " · ".join(x for x in (artist, str(a.get("year") or "")) if x),
                "art": art, "queue": tracks, "url": f"https://music.youtube.com/browse/{hits[0]['browseId']}"}
    if kind == "playlist":
        hits = yt.search(q, filter="playlists", limit=3) or yt.search(q, filter="community_playlists", limit=3)
        if not hits:
            return {"error": f"No playlist matching '{q}'."}
        pid = hits[0]["browseId"]
        p = yt.get_playlist(pid[2:] if pid.startswith("VL") else pid, limit=60)
        tracks = _dedupe([_track(t) for t in p.get("tracks", [])])
        author = (p.get("author") or {}).get("name", "") if isinstance(p.get("author"), dict) else ""
        return {"mode": "playlist", "title": p.get("title", ""), "subtitle": " · ".join(x for x in (author, f"{len(tracks)} songs") if x),
                "art": _thumb(p.get("thumbnails")), "queue": tracks, "url": f"https://music.youtube.com/playlist?list={p.get('id', '')}"}
    if kind == "artist":
        hits = yt.search(q, filter="artists", limit=2)
        if not hits:
            return {"error": f"No artist matching '{q}'."}
        ar = yt.get_artist(hits[0]["browseId"])
        songs = (ar.get("songs") or {})
        tracks = []
        if songs.get("browseId"):
            pl = yt.get_playlist(songs["browseId"][2:] if songs["browseId"].startswith("VL") else songs["browseId"], limit=40)
            tracks = [_track(t) for t in pl.get("tracks", [])]
        if not tracks:
            tracks = [_track(t) for t in songs.get("results", [])]
        return {"mode": "artist", "title": ar.get("name", hits[0].get("artist", q)), "subtitle": "Top songs",
                "art": _thumb(ar.get("thumbnails")), "queue": _dedupe(tracks),
                "url": f"https://music.youtube.com/channel/{hits[0]['browseId']}"}
    hits = yt.search(q, filter="songs", limit=5)
    song = next((t for t in map(_track, hits) if t), None)
    if not song:  # fall back to music videos
        song = next((t for t in map(_track, yt.search(q, filter="videos", limit=5)) if t), None)
    if not song:
        return {"error": f"Nothing on YouTube Music matching '{q}'."}
    queue = _safe(lambda: _song_radio(song), [song])
    return {"mode": "song", "title": song["title"], "subtitle": " · ".join(x for x in (song["artist"], song["album"]) if x),
            "art": song["thumb"], "queue": queue, "url": f"https://music.youtube.com/watch?v={song['video_id']}"}


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def music_play(query: str, kind: str = "", record: bool = True) -> dict:
    r = _cached(f"m|{kind}|{query.lower().strip()}", 1800, lambda: music_search(query, kind))
    if not r.get("error") and not r.get("queue"):
        r = {"error": f"Found '{r.get('title')}' but it has no playable tracks."}
    if record and not r.get("error"):
        r = {**r, "query": query, "started_at": time.time()}
        store.record_result("music_play", None, {"query": query, "kind": kind}, r)
    return r


# ------------------------------------------------------------------ YouTube videos
_VID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def _video_row(e: dict) -> dict:
    thumbs = e.get("thumbnails") or []
    thumb = next((t["url"] for t in reversed(thumbs) if "hq720" in t.get("url", "") or "hqdefault" in t.get("url", "")),
                 thumbs[-1]["url"] if thumbs else "")
    vid = e.get("id") or ""
    return {"video_id": vid, "title": e.get("title", ""), "channel": e.get("channel") or e.get("uploader", ""),
            "duration": _secs(e.get("duration")), "views": e.get("view_count"), "live": e.get("live_status") == "is_live",
            "thumb": thumb.split("?")[0] if thumb else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={vid}"}


def youtube_search(query: str, n: int = 10) -> list[dict]:
    import yt_dlp

    def run() -> list[dict]:
        opts: Any = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as y:
            info: Any = y.extract_info(f"ytsearch{n}:{query}", download=False)
        rows = [_video_row(e) for e in (info or {}).get("entries") or [] if e and e.get("id")]
        return [r for r in rows if r["duration"] is None or r["duration"] > 0]
    return _cached(f"v|{n}|{query.lower().strip()}", 1800, run)


def youtube_video(query: str = "", video_id: str = "", record: bool = True) -> dict:
    q = (query or "").strip()
    m = _VID.search(q) or _VID.search(video_id or "")
    vid = m.group(1) if m else (video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or "") else "")
    if not q and not vid:
        return {"error": "Say what to watch."}
    results = youtube_search(q, 10) if q and not m else []
    if vid:
        cur = next((r for r in results if r["video_id"] == vid), None) or {
            "video_id": vid, "title": "", "channel": "", "duration": None, "views": None, "live": False,
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "url": f"https://www.youtube.com/watch?v={vid}"}
        results = [cur] + [r for r in results if r["video_id"] != vid]
    if not results:
        return {"error": f"No YouTube videos matching '{q}'."}
    r = {"query": q, "results": results, "current": 0}
    if record:
        store.record_result("youtube_video", None, {"query": q, "video_id": vid}, r)
    return r


# ------------------------------------------------------------------ control + model briefs
def media_control(action: str, level: int | None = None) -> dict:
    a = (action or "").lower().strip().replace(" ", "_").replace("-", "_")
    a = {"play": "resume", "unpause": "resume", "skip": "next", "back": "previous", "prev": "previous",
         "louder": "volume_up", "quieter": "volume_down", "softer": "volume_down", "volume": "set_volume"}.get(a, a)
    if a not in CONTROL_ACTIONS:
        return {"error": f"Unknown action '{action}'. Use one of: {', '.join(sorted(CONTROL_ACTIONS))}."}
    cmd: dict = {"action": a}
    if a == "set_volume":
        if level is None:
            return {"error": "set_volume needs level 0-100."}
        cmd["level"] = max(0, min(100, int(level)))
    store.record_result("media_control", None, cmd, cmd)
    return {"ok": True, **cmd, "note": "sent to the HUD player"}


def brief(tool: str, r: dict) -> dict:
    if not isinstance(r, dict) or r.get("error"):
        return r
    if tool == "music_play":
        q = r.get("queue") or []
        first = q[0] if q else {}
        return {"mode": r.get("mode"), "title": r.get("title"), "subtitle": r.get("subtitle"),
                "now_playing": f"{first.get('title', '')} by {first.get('artist', '')}".strip(),
                "up_next": [f"{t['title']} - {t['artist']}" for t in q[1:5]], "queue_length": len(q),
                "card": "music player shown and playing"}
    if tool == "youtube_video":
        res = r.get("results") or []
        cur = res[0] if res else {}
        return {"playing": {k: cur.get(k) for k in ("title", "channel", "duration", "views")},
                "other_results": [f"{x['title']} ({x['channel']})" for x in res[1:5]],
                "card": "video player shown and playing; other results listed under it"}
    return r
