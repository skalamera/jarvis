"""YouTube Music + YouTube video lookups for JARVIS (no API key: ytmusicapi and yt-dlp's search).

``music_play`` resolves a request to a play queue (a song plus its radio, an album, a playlist, or an artist's top
songs); ``youtube_video`` resolves a request to one video plus related results. Both return plain dicts the HUD
renders as a MUSIC or VIDEO card that plays through YouTube's embedded player. ``media_control`` only records a
command (pause, next, volume...) for the HUD's player. Read-only: nothing here touches his YouTube account.
"""
from __future__ import annotations

import os
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


def _auth_path():
    from . import store
    return store.STATE_DIR / "ytmusic_auth.json"


def _ytm():
    """Signed in (his YouTube Music account) when he linked it from the music display; anonymous otherwise."""
    global _yt
    if _yt is None:
        from ytmusicapi import YTMusic
        ap = _auth_path()
        try:
            _yt = YTMusic(str(ap)) if ap.exists() else YTMusic()
        except Exception:
            _yt = YTMusic()
    return _yt


def music_set_auth(cookie: str, authuser: str = "0") -> dict:
    """Save his YouTube Music session (cookies from the in-app sign-in window). Stored outside the repo, 0600."""
    global _yt
    if "SAPISID" not in cookie and "__Secure-3PAPISID" not in cookie:
        raise ValueError("That sign-in didn't finish (no Google session cookie yet).")
    from ytmusicapi.auth.browser import setup_browser
    from ytmusicapi.helpers import get_authorization, sapisid_from_cookie
    origin = "https://music.youtube.com"
    raw = "\n".join([f"cookie: {cookie}", f"x-goog-authuser: {authuser}", f"origin: {origin}",
                     f"authorization: {get_authorization(sapisid_from_cookie(cookie) + ' ' + origin)}"])
    ap = _auth_path()
    ap.parent.mkdir(parents=True, exist_ok=True)
    setup_browser(str(ap), headers_raw=raw)
    os.chmod(ap, 0o600)
    _yt = None
    _cache.clear()
    st = music_auth_status()
    if not st["signed_in"]:
        ap.unlink(missing_ok=True)
        _yt = None
        raise RuntimeError("YouTube Music didn't accept that session; try signing in again.")
    return st


def music_sign_out() -> dict:
    global _yt
    _auth_path().unlink(missing_ok=True)
    _yt = None
    _cache.clear()
    return {"signed_in": False}


def music_auth_status() -> dict:
    if not _auth_path().exists():
        return {"signed_in": False}

    def fetch() -> dict:
        try:
            yt: Any = _ytm()
            acct = yt.get_account_info()
            return {"signed_in": True, "name": acct.get("accountName"), "handle": acct.get("channelHandle"),
                    "photo": acct.get("accountPhotoUrl")}
        except Exception as e:
            return {"signed_in": False, "error": str(e)[:160]}
    return _cached("auth_status", 600, fetch)


def music_library() -> dict:
    """His YouTube Music library (signed in only): playlists, liked songs, recently played."""
    if not music_auth_status().get("signed_in"):
        return {"signed_in": False}

    def fetch() -> dict:
        yt: Any = _ytm()
        pls = _safe(lambda: yt.get_library_playlists(limit=25), []) or []
        liked = _safe(lambda: yt.get_liked_songs(limit=50), {}) or {}
        hist = _safe(lambda: yt.get_history(), []) or []
        return {"signed_in": True,
                "playlists": [{"id": x.get("playlistId"), "title": x.get("title", ""), "count": x.get("count"),
                               "thumb": _thumb(x.get("thumbnails"))} for x in pls if x.get("playlistId")],
                "liked": _dedupe([_track(t) for t in liked.get("tracks") or []]),
                "recent": _dedupe([_track(t) for t in hist])[:25]}
    return _cached("library", 300, fetch)


def music_playlist(playlist_id: str) -> dict:
    def fetch() -> dict:
        for attempt in range(4):  # a just-created / just-edited playlist 404s for a few seconds
            try:
                p: Any = _ytm().get_playlist(playlist_id, limit=100)
                break
            except KeyError:
                if attempt == 3:
                    raise RuntimeError("YouTube Music is still updating that playlist; try again in a moment.")
                time.sleep(2.5)
        author = (p.get("author") or {}).get("name", "") if isinstance(p.get("author"), dict) else ""
        return {"id": playlist_id, "title": p.get("title", ""), "author": author, "art": _thumb(p.get("thumbnails")),
                "tracks": _dedupe([_track(t) for t in p.get("tracks", [])])}
    return _cached(f"pl|{playlist_id}", 600, fetch)


def music_find(query: str) -> dict:
    """Search results for the music display: top result, songs, artists, albums, playlists (no playback)."""
    q = (query or "").strip()
    if not q:
        return {"query": q}

    def fetch() -> dict:
        yt: Any = _ytm()
        res = yt.search(q, limit=20) or []
        out: dict[str, Any] = {"query": q, "top": None, "songs": [], "artists": [], "albums": [], "playlists": []}
        for r in res:
            rt = (r.get("resultType") or "").lower()
            cat = (r.get("category") or "").lower()
            item: dict | None = None
            if rt in ("song", "video"):
                t = _track(r)
                if t:
                    item = {**t, "type": rt}
                    if len(out["songs"]) < 8:
                        out["songs"].append(item)
            elif rt == "artist" and (r.get("browseId") or any(a.get("id") for a in r.get("artists") or [])):
                a0 = next((a for a in r.get("artists") or [] if a.get("id")), {})
                nm = r.get("artist") or r.get("title") or a0.get("name") or ""
                item = {"type": "artist", "id": r.get("browseId") or a0.get("id"), "name": nm,
                        "thumb": _thumb(r.get("thumbnails")), "sub": r.get("subscribers")}
                out["artists"].append(item)
            elif rt == "album" and r.get("browseId"):
                item = {"type": "album", "id": r["browseId"], "title": r.get("title", ""), "year": _year(r),
                        "artist": ", ".join(a.get("name", "") for a in r.get("artists") or []),
                        "thumb": _thumb(r.get("thumbnails")), "kind": r.get("type") or "Album"}
                out["albums"].append(item)
            elif rt == "playlist" and r.get("browseId"):
                pid = r["browseId"]
                item = {"type": "playlist", "id": pid[2:] if pid.startswith("VL") else pid, "title": r.get("title", ""),
                        "author": r.get("author") or "", "thumb": _thumb(r.get("thumbnails"))}
                out["playlists"].append(item)
            if item and cat == "top result" and not out["top"]:
                out["top"] = item
        if not out["top"]:
            out["top"] = (out["artists"] or out["songs"] or out["albums"] or [None])[0]
        return out
    return _cached(f"find|{q.lower()}", 600, fetch)


def _need_auth() -> Any:
    if not music_auth_status().get("signed_in"):
        raise RuntimeError("Sign in to YouTube Music first (button on the music display).")
    return _ytm()


def _liked_ids() -> set[str]:
    def fetch() -> list[str]:
        liked = _safe(lambda: _ytm().get_liked_songs(limit=1000), {}) or {}
        return [t["videoId"] for t in liked.get("tracks") or [] if t.get("videoId")]
    return set(_cached("liked_ids", 300, fetch))


def music_like_status(video_ids: list[str]) -> dict:
    """Which of these tracks he's liked (thumbs up) on YouTube Music."""
    if not music_auth_status().get("signed_in"):
        return {"signed_in": False, "liked": []}
    ids = _liked_ids()
    return {"signed_in": True, "liked": [v for v in video_ids if v in ids]}


def music_rate(video_id: str, rating: str = "like") -> dict:
    """Thumbs up (like), thumbs down (dislike) or clear (none) a song on his YouTube Music account."""
    from ytmusicapi.models.content.enums import LikeStatus
    yt: Any = _need_auth()
    r = {"like": LikeStatus.LIKE, "dislike": LikeStatus.DISLIKE, "none": LikeStatus.INDIFFERENT}.get(rating.lower())
    if r is None:
        raise ValueError("rating must be like, dislike or none")
    yt.rate_song(video_id, r)
    with _lock:
        hit = _cache.get("liked_ids")
        if hit:
            ids = set(hit[1])
            (ids.add if r == LikeStatus.LIKE else ids.discard)(video_id)
            _cache["liked_ids"] = (hit[0], list(ids))
        _cache.pop("library", None)
    return {"video_id": video_id, "rating": rating.lower()}


def music_my_playlists() -> dict:
    """His own (editable) playlists, for "add to playlist"."""
    yt: Any = _need_auth()

    def fetch() -> list[dict]:
        pls = _safe(lambda: yt.get_library_playlists(limit=100), []) or []
        return [{"id": x["playlistId"], "title": x.get("title", ""), "count": x.get("count"),
                 "thumb": _thumb(x.get("thumbnails"), big=False)}
                for x in pls if x.get("playlistId") and x["playlistId"] not in ("LM", "SE")]
    return {"playlists": _cached("my_playlists", 120, fetch)}


def music_playlist_add(playlist_id: str, video_ids: list[str]) -> dict:
    yt: Any = _need_auth()
    r = yt.add_playlist_items(playlist_id, video_ids, duplicates=False)
    if isinstance(r, dict) and r.get("status") not in (None, "STATUS_SUCCEEDED"):
        msg = (((r.get("actions") or [{}])[0].get("addToToastAction") or {}).get("item") or {})
        raise RuntimeError(f"YouTube Music: {r.get('status')}" + (f" ({msg})" if msg else ""))
    _invalidate_playlists(playlist_id)
    return {"playlist_id": playlist_id, "added": len(video_ids)}


def music_playlist_create(title: str, video_ids: list[str] | None = None, description: str = "",
                          privacy: str = "PRIVATE") -> dict:
    yt: Any = _need_auth()
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the playlist a name.")
    privacy = privacy.upper() if privacy.upper() in ("PRIVATE", "UNLISTED", "PUBLIC") else "PRIVATE"
    pid = yt.create_playlist(title, description or "", privacy_status=privacy, video_ids=video_ids or None)
    if not isinstance(pid, str):
        raise RuntimeError(f"YouTube Music couldn't create the playlist: {str(pid)[:160]}")
    _invalidate_playlists(pid)
    return {"playlist_id": pid, "title": title, "added": len(video_ids or []), "privacy": privacy}


def music_playlist_remove(playlist_id: str, video_id: str) -> dict:
    yt: Any = _need_auth()
    p = yt.get_playlist(playlist_id, limit=500)
    rows = [{"videoId": t["videoId"], "setVideoId": t["setVideoId"]} for t in p.get("tracks") or []
            if t.get("videoId") == video_id and t.get("setVideoId")]
    if not rows:
        raise ValueError("That song isn't in this playlist.")
    yt.remove_playlist_items(playlist_id, rows)
    _invalidate_playlists(playlist_id)
    return {"playlist_id": playlist_id, "removed": len(rows)}


def _invalidate_playlists(pid: str = "") -> None:
    with _lock:
        for k in ("my_playlists", "library", f"pl|{pid}"):
            _cache.pop(k, None)


def _resolve_track(query: str) -> dict:
    hits = _ytm().search(query, filter="songs", limit=3)
    t = next((x for x in map(_track, hits) if x), None)
    if not t:
        raise ValueError(f"No song matching '{query}'.")
    return t


def _resolve_playlist(name: str) -> dict | None:
    want = name.lower().strip()
    pls = music_my_playlists()["playlists"]
    return (next((p for p in pls if p["title"].lower() == want), None)
            or next((p for p in pls if want in p["title"].lower()), None))


def _now_playing() -> dict | None:
    from . import store
    for it in reversed(store.feed_since(0)[-200:]):
        if it.get("tool") == "music_play" and isinstance(it.get("result"), dict):
            q = it["result"].get("queue") or []
            return q[0] if q else None
    return None


def music_library_action(action: str, song: str = "", playlist: str = "", new_playlist: str = "") -> dict:
    """Voice entry point: like / unlike / add a song to a playlist / create a playlist. song="" = what's playing."""
    action = action.lower().strip()
    t = None
    if song:
        t = _resolve_track(song)
    elif action in ("like", "unlike", "dislike", "add"):
        t = _now_playing()
        if not t:
            raise ValueError("Nothing is playing; say which song.")
    if action in ("like", "unlike", "dislike"):
        music_rate(t["video_id"], {"like": "like", "unlike": "none", "dislike": "dislike"}[action])
        return {"done": action, "song": f"{t['title']} by {t['artist']}"}
    if action == "add":
        if new_playlist:
            r = music_playlist_create(new_playlist, [t["video_id"]])
            return {"done": "created_and_added", "song": t["title"], "playlist": r["title"]}
        pl = _resolve_playlist(playlist)
        if not pl:
            names = [p["title"] for p in music_my_playlists()["playlists"]][:15]
            raise ValueError(f"No playlist called '{playlist}'. His playlists: {', '.join(names)}")
        music_playlist_add(pl["id"], [t["video_id"]])
        return {"done": "added", "song": t["title"], "playlist": pl["title"]}
    if action == "create":
        r = music_playlist_create(new_playlist or playlist, [t["video_id"]] if t else None)
        return {"done": "created", "playlist": r["title"], "with": t["title"] if t else None}
    raise ValueError("action must be like, unlike, dislike, add or create")


def music_radio(video_id: str) -> dict:
    """Song radio queue starting from one track (for playing a search result)."""
    return {"queue": _cached(f"radio|{video_id}", 1800, lambda: _song_radio({"video_id": video_id}, 40))}


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
            "artists": [{"name": a["name"], "id": a.get("id")} for a in t.get("artists") or []
                        if isinstance(a, dict) and a.get("name")],
            "album_id": album.get("id") if isinstance(album, dict) else None,
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


def _year(x: dict) -> str:
    y = x.get("year")
    return str(y) if y and str(y).isdigit() else ""


def music_artist(artist_id: str = "", name: str = "") -> dict:
    """An artist's page for the music display: header, top songs (playable), albums, singles, similar artists.
    Read-only: browsing it never touches what's playing."""
    def fetch() -> dict:
        yt: Any = _ytm()
        aid = artist_id
        if not aid:
            hits = yt.search(name, filter="artists", limit=3) if name else []
            if not hits:
                raise ValueError(f"No artist matching '{name}'.")
            aid = hits[0]["browseId"]
        ar = yt.get_artist(aid)
        songs = ar.get("songs") or {}
        top: list[dict | None] = []
        if songs.get("browseId"):
            pid = songs["browseId"]
            top = [_track(t) for t in _safe(lambda: yt.get_playlist(pid[2:] if pid.startswith("VL") else pid, limit=25)
                                            .get("tracks", []), [])]
        if not top:
            top = [_track(t) for t in songs.get("results") or []]

        def rel(sec: str, kind: str) -> list[dict]:
            return [{"id": x.get("browseId"), "title": x.get("title", ""), "year": _year(x), "type": kind,
                     "thumb": _thumb(x.get("thumbnails"))}
                    for x in (ar.get(sec) or {}).get("results") or [] if x.get("browseId")]
        desc = ar.get("description") or ""
        return {"id": aid, "name": ar.get("name") or name, "art": _thumb(ar.get("thumbnails")),
                "subscribers": ar.get("subscribers"), "monthly_listeners": ar.get("monthlyListeners"),
                "description": "" if desc in ("None", None) else desc[:600],
                "top_songs": _dedupe(top)[:25], "albums": rel("albums", "Album"), "singles": rel("singles", "Single"),
                "related": [{"id": x.get("browseId"), "name": x.get("title", ""), "subscribers": x.get("subscribers"),
                             "thumb": _thumb(x.get("thumbnails"))}
                            for x in (ar.get("related") or {}).get("results") or [] if x.get("browseId")][:12],
                "url": f"https://music.youtube.com/channel/{aid}"}
    return _cached(f"artist|{artist_id or name.lower().strip()}", 3600, fetch)


def music_album(album_id: str) -> dict:
    """One album / single's tracks for the music display."""
    def fetch() -> dict:
        a: Any = _ytm().get_album(album_id)
        tracks = _dedupe([_track({**t, "thumbnails": t.get("thumbnails") or a.get("thumbnails"),
                                  "album": {"name": a.get("title", ""), "id": album_id}}) for t in a.get("tracks", [])])
        return {"id": album_id, "title": a.get("title", ""), "type": a.get("type") or "Album",
                "artist": ", ".join(x.get("name", "") for x in a.get("artists") or []),
                "artists": [{"name": x.get("name"), "id": x.get("id")} for x in a.get("artists") or [] if x.get("name")],
                "year": _year(a), "art": _thumb(a.get("thumbnails")), "tracks": tracks,
                "duration": a.get("duration"), "url": f"https://music.youtube.com/browse/{album_id}"}
    return _cached(f"album|{album_id}", 3600, fetch)


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
