"""Codebases: map a project (modules, languages, import graph, entry points, key symbols), and read / write code
with every write snapshotted so the HUD can show the diff and undo it.

Safety: work is confined to a project root inside the home folder. Secrets (.env, keys, credentials) and .git
internals are never read or written. Every write keeps the previous content, and Undo only restores it when the
file still holds exactly what JARVIS wrote (so it never clobbers his own later edits).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import store

PROJECTS_DIR = Path(os.environ.get("JARVIS_PROJECTS_DIR", Path.home() / "Documents/Projects"))
CHANGES_DIR = store.STATE_DIR / "code_changes"
MAP_CACHE: dict[str, dict] = {}
NOTES_PATH = store.STATE_DIR / "code_notes.json"

MAX_FILES = 6000
MAX_FILE_BYTES = 1_500_000
READ_CHUNK = 24_000
CARD_FILES_PER_MODULE = 80
MAX_MODULES = 28

IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
               ".mypy_cache", ".ruff_cache", "dist", "dist-electron", "build", "out", "release", "coverage", ".next",
               ".nuxt", ".turbo", ".cache", ".idea", ".vscode", "target", "vendor", "Pods", ".gradle", ".expo",
               ".hermes", "site-packages", ".tox", ".parcel-cache", ".svelte-kit"}
IGNORE_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock",
                "Gemfile.lock", "composer.lock", ".DS_Store", "bun.lockb"}
LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
        ".mjs": "JavaScript", ".cjs": "JavaScript", ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin",
        ".swift": "Swift", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
        ".rb": "Ruby", ".php": "PHP", ".sh": "Shell", ".zsh": "Shell", ".bash": "Shell", ".sql": "SQL",
        ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte", ".lua": "Lua",
        ".dart": "Dart", ".scala": "Scala", ".r": "R", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
        ".toml": "TOML", ".md": "Markdown", ".xml": "XML", ".ipynb": "Notebook"}
CODE_LANGS = set(LANG.values()) - {"JSON", "YAML", "TOML", "Markdown", "XML"}
_SECRET = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa.*|id_ed25519.*|\.npmrc|\.pypirc|"
                     r"credentials(\.json)?|.*secrets?\.(json|ya?ml|toml)|token(s)?\.json|.*\.keystore)$", re.I)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w*, ()]+)|import\s+([\w., ]+))", re.M)
_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|export\s[^'"]*?from\s*|import\s+)['"]([^'"]+)['"]""")
_SYMBOLS = {
    "Python": re.compile(r"^(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", re.M),
    "TypeScript": re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(function|class|const|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M),
    "JavaScript": re.compile(r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(function|class)\s+([A-Za-z_$][\w$]*)", re.M),
    "Go": re.compile(r"^(func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    "Rust": re.compile(r"^\s*pub\s+(fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.M),
}
_ENTRY_NAMES = {"main", "index", "app", "server", "__main__", "cli", "manage", "wsgi", "asgi"}


# ------------------------------------------------------------------ paths & safety
def resolve_root(path: str) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("which project? pass a folder path or a project name")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECTS_DIR / raw
        if not p.exists():  # case-insensitive match on the name
            hits = [d for d in PROJECTS_DIR.iterdir() if d.is_dir() and d.name.lower() == raw.lower()] if PROJECTS_DIR.exists() else []
            p = hits[0] if hits else p
    p = p.resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"no such folder: {p}")
    home = Path.home().resolve()
    if p == home or home not in p.parents:
        raise PermissionError("projects must be a folder inside your home directory (not the home folder itself)")
    return p


def _inside(root: Path, rel: str) -> Path:
    rel = str(rel or "").strip()
    if not rel:
        raise ValueError("file path required")
    p = Path(rel).expanduser()
    p = (p if p.is_absolute() else root / p).resolve()
    if p != root and root not in p.parents:
        raise PermissionError(f"{rel} is outside the project {root.name}")
    rp = p.relative_to(root).as_posix()
    if _SECRET.search(rp) or "/.git/" in f"/{rp}/" or rp.startswith(".git/"):
        raise PermissionError(f"{rp} looks like a secret or git internals; JARVIS doesn't touch those")
    return p


def _is_text(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return b"\x00" not in head


def _list_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             capture_output=True, timeout=15)
        if out.returncode == 0 and out.stdout:
            files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
            return [f for f in files if not any(part in IGNORE_DIRS for part in f.split("/")[:-1])
                    and Path(f).name not in IGNORE_FILES][:MAX_FILES]
    except (OSError, subprocess.TimeoutExpired):
        pass
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if fn in IGNORE_FILES or fn.startswith("."):
                continue
            files.append((Path(dirpath) / fn).relative_to(root).as_posix())
            if len(files) >= MAX_FILES:
                return files
    return files


# ------------------------------------------------------------------ analysis
def _module_of(rel: str, depth: int) -> str:
    parts = rel.split("/")[:-1]
    return "/".join(parts[:depth]) or "(root)"


def _pick_depth(files: list[str]) -> int:
    for depth in (3, 2, 1):
        if len({_module_of(f, depth) for f in files}) <= MAX_MODULES:
            return depth
    return 1


def _resolve_js(src: str, spec: str, known: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(src), spec)).replace("\\", "/")
    for cand in (base, *(base + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")),
                 *(f"{base}/index{e}" for e in (".ts", ".tsx", ".js", ".jsx"))):
        if cand in known:
            return cand
    return None


def _resolve_py(src: str, mod: str, names: str, py_index: dict[str, str]) -> list[str]:
    hits = []
    if mod.startswith("."):
        dots = len(mod) - len(mod.lstrip("."))
        pkg = Path(src).parent
        for _ in range(dots - 1):
            pkg = pkg.parent
        rest = mod.lstrip(".")
        base = (pkg / rest.replace(".", "/")).as_posix() if rest else pkg.as_posix()
        base = "" if base == "." else base
        cands = [base] + [f"{base}/{n.strip()}".lstrip("/") for n in re.split(r"[,()\s]+", names or "") if n.strip() and n.strip() != "*"]
    else:
        cands = [mod.replace(".", "/")]
        cands += [f"{c}/{n.strip()}" for c in cands[:1] for n in re.split(r"[,()\s]+", names or "") if n.strip() and n.strip() != "*"]
    for c in cands:
        c = c.lstrip("./")
        for key in (c, c + "/__init__"):
            if key in py_index:
                hits.append(py_index[key])
                break
        else:  # match on the trailing path (src-layout packages)
            for k, v in py_index.items():
                if c and (k.endswith("/" + c) or k == c):
                    hits.append(v)
                    break
    return hits


def _manifest(root: Path) -> dict:
    info: dict[str, Any] = {}
    pj = root / "package.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text())
            info["package.json"] = {"name": d.get("name"), "description": d.get("description"), "main": d.get("main"),
                                    "scripts": dict(list((d.get("scripts") or {}).items())[:12]),
                                    "dependencies": sorted((d.get("dependencies") or {}).keys())[:30],
                                    "devDependencies": sorted((d.get("devDependencies") or {}).keys())[:20]}
        except Exception:
            pass
    for name in ("pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt", "Gemfile", "build.gradle"):
        for p in [root / name, *root.glob(f"*/{name}")][:3]:
            if p.exists() and p.stat().st_size < 60_000:
                info[p.relative_to(root).as_posix()] = p.read_text(errors="replace")[:1500]
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = root / name
        if p.exists():
            info["readme_excerpt"] = p.read_text(errors="replace")[:2500]
            break
    return info


def analyze(root: Path) -> dict:
    files = _list_files(root)
    known = set(files)
    py_index = {f[:-3]: f for f in files if f.endswith(".py")}
    depth = _pick_depth(files)
    per_file: dict[str, dict] = {}
    lang_loc: Counter = Counter()
    lang_files: Counter = Counter()
    edges_f: Counter = Counter()
    indeg: Counter = Counter()
    total_loc = 0
    for rel in files:
        p = root / rel
        ext = p.suffix.lower()
        lang = LANG.get(ext)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        entry = {"path": rel, "name": p.name, "lang": lang or "Other", "loc": 0, "size": size, "symbols": []}
        per_file[rel] = entry
        if not lang or size > MAX_FILE_BYTES or _SECRET.search(rel) or not _is_text(p):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        loc = sum(1 for ln in text.splitlines() if ln.strip())
        entry["loc"] = loc
        total_loc += loc
        lang_loc[lang] += loc
        lang_files[lang] += 1
        sym_re = _SYMBOLS.get(lang)
        if sym_re:
            entry["symbols"] = [f"{k} {n}" for k, n in sym_re.findall(text)][:14]
        targets: list[str] = []
        if lang == "Python":
            for frm, names, imp in _PY_IMPORT.findall(text):
                if frm:
                    targets += _resolve_py(rel, frm, names, py_index)
                else:
                    for mod in imp.split(","):
                        targets += _resolve_py(rel, mod.strip().split(" as ")[0], "", py_index)
        elif lang in ("TypeScript", "JavaScript", "Vue", "Svelte"):
            targets = [t for t in (_resolve_js(rel, s, known) for s in _JS_IMPORT.findall(text)) if t]
        for t in set(targets):
            if t != rel:
                edges_f[(rel, t)] += 1
                indeg[t] += 1
    # modules
    mods: dict[str, dict] = defaultdict(lambda: {"files": [], "loc": 0, "langs": Counter()})
    for rel, e in per_file.items():
        m = mods[_module_of(rel, depth)]
        m["files"].append(e)
        m["loc"] += e["loc"]
        if e["lang"] in CODE_LANGS:
            m["langs"][e["lang"]] += e["loc"]
    mod_edges: Counter = Counter()
    for (a, b), n in edges_f.items():
        ma, mb = _module_of(a, depth), _module_of(b, depth)
        if ma != mb:
            mod_edges[(ma, mb)] += n
    script_langs = CODE_LANGS - {"HTML", "CSS", "SQL"}
    entry_points = sorted({f for f in files if Path(f).stem.lower() in _ENTRY_NAMES and LANG.get(Path(f).suffix.lower()) in script_langs},
                          key=lambda f: (f.count("/"), f))[:12]
    manifest = _manifest(root)
    pj = manifest.get("package.json") or {}
    if pj.get("main") and pj["main"] not in entry_points:
        entry_points.insert(0, pj["main"])
    try:
        branch = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, timeout=5).stdout.strip()
        log = subprocess.run(["git", "-C", str(root), "log", "-5", "--pretty=%h %s"], capture_output=True, text=True,
                             timeout=5).stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        branch, log = "", []
    modules = []
    for name, m in sorted(mods.items(), key=lambda kv: -kv[1]["loc"]):
        fl = sorted(m["files"], key=lambda e: -e["loc"])
        modules.append({"id": name, "name": name, "file_count": len(fl), "loc": m["loc"],
                        "lang": (m["langs"].most_common(1) or [("Other", 0)])[0][0],
                        "files": [{**e, "imported_by": indeg.get(e["path"], 0)} for e in fl[:CARD_FILES_PER_MODULE]]})
    langs = [{"lang": l, "loc": n, "files": lang_files[l], "pct": round(100 * n / max(1, total_loc), 1)}
             for l, n in lang_loc.most_common(10)]
    hubs = [{"path": f, "imported_by": n} for f, n in indeg.most_common(8)]
    largest = [{"path": e["path"], "loc": e["loc"]} for e in sorted(per_file.values(), key=lambda e: -e["loc"])[:8]]
    return {"root": str(root), "name": root.name, "branch": branch, "recent_commits": log, "scanned_at": time.time(),
            "total_files": len(files), "truncated": len(files) >= MAX_FILES, "total_loc": total_loc,
            "languages": langs, "modules": modules, "module_depth": depth,
            "edges": [{"source": a, "target": b, "weight": n} for (a, b), n in mod_edges.most_common(80)],
            "entry_points": entry_points, "hubs": hubs, "largest": largest, "manifest": manifest}


# ------------------------------------------------------------------ notes (the model's plain-English map)
def _notes_all() -> dict:
    try:
        return json.loads(NOTES_PATH.read_text())
    except Exception:
        return {}


def _notes(root: Path) -> dict:
    return _notes_all().get(str(root), {})


def _card(root: Path, a: dict) -> dict:
    return {"key": f"codebase:{root}", **a, "notes": _notes(root)}


def _brief(a: dict, notes: dict) -> dict:
    return {
        "root": a["root"], "name": a["name"], "branch": a["branch"], "total_files": a["total_files"],
        "total_loc": a["total_loc"], "languages": [f"{l['lang']} {l['pct']}%" for l in a["languages"][:6]],
        "modules": [{"name": m["name"], "files": m["file_count"], "loc": m["loc"], "lang": m["lang"],
                     "top_files": [f"{f['name']} ({f['loc']} loc; {', '.join(f['symbols'][:5])})" for f in m["files"][:5]]}
                    for m in a["modules"][:MAX_MODULES]],
        "module_dependencies": [f"{e['source']} -> {e['target']} ({e['weight']})" for e in a["edges"][:30]],
        "entry_points": a["entry_points"], "most_imported": a["hubs"], "recent_commits": a["recent_commits"],
        "manifest": {k: (v if not isinstance(v, str) else v[:800]) for k, v in a["manifest"].items()},
        "existing_notes": bool(notes.get("summary")),
        "next": "Read the key files if needed, then call code_annotate ONCE with a plain-English summary and one "
                "line per module so the map on screen explains itself.",
    }


def code_projects() -> dict:
    out = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir(), key=lambda d: -d.stat().st_mtime):
            if d.is_dir() and not d.name.startswith("."):
                out.append({"name": d.name, "path": str(d), "git": (d / ".git").exists(),
                            "modified": time.strftime("%Y-%m-%d", time.localtime(d.stat().st_mtime))})
    return {"projects_dir": str(PROJECTS_DIR), "projects": out[:60]}


def code_map(path: str, refresh: bool = False, show: bool = True) -> dict:
    root = resolve_root(path)
    a = MAP_CACHE.get(str(root))
    if refresh or not a or time.time() - a["scanned_at"] > 600:
        a = analyze(root)
        MAP_CACHE[str(root)] = a
    if show:
        store.record_result("code_map", None, {"path": str(root)}, _card(root, a))
    return _brief(a, _notes(root))


def code_annotate(path: str, summary: str, modules: dict[str, str] | None = None,
                  architecture: list[str] | None = None, how_to_run: str = "") -> dict:
    root = resolve_root(path)
    allnotes = _notes_all()
    cur = allnotes.get(str(root), {})
    cur.update({"summary": summary.strip()[:3000], "updated_at": time.time()})
    if modules:
        cur["modules"] = {**cur.get("modules", {}), **{str(k): str(v)[:400] for k, v in modules.items()}}
    if architecture:
        cur["architecture"] = [str(x)[:300] for x in architecture][:10]
    if how_to_run:
        cur["how_to_run"] = how_to_run.strip()[:1500]
    allnotes[str(root)] = cur
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(json.dumps(allnotes, indent=1))
    a = MAP_CACHE.get(str(root)) or analyze(root)
    MAP_CACHE[str(root)] = a
    store.record_result("code_annotate", None, {"path": str(root)}, _card(root, a))
    return {"ok": True, "root": str(root), "modules_annotated": len(modules or {})}


def code_read(path: str, file: str, offset: int = 0, limit: int = READ_CHUNK, show: bool = False) -> dict:
    root = resolve_root(path)
    p = _inside(root, file)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {file}")
    if not _is_text(p):
        return {"file": p.relative_to(root).as_posix(), "error": "binary file"}
    text = p.read_text(errors="replace")
    offset, limit = max(0, int(offset)), max(2000, min(int(limit), 80_000))
    rel = p.relative_to(root).as_posix()
    out = {"root": str(root), "file": rel, "total_chars": len(text), "lines": text.count("\n") + 1, "offset": offset,
           "content": text[offset:offset + limit]}
    if offset + limit < len(text):
        out["next_offset"] = offset + limit
    if show:
        store.record_result("code_read", None, {"path": str(root), "file": rel},
                            {"key": f"codefile:{root}:{rel}", "root": str(root), "file": rel,
                             "language": p.suffix.lstrip("."), "text": text[:300_000], "lines": out["lines"]})
    return out


# ------------------------------------------------------------------ writes (snapshotted, undoable)
def _diff(before: str | None, after: str | None, rel: str) -> tuple[str, int, int]:
    a = (before or "").splitlines(keepends=True)
    b = (after or "").splitlines(keepends=True)
    lines = list(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}", n=3))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    text = "".join(lines)
    return (text[:60_000] + ("\n… diff truncated" if len(text) > 60_000 else "")), added, removed


def _changes(root: Path, limit: int = 12) -> list[dict]:
    out = []
    if CHANGES_DIR.exists():
        for f in CHANGES_DIR.glob("*.json"):
            try:
                c = json.loads(f.read_text())
            except Exception:
                continue
            if c.get("root") == str(root):
                out.append({k: c[k] for k in ("id", "file", "ts", "note", "diff", "added", "removed", "status", "created")})
    return sorted(out, key=lambda c: -c["ts"])[:limit]


def _changes_card(root: Path) -> dict:
    return {"key": f"codechanges:{root}", "root": str(root), "name": root.name, "changes": _changes(root)}


def _write(root: Path, p: Path, new: str, note: str, source: str) -> dict:
    rel = p.relative_to(root).as_posix()
    before = p.read_text(errors="replace") if p.exists() else None
    if before == new:
        return {"file": rel, "unchanged": True}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new)
    diff, added, removed = _diff(before, new, rel)
    cid = "chg_" + uuid.uuid4().hex[:10]
    CHANGES_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": cid, "root": str(root), "file": rel, "ts": time.time(), "note": note[:300], "source": source,
           "before": before, "after": new, "diff": diff, "added": added, "removed": removed, "status": "applied",
           "created": before is None}
    (CHANGES_DIR / f"{cid}.json").write_text(json.dumps(rec))
    store.audit({"kind": "code_write", "change": cid, "root": str(root), "file": rel, "added": added,
                 "removed": removed, "source": source, "note": note[:200]})
    MAP_CACHE.pop(str(root), None)
    store.record_result("code_change", None, {"path": str(root), "file": rel}, _changes_card(root))
    return {"change_id": cid, "file": rel, "lines_added": added, "lines_removed": removed, "created": before is None}


def code_write(path: str, file: str, content: str, note: str = "") -> dict:
    root = resolve_root(path)
    return _write(root, _inside(root, file), content, note or "written by JARVIS", "model")


def code_edit(path: str, file: str, old: str, new: str, replace_all: bool = False, note: str = "") -> dict:
    root = resolve_root(path)
    p = _inside(root, file)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {file} (use code_write to create it)")
    text = p.read_text(errors="replace")
    n = text.count(old) if old else 0
    if n == 0:
        raise ValueError("old text not found; it must match the file exactly (code_read it first)")
    if n > 1 and not replace_all:
        raise ValueError(f"old text matches {n} places; include more surrounding lines or set replace_all")
    out = _write(root, p, text.replace(old, new) if replace_all else text.replace(old, new, 1),
                 note or "edited by JARVIS", "model")
    return {**out, "replacements": n if replace_all else 1}


def undo_change(change_id: str, source: str = "code_click") -> dict:
    if not re.fullmatch(r"chg_[0-9a-f]{10}", str(change_id or "")):
        raise ValueError("bad change id")
    f = CHANGES_DIR / f"{change_id}.json"
    rec = json.loads(f.read_text())
    if rec["status"] != "applied":
        raise ValueError("that change was already undone")
    root = Path(rec["root"])
    p = _inside(root, rec["file"])
    cur = p.read_text(errors="replace") if p.exists() else None
    if cur != rec["after"]:
        raise ValueError(f"{rec['file']} has changed since JARVIS wrote it; undo it by hand (or with git)")
    if rec["before"] is None:
        p.unlink()
    else:
        p.write_text(rec["before"])
    rec["status"] = "undone"
    f.write_text(json.dumps(rec))
    store.audit({"kind": "code_undo", "change": change_id, "file": rec["file"], "source": source})
    MAP_CACHE.pop(str(root), None)
    return {**_changes_card(root), "text": f"Reverted {rec['file']}."}


# ------------------------------------------------------------------ HUD click ops (Core allow-list)
def click_file(path: str, file: str) -> dict:
    r = code_read(path, file, 0, 80_000)
    return {"file": r["file"], "text": r.get("content", r.get("error", "")), "lines": r.get("lines", 0),
            "truncated": "next_offset" in r}


def click_refresh(path: str) -> dict:
    root = resolve_root(path)
    a = analyze(root)
    MAP_CACHE[str(root)] = a
    return _card(root, a)


def click_open_folder(path: str) -> dict:
    """Folder dropped on the HUD: map it and push the card."""
    code_map(path, refresh=True, show=True)
    return {"text": "Mapping project."}


CLICK_OPS = {"code_file": click_file, "code_refresh": click_refresh, "code_undo": undo_change,
             "code_open_folder": click_open_folder}
