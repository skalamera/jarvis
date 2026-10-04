"""Saved subjects for generation ("my car", "my dog", "me"): reference photos plus a precise visual description.
image_generate / video_generate take subject="my car" and get the real photos, so the result shows HIS car, not a
generic one. Photos are copied out of the workspace (later edits or discards of the upload don't affect them)."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx

from . import artifacts as AR
from . import store

DIR = store.STATE_DIR / "subjects"
MAX_PHOTOS = 3          # Veo takes up to 3 reference images
DESCRIBE_MODEL = "gemini-3.8-flash"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "subject"


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    s = re.sub(r"\b(my|the|a|an|our|his|her)\b", " ", s)
    return " ".join(s.split())


def _load(slug: str) -> dict:
    return json.loads((DIR / slug / "meta.json").read_text())


def _all() -> list[dict]:
    if not DIR.exists():
        return []
    out = []
    for d in sorted(DIR.iterdir()):
        try:
            out.append(_load(d.name))
        except Exception:
            continue
    return out


def find(name: str) -> dict | None:
    """Resolve "my car" / "the CL600" / "car" to a saved subject (exact name or alias, then substring)."""
    q = _norm(name)
    if not q:
        return None
    subs = _all()
    for s in subs:
        if q in {_norm(x) for x in [s["name"], *s.get("aliases", [])]}:
            return s
    for s in subs:
        keys = {_norm(x) for x in [s["name"], *s.get("aliases", [])]}
        if any(q in k or k in q for k in keys if k):
            return s
    return None


def photos(s: dict) -> list[tuple[bytes, str]]:
    out = []
    for f in s.get("photos", []):
        p = DIR / s["slug"] / f
        if p.exists():
            out.append((p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg"))
    return out


def sheet(s: dict) -> tuple[bytes, str] | None:
    """Optional multi-angle collage of the subject (image models get it in addition to the photos)."""
    f = s.get("sheet")
    p = DIR / s["slug"] / f if f else None
    return (p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png") if p and p.exists() else None


def set_sheet(name: str, path: str) -> dict:
    s = resolve(name)
    src = Path(path).expanduser()
    fn = "sheet" + (src.suffix.lower() or ".png")
    shutil.copyfile(src, DIR / s["slug"] / fn)
    s["sheet"] = fn
    (DIR / s["slug"] / "meta.json").write_text(json.dumps(s, indent=1))
    return {"sheet": fn}


def _describe(imgs: list[tuple[bytes, str]], name: str, hint: str) -> str:
    """One dense visual description (for Veo text prompts and as a check). Best effort."""
    from .generate import API, _h
    parts: list[dict] = [{"text": (
        f"These photos show the same subject, which the owner calls '{name}'. {('Owner notes: ' + hint) if hint else ''}\n"
        "Write ONE dense paragraph (max 70 words) describing exactly how it looks so an artist could reproduce it: "
        "for a vehicle give make/model/generation if identifiable, body style, exact paint colour and finish, wheels "
        "(design, finish, size impression), ride height/stance, trim, glass tint, lights, any distinctive mods or marks. "
        "No background, no speculation beyond what is visible.")}]
    for data, mime in imgs:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    try:
        r = httpx.post(f"{API}/models/{DESCRIBE_MODEL}:generateContent", headers=_h(), timeout=90,
                       json={"contents": [{"parts": parts}]})
        cand = (r.json().get("candidates") or [{}])[0]
        return " ".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()[:900]
    except Exception:
        return hint


def subject_save(name: str, artifact_ids: list[str], description: str = "", aliases: list[str] | None = None,
                 replace: bool = False) -> dict:
    """Remember photos of one of his things under a name. Adds to an existing subject unless replace=True."""
    name = (name or "").strip()
    if not name:
        raise ValueError("what should I call it?")
    ids = [a for a in (artifact_ids or []) if a]
    if not ids:
        raise ValueError("attach or point me at a photo of it first")
    existing = find(name)
    slug = existing["slug"] if existing else _slug(_norm(name) or name)
    d = DIR / slug
    if replace and d.exists():
        shutil.rmtree(d)
        existing = None
    d.mkdir(parents=True, exist_ok=True)
    meta = existing or {"slug": slug, "name": name, "aliases": [], "photos": [], "created": time.time()}
    for aid in ids:
        m = AR._meta(aid)
        if m["kind"] != "image":
            raise ValueError(f"{m['filename']} isn't a photo")
        src = AR.current_path(m)
        fn = f"p{int(time.time())}-{uuid.uuid4().hex[:6]}{src.suffix.lower() or '.jpg'}"
        shutil.copyfile(src, d / fn)
        meta["photos"].append(fn)
    # keep the newest MAX_PHOTOS
    for old in meta["photos"][:-MAX_PHOTOS]:
        (d / old).unlink(missing_ok=True)
    meta["photos"] = meta["photos"][-MAX_PHOTOS:]
    meta["aliases"] = sorted({*meta.get("aliases", []), *(aliases or [])} - {meta["name"]})
    meta["notes"] = description.strip() or meta.get("notes", "")
    if not meta.get("description_locked"):  # his own wording wins over the auto description
        meta["description"] = _describe(photos(meta), meta["name"], meta["notes"]) or meta["notes"]
    meta["updated"] = time.time()
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    store.audit({"kind": "subject_save", "subject": slug, "photos": len(meta["photos"]), "source": "model"})
    return {"saved": meta["name"], "aliases": meta["aliases"], "photos": len(meta["photos"]),
            "description": meta["description"],
            "note": f"From now on, pass subject='{meta['name']}' to image_generate / video_generate when he mentions it."}


def subject_list() -> dict:
    return {"subjects": [{"name": s["name"], "aliases": s.get("aliases", []), "photos": len(s.get("photos", [])),
                          "description": s.get("description", "")[:200]} for s in _all()]}


def subject_forget(name: str) -> dict:
    s = find(name)
    if not s:
        raise ValueError(f"I don't have anything saved as '{name}'")
    trash = store.STATE_DIR / "subjects_trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(DIR / s["slug"]), trash / f"{s['slug']}-{int(time.time())}")
    store.audit({"kind": "subject_forget", "subject": s["slug"], "source": "model"})
    return {"forgot": s["name"], "note": "moved to subjects_trash (recoverable)"}


def resolve(subject: str) -> dict:
    """For the generators: the subject or a clear error listing what is saved."""
    s = find(subject)
    if not s:
        names = [x["name"] for x in _all()]
        raise ValueError(f"I don't have photos of '{subject}' saved yet" +
                         (f" (saved: {', '.join(names)})" if names else "") +
                         ". Ask him to attach a photo and say what it is.")
    if not photos(s):
        raise ValueError(f"the photos for '{s['name']}' are missing; ask him to attach one again")
    return s


def ref_text(s: dict) -> str:
    multi = " The collage shows it from many angles; use it to get every side right." if s.get("sheet") else ""
    return (f"IMPORTANT: '{s['name']}' must be the exact subject shown in the reference photo(s): {s['description']}{multi} "
            "Reproduce it faithfully (same colour, wheels, stance, details); do not substitute a different model or "
            "variant. Only the scene, angle, lighting and action change.")
