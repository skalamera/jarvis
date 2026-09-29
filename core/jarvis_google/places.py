"""Google Places (New) for JARVIS: restaurant / place search and a full place card.

Places API (New) with explicit field masks (billing goes by the highest-tier field requested). Photo URLs are
resolved server-side (skipHttpRedirect) into short-lived googleusercontent links, so the API key never reaches
the HUD. Popular times is not in the API. Results are cached for 10 min to avoid paying twice for the same card.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import store
from .weather import _here

API = "https://places.googleapis.com/v1"
PRICE = {"PRICE_LEVEL_FREE": "Free", "PRICE_LEVEL_INEXPENSIVE": "$", "PRICE_LEVEL_MODERATE": "$$",
         "PRICE_LEVEL_EXPENSIVE": "$$$", "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$"}

# list rows: what a row shows + 1 thumbnail. No reviews here (they are the priciest fields); the full card has them.
LIST_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "formattedAddress", "shortFormattedAddress", "location", "rating", "userRatingCount",
    "priceLevel", "priceRange", "primaryTypeDisplayName", "currentOpeningHours.openNow",
    "currentOpeningHours.nextOpenTime", "currentOpeningHours.nextCloseTime", "photos",
    "businessStatus", "googleMapsUri"))
DETAIL_FIELDS = ",".join((
    "id", "displayName", "formattedAddress", "shortFormattedAddress", "location", "rating", "userRatingCount",
    "priceLevel", "priceRange", "primaryTypeDisplayName", "businessStatus", "googleMapsUri", "websiteUri",
    "nationalPhoneNumber", "internationalPhoneNumber", "reservable", "servesDinner", "servesLunch", "servesBrunch",
    "delivery", "takeout", "dineIn", "outdoorSeating", "servesVegetarianFood", "servesCocktails", "goodForGroups",
    "regularOpeningHours", "currentOpeningHours", "utcOffsetMinutes", "photos", "reviews", "reviewSummary",
    "generativeSummary", "editorialSummary"))


def _key() -> str:
    v = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if v:
        return v
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in f.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?GOOGLE_MAPS_API_KEY\s*=\s*(.*)", line)
            if m and m[1].strip():
                return m[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")


_local = threading.local()


def _http() -> httpx.Client:
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = httpx.Client(timeout=20, headers={"X-Goog-Api-Key": _key(), "Content-Type": "application/json"})
    return c


def _err(r: httpx.Response) -> str:
    try:
        return r.json().get("error", {}).get("message", r.text[:200])
    except Exception:
        return r.text[:200]


_cache: dict[str, tuple[float, Any]] = {}
_photo_cache: dict[str, tuple[float, str]] = {}


def _photo(name: str, width: int = 900) -> str | None:
    hit = _photo_cache.get(f"{name}@{width}")
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    r = _http().get(f"{API}/{name}/media", params={"maxWidthPx": width, "skipHttpRedirect": "true"})
    if r.status_code != 200:
        return None
    uri = r.json().get("photoUri")
    if uri:
        _photo_cache[f"{name}@{width}"] = (time.time(), uri)
    return uri


def _money(p: dict | None) -> str:
    if not p:
        return ""
    lo, hi = (p.get("startPrice") or {}).get("units"), (p.get("endPrice") or {}).get("units")
    sym = "$" if ((p.get("startPrice") or p.get("endPrice") or {}).get("currencyCode") or "USD") == "USD" else ""
    if lo and hi:
        return f"{sym}{lo}–{hi}"
    if lo:
        return f"{sym}{lo}+"
    return ""


def _fmt_t(iso: str | None) -> str:
    if not iso:
        return ""
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    now = dt.datetime.now().astimezone()
    hm = t.strftime("%-I:%M %p").replace(":00 ", " ")
    if t.date() == now.date():
        return hm
    if t.date() == (now + dt.timedelta(days=1)).date():
        return f"{hm} tomorrow"
    return f"{hm} {t:%a}"


def _hours(p: dict) -> dict:
    cur = p.get("currentOpeningHours") or {}
    reg = p.get("regularOpeningHours") or {}
    open_now = cur.get("openNow")
    status = "Open" if open_now else ("Closed" if open_now is False else "")
    if p.get("businessStatus") == "CLOSED_TEMPORARILY":
        status = "Temporarily closed"
    elif p.get("businessStatus") == "CLOSED_PERMANENTLY":
        status = "Permanently closed"
    detail = ""
    if open_now and cur.get("nextCloseTime"):
        detail = f"Closes {_fmt_t(cur['nextCloseTime'])}"
    elif open_now is False and cur.get("nextOpenTime"):
        detail = f"Opens {_fmt_t(cur['nextOpenTime'])}"
    week = [w.replace("\u2009", " ").replace("\u202f", " ") for w in (reg.get("weekdayDescriptions") or cur.get("weekdayDescriptions") or [])]
    today = dt.datetime.now().weekday()  # Google's list starts Monday
    return {"open_now": open_now, "status": status, "detail": detail, "week": week, "today_index": today if len(week) == 7 else None}


def _base(p: dict) -> dict:
    loc = p.get("location") or {}
    return {
        "id": p["id"], "name": (p.get("displayName") or {}).get("text", ""),
        "address": p.get("formattedAddress", ""), "short_address": p.get("shortFormattedAddress") or p.get("formattedAddress", ""),
        "lat": loc.get("latitude"), "lng": loc.get("longitude"),
        "rating": p.get("rating"), "reviews_count": p.get("userRatingCount"),
        "price": _money(p.get("priceRange")) or PRICE.get(p.get("priceLevel", ""), ""),
        "type": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
        "maps_url": p.get("googleMapsUri", ""), "hours": _hours(p),
    }


def _near() -> dict:
    with httpx.Client() as h:
        return _here(h)


def places_search(query: str, near: str = "", limit: int = 6, open_now: bool = False) -> dict:
    """Text search, biased to where Stephen is (or `near`). Returns rows with one photo each."""
    limit = max(1, min(int(limit or 6), 10))
    ck = f"s|{query}|{near}|{limit}|{open_now}"
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 600:
        res = hit[1]
    else:
        body: dict[str, Any] = {"textQuery": query + (f" near {near}" if near else ""), "pageSize": limit}
        where = ""
        if not near:
            try:
                h = _near()
                body["locationBias"] = {"circle": {"center": {"latitude": h["lat"], "longitude": h["lon"]}, "radius": 15000.0}}
                where = f"{h['name']}, {h.get('region', '')}".strip(", ")
            except Exception:
                pass
        if open_now:
            body["openNow"] = True
        r = _http().post(f"{API}/places:searchText", headers={"X-Goog-FieldMask": LIST_FIELDS}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Places search failed: {_err(r)}")
        ps = r.json().get("places") or []
        rows = [_base(p) for p in ps]
        with ThreadPoolExecutor(6) as ex:
            thumbs = list(ex.map(lambda p: _photo(p["photos"][0]["name"], 480) if p.get("photos") else None, ps))
        for row, th in zip(rows, thumbs):
            row["photo"] = th
        res = {"query": query, "near": near or where, "count": len(rows), "places": rows}
        _cache[ck] = (time.time(), res)
    store.record_result("places_search", None, {"query": query, "near": near}, res)
    return res


def place_details(place_id: str = "", query: str = "", record: bool = True) -> dict:
    """Full card for one place: photos, hours, reviews, AI review summary, contact, booking."""
    if not place_id:
        if not query:
            raise ValueError("Give a place_id or a query.")
        r = _http().post(f"{API}/places:searchText", headers={"X-Goog-FieldMask": "places.id"}, json={"textQuery": query, "pageSize": 1})
        if r.status_code != 200 or not r.json().get("places"):
            raise ValueError(f"Couldn't find '{query}'.")
        place_id = r.json()["places"][0]["id"]
    ck = f"d|{place_id}"
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 600:
        res = hit[1]
    else:
        r = _http().get(f"{API}/places/{place_id}", headers={"X-Goog-FieldMask": DETAIL_FIELDS})
        if r.status_code != 200:
            raise RuntimeError(f"Place details failed: {_err(r)}")
        p = r.json()
        res = _base(p)
        names = [ph["name"] for ph in (p.get("photos") or [])[:7]]
        with ThreadPoolExecutor(7) as ex:
            urls = list(ex.map(lambda n: _photo(n, 1000), names))
        photos = []
        for ph, u in zip((p.get("photos") or [])[:7], urls):
            if u:
                photos.append({"url": u, "by": ", ".join(a.get("displayName", "") for a in ph.get("authorAttributions") or [])})
        rs = []
        for rv in (p.get("reviews") or [])[:5]:
            a = rv.get("authorAttribution") or {}
            rs.append({"author": a.get("displayName", ""), "avatar": a.get("photoUri", ""), "rating": rv.get("rating"),
                       "when": rv.get("relativePublishTimeDescription", ""),
                       "text": ((rv.get("text") or rv.get("originalText") or {}).get("text") or "")[:600]})
        rsum = ((p.get("reviewSummary") or {}).get("text") or {}).get("text", "")
        overview = ((p.get("generativeSummary") or {}).get("overview") or {}).get("text", "") or \
            ((p.get("editorialSummary") or {}).get("text", ""))
        feats = [lab for key, lab in (("dineIn", "Dine-in"), ("takeout", "Takeout"), ("delivery", "Delivery"),
                                      ("outdoorSeating", "Outdoor seating"), ("servesCocktails", "Cocktails"),
                                      ("servesVegetarianFood", "Vegetarian"), ("goodForGroups", "Good for groups"),
                                      ("reservable", "Reservations")) if p.get(key)]
        res.update({
            "photos": photos, "reviews": rs, "review_summary": rsum, "overview": overview, "features": feats,
            "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
            "phone_intl": (p.get("internationalPhoneNumber") or "").replace(" ", "").replace("-", ""),
            "website": p.get("websiteUri", ""), "reservable": bool(p.get("reservable")),
            "attribution": "Google",
        })
        _cache[ck] = (time.time(), res)
    if record:
        store.record_result("place_details", None, {"place_id": place_id}, res)
    return res


def place_open(place_id: str) -> dict:
    """HUD click on a list row: show the full card."""
    return place_details(place_id=place_id)
