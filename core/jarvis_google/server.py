"""jarvis-google MCP server (stdio). Registered in Hermes as `jarvis_google`.

Every tool takes `account`: "personal" (skalamera@gmail.com) or "work" (stephen@hadrius.com).
Send / reply / trash / delete / share / invite / sheet-write tools only PROPOSE the action;
Stephen confirms it in the JARVIS HUD. The model cannot execute proposals itself.
"""
from __future__ import annotations

import json
import traceback
from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]

from . import places as PL
from . import routes as RT
from . import pylon as P
from . import tools as T
from .accounts import AccountError

mcp = FastMCP(
    "jarvis_google",
    instructions=(
        "Stephen's Google data. Two accounts: 'personal' = skalamera@gmail.com, 'work' = stephen@hadrius.com. "
        "If the user doesn't say which account, check BOTH for questions, and ask before writing. "
        "Gmail query syntax works in gmail_search (is:unread, from:, newer_than:7d, has:attachment...). "
        "Tools that send, reply, trash, delete, share, invite or overwrite return "
        "status=awaiting_user_confirmation: the action has NOT happened. Tell the user it's ready "
        "for their confirmation on screen; never say it was sent/deleted."
    ),
)


def _safe(fn, **kw) -> str:
    try:
        return json.dumps(fn(**kw), default=str)
    except AccountError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:  # return errors to the model instead of crashing the server
        return json.dumps({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=2)[-600:]})


# ------------------------------------------------------------------ accounts / mail read
@mcp.tool()
def accounts() -> str:
    """List linked Google accounts (personal, work) and their email addresses."""
    return _safe(T.accounts_list)


@mcp.tool()
def gmail_search(account: str, query: str = "in:inbox", max_results: int = 10) -> str:
    """Search Gmail with Gmail query syntax. Returns sender, subject, date, snippet, unread flag, ids.
    Examples: 'is:unread in:inbox', 'from:amazon newer_than:7d', 'subject:invoice has:attachment'."""
    return _safe(T.gmail_search, account=account, query=query, max_results=max_results)


@mcp.tool()
def gmail_inbox_stats(account: str) -> str:
    """Total and unread counts for the inbox."""
    return _safe(T.gmail_inbox_stats, account=account)


@mcp.tool()
def gmail_read(account: str, message_id: str) -> str:
    """Read one email in full (body text, cc, attachments list)."""
    return _safe(T.gmail_read, account=account, message_id=message_id)


@mcp.tool()
def gmail_read_thread(account: str, thread_id: str) -> str:
    """Read an entire email conversation."""
    return _safe(T.gmail_read_thread, account=account, thread_id=thread_id)


@mcp.tool()
def gmail_labels(account: str) -> str:
    """List Gmail labels with ids."""
    return _safe(T.gmail_labels, account=account)


# ------------------------------------------------------------------ drafts (safe, immediate)
@mcp.tool()
def gmail_create_draft(account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                       reply_to_message_id: str = "") -> str:
    """Create a Gmail draft (does NOT send). Set reply_to_message_id to draft a threaded reply
    (subject may be left empty for replies). Shown to the user as an editable draft card."""
    return _safe(T.gmail_create_draft, account=account, to=to, subject=subject, body=body, cc=cc, bcc=bcc,
                 reply_to_message_id=reply_to_message_id)


@mcp.tool()
def gmail_update_draft(account: str, draft_id: str, to: str, subject: str, body: str, cc: str = "",
                       bcc: str = "") -> str:
    """Replace the contents of an existing draft."""
    return _safe(T.gmail_update_draft, account=account, draft_id=draft_id, to=to, subject=subject, body=body,
                 cc=cc, bcc=bcc)


@mcp.tool()
def gmail_list_drafts(account: str, max_results: int = 10) -> str:
    """List existing drafts."""
    return _safe(T.gmail_list_drafts, account=account, max_results=max_results)


@mcp.tool()
def gmail_modify(account: str, message_ids: list[str], mark_read: bool | None = None, archive: bool = False,
                 star: bool | None = None, add_labels: list[str] | None = None,
                 remove_labels: list[str] | None = None) -> str:
    """Reversible changes, applied immediately: mark read/unread, archive (remove from inbox), star/unstar,
    add/remove label ids. Up to 500 messages."""
    return _safe(T.gmail_modify, account=account, message_ids=message_ids, mark_read=mark_read, archive=archive,
                 star=star, add_labels=add_labels, remove_labels=remove_labels)


# ------------------------------------------------------------------ proposals (need confirmation)
@mcp.tool()
def gmail_send(account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Propose sending a new email. Requires Stephen's on-screen confirmation before it is sent."""
    return _safe(T.gmail_send, account=account, to=to, subject=subject, body=body, cc=cc, bcc=bcc)


@mcp.tool()
def gmail_send_draft(account: str, draft_id: str) -> str:
    """Propose sending an existing draft. Requires confirmation."""
    return _safe(T.gmail_send_draft, account=account, draft_id=draft_id)


@mcp.tool()
def gmail_reply(account: str, message_id: str, body: str, reply_all: bool = False) -> str:
    """Propose a threaded reply to an email. Requires confirmation."""
    return _safe(T.gmail_reply, account=account, message_id=message_id, body=body, reply_all=reply_all)


@mcp.tool()
def gmail_trash(account: str, message_ids: list[str]) -> str:
    """Propose moving emails to Trash (recoverable 30 days). Requires confirmation. Prefer this over permanent delete."""
    return _safe(T.gmail_trash, account=account, message_ids=message_ids)


@mcp.tool()
def gmail_delete_permanently(account: str, message_ids: list[str]) -> str:
    """Propose PERMANENT deletion (cannot be undone). Only when Stephen explicitly says permanently."""
    return _safe(T.gmail_delete_permanently, account=account, message_ids=message_ids)


# ------------------------------------------------------------------ calendar
@mcp.tool()
def calendar_list(account: str, time_min: str, time_max: str, query: str = "", max_results: int = 50) -> str:
    """List calendar events between RFC3339 times with timezone offset, e.g. 2026-09-29T00:00:00-04:00."""
    return _safe(T.calendar_list, account=account, time_min=time_min, time_max=time_max, query=query,
                 max_results=max_results)


@mcp.tool()
def calendar_create(account: str, summary: str, start: str, end: str, attendees: list[str] | None = None,
                    location: str = "", description: str = "", add_meet: bool = False) -> str:
    """Create an event. start/end: RFC3339 with offset, or YYYY-MM-DD for all-day.
    No attendees -> created immediately. With attendees (invites go out) -> requires confirmation."""
    return _safe(T.calendar_create, account=account, summary=summary, start=start, end=end, attendees=attendees,
                 location=location, description=description, add_meet=add_meet)


@mcp.tool()
def calendar_delete(account: str, event_id: str) -> str:
    """Propose deleting a calendar event. Requires confirmation."""
    return _safe(T.calendar_delete, account=account, event_id=event_id)


# ------------------------------------------------------------------ drive / docs / sheets / contacts
@mcp.tool()
def drive_search(account: str, query: str = "", max_results: int = 12, raw_query: bool = False) -> str:
    """Search Drive by full text (or a raw Drive query when raw_query=true). Empty query = recent files."""
    return _safe(T.drive_search, account=account, query=query, max_results=max_results, raw_query=raw_query)


@mcp.tool()
def drive_read_text(account: str, file_id: str) -> str:
    """Read a Google Doc / Sheet (CSV) / Slides / text file as plain text."""
    return _safe(T.drive_read_text, account=account, file_id=file_id)


@mcp.tool()
def drive_share(account: str, file_id: str, email: str, role: str = "reader") -> str:
    """Propose sharing a file (role reader|commenter|writer). Requires confirmation."""
    return _safe(T.drive_share, account=account, file_id=file_id, email=email, role=role)


@mcp.tool()
def drive_trash(account: str, file_id: str) -> str:
    """Propose moving a Drive file to Trash. Requires confirmation."""
    return _safe(T.drive_trash, account=account, file_id=file_id)


@mcp.tool()
def docs_create(account: str, title: str, body: str = "") -> str:
    """Create a new Google Doc (private to Stephen) with optional starting text."""
    return _safe(T.docs_create, account=account, title=title, body=body)


@mcp.tool()
def sheets_read(account: str, spreadsheet_id: str, range_a1: str = "A1:Z200") -> str:
    """Read cells from a Google Sheet."""
    return _safe(T.sheets_read, account=account, spreadsheet_id=spreadsheet_id, range_a1=range_a1)


@mcp.tool()
def sheets_write(account: str, spreadsheet_id: str, range_a1: str, values: list[list[Any]], append: bool = False) -> str:
    """Propose writing/appending rows to a Sheet. Requires confirmation."""
    return _safe(T.sheets_write, account=account, spreadsheet_id=spreadsheet_id, range_a1=range_a1, values=values,
                 append=append)


@mcp.tool()
def weather(location: str = "", days: int = 7, units: str = "") -> str:
    """Current conditions + next 24 hours + daily forecast (up to 14 days). Leave location empty for
    Stephen's current location (from his Mac's IP). location examples: 'New York, NY', 'London', 'Paris, France'.
    units: '' (auto: imperial in the US), 'imperial' or 'metric'. The HUD renders a weather visual automatically."""
    return _safe(T.weather_lookup, location=location, days=days, units=units)


@mcp.tool()
def directions(destination: str, origin: str = "", mode: str = "driving", avoid_tolls: bool = False,
               avoid_highways: bool = False) -> str:
    """Turn-by-turn directions with live traffic (Google Routes). origin empty = where Stephen is now.
    mode: driving | transit | walking | bicycling. Returns ETA, distance, traffic delay, alternatives and every step.
    The HUD renders a directions card (map + steps + mode switch) automatically."""
    return _safe(RT.directions, destination=destination, origin=origin, mode=mode, avoid_tolls=avoid_tolls,
                 avoid_highways=avoid_highways)


@mcp.tool()
def places_search(query: str, near: str = "", limit: int = 6, open_now: bool = False) -> str:
    """Google Places search: restaurants, bars, cafes, shops, any business. Biased to where Stephen is unless `near`
    names a place. Returns rating, review count, price, type, open/closed, address. The HUD shows a photo list
    card automatically; Stephen can click one for its full card."""
    return _safe(PL.places_search, query=query, near=near, limit=limit, open_now=open_now)


@mcp.tool()
def place_details(place_id: str = "", query: str = "") -> str:
    """Full Google card for ONE place (by place_id from places_search, or a query like "O Mandarin Hartsdale"):
    photos, hours, Google review summary, recent reviews, phone, website, reservations. Renders a place card."""
    return _safe(PL.place_details, place_id=place_id, query=query)


@mcp.tool()
def pylon_tickets(query: str = "", mine: bool = True, states: list[str] | None = None, limit: int = 15) -> str:
    """Pylon support tickets. Default (no args): Stephen's open tickets, newest activity first.
    query: free text (customer, account, topic) or a ticket number like "17747". mine=False searches everyone's.
    states: any of new, waiting_on_you, waiting_on_customer, on_hold, closed (default: all open).
    The HUD renders actionable ticket cards automatically; Stephen changes tickets with the card buttons."""
    return _safe(P.pylon_tickets, query=query, mine=mine, states=states, limit=limit)


@mcp.tool()
def pylon_ticket(number: str) -> str:
    """One Pylon ticket by number (e.g. "17747") with its recent thread. Renders an actionable ticket card."""
    return _safe(P.pylon_ticket, number=number)


@mcp.tool()
def contacts_search(account: str, query: str, max_results: int = 10) -> str:
    """Find a person's email/phone by name (contacts, company directory for work, then mail history)."""
    return _safe(T.contacts_search, account=account, query=query, max_results=max_results)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
