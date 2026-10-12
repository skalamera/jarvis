"""Instagram / Facebook / LinkedIn displays: the real sites running in the HUD (his own logins, one persistent
partition per site), like the X display. No API keys involved (their APIs don't expose his feed), so these tools
don't read posts themselves: they open the right page (feed, messages, notifications, a search, a profile) and he
reads it there.
"""
from __future__ import annotations

from urllib.parse import quote

from . import store

SITES: dict[str, dict] = {
    "instagram": {
        "name": "Instagram",
        "base": "https://www.instagram.com",
        "views": {
            "home": "/", "feed": "/", "explore": "/explore/", "reels": "/reels/", "messages": "/direct/inbox/",
            "notifications": "/notifications/", "saved": "/saved/",
        },
        "labels": {"home": "Feed", "feed": "Feed", "explore": "Explore", "reels": "Reels", "messages": "Messages",
                   "notifications": "Notifications", "saved": "Saved"},
        "search": lambda q, kind: (f"/explore/tags/{quote(q.lstrip('#'))}/" if q.startswith("#")
                                   else f"/explore/search/keyword/?q={quote(q)}"),
        "profile": lambda u: f"/{quote(u)}/",
    },
    "facebook": {
        "name": "Facebook",
        "base": "https://www.facebook.com",
        "views": {
            "home": "/", "feed": "/", "watch": "/watch/", "marketplace": "/marketplace/", "groups": "/groups/feed/",
            "messages": "/messages/", "notifications": "/notifications/", "friends": "/friends/", "events": "/events/",
        },
        "labels": {"home": "Feed", "feed": "Feed", "watch": "Watch", "marketplace": "Marketplace", "groups": "Groups",
                   "messages": "Messages", "notifications": "Notifications", "friends": "Friends", "events": "Events"},
        "search": lambda q, kind: (f"/marketplace/search/?query={quote(q)}" if kind == "marketplace"
                                   else f"/search/{ {'people': 'people', 'posts': 'posts', 'groups': 'groups', 'videos': 'videos', 'pages': 'pages'}.get(kind, 'top') }/?q={quote(q)}"),
        "profile": lambda u: f"/{quote(u)}",
    },
    "linkedin": {
        "name": "LinkedIn",
        "base": "https://www.linkedin.com",
        "views": {
            "home": "/feed/", "feed": "/feed/", "network": "/mynetwork/", "jobs": "/jobs/", "messages": "/messaging/",
            "notifications": "/notifications/", "me": "/in/me/",
        },
        "labels": {"home": "Feed", "feed": "Feed", "network": "My Network", "jobs": "Jobs", "messages": "Messages",
                   "notifications": "Notifications", "me": "Profile"},
        "search": lambda q, kind: (f"/jobs/search/?keywords={quote(q)}" if kind == "jobs"
                                   else f"/search/results/{ {'people': 'people', 'companies': 'companies', 'posts': 'content', 'content': 'content'}.get(kind, 'all') }/?keywords={quote(q)}"),
        "profile": lambda u: (f"/company/{quote(u.split(':', 1)[1])}/" if u.startswith("company:") else f"/in/{quote(u)}/"),
    },
}


def social_url(site: str, view: str = "home", query: str = "", kind: str = "", user: str = "") -> dict:
    s = SITES[site]
    view = (view or "home").lower().strip()
    kind = (kind or "").lower().strip()
    q = (query or "").strip()
    user = (user or "").strip().lstrip("@")
    name = s["name"]
    if "://" in user:  # a full profile link
        return {"site": site, "view": "profile", "user": user, "url": user, "title": f"{name} · profile"}
    if user or view == "profile":
        if not user:
            raise ValueError("Whose profile?")
        return {"site": site, "view": "profile", "user": user, "url": s["base"] + s["profile"](user),
                "title": f"{name} · {user.split(':', 1)[-1]}"}
    if q or view == "search":
        if not q:
            raise ValueError(f"Search {name} for what?")
        return {"site": site, "view": "search", "query": q, "kind": kind, "url": s["base"] + s["search"](q, kind),
                "title": f"{name} · {q}"}
    if view not in s["views"]:
        view = "home"
    return {"site": site, "view": view, "url": s["base"] + s["views"][view], "title": f"{name} · {s['labels'][view]}"}


def social_open(site: str, view: str = "home", query: str = "", kind: str = "", user: str = "", record: bool = True) -> dict:
    r = social_url(site, view, query, kind, user)
    if record:
        store.record_result(f"{site}_open", None, {"view": view, "query": query, "kind": kind, "user": user}, r)
    return r
