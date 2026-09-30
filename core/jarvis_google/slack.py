"""Slack inbox for JARVIS: unread DMs / group DMs, @mentions, and important company posts.

Read-only. Uses Stephen's user token (search:read, im/mpim/channels/groups history) for reading and the bot
token only for the user directory (names + avatars). Slack has no public "unread count" API for user tokens, so
"unread" is computed exactly the way the client does: messages newer than the conversation's last_read mark.

Everything returned is plain, already-rendered data (names resolved, mrkdwn converted to readable text) so the
HUD can draw it and the briefing model can triage it without seeing any Slack ids.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import slack_dir

LOOKBACK_DAYS = 4
TEXT_CHARS = 600
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_info_cache: dict[str, tuple[float, dict]] = {}

# channels whose posts are company-wide news, not alerts (member of these, humans posting)
# Workspace-specific extras come from .env (comma-separated): JARVIS_SLACK_ANNOUNCE / JARVIS_SLACK_NOISE.
def _env_list(name: str) -> list[str]:
    return [x.strip().lstrip("#").lower() for x in slack_dir.env_token(name).split(",") if x.strip()]


ANNOUNCE_CHANNELS = {"general", "announcements", "company", "all-hands", "engineering-and-product", "customers",
                     "compliance", "standup", "knowledge-sharing", "feedback", *_env_list("JARVIS_SLACK_ANNOUNCE")}
NOISE_CHANNEL_RE = re.compile(r"(alert|sentry|noisy|_pr_|^pr-|deploy|ci-|bot|notif|log|monitor|webhook"
                              + "".join("|" + re.escape(x) for x in _env_list("JARVIS_SLACK_NOISE")) + ")", re.I)


def _utok() -> str:
    return slack_dir.env_token("SLACK_USER_TOKEN")


def available() -> bool:
    return bool(_utok())


def _get(method: str, **params) -> dict:
    url = f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_utok()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise RuntimeError(f"slack {method}: {d.get('error')}")
    return d


def me() -> dict:
    with _lock:
        if "me" in _cache:
            return _cache["me"]
    a = _get("auth.test")
    out = {"user_id": a["user_id"], "team_id": a["team_id"], "url": a["url"].rstrip("/"), "team": a.get("team")}
    with _lock:
        _cache["me"] = out
    return out


def _conv_info(cid: str) -> dict:
    hit = _info_cache.get(cid)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    ch = _get("conversations.info", channel=cid)["channel"]
    _info_cache[cid] = (time.time(), ch)
    return ch


# ----------------------------------------------------------------- mrkdwn -> readable text
_USER_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHAN_RE = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]*))?>")
_LINK_RE = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]+))?>")
_SPECIAL_RE = re.compile(r"<!(channel|here|everyone)(?:\|[^>]*)?>")
_SUBTEAM_RE = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|([^>]+))?>")
_DATE_RE = re.compile(r"<!date\^(\d+)\^[^|>]*(?:\|([^>]*))?>")
_EMOJI = {"white_check_mark": "✅", "tada": "🎉", "rocket": "🚀", "fire": "🔥", "eyes": "👀", "pray": "🙏", "+1": "👍",
          "thumbsup": "👍", "heart": "❤️", "joy": "😂", "warning": "⚠️", "rotating_light": "🚨", "100": "💯", "raised_hands": "🙌",
          "clap": "👏", "sparkles": "✨", "wave": "👋", "smile": "😄", "slightly_smiling_face": "🙂", "thinking_face": "🤔",
          "point_up": "☝️", "point_right": "👉", "x": "❌", "heavy_check_mark": "✔️", "star": "⭐", "memo": "📝",
          "calendar": "📅", "bulb": "💡", "muscle": "💪", "sob": "😭", "grinning": "😀", "laughing": "😆", "ok_hand": "👌",
          "loudspeaker": "📢", "mega": "📣", "speaking_head_in_silhouette": "🗣️", "moneybag": "💰", "money_with_wings": "💸",
          "dollar": "💵", "chart_with_upwards_trend": "📈", "trophy": "🏆", "partying_face": "🥳", "handshake": "🤝",
          "bell": "🔔", "alarm_clock": "⏰", "hourglass": "⌛", "link": "🔗", "paperclip": "📎", "page_facing_up": "📄",
          "email": "📧", "phone": "📞", "red_circle": "🔴", "large_green_circle": "🟢", "white_circle": "⚪",
          "exclamation": "❗", "question": "❓", "heavy_plus_sign": "➕", "arrow_right": "➡️", "calendar_spiral": "🗓️",
          "sunglasses": "😎", "blush": "😊", "wink": "😉", "grin": "😁", "sweat_smile": "😅", "heart_eyes": "😍",
          "star-struck": "🤩", "goat": "🐐", "crown": "👑", "gem": "💎", "zap": "⚡", "boom": "💥", "dart": "🎯",
          "confetti_ball": "🎊", "balloon": "🎈", "gift": "🎁", "coffee": "☕", "pizza": "🍕", "beers": "🍻",
          "champagne": "🍾", "raising_hand": "🙋", "thumbsdown": "👎", "-1": "👎", "slightly_frowning_face": "🙁"}


def render_text(text: str, users: dict[str, dict], limit: int = TEXT_CHARS) -> tuple[str, list[dict]]:
    """Slack mrkdwn -> plain readable text plus a list of links. Mentions of Stephen become '@you'."""
    my = _cache.get("me", {}).get("user_id")
    links: list[dict] = []

    def user(m: re.Match) -> str:
        uid = m[1]
        return "@you" if uid == my else "@" + ((users.get(uid) or {}).get("name") or "someone")

    def link(m: re.Match) -> str:
        url, label = m[1], m[2]
        if len(links) < 4 and url.startswith("http"):
            links.append({"url": url, "label": label or re.sub(r"^https?://(www\.)?", "", url)[:48]})
        return label or re.sub(r"^https?://(www\.)?", "", url)[:48]

    t = text or ""
    t = _USER_RE.sub(user, t)
    t = _CHAN_RE.sub(lambda m: "#" + (m[2] or "channel"), t)
    t = _SUBTEAM_RE.sub(lambda m: "@" + (m[1] or "team").lstrip("@"), t)
    t = _SPECIAL_RE.sub(lambda m: "@" + m[1], t)
    t = _DATE_RE.sub(lambda m: m[2] or time.strftime("%b %d, %I:%M %p", time.localtime(int(m[1]))), t)
    t = _LINK_RE.sub(link, t)
    t = re.sub(r":([a-z0-9_+\-]+)(?:::skin-tone-\d)?:", lambda m: _EMOJI.get(m[1], ""), t)  # unknown custom emoji: drop
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", t)  # *bold* / _italic_ markers read as noise on the HUD
    t = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"(?m)^\s*(?:&gt;|>)\s?", "", t)  # block quotes
    t = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", t)
    t = html.unescape(t)
    t = re.sub(r"```.*?```", "[code]", t, flags=re.S)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t, links


def _blocks_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for b in blocks or []:
        if b.get("type") == "section":
            if (b.get("text") or {}).get("text"):
                parts.append(b["text"]["text"])
            parts += [f.get("text", "") for f in b.get("fields") or [] if f.get("text")]
        elif b.get("type") == "context":
            parts += [e.get("text", "") for e in b.get("elements") or [] if e.get("type") in ("mrkdwn", "plain_text")]
        elif b.get("type") == "header":
            parts.append((b.get("text") or {}).get("text", ""))
    return "\n".join(p for p in parts if p.strip())


def _msg_text(m: dict) -> str:
    """Bots often leave `text` empty or as a summary and put the content in blocks/attachments."""
    t = m.get("text") or ""
    if not t.strip() or (m.get("bot_id") or not m.get("user") or m.get("subtype") == "bot_message"):
        bt = _blocks_text(m.get("blocks") or [])
        if len(bt) > len(t):
            t = bt
    if len(t) < 40:
        for a in m.get("attachments") or []:
            extra = " ".join(x for x in (a.get("pretext"), a.get("title"), a.get("text")) if x)
            if extra and extra not in t:
                t = f"{t}\n{extra}".strip()
    return t


def _permalink(me_: dict, cid: str, ts: str, thread_ts: str | None = None) -> str:
    base = f"{me_['url']}/archives/{cid}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        base += f"?thread_ts={thread_ts}&cid={cid}"
    return base


def app_link(me_: dict, cid: str, ts: str | None = None) -> str:
    """Deep link that opens the Slack desktop app on the conversation/message."""
    q = {"team": me_["team_id"], "id": cid}
    if ts:
        q["message"] = ts
    return "slack://channel?" + urllib.parse.urlencode(q)


# ----------------------------------------------------------------- gather
def _conv_title(ch: dict, users: dict[str, dict], my: str) -> tuple[str, str, list[str]]:
    """(kind, title, avatar urls) for a conversation."""
    if ch.get("is_im"):
        u = users.get(ch.get("user") or "") or {}
        return "dm", u.get("name") or "Direct message", [u.get("avatar", "")]
    if ch.get("is_mpim"):
        names = [n for n in re.sub(r"^mpdm-|-\d+$", "", ch.get("name", "")).split("--") if n]
        # mpim names use handles; map handle -> real name via the directory when possible
        by_handle = {v.get("handle"): v for v in users.values() if v.get("handle")}
        pretty = [(by_handle.get(n) or {}).get("name") or n for n in names]
        pretty = [p for p in pretty if p and (by_handle.get(p) or {}).get("id") != my]
        return "group", ", ".join(pretty[:4]) + (f" +{len(pretty) - 4}" if len(pretty) > 4 else ""), []
    return "channel", "#" + (ch.get("name") or "channel"), []


def gather(lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Unread DMs/group DMs, recent @mentions and important channel posts. Read-only."""
    if not available():
        return {"available": False, "conversations": [], "mentions": [], "channels": []}
    t0 = time.time()
    me_ = me()
    my = me_["user_id"]
    users = slack_dir.user_map()
    after = time.strftime("%Y-%m-%d", time.localtime(time.time() - lookback_days * 86400))

    # 1) direct + group messages to me (search is one call and returns channel ids)
    dm_hits: list[dict] = []
    for page in (1, 2, 3):
        s = _get("search.messages", query=f"to:me after:{after}", sort="timestamp", count=100, page=page)["messages"]
        dm_hits += s.get("matches", [])
        if page >= (s.get("paging") or {}).get("pages", 1):
            break
    # 2) @mentions of me in channels
    men = _get("search.messages", query=f"<@{my}> after:{after}", sort="timestamp", count=40)["messages"]
    mention_hits = [m for m in men.get("matches", []) if not (m["channel"].get("is_im") or m["channel"].get("is_mpim"))]

    conv_ids = sorted({m["channel"]["id"] for m in dm_hits} | {m["channel"]["id"] for m in mention_hits})
    with ThreadPoolExecutor(8) as ex:
        infos = dict(zip(conv_ids, ex.map(lambda c: _safe_info(c), conv_ids)))

    def view(m: dict, ch: dict) -> dict:
        u = users.get(m.get("user") or "") or {}
        name = u.get("name") or m.get("username") or "Slack"
        text, links = render_text(_msg_text(m), users)
        return {"ts": m["ts"], "user": name, "avatar": u.get("avatar", ""), "bot": bool(u.get("bot")) or not m.get("user"),
                "text": text, "links": links, "permalink": m.get("permalink") or _permalink(me_, ch["id"], m["ts"])}

    # group DM hits by conversation; unread = newer than last_read
    convs: dict[str, dict] = {}
    for m in sorted(dm_hits, key=lambda x: float(x["ts"])):
        cid = m["channel"]["id"]
        ch = infos.get(cid) or {"id": cid, **m["channel"]}
        if m.get("user") == my:
            continue
        last_read = float(ch.get("last_read") or 0)
        c = convs.setdefault(cid, {"id": cid, "messages": [], "unread": 0, "last_read": last_read})
        is_unread = float(m["ts"]) > last_read
        c["unread"] += int(is_unread)
        c["messages"].append({**view(m, ch), "unread": is_unread})
    conversations = []
    for cid, c in convs.items():
        ch = infos.get(cid) or {"id": cid}
        kind, title, avatars = _conv_title(ch, users, my)
        unread_msgs = [x for x in c["messages"] if x["unread"]]
        if not unread_msgs:
            continue
        bot = all(x["bot"] for x in unread_msgs)
        conversations.append({
            "id": cid, "kind": kind, "title": title, "avatar": avatars[0] if avatars else "",
            "unread": len(unread_msgs), "bot": bot, "latest_ts": unread_msgs[-1]["ts"],
            "messages": unread_msgs[-6:],  # most recent unread, oldest first
            "url": app_link(me_, cid, unread_msgs[-1]["ts"]), "web_url": _permalink(me_, cid, unread_msgs[-1]["ts"]),
        })
    conversations.sort(key=lambda c: (c["bot"], -float(c["latest_ts"])))  # people first, newest first

    mentions = []
    for m in mention_hits[:25]:
        cid = m["channel"]["id"]
        ch = infos.get(cid) or {"id": cid, **m["channel"]}
        if m.get("user") == my:
            continue
        v = view(m, ch)
        mentions.append({**v, "id": f"{cid}:{m['ts']}", "channel": "#" + (ch.get("name") or m["channel"].get("name") or "channel"),
                         "channel_id": cid, "unread": float(m["ts"]) > float(ch.get("last_read") or 0),
                         "url": app_link(me_, cid, m["ts"])})

    channels = _important_channel_posts(me_, users, lookback_days)
    return {"available": True, "team": me_.get("team"), "conversations": conversations, "mentions": mentions,
            "channels": channels, "took_s": round(time.time() - t0, 2), "generated_at": time.time()}


def _safe_info(cid: str) -> dict:
    try:
        return _conv_info(cid)
    except Exception:
        return {"id": cid}


def _important_channel_posts(me_: dict, users: dict[str, dict], lookback_days: int) -> list[dict]:
    """Human posts in company channels I'm in: @channel/@here broadcasts, and posts with real engagement."""
    try:
        chans = _get("conversations.list", types="public_channel,private_channel", limit=400,
                     exclude_archived="true")["channels"]
    except Exception:
        return []
    mine = [c for c in chans if c.get("is_member") and not NOISE_CHANNEL_RE.search(c.get("name", ""))
            and (c.get("name") in ANNOUNCE_CHANNELS or (c.get("num_members") or 0) >= 25)]
    oldest = str(int(time.time() - lookback_days * 86400))
    my = me_["user_id"]

    def one(c: dict) -> list[dict]:
        try:
            h = _get("conversations.history", channel=c["id"], oldest=oldest, limit=60)
        except Exception:
            return []
        out = []
        for m in h.get("messages", []):
            if m.get("bot_id") or not m.get("user") or m.get("user") == my:
                continue
            if m.get("subtype") not in (None, "thread_broadcast"):
                continue
            raw = m.get("text") or ""
            broadcast = bool(_SPECIAL_RE.search(raw))
            reactions = sum(r.get("count", 0) for r in m.get("reactions", []))
            replies = int(m.get("reply_count") or 0)
            score = (3 if broadcast else 0) + min(reactions, 12) / 3 + min(replies, 12) / 3
            if score < 1.3 and not (c.get("name") == "general" and len(raw) > 80):
                continue
            u = users.get(m["user"]) or {}
            text, links = render_text(raw, users)
            out.append({"id": f"{c['id']}:{m['ts']}", "channel": "#" + c["name"], "channel_id": c["id"], "ts": m["ts"],
                        "user": u.get("name") or "Someone", "avatar": u.get("avatar", ""), "text": text, "links": links,
                        "broadcast": broadcast, "reactions": reactions, "replies": replies,
                        "top_reactions": [{"emoji": _EMOJI.get(r["name"].split("::")[0]) or "", "name": r["name"].split("::")[0],
                                           "count": r.get("count", 0)}
                                          for r in sorted(m.get("reactions", []), key=lambda r: -r.get("count", 0))[:4]],
                        "unread": float(m["ts"]) > float(c.get("last_read") or 0) if c.get("last_read") else None,
                        "score": round(score, 2), "url": app_link(me_, c["id"], m["ts"]),
                        "permalink": _permalink(me_, c["id"], m["ts"])})
        return out

    with ThreadPoolExecutor(8) as ex:
        posts = [p for chunk in ex.map(one, mine) for p in chunk]
    posts.sort(key=lambda p: (-p["score"], -float(p["ts"])))
    per: dict[str, int] = {}
    out = []
    for p in posts:  # a busy channel (e.g. a deals channel) shouldn't crowd out everything else
        if per.get(p["channel"], 0) >= 3:
            continue
        per[p["channel"]] = per.get(p["channel"], 0) + 1
        out.append(p)
    return out[:10]


def search(query: str, count: int = 15) -> dict:
    """Slack message search (Slack search syntax: from:@name, in:#channel, after:YYYY-MM-DD, "phrase")."""
    if not available():
        return {"error": "Slack is not connected (SLACK_USER_TOKEN missing)."}
    me_ = me()
    users = slack_dir.user_map()
    s = _get("search.messages", query=query, sort="timestamp", count=max(1, min(int(count), 40)))["messages"]
    results = []
    for m in s.get("matches", []):
        ch = m.get("channel") or {}
        u = users.get(m.get("user") or "") or {}
        text, links = render_text(_msg_text(m), users)
        if ch.get("is_im"):
            where = "DM with " + ((users.get(ch.get("user") or "") or {}).get("name") or "someone")
        elif ch.get("is_mpim"):
            where = "Group DM"
        else:
            where = "#" + (ch.get("name") or "channel")
        results.append({"id": f"{ch.get('id')}:{m['ts']}", "ts": m["ts"], "channel": where, "channel_id": ch.get("id"),
                        "user": u.get("name") or m.get("username") or "Slack", "avatar": u.get("avatar", ""),
                        "bot": bool(u.get("bot")) or not m.get("user"), "text": text, "links": links,
                        "url": app_link(me_, ch.get("id", ""), m["ts"]),
                        "permalink": m.get("permalink") or _permalink(me_, ch.get("id", ""), m["ts"])})
    return {"query": query, "total": s.get("total", len(results)), "results": results}


def summary_for_model(data: dict) -> dict:
    """Compact version for the voice model (no avatars / links / ids beyond what it needs)."""
    return {
        "unread_conversations": [{"with": c["title"], "kind": c["kind"], "unread": c["unread"], "bot": c["bot"],
                                  "latest": [f"{m['user']}: {m['text'][:220]}" for m in c["messages"][-3:]]}
                                 for c in data.get("conversations", [])[:12]],
        "mentions": [{"channel": m["channel"], "from": m["user"], "text": m["text"][:240], "unread": m["unread"]}
                     for m in data.get("mentions", [])[:10]],
        "company_posts": [{"channel": p["channel"], "from": p["user"], "text": p["text"][:240],
                           "broadcast": p["broadcast"], "reactions": p["reactions"], "replies": p["replies"]}
                          for p in data.get("channels", [])[:8]],
    }
