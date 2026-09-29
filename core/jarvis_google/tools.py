"""Gmail / Calendar / Drive / Docs / Sheets / Contacts operations for JARVIS.

Read + reversible operations execute immediately. Outbound / destructive operations only
*propose* (see store.propose); they run via the registered executor after user confirmation.
"""
from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from googleapiclient.errors import HttpError

from . import store
from .accounts import account_email, resolve_account, service

BULK_MAX = 500


# ====================================================================== helpers

def _hdrs(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _decode(data: str | None) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n\s*", "\n\n", s).strip()


def _walk(part: dict, out: dict) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    if part.get("filename") and body.get("attachmentId"):
        out["attachments"].append({"filename": part["filename"], "mimeType": mime, "size": body.get("size", 0),
                                   "attachmentId": body["attachmentId"]})
    elif mime == "text/plain" and body.get("data"):
        out["plain"].append(_decode(body["data"]))
    elif mime == "text/html" and body.get("data"):
        out["html"].append(_decode(body["data"]))
    for p in part.get("parts", []) or []:
        _walk(p, out)


def _message_summary(m: dict) -> dict:
    h = _hdrs(m.get("payload", {}))
    labels = m.get("labelIds", [])
    name, addr = parseaddr(h.get("from", ""))
    return {
        "id": m["id"], "threadId": m.get("threadId"),
        "from": h.get("from", ""), "from_name": name or addr, "from_email": addr,
        "to": h.get("to", ""), "subject": h.get("subject", "(no subject)"), "date": h.get("date", ""),
        "internalDate": int(m.get("internalDate", 0)), "snippet": html.unescape(m.get("snippet", "")),
        "unread": "UNREAD" in labels, "starred": "STARRED" in labels, "important": "IMPORTANT" in labels,
        "labels": labels,
    }


def _message_full(m: dict, max_chars: int) -> dict:
    out = {"plain": [], "html": [], "attachments": []}
    _walk(m.get("payload", {}), out)
    body = "\n".join(out["plain"]).strip() or _html_to_text("\n".join(out["html"]))
    h = _hdrs(m.get("payload", {}))
    d = _message_summary(m)
    d.update({"cc": h.get("cc", ""), "message_id_header": h.get("message-id", ""),
              "references": h.get("references", ""), "body": body[:max_chars],
              "body_truncated": len(body) > max_chars, "attachments": out["attachments"]})
    return d


def _batch_get(gm, ids: list[str], fmt: str = "metadata") -> list[dict]:
    results: dict[str, dict] = {}

    def cb(req_id, resp, exc):
        if exc is None:
            results[req_id] = resp

    for i in range(0, len(ids), 50):
        batch = gm.new_batch_http_request(callback=cb)
        for mid in ids[i:i + 50]:
            kw = {"userId": "me", "id": mid, "format": fmt}
            if fmt == "metadata":
                kw["metadataHeaders"] = ["From", "To", "Subject", "Date"]
            batch.add(gm.users().messages().get(**kw), request_id=mid)
        batch.execute()
    return [results[i] for i in ids if i in results]


def _raw(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _build_mime(account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                html_body: str | None = None, in_reply_to: str = "", references: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = account_email(account)
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _recipients(*fields: str) -> list[str]:
    return [a for _, a in getaddresses([f for f in fields if f]) if a]


def _feed(tool: str, account: str | None, args: dict, result: Any) -> Any:
    store.record_result(tool, account, args, result)
    return result


def accounts_list() -> list[dict]:
    from .accounts import linked_accounts
    return linked_accounts()


# ====================================================================== Gmail: read

def gmail_search(account: str, query: str = "in:inbox", max_results: int = 10) -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    max_results = max(1, min(int(max_results), 50))
    resp = gm.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    msgs = [_message_summary(m) for m in _batch_get(gm, ids)]
    result = {"account": account, "email": account_email(account), "query": query,
              "result_size_estimate": resp.get("resultSizeEstimate", len(msgs)), "messages": msgs}
    return _feed("gmail_search", account, {"query": query, "max_results": max_results}, result)


def gmail_inbox_stats(account: str) -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    inbox = gm.users().labels().get(userId="me", id="INBOX").execute()
    result = {"account": account, "email": account_email(account),
              "inbox_total": inbox.get("messagesTotal"), "inbox_unread": inbox.get("messagesUnread"),
              "threads_unread": inbox.get("threadsUnread")}
    return _feed("gmail_inbox_stats", account, {}, result)


def gmail_read(account: str, message_id: str, max_chars: int = 20000) -> dict:
    account = resolve_account(account)
    m = service("gmail", account).users().messages().get(userId="me", id=message_id, format="full").execute()
    result = {"account": account, **_message_full(m, int(max_chars))}
    return _feed("gmail_read", account, {"message_id": message_id}, result)


def gmail_read_thread(account: str, thread_id: str, max_chars_per_message: int = 8000) -> dict:
    account = resolve_account(account)
    t = service("gmail", account).users().threads().get(userId="me", id=thread_id, format="full").execute()
    msgs = [_message_full(m, int(max_chars_per_message)) for m in t.get("messages", [])]
    result = {"account": account, "threadId": thread_id,
              "subject": msgs[0]["subject"] if msgs else "", "messages": msgs}
    return _feed("gmail_read_thread", account, {"thread_id": thread_id}, result)


def gmail_labels(account: str) -> dict:
    account = resolve_account(account)
    labels = service("gmail", account).users().labels().list(userId="me").execute().get("labels", [])
    return {"account": account, "labels": [{"id": l["id"], "name": l["name"], "type": l.get("type")} for l in labels]}


# ====================================================================== HUD-only helpers (no feed, no model)

def telemetry(account: str, tz_now_iso: str, horizon_iso: str) -> dict:
    account = resolve_account(account)
    inbox = service("gmail", account).users().labels().get(userId="me", id="INBOX").execute()
    items = service("calendar", account).events().list(
        calendarId="primary", timeMin=tz_now_iso, timeMax=horizon_iso, singleEvents=True, orderBy="startTime",
        maxResults=6).execute().get("items", [])
    events = [{"summary": e.get("summary", "(no title)"),
               "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
               "all_day": "date" in e.get("start", {}), "hangout": e.get("hangoutLink", "")}
              for e in items if not any(a.get("self") and a.get("responseStatus") == "declined"
                                        for a in e.get("attendees", []))]
    return {"account": account, "email": account_email(account), "unread": inbox.get("messagesUnread"),
            "threads_unread": inbox.get("threadsUnread"), "events": events}


def gmail_delete_draft(account: str, draft_id: str) -> dict:
    """UI-initiated only (Stephen clicked Discard on his own unsent draft)."""
    account = resolve_account(account)
    service("gmail", account).users().drafts().delete(userId="me", id=draft_id).execute()
    store._audit({"event": "executed", "kind": "gmail_delete_draft", "account": account, "source": "click",
                  "draft_id": draft_id})
    return {"status": "draft_deleted", "account": account, "draft_id": draft_id}


# ---- Briefing buttons. Stephen clicked a button on a specific, visible email, so the click IS the
# confirmation. These are NOT exposed over MCP: the model can still only propose trash/send.

def _click_audit(kind: str, account: str, **extra) -> None:
    store._audit({"event": "executed", "kind": kind, "account": account, "source": "briefing_click", **extra})


def gmail_trash_now(account: str, message_ids: list[str]) -> dict:
    account = resolve_account(account)
    ids = list(dict.fromkeys(message_ids))[:BULK_MAX]
    r = _x_trash(account, ids)
    _click_audit("gmail_trash", account, message_ids=ids)
    return {"status": "trashed", "account": account, "message_ids": ids, **r}


def gmail_untrash(account: str, message_ids: list[str]) -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    for mid in message_ids[:BULK_MAX]:
        gm.users().messages().untrash(userId="me", id=mid).execute()
    _click_audit("gmail_untrash", account, message_ids=message_ids)
    return {"status": "restored", "account": account, "message_ids": message_ids}


def gmail_restore(account: str, message_ids: list[str]) -> dict:
    """Undo for a click-trash: untrash AND put back in the inbox (Gmail's untrash alone doesn't)."""
    r = gmail_untrash(account, message_ids)
    gmail_labels_now(account, message_ids, add=["INBOX"])
    return r


def gmail_labels_now(account: str, message_ids: list[str], add: list[str] | None = None,
                     remove: list[str] | None = None) -> dict:
    """Quiet label change (archive / mark read / undo) with no HUD feed card."""
    account = resolve_account(account)
    ids = list(dict.fromkeys(message_ids))[:BULK_MAX]
    service("gmail", account).users().messages().batchModify(
        userId="me", body={"ids": ids, "addLabelIds": list(add or []), "removeLabelIds": list(remove or [])}).execute()
    _click_audit("gmail_labels", account, count=len(ids), add=add, remove=remove)
    return {"status": "modified", "account": account, "message_ids": ids}


def gmail_reply_now(account: str, message_id: str, body: str, reply_all: bool = False) -> dict:
    if not body.strip():
        return {"error": "empty reply"}
    account = resolve_account(account)
    r = _x_reply(account, message_id, body, reply_all)
    _click_audit("gmail_reply", account, message_id=message_id, reply_all=reply_all, chars=len(body))
    return {"status": "sent", "account": account, **r}


def gmail_reply_draft_now(account: str, message_id: str, body: str, reply_all: bool = False) -> dict:
    """Save a reply as a threaded Gmail draft (not sent); recipients resolved exactly like gmail_reply."""
    account = resolve_account(account)
    gm = service("gmail", account)
    orig = gm.users().messages().get(userId="me", id=message_id, format="metadata",
                                     metadataHeaders=["From", "To", "Cc", "Reply-To"]).execute()
    h = _hdrs(orig.get("payload", {}))
    me = account_email(account).lower()
    to = h.get("reply-to") or h.get("from", "")
    cc = ", ".join(a for a in _recipients(h.get("to", ""), h.get("cc", "")) if a.lower() != me) if reply_all else ""
    d = gmail_create_draft(account, to, "", body, cc, "", reply_to_message_id=message_id)
    _click_audit("gmail_reply_draft", account, message_id=message_id, draft_id=d.get("draft_id"))
    return d


# ====================================================================== Gmail: drafts (safe)

def gmail_create_draft(account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                       reply_to_message_id: str = "") -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    thread_id, irt, refs = None, "", ""
    if reply_to_message_id:
        orig = gm.users().messages().get(userId="me", id=reply_to_message_id, format="metadata",
                                         metadataHeaders=["Message-ID", "References", "Subject"]).execute()
        h = _hdrs(orig.get("payload", {}))
        thread_id, irt, refs = orig.get("threadId"), h.get("message-id", ""), h.get("references", "")
        if not subject:
            s = h.get("subject", "")
            subject = s if s.lower().startswith("re:") else f"Re: {s}"
    msg = _build_mime(account, to, subject, body, cc, bcc, in_reply_to=irt, references=refs)
    draft_body: dict = {"message": {"raw": _raw(msg)}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id
    d = gm.users().drafts().create(userId="me", body=draft_body).execute()
    result = {"status": "draft_created", "account": account, "draft_id": d["id"],
              "message_id": d["message"]["id"], "threadId": d["message"].get("threadId"),
              "from": account_email(account), "to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body,
              "note": "Saved as a Gmail draft. NOT sent."}
    return _feed("gmail_create_draft", account, {"to": to, "subject": subject}, result)


def gmail_update_draft(account: str, draft_id: str, to: str, subject: str, body: str, cc: str = "",
                       bcc: str = "") -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    existing = gm.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
    h = _hdrs(existing["message"].get("payload", {}))
    msg = _build_mime(account, to, subject, body, cc, bcc,
                      in_reply_to=h.get("in-reply-to", ""), references=h.get("references", ""))
    msg_body: dict = {"raw": _raw(msg)}
    if existing["message"].get("threadId"):
        msg_body["threadId"] = existing["message"]["threadId"]
    d = gm.users().drafts().update(userId="me", id=draft_id, body={"message": msg_body}).execute()
    result = {"status": "draft_updated", "account": account, "draft_id": d["id"], "to": to, "cc": cc,
              "bcc": bcc, "subject": subject, "body": body, "from": account_email(account)}
    return _feed("gmail_update_draft", account, {"draft_id": draft_id}, result)


def gmail_list_drafts(account: str, max_results: int = 10) -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    ds = gm.users().drafts().list(userId="me", maxResults=max(1, min(int(max_results), 50))).execute()
    out = []
    for d in ds.get("drafts", []):
        full = gm.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
        s = _message_summary(full["message"])
        out.append({"draft_id": d["id"], "to": s["to"], "subject": s["subject"], "snippet": s["snippet"]})
    return _feed("gmail_list_drafts", account, {}, {"account": account, "drafts": out})


# ====================================================================== Gmail: reversible modify

def gmail_modify(account: str, message_ids: list[str], mark_read: bool | None = None,
                 archive: bool = False, star: bool | None = None, add_labels: list[str] | None = None,
                 remove_labels: list[str] | None = None) -> dict:
    """Mark read/unread, archive, star, add/remove labels. All reversible, so no confirmation needed."""
    account = resolve_account(account)
    ids = list(message_ids)[:BULK_MAX]
    add, rem = list(add_labels or []), list(remove_labels or [])
    if mark_read is True:
        rem.append("UNREAD")
    elif mark_read is False:
        add.append("UNREAD")
    if archive:
        rem.append("INBOX")
    if star is True:
        add.append("STARRED")
    elif star is False:
        rem.append("STARRED")
    if not (add or rem):
        return {"error": "nothing to change"}
    service("gmail", account).users().messages().batchModify(
        userId="me", body={"ids": ids, "addLabelIds": add, "removeLabelIds": rem}).execute()
    store._audit({"event": "executed", "kind": "gmail_modify", "account": account, "source": "direct",
                  "count": len(ids), "add": add, "remove": rem})
    return _feed("gmail_modify", account, {"count": len(ids)},
                 {"status": "modified", "account": account, "count": len(ids), "added": add, "removed": rem})


# ====================================================================== Gmail: proposals (need confirm)

def _preview_messages(account: str, ids: list[str], n: int = 5) -> list[dict]:
    gm = service("gmail", account)
    return [{k: s[k] for k in ("from_name", "subject", "date")} for s in map(_message_summary, _batch_get(gm, ids[:n]))]


def gmail_send(account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    account = resolve_account(account)
    rcpts = _recipients(to, cc, bcc)
    if not rcpts:
        return {"error": "no valid recipients"}
    preview = {"type": "email_send", "from": account_email(account), "to": to, "cc": cc, "bcc": bcc,
               "subject": subject, "body": body}
    return store.propose("gmail_send", account, {"to": to, "subject": subject, "body": body, "cc": cc, "bcc": bcc},
                         f"Send email from {account_email(account)} to {', '.join(rcpts)}: \"{subject}\"", preview)


def gmail_send_draft(account: str, draft_id: str) -> dict:
    account = resolve_account(account)
    d = service("gmail", account).users().drafts().get(userId="me", id=draft_id, format="full").execute()
    full = _message_full(d["message"], 4000)
    preview = {"type": "email_send", "from": account_email(account), "to": full["to"], "cc": full["cc"],
               "subject": full["subject"], "body": full["body"], "draft_id": draft_id}
    return store.propose("gmail_send_draft", account, {"draft_id": draft_id},
                         f"Send draft to {full['to']}: \"{full['subject']}\"", preview)


def gmail_reply(account: str, message_id: str, body: str, reply_all: bool = False) -> dict:
    account = resolve_account(account)
    gm = service("gmail", account)
    orig = gm.users().messages().get(userId="me", id=message_id, format="metadata",
                                     metadataHeaders=["From", "To", "Cc", "Subject", "Reply-To"]).execute()
    h = _hdrs(orig.get("payload", {}))
    me = account_email(account).lower()
    to = h.get("reply-to") or h.get("from", "")
    cc = ""
    if reply_all:
        others = [a for a in _recipients(h.get("to", ""), h.get("cc", "")) if a.lower() != me]
        cc = ", ".join(others)
    s = h.get("subject", "")
    subject = s if s.lower().startswith("re:") else f"Re: {s}"
    preview = {"type": "email_send", "from": account_email(account), "to": to, "cc": cc, "subject": subject,
               "body": body, "in_reply_to": {"from": h.get("from"), "subject": s}}
    return store.propose("gmail_reply", account, {"message_id": message_id, "body": body, "reply_all": reply_all},
                         f"Reply{' all' if reply_all else ''} to {to}: \"{subject}\"", preview)


def gmail_trash(account: str, message_ids: list[str]) -> dict:
    account = resolve_account(account)
    ids = list(dict.fromkeys(message_ids))[:BULK_MAX]
    if not ids:
        return {"error": "no message ids"}
    preview = {"type": "email_trash", "count": len(ids), "sample": _preview_messages(account, ids)}
    return store.propose("gmail_trash", account, {"message_ids": ids},
                         f"Move {len(ids)} email(s) to Trash in {account_email(account)} (recoverable for 30 days)",
                         preview)


def gmail_delete_permanently(account: str, message_ids: list[str]) -> dict:
    account = resolve_account(account)
    ids = list(dict.fromkeys(message_ids))[:BULK_MAX]
    if not ids:
        return {"error": "no message ids"}
    preview = {"type": "email_delete", "count": len(ids), "sample": _preview_messages(account, ids),
               "danger": "PERMANENT. Cannot be undone."}
    return store.propose("gmail_delete_permanently", account, {"message_ids": ids},
                         f"PERMANENTLY delete {len(ids)} email(s) from {account_email(account)}", preview)


@store.executor("gmail_send")
def _x_send(account, to, subject, body, cc="", bcc=""):
    msg = _build_mime(account, to, subject, body, cc, bcc)
    r = service("gmail", account).users().messages().send(userId="me", body={"raw": _raw(msg)}).execute()
    return {"message_id": r["id"], "threadId": r.get("threadId")}


@store.executor("gmail_send_draft")
def _x_send_draft(account, draft_id):
    r = service("gmail", account).users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return {"message_id": r["id"], "threadId": r.get("threadId")}


@store.executor("gmail_reply")
def _x_reply(account, message_id, body, reply_all=False):
    gm = service("gmail", account)
    orig = gm.users().messages().get(userId="me", id=message_id, format="metadata",
                                     metadataHeaders=["From", "To", "Cc", "Subject", "Reply-To", "Message-ID",
                                                      "References"]).execute()
    h = _hdrs(orig.get("payload", {}))
    me = account_email(account).lower()
    to = h.get("reply-to") or h.get("from", "")
    cc = ", ".join(a for a in _recipients(h.get("to", ""), h.get("cc", "")) if a.lower() != me) if reply_all else ""
    s = h.get("subject", "")
    subject = s if s.lower().startswith("re:") else f"Re: {s}"
    msg = _build_mime(account, to, subject, body, cc, in_reply_to=h.get("message-id", ""),
                      references=h.get("references", ""))
    r = gm.users().messages().send(userId="me", body={"raw": _raw(msg), "threadId": orig["threadId"]}).execute()
    return {"message_id": r["id"], "threadId": r.get("threadId")}


@store.executor("gmail_trash")
def _x_trash(account, message_ids):
    gm = service("gmail", account)
    gm.users().messages().batchModify(userId="me", body={"ids": message_ids, "addLabelIds": ["TRASH"]}).execute()
    return {"trashed": len(message_ids)}


@store.executor("gmail_delete_permanently")
def _x_delete(account, message_ids):
    service("gmail", account).users().messages().batchDelete(userId="me", body={"ids": message_ids}).execute()
    return {"deleted": len(message_ids)}


# ====================================================================== Calendar

def calendar_list(account: str, time_min: str, time_max: str, query: str = "", max_results: int = 50) -> dict:
    account = resolve_account(account)
    kw = {"calendarId": "primary", "timeMin": time_min, "timeMax": time_max, "singleEvents": True,
          "orderBy": "startTime", "maxResults": max(1, min(int(max_results), 250))}
    if query:
        kw["q"] = query
    items = service("calendar", account).events().list(**kw).execute().get("items", [])
    events = [{
        "id": e["id"], "summary": e.get("summary", "(no title)"),
        "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
        "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
        "all_day": "date" in e.get("start", {}), "location": e.get("location", ""),
        "hangout": e.get("hangoutLink", ""), "organizer": e.get("organizer", {}).get("email", ""),
        "attendees": [{"email": a.get("email"), "status": a.get("responseStatus")} for a in e.get("attendees", [])][:25],
        "my_status": next((a.get("responseStatus") for a in e.get("attendees", []) if a.get("self")), None),
        "description": (e.get("description") or "")[:1500], "htmlLink": e.get("htmlLink"),
    } for e in items]
    result = {"account": account, "email": account_email(account), "time_min": time_min, "time_max": time_max,
              "events": events}
    return _feed("calendar_list", account, {"time_min": time_min, "time_max": time_max, "query": query}, result)


def calendar_create(account: str, summary: str, start: str, end: str, attendees: list[str] | None = None,
                    location: str = "", description: str = "", add_meet: bool = False) -> dict:
    """Events with no guests are created immediately; events that invite people need confirmation."""
    account = resolve_account(account)
    params = {"summary": summary, "start": start, "end": end, "attendees": list(attendees or []),
              "location": location, "description": description, "add_meet": add_meet}
    if params["attendees"]:
        preview = {"type": "calendar_invite", **params}
        return store.propose("calendar_create", account, params,
                             f"Create \"{summary}\" {start} and send invites to {', '.join(params['attendees'])}",
                             preview)
    r = _x_cal_create(account, **params)
    return _feed("calendar_create", account, {"summary": summary},
                 {"status": "created", "account": account, **params, **r})


@store.executor("calendar_create")
def _x_cal_create(account, summary, start, end, attendees=(), location="", description="", add_meet=False):
    def t(v):
        return {"date": v} if len(v) == 10 else {"dateTime": v}
    body = {"summary": summary, "start": t(start), "end": t(end), "location": location, "description": description,
            "attendees": [{"email": a} for a in attendees]}
    kw = {"calendarId": "primary", "body": body, "sendUpdates": "all" if attendees else "none"}
    if add_meet:
        import uuid
        body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex}}
        kw["conferenceDataVersion"] = 1
    e = service("calendar", account).events().insert(**kw).execute()
    return {"event_id": e["id"], "htmlLink": e.get("htmlLink"), "hangout": e.get("hangoutLink", "")}


def calendar_delete(account: str, event_id: str) -> dict:
    account = resolve_account(account)
    e = service("calendar", account).events().get(calendarId="primary", eventId=event_id).execute()
    start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
    preview = {"type": "calendar_delete", "summary": e.get("summary"), "start": start,
               "attendees": [a.get("email") for a in e.get("attendees", [])]}
    return store.propose("calendar_delete", account, {"event_id": event_id},
                         f"Delete calendar event \"{e.get('summary')}\" ({start})", preview)


@store.executor("calendar_delete")
def _x_cal_delete(account, event_id):
    service("calendar", account).events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
    return {"deleted": event_id}


# ====================================================================== Drive / Docs / Sheets

_DRIVE_FIELDS = "files(id,name,mimeType,modifiedTime,webViewLink,iconLink,thumbnailLink,owners(displayName),size)"


def drive_search(account: str, query: str = "", max_results: int = 12, raw_query: bool = False) -> dict:
    account = resolve_account(account)
    q = query if raw_query else (f"fullText contains '{query.replace(chr(39), chr(92) + chr(39))}' and trashed = false"
                                 if query else "trashed = false")
    kw = {"q": q, "pageSize": max(1, min(int(max_results), 50)), "fields": _DRIVE_FIELDS,
          "supportsAllDrives": True, "includeItemsFromAllDrives": True}
    if raw_query or not query:  # fullText searches can't be ordered
        kw["orderBy"] = "modifiedTime desc"
    files = service("drive", account).files().list(**kw).execute().get("files", [])
    return _feed("drive_search", account, {"query": query}, {"account": account, "query": query, "files": files})


def drive_read_text(account: str, file_id: str, max_chars: int = 30000) -> dict:
    """Plain-text content of a Google Doc/Sheet/Slide or text-like file."""
    account = resolve_account(account)
    dr = service("drive", account)
    meta = dr.files().get(fileId=file_id, fields="id,name,mimeType,webViewLink", supportsAllDrives=True).execute()
    mt = meta["mimeType"]
    export = {"application/vnd.google-apps.document": "text/plain",
              "application/vnd.google-apps.spreadsheet": "text/csv",
              "application/vnd.google-apps.presentation": "text/plain"}.get(mt)
    if export:
        data = dr.files().export(fileId=file_id, mimeType=export).execute()
    elif mt.startswith("text/") or mt in ("application/json",):
        data = dr.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    else:
        return {"account": account, **meta, "error": f"cannot extract text from {mt}"}
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    return _feed("drive_read_text", account, {"file_id": file_id},
                 {"account": account, **meta, "text": text[:max_chars], "truncated": len(text) > max_chars})


def drive_share(account: str, file_id: str, email: str, role: str = "reader") -> dict:
    account = resolve_account(account)
    meta = service("drive", account).files().get(fileId=file_id, fields="name", supportsAllDrives=True).execute()
    return store.propose("drive_share", account, {"file_id": file_id, "email": email, "role": role},
                         f"Share \"{meta['name']}\" with {email} as {role}",
                         {"type": "drive_share", "name": meta["name"], "email": email, "role": role})


@store.executor("drive_share")
def _x_share(account, file_id, email, role):
    p = service("drive", account).permissions().create(
        fileId=file_id, body={"type": "user", "role": role, "emailAddress": email},
        sendNotificationEmail=True, supportsAllDrives=True).execute()
    return {"permission_id": p["id"]}


def drive_trash(account: str, file_id: str) -> dict:
    account = resolve_account(account)
    meta = service("drive", account).files().get(fileId=file_id, fields="name,mimeType",
                                                 supportsAllDrives=True).execute()
    return store.propose("drive_trash", account, {"file_id": file_id}, f"Move \"{meta['name']}\" to Drive Trash",
                         {"type": "drive_trash", "name": meta["name"], "mimeType": meta["mimeType"]})


@store.executor("drive_trash")
def _x_drive_trash(account, file_id):
    service("drive", account).files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()
    return {"trashed": file_id}


def docs_create(account: str, title: str, body: str = "") -> dict:
    account = resolve_account(account)
    d = service("docs", account).documents().create(body={"title": title}).execute()
    if body:
        service("docs", account).documents().batchUpdate(
            documentId=d["documentId"], body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]}
        ).execute()
    url = f"https://docs.google.com/document/d/{d['documentId']}/edit"
    return _feed("docs_create", account, {"title": title},
                 {"status": "created", "account": account, "document_id": d["documentId"], "title": title, "url": url})


def sheets_read(account: str, spreadsheet_id: str, range_a1: str = "A1:Z200") -> dict:
    account = resolve_account(account)
    v = service("sheets", account).spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_a1).execute()
    return _feed("sheets_read", account, {"spreadsheet_id": spreadsheet_id, "range": range_a1},
                 {"account": account, "range": v.get("range"), "values": v.get("values", [])})


def sheets_write(account: str, spreadsheet_id: str, range_a1: str, values: list[list[Any]], append: bool = False) -> dict:
    account = resolve_account(account)
    title = service("sheets", account).spreadsheets().get(spreadsheetId=spreadsheet_id,
                                                          fields="properties.title").execute()["properties"]["title"]
    verb = "Append" if append else "Overwrite"
    return store.propose("sheets_write", account,
                         {"spreadsheet_id": spreadsheet_id, "range_a1": range_a1, "values": values, "append": append},
                         f"{verb} {len(values)} row(s) in \"{title}\" {range_a1}",
                         {"type": "sheets_write", "title": title, "range": range_a1, "values": values[:20]})


@store.executor("sheets_write")
def _x_sheets(account, spreadsheet_id, range_a1, values, append=False):
    vals = service("sheets", account).spreadsheets().values()
    kw = {"spreadsheetId": spreadsheet_id, "range": range_a1, "valueInputOption": "USER_ENTERED",
          "body": {"values": values}}
    r = (vals.append(**kw) if append else vals.update(**kw)).execute()
    return {"updated": r.get("updates", r).get("updatedCells")}


# ====================================================================== Contacts

def contacts_search(account: str, query: str, max_results: int = 10) -> dict:
    account = resolve_account(account)
    ppl = service("people", account).people()
    mask = "names,emailAddresses,phoneNumbers,organizations"
    people: list[dict] = []
    try:
        ppl.searchContacts(query="", readMask="names").execute()  # cache warm-up required by the API
        people += [r["person"] for r in ppl.searchContacts(query=query, readMask=mask, pageSize=max_results)
                   .execute().get("results", [])]
    except HttpError:
        pass
    if account == "work":
        try:
            people += ppl.searchDirectoryPeople(query=query, readMask=mask, pageSize=max_results,
                                                sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"]
                                                ).execute().get("people", [])
        except HttpError:
            pass
    out: dict[str, dict] = {}
    for p in people:
        emails = [e["value"] for e in p.get("emailAddresses", [])]
        key = (emails[0] if emails else p.get("resourceName", "")).lower()
        out.setdefault(key, {"name": (p.get("names") or [{}])[0].get("displayName", ""), "emails": emails,
                             "phones": [x["value"] for x in p.get("phoneNumbers", [])],
                             "org": (p.get("organizations") or [{}])[0].get("name", ""), "source": "contacts"})
    if not out:  # fall back to correspondents found in mail
        for m in gmail_search(account, f"from:({query})", 10)["messages"]:
            out.setdefault(m["from_email"].lower(), {"name": m["from_name"], "emails": [m["from_email"]],
                                                     "phones": [], "org": "", "source": "gmail"})
    return {"account": account, "query": query, "contacts": list(out.values())[:max_results]}


# ====================================================================== weather (no Google; lives here for the feed)
def weather_lookup(location: str = "", days: int = 7, units: str = "") -> dict:
    from .weather import weather
    try:
        r = weather(location, days, units)
    except ValueError as e:
        return {"error": str(e)}
    return _feed("weather", None, {"location": location}, r)
