"""Uber Eats for JARVIS: stores near him, full menus, item options and a cart, through Uber Eats' own web API
(www.ubereats.com/_p/api/*, no official public API). Browsing works without a login (delivery location = his home).
Placing orders needs his signed-in Uber session (UBER_EATS_SID in ~/.hermes/.env) and ALWAYS goes through a
confirm card with the full total; it is not wired yet (see order_status()).

Cart lives in STATE_DIR/eats_cart.json: one store at a time, like the app.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import uuid as uuidlib
from pathlib import Path
from typing import Any

import httpx

from . import store as S

API = "https://www.ubereats.com/_p/api/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
CART = S.STATE_DIR / "eats_cart.json"
DEFAULT_TIP_PCT = 15
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_loc_cache: dict | None = None


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in f.read_text().splitlines():
            m = re.match(rf"\s*(?:export\s+)?{name}\s*=\s*(.*)", line)
            if m and m[1].strip():
                return m[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------ http
def _location() -> dict:
    """His delivery location: JARVIS_EATS_ADDRESS (lat,lng) or the weather HOME, reverse-geocoded by Uber."""
    global _loc_cache
    if _loc_cache:
        return _loc_cache
    from . import weather
    with httpx.Client(timeout=10) as h:
        here = weather._here(h)
    lat, lng = here["lat"], here["lon"]
    with httpx.Client(headers=_headers(), timeout=15) as c:
        d = c.post(API + "getOriginsLocationDetailsFromCoordinateV1", json={"latitude": lat, "longitude": lng}).json()["data"]
    _loc_cache = {"address": {"address1": d.get("addressLine1") or d.get("title"), "address2": d.get("addressLine2"),
                              "aptOrSuite": "", "eaterFormattedAddress": d.get("fullAddress"),
                              "subtitle": d.get("subtitle"), "title": d.get("title"), "uuid": ""},
                  "latitude": d["coordinate"]["latitude"], "longitude": d["coordinate"]["longitude"],
                  "reference": d.get("id", ""), "referenceType": d.get("provider", "uber_places"),
                  "type": d.get("provider", "uber_places")}
    return _loc_cache


def _headers() -> dict:
    return {"User-Agent": UA, "x-csrf-token": "x", "Content-Type": "application/json", "Accept-Language": "en-US"}


def _post(endpoint: str, body: dict, ttl: float = 0) -> dict:
    key = endpoint + json.dumps(body, sort_keys=True)
    if ttl and key in _cache and time.time() - _cache[key][0] < ttl:
        return _cache[key][1]
    cookies = {"uev2.loc": urllib.parse.quote(json.dumps(_location()))}
    sid = _env("UBER_EATS_SID")
    if sid:
        cookies["sid"] = sid
    with httpx.Client(headers=_headers(), cookies=cookies, timeout=20) as c:
        r = c.post(API + endpoint, json=body)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"Uber Eats returned HTTP {r.status_code} (site may be blocking or changed)")
    if j.get("status") != "success":
        raise RuntimeError(f"Uber Eats {endpoint}: {(j.get('data') or {}).get('message') or r.status_code}")
    if ttl:
        _cache[key] = (time.time(), j["data"])
    return j["data"]


# ------------------------------------------------------------------ parsing
def _img(store_: dict, width: int = 750) -> str | None:
    items = ((store_.get("image") or {}).get("items")) or []
    if not items:
        return None
    return min(items, key=lambda i: abs((i.get("width") or 0) - width)).get("url")


def _store_summary(st: dict) -> dict:
    pay = (st.get("tracking") or {}).get("storePayload") or {}
    eta = (pay.get("etdInfo") or {}).get("dropoffETARange") or {}
    meta = [m.get("text") for m in st.get("meta") or [] if m.get("text")]
    signs = [s.get("text") for s in st.get("signposts") or [] if s.get("text")]
    state = pay.get("storeAvailablityState") or ""
    return {"id": st.get("storeUuid"), "name": (st.get("title") or {}).get("text"), "image": _img(st),
            "rating": (pay.get("ratingInfo") or {}).get("storeRatingScore"),
            "reviews": (pay.get("ratingInfo") or {}).get("ratingCount"),
            "eta_min": eta.get("min"), "eta_max": eta.get("max"), "meta": meta, "promos": signs,
            "accepting": state not in ("NOT_ACCEPTING_ORDERS", "CLOSED") and pay.get("isOrderable", True),
            "state": state, "url": "https://www.ubereats.com" + (st.get("actionUrl") or "")}


def _feed_stores(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i in items:
        st = i.get("store") or (i.get("miniStoreWithItems") or {}).get("store")
        if st and st.get("storeUuid") and st["storeUuid"] not in seen:
            seen.add(st["storeUuid"])
            out.append(_store_summary(st))
        for sub in (i.get("carousel") or {}).get("stores") or []:
            if sub.get("storeUuid") and sub["storeUuid"] not in seen:
                seen.add(sub["storeUuid"])
                out.append(_store_summary(sub))
    return out


def _cents(v: Any) -> float | None:
    return None if v is None else round(v / 100, 2)


def parse_menu(d: dict) -> dict:
    """getStoreV1 data -> {store info, menus: [{title, hours, sections: [{title, items}]}]}. Every catalog item."""
    sections: list[dict] = []
    for menu_id, blocks in (d.get("catalogSectionsMap") or {}).items():
        for b in blocks:
            p = (b.get("payload") or {}).get("standardItemsPayload")
            if not p:
                continue
            items = [{"id": it["uuid"], "name": it.get("title"), "price": _cents(it.get("price")),
                      "description": (it.get("itemDescription") or "").strip() or None, "image": it.get("imageUrl") or None,
                      "sold_out": bool(it.get("isSoldOut")) or it.get("isAvailable") is False,
                      "options": bool(it.get("hasCustomizations")), "section_id": it.get("sectionUuid"),
                      "subsection_id": it.get("subsectionUuid"),
                      "tagline": ((it.get("priceTagline") or {}).get("text") or "")}
                     for it in p.get("catalogItems") or []]
            if items:
                sections.append({"menu_id": menu_id, "title": (p.get("title") or {}).get("text") or "Menu", "items": items})
    loc = d.get("location") or {}
    hours = [f"{h['dayRange']}: " + ", ".join(f"{_hm(s['startTime'])}–{_hm(s['endTime'])}" for s in h.get("sectionHours") or [])
             for h in d.get("hours") or []]
    return {"id": d.get("uuid"), "name": d.get("title"), "address": loc.get("address"),
            "lat": loc.get("latitude"), "lng": loc.get("longitude"),
            "image": (d.get("heroImageUrls") or [{}])[-1].get("url") if d.get("heroImageUrls") else None,
            "rating": (d.get("rating") or {}).get("ratingValue"), "reviews": (d.get("rating") or {}).get("reviewCount"),
            "eta": (d.get("etaRange") or {}).get("text"), "distance": (d.get("distanceBadge") or {}).get("text"),
            "price_bucket": d.get("priceBucket"), "cuisines": d.get("cuisineList") or [],
            "open": bool(d.get("isOpen")), "closed_message": d.get("closedMessage") or None, "hours": hours,
            "menus": [{"title": s.get("title"), "hours": s.get("subtitle")} for s in d.get("sections") or []],
            "sections": sections, "item_count": sum(len(s["items"]) for s in sections),
            "url": f"https://www.ubereats.com/store/{d.get('slug', '')}/{d.get('uuid', '')}"}


def _hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


# ------------------------------------------------------------------ tools
def search(query: str = "", limit: int = 12) -> dict:
    """Stores near him for a query (cuisine, dish, restaurant name); empty query = the home feed."""
    if query.strip():
        d = _post("getSearchFeedV1", {"userQuery": query, "date": "", "startTime": 0, "endTime": 0, "vertical": "ALL",
                                      "searchSource": "SEARCH_SUGGESTION", "displayType": "SEARCH_RESULTS",
                                      "searchType": "GLOBAL_SEARCH", "keyName": "", "cacheKey": "", "recaptchaToken": ""},
                  ttl=300)
    else:
        d = _post("getFeedV1", {"cacheKey": "", "feedSessionCount": {"announcementCount": 0, "announcementLabel": ""},
                                "userQuery": "", "date": "", "startTime": 0, "endTime": 0, "carouselId": "",
                                "sortAndFilters": [], "billboardUuid": "", "feedProvider": "", "promotionUuid": "",
                                "targetingStoreTag": "", "venueUUID": "", "selectedSectionUUID": "", "favorites": "",
                                "vertical": "", "searchSource": "", "searchType": "", "keyName": "",
                                "serializedRequestContext": "", "isUserInitiatedRefresh": False}, ttl=300)
    stores = _feed_stores(d.get("feedItems") or [])[:max(1, min(limit, 30))]
    addr = (_location()["address"] or {}).get("eaterFormattedAddress")
    res = {"key": f"eats:search:{query.lower()[:40]}", "kind": "eats_stores", "query": query, "deliver_to": addr,
           "stores": stores}
    S.record_result("eats_search", None, {"query": query}, res)
    return {"query": query, "deliver_to": addr,
            "stores": [{k: s[k] for k in ("id", "name", "rating", "eta_min", "eta_max", "accepting", "promos")} for s in stores],
            "shown": "the stores are on screen"}


def _find_store_id(store_name_or_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", store_name_or_id):
        return store_name_or_id
    d = _post("getSearchFeedV1", {"userQuery": store_name_or_id, "date": "", "startTime": 0, "endTime": 0,
                                  "vertical": "ALL", "searchSource": "SEARCH_SUGGESTION", "displayType": "SEARCH_RESULTS",
                                  "searchType": "GLOBAL_SEARCH", "keyName": "", "cacheKey": "", "recaptchaToken": ""}, ttl=300)
    stores = _feed_stores(d.get("feedItems") or [])
    if not stores:
        raise ValueError(f"No Uber Eats store matching '{store_name_or_id}' delivers to him.")
    want = store_name_or_id.lower().strip()
    exact = [s for s in stores if (s["name"] or "").lower().strip() == want]
    return (exact or stores)[0]["id"]


def get_menu(store_id: str) -> dict:
    return parse_menu(_post("getStoreV1", {"storeUuid": store_id, "diningMode": "DELIVERY", "time": {"asap": True},
                                           "cbType": "EATER_ENDORSED"}, ttl=600))


def menu(store: str) -> dict:
    """The FULL menu of one store (name or id): every section and item, with prices. Opens the menu display."""
    m = get_menu(_find_store_id(store))
    S.record_result("eats_menu", None, {"store": m["name"]},
                                     {"key": f"eats:menu:{m['id']}", "kind": "eats_menu", "cart": _cart_view(), **m})
    # compact for the model: section -> "name $price" (full detail is on screen)
    return {"store_id": m["id"], "name": m["name"], "open": m["open"], "eta": m["eta"], "rating": m["rating"],
            "hours": m["hours"], "item_count": m["item_count"],
            "sections": {s["title"]: [f"{i['name']} ${i['price']:.2f}" + (" (sold out)" if i["sold_out"] else "")
                                      for i in s["items"]] for s in m["sections"]},
            "shown": f"the full {m['item_count']}-item menu is on screen; do NOT draw another table of it"}


def item_options(store_id: str, item_id: str) -> dict:
    m = get_menu(store_id)
    it = next((i for s in m["sections"] for i in s["items"] if i["id"] == item_id), None)
    if not it:
        raise ValueError("That item isn't on this store's menu.")
    d = _post("getMenuItemV1", {"itemRequestType": "ITEM", "storeUuid": store_id, "sectionUuid": it["section_id"],
                                "subsectionUuid": it["subsection_id"], "menuItemUuid": item_id, "cbType": "EATER_ENDORSED",
                                "contextReferences": [{"type": "GROUP_ITEMS", "payload": {
                                    "type": "groupItemsContextReferencePayload", "groupItemsContextReferencePayload": {}},
                                    "pageContext": "UNKNOWN"}]}, ttl=600)
    groups = [{"id": g["uuid"], "title": g.get("title"), "min": g.get("minPermitted") or 0, "max": g.get("maxPermitted"),
               "options": [{"id": o["uuid"], "name": o.get("title"), "price": _cents(o.get("price")),
                            "default": (o.get("defaultQuantity") or 0) > 0} for o in g.get("options") or []]}
              for g in d.get("customizationsList") or []]
    return {"item_id": item_id, "name": d.get("title"), "price": _cents(d.get("price")),
            "description": d.get("itemDescription"), "groups": groups}


# ------------------------------------------------------------------ cart (local until checkout)
def _load_cart() -> dict:
    try:
        return json.loads(CART.read_text())
    except Exception:
        return {"store_id": None, "store_name": None, "items": []}


def _save_cart(c: dict) -> None:
    CART.parent.mkdir(parents=True, exist_ok=True)
    CART.write_text(json.dumps(c, indent=1))


def _cart_view() -> dict:
    c = _load_cart()
    sub = round(sum(i["unit_price"] * i["qty"] for i in c["items"]), 2)
    return {**c, "subtotal": sub, "tip_pct": DEFAULT_TIP_PCT, "count": sum(i["qty"] for i in c["items"])}


def cart_add(store: str, item: str, qty: int = 1, options: list[str] | None = None, note: str = "") -> dict:
    """Add an item (name or id) to the cart. options: option names to add (e.g. 'Bacon', 'Everything bagel').
    Required choices without a pick are filled with the menu's default and reported back."""
    sid = _find_store_id(store)
    m = get_menu(sid)
    items = [i for s in m["sections"] for i in s["items"]]
    want = item.lower().strip()
    it = (next((i for i in items if i["id"] == item), None) or next((i for i in items if (i["name"] or "").lower() == want), None)
          or next((i for i in items if want in (i["name"] or "").lower()), None))
    if not it:
        close = [i["name"] for i in items if any(w in (i["name"] or "").lower() for w in want.split() if len(w) > 3)][:6]
        raise ValueError(f"'{item}' isn't on the {m['name']} menu." + (f" Close: {', '.join(close)}." if close else ""))
    if it["sold_out"]:
        raise ValueError(f"{it['name']} is sold out at {m['name']}.")
    picked, unmatched, defaulted, extra = [], list(options or []), [], 0.0
    if it["options"]:
        opts = item_options(sid, it["id"])
        for g in opts["groups"]:
            chosen = []
            for o in g["options"]:
                hit = next((u for u in unmatched if u.lower() in (o["name"] or "").lower()), None)
                if hit:
                    chosen.append(o)
                    unmatched.remove(hit)
            if not chosen and g["min"] > 0:
                chosen = [o for o in g["options"] if o["default"]][:g["min"]] or g["options"][:g["min"]]
                defaulted += [f"{g['title']}: {o['name']}" for o in chosen]
            for o in chosen:
                picked.append({"group_id": g["id"], "group": g["title"], "option_id": o["id"], "name": o["name"],
                               "price": o["price"] or 0})
                # "Comes With" groups price their defaults into the base item
                if not o["default"]:
                    extra += o["price"] or 0
    with _lock:
        c = _load_cart()
        if c["store_id"] and c["store_id"] != sid and c["items"]:
            raise ValueError(f"The cart has items from {c['store_name']}. Clear it first (one store per order).")
        c.update(store_id=sid, store_name=m["name"])
        c["items"].append({"line_id": uuidlib.uuid4().hex[:8], "item_id": it["id"], "name": it["name"], "qty": max(1, int(qty)),
                           "unit_price": round((it["price"] or 0) + extra, 2), "options": picked, "note": note,
                           "section_id": it["section_id"], "subsection_id": it["subsection_id"], "image": it["image"]})
        _save_cart(c)
    _show_cart(m)
    v = _cart_view()
    return {"added": it["name"], "qty": qty, "options": [p["name"] for p in picked], "defaulted": defaulted,
            "unmatched_options": unmatched, "cart_subtotal": v["subtotal"], "cart_count": v["count"],
            "note": "Estimated subtotal before fees/tax/tip. Tell him any defaulted or unmatched options."}


def cart_remove(item: str = "", clear: bool = False) -> dict:
    with _lock:
        c = _load_cart()
        if clear:
            c = {"store_id": None, "store_name": None, "items": []}
        else:
            w = item.lower()
            idx = next((k for k, i in enumerate(c["items"]) if i["line_id"] == item or w in i["name"].lower()), None)
            if idx is None:
                raise ValueError(f"'{item}' isn't in the cart.")
            c["items"].pop(idx)
        _save_cart(c)
    _show_cart(None)
    v = _cart_view()
    return {"cart_count": v["count"], "cart_subtotal": v["subtotal"]}


def cart() -> dict:
    _show_cart(None)
    v = _cart_view()
    return {"store": v["store_name"], "items": [f"{i['qty']}x {i['name']} ({', '.join(o['name'] for o in i['options'])})"
                                                for i in v["items"]], "subtotal": v["subtotal"], "shown": "the cart is on screen"}


def _show_cart(m: dict | None) -> None:
    v = _cart_view()
    S.record_result("eats_cart", None, {}, {"key": "eats:cart", "kind": "eats_cart", **v,
                                                 "can_order": bool(_env("UBER_EATS_SID"))})
    if m:  # refresh the open menu's cart rail too
        S.record_result("eats_menu", None, {"store": m["name"]},
                            {"key": f"eats:menu:{m['id']}", "kind": "eats_menu", "cart": v, **m})


def order(tip_pct: int | None = None) -> dict:
    """Place the cart as an order (always behind a confirm card). Needs his Uber session; not wired yet."""
    if not _load_cart()["items"]:
        raise ValueError("The cart is empty.")
    if not _env("UBER_EATS_SID"):
        raise RuntimeError("Ordering isn't connected yet: he needs to sign in to Uber in the JARVIS browser first. "
                           "Nothing was ordered. The cart is saved.")
    raise RuntimeError("Uber Eats checkout isn't wired up yet (pending his sign-in and a verified checkout flow). "
                       "Nothing was ordered. The cart is saved.")
