"""Video understanding: phone screen recordings, camera clips, desktop video files.

Any container / codec ffmpeg can read (iPhone HEVC / HDR .mov, Android .mp4 / .3gp / .webm / .mkv, desktop .avi /
.wmv / .flv / .mpg / .mts ...) is normalised to an H.264/AAC "proxy" MP4 (long side <= 1280 px so on-screen text stays
legible, 8-bit, upright). The proxy is what the HUD plays and what Gemini watches (picture + sound, ~1 frame/s).

  prepare(aid)   probe + proxy + thumbnails + Gemini upload (cached; started as soon as a video is attached)
  analyze(aid)   full read: summary, timeline, on-screen text, issues, steps, speech (cached in analysis.json)
  ask(aid, q)    follow-up questions about the same video, answered with timestamps (reuses the upload)

Everything lives next to the artifact (STATE_DIR/artifacts/<aid>/); the original upload is never modified.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import artifacts as AR
from . import garage as G
from . import store

MODEL = os.environ.get("JARVIS_VIDEO_MODEL", G.MODEL)
API = G.API
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
PROXY_LONG_SIDE = 1280
URI_TTL_S = 40 * 3600            # Gemini keeps uploads 48 h
LOW_RES_AFTER_S = 40 * 60        # long videos: fewer tokens per frame so they fit the context
WAIT_S = 75                      # how long the tool call blocks before handing off to the background

_locks: dict[str, threading.Lock] = {}
_glock = threading.Lock()
_running: set[str] = set()


@contextlib.contextmanager
def _lock(aid: str, name: str = "prep"):
    """Thread + cross-process lock (Core starts the analysis at upload; the tool server may ask at the same time)."""
    with _glock:
        tl = _locks.setdefault(f"{aid}:{name}", threading.Lock())
    with tl:
        _dir(aid).mkdir(parents=True, exist_ok=True)
        with open(_dir(aid) / f".{name}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _dir(aid: str) -> Path:
    return AR.ART_DIR / aid


def _state(aid: str) -> dict:
    try:
        return json.loads((_dir(aid) / "video.json").read_text())
    except Exception:
        return {}


def _save(aid: str, st: dict) -> dict:
    p = _dir(aid) / "video.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(p)
    return st


def _update(aid: str, **kw: Any) -> dict:
    st = _state(aid)
    st.update(kw)
    return _save(aid, st)


# ------------------------------------------------------------------ probe / proxy
def _fmt_t(s: float | int | None) -> str:
    s = int(round(float(s or 0)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _secs(t: Any) -> int | None:
    if isinstance(t, (int, float)):
        return int(t)
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s*", str(t or ""))
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def probe(path: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError("That file isn't a video I can read" + (f" ({r.stderr.strip()[:120]})" if r.stderr else ""))
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    if not v:
        raise ValueError("No video track in that file.")
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = j.get("format") or {}
    tags = {k.lower(): v_ for k, v_ in {**(fmt.get("tags") or {}), **(v.get("tags") or {})}.items()}
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float(tags.get("rotate", 0) or 0))
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if abs(rot) % 180 == 90:
        w, h = h, w
    try:
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0
    except Exception:
        fps = 0
    make = tags.get("com.apple.quicktime.make") or tags.get("com.android.manufacturer") or tags.get("make") or ""
    model = tags.get("com.apple.quicktime.model") or tags.get("com.android.model") or tags.get("model") or ""
    sw = tags.get("com.apple.quicktime.software") or tags.get("com.android.version") or ""
    device = " ".join(x for x in (make, model) if x).strip()
    platform = ("iPhone / iOS" if "apple" in make.lower() or "iphone" in model.lower() or tags.get("com.apple.quicktime.model")
                else "Android" if tags.get("com.android.version") or tags.get("com.android.manufacturer") else "")
    return {"duration": round(float(fmt.get("duration") or v.get("duration") or 0), 2), "width": w, "height": h,
            "fps": fps, "vcodec": v.get("codec_name"), "acodec": (a or {}).get("codec_name"), "has_audio": bool(a),
            "hdr": (v.get("color_transfer") or "") in ("arib-std-b67", "smpte2084"), "rotation": rot,
            "container": fmt.get("format_name"), "size": int(fmt.get("size") or path.stat().st_size),
            "created": tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time") or "",
            "device": device, "software": sw, "platform": platform,
            "orientation": "portrait" if h > w else "landscape" if w > h else "square"}


_FILTERS: set[str] | None = None


def _has_filter(name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
            _FILTERS = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 2 and "->" in ln}
        except Exception:
            _FILTERS = set()
    return name in _FILTERS


def _hdr_chain() -> str:
    """iPhone HDR (HLG / Dolby Vision) -> SDR so colours aren't washed out. Uses what this ffmpeg build has."""
    if _has_filter("zscale") and _has_filter("tonemap"):
        return "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,"
    if _has_filter("libplacebo"):
        return "libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
    return "colorspace=all=bt709:iall=bt2020:fast=1," if _has_filter("colorspace") else ""


def _encode(src: Path, dst: Path, info: dict) -> None:
    long_side = max(info["width"], info["height"])
    target = min(PROXY_LONG_SIDE, long_side - long_side % 2 or PROXY_LONG_SIDE)
    scale = (f"scale='if(gte(iw,ih),{target},-2)':'if(gte(iw,ih),-2,{target})'" if long_side > PROXY_LONG_SIDE
             else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    vf = f"{scale},fps='min(30,source_fps)',format=yuv420p"
    if info.get("hdr"):
        vf = _hdr_chain() + vf
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k", "-ac", "2"]
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", *audio,
            "-movflags", "+faststart", "-map_metadata", "-1"]
    tries = [["-vf", vf, "-c:v", "h264_videotoolbox", "-b:v", "3500k", "-allow_sw", "1"],
             ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]]
    if info.get("hdr"):  # if the tone-map chain fails on this file, still convert (colours a bit flat)
        plain = f"{scale},fps='min(30,source_fps)',format=yuv420p"
        tries.append(["-vf", plain, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24"])
    err = ""
    for t in tries:
        r = subprocess.run([*base, *t, str(dst)], capture_output=True, text=True,
                           timeout=max(300, int(info["duration"] * 2)))
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 1000:
            return
        err = r.stderr.strip()[-300:]
    raise RuntimeError(f"Couldn't convert that video: {err}")


def _thumbs(proxy: Path, out: Path, duration: float, n: int = 8) -> list[dict]:
    out.mkdir(exist_ok=True)
    shots = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        f = out / f"t{i}.jpg"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(proxy),
                        "-frames:v", "1", "-vf", "scale=-2:200", "-q:v", "5", str(f)], capture_output=True, timeout=60)
        if f.exists():
            shots.append({"i": i, "s": round(t, 1), "t": _fmt_t(t)})
    return shots


def proxy_path(aid: str) -> Path | None:
    p = _dir(aid) / "proxy.mp4"
    return p if p.exists() else None


def thumb_path(aid: str, i: int) -> Path | None:
    p = _dir(aid) / "thumbs" / f"t{int(i)}.jpg"
    return p if p.exists() else None


def prepare(aid: str) -> dict:
    """Probe, convert, thumbnail and upload once; later calls return the cached state."""
    m = AR._meta(aid)
    if m["kind"] != "video":
        raise ValueError(f"{m['filename']} isn't a video.")
    with _lock(aid):
        st = _state(aid)
        if st.get("version") != len(m["versions"]):
            st = {"version": len(m["versions"]), "filename": m["filename"]}
        if not st.get("info"):
            _publish(aid, {**st, "status": "preparing"})
            st["info"] = probe(AR.current_path(m))
            _save(aid, st)
        proxy = _dir(aid) / "proxy.mp4"
        if not proxy.exists():
            _encode(AR.current_path(m), proxy, st["info"])
            st["thumbs"] = _thumbs(proxy, _dir(aid) / "thumbs", st["info"]["duration"])
            st["proxy_size"] = proxy.stat().st_size
            _save(aid, st)
        if not st.get("uri") or time.time() - st.get("uri_ts", 0) > URI_TTL_S:
            _publish(aid, {**st, "status": "uploading"})
            st["uri"] = G._upload_file(proxy.read_bytes(), "video/mp4", m["filename"])
            st["uri_ts"] = time.time()
            _save(aid, st)
        return st


# ------------------------------------------------------------------ Gemini
ANALYZE_PROMPT = """You are watching a video Stephen gave you{kind_hint}. Watch ALL of it (picture and sound) and
report what is actually in it. Never guess: if something is unreadable or not shown, say so.
Today is {today} (use it when judging dates shown on screen, e.g. expiry dates).
File: {filename}. Duration {duration}. {orientation}. {device}

Return JSON only:
{{"title": short descriptive title,
 "type": "screen_recording" | "camera" | "screen_share" | "presentation" | "tutorial" | "meeting" | "other",
 "platform": what it was recorded on / shows (e.g. "iOS", "Android", "macOS", "Windows", "web browser", "real world"),
 "apps": [apps / websites / products visible, in order of appearance],
 "summary": 3-5 plain sentences: what happens, what he was doing or trying to do, how it ends,
 "timeline": [{{"t": "m:ss", "event": one line}}]  (every meaningful change: screens, taps, typed input, results,
   errors; 6-30 entries),
 "on_screen_text": [{{"t": "m:ss", "text": exact important text: errors, messages, prices, names, numbers, URLs}}],
 "issues": [{{"t": "m:ss", "issue": errors, failures, bugs, glitches, confusing UI, anything that went wrong}}],
 "steps": [numbered how-to steps if the video demonstrates a procedure, else []],
 "speech": "what is said (concise transcript or summary) or 'no speech'",
 "takeaways": [2-5 things worth knowing / doing],
 "spoken": 1-2 natural sentences JARVIS would say about it out loud (no lists, no timestamps unless essential)}}"""

ASK_PROMPT = """You are answering Stephen's question about a video you can watch (picture and sound). Watch the
relevant parts carefully. Answer from what the video actually shows; if it isn't in the video, say so plainly.
Today is {today} (use it when judging dates shown on screen).
Earlier analysis for reference (may be incomplete): {summary}
Question: {question}

Return JSON only: {{"answer": a direct answer in 1-4 sentences, "moments": [{{"t": "m:ss", "note": what happens there}}]
(the timestamps that support the answer, 0-6)}}"""


def _today() -> str:
    return time.strftime("%A, %B %-d, %Y")


def _generate(st: dict, prompt: str) -> dict:
    key = G._env("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    cfg: dict[str, Any] = {"temperature": 0.2, "responseMimeType": "application/json"}
    if st["info"]["duration"] > LOW_RES_AFTER_S:
        cfg["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    body = {"contents": [{"parts": [{"fileData": {"mimeType": "video/mp4", "fileUri": st["uri"]}}, {"text": prompt}]}],
            "generationConfig": cfg}
    for attempt in range(3):
        r = httpx.post(f"{API}/models/{MODEL}:generateContent", headers={"x-goog-api-key": key}, json=body,
                       timeout=600)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 400 and "fileData" in r.text or r.status_code == 403:
            raise _Expired()
        if r.status_code != 200:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            mm = re.search(r"\{.*\}", text, re.S)
            if mm:
                return json.loads(mm.group(0))
            raise RuntimeError("Gemini returned an unreadable answer for that video.")
    raise RuntimeError("Gemini is unavailable right now.")


class _Expired(Exception):
    pass


def _with_upload(aid: str, prompt_fn) -> dict:
    st = prepare(aid)
    try:
        return _generate(st, prompt_fn(st))
    except _Expired:
        _update(aid, uri=None)
        st = prepare(aid)
        return _generate(st, prompt_fn(st))


def _norm_moments(rows: Any, key: str) -> list[dict]:
    out = []
    for x in rows or []:
        if isinstance(x, dict) and x.get(key):
            s = _secs(x.get("t"))
            out.append({"t": _fmt_t(s) if s is not None else str(x.get("t") or ""), "s": s, key: str(x[key])})
    return out


def _clean(a: dict, info: dict) -> dict:
    return {"title": str(a.get("title") or "Video"), "type": a.get("type") or "other",
            "platform": info.get("platform") or a.get("platform") or "",  # file metadata beats a visual guess "apps": [str(x) for x in a.get("apps") or []][:12],
            "summary": str(a.get("summary") or ""), "timeline": _norm_moments(a.get("timeline"), "event")[:40],
            "on_screen_text": _norm_moments(a.get("on_screen_text"), "text")[:30],
            "issues": _norm_moments(a.get("issues"), "issue")[:15],
            "steps": [str(x) for x in a.get("steps") or []][:20], "speech": str(a.get("speech") or ""),
            "takeaways": [str(x) for x in a.get("takeaways") or []][:6], "spoken": str(a.get("spoken") or "")}


def analyze(aid: str, force: bool = False) -> dict:
    with _lock(aid, "analyze"):  # a second caller waits here, then gets the cached result
        st = prepare(aid)
        if st.get("analysis") and not force:
            return st
        _publish(aid, {**st, "status": "analyzing"})
        info = st["info"]
        kind_hint = (" (it looks like a phone screen recording)"
                     if info["orientation"] == "portrait" and not info["has_audio"] else "")
        raw = _with_upload(aid, lambda s: ANALYZE_PROMPT.format(
            today=_today(), kind_hint=kind_hint, filename=s["filename"], duration=_fmt_t(info["duration"]),
            orientation=f"{info['width']}x{info['height']} {info['orientation']}",
            device=f"Recorded on {info['device']}." if info.get("device") else ""))
        with _lock(aid):
            st = _update(aid, analysis=_clean(raw, info), analyzed_at=time.time())
        return st


def ask(aid: str, question: str) -> dict:
    st = analyze(aid)
    _publish(aid, {**st, "status": "thinking", "pending_q": question})
    raw = _with_upload(aid, lambda s: ASK_PROMPT.format(today=_today(), summary=(s.get("analysis") or {}).get("summary", ""),
                                                        question=question))
    qa = {"q": question, "a": str(raw.get("answer") or ""), "moments": _norm_moments(raw.get("moments"), "note")[:6],
          "ts": time.time()}
    with _lock(aid):
        st = _state(aid)
        st["qa"] = ([*(st.get("qa") or []), qa])[-12:]
        _save(aid, st)
        st.pop("pending_q", None)
    return {**st, "last": qa}


# ------------------------------------------------------------------ display + tool
def card_data(aid: str, st: dict | None = None, status: str = "ready", error: str = "") -> dict:
    st = st if st is not None else _state(aid)
    m = AR._meta(aid)
    return {"key": f"artifact:{aid}", "video": {
        "id": aid, "filename": m["filename"], "status": status, "error": error, "info": st.get("info"),
        "proxy": bool(proxy_path(aid)), "thumbs": st.get("thumbs") or [], "analysis": st.get("analysis"),
        "qa": st.get("qa") or [], "pending_q": st.get("pending_q") if status == "thinking" else None}}


def _publish(aid: str, st: dict, status: str = "", error: str = "", announce: str = "") -> None:
    data = card_data(aid, st, status or st.get("status") or "ready", error)
    if announce:
        data["announce"] = announce
    store.record_result("video_analyze", None, {"artifact_id": aid}, data)


def _brief(st: dict, last: dict | None = None) -> dict:
    a = st.get("analysis") or {}
    info = st.get("info") or {}
    out: dict[str, Any] = {"artifact_id": st.get("id"), "duration": _fmt_t(info.get("duration")),
                           "recorded_on": info.get("device") or info.get("platform") or None, **a}
    out.pop("spoken", None)
    if last:
        out = {"question": last["q"], "answer": last["a"], "moments": last["moments"],
               "context_summary": a.get("summary")}
    out["note"] = ("The video display is on screen (summary, timeline, on-screen text; timestamps are clickable). "
                   "Speak the answer naturally in 1-3 sentences; don't read lists or timestamps unless asked.")
    return out


def _job(aid: str, question: str) -> dict:
    try:
        st = ask(aid, question) if question else analyze(aid)
        _publish(aid, st, "ready")
        return st
    except Exception as e:
        _publish(aid, _state(aid), "error", str(e)[:300])
        raise


def start_background(aid: str) -> None:
    """Kick off prepare + analysis right after upload so it's ready by the time he asks."""
    with _glock:
        if aid in _running:
            return
        _running.add(aid)

    def run() -> None:
        try:
            _job(aid, "")
        except Exception:
            pass
        finally:
            with _glock:
                _running.discard(aid)
    threading.Thread(target=run, daemon=True, name=f"video-{aid}").start()


def video_analyze(artifact_id: str, question: str = "") -> dict:
    """Watch an attached video and summarise it, or answer a question about it (timestamps included). Long videos
    keep working in the background; the display updates and JARVIS says the result when it lands."""
    aid = artifact_id
    if AR._meta(aid)["kind"] != "video":
        raise ValueError("That attachment isn't a video.")
    st = _state(aid)
    if not question and st.get("analysis") and st.get("version") == len(AR._meta(aid)["versions"]):
        _publish(aid, st, "ready")
        return _brief({**st, "id": aid})
    box: dict[str, Any] = {}

    def run() -> None:
        try:  # waits on the per-video lock if the upload-time analysis is still running, then uses its result
            box["st"] = _job(aid, question)
        except Exception as e:
            box["err"] = e
        finally:
            if box.get("late"):
                st2 = box.get("st") or _state(aid)
                if box.get("err"):
                    _publish(aid, st2, "error", str(box["err"])[:300],
                             announce=f"Sir, I couldn't finish reading that video: {box['err']}"[:240])
                else:
                    last = (st2.get("last") or {}).get("a") if question else None
                    _publish(aid, st2, "ready", announce=last or (st2.get("analysis") or {}).get("spoken")
                             or "That video's analysis is on screen, sir.")

    t = threading.Thread(target=run, daemon=True, name=f"video-ask-{aid}")
    t.start()
    t.join(WAIT_S)
    if t.is_alive():
        box["late"] = True
        info = (_state(aid).get("info") or {})
        return {"started": True, "artifact_id": aid, "duration": _fmt_t(info.get("duration")),
                "note": "Still watching it (long video). The display shows progress and I'll say the answer out loud "
                        "when it's ready. Tell him it's underway in one short line; don't call anything else."}
    if box.get("err"):
        raise box["err"]
    st = box["st"]
    return _brief({**st, "id": aid}, st.get("last") if question else None)
