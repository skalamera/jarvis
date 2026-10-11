"""X (formerly Twitter) display: the real x.com running in the HUD (his own login, kept in its own partition).

There's no API key involved (X's API is paid), so this tool doesn't read posts itself: it opens the X display at the
right page (home timeline, trending, notifications, messages, a search, or someone's profile) and he reads it there.
"""
from __future__ import annotations

from urllib.parse import quote

from . import store

VIEWS = {
    "home": ("https://x.com/home", "X · Home"),
    "explore": ("https://x.com/explore/tabs/trending", "X · Trending"),
    "trending": ("https://x.com/explore/tabs/trending", "X · Trending"),
    "news": ("https://x.com/explore/tabs/news", "X · News"),
    "notifications": ("https://x.com/notifications", "X · Notifications"),
    "messages": ("https://x.com/messages", "X · Messages"),
    "bookmarks": ("https://x.com/i/bookmarks", "X · Bookmarks"),
}


def x_url(view: str = "home", query: str = "", latest: bool = True, user: str = "") -> dict:
    view = (view or "home").lower().strip()
    user = (user or "").strip().lstrip("@")
    q = (query or "").strip()
    if user or view == "profile":
        if not user:
            raise ValueError("Whose profile?")
        return {"view": "profile", "user": user, "url": f"https://x.com/{quote(user)}", "title": f"X · @{user}"}
    if q or view == "search":
        if not q:
            raise ValueError("Search X for what?")
        return {"view": "search", "query": q, "latest": bool(latest),
                "url": f"https://x.com/search?q={quote(q)}&src=typed_query" + ("&f=live" if latest else ""),
                "title": f"X · {q}"}
    url, title = VIEWS.get(view, VIEWS["home"])
    return {"view": view if view in VIEWS else "home", "url": url, "title": title}


def x_open(view: str = "home", query: str = "", latest: bool = True, user: str = "", record: bool = True) -> dict:
    r = x_url(view, query, latest, user)
    if record:
        store.record_result("x_open", None, {"view": view, "query": query, "user": user}, r)
    return r
