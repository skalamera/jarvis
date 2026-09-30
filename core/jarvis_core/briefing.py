"""Inbox briefing: AI-Inbox-style to-dos, priority emails and catch-up topics across both accounts.

Pipeline: Gmail (recent inbox, noise categories excluded) -> one structured Gemini call -> validated items.
The model only ever sees short refs (e1, e2, ...). Every message id in the output is mapped back from
those refs, so hallucinated ids are impossible: an unknown ref is dropped, never acted on.

Actions (click-only, from the HUD): dismiss, archive, mark_read, trash, reply_send, reply_draft, undo.
Handled message ids are remembered so they don't come back on the next refresh.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from jarvis_google import slack as gslack
from jarvis_google import tools as gtools
from jarvis_google.accounts import account_email, linked_accounts, service

from .config import settings

log = logging.getLogger("jarvis.briefing")

QUERY = "in:inbox newer_than:4d -category:promotions -category:social -category:forums"
PER_ACCOUNT = 35
BODY_CHARS = 900
STATE_FILE = settings.state_dir / "briefing.json"
HANDLED_KEEP = 3000
SLACK_REFRESH_S = 120  # Slack is cheap (no model call): keep it fresher than the email triage

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "todos": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Imperative action, <= 9 words, specific (amounts, names)."},
            "detail": {"type": "string", "description": "1-2 sentences: who asked, what exactly, by when."},
            "refs": {"type": "array", "items": {"type": "string"}},
            "urgency": {"type": "string", "enum": ["high", "normal"]},
            "kind": {"type": "string", "enum": ["reply", "pay", "review", "schedule", "deadline", "task"]},
            "due": {"type": "string", "description": "Deadline as written in the email, or empty."},
            "reply_suggestion": {"type": "string", "description": "Only for kind=reply: short reply in Stephen's voice, else empty."},
        }, "required": ["title", "detail", "refs", "urgency", "kind"]}},
        "priority": {"type": "array", "items": {"type": "object", "properties": {
            "ref": {"type": "string"},
            "reason": {"type": "string", "description": "Why it matters, <= 20 words."},
            "reply_suggestion": {"type": "string"},
        }, "required": ["ref", "reason"]}},
        "topics": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "emoji": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"},
                "headline": {"type": "string", "description": "Sender or subject, <= 5 words."},
                "summary": {"type": "string", "description": "One sentence with the concrete facts."},
            }, "required": ["ref", "headline", "summary"]}},
        }, "required": ["title", "emoji", "items"]}},
    },
    "required": ["todos", "priority", "topics"],
}

PROMPT = """You are J.A.R.V.I.S., triaging Stephen Skalamera's inboxes like Gmail's AI Inbox.
Accounts: "personal" (skalamera@gmail.com) and "work" (stephen@hadrius.com; Hadrius is his employer).
Now: {now}.

Produce three sections. Each email ref may appear in AT MOST ONE section. Skip noise entirely
(newsletters, marketing, generic notifications, automated digests with nothing to act on).

1. todos: concrete things Stephen must DO (pay, reply, sign, review, attend, fix, deadline). Merge emails that
   are about the same action into one todo with several refs. Titles are specific and imperative, like
   "Renew car registration before Oct 15". Order by urgency, most urgent first. At most 10.
2. priority: emails from real people (not automated) that deserve his attention or a reply and are not
   already a todo. At most 6.
3. topics: everything else worth knowing, grouped into 2-5 themed topics (e.g. "Finances", "Car repair",
   "Hadrius customers"), one fitting emoji each, one line per email. At most 5 items per topic.

reply_suggestion: when a reply is the obvious action, write a brief, natural reply as Stephen: no
greeting line fluff, no promises of work or timelines, no em dashes. End with a blank line, then "Stephen".
Otherwise empty.
Use only facts in the emails. Refs must be copied exactly from the list.

EMAILS:
{emails}
"""

Listener = Callable[[dict], Awaitable[None]]


def _item_id(kind: str, ids: list[str]) -> str:
    return hashlib.sha1(f"{kind}:{','.join(sorted(ids))}".encode()).hexdigest()[:12]


class Briefing:
    def __init__(self) -> None:
        self.data: dict | None = None
        self.handled: dict[str, float] = {}      # message id -> when handled
        self.removed: dict[str, dict] = {}       # item id -> {item, section, index, undo}
        self.listeners: set[Listener] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.refreshing = False
        self.error = ""
        self.slack: dict | None = None
        self.slack_error = ""
        self._slack_lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            d = json.loads(STATE_FILE.read_text())
            self.data, self.handled = d.get("data"), dict(d.get("handled") or {})
            self.slack = d.get("slack")
        except (FileNotFoundError, ValueError):
            pass

    def _save(self) -> None:
        if len(self.handled) > HANDLED_KEEP:
            self.handled = dict(sorted(self.handled.items(), key=lambda kv: kv[1])[-HANDLED_KEEP:])
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"data": self.data, "handled": self.handled, "slack": self.slack}, default=str))
        tmp.replace(STATE_FILE)

    # ------------------------------------------------------------ pub/sub
    async def publish(self) -> None:
        msg = self.snapshot()
        for fn in list(self.listeners):
            try:
                await fn(msg)
            except Exception:
                self.listeners.discard(fn)

    def snapshot(self) -> dict:
        return {"type": "briefing", "data": self.data, "refreshing": self.refreshing, "error": self.error,
                "slack": self.slack_view(), "slack_error": self.slack_error}

    # ------------------------------------------------------------ Slack (read-only, no model call)
    @staticmethod
    def _slack_key(kind: str, obj: dict) -> str:
        # a conversation comes back when someone writes again (new latest_ts)
        return f"slack:{obj['id']}:{obj['latest_ts']}" if kind == "conv" else f"slack:{obj['id']}"

    def slack_view(self) -> dict | None:
        s = self.slack
        if not s or not s.get("available"):
            return s
        return {**s,
                "conversations": [c for c in s.get("conversations", []) if self._slack_key("conv", c) not in self.handled],
                "mentions": [m for m in s.get("mentions", []) if self._slack_key("m", m) not in self.handled],
                "channels": [p for p in s.get("channels", []) if self._slack_key("p", p) not in self.handled]}

    async def refresh_slack(self, publish: bool = True) -> None:
        if self._slack_lock.locked() or not gslack.available():
            return
        async with self._slack_lock:
            try:
                self.slack = await asyncio.to_thread(gslack.gather)
                self.slack_error = ""
                self._save()
            except Exception as e:
                self.slack_error = f"{type(e).__name__}: {e}"[:200]
                log.warning("slack refresh failed: %s", e)
        if publish:
            await self.publish()

    def _slack_find(self, key: str) -> dict | None:
        for kind, sec in (("conv", "conversations"), ("m", "mentions"), ("p", "channels")):
            for o in (self.slack or {}).get(sec, []):
                if self._slack_key(kind, o) == key:
                    return o
        return None

    async def _slack_act(self, key: str, action: str) -> dict:
        if action == "undo":
            if self.handled.pop(key, None) is None:
                return {"ok": False, "error": "Nothing to undo."}
            self._save()
            await self.publish()
            return {"ok": True, "text": "Restored.", "item_id": key}
        if action != "dismiss":
            return {"ok": False, "error": "Slack items can only be dismissed here (Slack stays untouched)."}
        if not self._slack_find(key):
            return {"ok": False, "error": "That Slack item is no longer in the briefing."}
        self.handled[key] = time.time()
        self._save()
        await self.publish()
        return {"ok": True, "text": "Dismissed. Slack untouched.", "item_id": key, "undoable": True}

    # ------------------------------------------------------------ gather
    def _gather(self) -> list[dict]:
        out: list[dict] = []
        for a in linked_accounts():
            acct = a["account"]
            try:
                gm = service("gmail", acct)
                ids = [m["id"] for m in gm.users().messages().list(
                    userId="me", q=QUERY, maxResults=PER_ACCOUNT).execute().get("messages", [])]
                ids = [i for i in ids if i not in self.handled]
                mine = {x["email"].lower() for x in linked_accounts()}
                for m in gtools._batch_get(gm, ids, fmt="full"):
                    f = gtools._message_full(m, BODY_CHARS)
                    if (f["from_email"] or "").lower() in mine or "SENT" in f["labels"]:
                        continue
                    out.append({"account": acct, "email": a["email"], "id": f["id"], "threadId": f["threadId"],
                                "from": f["from"], "from_name": f["from_name"], "subject": f["subject"],
                                "internalDate": f["internalDate"], "unread": f["unread"],
                                "important": f["important"], "snippet": f["snippet"],
                                "body": " ".join(f["body"].split())[:BODY_CHARS]})
            except Exception as e:
                log.warning("briefing gather %s failed: %s", acct, e)
        out.sort(key=lambda m: m["internalDate"], reverse=True)
        return out

    # ------------------------------------------------------------ model
    async def _analyze(self, mails: list[dict]) -> dict:
        now = dt.datetime.now(ZoneInfo(settings.timezone))
        lines = []
        for i, m in enumerate(mails, 1):
            when = dt.datetime.fromtimestamp(m["internalDate"] / 1000, ZoneInfo(settings.timezone))
            flags = ",".join(f for f, on in (("unread", m["unread"]), ("important", m["important"])) if on)
            lines.append(f"[e{i}] account={m['account']} date={when:%a %b %d %I:%M%p} {flags}\n"
                         f"From: {m['from']}\nSubject: {m['subject']}\n{m['body'] or m['snippet']}\n")
        prompt = PROMPT.format(now=now.strftime("%A, %B %d, %Y %I:%M %p %Z"), emails="\n".join(lines))
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                                     "temperature": 0.2}}
        if settings.brief_thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": settings.brief_thinking}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.brief_model}:generateContent"
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(url, params={"key": settings.brief_key}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))

    # ------------------------------------------------------------ validate + shape
    @staticmethod
    def _shape(raw: dict, mails: list[dict]) -> dict:
        by_ref = {f"e{i}": m for i, m in enumerate(mails, 1)}
        used: set[str] = set()

        def take(refs: list[str]) -> list[dict]:
            got = []
            for r in refs:
                r = str(r).strip().strip("[]")
                if r in by_ref and r not in used:
                    used.add(r)
                    got.append(by_ref[r])
            return got

        def msg_view(m: dict) -> dict:
            return {k: m[k] for k in ("account", "email", "id", "threadId", "from_name", "from", "subject",
                                      "internalDate", "unread")}

        todos = []
        for t in (raw.get("todos") or [])[:10]:
            ms = take(t.get("refs") or [])
            if not ms:
                continue
            todos.append({"id": _item_id("todo", [m["id"] for m in ms]), "title": t.get("title", "").strip(),
                          "detail": t.get("detail", "").strip(), "urgency": t.get("urgency", "normal"),
                          "kind": t.get("kind", "task"), "due": (t.get("due") or "").strip(),
                          "reply_suggestion": (t.get("reply_suggestion") or "").strip(),
                          "messages": [msg_view(m) for m in ms]})
        priority = []
        for p in (raw.get("priority") or [])[:6]:
            ms = take([p.get("ref", "")])
            if ms:
                priority.append({"id": _item_id("prio", [ms[0]["id"]]), "reason": p.get("reason", "").strip(),
                                 "reply_suggestion": (p.get("reply_suggestion") or "").strip(),
                                 "snippet": ms[0]["snippet"], "messages": [msg_view(ms[0])]})
        topics = []
        for tp in (raw.get("topics") or [])[:5]:
            items = []
            for it in (tp.get("items") or [])[:5]:
                ms = take([it.get("ref", "")])
                if ms:
                    items.append({"id": _item_id("topic", [ms[0]["id"]]), "headline": it.get("headline", "").strip(),
                                  "summary": it.get("summary", "").strip(), "messages": [msg_view(ms[0])]})
            if items:
                topics.append({"id": _item_id("tgroup", [i["messages"][0]["id"] for i in items]),
                               "title": tp.get("title", "").strip(), "emoji": tp.get("emoji", "•"), "items": items})
        return {"todos": todos, "priority": priority, "topics": topics}

    # ------------------------------------------------------------ refresh
    async def refresh(self, reason: str = "manual") -> None:
        if self._lock.locked():
            return
        asyncio.create_task(self.refresh_slack())  # in parallel with the email triage
        async with self._lock:
            self.refreshing, self.error = True, ""
            await self.publish()
            t0 = time.time()
            try:
                if not settings.brief_key:
                    raise RuntimeError("GEMINI_API_KEY not found in ~/.hermes/.env")
                mails = await asyncio.to_thread(self._gather)
                raw = await self._analyze(mails) if mails else {}
                shaped = self._shape(raw, mails)
                prev_new = {i["id"] for i in self._all_items(self.data)} if self.data else None
                for it in self._all_items(shaped):  # "New" badge = not in the previous briefing
                    it["new"] = prev_new is not None and it["id"] not in prev_new
                self.data = {**shaped, "generated_at": time.time(), "scanned": len(mails),
                             "took_s": round(time.time() - t0, 1)}
                self.removed.clear()
                self._save()
                log.info("briefing refreshed (%s): %d mails, %d todos, %d priority, %d topics in %.1fs", reason,
                         len(mails), len(shaped["todos"]), len(shaped["priority"]), len(shaped["topics"]),
                         time.time() - t0)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:300]
                log.exception("briefing refresh failed")
            finally:
                self.refreshing = False
                await self.publish()

    async def ensure(self) -> None:
        stale = not self.data or time.time() - self.data.get("generated_at", 0) > settings.brief_refresh_s
        if stale and not self.refreshing:
            asyncio.create_task(self.refresh("stale"))

    def start(self) -> None:
        async def loop():
            while True:
                try:
                    await self.ensure()
                    if not self.slack or time.time() - (self.slack.get("generated_at") or 0) > SLACK_REFRESH_S:
                        await self.refresh_slack()
                except Exception:
                    log.exception("briefing loop")
                await asyncio.sleep(60)
        self._task = asyncio.create_task(loop())

    # ------------------------------------------------------------ items
    @staticmethod
    def _all_items(data: dict | None) -> list[dict]:
        if not data:
            return []
        return [*data.get("todos", []), *data.get("priority", []),
                *(i for tp in data.get("topics", []) for i in tp["items"])]

    def _find(self, item_id: str) -> tuple[dict, list, int] | None:
        if not self.data:
            return None
        for sec in ("todos", "priority"):
            for i, it in enumerate(self.data.get(sec, [])):
                if it["id"] == item_id:
                    return it, self.data[sec], i
        for tp in self.data.get("topics", []):
            for i, it in enumerate(tp["items"]):
                if it["id"] == item_id:
                    return it, tp["items"], i
        return None

    def _detach(self, item_id: str, undo: dict | None) -> dict | None:
        found = self._find(item_id)
        if not found:
            return None
        it, lst, idx = found
        lst.pop(idx)  # empty topic groups stay in data (UI hides them) so undo can re-insert in place
        for m in it["messages"]:
            self.handled[m["id"]] = time.time()
        self.removed[item_id] = {"item": it, "list": lst, "index": idx, "undo": undo}
        self._save()
        return it

    # ------------------------------------------------------------ actions (human clicks only)
    async def act(self, item_id: str, action: str, body: str = "", reply_all: bool = False) -> dict:
        if item_id.startswith("slack:"):
            return await self._slack_act(item_id, action)
        if action == "undo":
            return await self._undo(item_id)
        found = self._find(item_id)
        if not found:
            return {"ok": False, "error": "That item is no longer in the briefing."}
        it = found[0]
        msgs = it["messages"]
        by_acct: dict[str, list[str]] = {}
        for m in msgs:
            by_acct.setdefault(m["account"], []).append(m["id"])
        latest = max(msgs, key=lambda m: m["internalDate"])
        undo: dict | None = None
        text = ""
        if action == "dismiss":
            text = "Dismissed."
        elif action == "mark_read":
            for acct, ids in by_acct.items():
                await asyncio.to_thread(gtools.gmail_labels_now, acct, ids, remove=["UNREAD"])
            undo, text = {"labels_add": ["UNREAD"], "by_acct": by_acct}, "Marked as read."
        elif action == "archive":  # like Gmail "Done": leaves read/unread alone
            for acct, ids in by_acct.items():
                await asyncio.to_thread(gtools.gmail_labels_now, acct, ids, remove=["INBOX"])
            undo, text = {"labels_add": ["INBOX"], "by_acct": by_acct}, "Archived."
        elif action == "trash":
            for acct, ids in by_acct.items():
                await asyncio.to_thread(gtools.gmail_trash_now, acct, ids)
            # Gmail's untrash does NOT restore INBOX (verified live), so undo re-adds it explicitly
            undo, text = {"untrash": True, "labels_add": ["INBOX"], "by_acct": by_acct}, "Moved to trash."
        elif action in ("reply_send", "reply_draft"):
            if not body.strip():
                return {"ok": False, "error": "The reply is empty."}
            if action == "reply_send":
                r = await asyncio.to_thread(gtools.gmail_reply_now, latest["account"], latest["id"], body, reply_all)
                text = f"Reply sent to {latest['from_name']}."
            else:
                r = await asyncio.to_thread(gtools.gmail_reply_draft_now, latest["account"], latest["id"], body,
                                            reply_all)
                if r.get("error"):
                    return {"ok": False, "error": r["error"]}
                return {"ok": True, "text": "Reply saved to Gmail drafts.", "item_id": item_id, "keep": True}
            if r.get("error"):
                return {"ok": False, "error": r["error"]}
        else:
            return {"ok": False, "error": f"Unknown action {action}"}
        self._detach(item_id, undo)
        await self.publish()
        return {"ok": True, "text": text, "item_id": item_id, "undoable": action != "reply_send",
                "trashed_ids": [m["id"] for m in msgs] if action == "trash" else []}

    async def forget_messages(self, message_ids: list[str]) -> int:
        """An email was trashed elsewhere (HUD card / AUTHORIZE): drop any briefing item that contains it."""
        ids = set(message_ids)
        if not ids or not self.data:
            return 0
        gone = [it["id"] for it in self._all_items(self.data) if ids & {m["id"] for m in it["messages"]}]
        for item_id in gone:
            self._detach(item_id, None)
        if gone:
            await self.publish()
        return len(gone)

    async def _undo(self, item_id: str) -> dict:
        rec = self.removed.pop(item_id, None)
        if not rec:
            return {"ok": False, "error": "Nothing to undo."}
        u = rec["undo"] or {}
        for acct, ids in (u.get("by_acct") or {}).items():
            if u.get("untrash"):
                await asyncio.to_thread(gtools.gmail_untrash, acct, ids)
            if u.get("labels_add"):
                await asyncio.to_thread(gtools.gmail_labels_now, acct, ids, add=u["labels_add"])
        it = rec["item"]
        for m in it["messages"]:
            self.handled.pop(m["id"], None)
        rec["list"].insert(min(rec["index"], len(rec["list"])), it)
        self._save()
        await self.publish()
        return {"ok": True, "text": "Restored.", "item_id": item_id}


briefing = Briefing()


def gmail_url(email: str, thread_id: str) -> str:
    return f"https://mail.google.com/mail/?authuser={email}#all/{thread_id}"


__all__ = ["briefing", "Briefing", "gmail_url", "account_email"]
