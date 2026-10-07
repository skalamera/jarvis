"""career-ops (github.com/career-ops-hq/career-ops) inside JARVIS.

The project lives at ~/Documents/Projects/career-ops (its own repo, updated with its own updater). JARVIS runs its
local web app (loopback only, port 3077) on demand and reads the same files it does for the sidebar summary.
Nothing here applies to jobs or sends anything: career-ops itself never auto-submits; JARVIS only adds URLs to its
inbox (data/pipeline.md) when he asks."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from . import store

ROOT = Path(os.environ.get("CAREER_OPS_ROOT", Path.home() / "Documents/Projects/career-ops"))
PORT = int(os.environ.get("CAREER_OPS_PORT", "3077"))
URL = f"http://127.0.0.1:{PORT}"
LOG = store.STATE_DIR / "career-ops-web.log"
_proc: subprocess.Popen | None = None


def _up() -> bool:
    try:
        return httpx.get(URL + "/api/version", timeout=1.5).status_code < 500
    except Exception:
        return False


def ensure_server() -> dict:
    """Start the career-ops web app if it isn't running (builds once if needed)."""
    global _proc
    if not (ROOT / "web").exists():
        raise RuntimeError(f"career-ops isn't installed at {ROOT}.")
    if _up():
        return {"url": URL, "running": True}
    node = shutil.which("node") or "/usr/local/bin/node"
    web = ROOT / "web"
    if not (web / ".next" / "BUILD_ID").exists():
        subprocess.run(["npm", "run", "build"], cwd=web, check=True, capture_output=True, timeout=600)
    env = {**os.environ, "PORT": str(PORT)}
    _proc = subprocess.Popen([node, "server.mjs", "start", "-p", str(PORT)], cwd=web, env=env,
                             stdout=LOG.open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(60):
        if _up():
            return {"url": URL, "running": True, "started": True}
        time.sleep(0.5)
    raise RuntimeError(f"career-ops web didn't start (see {LOG}).")


def _get(path: str) -> dict:
    return httpx.get(URL + path, timeout=15).json()


def _score(v) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def summary() -> dict:
    ensure_server()
    pipe = _get("/api/pipeline")
    doc = _get("/api/doctor")
    apps = pipe.get("applications") or []
    by: dict[str, int] = {}
    for a in apps:
        s = (a.get("status") or a.get("estado") or "Unknown").strip() or "Unknown"
        by[s] = by.get(s, 0) + 1
    top = sorted((a for a in apps if _score(a.get("score")) is not None), key=lambda a: -(_score(a.get("score")) or 0))[:8]
    recent = sorted(apps, key=lambda a: str(a.get("date") or ""), reverse=True)[:8]
    return {"url": URL, "onboarding": doc.get("onboardingNeeded", False), "missing": doc.get("missing") or [],
            "inbox": len(pipe.get("inbox") or []), "inbox_items": (pipe.get("inbox") or [])[:8],
            "applications": len(apps), "by_status": by, "top": top, "recent": recent}


def add_job(url: str, company: str = "", role: str = "") -> dict:
    """Queue a job posting URL in career-ops' inbox (data/pipeline.md → Pending) for evaluation."""
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise ValueError("That isn't a job posting URL.")
    p = ROOT / "data" / "pipeline.md"
    text = p.read_text() if p.exists() else "# Pipeline\n\n## Pending\n\n## Processed\n"
    if url in text:
        return {"status": "already_queued", "url": url}
    line = "- [ ] " + " | ".join(x for x in (url, company.strip(), role.strip()) if x)
    m = re.search(r"^##\s*(Pending|Pendientes)\s*$", text, re.M)
    if m:
        i = text.find("\n", m.end()) + 1
        text = text[:i] + ("\n" if not text[i:].startswith("\n") else "") + line + "\n" + text[i:].lstrip("\n")
    else:
        text = text.rstrip() + "\n\n## Pending\n\n" + line + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return {"status": "queued", "url": url}


def show(view: str = "") -> dict:
    d = summary()
    store.record_result("career_ops", None, {"view": view}, {"key": "career_ops", "kind": "career_ops", **d, "view": view})
    return {"shown": True, **{k: d[k] for k in ("onboarding", "missing", "inbox", "applications", "by_status")},
            "top": [{"company": a.get("company"), "role": a.get("role"), "score": a.get("score"), "status": a.get("status")} for a in d["top"][:5]]}


CLICK_OPS = {"careerops_summary": summary, "careerops_start": ensure_server, "careerops_add": add_job}
