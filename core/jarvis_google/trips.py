"""Trip planner: a described trip -> researched options + three hour-by-hour itineraries, revisable by voice.

Data sources (all real, nothing invented by the model on its own):
  - Google Places: hotels, sights and restaurants (names, ratings, price level, photos, map links).
  - Open-Meteo forecast (weather.weather) when the trip is inside the 14-day window; otherwise typical
    conditions from a Google-search-grounded Gemini pass, labelled "typical".
  - Google-search-grounded Gemini research: events on the trip dates, getting there (flights/train/drive),
    hotel nightly price estimates, car rentals and local tips. Prices from search are labelled estimates.
  - Duffel live rates are used for hotels/cars only when the Duffel token is live (test inventory is fake).
The composer may only place hotels/sights/restaurants/events from those lists into the itineraries.

Trips persist in STATE_DIR/trips/<id>.json with every version kept, so a revision can always be undone.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from . import store
from .garage import API, MODEL, _env, _gemini

HOME = "New Rochelle, NY"
DIR = store.STATE_DIR / "trips"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_LOCK = threading.Lock()


# ---------------------------------------------------------------- storage

def _path(tid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{4,40}", tid or ""):
        raise ValueError("bad trip id")
    return DIR / f"{tid}.json"


def _save(trip: dict) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    p = _path(trip["id"])
    hist = json.loads(p.read_text()).get("versions", []) if p.exists() else []
    snap = {k: v for k, v in trip.items() if k != "versions"}
    trip["versions"] = (hist + [snap])[-15:]
    trip["updated"] = time.time()
    p.write_text(json.dumps(trip, default=str))
    p.chmod(0o600)
    return trip


def _load(tid: str = "") -> dict:
    if not tid:
        files = sorted(DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True) if DIR.exists() else []
        if not files:
            raise ValueError("no trip has been planned yet")
        return json.loads(files[0].read_text())
    return json.loads(_path(tid).read_text())


def _card(trip: dict) -> dict:
    """The card payload: everything but the version history."""
    return {**{k: v for k, v in trip.items() if k not in ("versions", "_gathered")}, "kind": "trip", "key": f"trip:{trip['id']}",
            "version_count": len(trip.get("versions") or [])}


def _publish(trip: dict, tool: str, args: dict) -> None:
    store.record_result(tool, None, args, _card(trip))


def _ppath(tid: str) -> Path:
    _path(tid)  # validates the id
    return DIR / "progress" / f"{tid}.json"


def _step(tid: str, msg: str, done: bool = False, error: str = "") -> None:
    """Progress lives in a file: the voice path runs in the MCP server process, the HUD polls through Core."""
    with _LOCK:
        f = _ppath(tid)
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            p = json.loads(f.read_text())
        except Exception:
            p = {"steps": [], "started": time.time()}
        if msg and (not p["steps"] or p["steps"][-1] != msg):
            p["steps"].append(msg)
            p.setdefault("at", []).append(round(time.time() - p["started"], 1))
        p["done"], p["error"] = done, error
        f.write_text(json.dumps(p))


def _reset(tid: str) -> None:
    _ppath(tid).unlink(missing_ok=True)


def trip_progress(trip_id: str) -> dict:
    try:
        return json.loads(_ppath(trip_id).read_text())
    except Exception:
        return {"steps": [], "done": False}


# ---------------------------------------------------------------- research helpers

def _grounded(prompt: str, timeout: float = 150) -> dict:
    """Gemini with Google Search grounding; JSON is requested in the text (grounding forbids a JSON mime type)."""
    key = _env("GEMINI_API_KEY")
    body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2}}
    for attempt in range(3):
        r = httpx.post(f"{API}/models/{MODEL}:generateContent", headers={"x-goog-api-key": key}, json=body,
                       timeout=timeout)
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Gemini search {r.status_code}: {r.text[:200]}")
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}
    return {}


def _places(query: str, limit: int = 8) -> list[dict]:
    from . import places
    with store.capture():
        try:
            res = places.places_search(query, limit=limit)
        except Exception:
            return []
    return [{k: p.get(k) for k in ("id", "name", "short_address", "address", "rating", "reviews_count", "price",
                                     "type", "maps_url", "photo", "lat", "lng")} for p in res.get("places") or []]


def _weather(dest: str, start: str, end: str) -> dict | None:
    from . import weather as W
    try:
        s = dt.date.fromisoformat(start)
    except ValueError:
        return None
    if (s - dt.date.today()).days > 13:
        return None
    try:
        w = W.weather(dest, days=14)
    except Exception:
        return None
    days = [d for d in w.get("daily") or [] if start <= d.get("date", "") <= end]
    return {"source": "forecast", "units": w.get("units"), "days": days} if days else None


def _live_flights(b: dict) -> dict | None:
    """Real Duffel fares (LGA, then JFK, then EWR) when the Duffel token is live and the trip isn't drive-only.
    Returns None when not applicable or the search fails; the grounded estimates remain as a fallback."""
    from . import travel
    try:
        if travel.test_mode() or b.get("transport") in ("drive", "train"):
            return None
    except Exception:
        return None
    pax = max(1, int(b.get("adults") or 1) + int(b.get("children") or 0))
    dest = b.get("airport") or b["destination"]
    for q in (dest, dest.split(",")[0]):
        try:
            with store.capture() as cap:
                travel.flights_search(q, b["start_date"], b["end_date"], adults=pax, max_results=8)
            res = next((c["result"] for c in cap if c["tool"] == "flights_search"), None)
            break
        except Exception as e:
            res, err = None, str(e)
    if not res or not res.get("offers"):
        return None

    def leg(s: dict) -> dict:
        return {k: s.get(k) for k in ("origin", "destination", "depart", "arrive", "duration", "stops", "via", "flights")}
    offers = []
    for o in res["offers"]:
        offers.append({"offer_id": o["id"], "airline": o.get("airline"), "logo": o.get("logo"), "price": o.get("price"),
                       "total": o.get("total"), "currency": o.get("currency"),
                       "per_person": round((o.get("total") or 0) / pax, 2), "cabin": o.get("cabin"),
                       "refundable": o.get("refundable"), "changeable": o.get("changeable"), "bags": o.get("bags"),
                       "out": leg(o["slices"][0]) if o.get("slices") else None,
                       "back": leg(o["slices"][1]) if len(o.get("slices") or []) > 1 else None,
                       "expires_at": o.get("expires_at")})
    return {"source": "duffel", "searched_at": time.time(), "destination": res.get("destination"), "passengers": pax,
            "count": res.get("count"), "cheapest": res.get("cheapest"), "offers": offers}


def _live_cars(b: dict) -> dict | None:
    """Live rental-car prices at the destination airport (or city) for the trip dates (OctoTrip)."""
    if b.get("transport") == "train":
        return None
    from . import cars_live
    for q in ([f"{b['airport']} airport"] if b.get("airport") else []) + [b["destination"]]:
        try:
            r = cars_live.search(q, b["start_date"], b["end_date"], max_results=8)
        except Exception:
            continue
        if r.get("cars"):
            return {**r, "source": "octotrip"}
    return None


def _live_hotels(b: dict, places_hotels: list[dict]) -> dict | None:
    """Bookable LiteAPI rates around the destination (centered on the Places hotels), when a key is set."""
    from . import liteapi
    if not liteapi.configured():
        return None
    pts = [(h["lat"], h["lng"]) for h in places_hotels if h.get("lat") is not None]
    if not pts:
        return None
    lat, lng = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    try:
        return liteapi.search(lat, lng, b["start_date"], b["end_date"], adults=max(1, int(b.get("adults") or 2)),
                              radius_m=8000, limit=40, near=b["destination"])
    except Exception as e:
        store.audit({"kind": "trip_liteapi_error", "error": str(e)[:200]})
        return None


def _merge_hotels(places_hotels: list[dict], live: dict | None) -> list[dict]:
    """Attach live, bookable rates to the Places hotels by name; add the best-rated live-only hotels too."""
    if not live:
        return places_hotels
    by = {_norm(h["name"]): h for h in live.get("hotels") or []}
    used = set()
    for h in places_hotels:
        m = by.get(_norm(h["name"])) or next((v for k, v in by.items() if k and (k in _norm(h["name"]) or _norm(h["name"]) in k)), None)
        if m:
            used.add(_norm(m["name"]))
            h["live"] = {k: m.get(k) for k in ("offer_id", "price", "nightly", "total", "per_night", "currency", "room",
                                               "board", "refundable", "policy", "due_at_hotel")}
    extra = [{"id": m["hotel_id"], "live_only": True, "name": m["name"], "photo": m.get("photo"), "rating": m.get("review_score"),
              "reviews_count": m.get("reviews"), "short_address": m.get("address"), "lat": m.get("lat"), "lng": m.get("lng"),
              "price": "★" * int(m["stars"]) if m.get("stars") else "",
              "live": {k: m.get(k) for k in ("offer_id", "price", "nightly", "total", "per_night", "currency", "room",
                                              "board", "refundable", "policy", "due_at_hotel")}}
             for m in live.get("hotels") or [] if _norm(m["name"]) not in used][:8]
    return places_hotels + extra


def trip_refresh_flights(trip_id: str) -> dict:
    """Re-search live fares for a saved trip (offers expire after ~30 min). Click / RPC."""
    """Re-search every live price on a saved trip (fares, hotel rates, rental cars); offers expire."""
    trip = _load(trip_id)
    b = trip["brief"]
    with ThreadPoolExecutor(3) as ex:
        ff, cf = ex.submit(_live_flights, b), ex.submit(_live_cars, b)
        base = [{k: v for k, v in h.items() if k != "live"} for h in trip.get("hotels") or [] if not h.get("live_only")]
        hf = ex.submit(_live_hotels, b, base)
        live, cars, hotels = ff.result(), cf.result(), hf.result()
    if not (live or cars or hotels):
        raise RuntimeError("no live prices available for this trip")
    if live:
        trip["transport"]["live_flights"] = live
    if cars:
        trip["transport"]["live_cars"] = cars
    if hotels:
        trip["hotels"] = _merge_hotels(base, hotels)
        trip["hotels_live_at"] = time.time()
    return _card(_save(trip))


def _brief(request: str, prior: dict | None = None) -> dict:
    today = dt.date.today()
    schema = ('{"title": str, "destination": str (city, region/country), "origin": str, "start_date": "YYYY-MM-DD", '
              '"end_date": "YYYY-MM-DD", "nights": int, "adults": int, "children": int, "budget": "budget|moderate|'
              'upscale|luxury", "themes": [str], "must_do": [str], "avoid": [str], "transport": "fly|drive|train|'
              'auto", "airport": "IATA code of the main commercial airport for the destination", "assumptions": [str]}')
    prompt = (f"Today is {today:%A %Y-%m-%d}. The traveller lives in {HOME} (default origin). Turn this trip request "
              f"into a structured brief. Resolve relative dates ('next weekend', 'in March') to real dates after today. "
              f"If dates are missing pick the next sensible window of the length implied (default 3 nights) and say so "
              f"in assumptions. Default 2 adults, moderate budget, transport auto. Themes are style asks like "
              f"'outdoors', 'by the water', 'no hiking', 'foodie'. Keep any wording the traveller used.\n"
              + (f"Previous brief (keep anything the new text doesn't change): {json.dumps(prior)}\n" if prior else "")
              + f"Request: {request}\nReturn JSON only: {schema}")
    b = _gemini([{"text": prompt}])
    b["origin"] = b.get("origin") or HOME
    try:
        b["nights"] = max(1, (dt.date.fromisoformat(b["end_date"]) - dt.date.fromisoformat(b["start_date"])).days)
    except Exception:
        b["nights"] = int(b.get("nights") or 3)
    for k in ("themes", "must_do", "avoid", "assumptions"):
        b[k] = [str(x) for x in b.get(k) or []]
    return b


def _research(b: dict, hotels: list[dict], need_climate: bool) -> dict:
    names = ", ".join(h["name"] for h in hotels[:10])
    prompt = f"""Research a trip using Google Search. Be factual; only list things you found in search results.
Trip: {b['destination']} from {b['origin']}, {b['start_date']} to {b['end_date']} ({b['nights']} nights),
{b['adults']} adults{', ' + str(b['children']) + ' children' if b.get('children') else ''}, budget {b['budget']},
themes: {', '.join(b['themes']) or 'none'}; avoid: {', '.join(b['avoid']) or 'nothing'}.

Return ONE JSON object in a ```json block:
{{"events": [{{"name","date":"YYYY-MM-DD or range","time","venue","category","price","url","why"}}]  (real events,
   concerts, festivals, games, exhibitions, markets happening in or near the destination ON THOSE DATES; up to 12),
 "getting_there": [{{"mode":"flight|train|drive|bus|ferry","summary","duration","est_cost","notes"}}],
 "flights": [{{"airline","route","typical_duration","est_round_trip":"$..","nonstop":bool}}]  (from the nearest
   airports to the origin, LGA/JFK/EWR for NY; empty if not flying),
 "hotel_prices": [{{"name","est_nightly":"$..","area","notes"}}]  for these hotels: {names},
 "car_rentals": [{{"company","category","est_daily":"$..","pickup","notes"}}] (only if a car makes sense),
 "getting_around": [str],
 {'"climate": {"summary", "avg_high", "avg_low", "rain", "daylight"},' if need_climate else ''}
 "tips": [str]  (reservations to make early, local customs, closures on those dates, safety)}}"""
    try:
        return _grounded(prompt)
    except Exception as e:
        store.audit({"kind": "trip_research_error", "error": str(e)[:200]})
        return {}


def _gather(tid: str, b: dict) -> dict:
    d = b["destination"]
    theme = " ".join(b["themes"])[:60]
    _step(tid, f"Searching hotels, sights and restaurants in {d}")
    tier = {"budget": "affordable ", "upscale": "upscale ", "luxury": "luxury "}.get(b["budget"], "")
    q = {"hotels": (f"best {tier}hotels in {d}", 8),
         "hotels2": (f"boutique hotels in central {d}", 6),
         "sights": (f"top attractions in {d}", 10),
         "theme": (f"{theme} things to do in {d}" if theme else f"hidden gems in {d}", 8),
         "dining": (f"best restaurants in {d}", 10),
         "dining2": (f"local favorite restaurants in {d}", 10),
         "dining3": (f"{theme + ' ' if theme else ''}lunch spots in {d}", 10),
         "breakfast": (f"best breakfast brunch cafes in {d}", 10),
         "nightlife": (f"cocktail bars evening in {d}", 8),
         "sights2": (f"museums and landmarks in {d}", 8)}
    with ThreadPoolExecutor(8) as ex:
        futs = {k: ex.submit(_places, qq, n) for k, (qq, n) in q.items()}
        wfut = ex.submit(_weather, d, b["start_date"], b["end_date"])
        ffut = ex.submit(_live_flights, b)
        cfut = ex.submit(_live_cars, b)
        got = {k: f.result() for k, f in futs.items()}
        wx = wfut.result()
        live = ffut.result()
        cars = cfut.result()
    have = {h["name"] for h in got["hotels"]}
    got["hotels"] += [h for h in got.pop("hotels2") if h["name"] not in have]
    lh = _live_hotels(b, got["hotels"])
    if lh:
        _step(tid, f"Found live rates at {lh['count']} hotels")
        got["hotels"] = _merge_hotels(got["hotels"], lh)
    if live:
        _step(tid, f"Found {live['count']} live fares to {live['destination']}")
    _step(tid, "Researching events, transport and prices for your dates")
    res = _research(b, got["hotels"], need_climate=wx is None)
    if wx is None:
        c = res.get("climate") or {}
        wx = {"source": "typical", "summary": c.get("summary", ""), "avg_high": c.get("avg_high"),
              "avg_low": c.get("avg_low"), "rain": c.get("rain"), "daylight": c.get("daylight"), "days": []}
    prices = {re.sub(r"\W", "", (h.get("name") or "").lower()): h for h in res.get("hotel_prices") or []}
    for h in got["hotels"]:
        p = prices.get(re.sub(r"\W", "", h["name"].lower()))
        if p:
            h["est_nightly"], h["area"] = p.get("est_nightly"), p.get("area")
            h["price_note"] = p.get("notes")
    sights, seen = [], set()
    for s in got["sights"] + got["theme"] + got["sights2"]:
        if s["name"] not in seen:
            seen.add(s["name"])
            sights.append(s)
    dining = []  # a deep, de-duplicated pool so three itineraries can each eat somewhere different
    for s in got["dining"] + got["dining2"] + got["dining3"] + got["breakfast"]:
        if s["name"] not in seen:
            seen.add(s["name"])
            dining.append(s)
    return {"hotels": got["hotels"], "sights": sights, "dining": dining, "live_flights": live, "live_cars": cars,
            "hotels_live_at": time.time() if lh else None,
            "nightlife": got["nightlife"], "weather": wx, "research": res}


# ---------------------------------------------------------------- composition

_TRIP_SCHEMA = """{
 "title": str, "summary": str (3-4 sentences), "highlights": [str],
 "weather_summary": str, "attire": {"summary": str, "pack": [str]},
 "transport": {"recommended": str, "notes": [str]},
 "itineraries": [  exactly 3, meaningfully different (e.g. A = the classic version, B = leans hardest into the
                   stated themes, C = slower / better value), each:
   {"id": "A|B|C", "name": str (evocative, 2-4 words), "style": str, "summary": str,
    "hotel": str (EXACT name from HOTELS), "hotel_why": str, "est_total": str (whole trip, per the budget),
    "days": [ one per calendar day from start to end date inclusive:
       {"date": "YYYY-MM-DD", "title": str, "items": [
          {"time": "HH:MM", "end": "HH:MM", "type": "breakfast|lunch|dinner|activity|event|transit|hotel|free|nightlife",
           "title": str, "place": str (EXACT name from the lists, or "" for transit/free), "detail": str (1-2 sentences:
           what to do, why, booking tip), "cost": str ("$25/pp", "free", ...)}]}]}]}"""


def _compose(b: dict, g: dict, instruction: str = "", current: dict | None = None) -> dict:
    def rows(xs, keys):
        return [{k: x.get(k) for k in keys if x.get(k) not in (None, "")} for x in xs]
    lists = {
        "HOTELS": [{**r_, **({"live_nightly": h["live"]["nightly"], "live_total": h["live"]["price"]} if h.get("live") else {})}
                   for h, r_ in zip(g["hotels"], rows(g["hotels"], ("name", "short_address", "rating", "price", "est_nightly", "area")))],
        "SIGHTS": rows(g["sights"], ("name", "type", "rating", "short_address")),
        "RESTAURANTS": rows(g["dining"], ("name", "type", "rating", "price", "short_address")),
        "NIGHTLIFE": rows(g["nightlife"], ("name", "type", "rating")),
        "EVENTS": (g["research"].get("events") or []),
    }
    wx = g["weather"]
    rules = f"""You are a meticulous travel planner. Plan the trip in the BRIEF using ONLY the places in the lists
(hotels, sights, restaurants, nightlife, events). Never invent a venue. Rules:
- Every day runs about 07:30-22:30 in time order with breakfast, lunch and dinner at real listed restaurants (vary
  them; no restaurant twice in one itinerary), realistic transit legs between areas, and some downtime.
- Day 1 starts with getting there from {b['origin']}; the last day ends with the trip home. Check-in after 15:00.
- Respect themes {b['themes']}, must-do {b['must_do']} and avoid {b['avoid']} strictly (e.g. 'no hiking' = no trails).
- Use events only on their actual dates. Use the weather to put outdoor things on dry days.
- Costs are estimates for {b['adults']} adults at a {b['budget']} budget. Where a hotel has live_nightly/live_total
  (real bookable rates) use those, and prefer hotels with live rates. Use LIVE CARS prices for any rental car.
- The 3 itineraries should each use a different hotel when the list allows.
- Make the 3 itineraries as DIFFERENT as possible: every restaurant and bar appears in AT MOST ONE itinerary, and
  each sight or event appears in at most one itinerary. The only exceptions are the traveller's must-do items and at
  most two truly iconic must-sees for the destination. Spread the lists across A, B and C rather than reusing favourites."""
    lf = g.get("live_flights")
    fares = ("\nLIVE FARES (real, bookable, round trip for all travellers; use these for flight times and costs on the "
             "travel days instead of estimates, preferring LGA, and say which flight in the transit item): " + json.dumps(
                 [{k: o[k] for k in ("airline", "price", "per_person", "out", "back")} for o in lf["offers"][:5]])) if lf else ""
    lc = g.get("live_cars")
    fares += ("\nLIVE CARS (real prices, whole rental): " + json.dumps(
        [{k: c[k] for k in ("name", "category", "supplier", "price")} for c in lc["cars"][:6]])) if lc else ""
    data_head = f"BRIEF: {json.dumps(b)}\nWEATHER: {json.dumps(wx)}{fares}\nTRANSPORT RESEARCH: " \
                f"{json.dumps({k: g['research'].get(k) for k in ('getting_there', 'flights', 'getting_around')})}\n"
    data = data_head + "\n".join(f"{k}: {json.dumps(v)}" for k, v in lists.items())
    if current is not None:
        ask = (f"CURRENT PLAN (JSON): {json.dumps({k: current.get(k) for k in ('summary', 'highlights', 'attire', 'transport', 'weather_summary', 'itineraries', 'chosen')})}\n"
               f"CHANGE REQUEST: {instruction}\nApply the change. Change only what the request implies (e.g. a hotel "
               f"swap rebuilds the affected itinerary's days around the new hotel's area; a new theme reworks all 3). "
               f"Keep the rest identical. Also return \"change_summary\": one sentence describing what changed.")
    else:
        # Pools are split per itinerary, so no repair pass: rewriting all three in one call is what made plans
        # slow (it echoes every itinerary back) for little gain over the split.
        return _compose_parallel(b, rules, data_head, lists)
    out = _gem([{"text": f"{rules}\n\n{data}\n\n{ask}\nReturn JSON only:\n{_TRIP_SCHEMA}"}], timeout=420)
    if not isinstance(out, dict) or len(out.get("itineraries") or []) < 1:
        raise RuntimeError("the planner returned no itineraries")
    _dedupe(out, b, lists)
    return out


def _gem(parts: list[dict], timeout: float = 300, tries: int = 2) -> Any:
    """Gemini with one retry on a network/read timeout (long JSON answers occasionally stall)."""
    for i in range(tries):
        try:
            return _gemini(parts, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError):
            if i == tries - 1:
                raise RuntimeError("the planning model timed out twice; try again in a minute")


_FRAME_SCHEMA = """{"title": str, "summary": str (3-4 sentences), "highlights": [str], "weather_summary": str,
 "attire": {"summary": str, "pack": [str]}, "transport": {"recommended": str, "notes": [str]},
 "itineraries": [exactly 3: {"id": "A|B|C", "name": str (2-4 words), "style": str, "summary": str,
                  "hotel": str (EXACT name from HOTELS, a different hotel for each), "hotel_why": str}]}"""

_DAYS_SCHEMA = """{"est_total": str (whole trip for the travellers), "days": [ one per calendar day, start to end
 inclusive: {"date": "YYYY-MM-DD", "title": str, "items": [{"time": "HH:MM", "end": "HH:MM",
 "type": "breakfast|lunch|dinner|activity|event|transit|hotel|free|nightlife", "title": str,
 "place": str (EXACT name from THIS itinerary's lists, or "" for transit/free), "detail": str (1-2 sentences),
 "cost": str}]}]}"""


def _compose_parallel(b: dict, rules: str, head: str, lists: dict) -> dict:
    """New plans: one small call frames the trip (3 concepts + hotels), then the three hour-by-hour itineraries are
    written IN PARALLEL, each from its own share of the restaurants/sights/bars/events. Splitting the pools makes
    the options different by construction, and three short answers finish far sooner than one huge one."""
    frame = _gem([{"text": f"{rules}\n\n{head}\nHOTELS: {json.dumps(lists['HOTELS'])}\n"
                           f"SIGHTS (names only): {json.dumps([s.get('name') for s in lists['SIGHTS']])}\n"
                           f"EVENTS: {json.dumps(lists['EVENTS'])}\n\nFrame the trip: overview plus three distinct "
                           f"itinerary concepts (A classic, B leaning hardest into the themes, C slower / better value)."
                           f"\nReturn JSON only:\n{_FRAME_SCHEMA}"}], timeout=180)
    concepts = (frame or {}).get("itineraries") or []
    if len(concepts) < 3:
        raise RuntimeError("the planner returned no itinerary concepts")
    keep = {_norm(m) for m in b.get("must_do") or []}
    iconic = lists["SIGHTS"][:2]  # the two top-ranked sights may appear in every option

    def share(xs: list, i: int, shared: list | None = None) -> list:
        own = [x for k, x in enumerate(xs) if k % 3 == i or _norm(x.get("name", "")) in keep]
        return (shared or []) + [x for x in own if x not in (shared or [])]
    def one(i: int, c: dict) -> dict:
        pools = {"RESTAURANTS": share(lists["RESTAURANTS"], i), "SIGHTS": share(lists["SIGHTS"][2:], i, iconic),
                 "NIGHTLIFE": share(lists["NIGHTLIFE"], i), "EVENTS": share(lists["EVENTS"], i)}
        txt = (f"{rules}\n\n{head}\nTHIS ITINERARY: {json.dumps(c)} (stay at its hotel the whole trip)\n"
               + "\n".join(f"{k} (use ONLY these for this itinerary): {json.dumps(v)}" for k, v in pools.items())
               + f"\n\nWrite itinerary {c.get('id')} hour by hour.\nReturn JSON only:\n{_DAYS_SCHEMA}")
        return _gem([{"text": txt}], timeout=300) or {}
    with ThreadPoolExecutor(3) as ex:
        parts = list(ex.map(lambda ic: one(*ic), enumerate(concepts[:3])))
    its = []
    for c, p in zip(concepts[:3], parts):
        if not p.get("days"):
            raise RuntimeError(f"itinerary {c.get('id')} came back empty")
        its.append({**c, "est_total": p.get("est_total", ""), "days": p["days"]})
    return {**{k: frame.get(k) for k in ("title", "summary", "highlights", "weather_summary", "attire", "transport")},
            "itineraries": its}


def overlaps(plan: dict, must: list[str] | None = None) -> dict[str, list[str]]:
    """Places used by more than one itinerary (must-do items excluded) -> the itinerary ids that use them."""
    keep = {_norm(m) for m in must or []}
    seen: dict[str, set] = {}
    names: dict[str, str] = {}
    for it in plan.get("itineraries") or []:
        for day in it.get("days") or []:
            for x in day.get("items") or []:
                n = _norm(x.get("place", ""))
                if not n or n == _norm(it.get("hotel", "")) or x.get("type") in ("transit", "hotel"):
                    continue
                if any(k and (k in n or n in k) for k in keep):
                    continue
                seen.setdefault(n, set()).add(it.get("id"))
                names.setdefault(n, x.get("place", ""))
    return {names[n]: sorted(ids) for n, ids in seen.items() if len(ids) > 1}


def _dedupe(plan: dict, b: dict, lists: dict) -> None:
    """Code check after composing: if itineraries still share restaurants or activities, one repair pass swaps the
    repeats out of the later itineraries for unused places from the same lists (best effort)."""
    dup = overlaps(plan, b.get("must_do"))
    if len(dup) <= 2:  # a couple of iconic must-sees shared is fine
        return
    used = {_norm(x.get("place", "")) for it in plan["itineraries"] for d in it.get("days") or [] for x in d.get("items") or []}
    spare = {k: [p for p in v if _norm(p.get("name", "")) not in used] for k, v in lists.items() if k != "HOTELS"}
    try:
        fix = _gemini([{"text": f"""These three trip itineraries repeat the same places across itineraries:
{json.dumps(dup)}  (place -> itineraries using it)
Keep each repeated place in the FIRST itinerary listed and replace it in the others with an UNUSED place of the same
kind (restaurant for a meal, sight/event for an activity) from SPARE, fitting the same time slot, area and day.
Events must stay on their real dates. Keep everything else identical (times, hotels, order).
SPARE: {json.dumps(spare)}
ITINERARIES: {json.dumps(plan['itineraries'])}
Return JSON only: {{"itineraries": [...same schema, all 3...]}}"""}], timeout=240)
    except Exception:
        return
    its = (fix or {}).get("itineraries") or []
    if len(its) == len(plan["itineraries"]) and len(overlaps({"itineraries": its}, b.get("must_do"))) < len(dup):
        plan["itineraries"] = its


def _norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def _enrich(plan: dict, g: dict) -> None:
    """Attach photos / ratings / map links for every place the composer used (code, not the model)."""
    idx: dict[str, dict] = {}
    for k in ("hotels", "sights", "dining", "nightlife"):
        for p in g[k]:
            idx.setdefault(_norm(p["name"]), {**p, "list": k})
    for it in plan.get("itineraries") or []:
        h = idx.get(_norm(it.get("hotel", "")))
        it["hotel_info"] = {k: h.get(k) for k in ("name", "photo", "rating", "short_address", "maps_url",
                                                   "est_nightly", "price", "live")} if h else None
        for day in it.get("days") or []:
            for item in day.get("items") or []:
                p = idx.get(_norm(item.get("place", "")))
                if p:
                    item["photo"], item["maps_url"] = p.get("photo"), p.get("maps_url")
                    item["rating"], item["address"] = p.get("rating"), p.get("short_address")


def _assemble(tid: str, request: str, b: dict, g: dict, plan: dict, history: list, chosen: int | None) -> dict:
    _enrich(plan, g)
    r = g["research"]
    return {
        "id": tid, "created": time.time(), "request": request, "brief": b, "status": "ready",
        "title": plan.get("title") or b.get("title") or b["destination"], "summary": plan.get("summary", ""),
        "highlights": plan.get("highlights") or [], "weather": {**g["weather"], "summary": plan.get("weather_summary", "")},
        "attire": plan.get("attire") or {}, "transport": {**(plan.get("transport") or {}),
                                                          "getting_there": r.get("getting_there") or [],
                                                          "flights": r.get("flights") or [],
                                                          "getting_around": r.get("getting_around") or [],
                                                          "car_rentals": r.get("car_rentals") or [],
                                                          "live_flights": g.get("live_flights"),
                                                          "live_cars": g.get("live_cars")},
        "hotels_live_at": g.get("hotels_live_at"),
        "hotels": g["hotels"], "sights": g["sights"], "dining": g["dining"], "nightlife": g["nightlife"],
        "events": r.get("events") or [], "tips": r.get("tips") or [], "itineraries": plan["itineraries"][:3],
        "chosen": chosen, "history": history, "_gathered": g,
    }


# ---------------------------------------------------------------- public API

def trip_plan(request: str, trip_id: str = "", announce: bool = False) -> dict:
    """Plan a trip from a free-text description. Records a `trip` card. trip_id lets the HUD poll progress."""
    request = (request or "").strip()
    if len(request) < 4:
        raise ValueError("describe the trip (where, when, who, style)")
    tid = trip_id if re.fullmatch(r"[a-z0-9-]{4,40}", trip_id or "") else uuid.uuid4().hex[:10]
    try:
        _step(tid, "Reading your request")
        b = _brief(request)
        _step(tid, f"{b['destination']}, {b['start_date']} to {b['end_date']}")
        g = _gather(tid, b)
        _step(tid, "Drafting three hour-by-hour itineraries")
        plan = _compose(b, g)
        trip = _save(_assemble(tid, request, b, g, plan, [{"at": time.time(), "change": "Planned: " + request}], None))
        _step(tid, "Done", done=True)
    except Exception as e:
        _step(tid, "", done=True, error=str(e)[:200])
        if announce:
            store.record_result("trip_plan", None, {"request": request}, {
                "kind": "trip", "key": f"trip:{tid}", "id": tid, "status": "error", "request": request,
                "error": str(e)[:200], "announce": "I couldn't finish planning that trip, sir. The details are on screen."})
        raise
    if announce:
        trip = {**trip, "announce": f"Your {trip['title']} plans are ready, sir. Three itineraries are on screen."}
    _publish(trip, "trip_plan", {"request": request})
    return _summary(trip)


def _summary(trip: dict) -> dict:
    return {"trip_id": trip["id"], "title": trip["title"], "dates": f"{trip['brief']['start_date']} to {trip['brief']['end_date']}",
            "itineraries": [{"id": i.get("id"), "name": i.get("name"), "hotel": i.get("hotel"), "est_total": i.get("est_total")}
                            for i in trip["itineraries"]],
            "hotels_available": [h["name"] for h in trip["hotels"]], "events": [e.get("name") for e in trip["events"][:6]],
            "chosen": trip.get("chosen"), "last_change": (trip.get("history") or [{}])[-1].get("change"),
            "shown": "the trip planner card is on screen; summarize the three options in two or three sentences"}


def trip_revise(instruction: str, trip_id: str = "", announce: bool = False) -> dict:
    """Change an existing plan by description: swap hotels, add a theme, move a day, change dates or destination."""
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("say what to change")
    trip = _load(trip_id)
    tid = trip["id"]
    _reset(tid)
    try:
        _step(tid, "Reading the change")
        b0 = trip["brief"]
        b = _brief(instruction, prior=b0)
        g = trip.get("_gathered") or {}
        moved = (_norm(b["destination"]) != _norm(b0["destination"]) or b["start_date"] != b0["start_date"]
                 or b["end_date"] != b0["end_date"])
        if moved or not g:
            _step(tid, "New destination or dates: researching again")
            g = _gather(tid, b)
            plan = _compose(b, g)
        else:
            extra = _gemini([{"text": f"A traveller is changing a trip plan to {b0['destination']}. Request: "
                              f"{instruction}\nKnown hotels: {[h['name'] for h in g['hotels']]}\nDoes the request need "
                              f"places that are NOT in the plan's lists (a specific hotel, restaurant or attraction not "
                              f"listed, or a new kind of place)? Return JSON {{\"searches\": [{{\"list\": \"hotels|sights|"
                              f"dining|nightlife\", \"query\": str}}]}} with at most 3 searches, or an empty list."}])
            for s in (extra or {}).get("searches") or []:
                lst = s.get("list") if s.get("list") in ("hotels", "sights", "dining", "nightlife") else "sights"
                _step(tid, f"Looking up {s.get('query')}")
                have = {_norm(x["name"]) for x in g[lst]}
                g[lst] = g[lst] + [p for p in _places(f"{s.get('query')} {b['destination']}", 6) if _norm(p["name"]) not in have]
            _step(tid, "Rebuilding the itineraries")
            plan = _compose(b, g, instruction, current=trip)
        hist = (trip.get("history") or []) + [{"at": time.time(), "change": plan.get("change_summary") or instruction}]
        new = _assemble(tid, trip["request"], b, g, plan, hist, trip.get("chosen"))
        new["created"] = trip.get("created", time.time())
        trip = _save(new)
        _step(tid, "Done", done=True)
    except Exception as e:
        _step(tid, "", done=True, error=str(e)[:200])
        if announce:
            store.record_result("trip_revise", None, {"instruction": instruction}, {
                **_card(trip), "status": "ready", "revise_error": str(e)[:200],
                "announce": "I couldn't apply that change, sir. The plan is unchanged."})
        raise
    if announce:
        trip = {**trip, "announce": f"Done, sir. {(trip.get('history') or [{}])[-1].get('change', '')}"}
    _publish(trip, "trip_revise", {"instruction": instruction})
    return _summary(trip)


def trip_get(trip_id: str = "") -> dict:
    return _card(_load(trip_id))


def trip_list() -> dict:
    out = []
    for f in sorted(DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:20] if DIR.exists() else []:
        try:
            t = json.loads(f.read_text())
            out.append({"id": t["id"], "title": t.get("title"), "dates": f"{t['brief']['start_date']} – {t['brief']['end_date']}",
                        "updated": t.get("updated")})
        except Exception:
            continue
    return {"trips": out}


def trip_choose(trip_id: str, index: int) -> dict:
    trip = _load(trip_id)
    i = int(index)
    trip["chosen"] = i if 0 <= i < len(trip["itineraries"]) else None
    return _card(_save(trip))


def trip_undo(trip_id: str) -> dict:
    """Back to the previous version (click-only)."""
    p = _path(trip_id)
    data = json.loads(p.read_text())
    vers = data.get("versions") or []
    if len(vers) < 2:
        raise ValueError("nothing to undo")
    prev = {**vers[-2], "versions": vers[:-1]}
    p.write_text(json.dumps(prev, default=str))
    return _card(prev)


# ---------------------------------------------------------------- export

def _hm(s: str | None) -> str:
    try:
        return dt.datetime.fromisoformat(s or "").strftime("%a %-I:%M %p")
    except ValueError:
        return s or ""


def _day(s: str) -> str:
    try:
        return dt.date.fromisoformat(s).strftime("%A, %B %-d")
    except ValueError:
        return s


def _html(trip: dict, idx: int) -> str:
    it = trip["itineraries"][idx]
    e = html.escape
    b = trip["brief"]
    wx = trip.get("weather") or {}
    days = "".join(
        f"<section class=day><h2>{e(_day(d.get('date', '')))}"
        f" <small>{e(d.get('title', ''))}</small></h2><table>"
        + "".join(f"<tr class='t-{e(x.get('type', ''))}'><td class=tm>{e(x.get('time', ''))}"
                  f"{'–' + e(x['end']) if x.get('end') else ''}</td><td><b>{e(x.get('title', ''))}</b>"
                  f"{' · <i>' + e(x['place']) + '</i>' if x.get('place') else ''}<br><span>{e(x.get('detail', ''))}</span></td>"
                  f"<td class=c>{e(x.get('cost', ''))}</td></tr>" for x in d.get("items") or [])
        + "</table></section>" for d in it.get("days") or [])
    hotels = "".join(f"<li><b>{e(h['name'])}</b> — {e(h.get('est_nightly') or h.get('price') or '')} "
                     f"{'★' + str(h['rating']) if h.get('rating') else ''} <span>{e(h.get('short_address') or '')}</span></li>"
                     for h in trip.get("hotels", [])[:8])
    events = "".join(f"<li><b>{e(x.get('name', ''))}</b> — {e(str(x.get('date', '')))} {e(x.get('time') or '')} "
                     f"<span>{e(x.get('venue') or '')}</span></li>" for x in trip.get("events", [])[:10])
    t = trip.get("transport") or {}
    there = "".join(f"<li><b>{e(x.get('mode', '').title())}</b>: {e(x.get('summary', ''))} "
                    f"({e(x.get('duration', ''))}, {e(x.get('est_cost', ''))})</li>" for x in t.get("getting_there") or [])
    pack = "".join(f"<li>{e(x)}</li>" for x in (trip.get("attire") or {}).get("pack") or [])
    lf = t.get("live_flights") or {}
    fares = ("<b>Live fares (Duffel, at planning time)</b><ul>" + "".join(
        f"<li><b>{e(o.get('airline') or '')}</b> {e(o.get('price') or '')} total ({o.get('per_person')}/person) · "
        f"{e((o.get('out') or {}).get('origin') or '')}→{e((o.get('out') or {}).get('destination') or '')} "
        f"{e(_hm((o.get('out') or {}).get('depart')))}, {(o.get('out') or {}).get('stops', 0)} stop(s)"
        f"{' · refundable' if o.get('refundable') else ''}</li>" for o in (lf.get("offers") or [])[:5]) + "</ul>") if lf else ""
    wdays = "".join(f"<td><b>{e(d['date'][5:])}</b><br>{d.get('hi')}°/{d.get('lo')}°<br>{e(str(d.get('condition') or ''))}</td>"
                    for d in wx.get("days") or [])
    return f"""<!doctype html><html><head><meta charset=utf-8><style>
@page {{ size: Letter; margin: 14mm 13mm; }}
body {{ font: 10.5pt/1.45 -apple-system, Helvetica, Arial, sans-serif; color: #18202a; }}
h1 {{ font-size: 22pt; margin: 0 0 2px; color: #0b3a5a; }} .sub {{ color: #4b6475; margin-bottom: 10px; }}
h2 {{ font-size: 13pt; border-bottom: 2px solid #0e7fb0; padding-bottom: 3px; margin: 16px 0 6px; color: #0b3a5a; }}
h2 small {{ font-weight: 400; color: #4b6475; font-size: 10pt; }} h3 {{ font-size: 11pt; margin: 12px 0 4px; color: #0e7fb0; }}
table {{ width: 100%; border-collapse: collapse; }} td {{ vertical-align: top; padding: 4px 6px; border-bottom: 1px solid #e3eaef; }}
td.tm {{ width: 82px; white-space: nowrap; color: #0e7fb0; font-weight: 600; }} td.c {{ width: 70px; text-align: right; color: #4b6475; }}
td span, li span {{ color: #4b6475; }} tr.t-breakfast td.tm, tr.t-lunch td.tm, tr.t-dinner td.tm {{ color: #b45309; }}
.box {{ background: #f2f7fa; border-radius: 8px; padding: 8px 12px; margin: 8px 0; }} ul {{ margin: 4px 0; padding-left: 18px; }}
.day {{ page-break-inside: auto; }} .wx td {{ text-align: center; border: 0; font-size: 9pt; }}
</style></head><body>
<h1>{e(trip['title'])}</h1>
<div class=sub>{e(b['destination'])} · {e(b['start_date'])} → {e(b['end_date'])} · {b.get('adults', 2)} adults ·
Itinerary {e(it.get('id', ''))}: <b>{e(it.get('name', ''))}</b> · est. {e(it.get('est_total', ''))}</div>
<div class=box>{e(it.get('summary') or trip.get('summary', ''))}<br><b>Hotel:</b> {e(it.get('hotel', ''))} — {e(it.get('hotel_why', ''))}</div>
<h3>Weather ({e(wx.get('source', ''))})</h3><div>{e(wx.get('summary', ''))}</div>{'<table class=wx><tr>' + wdays + '</tr></table>' if wdays else ''}
<h3>What to wear</h3><div>{e((trip.get('attire') or {}).get('summary', ''))}</div><ul>{pack}</ul>
<h3>Getting there</h3><div>{e(t.get('recommended', ''))}</div><ul>{there}</ul>{fares}
{days}
<h2>Other hotels</h2><ul>{hotels}</ul>
{'<h2>Events during your stay</h2><ul>' + events + '</ul>' if events else ''}
<h2>Tips</h2><ul>{''.join(f'<li>{e(x)}</li>' for x in trip.get('tips', []))}</ul>
<p style="color:#8aa;font-size:8pt;margin-top:16px">Prices are estimates from public listings at planning time. Planned by J.A.R.V.I.S.</p>
</body></html>"""


def _pdf(trip: dict, idx: int) -> Path:
    out = store.STATE_DIR / "trips" / "pdf"
    out.mkdir(parents=True, exist_ok=True)
    src = out / f"{trip['id']}-{idx}.html"
    src.write_text(_html(trip, idx))
    dst = out / f"{trip['id']}-{idx}.pdf"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={dst}",
                    src.as_uri()], capture_output=True, timeout=90)
    if not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError("PDF rendering failed")
    return dst


def _fname(trip: dict, idx: int) -> str:
    it = trip["itineraries"][idx]
    base = re.sub(r"[^\w\- ]", "", f"{trip['title']} - {it.get('name', it.get('id', ''))}").strip()[:80]
    return f"{base}.pdf"


def trip_export(trip_id: str, index: int, to: str = "local", account: str = "personal") -> dict:
    """Click-only: save one itinerary as a PDF to ~/Downloads (never overwrites) or upload it to Google Drive."""
    trip = _load(trip_id)
    idx = int(index)
    if not 0 <= idx < len(trip["itineraries"]):
        raise ValueError("no such itinerary")
    pdf = _pdf(trip, idx)
    name = _fname(trip, idx)
    if to == "drive":
        from googleapiclient.http import MediaFileUpload
        from .accounts import service
        f = service("drive", account).files().create(
            body={"name": name, "mimeType": "application/pdf"},
            media_body=MediaFileUpload(str(pdf), mimetype="application/pdf"), fields="id,webViewLink").execute()
        store.audit({"kind": "trip_export", "source": "trip_click", "to": "drive", "account": account, "file_id": f["id"]})
        return {"to": "drive", "name": name, "url": f.get("webViewLink"), "file_id": f["id"]}
    dl = Path.home() / "Downloads"
    dst, n = dl / name, 1
    while dst.exists():
        n += 1
        dst = dl / name.replace(".pdf", f" ({n}).pdf")
    dst.write_bytes(pdf.read_bytes())
    store.audit({"kind": "trip_export", "source": "trip_click", "to": "local", "path": str(dst)})
    return {"to": "local", "name": dst.name, "path": str(dst)}


def trip_plan_async(request: str, trip_id: str) -> dict:
    """HUD path: start planning in the background and return at once; the card polls trip_progress/trip_get."""
    threading.Thread(target=lambda: _quiet(trip_plan, request, trip_id), daemon=True).start()
    return {"trip_id": trip_id, "started": True}


def trip_revise_async(instruction: str, trip_id: str) -> dict:
    threading.Thread(target=lambda: _quiet(trip_revise, instruction, trip_id), daemon=True).start()
    return {"trip_id": trip_id, "started": True}


# ---- voice path (MCP): show a live "planning" card now, finish in the background, announce when ready

def start_plan(request: str) -> dict:
    request = (request or "").strip()
    if len(request) < 4:
        raise ValueError("describe the trip (where, when, who, style)")
    tid = uuid.uuid4().hex[:10]
    _step(tid, "Reading your request")
    store.record_result("trip_plan", None, {"request": request},
                        {"kind": "trip", "key": f"trip:{tid}", "id": tid, "status": "planning", "request": request})
    threading.Thread(target=lambda: _swallow(trip_plan, request, tid, True), daemon=True).start()
    return {"trip_id": tid, "status": "planning",
            "shown": "a live TRIP PLANNER card is on screen showing progress; it takes about two minutes. Say you're "
                     "on it in one short sentence. Don't describe the trip yet; the finished plan is announced."}


def start_revise(instruction: str, trip_id: str = "") -> dict:
    trip = _load(trip_id)
    _reset(trip["id"])
    _step(trip["id"], "Reading the change")
    store.record_result("trip_revise", None, {"instruction": instruction},
                        {**_card(trip), "status": "revising", "pending_change": instruction})
    threading.Thread(target=lambda: _swallow(trip_revise, instruction, trip["id"], True), daemon=True).start()
    return {"trip_id": trip["id"], "status": "revising",
            "shown": "the trip card shows the change in progress (about a minute). Acknowledge in one short sentence."}


def _swallow(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


def _quiet(fn, *a):
    try:
        with store.capture():  # the HUD patches its own card; don't also push a second one through the feed
            fn(*a)
    except Exception:
        pass


RPC = {"trip_refresh_flights": trip_refresh_flights, "trip_progress": trip_progress, "trip_get": trip_get, "trip_list": trip_list,
       "trip_plan_async": trip_plan_async, "trip_revise_async": trip_revise_async}
CLICK_OPS = {"trip_choose": trip_choose, "trip_undo": trip_undo, "trip_export": trip_export}
