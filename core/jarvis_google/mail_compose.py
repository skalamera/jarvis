"""Gmail compose from the HUD (click-only: these run because Stephen clicked Send / Save in the composer).

Rich HTML body + plain-text alternative, To/Cc/Bcc, uploaded attachments, threaded replies, forwards that carry the
original attachments, drafts that can be re-opened and updated. Every send/save is audit-logged."""
from __future__ import annotations

import base64
import html as H
import re
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any

from . import store
from .accounts import account_email, resolve_account, service
from .tools import _click_audit, _hdrs, _html_to_text, _raw, _walk

MAX_TOTAL = 24 * 1024 * 1024  # Gmail's 25 MB limit, minus headroom


def _clean(addrs: Any) -> str:
    if isinstance(addrs, list):
        addrs = ", ".join(str(a) for a in addrs)
    return ", ".join(f"{n} <{a}>" if n else a for n, a in getaddresses([addrs or ""]) if a)


def _full(gm, message_id: str) -> dict:
    return gm.users().messages().get(userId="me", id=message_id, format="full").execute()


def _bodies(m: dict) -> tuple[str, str, list[dict]]:
    out: dict = {"plain": [], "html": [], "attachments": []}
    _walk(m.get("payload", {}), out)
    html = "\n".join(out["html"]).strip()
    text = "\n".join(out["plain"]).strip() or _html_to_text(html)
    if not html:
        html = "<div>" + H.escape(text).replace("\n", "<br>") + "</div>"
    return html, text, out["attachments"]


_STRIP = re.compile(r"<(script|style|iframe|object|embed)[\s\S]*?</\1>|<(script|iframe|object|embed)[^>]*/?>", re.I)


def mail_html(account: str, message_id: str) -> dict:
    """The message's HTML for display (rendered by the HUD in a sandboxed frame: no scripts)."""
    account = resolve_account(account)
    m = _full(service("gmail", account), message_id)
    html, _, atts = _bodies(m)
    return {"html": _STRIP.sub("", html), "attachments": atts}


def mail_context(account: str, message_id: str, mode: str = "reply") -> dict:
    """Prefill for Reply / Reply all / Forward: recipients, subject, quoted original, forwardable attachments."""
    account = resolve_account(account)
    gm = service("gmail", account)
    m = _full(gm, message_id)
    h = _hdrs(m.get("payload", {}))
    me = account_email(account).lower()
    html, _, atts = _bodies(m)
    subj = h.get("subject", "")
    when = h.get("date", "")
    sender = h.get("from", "")
    if mode == "forward":
        subject = subj if subj.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subj}"
        to, cc = [], []
        quote = (f"<br><br><div>---------- Forwarded message ---------<br>From: {H.escape(sender)}<br>Date: {H.escape(when)}"
                 f"<br>Subject: {H.escape(subj)}<br>To: {H.escape(h.get('to', ''))}"
                 + (f"<br>Cc: {H.escape(h.get('cc', ''))}" if h.get("cc") else "") + f"<br><br>{_STRIP.sub('', html)}</div>")
    else:
        subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        to = [a for a in [_clean(h.get("reply-to") or sender)] if a]
        cc = []
        if mode == "reply_all":
            seen = {a.lower() for _, a in getaddresses(to)} | {me}
            for n, a in getaddresses([h.get("to", ""), h.get("cc", "")]):
                if a and a.lower() not in seen:
                    seen.add(a.lower())
                    cc.append(f"{n} <{a}>" if n else a)
        quote = (f"<br><br><div class=\"gmail_quote\">On {H.escape(when)}, {H.escape(sender)} wrote:<br>"
                 f"<blockquote style=\"margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex\">"
                 f"{_STRIP.sub('', html)}</blockquote></div>")
    return {"account": account, "from": account_email(account), "mode": mode, "to": to, "cc": cc, "bcc": [],
            "subject": subject, "quote": quote, "thread_id": m.get("threadId"), "message_id": message_id,
            "forward_attachments": [{"id": a.get("attachmentId"), "filename": a.get("filename"), "size": a.get("size"),
                                     "mimeType": a.get("mimeType")} for a in atts] if mode == "forward" else []}


def mail_ai_reply(account: str, message_id: str, mode: str = "reply", steer: str = "") -> dict:
    """JARVIS drafts the reply body (HTML) in Stephen's voice; nothing is sent or saved."""
    from .garage import _gemini
    account = resolve_account(account)
    gm = service("gmail", account)
    m = _full(gm, message_id)
    h = _hdrs(m.get("payload", {}))
    _, text, _ = _bodies(m)
    prompt = (
        "You are writing an email reply AS Stephen Skalamera (first person). Write only the body: a short greeting, "
        "the substance, and a sign-off with just 'Stephen'. Warm, direct, plain language; no em dashes; no filler; "
        "don't invent facts, commitments, dates or numbers that aren't in the email (leave [brackets] where he must "
        "fill something in). Output simple HTML only (<p>, <br>, <ul><li>, <b>), no <html>/<body>, no quoted original."
        + (f"\nHis instructions for this reply: {steer}" if steer else "")
        + f"\n\nAccount: {account}\nFrom: {h.get('from', '')}\nTo: {h.get('to', '')}\nCc: {h.get('cc', '')}"
        f"\nSubject: {h.get('subject', '')}\n\n{text[:12000]}")
    out = _gemini([{"text": prompt}], json_out=False, timeout=90)
    body = re.sub(r"^```(?:html)?\s*|\s*```$", "", (out or "").strip())
    return {"html": body}


def _build(account: str, to: str, cc: str, bcc: str, subject: str, html: str, attachments: list[dict],
           forward: dict | None, irt: str = "", refs: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = account_email(account)
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if irt:
        msg["In-Reply-To"] = irt
        msg["References"] = (refs + " " + irt).strip()
    msg.set_content(_html_to_text(html) or " ")
    msg.add_alternative(f"<div dir=\"ltr\">{html}</div>", subtype="html")
    total = 0
    for a in attachments or []:
        data = base64.b64decode(a["data"])
        total += len(data)
        mt = (a.get("type") or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=mt[0], subtype=mt[1] if len(mt) > 1 else "octet-stream", filename=a["name"])
    if forward:
        gm = service("gmail", account)
        for a in forward.get("attachments") or []:
            raw = gm.users().messages().attachments().get(userId="me", messageId=forward["message_id"], id=a["id"]).execute()
            data = base64.urlsafe_b64decode(raw["data"])
            total += len(data)
            mt = (a.get("mimeType") or "application/octet-stream").split("/", 1)
            msg.add_attachment(data, maintype=mt[0], subtype=mt[1] if len(mt) > 1 else "octet-stream",
                               filename=a.get("filename") or "attachment")
    if total > MAX_TOTAL:
        raise ValueError(f"Attachments total {total / 1e6:.1f} MB; Gmail's limit is 25 MB.")
    return msg


def _thread_headers(gm, message_id: str) -> tuple[str, str, str]:
    if not message_id:
        return "", "", ""
    o = gm.users().messages().get(userId="me", id=message_id, format="metadata",
                                  metadataHeaders=["Message-ID", "References"]).execute()
    hh = _hdrs(o.get("payload", {}))
    return o.get("threadId", ""), hh.get("message-id", ""), hh.get("references", "")


def _prep(account: str, to: Any, cc: Any, bcc: Any, subject: str, html: str, attachments: list | None,
          reply_to_message_id: str, mode: str, forward_attachments: list | None):
    account = resolve_account(account)
    gm = service("gmail", account)
    to, cc, bcc = _clean(to), _clean(cc), _clean(bcc)
    thread, irt, refs = _thread_headers(gm, reply_to_message_id) if mode != "forward" else ("", "", "")
    fwd = {"message_id": reply_to_message_id, "attachments": forward_attachments} if mode == "forward" and forward_attachments else None
    msg = _build(account, to, cc, bcc, subject, html, attachments or [], fwd, irt, refs)
    body: dict = {"raw": _raw(msg)}
    if thread:
        body["threadId"] = thread
    return account, gm, body, to, cc, bcc


def mail_send(account: str, to: Any, subject: str, html: str, cc: Any = "", bcc: Any = "",
              attachments: list | None = None, reply_to_message_id: str = "", mode: str = "new",
              forward_attachments: list | None = None, draft_id: str = "") -> dict:
    """Send (he clicked Send in the composer)."""
    if not _clean(to) and not _clean(cc) and not _clean(bcc):
        raise ValueError("Add at least one recipient.")
    account, gm, body, to, cc, bcc = _prep(account, to, cc, bcc, subject, html, attachments, reply_to_message_id,
                                           mode, forward_attachments)
    r = gm.users().messages().send(userId="me", body=body).execute()
    if draft_id:
        try:
            gm.users().drafts().delete(userId="me", id=draft_id).execute()
        except Exception:
            pass
    _click_audit("mail_send", account, to=to, cc=cc, bcc=bcc, subject=subject, mode=mode,
                 attachments=len(attachments or []) + len(forward_attachments or []), message_id=r.get("id"))
    return {"status": "sent", "message_id": r.get("id"), "threadId": r.get("threadId")}


def mail_save_draft(account: str, to: Any, subject: str, html: str, cc: Any = "", bcc: Any = "",
                    attachments: list | None = None, reply_to_message_id: str = "", mode: str = "new",
                    forward_attachments: list | None = None, draft_id: str = "") -> dict:
    account, gm, body, to, cc, bcc = _prep(account, to, cc, bcc, subject, html, attachments, reply_to_message_id,
                                           mode, forward_attachments)
    if draft_id:
        d = gm.users().drafts().update(userId="me", id=draft_id, body={"message": body}).execute()
    else:
        d = gm.users().drafts().create(userId="me", body={"message": body}).execute()
        store.remember_own_draft(account, d["id"])
    _click_audit("mail_save_draft", account, draft_id=d["id"], to=to, subject=subject)
    return {"status": "draft_saved", "draft_id": d["id"]}


def mail_discard_draft(account: str, draft_id: str) -> dict:
    account = resolve_account(account)
    service("gmail", account).users().drafts().delete(userId="me", id=draft_id).execute()
    _click_audit("mail_discard_draft", account, draft_id=draft_id)
    return {"status": "discarded"}


def mail_inbox(account: str, query: str = "in:inbox", max_results: int = 25) -> dict:
    from .tools import gmail_search
    return gmail_search(account, query, max_results, show=False)


def mail_contacts(account: str, q: str) -> list[dict]:
    """Address suggestions from people he's emailed with (recent headers)."""
    from .tools import gmail_search
    if len(q.strip()) < 2:
        return []
    r = gmail_search(account, f"{{from:{q} to:{q} cc:{q}}}", 15, show=False)
    seen, out = set(), []
    for m in r.get("messages") or []:
        for name, addr in [(m.get("from_name"), m.get("from_email"))]:
            if addr and addr.lower() not in seen and q.lower() in f"{name} {addr}".lower():
                seen.add(addr.lower())
                out.append({"name": name or "", "email": addr})
    return out[:8]


CLICK_OPS = {"mail_html": mail_html, "mail_context": mail_context, "mail_ai_reply": mail_ai_reply,
             "mail_send": mail_send, "mail_save_draft": mail_save_draft, "mail_discard_draft": mail_discard_draft,
             "mail_inbox": mail_inbox, "mail_contacts": mail_contacts}
