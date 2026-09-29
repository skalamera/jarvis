"""AI drafts for Pylon ticket cards: a customer reply and a Linear ticket (HAD bug / PROD feature request).
One structured Gemini call each (same model/key as the briefing). Drafts only: nothing is sent or created until
Stephen edits and clicks on the card."""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from jarvis_google import pylon as P

from .config import settings

REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
LINEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["bug", "feature_request", "task"]},
        "team_key": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["kind", "team_key", "title", "description"],
}

REPLY_PROMPT = """You draft customer replies for Stephen Skalamera, Hadrius Support (compliance software for RIAs).
Write the next reply on this ticket, to {first}.

Rules (strict):
- Line 1: "Hi {first}," then a blank line.
- Warm and human. Acknowledge their situation first, then give clear, useful self-service guidance or the answer.
- NEVER promise work, fixes, timelines or follow-ups ("I will", "we will", "I can", "happy to help", "shortly").
- Never apologize for colleagues, never mention internal tools, Linear, engineering ticket IDs or internal notes.
- No em dashes, no AI filler, no bullet walls. Short paragraphs.
- State only facts the thread supports. If the answer isn't in the thread, ask the one clarifying question needed.
- Offer no calls or meetings.
- End with exactly:
Thanks,
Stephen
Hadrius Support
{steer}
TICKET #{number}: {title}
Account: {account}
Thread (oldest first; PRIVATE lines are internal notes, never quote them):
{thread}
"""

LINEAR_PROMPT = """Turn this Pylon support ticket into a Linear ticket draft for Hadrius engineering/product.
Pick kind: "bug" (team HAD) when something is broken, "feature_request" (team PROD) when they want new capability,
"task" for anything else (pick the best team key from: {teams}).
Title: short, specific, no customer name, no ticket number.
Description in markdown, using the template for the kind:
bug:
**OBSERVED BEHAVIOR:**
(what's happening, with exact error text if any)

**EXPECTED BEHAVIOR:**
(what should happen instead)

**STEPS TO REPRODUCE:**
1. ...

**CUSTOMER:** {account}

feature_request:
**PROBLEM:**
**CURRENT WORKAROUND:**
**REQUESTED CAPABILITY:**
**CUSTOMER:** {account}

Use only facts in the thread; write "Unknown" where the thread doesn't say. Plain, precise, no em dashes.
{steer}
TICKET #{number}: {title}
Thread (oldest first):
{thread}
"""


async def _gemini(prompt: str, schema: dict) -> dict:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                 "temperature": 0.3}}
    if settings.brief_thinking:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": settings.brief_thinking}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.brief_model}:generateContent"
    async with httpx.AsyncClient(timeout=90) as http:
        r = await http.post(url, params={"key": settings.brief_key}, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))


def _thread(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        who = ("PRIVATE NOTE " if m["private"] else "") + (m["author"] or ("customer" if m["from_customer"] else "support"))
        out.append(f"[{(m['at'] or '')[:16]}] {who}:\n{m['text'][:1800]}\n")
    return "\n".join(out)


async def draft(issue_id: str, kind: str, steer: str = "") -> dict:
    t = await asyncio.to_thread(P.pylon_ticket_view, issue_id)
    thread = _thread(t["messages"])
    steer_line = f"\nStephen's instruction for this draft: {steer.strip()}\n" if steer.strip() else ""
    acct = (t.get("account") or {}).get("name") or "Unknown"
    if kind == "reply":
        first = ((t.get("requester") or {}).get("name") or "").split(" ")[0] or "there"
        out = await _gemini(REPLY_PROMPT.format(first=first, number=t["number"], title=t["title"], account=acct,
                                                thread=thread, steer=steer_line), REPLY_SCHEMA)
        reply = out["reply"].replace("—", ", ").replace("–", "-").strip()
        reply = re.sub(r"\b(?:HAD|PROD|TRA|POP|PLAT|MR|CS|FDE|COMM)-\d+\b", "the issue", reply)  # never leak Linear keys
        return {"kind": "reply", "reply": reply, "to": [(t.get("requester") or {}).get("email")] if (t.get("requester") or {}).get("email") else []}
    if kind == "linear":
        teams = ", ".join(x["key"] for x in await asyncio.to_thread(P._linear_teams))
        out = await _gemini(LINEAR_PROMPT.format(teams=teams, account=acct, number=t["number"], title=t["title"],
                                                 thread=thread, steer=steer_line), LINEAR_SCHEMA)
        team = {"bug": "HAD", "feature_request": "PROD"}.get(out["kind"], out["team_key"] or "HAD")
        return {"kind": "linear", "linear_kind": out["kind"], "team_key": team, "title": out["title"].strip(),
                "description": out["description"].replace("—", ", ").strip(), "priority": t.get("priority") or ""}
    raise ValueError(f"unknown draft kind {kind}")
