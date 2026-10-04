"""Turn tool results + model-authored ```jarvis-visual``` blocks into HUD cards, and text into speech."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

VISUAL_RE = re.compile(r"```jarvis-visual\s*\n(.*?)```", re.S)
VISUAL_TYPES = {"chart", "stats", "table", "list", "markdown", "image", "link", "map"}
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")


def _cid() -> str:
    return "card_" + uuid.uuid4().hex[:10]


def card(kind: str, title: str, data: Any, account: str | None = None) -> dict:
    return {"id": _cid(), "kind": kind, "title": title, "account": account, "data": data}


# ---------------------------------------------------------------- model-authored visuals

def extract_visuals(text: str) -> tuple[str, list[dict]]:
    """Strip ```jarvis-visual``` blocks from text; return (clean_text, cards). Invalid blocks are dropped."""
    cards: list[dict] = []
    for raw in VISUAL_RE.findall(text):
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        specs = spec if isinstance(spec, list) else [spec]
        for s in specs:
            if isinstance(s, dict) and s.get("type") in VISUAL_TYPES:
                default = {"chart": "Analysis", "stats": "Summary", "table": "Data", "list": "Overview",
                           "markdown": "Brief", "image": "Image", "link": "Link"}.get(s["type"], "")
                if s["type"] == "map":
                    default = (f"{s.get('origin', '')} → {s.get('destination', '')}" if s.get("destination")
                               else str(s.get("query") or "Map"))
                cards.append(card(f"visual.{s['type']}", str(s.get("title") or default), s))
    clean = VISUAL_RE.sub("", text)
    clean = re.sub(r"```jarvis-visual.*$", "", clean, flags=re.S)  # unterminated trailing block
    for alt, url in _MD_IMAGE.findall(clean):
        cards.append(card("visual.image", alt, {"type": "image", "url": url, "caption": alt}))
    return clean.strip(), cards


def partial_visible_text(text: str) -> str:
    """Text safe to show/speak while streaming: hides visual blocks, including one still being written."""
    t = VISUAL_RE.sub("", text)
    idx = t.find("```jarvis-visual")
    return t[:idx] if idx >= 0 else t


# ---------------------------------------------------------------- tool results -> cards

def _short_tool(name: str) -> str:
    return name.split("__")[-1] if "__" in name else name


def cards_from_feed(item: dict) -> list[dict]:
    tool, acct, r = item["tool"], item.get("account"), item.get("result") or {}
    if not isinstance(r, dict) or r.get("error"):
        return []
    email = r.get("email") or acct
    if tool == "gmail_search":
        return [card("email_list", f"{email} · {r.get('query', '')}", r, acct)]
    if tool == "gmail_inbox_stats":
        return [card("visual.stats", f"Inbox · {email}", {"type": "stats", "items": [
            {"label": "Unread", "value": r.get("inbox_unread"), "tone": "warn"},
            {"label": "Unread threads", "value": r.get("threads_unread")},
            {"label": "Total", "value": r.get("inbox_total")}]}, acct)]
    if tool == "gmail_read":
        return [card("email", r.get("subject", "Email"), r, acct)]
    if tool == "gmail_read_thread":
        return [card("thread", r.get("subject", "Conversation"), r, acct)]
    if tool in ("gmail_create_draft", "gmail_update_draft"):
        return [card("draft", r.get("subject") or "Draft", r, acct)]
    if tool == "gmail_list_drafts":
        return [card("visual.list", "Drafts", {"type": "list", "items": [
            {"title": d.get("subject"), "subtitle": d.get("to"), "meta": d.get("snippet")} for d in r.get("drafts", [])
        ]}, acct)]
    if tool == "gmail_modify":
        return [card("notice", "Mailbox updated", {"text": f"{r.get('count')} message(s) updated"}, acct)]
    if tool == "calendar_list":
        return [card("calendar", f"Calendar · {email}", r, acct)]
    if tool == "calendar_create":
        return [card("notice", "Event created", {"text": r.get("summary"), "url": r.get("htmlLink")}, acct)]
    if tool == "drive_search":
        return [card("files", f"Drive · {r.get('query') or 'recent'}", r, acct)]
    if tool == "drive_read_text":
        return [card("document", r.get("name", "Document"), r, acct)]
    if tool == "docs_create":
        return [card("notice", "Document created", {"text": r.get("title"), "url": r.get("url")}, acct)]
    if tool == "sheets_read":
        vals = r.get("values") or []
        return [card("visual.table", r.get("range", "Sheet"), {
            "type": "table", "columns": vals[0] if vals else [], "rows": vals[1:60]}, acct)]
    if tool == "directions":
        return [card("directions", f"To {r.get('destination', '')}", r)]
    if tool == "places_search":
        return [card("places", f"{r.get('query', 'Places')}" + (f" · near {r['near']}" if r.get("near") else ""), r)]
    if tool == "place_details":
        return [card("place", r.get("name", "Place"), r)]
    if tool == "pylon_tickets":
        return [card("pylon_list", f"Pylon · {r.get('query', 'tickets')}", r)]
    if tool == "pylon_ticket":
        return [card("pylon_ticket", f"#{r.get('number')} {r.get('title', '')}", r)]
    if tool == "slack_updates":
        return [card("slack", "Slack · Hadrius", r)]
    if tool == "slack_search":
        return [card("slack", f"Slack · “{r.get('query', '')}”", r)]
    if tool == "stock_quote":
        if r.get("series"):
            return [card("stock_compare", " vs ".join(s["symbol"] for s in r["series"]), r)]
        return [card("stock", f"{r.get('name', '')} · {r.get('symbol', '')}", r)]
    if tool == "market_overview":
        return [card("market", "Crypto overview" if r.get("focus") == "crypto" else "Market overview", r)]
    if tool == "crypto_quote":
        if r.get("indices") is not None:
            return [card("market", "Crypto overview", r)]
        return [card("crypto", f"{r.get('name', '')} · {r.get('symbol', '')}", r)]
    if tool == "music_play":
        return [card("music", r.get("title", "Music"), r)]
    if tool == "youtube_video":
        first = (r.get("results") or [{}])[0]
        return [card("video", first.get("title") or r.get("query", "YouTube"), r)]
    if tool == "media_control":
        return [card("media_control", "", r)]
    if tool == "sports_game":
        return [card("sports_game", f"{r.get('league_label', '')} · " + " vs ".join(t.get("abbr", "") for t in r.get("teams", [])), r)]
    if tool == "sports_scoreboard":
        return [card("sports_scoreboard", f"{r.get('league_label', '')} scoreboard" + (f" · {r['date']}" if r.get("date") else ""), r)]
    if tool == "weather":
        loc = r.get("location") or {}
        place = ", ".join(x for x in (loc.get("name"), loc.get("region") if loc.get("country") == "US"
                                      else loc.get("country")) if x)
        return [card("weather", place or "Weather", r)]
    if tool == "cars_nearby" and r.get("cars") is not None:
        return [card("car_listings", f"{r.get('query', 'Cars')} · near {r.get('near', '')}", r)]
    if tool == "support_model" and r.get("tiers"):
        return [card("support_blueprint", r.get("title") or "Hadrius Support Model", r)]
    if tool == "demo_note" and r.get("text"):
        return [card("notice", r.get("title") or "", r)]
    if tool in ("trade_desk", "trade_portfolio") and r.get("kind") == "trade_desk":
        return [card("trade_desk", "Trading Desk · Kraken", r)]
    if tool == "trade_insights" and r.get("kind") == "trade_insights":
        return [card("trade_insights", "Investment Intelligence", r)]
    if tool == "trade_orders" and r.get("kind") == "trade_orders":
        return [card("trade_orders", "Open orders", r)]
    if tool in ("trade_alert", "trade_order_placed") and r.get("kind") in ("trade_alert", "trade_order"):
        title = "Order placed" if r["kind"] == "trade_order" else "Market alert"
        return [card(r["kind"], title, r)]
    if tool == "eats_search" and r.get("kind") == "eats_stores":
        return [card("eats_stores", f"Uber Eats · {r.get('query') or 'Near you'}", r)]
    if tool == "eats_menu" and r.get("kind") == "eats_menu":
        return [card("eats_menu", r.get("name") or "Menu", r)]
    if tool == "eats_cart" and r.get("kind") == "eats_cart":
        return [card("eats_cart", f"Cart · {r.get('store_name') or 'empty'}", r)]
    if tool == "car_profile" and r.get("kind") == "garage":
        v = (r.get("profile") or {}).get("vehicle") or {}
        return [card("garage", f"{v.get('year', 2003)} {v.get('make', 'Mercedes-Benz')} {v.get('model', 'CL600')}", r)]
    if tool == "car_search" and r.get("kind") == "garage_search":
        return [card("garage_search", f"Your car documents · “{r.get('query', '')}”", r)]
    if tool == "car_diagnose" and r.get("kind") == "garage_diagnosis":
        return [card("garage_diagnosis", "Diagnosis · CL600", r)]
    if tool == "car_plan" and r.get("kind") == "garage_plan":
        return [card("garage_plan", f"Build plan · {r.get('title', '')}", r)]
    if tool == "reservations_list" and r.get("kind") == "reservations":
        return [card("travel_reservations", f"Your reservations · {len(r.get('items') or [])}", r)]
    if tool in ("flights_search", "hotels_search", "car_rentals_search", "restaurants_search") and r.get("kind"):
        k = r["kind"]
        title = {"flights": f"Flights · {'/'.join(r.get('origins') or [])} → {r.get('destination', '')} · {r.get('depart_date', '')}",
                 "hotels": f"Hotels · {r.get('near', '')} · {r.get('check_in', '')} → {r.get('check_out', '')}",
                 "car_rentals": f"Rental cars · {r.get('near', '')} · {r.get('pickup_date', '')}",
                 "restaurants": f"Tables · {r.get('query') or r.get('near', '')} · {r.get('date', '')} · {r.get('party_size', 2)} ppl"}[k]
        return [card(f"travel_{k}", title, r)]
    if tool == "booking" and r.get("category") == "cancel":
        return [card("booking_confirmed", f"Cancelled · {r.get('name') or r.get('reference', '')}", r)]
    if tool == "booking" and r.get("category"):
        return [card("booking_confirmed", f"Booked · {r.get('name') or r.get('car') or r.get('reference', '')}", r)]
    if tool == "video_analyze" and r.get("video"):
        v = r["video"]
        title = (v.get("analysis") or {}).get("title") or v.get("filename") or "Video"
        return [card("video_analysis", title, r)]
    if tool in ("image_generate", "video_generate") and r.get("generating"):
        g = r["generating"]
        what = "Video" if g.get("kind") == "video" else "Image"
        return [card("generating", f"{what} · {'failed' if g.get('error') else 'generating'}", r)]
    if tool in ARTIFACT_TOOLS and r.get("artifact"):
        a = r["artifact"]
        return [card("artifact", f"{a['filename']} · v{a['version']}", r)]
    if tool in ("code_map", "code_annotate") and r.get("modules") is not None:
        return [card("codebase", f"{r.get('name', 'Project')} · {r.get('total_files', 0)} files", r)]
    if tool == "code_read" and r.get("file"):
        return [card("code_file", r["file"], r)]
    if tool == "code_change" and r.get("changes") is not None:
        return [card("code_changes", f"{r.get('name', '')} · changes", r)]
    return []


ARTIFACT_TOOLS = {"image_generate", "video_generate", "file_open", "file_edit", "file_write", "sheet_edit", "image_edit", "file_create", "file_revert",
                  "file_upload"}


def action_card(action: dict) -> dict:
    return {"id": action["id"], "kind": "confirm", "title": action["summary"], "account": action["account"],
            "data": {"kind": action["kind"], "preview": action["preview"], "status": action["status"],
                     "message_ids": (action.get("params") or {}).get("message_ids") or []}}


# ---------------------------------------------------------------- tool labels for the live trace

_LABELS = {
    "tool_describe": None, "tool_search": None, "tool_call": None, "skill_view": "SKILLS · LOAD", "web_search": "WEB · SEARCH",
    "web_extract": "WEB · EXTRACT", "terminal": "SYSTEM · TERMINAL", "execute_code": "SYSTEM · COMPUTE",
    "browser_exec": "BROWSER · DRIVE", "read_file": "FILES · READ", "write_file": "FILES · WRITE",
    "search_files": "FILES · SEARCH", "delegate_task": "AGENTS · DEPLOY", "memory": "MEMORY",
    "vision_analyze": "VISION · ANALYZE", "image_generate": "IMAGE · GENERATE", "session_search": "MEMORY · RECALL",
}


def tool_label(name: str) -> str | None:
    if name in _LABELS:
        return _LABELS[name]
    if name.startswith("mcp__"):
        parts = name.split("__")
        server, tool = (parts[1], parts[-1]) if len(parts) >= 3 else ("mcp", parts[-1])
        if server == "jarvis_google":
            head, _, rest = tool.partition("_")
            return f"{head.upper()} · {rest.replace('_', ' ').upper() or 'QUERY'}"
        return f"{server.upper()} · {tool.replace('_', ' ').upper()}"
    return name.replace("_", " ").upper()


# ---------------------------------------------------------------- speech

_SENT_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+(?=[A-Z0-9\"'(])")


def speakable(text: str) -> str:
    t = VISUAL_RE.sub("", text)
    t = re.sub(r"```.*?```", " The code is on screen. ", t, flags=re.S)
    t = re.sub(r"```.*$", " ", t, flags=re.S)
    t = _MD_IMAGE.sub("", t)
    t = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", t)  # [label](url) -> label
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\|.*\|\s*$", "", t, flags=re.M)  # markdown tables
    t = re.sub(r"(\*\*|__|\*|_)(\S.*?\S|\S)\1", r"\2", t)
    t = t.replace("—", ", ").replace("–", ", ").replace("&", " and ")
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"(\.\s*){2,}", ". ", t).strip()
    t = re.sub(r"^[.\s]+", "", t)
    return t if not t or t[-1] in ".!?…" else t + "."


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(text) if s.strip()]


_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=(?:and|but|while|which|so)\s)")


def chunk_for_tts(sentence: str, max_len: int) -> list[str]:
    """Split a long sentence at clause boundaries so each TTS request is short (synthesis time ~ length)."""
    if len(sentence) <= max_len:
        return [sentence]
    parts, cur = [], ""
    for piece in _CLAUSE.split(sentence):
        if not piece:
            continue
        cand = f"{cur} {piece}".strip() if cur else piece
        if len(cand) <= max_len or not cur:
            cur = cand
        else:
            parts.append(cur)
            cur = piece
    if cur:
        parts.append(cur)
    return parts


class SentenceStream:
    """Feed streaming text; yields TTS-sized chunks as complete sentences become available.
    The very first chunk is kept short so audio starts fast; later chunks synthesize during playback."""

    def __init__(self, max_spoken_chars: int = 700, first_max: int = 140, next_max: int = 320):
        self.emitted = 0
        self.spoken_chars = 0
        self.max = max_spoken_chars
        self.first_max, self.next_max = first_max, next_max
        self.chunks_out = 0
        self.truncated = False

    def feed(self, full_visible_text: str, final: bool = False) -> list[str]:
        text = speakable(full_visible_text) if full_visible_text.strip() else ""
        sents = split_sentences(text)
        ready = sents if final else sents[:-1]  # last one may be incomplete
        out = []
        for s in ready[self.emitted:]:
            self.emitted += 1
            if self.spoken_chars >= self.max:
                self.truncated = True
                continue
            self.spoken_chars += len(s)
            for c in chunk_for_tts(s, self.first_max if self.chunks_out == 0 else self.next_max):
                out.append(c)
                self.chunks_out += 1
        return out
