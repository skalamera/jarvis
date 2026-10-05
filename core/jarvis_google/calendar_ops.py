"""Click-only calendar ops for the expanded calendar card (month / week / day views).
He drives these with his own clicks (and the HUD asks before deleting events with guests), so they run directly
instead of going through the model's confirm flow. Every write is audit-logged."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import store
from .accounts import ACCOUNTS, account_email, resolve_account, service, token_path


def _connected() -> list[str]:
    return [a for a in ACCOUNTS if token_path(a).exists()]


def _ev(e: dict, account: str) -> dict:
    s, en = e.get("start", {}), e.get("end", {})
    return {
        "id": e["id"], "account": account, "summary": e.get("summary", "(no title)"),
        "start": s.get("dateTime") or s.get("date"), "end": en.get("dateTime") or en.get("date"),
        "all_day": "date" in s, "location": e.get("location", ""), "description": (e.get("description") or "")[:4000],
        "hangout": e.get("hangoutLink", ""), "htmlLink": e.get("htmlLink"),
        "attendees": [a.get("email") for a in e.get("attendees", []) if not a.get("self")][:50],
        "my_status": next((a.get("responseStatus") for a in e.get("attendees", []) if a.get("self")), None),
        "organizer_self": bool(e.get("organizer", {}).get("self", True)),
        "recurring": bool(e.get("recurringEventId")),
    }


def cal_events(time_min: str, time_max: str) -> dict:
    """Events from every connected calendar in [time_min, time_max)."""
    def one(a):
        items, tok = [], None
        while True:
            r = service("calendar", a).events().list(
                calendarId="primary", timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy="startTime",
                maxResults=250, pageToken=tok).execute()
            items += r.get("items", [])
            tok = r.get("nextPageToken")
            if not tok or len(items) > 1500:
                break
        return [_ev(e, a) for e in items if e.get("status") != "cancelled"]
    accts = _connected()
    out, errors = [], {}
    with ThreadPoolExecutor(len(accts) or 1) as ex:
        futs = {a: ex.submit(one, a) for a in accts}
        for a, f in futs.items():
            try:
                out += f.result()
            except Exception as e:
                errors[a] = str(e)[:200]
    return {"events": out, "accounts": [{"account": a, "email": account_email(a)} for a in accts], "errors": errors}


def _when(start: str, end: str, all_day: bool) -> tuple[dict, dict]:
    if all_day:
        return {"date": start[:10]}, {"date": end[:10]}
    return {"dateTime": start}, {"dateTime": end}


def cal_create(account: str, summary: str, start: str, end: str, all_day: bool = False, location: str = "",
               description: str = "") -> dict:
    account = resolve_account(account)
    s, e = _when(start, end, all_day)
    body = {"summary": summary or "(no title)", "start": s, "end": e, "location": location, "description": description}
    ev = service("calendar", account).events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
    store.audit({"kind": "calendar_create", "account": account, "event": ev["id"], "source": "click"})
    return _ev(ev, account)


def cal_update(account: str, event_id: str, summary: str, start: str, end: str, all_day: bool = False,
               location: str = "", description: str = "", new_account: str = "") -> dict:
    account = resolve_account(account)
    svc = service("calendar", account)
    s, e = _when(start, end, all_day)
    body = {"summary": summary or "(no title)", "start": s, "end": e, "location": location, "description": description}
    target = resolve_account(new_account) if new_account else account
    if target != account:
        # Moving between Google accounts: recreate in the other calendar, then remove the original.
        old = svc.events().get(calendarId="primary", eventId=event_id).execute()
        if old.get("attendees"):
            raise ValueError("events with guests can't be moved between accounts")
        ev = service("calendar", target).events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
        svc.events().delete(calendarId="primary", eventId=event_id, sendUpdates="none").execute()
        store.audit({"kind": "calendar_move", "from": account, "to": target, "event": ev["id"], "source": "click"})
        return _ev(ev, target)
    cur = svc.events().get(calendarId="primary", eventId=event_id).execute()
    ev = svc.events().patch(calendarId="primary", eventId=event_id, body=body,
                            sendUpdates="all" if cur.get("attendees") else "none").execute()
    store.audit({"kind": "calendar_update", "account": account, "event": event_id, "source": "click"})
    return _ev(ev, account)


def cal_delete(account: str, event_id: str) -> dict:
    account = resolve_account(account)
    svc = service("calendar", account)
    cur = svc.events().get(calendarId="primary", eventId=event_id).execute()
    svc.events().delete(calendarId="primary", eventId=event_id,
                        sendUpdates="all" if cur.get("attendees") else "none").execute()
    store.audit({"kind": "calendar_delete", "account": account, "event": event_id,
                 "summary": cur.get("summary"), "source": "click"})
    return {"deleted": event_id}


CLICK_OPS = {"cal_events": cal_events, "cal_create": cal_create, "cal_update": cal_update, "cal_delete": cal_delete}
