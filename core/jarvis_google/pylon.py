"""Pylon + Linear for the JARVIS HUD.

Reads (pylon_tickets / pylon_ticket) are MCP tools the model can call; they record to the HUD feed so the HUD
renders actionable ticket cards. Everything that changes a ticket (status, team, assignee, snooze, note, reply,
Linear create/link) is CLICK-ONLY: called by Core when Stephen clicks a button on a specific visible ticket card,
never exposed to the model, and audit-logged with source "pylon_click".

Rules baked in (from the pylon-api / pylon-linear-linking / pylon-send-as-stephen playbooks):
  * API base has no /v1; Pylon takes "Bearer <key>", Linear takes the raw key.
  * Assigning a human: skip entirely when it's already that person; otherwise clear team first, then set the
    assignee in a second call, so Pylon's team-from-assignee routing fires.
  * Replies/notes are attributed to Stephen (user_id) and replies thread onto the latest public message.
  * Never PATCH status after an external reply (Pylon moves it to On Customer itself).
  * Linking a Linear issue makes Pylon move the ticket to On Hold (and may sync Priority): we report what changed.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import store

PYLON = "https://api.usepylon.com"
LINEAR = "https://api.linear.app/graphql"
OPEN_STATES = ["new", "waiting_on_you", "waiting_on_customer", "on_hold", "meeting_pending"]
# Stephen's preferred labels for the standard states (Pylon UI names).
STATE_LABEL = {"new": "New", "waiting_on_you": "On You", "waiting_on_customer": "On Customer",
               "on_hold": "On Hold", "closed": "Closed"}
LINEAR_PRIORITY = {"urgent": 1, "high": 2, "medium": 3, "low": 4}


# ---------------------------------------------------------------- credentials (never logged)
def _env_files() -> list[Path]:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    extra = os.environ.get("JARVIS_LINEAR_ENV_FILE", str(Path.home() / "webhook-service/.env.production.local"))
    return [home / ".env", Path(extra)]


def _secret(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    for f in _env_files():
        try:
            for line in f.read_text().splitlines():
                m = re.match(rf"\s*(?:export\s+)?{name}\s*=\s*(.*)", line)
                if m:
                    v = m[1].strip().strip('"').strip("'")
                    if v:
                        return v
        except OSError:
            continue
    raise RuntimeError(f"{name} is not configured")


def _optional(name: str) -> str:
    try:
        return _secret(name)
    except RuntimeError:
        return ""


# Pylon user id of the person JARVIS acts as (notes/replies attributed to them, "my tickets"). Kept in
# ~/.hermes/.env (JARVIS_PYLON_USER_ID) rather than in the repo.
STEPHEN_ID = _optional("JARVIS_PYLON_USER_ID")

_local = threading.local()


def _me() -> str:
    if not STEPHEN_ID:
        raise RuntimeError("JARVIS_PYLON_USER_ID is not configured")
    return STEPHEN_ID


def _pylon() -> httpx.Client:
    c = getattr(_local, "pylon", None)
    if c is None:
        c = _local.pylon = httpx.Client(base_url=PYLON, timeout=30,
                                        headers={"Authorization": f"Bearer {_secret('PYLON_API_KEY')}"})
    return c


def _req(method: str, path: str, **kw) -> Any:
    for attempt in range(3):
        r = _pylon().request(method, path, **kw)
        if r.status_code == 429 and attempt < 2:
            time.sleep(6.5)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Pylon {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}
    raise RuntimeError("Pylon rate limit")


def _gql(query: str, variables: dict | None = None) -> dict:
    r = httpx.post(LINEAR, json={"query": query, "variables": variables or {}}, timeout=30,
                   headers={"Authorization": _secret("LINEAR_API_KEY"), "Content-Type": "application/json"})
    j = r.json()
    if r.status_code >= 400 or j.get("errors"):
        raise RuntimeError(f"Linear: {(j.get('errors') or [{}])[0].get('message', r.text[:300])}")
    return j["data"]


# ---------------------------------------------------------------- directory caches
_cache: dict[str, tuple[float, Any]] = {}
_names: dict[str, dict] = {"account": {}, "contact": {}}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (time.time(), v)
    return v


def _teams() -> list[dict]:
    return _cached("teams", 600, lambda: [{"id": t["id"], "name": t["name"]} for t in _req("GET", "/teams")["data"]])


def _users() -> list[dict]:
    def load():
        us = _req("GET", "/users", params={"limit": 200})["data"]
        return sorted(({"id": u["id"], "name": u.get("name") or u.get("email", "?"), "email": u.get("email", "")}
                       for u in us if not u.get("is_deactivated")), key=lambda u: u["name"].lower())
    return _cached("users", 600, load)


def _statuses() -> list[dict]:
    def load():
        out = []
        for s in _req("GET", "/issue-statuses")["data"]:
            if s.get("is_archived"):
                continue
            out.append({"slug": s["slug"], "label": STATE_LABEL.get(s["slug"], s["label"]), "category": s["category"]})
        return out
    return _cached("statuses", 3600, load)


def _linear_teams() -> list[dict]:
    return _cached("linear_teams", 3600, lambda: [
        {"id": n["id"], "key": n["key"], "name": n["name"]} for n in _gql("{ teams { nodes { id key name } } }")["teams"]["nodes"]])


def _account(aid: str | None) -> dict:
    if not aid:
        return {}
    if aid not in _names["account"]:
        try:
            a = _req("GET", f"/accounts/{aid}")["data"]
            _names["account"][aid] = {"id": aid, "name": a.get("name", ""), "domain": a.get("primary_domain") or a.get("domain", "")}
        except Exception:
            _names["account"][aid] = {"id": aid, "name": ""}
    return _names["account"][aid]


def _person(pid: str | None) -> dict:
    """Requester: customers are contacts; internal requesters are users."""
    if not pid:
        return {}
    if pid not in _names["contact"]:
        u = next((u for u in _users() if u["id"] == pid), None)
        if u:
            _names["contact"][pid] = {"id": pid, "name": u["name"], "email": u["email"], "internal": True}
        else:
            try:
                c = _req("GET", f"/contacts/{pid}")["data"]
                _names["contact"][pid] = {"id": pid, "name": c.get("name", ""), "email": c.get("email", "")}
            except Exception:
                _names["contact"][pid] = {"id": pid, "name": "", "email": ""}
    return _names["contact"][pid]


def options() -> dict:
    """Pickers for the ticket cards (teams, people, statuses, Linear teams)."""
    lt = []
    try:
        lt = _linear_teams()
    except Exception:
        pass
    return {"teams": _teams(), "users": [{"id": u["id"], "name": u["name"]} for u in _users()],
            "statuses": _statuses(), "linear_teams": lt, "me": STEPHEN_ID}


# ---------------------------------------------------------------- shaping
def _text(h: str | None, limit: int = 1200) -> str:
    if not h:
        return ""
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\u00a0]+", " ", t)).strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def _summary(it: dict) -> dict:
    team_id = (it.get("team") or {}).get("id")
    asg = (it.get("assignee") or {}).get("id")
    team = next((t for t in _teams() if t["id"] == team_id), None)
    user = next((u for u in _users() if u["id"] == asg), None)
    cf = it.get("custom_fields") or {}
    ext = []
    for e in it.get("external_issues") or []:
        ext.append({"source": e.get("source"), "id": e.get("external_id") or e.get("id"), "link": e.get("link") or e.get("url")})
    st = next((s for s in _statuses() if s["slug"] == it.get("state")), None)
    return {
        "id": it["id"], "number": it.get("number"), "title": it.get("title") or "(no title)",
        "link": it.get("link") or f"https://app.usepylon.com/issues?issueNumber={it.get('number')}",
        "state": it.get("state"), "state_label": st["label"] if st else STATE_LABEL.get(it.get("state") or "", it.get("state")),
        "priority": (cf.get("priority") or {}).get("value") or "",
        "account": _account((it.get("account") or {}).get("id")),
        "requester": _person((it.get("requester") or {}).get("id")),
        "assignee": {"id": asg, "name": user["name"] if user else ""} if asg else None,
        "team": team, "tags": it.get("tags") or [], "source": it.get("source"),
        "created_at": it.get("created_at"), "updated_at": it.get("updated_at"),
        "latest_message_time": it.get("latest_message_time"),
        "snippet": _text(it.get("body_html"), 280), "external_issues": ext,
    }


def _summaries(items: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(8) as ex:  # warm name caches in parallel
        list(ex.map(lambda i: (_account((i.get("account") or {}).get("id")), _person((i.get("requester") or {}).get("id"))), items))
    return [_summary(i) for i in items]


def _messages(issue_id: str, limit: int = 12) -> list[dict]:
    out = []
    for m in _req("GET", f"/issues/{issue_id}/messages")["data"]:
        a = m.get("author") or {}
        out.append({"id": m["id"], "private": bool(m.get("is_private")), "source": m.get("source"),
                    "at": m.get("timestamp"), "author": a.get("name") or (a.get("contact") or {}).get("email") or "",
                    "from_customer": "contact" in a,
                    "to": (m.get("email_info") or {}).get("to_emails") or [],
                    "cc": (m.get("email_info") or {}).get("cc_emails") or [],
                    "text": _text(m.get("message_html"), 2500)})
    out.sort(key=lambda m: m["at"] or "")
    return out[-limit:]


def _get(number_or_id: str | int) -> dict:
    return _req("GET", f"/issues/{number_or_id}")["data"]


# ---------------------------------------------------------------- READ tools (model + HUD)
def pylon_tickets(query: str = "", mine: bool = True, states: list[str] | None = None, limit: int = 15,
                  account: str = "") -> dict:
    """Search tickets. Default: Stephen's open tickets, most recent activity first."""
    limit = max(1, min(int(limit or 15), 40))
    subs: list[dict] = []
    if mine:
        subs.append({"field": "assignee_id", "operator": "equals", "value": _me()})
    sts = [s for s in (states or []) if s] or (OPEN_STATES if not query else [])
    if sts:
        subs.append({"field": "state", "operator": "in", "values": sts})
    body: dict[str, Any] = {"limit": 100 if not query else min(100, limit * 3)}
    if subs:
        body["filter"] = subs[0] if len(subs) == 1 else {"operator": "and", "subfilters": subs}
    q = " ".join(x for x in (query.strip(), account.strip()) if x)
    if q:
        if re.fullmatch(r"#?\d{3,7}", q):
            return pylon_ticket(q.lstrip("#"))
        body["search_text"] = q
    res0 = _req("POST", "/issues/search", json=body)
    items = res0["data"]
    pages = 1
    while not q and (res0.get("pagination") or {}).get("has_next_page") and pages < 5:  # "all my open" must be complete
        res0 = _req("POST", "/issues/search", json={**body, "cursor": res0["pagination"]["cursor"]})
        items += res0["data"]
        pages += 1
    items.sort(key=lambda i: i.get("latest_message_time") or i.get("updated_at") or "", reverse=True)
    tickets = _summaries(items[:limit])
    lab = {"new": "New", "waiting_on_you": "On You", "waiting_on_customer": "On Customer", "on_hold": "On Hold",
           "closed": "Closed", "meeting_pending": "Meeting Pending"}
    which = "open" if sts == OPEN_STATES or not sts else " / ".join(lab.get(s, s) for s in sts)
    desc = (f"your {which} tickets" if mine else f"all {which} tickets") if not q else f"“{q}”" + ("" if mine else " (everyone)")
    res = {"query": desc, "count": len(tickets), "total_matched": len(items), "tickets": tickets}
    store.record_result("pylon_tickets", None, {"query": query, "mine": mine, "states": sts}, res)
    return res


def pylon_ticket_view(number_or_id: str | int) -> dict:
    """One ticket with its recent thread (no HUD feed card)."""
    it = _get(str(number_or_id).lstrip("#"))
    t = _summary(it)
    try:
        t["messages"] = _messages(it["id"])
    except Exception as e:  # messages endpoint is rate-limited (10/min)
        t["messages"], t["messages_error"] = [], str(e)[:160]
    return t


def pylon_ticket(number: str | int) -> dict:
    """One ticket with its recent thread; shows a ticket card on the HUD."""
    t = pylon_ticket_view(number)
    store.record_result("pylon_ticket", None, {"number": t["number"]}, t)
    return t


def pylon_thread(issue_id: str) -> dict:
    """HUD click: load the thread for a card that only has the summary (no feed card)."""
    return {"issue_id": issue_id, "messages": _messages(issue_id)}


# ---------------------------------------------------------------- CLICK-ONLY writes
def _audit(kind: str, issue: dict, **extra) -> None:
    store._audit({"event": "executed", "kind": kind, "account": "pylon", "source": "pylon_click",
                  "issue_number": issue.get("number"), **extra})


def _fresh(issue_id: str) -> dict:
    return _summary(_get(issue_id))


def pylon_set_status(issue_id: str, state: str) -> dict:
    before = _get(issue_id)
    if state not in {s["slug"] for s in _statuses()}:
        raise ValueError(f"Unknown status {state}")
    prev = before.get("state")
    if prev == state:
        return {"ticket": _summary(before), "text": "Already in that status."}
    _req("PATCH", f"/issues/{issue_id}", json={"state": state})
    _audit("pylon_status", before, state=state, previous=prev)
    t = _fresh(issue_id)
    return {"ticket": t, "text": f"#{t['number']} → {t['state_label']}",
            "undo": {"op": "pylon_set_status", "args": {"issue_id": issue_id, "state": prev}}}


def pylon_set_team(issue_id: str, team_id: str) -> dict:
    """Pylon's routing round-robins a team change onto someone on the new team, so the result reports the new
    assignee and the undo restores team AND assignee."""
    before = _get(issue_id)
    prev = (before.get("team") or {}).get("id") or ""
    prev_asg = (before.get("assignee") or {}).get("id") or ""
    if prev == team_id:
        return {"ticket": _summary(before), "text": "Already on that team."}
    _req("PATCH", f"/issues/{issue_id}", json={"team_id": team_id})
    _audit("pylon_team", before, team_id=team_id, previous=prev)
    time.sleep(1.2)  # routing lands a moment after the PATCH
    t = _fresh(issue_id)
    text = f"#{t['number']} → {(t['team'] or {}).get('name') or 'No team'}"
    if ((t["assignee"] or {}).get("id") or "") != prev_asg:
        text += f", now assigned to {(t['assignee'] or {}).get('name') or 'nobody'}"
    return {"ticket": t, "text": text,
            "undo": {"op": "pylon_restore", "args": {"issue_id": issue_id, "team_id": prev, "assignee_id": prev_asg}}}


def pylon_restore(issue_id: str, team_id: str, assignee_id: str) -> dict:
    """Undo for a team change: put the assignee back (routing re-derives the team), then pin the old team."""
    before = _get(issue_id)
    if ((before.get("assignee") or {}).get("id") or "") != assignee_id:
        _req("PATCH", f"/issues/{issue_id}", json={"assignee_id": assignee_id})
        time.sleep(1.2)
    now = _get(issue_id)
    if ((now.get("team") or {}).get("id") or "") != team_id:
        _req("PATCH", f"/issues/{issue_id}", json={"team_id": team_id})
        time.sleep(1.2)
        now = _get(issue_id)
        if ((now.get("assignee") or {}).get("id") or "") != assignee_id:  # the team PATCH re-routed it again
            _req("PATCH", f"/issues/{issue_id}", json={"assignee_id": assignee_id})
    _audit("pylon_restore", before, team_id=team_id, assignee_id=assignee_id)
    t = _fresh(issue_id)
    return {"ticket": t, "text": f"#{t['number']} restored: {(t['team'] or {}).get('name') or 'No team'}, {(t['assignee'] or {}).get('name') or 'Unassigned'}"}


def pylon_assign(issue_id: str, user_id: str) -> dict:
    before = _get(issue_id)
    prev = (before.get("assignee") or {}).get("id") or ""
    if prev.lower() == (user_id or "").lower():
        return {"ticket": _summary(before), "text": "Already assigned to them."}  # send nothing (routing quirk)
    if user_id:
        # two-step so Pylon's team-from-assignee triggers route the ticket to the new person's team
        _req("PATCH", f"/issues/{issue_id}", json={"team_id": ""})
        _req("PATCH", f"/issues/{issue_id}", json={"assignee_id": user_id})
    else:
        _req("PATCH", f"/issues/{issue_id}", json={"assignee_id": ""})
    _audit("pylon_assign", before, assignee_id=user_id, previous=prev)
    t = _fresh(issue_id)
    who = (t["assignee"] or {}).get("name") or "Unassigned"
    return {"ticket": t, "text": f"#{t['number']} → {who}",
            "undo": {"op": "pylon_assign", "args": {"issue_id": issue_id, "user_id": prev}} if prev else None}


def pylon_snooze(issue_id: str, until: str) -> dict:
    before = _get(issue_id)
    when = dt.datetime.fromisoformat(until.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.astimezone()
    if when <= dt.datetime.now(dt.timezone.utc):
        raise ValueError("Snooze time must be in the future.")
    _req("POST", f"/issues/{issue_id}/snooze", json={"snooze_until": when.isoformat()})
    _audit("pylon_snooze", before, until=when.isoformat())
    t = _fresh(issue_id)
    return {"ticket": t, "text": f"#{t['number']} snoozed until {when.astimezone():%a %b %-d, %-I:%M %p}"}


def _html(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)


def pylon_note(issue_id: str, text: str) -> dict:
    if not text.strip():
        raise ValueError("The note is empty.")
    before = _get(issue_id)
    _req("POST", f"/issues/{issue_id}/note", json={"body_html": _html(text), "user_id": _me()})
    _audit("pylon_note", before, chars=len(text))
    return {"ticket": _fresh(issue_id), "text": f"Internal note added to #{before.get('number')}."}


def pylon_reply(issue_id: str, text: str, to_emails: list[str] | None = None, cc_emails: list[str] | None = None) -> dict:
    """External reply to the customer, as Stephen. Stephen clicked Send, then Confirm, on this ticket's card."""
    if not text.strip():
        raise ValueError("The reply is empty.")
    if "—" in text:
        raise ValueError("The reply contains an em dash. Replace it before sending.")
    before = _get(issue_id)
    msgs = _req("GET", f"/issues/{issue_id}/messages")["data"]
    public = [m for m in msgs if not m.get("is_private")]
    public.sort(key=lambda m: m.get("timestamp") or "")
    last = public[-1] if public else None
    to = [e for e in (to_emails or []) if e] or [_person((before.get("requester") or {}).get("id")).get("email", "")]
    to = [e for e in to if e]
    payload: dict[str, Any] = {"body_html": _html(text), "user_id": _me()}
    if last:
        payload["message_id"] = last["id"]
    if before.get("source") == "email" or to:
        if not to:
            raise ValueError("No recipient email for this ticket.")
        payload["email_info"] = {"to_emails": to, **({"cc_emails": cc_emails} if cc_emails else {})}
    _req("POST", f"/issues/{issue_id}/reply", json=payload)
    _audit("pylon_reply", before, to=len(to), chars=len(text))
    # no status PATCH: Pylon moves the ticket to On Customer by itself after an external reply
    return {"ticket": _fresh(issue_id), "text": f"Reply sent on #{before.get('number')}."}


def _link_linear(issue: dict, linear_uuid: str) -> None:
    num = issue.get("number")
    _gql("mutation($i: AttachmentCreateInput!) { attachmentCreate(input: $i) { success } }",
         {"i": {"issueId": linear_uuid, "title": f"Pylon Ticket #{num}",
                "url": f"https://app.usepylon.com/issues?issueNumber={num}"}})
    try:  # the backlink alone makes Pylon link it; this is belt and braces
        _req("POST", f"/issues/{issue['id']}/external-issues", json={"source": "linear", "external_issue_id": linear_uuid})
    except RuntimeError:
        pass


def _after_link_note(before: dict, after: dict) -> str:
    notes = []
    b_st, a_st = before.get("state"), after.get("state")
    if b_st != a_st:
        notes.append(f"status {STATE_LABEL.get(b_st or '', b_st)} → {after.get('state_label')}")
    bp = ((before.get("custom_fields") or {}).get("priority") or {}).get("value") or ""
    if bp != after.get("priority"):
        notes.append(f"priority {bp or 'none'} → {after.get('priority') or 'none'}")
    return f" Pylon changed {', '.join(notes)}." if notes else ""


def pylon_linear_create(issue_id: str, team_key: str, title: str, description: str, priority: str = "",
                        labels: list[str] | None = None) -> dict:
    if not title.strip():
        raise ValueError("The Linear ticket needs a title.")
    before = _get(issue_id)
    team = next((t for t in _linear_teams() if t["key"].lower() == team_key.lower()), None)
    if not team:
        raise ValueError(f"Unknown Linear team {team_key}")
    num = before.get("number")
    desc = description.strip() + f"\n\n---\nPylon ticket: [#{num}](https://app.usepylon.com/issues?issueNumber={num})"
    inp: dict[str, Any] = {"teamId": team["id"], "title": title.strip(), "description": desc}
    pr = priority or ((before.get("custom_fields") or {}).get("priority") or {}).get("value") or ""
    if pr.lower() in LINEAR_PRIORITY:  # match the ticket's priority so a priority sync keeps it unchanged
        inp["priority"] = LINEAR_PRIORITY[pr.lower()]
    if labels:
        ids = []
        for name in labels:
            n = _gql("query($n: String!) { issueLabels(filter: {name: {eq: $n}}) { nodes { id } } }", {"n": name})
            ids += [x["id"] for x in n["issueLabels"]["nodes"][:1]]
        if ids:
            inp["labelIds"] = ids
    made = _gql("mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success issue { id identifier url title } } }",
                {"i": inp})["issueCreate"]["issue"]
    _link_linear(before, made["id"])
    _audit("pylon_linear_create", before, linear=made["identifier"], team=team["key"])
    time.sleep(1.5)  # let Pylon's own link automation land before re-reading
    after = _fresh(issue_id)
    return {"ticket": after, "linear": made,
            "text": f"Created {made['identifier']} and linked it to #{num}.{_after_link_note(before, after)}"}


def pylon_linear_link(issue_id: str, key: str) -> dict:
    key = key.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", key):
        raise ValueError("Use a Linear key like HAD-123.")
    before = _get(issue_id)
    li = _gql("query($id: String!) { issue(id: $id) { id identifier url title } }", {"id": key})["issue"]
    if not li:
        raise ValueError(f"{key} not found in Linear.")
    _link_linear(before, li["id"])
    _audit("pylon_linear_link", before, linear=li["identifier"])
    time.sleep(1.5)
    after = _fresh(issue_id)
    return {"ticket": after, "linear": li,
            "text": f"Linked {li['identifier']} to #{before.get('number')}.{_after_link_note(before, after)}"}


def pylon_refresh(issue_id: str) -> dict:
    return {"ticket": _fresh(issue_id), "text": ""}


# ops Core may run on a HUD click (no model involvement)
CLICK_READ = {"pylon_thread", "pylon_refresh", "pylon_open"}


def pylon_open(number: str | int) -> dict:
    """HUD click on a list row: show that ticket as its own card."""
    return pylon_ticket(number)
CLICK_WRITE = {"pylon_set_status", "pylon_set_team", "pylon_restore", "pylon_assign", "pylon_snooze", "pylon_note",
               "pylon_reply", "pylon_linear_create", "pylon_linear_link"}
