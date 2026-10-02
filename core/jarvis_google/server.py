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
from . import markets as MK
from . import pylon as P
from . import artifacts as AR
from . import code as CODE
from . import store
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
    """Create a NEW Gmail draft (does NOT send). This is the tool for "draft / write / respond / reply to X".
    For a reply, set reply_to_message_id to the id of the LAST message in the thread (from gmail_search or
    gmail_read_thread) so it threads; subject may be empty. Always create a new draft; never repurpose an
    existing one. Shown to the user as an editable draft card."""
    return _safe(T.gmail_create_draft, account=account, to=to, subject=subject, body=body, cc=cc, bcc=bcc,
                 reply_to_message_id=reply_to_message_id)


@mcp.tool()
def gmail_update_draft(account: str, draft_id: str, to: str, subject: str, body: str, cc: str = "",
                       bcc: str = "") -> str:
    """Revise a draft YOU created earlier in this conversation (its draft_id came back from gmail_create_draft).
    Refuses drafts Stephen wrote himself: those are his work, so create a new draft instead."""
    if not T.store.is_own_draft(T.resolve_account(account), draft_id):
        return json.dumps({"error": "refused: that draft was not created by JARVIS, so it may be Stephen's own "
                                    "writing. Do not overwrite it. Use gmail_create_draft to make a new draft."})
    return _safe(T.gmail_update_draft, account=account, draft_id=draft_id, to=to, subject=subject, body=body,
                 cc=cc, bcc=bcc)


@mcp.tool()
def gmail_list_drafts(account: str, max_results: int = 10) -> str:
    """List existing drafts (only when Stephen asks about his drafts; not needed to write a new one)."""
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


def _brief(tool: str, fn, **kw) -> str:
    try:
        return json.dumps(MK.brief(tool, fn(**kw)), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ media (YouTube Music / YouTube, read-only)
@mcp.tool()
def music_play(query: str, kind: str = "") -> str:
    """Play music from YouTube Music in the HUD's music player (starts playing immediately). query = song, artist,
    album, playlist or mood ("Bohemian Rhapsody", "Kind of Blue", "lofi hip hop", "80s rock"). kind: "song"
    (default: that song, then a radio of similar songs), "album", "playlist" (moods/genres/"focus music"), "artist"
    (their top songs). Renders a music card with album art, controls and the up-next queue."""
    from . import media as MD
    try:
        return json.dumps(MD.brief("music_play", MD.music_play(query, kind)), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
def youtube_video(query: str = "", video_id: str = "") -> str:
    """Play a regular YouTube video in the HUD's video player (starts playing). query = what to watch ("how to
    make sourdough", "SpaceX launch", "Veritasium latest") or a YouTube URL; video_id = an 11-char id from an
    earlier result. Renders a video card with the player and the other search results to pick from."""
    from . import media as MD
    try:
        return json.dumps(MD.brief("youtube_video", MD.youtube_video(query, video_id)), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
def media_control(action: str, level: int = -1) -> str:
    """Control what's playing in the HUD (music or video): pause, resume, next, previous, stop, volume_up,
    volume_down, set_volume (level 0-100), mute, unmute."""
    from . import media as MD
    return json.dumps(MD.media_control(action, None if level < 0 else level))


# ------------------------------------------------------------------ sports (ESPN, read-only)
@mcp.tool()
def sports_game(league: str = "", team: str = "", date: str = "", when: str = "", game_id: str = "") -> str:
    """Sports scores and games (live ESPN data): NFL, NBA, MLB, NHL, WNBA, college football/basketball, MLS,
    Premier League, La Liga, Champions League. Give a team ("Bears", "Man City") and/or a league ("nfl"), and
    optionally a date ("2026-09-28", "yesterday", "monday", "last night") or when="next" for the upcoming game.
    One game -> full game card: score by period, team box score, player stats, leaders, scoring plays,
    win-probability chart, playable highlight clips, recap and related articles. Several games (league + date,
    no team) -> scoreboard card. Live games show live score. The HUD renders the visuals automatically."""
    from . import sports as SP
    try:
        return json.dumps(SP.brief(SP.sports_game(league=league, team=team, date=date, when=when, game_id=game_id)),
                          default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ used cars near Stephen (read-only)
@mcp.tool()
def cars_nearby(make: str = "Mercedes-Benz", model: str = "CLS-Class", trim: str = "CLS 550 4MATIC") -> str:
    """Used cars for sale near Stephen (home area, nearest first): price, mileage, dealer town, distance, photos,
    links. Defaults to the Mercedes-Benz CLS 550 4MATIC he's watching. Renders a car listings card with a price vs
    mileage chart. Use for "any CLS 550s for sale", "update on the CLS", "used Mercedes near me"."""
    from . import cars as CA
    try:
        return json.dumps(CA.brief(CA.cars_nearby(make, model, trim)), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ Hadrius support model (precomputed blueprint)
@mcp.tool()
def support_model(section: str = "", tour: bool = False) -> str:
    """How Hadrius Support works: the 3-tier AI support model (Pylon triggers, Vercel middleware, Tier 1 Jamie,
    Tier 2 Hadrian, Tier 3 support team, Account Management, Linear, statuses, guardrails). Instant: a
    precomputed, source-grounded blueprint (no research needed). Renders the interactive Support Model display
    (architecture blueprint, workflow diagrams, routing matrix, lifecycle, budgets). section: "" (overview) or one of
    architecture, layers, tiers, flows, routing, lifecycle, budgets, payloads, guardrails, faq, glossary, or a free-text
    topic ("draft-reply", "feature request", "loop guard") to pull matching detail. tour=True for broad "explain /
    walk me through the support model" asks: JARVIS then narrates the whole blueprint tab by tab."""
    from . import support as SU
    try:
        return json.dumps(SU.support_model(section, tour), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ Slack (read-only)
@mcp.tool()
def slack_updates() -> str:
    """Stephen's Slack (Hadrius workspace): unread DMs and group DMs, @mentions of him, and important company posts
    (@channel/@here broadcasts, posts with lots of reactions/replies) from the last few days. Renders a Slack card.
    Use for "any Slack messages?", "what did I miss", "catch me up", "any updates"."""
    from . import slack as SL
    try:
        d = SL.gather()
        store.record_result("slack_updates", None, {}, d)
        return json.dumps(SL.summary_for_model(d), default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
def slack_search(query: str, count: int = 15) -> str:
    """Search Slack messages. Slack search syntax works: from:@ben, in:#customers, after:2026-09-01, "exact phrase".
    Renders the matching messages as a Slack card."""
    from . import slack as SL
    try:
        d = SL.search(query, count)
        if not d.get("error"):
            store.record_result("slack_search", None, {"query": query}, d)
        return json.dumps({"query": query, "total": d.get("total"), "error": d.get("error"),
                           "results": [f"{r['channel']} · {r['user']}: {r['text'][:220]}" for r in d.get("results", [])[:12]]})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
def stock_quote(symbols: str, range: str = "") -> str:
    """Stocks / ETFs / indices / futures (live Yahoo Finance data). `symbols` = one ticker or company name
    ("NVDA", "Apple", "the S&P", "gold") for a full card: price, change, after-hours, interactive chart, key stats,
    analyst ratings + price targets, earnings (beats/misses, next date), quarterly revenue, company profile, news.
    2-6 comma-separated symbols ("AAPL, MSFT, GOOGL") = a performance comparison card. `range` optional:
    1D 5D 1M 6M YTD 1Y 5Y MAX. The HUD renders the visuals automatically."""
    return _brief("stock_quote", MK.stock_quote, symbols=symbols, range=range)


@mcp.tool()
def market_overview(focus: str = "") -> str:
    """How the market is doing today: S&P 500 / Nasdaq / Dow / Russell / VIX / 10Y / gold / oil / bitcoin with
    intraday sparklines, sector heatmap, top gainers / losers / most active, and top crypto. Renders a market card."""
    return _brief("market_overview", MK.market_overview, focus=focus)


@mcp.tool()
def crypto_quote(coin: str = "", range: str = "") -> str:
    """Crypto (live CoinGecko data). `coin` = name or symbol ("bitcoin", "ETH", "solana") for a full card: price,
    1h/24h/7d/30d/1y change, chart, market cap, volume, supply, all-time high. Empty coin = top coins overview.
    `range` optional: 1D 7D 1M 1Y MAX. The HUD renders the visuals automatically."""
    return _brief("crypto_quote", MK.crypto_quote, coin=coin, range=range)


@mcp.tool()
def contacts_search(account: str, query: str, max_results: int = 10) -> str:
    """Find a person's email/phone/title by name: Google contacts, then (work) the Hadrius company directory
    from Slack with fuzzy matching for misheard names, then mail history. If nothing matches, the result has
    no_match_closest_names: weak guesses to ASK Stephen about, never to email without his confirmation."""
    return _safe(T.contacts_search, account=account, query=query, max_results=max_results)


def main() -> None:
    try:  # videos still rendering when the server last stopped: keep polling them
        from . import generate as GEN
        GEN.resume_jobs()
    except Exception:
        pass
    mcp.run()


# ------------------------------------------------------------------ files Stephen uploaded to the HUD
# Uploads are sandboxed copies: every write makes a new version (undo restores the previous one). Saving a copy
# back out (to Downloads) is a click on the card, never a tool.
@mcp.tool()
def file_list(limit: int = 20) -> str:
    """Files Stephen has uploaded to the HUD (newest first) with their artifact ids."""
    return _safe(AR.file_list, limit=limit)


@mcp.tool()
def file_open(artifact_id: str) -> str:
    """Show an uploaded file in its interactive HUD display and get a compact summary (sheet headers + numeric
    column stats, document excerpt, image size + path). Images: then call vision_analyze on the returned path."""
    return _safe(AR.file_open, artifact_id=artifact_id)


@mcp.tool()
def file_read(artifact_id: str, offset: int = 0, limit: int = 20000) -> str:
    """Full text of an uploaded file in chunks (spreadsheets as CSV per sheet, PDF / Word as extracted text).
    Follow next_offset for long files. Does not render anything."""
    return _safe(AR.file_read, artifact_id=artifact_id, offset=offset, limit=limit)


@mcp.tool()
def file_edit(artifact_id: str, old: str, new: str, replace_all: bool = False, note: str = "") -> str:
    """Replace exact text in an uploaded text / code / CSV file (old must match exactly; file_read first).
    Saves a new version and refreshes the display; Stephen can undo it."""
    return _safe(AR.file_edit, artifact_id=artifact_id, old=old, new=new, replace_all=replace_all, note=note)


@mcp.tool()
def file_write(artifact_id: str, content: str, note: str = "") -> str:
    """Replace the whole content of an uploaded text / code / CSV file. Saves a new version (undoable)."""
    return _safe(AR.file_write, artifact_id=artifact_id, content=content, note=note)


@mcp.tool()
def sheet_edit(artifact_id: str, edits: list[dict] | None = None, sheet: str = "", append_rows: list[list] | None = None,
               delete_rows: list[int] | None = None, note: str = "") -> str:
    """Change cells in an uploaded spreadsheet (.csv / .xlsx). edits: [{"cell": "B3", "value": "42"}] (A1 style;
    values starting with "=" are Excel formulas) or [{"row": 2, "col": 1, "value": ...}] (0-based, row 0 = header).
    append_rows: [[...], ...]; delete_rows: 0-based grid rows. sheet: xlsx sheet name (default: first).
    Saves a new version (undoable) and refreshes the grid on screen."""
    return _safe(AR.sheet_edit, artifact_id=artifact_id, edits=edits, sheet=sheet, append_rows=append_rows,
                 delete_rows=delete_rows, note=note)


@mcp.tool()
def image_edit(artifact_id: str, ops: list[dict], note: str = "") -> str:
    """Edit an uploaded image; ops applied in order: {"op":"rotate","degrees":90} (clockwise), {"op":"flip",
    "direction":"horizontal|vertical"}, {"op":"crop","left":0.1,"top":0,"right":0.9,"bottom":1} (fractions or px),
    {"op":"resize","width":800}, {"op":"brightness|contrast|saturation","factor":1.3}, {"op":"grayscale"},
    {"op":"invert"}, {"op":"sharpen"}, {"op":"blur","radius":2}, {"op":"annotate","text":"...","x":0.05,"y":0.05,
    "color":"#ff3b30","size":32}. Saves a new version (undoable)."""
    return _safe(AR.image_edit, artifact_id=artifact_id, ops=ops, note=note)


# ------------------------------------------------------------------ travel & dining (search + confirm-to-book)
@mcp.tool()
def flights_search(destination: str, depart_date: str, return_date: str = "", origin: str = "", adults: int = 1,
                   cabin: str = "economy", nonstop_only: bool = False) -> str:
    """Search real flights (Duffel) and show an offers display. Dates YYYY-MM-DD; return_date '' = one way.
    origin '' = his New York airports (LaGuardia preferred, then JFK, then Newark). destination: IATA code or city.
    cabin: economy | premium_economy | business | first."""
    from . import travel as TR
    return _safe(TR.flights_search, destination=destination, depart_date=depart_date, return_date=return_date,
                 origin=origin, adults=adults, cabin=cabin, nonstop_only=nonstop_only)


@mcp.tool()
def flight_book(offer_id: str) -> str:
    """Prepare to book a flight offer from flights_search: re-checks the live price and shows a confirm card with the
    total and fare rules. Nothing is booked or charged until Stephen authorizes it on the card."""
    from . import travel as TR
    return _safe(TR.flight_book, offer_id=offer_id)


@mcp.tool()
def hotels_search(location: str, check_in: str, check_out: str, guests: int = 1, rooms: int = 1,
                  free_cancellation_only: bool = False) -> str:
    """Search hotels near a place (city, neighborhood, address or landmark) with live prices; shows a hotels display."""
    from . import travel as TR
    return _safe(TR.hotels_search, location=location, check_in=check_in, check_out=check_out, guests=guests,
                 rooms=rooms, free_cancellation_only=free_cancellation_only)


@mcp.tool()
def hotel_book(search_result_id: str, rate_id: str = "") -> str:
    """Prepare to book a hotel from hotels_search (cheapest room unless rate_id): quotes the final price and shows a
    confirm card with the cancellation policy. Nothing is booked until Stephen authorizes it."""
    from . import travel as TR
    return _safe(TR.hotel_book, search_result_id=search_result_id, rate_id=rate_id)


@mcp.tool()
def car_rentals_search(location: str, pickup_date: str, dropoff_date: str, pickup_time: str = "10:00",
                       dropoff_time: str = "10:00") -> str:
    """Search rental cars (Avis, Hertz, Enterprise, Sixt, ...) at an airport code or address; shows a cars display."""
    from . import travel as TR
    return _safe(TR.car_rentals_search, location=location, pickup_date=pickup_date, dropoff_date=dropoff_date,
                 pickup_time=pickup_time, dropoff_time=dropoff_time)


@mcp.tool()
def car_rental_book(rate_id: str) -> str:
    """Prepare to book a rental car rate from car_rentals_search: shows a confirm card. Booked only on his authorization."""
    from . import travel as TR
    return _safe(TR.car_rental_book, rate_id=rate_id)


@mcp.tool()
def restaurants_search(query: str = "", near: str = "", date: str = "", time: str = "19:00", party_size: int = 2) -> str:
    """Find Resy restaurants with open tables (near home unless `near`), showing the times closest to `time` (24h HH:MM).
    query = a restaurant name or cuisine ('Carbone', 'sushi'); '' = best nearby."""
    from . import travel as TR
    return _safe(TR.restaurants_search, query=query, near=near, date=date, time=time, party_size=party_size)


@mcp.tool()
def restaurant_book(venue_id: int, date: str, time: str, party_size: int = 2, seating: str = "") -> str:
    """Prepare a Resy reservation (closest open time to `time`): shows a confirm card with the cancellation / no-show
    policy. Reserved only when Stephen authorizes it."""
    from . import travel as TR
    return _safe(TR.restaurant_book, venue_id=venue_id, date=date, time=time, party_size=party_size, seating=seating)


@mcp.tool()
def reservations_list() -> str:
    """Stephen's upcoming reservations and bookings: Resy tables plus flights / hotels / cars booked through JARVIS.
    Shows a display with a Cancel button on each."""
    from . import travel as TR
    return _safe(TR.reservations_list)


@mcp.tool()
def reservation_cancel(reservation_id: str, provider: str = "resy") -> str:
    """Prepare to cancel one of his reservations (ids + provider from reservations_list: resy | duffel_flight |
    duffel_hotel | duffel_car). Shows a confirm card with any fee / refund; cancelled only when he authorizes it."""
    from . import travel as TR
    return _safe(TR.reservation_cancel, reservation_id=reservation_id, provider=provider)


@mcp.tool()
def traveler_profile(given_name: str = "", family_name: str = "", born_on: str = "", gender: str = "", title: str = "",
                     email: str = "", phone_number: str = "") -> str:
    """Read (no args) or save Stephen's traveller details used for bookings: legal name as on his ID, date of birth
    (YYYY-MM-DD), gender (m/f), title (mr), email, phone. Only save what he actually tells you."""
    from . import travel as TR
    if any((given_name, family_name, born_on, gender, title, email, phone_number)):
        return _safe(TR.traveler_profile_set, given_name=given_name, family_name=family_name, born_on=born_on,
                     gender=gender, title=title, email=email, phone_number=phone_number)
    return _safe(TR.traveler_profile_get)


@mcp.tool()
def image_generate(prompt: str, aspect_ratio: str = "", source_artifact_id: str = "", quality: str = "fast",
                   filename: str = "") -> str:
    """Generate a picture/photo/illustration from a text prompt with Gemini (Nano Banana) and show it on the HUD.
    Write a rich prompt (subject, setting, style, lighting, lens/mood). aspect_ratio: 1:1, 16:9, 9:16, 4:3, 3:4, 3:2,
    2:3, 21:9. To change the CONTENT of an existing image (uploaded or generated): pass its source_artifact_id and say
    the change ("make it night", "put me in a suit"); that saves a new, undoable version. quality: fast | best.
    Takes ~10-20 s."""
    from . import generate as GEN
    return _safe(GEN.image_generate, prompt=prompt, aspect_ratio=aspect_ratio, source_artifact_id=source_artifact_id,
                 quality=quality, filename=filename)


@mcp.tool()
def video_generate(prompt: str, aspect_ratio: str = "16:9", duration_s: int = 8, image_artifact_id: str = "",
                   quality: str = "fast", filename: str = "") -> str:
    """Generate a short video clip (4, 6 or 8 s, with sound) from a prompt with Google Veo 3.1. Returns immediately:
    it renders in the background (1-3 min) and the HUD card turns into the playable video when done. Describe shot,
    subject, action, camera move, style and sound. image_artifact_id animates an existing image. aspect_ratio 16:9 or
    9:16. quality: lite | fast | best (best is slower)."""
    from . import generate as GEN
    return _safe(GEN.video_generate, prompt=prompt, aspect_ratio=aspect_ratio, duration_s=duration_s,
                 image_artifact_id=image_artifact_id, quality=quality, filename=filename)


@mcp.tool()
def video_status(artifact_id: str = "") -> str:
    """Status of recent generated videos (running / done / failed)."""
    from . import generate as GEN
    return _safe(GEN.video_status, artifact_id=artifact_id)


@mcp.tool()
def file_create(filename: str, content: str = "", rows: list[list] | None = None, note: str = "") -> str:
    """Create a NEW file in the HUD workspace and show it: a report (.md), script, cleaned copy, or a spreadsheet
    from `rows` (.xlsx or .csv). Use this for PDF / Word output too (write .md). Stephen saves it out with a click."""
    return _safe(AR.file_create, filename=filename, content=content, rows=rows, note=note)


@mcp.tool()
def file_revert(artifact_id: str) -> str:
    """Undo the last change to an uploaded file (restores the previous version as a new version)."""
    return _safe(AR.file_revert, artifact_id=artifact_id)


# ------------------------------------------------------------------ codebases (projects under ~/Documents/Projects)
@mcp.tool()
def code_projects() -> str:
    """Stephen's local projects (folders under ~/Documents/Projects), most recently modified first."""
    return _safe(CODE.code_projects)


@mcp.tool()
def code_map(path: str, refresh: bool = False) -> str:
    """Map a codebase and render the interactive architecture display: modules, languages, lines of code,
    module dependency graph (from real imports), entry points, most-imported files, key symbols, recent commits.
    path = project name ("jarvis") or absolute folder. Returns a compact summary; then read key files with
    code_read and call code_annotate once so the display explains itself."""
    return _safe(CODE.code_map, path=path, refresh=refresh)


@mcp.tool()
def code_annotate(path: str, summary: str, modules: dict[str, str] | None = None,
                  architecture: list[str] | None = None, how_to_run: str = "") -> str:
    """Attach your plain-English explanation to the codebase display: summary (what the project does, 2-5
    sentences), modules {module name exactly as in code_map: one line on what it does}, architecture (3-6 short
    lines on how data flows), how_to_run. Saved, so the map stays annotated next time."""
    return _safe(CODE.code_annotate, path=path, summary=summary, modules=modules, architecture=architecture,
                 how_to_run=how_to_run)


@mcp.tool()
def code_read(path: str, file: str, offset: int = 0, limit: int = 24000, show: bool = False) -> str:
    """Read a source file in a project (file relative to the project root). show=True also opens it in a code
    viewer on screen (only when Stephen asks to see the file). Secrets (.env, keys) are refused."""
    return _safe(CODE.code_read, path=path, file=file, offset=offset, limit=limit, show=show)


@mcp.tool()
def code_edit(path: str, file: str, old: str, new: str, replace_all: bool = False, note: str = "") -> str:
    """Edit a source file: replace exact text `old` with `new` (code_read first; old must match exactly and be
    unique unless replace_all). Writes to disk, shows the diff on screen with an Undo button. note = why."""
    return _safe(CODE.code_edit, path=path, file=file, old=old, new=new, replace_all=replace_all, note=note)


@mcp.tool()
def code_write(path: str, file: str, content: str, note: str = "") -> str:
    """Create a new source file or replace one entirely (prefer code_edit for changes to existing files).
    Shows the diff on screen with an Undo button."""
    return _safe(CODE.code_write, path=path, file=file, content=content, note=note)


if __name__ == "__main__":
    main()
