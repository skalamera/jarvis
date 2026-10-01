"""Image and video generation with Gemini (Nano Banana image models + Veo), saved as HUD workspace files.

image_generate(prompt) -> a new image file (or a new version of an existing image when editing it), shown as a card.
video_generate(prompt) -> Veo runs in the background (a minute or two); a progress card shows right away and becomes the
playable video when it lands. Jobs persist in STATE_DIR/gen_jobs.json so a server restart resumes polling.
The key comes from GEMINI_API_KEY (env or ~/.hermes/.env), never from the repo.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import artifacts as AR
from . import store

API = "https://generativelanguage.googleapis.com/v1beta"
IMAGE_MODELS = {"fast": "gemini-3.1-flash-image", "best": "gemini-3-pro-image"}
VIDEO_MODELS = {"lite": "veo-3.1-lite-generate-preview", "fast": "veo-3.1-fast-generate-preview",
                "best": "veo-3.1-generate-preview"}
IMAGE_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
VIDEO_RATIOS = {"16:9", "9:16"}
JOBS = store.STATE_DIR / "gen_jobs.json"
POLL_S, VIDEO_TIMEOUT_S = 6.0, 15 * 60
_lock = threading.Lock()
_running: set[str] = set()


def _key() -> str:
    v = os.environ.get("GEMINI_API_KEY", "").strip()
    if v:
        return v
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in f.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*)", line)
            if m and m[1].strip():
                return m[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("GEMINI_API_KEY is not configured")


def _h() -> dict:
    return {"x-goog-api-key": _key()}


def _err(r: httpx.Response) -> str:
    try:
        e = r.json().get("error") or {}
        return f"Gemini {r.status_code}: {e.get('message') or r.text[:300]}"
    except Exception:
        return f"Gemini {r.status_code}: {r.text[:300]}"


def _slug(prompt: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())
    return "-".join(words[:n]) or "generated"


def _progress(tool: str, aid: str, kind: str, prompt: str, **extra: Any) -> None:
    """A placeholder card keyed like the final artifact card, so the result replaces it in place."""
    store.record_result(tool, None, {"prompt": prompt[:200]}, {
        "key": f"artifact:{aid}", "generating": {"kind": kind, "prompt": prompt, "started": time.time(), **extra}})


def _failed(tool: str, aid: str, kind: str, prompt: str, error: str) -> None:
    store.record_result(tool, None, {"prompt": prompt[:200]}, {
        "key": f"artifact:{aid}", "generating": {"kind": kind, "prompt": prompt, "error": error[:300]}})


def _image_part(artifact_id: str) -> dict:
    m = AR._meta(artifact_id)
    if m["kind"] != "image":
        raise ValueError(f"{m['filename']} is not an image")
    data = AR.current_path(m).read_bytes()
    return {"inlineData": {"mimeType": m["mime"] or "image/png", "data": base64.b64encode(data).decode()}}


# ------------------------------------------------------------------ images
def image_generate(prompt: str, aspect_ratio: str = "", source_artifact_id: str = "", quality: str = "fast",
                   filename: str = "") -> dict:
    """Generate an image from a prompt. With source_artifact_id the image is reworked (new version of that file)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("describe the image to generate")
    model = IMAGE_MODELS.get(quality, IMAGE_MODELS["fast"])
    parts: list[dict] = [{"text": prompt}]
    editing = bool(source_artifact_id)
    if editing:
        parts.append(_image_part(source_artifact_id))
    aid = source_artifact_id or "art_" + uuid.uuid4().hex[:10]
    if not editing:
        _progress("image_generate", aid, "image", prompt, model=model)
    cfg: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
    ar = aspect_ratio.strip()
    if ar in IMAGE_RATIOS:
        cfg["imageConfig"] = {"aspectRatio": ar}
    try:
        r = httpx.post(f"{API}/models/{model}:generateContent", headers=_h(), timeout=180,
                       json={"contents": [{"parts": parts}], "generationConfig": cfg})
        if r.status_code != 200:
            raise RuntimeError(_err(r))
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        out = [p for p in (cand.get("content") or {}).get("parts", []) if "inlineData" in p]
        note = " ".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if p.get("text"))
        if not out:
            why = cand.get("finishReason") or (j.get("promptFeedback") or {}).get("blockReason") or "no image returned"
            raise RuntimeError(f"Gemini didn't return an image ({why}). {note[:200]}".strip())
        data = base64.b64decode(out[-1]["inlineData"]["data"])
        mime = out[-1]["inlineData"].get("mimeType") or "image/png"
    except Exception as e:
        if not editing:
            _failed("image_generate", aid, "image", prompt, str(e))
        raise
    if editing:
        m = AR._meta(aid)
        if mimetypes.guess_extension(mime) not in (m["ext"], ".jpg" if m["ext"] == ".jpeg" else None):
            data = _convert(data, m["ext"])
        m = AR._new_version(m, data, "model", f"AI: {prompt[:150]}")
    else:
        ext = mimetypes.guess_extension(mime) or ".png"
        ext = ".jpg" if ext in (".jpe", ".jpeg") else ext
        m = AR.save_upload(filename or f"{_slug(prompt)}{ext}", data, mime, aid=aid, source="generated",
                           note=f"Generated: {prompt[:150]}")
    AR._show("image_generate", {"prompt": prompt[:200]}, m)
    return {**AR.brief(m), "model": model, "model_note": note[:300] or None,
            "shown": "the image is on screen; describe it briefly, don't read out the prompt"}


def _convert(data: bytes, ext: str) -> bytes:
    import io

    from PIL import Image
    fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(ext, "PNG")
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        (im.convert("RGB") if fmt == "JPEG" else im).save(buf, fmt, quality=92)
        return buf.getvalue()


# ------------------------------------------------------------------ video (background jobs)
def _jobs() -> dict:
    try:
        return json.loads(JOBS.read_text())
    except Exception:
        return {}


def _save_job(job: dict) -> None:
    with _lock:
        j = _jobs()
        j[job["aid"]] = job
        cutoff = time.time() - 7 * 86400
        j = {k: v for k, v in j.items() if v.get("started", 0) > cutoff}
        JOBS.parent.mkdir(parents=True, exist_ok=True)
        JOBS.write_text(json.dumps(j, indent=1))


def video_generate(prompt: str, aspect_ratio: str = "16:9", duration_s: int = 8, image_artifact_id: str = "",
                   quality: str = "fast", filename: str = "") -> dict:
    """Start a Veo video (returns immediately; the card plays it when it's ready, usually 1-3 minutes)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("describe the video to generate")
    model = VIDEO_MODELS.get(quality, VIDEO_MODELS["fast"])
    dur = min((4, 6, 8), key=lambda d: abs(d - int(duration_s or 8)))
    inst: dict[str, Any] = {"prompt": prompt}
    if image_artifact_id:
        p = _image_part(image_artifact_id)["inlineData"]
        inst["image"] = {"bytesBase64Encoded": p["data"], "mimeType": p["mimeType"]}
    params = {"aspectRatio": aspect_ratio if aspect_ratio in VIDEO_RATIOS else "16:9", "durationSeconds": dur}
    r = httpx.post(f"{API}/models/{model}:predictLongRunning", headers=_h(), timeout=60,
                   json={"instances": [inst], "parameters": params})
    if r.status_code != 200:
        raise RuntimeError(_err(r))
    aid = "art_" + uuid.uuid4().hex[:10]
    job = {"aid": aid, "op": r.json()["name"], "prompt": prompt, "model": model, "duration": dur,
           "aspect": params["aspectRatio"], "filename": filename or f"{_slug(prompt)}.mp4", "started": time.time(),
           "status": "running"}
    _save_job(job)
    _progress("video_generate", aid, "video", prompt, model=model, duration=dur, aspect=params["aspectRatio"],
              eta_s=90 if "lite" in model or "fast" in model else 150)
    _spawn(job)
    return {"started": True, "artifact_id": aid, "model": model, "duration_s": dur,
            "note": "Generating in the background (usually 1-3 minutes). A progress display is on screen and turns into "
                    "the video when it's ready. Don't wait for it or call anything else; just tell him it's underway."}


def video_status(artifact_id: str = "") -> dict:
    jobs = sorted(_jobs().values(), key=lambda j: j["started"], reverse=True)
    if artifact_id:
        jobs = [j for j in jobs if j["aid"] == artifact_id]
    return {"jobs": [{k: j.get(k) for k in ("aid", "prompt", "status", "error", "model", "duration")} |
                     {"elapsed_s": round(time.time() - j["started"])} for j in jobs[:8]]}


def _spawn(job: dict) -> None:
    with _lock:
        if job["aid"] in _running:
            return
        _running.add(job["aid"])
    threading.Thread(target=_watch, args=(job,), daemon=True, name=f"veo-{job['aid']}").start()


def _watch(job: dict) -> None:
    try:
        while time.time() - job["started"] < VIDEO_TIMEOUT_S:
            time.sleep(POLL_S)
            try:
                r = httpx.get(f"{API}/{job['op']}", headers=_h(), timeout=30)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                if r.status_code in (429, 500, 502, 503, 504):
                    continue
                raise RuntimeError(_err(r))
            j = r.json()
            if not j.get("done"):
                continue
            if j.get("error"):
                raise RuntimeError(j["error"].get("message") or "Veo failed")
            resp = (j.get("response") or {}).get("generateVideoResponse") or {}
            samples = resp.get("generatedSamples") or []
            if not samples:
                why = resp.get("raiFilteredReasons") or ["no video returned (likely filtered)"]
                raise RuntimeError("; ".join(why)[:300])
            uri = samples[0]["video"]["uri"]
            v = httpx.get(uri, headers=_h(), timeout=300, follow_redirects=True)
            if v.status_code != 200:
                raise RuntimeError(_err(v))
            m = AR.save_upload(job["filename"], v.content, "video/mp4", aid=job["aid"], source="generated",
                               note=f"Generated: {job['prompt'][:150]}")
            AR._show("video_generate", {"prompt": job["prompt"][:200]}, m)
            _save_job({**job, "status": "done", "finished": time.time()})
            return
        raise RuntimeError("timed out waiting for Veo")
    except Exception as e:
        _save_job({**job, "status": "failed", "error": str(e)[:300]})
        _failed("video_generate", job["aid"], "video", job["prompt"], str(e))
    finally:
        with _lock:
            _running.discard(job["aid"])


def resume_jobs() -> None:
    """On server start: keep polling any video still running from before a restart."""
    for job in _jobs().values():
        if job.get("status") == "running" and time.time() - job["started"] < VIDEO_TIMEOUT_S:
            _spawn(job)
