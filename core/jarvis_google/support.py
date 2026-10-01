"""Hadrius support model blueprint: a precomputed, source-grounded knowledge base (no runtime research).

The blueprint lives OUTSIDE the repo at STATE_DIR/support_blueprint.json (internal company architecture; the repo is
public). It was built once from the triage-flow PDF, the webhook-service codebase, the Gumloop skills and the
Tier 1 / Tier 2 agent instructions. `support_model` hands the model the narrative plus whichever section it asks for,
and records a `support_blueprint` card the HUD renders as an interactive explainer (diagrams, flows, charts).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import store

PATH = Path(os.environ.get("JARVIS_SUPPORT_BLUEPRINT", store.STATE_DIR / "support_blueprint.json"))
SECTIONS = ("overview", "architecture", "layers", "tiers", "flows", "routing", "lifecycle", "budgets", "payloads",
            "guardrails", "faq", "glossary")
_cache: dict[str, Any] = {"mtime": None, "data": None}


def load() -> dict:
    """The blueprint, reloaded only when the file changes."""
    if not PATH.exists():
        raise FileNotFoundError("The support blueprint has not been built yet (support_blueprint.json is missing).")
    mt = PATH.stat().st_mtime
    if _cache["mtime"] != mt:
        _cache["data"], _cache["mtime"] = json.loads(PATH.read_text()), mt
    return _cache["data"]


def available() -> bool:
    try:
        load()
        return True
    except Exception:
        return False


def _section(bp: dict, name: str) -> Any:
    if name == "overview":
        return {k: bp.get(k) for k in ("title", "subtitle", "summary", "stats")}
    return bp.get(name)


def show(tab: str = "overview", tour: bool = False) -> dict:
    """Record the blueprint card (the HUD renders it; same key = refresh in place, not a new card)."""
    bp = load()
    store.record_result("support_model", None, {"tab": tab, "tour": tour},
                        {**bp, "key": "support:blueprint", "tab": tab if tab in SECTIONS else "overview", "tour": tour})
    return bp


def support_model(section: str = "", tour: bool = False, show_card: bool = True) -> dict:
    """For the model: narrative + FAQ always (enough for most answers), plus any section asked for in full.
    tour=True: JARVIS Core narrates the full walkthrough over the blueprint (tab by tab) once the reply is spoken."""
    bp = load()
    sec = (section or "").strip().lower()
    if show_card:
        show(sec if sec in SECTIONS else "overview", tour=tour)
    out: dict[str, Any] = {
        "title": bp.get("title"), "summary": bp.get("summary"), "narrative": bp.get("narrative"),
        "tiers": [{k: t.get(k) for k in ("tier", "name", "agent", "platform", "mission", "invoked_by", "does",
                                        "never", "outputs", "exit_states")} for t in bp.get("tiers") or []],
        "flows": [{"id": f.get("id"), "title": f.get("title"), "summary": f.get("summary")} for f in bp.get("flows") or []],
        "faq": bp.get("faq"),
        "sections_available": list(SECTIONS),
        "card": "the interactive Support Model blueprint is on screen (architecture, flows, routing, lifecycle, "
                "budgets, guardrails tabs)",
    }
    if tour:
        out["tour"] = ("A narrated tour of the blueprint starts automatically right after your reply. Say ONE short "
                       "sentence introducing it and nothing else; do not summarise, the tour covers everything.")
    if sec and sec in SECTIONS and sec != "overview":
        out[sec] = _section(bp, sec)
    elif sec and sec not in SECTIONS:
        # free-text: return every flow / routing row / faq that mentions it
        q = sec.lower()
        hit = lambda o: q in json.dumps(o).lower()  # noqa: E731
        out["matches"] = {"flows": [f for f in bp.get("flows") or [] if hit(f)][:3],
                          "routing": [r for r in bp.get("routing") or [] if hit(r)],
                          "guardrails": [g for g in bp.get("guardrails") or [] if hit(g)],
                          "glossary": [g for g in bp.get("glossary") or [] if hit(g)]}
    return out


# What each kind of paragraph should SHOW while it is spoken, in order (one view per spoken chunk). Keys are matched
# against the paragraph text; flows/tiers resolve to real ids in the blueprint, missing ones are skipped.
_VIEW_RULES: list[tuple[tuple[str, ...], list[dict]]] = [
    (("tier 1", "jamie"), [{"tab": "tiers", "tier": "1"}, {"tab": "flows", "flow": "jamie"}, {"tab": "flows", "flow": "handoff"}]),
    (("tier 2", "hadrian"), [{"tab": "tiers", "tier": "2"}, {"tab": "flows", "flow": "hadrian"}, {"tab": "budgets"},
                             {"tab": "flows", "flow": "draft"}]),
    (("tier 3",), [{"tab": "tiers", "tier": "3"}, {"tab": "routing"}]),
    (("every ticket starts", "intake", "at creation", "junk", "billing"), [{"tab": "flows", "flow": "intake"},
                                                                            {"tab": "routing"}]),
    (("middleware", "shared secret", "duplicate", "webhook"), [{"tab": "architecture"}, {"tab": "flows", "flow": "webhook"}]),
    (("in control", "timers", "on hold", "auto-close", "approve"), [{"tab": "lifecycle"}, {"tab": "flows", "flow": "timer"},
                                                                    {"tab": "guardrails"}]),
]


def _resolve(bp: dict, v: dict) -> dict | None:
    if v.get("flow"):
        f = next((f for f in bp.get("flows") or [] if v["flow"] in f"{f.get('id', '')} {f.get('title', '')}".lower()), None)
        return {"tab": "flows", "flow": f["id"]} if f else None
    if v.get("tier"):
        return v if any(str(t.get("tier")) == v["tier"] for t in bp.get("tiers") or []) else None
    return v if (bp.get(v["tab"]) or v["tab"] == "overview") else None


def narration() -> list[dict]:
    """Paragraphs of the spoken walkthrough, each with the views to show while it is spoken (one per chunk)."""
    bp = load()
    paras = [p.strip() for p in (bp.get("narrative") or "").split("\n\n") if p.strip()]
    out, used = [], set()
    for i, p in enumerate(paras):
        low = p.lower()
        if i == 0:
            views = [{"tab": "overview"}, {"tab": "layers"}, {"tab": "architecture"}]
        else:
            best = max(_VIEW_RULES, key=lambda r: sum(low.count(k) * (3 if low.startswith(k) or f". {k}" in low[:40] else 1)
                                                      for k in r[0]))
            views = best[1] if any(k in low for k in best[0]) else [{"tab": "overview"}]
            if id(best) in used and not any(low.startswith(k) for k in best[0]):
                views = [{"tab": "lifecycle"}, {"tab": "guardrails"}]
            used.add(id(best))
        views = [r for r in (_resolve(bp, v) for v in views) if r] or [{"tab": "overview"}]
        tier = next((v["tier"] for v in views if v.get("tier")), None)
        out.append({"text": p, "tab": views[0]["tab"], "tier": tier, "views": views})
    return out
