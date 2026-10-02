"""Travel & dining: search and book flights, hotels, rental cars (Duffel) and restaurant tables (Resy).

Searching is read-only and renders rich offer displays. Booking NEVER happens from a tool call: *_book tools re-check
the live price / slot, then `store.propose(...)` a confirm card (price, details, cancellation policy). Only Stephen's
"Authorize" (click or "confirm") runs the executor below, through Core's execute_action.

Secrets/config (never in the repo): DUFFEL_ACCESS_TOKEN and RESY_AUTH_TOKEN in env or ~/.hermes/.env. The traveller
profile (legal name, date of birth, phone, email) lives in STATE_DIR/traveler.json, written only from what he states.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import store

DUFFEL = "https://api.duffel.com"
RESY = "https://api.resy.com"
RESY_PUBLIC_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"  # the key resy.com's own web app sends (public, not a secret)
PROFILE = store.STATE_DIR / "traveler.json"
HOME_AIRPORTS = ["LGA", "JFK", "EWR"]  # his order of preference
AIRPORT_PENALTY = {"LGA": 0.0, "JFK": 30.0, "EWR": 45.0}  # dollars of "inconvenience" when ranking
_local = threading.local()


# ------------------------------------------------------------------ config
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


def _duffel_token() -> str:
    t = _env("DUFFEL_ACCESS_TOKEN")
    if not t:
        raise RuntimeError("Flights, hotels and rental cars need a Duffel access token. Add DUFFEL_ACCESS_TOKEN to "
                           "~/.hermes/.env (Duffel dashboard > Developers > Access tokens; a duffel_test_ token books "
                           "against Duffel's test inventory, a live one books for real).")
    return t


def test_mode() -> bool:
    return _duffel_token().startswith("duffel_test")


def _duffel(method: str, path: str, body: dict | None = None, params: dict | None = None, timeout: float = 60) -> dict:
    h = {"Authorization": f"Bearer {_duffel_token()}", "Duffel-Version": "v2", "Accept": "application/json",
         "Content-Type": "application/json", "Accept-Encoding": "gzip"}
    r = httpx.request(method, DUFFEL + path, headers=h, json={"data": body} if body is not None else None,
                      params=params, timeout=timeout)
    if r.status_code >= 400:
        try:
            errs = r.json().get("errors") or []
            msg = "; ".join(e.get("message") or e.get("title") or "" for e in errs) or r.text[:300]
        except Exception:
            msg = r.text[:300]
        raise RuntimeError(f"Duffel {r.status_code}: {msg}")
    return r.json().get("data") or {}


def _resy_headers(auth: bool = False) -> dict:
    h = {"Authorization": f'ResyAPI api_key="{_env("RESY_API_KEY") or RESY_PUBLIC_KEY}"',
         "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0",
         "Origin": "https://resy.com", "Referer": "https://resy.com/", "X-Origin": "https://resy.com",
         "Accept": "application/json"}
    if auth:
        tok = _resy_token()
        h["X-Resy-Auth-Token"] = h["X-Resy-Universal-Auth"] = tok
    return h


_resy_auth: dict[str, Any] = {"token": "", "at": 0.0}


def _resy_token(force: bool = False) -> str:
    """A short-lived Resy auth token, minted from his long-lived login (RESY_REFRESH_TOKEN, the
    production_refresh_token cookie from signing in at resy.com) and cached ~20 minutes."""
    if not force and _resy_auth["token"] and time.time() - _resy_auth["at"] < 20 * 60:
        return _resy_auth["token"]
    refresh = _env("RESY_REFRESH_TOKEN")
    if not refresh:
        tok = _env("RESY_AUTH_TOKEN")  # a token pasted directly (expires within hours)
        if tok:
            return tok
        raise RuntimeError("Booking a Resy table needs his Resy login: sign in at resy.com once in the JARVIS browser "
                           "and save the production_refresh_token cookie as RESY_REFRESH_TOKEN in ~/.hermes/.env.")
    h = _resy_headers()
    r = httpx.post(f"{RESY}/3/auth/refresh", headers=h, cookies={"production_refresh_token": refresh}, timeout=20)
    tok = (r.json() if r.status_code == 200 else {}).get("token") or ""
    if not tok:
        raise RuntimeError(f"Resy login has expired ({r.status_code}); sign in at resy.com again in the JARVIS browser.")
    _resy_auth.update(token=tok, at=time.time())
    new = r.cookies.get("production_refresh_token")
    if new and new != refresh:  # Resy rotates the refresh token: keep the newest
        _save_env("RESY_REFRESH_TOKEN", new)
    return tok


def _save_env(name: str, value: str) -> None:
    f = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        s = f.read_text()
    except OSError:
        s = ""
    line = f"{name}={value}"
    s = re.sub(rf"^\s*(?:export\s+)?{name}\s*=.*$", line, s, flags=re.M) if re.search(
        rf"^\s*(?:export\s+)?{name}\s*=", s, re.M) else s.rstrip("\n") + "\n" + line + "\n"
    f.write_text(s)
    os.chmod(f, 0o600)


# ------------------------------------------------------------------ traveller profile
PROFILE_FIELDS = ("given_name", "family_name", "born_on", "gender", "title", "email", "phone_number")


def _profile() -> dict:
    try:
        return json.loads(PROFILE.read_text())
    except Exception:
        return {}


def traveler_profile_get() -> dict:
    p = _profile()
    missing = [k for k in PROFILE_FIELDS if not p.get(k)]
    shown = {k: (v if k not in ("born_on", "phone_number") else "on file") for k, v in p.items()}
    return {"profile": shown, "missing": missing,
            "note": "Ask him for any missing field before booking a flight; never guess it." if missing else ""}


def traveler_profile_set(given_name: str = "", family_name: str = "", born_on: str = "", gender: str = "",
                         title: str = "", email: str = "", phone_number: str = "") -> dict:
    """Save what he states (legal name as on his ID, DOB YYYY-MM-DD, gender m/f, title mr/ms/mrs/miss/dr, email, phone
    in +1XXXXXXXXXX form)."""
    p = _profile()
    new = {"given_name": given_name, "family_name": family_name, "born_on": born_on, "gender": gender[:1].lower(),
           "title": title.lower().rstrip("."), "email": email, "phone_number": re.sub(r"[^\d+]", "", phone_number)}
    if new["phone_number"] and not new["phone_number"].startswith("+"):
        new["phone_number"] = "+1" + new["phone_number"][-10:]
    if born_on:
        dt.date.fromisoformat(born_on)  # validate
    p.update({k: v for k, v in new.items() if v})
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(p, indent=1))
    os.chmod(PROFILE, 0o600)
    return traveler_profile_get()


def _need(*fields: str) -> dict:
    p = _profile()
    missing = [f for f in fields if not p.get(f)]
    if missing:
        raise ValueError("I need these details first (ask him, then traveler_profile_set): " + ", ".join(missing))
    return p


# ------------------------------------------------------------------ places / dates
def _where(location: str) -> dict:
    """'' = home. Otherwise Google Places text search -> {name, lat, lng}."""
    from .weather import _here
    if not (location or "").strip():
        with httpx.Client() as h:
            here = _here(h)
        return {"name": here.get("name", "home"), "lat": here["lat"], "lng": here["lon"]}
    from . import places as PL
    r = PL._http().post(f"{PL.API}/places:searchText", json={"textQuery": location, "pageSize": 1},
                        headers={"X-Goog-FieldMask": "places.displayName,places.location,places.formattedAddress"})
    ps = (r.json() if r.status_code == 200 else {}).get("places") or []
    if not ps:
        raise ValueError(f"Couldn't find '{location}'.")
    p = ps[0]
    return {"name": (p.get("displayName") or {}).get("text") or location, "address": p.get("formattedAddress", ""),
            "lat": p["location"]["latitude"], "lng": p["location"]["longitude"]}


def _date(s: str, default_days: int = 0) -> str:
    s = (s or "").strip()
    if not s:
        return (dt.date.today() + dt.timedelta(days=default_days)).isoformat()
    return dt.date.fromisoformat(s[:10]).isoformat()


def _iso_dur(s: str | None) -> str:
    """'PT5H40M' -> '5h 40m'."""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", s or "")
    if not m:
        return s or ""
    d, h, mi = (int(x or 0) for x in m.groups())
    h += d * 24
    return f"{h}h {mi:02d}m" if h else f"{mi}m"


def _money(amount: Any, cur: str | None = "USD") -> str:
    cur = cur or "USD"
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, cur + " ")
    return f"{sym}{a:,.2f}" if a % 1 else f"{sym}{a:,.0f}"


# ------------------------------------------------------------------ flights
def _airport(q: str) -> str:
    q = (q or "").strip()
    if re.fullmatch(r"[A-Za-z]{3}", q):
        return q.upper()
    sug = _duffel("GET", "/places/suggestions", params={"query": q})
    for s in sug if isinstance(sug, list) else []:
        if s.get("iata_code"):
            return s["iata_code"]
    raise ValueError(f"Couldn't find an airport for '{q}'.")


def _slice(s: dict) -> dict:
    segs = s.get("segments") or []
    first, last = segs[0], segs[-1]
    return {
        "origin": (s.get("origin") or {}).get("iata_code"), "destination": (s.get("destination") or {}).get("iata_code"),
        "origin_city": (s.get("origin") or {}).get("city_name"), "destination_city": (s.get("destination") or {}).get("city_name"),
        "depart": first.get("departing_at"), "arrive": last.get("arriving_at"), "duration": _iso_dur(s.get("duration")),
        "stops": len(segs) - 1, "via": [x["destination"]["iata_code"] for x in segs[:-1]],
        "flights": [f"{(x.get('marketing_carrier') or {}).get('iata_code', '')}{x.get('marketing_carrier_flight_number', '')}"
                    for x in segs],
        "aircraft": ((first.get("aircraft") or {}).get("name") or ""),
    }


def _offer(o: dict) -> dict:
    owner = o.get("owner") or {}
    cond = o.get("conditions") or {}
    bags = [b for p in ((o.get("slices") or [{}])[0].get("segments") or [{}])[0].get("passengers") or [] for b in p.get("baggages") or []]
    slices = [_slice(s) for s in o.get("slices") or []]
    origin = slices[0]["origin"] if slices else ""
    total = float(o.get("total_amount") or 0)
    return {
        "id": o["id"], "airline": owner.get("name"), "airline_code": owner.get("iata_code"),
        "logo": owner.get("logo_symbol_url") or owner.get("logo_lockup_url"), "total": total,
        "currency": o.get("total_currency"), "price": _money(total, o.get("total_currency")),
        "cabin": ((((o.get("slices") or [{}])[0].get("segments") or [{}])[0].get("passengers") or [{}])[0]
                  .get("cabin_class_marketing_name") or ""),
        "slices": slices, "expires_at": o.get("expires_at"),
        "refundable": bool((cond.get("refund_before_departure") or {}).get("allowed")),
        "changeable": bool((cond.get("change_before_departure") or {}).get("allowed")),
        "bags": ", ".join(f"{b.get('quantity')} {b.get('type', '').replace('_', ' ')}" for b in bags if b.get("quantity")),
        "score": total + AIRPORT_PENALTY.get(origin, 0) + 40 * sum(s["stops"] for s in slices),
    }


def flights_search(destination: str, depart_date: str, return_date: str = "", origin: str = "", adults: int = 1,
                   cabin: str = "economy", nonstop_only: bool = False, max_results: int = 8) -> dict:
    """Search flights. origin '' = his New York airports (LaGuardia preferred, then JFK, then Newark)."""
    dest = _airport(destination)
    origins = [_airport(origin)] if origin else HOME_AIRPORTS
    pax = [{"type": "adult"} for _ in range(max(1, min(int(adults or 1), 9)))]
    dep, ret = _date(depart_date), _date(return_date) if return_date else ""
    offers: list[dict] = []
    for org in origins:  # one search per home airport (a city code would hide which airport he flies from)
        slices = [{"origin": org, "destination": dest, "departure_date": dep}]
        if ret:
            slices.append({"origin": dest, "destination": org, "departure_date": ret})
        body = {"slices": slices, "passengers": pax, "cabin_class": cabin if cabin in
                ("economy", "premium_economy", "business", "first") else "economy"}
        if nonstop_only:
            body["max_connections"] = 0
        try:
            res = _duffel("POST", "/air/offer_requests", body, params={"return_offers": "true"}, timeout=90)
        except RuntimeError as e:
            if len(origins) == 1:
                raise
            store.audit({"kind": "travel_search_error", "origin": org, "error": str(e)[:200]})
            continue
        offers += [_offer(o) for o in (res.get("offers") or [])[:60]]
    if nonstop_only:
        offers = [o for o in offers if all(s["stops"] == 0 for s in o["slices"])]
    offers.sort(key=lambda o: o["score"])
    seen, top = set(), []
    for o in offers:  # drop near-duplicates (same flights, different fare brand) keeping the cheapest
        k = (o["airline_code"], tuple(f for s in o["slices"] for f in s["flights"]))
        if k not in seen:
            seen.add(k)
            top.append(o)
    top = top[:max(1, min(int(max_results or 8), 12))]
    cheapest = min((o["total"] for o in offers), default=None)
    res = {"kind": "flights", "key": f"travel:flights:{'-'.join(origins)}-{dest}-{dep}-{ret}", "destination": dest,
           "origins": origins, "depart_date": dep, "return_date": ret, "adults": len(pax), "cabin": cabin,
           "offers": top, "count": len(offers), "cheapest": cheapest, "test_mode": test_mode()}
    store.record_result("flights_search", None, {"destination": dest, "depart_date": dep}, res)
    return {**{k: res[k] for k in ("destination", "origins", "depart_date", "return_date", "count", "test_mode")},
            "best": [{"n": i + 1, "offer_id": o["id"], "airline": o["airline"], "price": o["price"],
                      "from": o["slices"][0]["origin"], "depart": o["slices"][0]["depart"],
                      "stops": [s["stops"] for s in o["slices"]], "duration": [s["duration"] for s in o["slices"]],
                      "refundable": o["refundable"]} for i, o in enumerate(top[:6])],
            "shown": "offers are on screen; recommend one (cheapest sensible, LaGuardia preferred) in a sentence"}


def flight_book(offer_id: str) -> dict:
    """Re-price the offer and put a confirm card up. Books only when he authorizes."""
    p = _need("given_name", "family_name", "born_on", "gender", "title", "email", "phone_number")
    o = _duffel("GET", f"/air/offers/{offer_id}", params={"return_available_services": "false"})
    off = _offer(o)
    pax = [x["id"] for x in o.get("passengers") or []]
    if len(pax) > 1:
        raise ValueError("Booking for more than one traveller needs each person's details; I only have his profile.")
    lines = []
    for s in off["slices"]:
        lines.append([f"{s['origin']} → {s['destination']}",
                      f"{_fmt_dt(s['depart'])} · {s['duration']} · {'nonstop' if not s['stops'] else str(s['stops']) + ' stop'}"
                      f" · {' '.join(s['flights'])}"])
    lines.append(["Passenger", f"{p['given_name']} {p['family_name']}"])
    if off["bags"]:
        lines.append(["Bags", off["bags"]])
    policy = ("Refundable before departure" if off["refundable"] else "Non-refundable") + \
             (", changes allowed (fees may apply)" if off["changeable"] else ", no changes")
    params = {"offer_id": offer_id, "passenger_id": pax[0], "amount": o["total_amount"], "currency": o["total_currency"]}
    preview = {"type": "booking", "category": "flight", "title": f"{off['airline']} · {off['slices'][0]['origin']} → "
               f"{off['slices'][0]['destination']}", "logo": off["logo"], "lines": lines,
               "total": off["price"], "policy": policy, "test_mode": test_mode(), "expires_at": off["expires_at"]}
    return store.propose("flight_book", "travel", params,
                         f"Book {off['airline']} {off['slices'][0]['origin']}→{off['slices'][0]['destination']} "
                         f"for {off['price']}", preview)


def _exec_flight(account: str, offer_id: str, passenger_id: str, amount: str, currency: str) -> dict:
    p = _need("given_name", "family_name", "born_on", "gender", "title", "email", "phone_number")
    order = _duffel("POST", "/air/orders", {
        "type": "instant", "selected_offers": [offer_id],
        "passengers": [{"id": passenger_id, "given_name": p["given_name"], "family_name": p["family_name"],
                        "born_on": p["born_on"], "gender": p["gender"], "title": p["title"], "email": p["email"],
                        "phone_number": p["phone_number"]}],
        "payments": [{"type": "balance", "amount": amount, "currency": currency}]}, timeout=120)
    out = {"category": "flight", "reference": order.get("booking_reference"), "order_id": order.get("id"),
           "total": _money(order.get("total_amount"), order.get("total_currency")),
           "slices": [_slice(s) for s in order.get("slices") or []], "test_mode": test_mode()}
    store.record_result("booking", None, {"category": "flight"}, {"key": f"booking:{order.get('id')}", **out})
    return out


def _fmt_dt(iso: str) -> str:
    try:
        d = dt.datetime.fromisoformat(iso)
        return d.strftime("%a %b %-d, %-I:%M %p")
    except Exception:
        return iso or ""


# ------------------------------------------------------------------ hotels (Duffel Stays)
def hotels_search(location: str, check_in: str, check_out: str, guests: int = 1, rooms: int = 1,
                  radius_km: float = 5, free_cancellation_only: bool = False, max_results: int = 8) -> dict:
    w = _where(location)
    ci, co = _date(check_in), _date(check_out, 1)
    body = {"rooms": max(1, int(rooms or 1)), "guests": [{"type": "adult"} for _ in range(max(1, int(guests or 1)))],
            "check_in_date": ci, "check_out_date": co, "free_cancellation_only": bool(free_cancellation_only),
            "location": {"radius": radius_km, "geographic_coordinates": {"latitude": w["lat"], "longitude": w["lng"]}}}
    res = _duffel("POST", "/stays/search", body, timeout=90)
    nights = max(1, (dt.date.fromisoformat(co) - dt.date.fromisoformat(ci)).days)
    hotels = []
    for r in res.get("results") or []:
        a = r.get("accommodation") or {}
        loc = (a.get("location") or {})
        addr = loc.get("address") or {}
        geo = loc.get("geographic_coordinates") or {}
        total = float(r.get("cheapest_rate_total_amount") or 0)
        hotels.append({
            "id": r["id"], "name": a.get("name"), "stars": a.get("rating"), "review_score": a.get("review_score"),
            "reviews": a.get("review_count"), "photo": ((a.get("photos") or [{}])[0]).get("url"),
            "address": ", ".join(x for x in (addr.get("line_one"), addr.get("city_name")) if x),
            "total": total, "per_night": round(total / nights, 2), "currency": r.get("cheapest_rate_currency"),
            "price": _money(total, r.get("cheapest_rate_currency")),
            "nightly": _money(total / nights, r.get("cheapest_rate_currency")),
            "distance_km": _km(w, geo), "chain": (a.get("chain") or {}).get("name"),
            "amenities": [x.get("description") for x in (a.get("amenities") or [])[:6]],
        })
    hotels.sort(key=lambda h: (-(h["review_score"] or 0) * 40 + h["per_night"]))
    top = hotels[:max(1, min(int(max_results or 8), 12))]
    out = {"kind": "hotels", "key": f"travel:hotels:{w['name']}-{ci}-{co}", "near": w["name"], "check_in": ci,
           "check_out": co, "nights": nights, "guests": len(body["guests"]), "hotels": top, "count": len(hotels),
           "test_mode": test_mode()}
    store.record_result("hotels_search", None, {"location": location, "check_in": ci}, out)
    return {"near": w["name"], "nights": nights, "count": len(hotels), "test_mode": out["test_mode"],
            "best": [{"n": i + 1, "search_result_id": h["id"], "name": h["name"], "nightly": h["nightly"],
                      "total": h["price"], "review_score": h["review_score"], "stars": h["stars"],
                      "distance_km": h["distance_km"]} for i, h in enumerate(top[:6])],
            "shown": "hotels are on screen; recommend one in a sentence"}


def _km(a: dict, g: dict) -> float | None:
    import math
    if not g.get("latitude"):
        return None
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], g["latitude"], g["longitude"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)), 1)


def hotel_book(search_result_id: str, rate_id: str = "") -> dict:
    p = _need("given_name", "family_name", "email", "phone_number")
    sr = _duffel("POST", f"/stays/search_results/{search_result_id}/actions/fetch_all_rates", timeout=90)
    acc = sr.get("accommodation") or {}
    rates = [(room, rt) for room in acc.get("rooms") or [] for rt in room.get("rates") or []]
    if not rates:
        raise RuntimeError("No bookable rooms are left at that hotel for those dates.")
    room, rate = next(((r, t) for r, t in rates if t["id"] == rate_id), None) or \
        min(rates, key=lambda x: float(x[1].get("total_amount") or 1e12))
    q = _duffel("POST", "/stays/quotes", {"rate_id": rate["id"]})
    cur = q.get("total_currency")
    tl = rate.get("cancellation_timeline") or []
    if tl:
        policy = "Free cancellation until " + _fmt_dt(tl[0].get("before", "")) + \
                 (f" (refund {_money(tl[0].get('refund_amount'), cur)})" if tl[0].get("refund_amount") else "")
    else:
        policy = "Non-refundable"
    nights = (dt.date.fromisoformat(q["check_out_date"]) - dt.date.fromisoformat(q["check_in_date"])).days
    lines = [["Dates", f"{q['check_in_date']} → {q['check_out_date']} · {nights} night{'s' if nights != 1 else ''}"],
             ["Room", f"{room.get('name', '')} · {rate.get('board_type', '').replace('_', ' ')}"],
             ["Guest", f"{p['given_name']} {p['family_name']}"]]
    if q.get("due_at_accommodation_amount") and float(q["due_at_accommodation_amount"]):
        lines.append(["Due at hotel", _money(q["due_at_accommodation_amount"], q.get("due_at_accommodation_currency"))])
    preview = {"type": "booking", "category": "hotel", "title": acc.get("name") or "Hotel",
               "photo": ((acc.get("photos") or [{}])[0]).get("url"), "lines": lines,
               "total": _money(q.get("total_amount"), cur), "policy": policy, "test_mode": test_mode()}
    return store.propose("hotel_book", "travel", {"quote_id": q["id"]},
                         f"Book {acc.get('name')} for {preview['total']}", preview)


def _exec_hotel(account: str, quote_id: str) -> dict:
    p = _need("given_name", "family_name", "email", "phone_number")
    b = _duffel("POST", "/stays/bookings", {"quote_id": quote_id, "email": p["email"], "phone_number": p["phone_number"],
                                           "guests": [{"given_name": p["given_name"], "family_name": p["family_name"]}]},
                timeout=120)
    acc = b.get("accommodation") or {}
    out = {"category": "hotel", "reference": b.get("reference") or b.get("id"), "booking_id": b.get("id"),
           "name": acc.get("name"), "check_in": b.get("check_in_date"), "check_out": b.get("check_out_date"),
           "status": b.get("status"), "test_mode": test_mode()}
    store.record_result("booking", None, {"category": "hotel"}, {"key": f"booking:{b.get('id')}", **out})
    return out


# ------------------------------------------------------------------ rental cars (Duffel Cars)
def car_rentals_search(location: str, pickup_date: str, dropoff_date: str, pickup_time: str = "10:00",
                       dropoff_time: str = "10:00", driver_age: int = 0, max_results: int = 8) -> dict:
    """location: an airport ('LGA') or address; '' = near home."""
    loc = f"{location} airport" if re.fullmatch(r"[A-Za-z]{3}", (location or "").strip()) else location
    w = _where(loc)
    age = int(driver_age or _age() or 30)
    spot = {"radius": 10, "geographic_coordinates": {"latitude": w["lat"], "longitude": w["lng"]}}
    pu, do = _date(pickup_date), _date(dropoff_date, 1)
    res = _duffel("POST", "/cars/search", {"pickup_date": pu, "pickup_time": pickup_time, "pickup_location": spot,
                                            "dropoff_date": do, "dropoff_time": dropoff_time, "dropoff_location": spot,
                                            "driver": {"residence_country_code": "US", "age": age}}, timeout=90)
    cars = []
    for r in res.get("rates") or []:
        c, sup, pl = r.get("car") or {}, r.get("supplier") or {}, r.get("pickup_location") or {}
        total = float(r.get("total_amount") or 0)
        cars.append({"id": r["id"], "name": c.get("name"), "category": c.get("category"), "type": c.get("type"),
                     "seats": c.get("max_passengers"), "bags": c.get("baggage"), "transmission": c.get("transmission"),
                     "photo": ((c.get("images") or [{}])[0]).get("url"), "supplier": sup.get("name"),
                     "supplier_logo": sup.get("logo_url"), "pickup": pl.get("name"),
                     "total": total, "currency": r.get("total_currency"), "price": _money(total, r.get("total_currency")),
                     "payment_type": r.get("payment_type")})
    cars.sort(key=lambda c: c["total"])
    top = cars[:max(1, min(int(max_results or 8), 12))]
    days = max(1, (dt.date.fromisoformat(do) - dt.date.fromisoformat(pu)).days)
    out = {"kind": "car_rentals", "key": f"travel:cars:{w['name']}-{pu}-{do}", "near": w["name"], "pickup_date": pu,
           "dropoff_date": do, "days": days, "cars": top, "count": len(cars), "test_mode": test_mode()}
    store.record_result("car_rentals_search", None, {"location": location, "pickup_date": pu}, out)
    return {"near": w["name"], "days": days, "count": len(cars), "test_mode": out["test_mode"],
            "best": [{"n": i + 1, "rate_id": c["id"], "car": c["name"], "category": c["category"], "supplier": c["supplier"],
                      "total": c["price"]} for i, c in enumerate(top[:6])],
            "shown": "rental cars are on screen; recommend one in a sentence"}


def _age() -> int | None:
    b = _profile().get("born_on")
    if not b:
        return None
    d, t = dt.date.fromisoformat(b), dt.date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def car_rental_book(rate_id: str) -> dict:
    p = _need("given_name", "family_name", "born_on", "email", "phone_number")
    q = _duffel("POST", "/cars/quotes", {"rate_id": rate_id})
    c, sup = q.get("car") or {}, q.get("supplier") or {}
    cur = q.get("total_currency")
    conds = "; ".join(f"{x.get('title')}: {x.get('description') or x.get('text') or ''}".strip(": ")
                      for x in (q.get("conditions") or [])[:3])
    pay_label = {"prepaid": "Paid now", "postpaid": "Pay at the counter",
                 "guarantee": "Card guarantee, pay at counter"}.get(str(q.get("payment_type") or ""), q.get("payment_type") or "")
    lines = [["Car", f"{c.get('name', '')} or similar · {c.get('category', '')}"],
             ["Pick up", f"{q.get('pickup_date')} {q.get('pickup_time')} · {(q.get('pickup_location') or {}).get('name', '')}"],
             ["Drop off", f"{q.get('dropoff_date')} {q.get('dropoff_time')} · {(q.get('dropoff_location') or {}).get('name', '')}"],
             ["Driver", f"{p['given_name']} {p['family_name']}"],
             ["Payment", pay_label]]
    preview = {"type": "booking", "category": "car", "title": f"{sup.get('name', 'Rental')} · {c.get('name', '')}",
               "logo": sup.get("logo_url"), "photo": ((c.get("images") or [{}])[0]).get("url"), "lines": lines,
               "total": _money(q.get("total_amount"), cur), "policy": conds or "See supplier terms", "test_mode": test_mode()}
    return store.propose("car_rental_book", "travel", {"quote_id": q["id"]},
                         f"Rent a {c.get('name', 'car')} from {sup.get('name', '')} for {preview['total']}", preview)


def _exec_car(account: str, quote_id: str) -> dict:
    p = _need("given_name", "family_name", "born_on", "email", "phone_number")
    b = _duffel("POST", "/cars/bookings", {"quote_id": quote_id, "driver": {
        "given_name": p["given_name"], "family_name": p["family_name"], "date_of_birth": p["born_on"],
        "phone_number": p["phone_number"], "email": p["email"]}}, timeout=120)
    out = {"category": "car", "reference": b.get("reference") or b.get("id"), "booking_id": b.get("id"),
           "car": (b.get("car") or {}).get("name"), "pickup": f"{b.get('pickup_date')} {b.get('pickup_time')}",
           "total": _money(b.get("total_amount"), b.get("total_currency")),
           "test_mode": test_mode()}
    store.record_result("booking", None, {"category": "car"}, {"key": f"booking:{b.get('id')}", **out})
    return out


# ------------------------------------------------------------------ restaurants (Resy)
def _slots_near(slots: list[dict], want: str) -> list[dict]:
    """Slots sorted by closeness to the wanted time ('19:00'), each {time, label, type, token}."""
    try:
        wh, wm = map(int, (want or "19:00").split(":")[:2])
    except ValueError:
        wh, wm = 19, 0
    out, seen = [], set()
    for s in slots:
        start = (s.get("date") or {}).get("start", "")
        t = start[11:16]
        typ = (s.get("config") or {}).get("type") or ""
        if not t or t in seen:  # one button per time; the first seating type listed (usually the main room) wins
            continue
        seen.add(t)
        h, m = map(int, t.split(":"))
        out.append({"time": t, "label": dt.time(h, m).strftime("%-I:%M %p"), "type": typ,
                    "token": (s.get("config") or {}).get("token"), "off": abs((h * 60 + m) - (wh * 60 + wm))})
    out.sort(key=lambda x: (x["off"], x["time"]))
    return out


def restaurants_search(query: str = "", near: str = "", date: str = "", time: str = "19:00", party_size: int = 2,
                       max_results: int = 8) -> dict:
    """Resy tables near home (or `near`) for a date/party, with the open times closest to `time`."""
    w = _where(near)
    day = _date(date)
    party = max(1, min(int(party_size or 2), 20))
    body = {"geo": {"latitude": w["lat"], "longitude": w["lng"]}, "query": query or "", "types": ["venue"],
            "per_page": 20, "availability": True, "slot_filter": {"day": day, "party_size": party}}
    if not query:
        body["order_by"] = "distance"
    r = httpx.post(f"{RESY}/3/venuesearch/search", headers=_resy_headers(), json=body, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Resy search failed ({r.status_code})")
    rows = []
    for h in (r.json().get("search") or {}).get("hits") or []:
        slots = _slots_near((h.get("availability") or {}).get("slots") or [], time)
        if not slots:
            continue
        g = h.get("_geoloc") or {}
        rows.append({"venue_id": (h.get("id") or {}).get("resy"), "name": h.get("name"),
                     "cuisine": ", ".join(h.get("cuisine") or []), "neighborhood": h.get("neighborhood"),
                     "price": "$" * int(h.get("price_range_id") or 0),
                     "rating": round((h.get("rating") or {}).get("average") or 0, 1) or None,
                     "reviews": (h.get("rating") or {}).get("count"), "photo": (h.get("images") or [None])[0],
                     "url": f"https://resy.com/cities/{(h.get('location') or {}).get('url_slug', '')}/venues/{h.get('url_slug', '')}",
                     "distance_km": _km(w, {"latitude": g.get("lat"), "longitude": g.get("lng")}),
                     "slots": slots[:8], "best_off": slots[0]["off"]})
    if query:  # a named place: keep Resy's relevance order
        pass
    else:
        rows.sort(key=lambda x: (x["best_off"] > 60, -(x["rating"] or 0)))
    top = rows[:max(1, min(int(max_results or 8), 12))]
    out = {"kind": "restaurants", "key": f"travel:resy:{query}-{w['name']}-{day}-{party}", "near": w["name"],
           "date": day, "time": time, "party_size": party, "query": query, "restaurants": top}
    store.record_result("restaurants_search", None, {"query": query, "date": day}, out)
    return {"near": w["name"], "date": day, "party_size": party,
            "options": [{"venue_id": x["venue_id"], "name": x["name"], "cuisine": x["cuisine"], "rating": x["rating"],
                         "times": [s["label"] for s in x["slots"][:4]]} for x in top[:6]],
            "shown": "restaurants with open times are on screen"}


def restaurant_book(venue_id: int, date: str, time: str, party_size: int = 2, seating: str = "") -> dict:
    """Find the slot (closest to `time`) and read its policy. No fees at all (no deposit / charge, no no-show or
    late-cancel fee) = book it right away (his standing rule). Any fee = a confirm card he must authorize."""
    day = _date(date)
    party = max(1, int(party_size or 2))
    f = httpx.get(f"{RESY}/4/find", headers=_resy_headers(), timeout=25,
                  params={"lat": 0, "long": 0, "day": day, "party_size": party, "venue_id": int(venue_id)})
    venues = (f.json().get("results") or {}).get("venues") or [] if f.status_code == 200 else []
    if not venues:
        raise RuntimeError("Resy has no availability there for that date and party size.")
    v = venues[0]
    slots = _slots_near(v.get("slots") or [], time)
    if seating:
        slots = [s for s in slots if seating.lower() in s["type"].lower()] or slots
    if not slots:
        raise RuntimeError("No open tables there that day.")
    s = slots[0]
    d = httpx.post(f"{RESY}/3/details", headers=_resy_headers(), json={"config_id": s["token"], "day": day,
                                                                       "party_size": party}, timeout=25)
    det = d.json() if d.status_code < 300 else {}
    canc = " ".join((((det.get("cancellation") or {}).get("display") or {}).get("policy")) or [])
    fee = ((det.get("cancellation") or {}).get("fee") or {})
    pay = (det.get("payment") or {}).get("amounts") or {}
    venue = v.get("venue") or {}
    name = venue.get("name") or "the restaurant"
    lines = [["When", f"{dt.date.fromisoformat(day).strftime('%a %b %-d')} · {s['label']}"],
             ["Party", f"{party} {'person' if party == 1 else 'people'}"], ["Seating", s["type"]]]
    if s["off"]:
        lines.append(["Note", f"Closest open time to {time} ({s['off']} min off)"])
    if fee.get("amount"):
        lines.append(["No-show / late cancel fee", _money(fee["amount"]) + " per person"])
    total = float(pay.get("total") or 0)
    preview = {"type": "booking", "category": "restaurant", "title": name,
               "photo": ((venue.get("images") or [None])[0]) if isinstance(venue.get("images"), list) else None,
               "lines": lines, "total": _money(total) if total else "No charge to reserve",
               "policy": canc or "Standard Resy cancellation policy", "test_mode": False}
    params = {"config_token": s["token"], "day": day, "party_size": party, "venue": name, "time_label": s["label"]}
    summary = f"Reserve {name}, {s['label']} for {party}"
    fees = _fee_amount(fee) > 0 or total > 0 or bool(det.get("cancellation") is None and d.status_code >= 300)
    if not fees:
        out = store.run_now("restaurant_book", "resy", params, summary, preview, "no_fees")
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "Resy booking failed")
        return {"status": "booked", "no_fees": True, **out["result"], "policy": preview["policy"],
                "note": "Booked immediately (no fees, his standing rule). Tell him it's booked; the confirmation is on screen."}
    return store.propose("restaurant_book", "resy", params, summary, preview)


def _fee_amount(fee: dict | None) -> float:
    try:
        return float((fee or {}).get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _fee_due_now(canc: dict) -> float:
    """The cancellation fee that would apply if he cancelled right now (0 inside the free-cancellation window)."""
    fee = canc.get("fee") or {}
    amt = _fee_amount(fee)
    if amt <= 0 or fee.get("applies") is False:
        return 0.0
    cut = fee.get("date_cut_off")
    if cut:
        try:
            if dt.datetime.now(dt.timezone.utc) < dt.datetime.fromisoformat(str(cut).replace("Z", "+00:00")):
                return 0.0
        except ValueError:
            pass
    return amt


def _exec_resy(account: str, config_token: str, day: str, party_size: int, venue: str = "", time_label: str = "") -> dict:
    h = _resy_headers(auth=True)
    d = httpx.post(f"{RESY}/3/details", headers=h, json={"config_id": config_token, "day": day,
                                                         "party_size": party_size}, timeout=25)
    if d.status_code >= 300:
        raise RuntimeError(f"Resy details failed ({d.status_code}): that time may be gone.")
    book_token = ((d.json().get("book_token") or {}).get("value"))
    if not book_token:
        raise RuntimeError("Resy didn't return a booking token (the slot may have just been taken).")
    u = httpx.get(f"{RESY}/2/user", headers=h, timeout=20)
    pms = (u.json().get("payment_methods") or []) if u.status_code == 200 else []
    form = {"book_token": book_token, "source_id": "resy.com-venue-details"}
    if pms:
        form["struct_payment_method"] = json.dumps({"id": pms[0]["id"]})
    b = httpx.post(f"{RESY}/3/book", headers={**h, "Content-Type": "application/x-www-form-urlencoded"},
                   data=form, timeout=30)
    if b.status_code >= 300:
        raise RuntimeError(f"Resy booking failed ({b.status_code}): {b.text[:200]}")
    j = b.json()
    out = {"category": "restaurant", "reference": j.get("reservation_id") or j.get("resy_token", "")[:12],
           "name": venue, "when": f"{day} {time_label}", "party_size": party_size, "test_mode": False}
    store.record_result("booking", None, {"category": "restaurant"}, {"key": f"booking:resy:{out['reference']}", **out})
    return out


# ------------------------------------------------------------------ his reservations: list + cancel (confirm first)
def _resy_reservations(upcoming_only: bool = True) -> list[dict]:
    h = _resy_headers(auth=True)
    j = httpx.get(f"{RESY}/3/user/reservations", headers=h, params={"limit": 50, "offset": 0}, timeout=25)
    if j.status_code != 200:
        raise RuntimeError(f"Couldn't read his Resy reservations ({j.status_code}).")
    data = j.json()
    venues = data.get("venues") or {}
    today = dt.date.today().isoformat()
    out = []
    for x in data.get("reservations") or []:
        if upcoming_only and (x.get("day") or "") < today:
            continue
        st = x.get("status") or {}
        if st.get("finished") or st.get("no_show"):
            continue
        v = venues.get(str((x.get("venue") or {}).get("id"))) or {}
        loc = v.get("location") or {}
        canc = x.get("cancellation") or {}
        fee = _fee_due_now(canc)
        t = (x.get("time_slot") or "00:00")[:5]
        hh, mm = map(int, t.split(":"))
        out.append({
            "provider": "resy", "id": str(x.get("reservation_id")), "token": x.get("resy_token"),
            "name": v.get("name") or "Restaurant", "day": x.get("day"), "time": t,
            "time_label": dt.time(hh, mm).strftime("%-I:%M %p"), "party_size": x.get("num_seats"),
            "photo": (v.get("images") or [None])[0], "neighborhood": loc.get("neighborhood"),
            "address": ", ".join(y for y in (loc.get("address_1"), loc.get("locality")) if y),
            "cancel_allowed": canc.get("allowed", True) is not False,
            "cancel_fee": _money(fee) if fee else "",
            "policy": " ".join((x.get("cancellation_policy") or [])) if isinstance(x.get("cancellation_policy"), list)
            else str(x.get("cancellation_policy") or ""),
        })
    out.sort(key=lambda r: (r["day"], r["time"]))
    return out


def _duffel_bookings() -> list[dict]:
    """Flights / stays / cars booked through Duffel that haven't happened yet (skips quietly if not set up)."""
    try:
        _duffel_token()
    except RuntimeError:
        return []
    out, today = [], dt.date.today().isoformat()
    try:
        for o in _duffel("GET", "/air/orders", params={"limit": 50}) or []:
            sl = [_slice(s) for s in o.get("slices") or []]
            if o.get("cancelled_at") or not sl or (sl[0]["depart"] or "")[:10] < today:
                continue
            out.append({"provider": "duffel_flight", "id": o["id"], "name": f"{(o.get('owner') or {}).get('name', 'Flight')} · "
                        f"{sl[0]['origin']} → {sl[0]['destination']}", "day": sl[0]["depart"][:10],
                        "time": sl[0]["depart"][11:16], "time_label": _fmt_dt(sl[0]["depart"]),
                        "reference": o.get("booking_reference"), "total": _money(o.get("total_amount"), o.get("total_currency")),
                        "cancel_allowed": "cancel" in (o.get("available_actions") or []), "test_mode": not o.get("live_mode", False)})
    except RuntimeError:
        pass
    for path, prov in (("/stays/bookings", "duffel_hotel"), ("/cars/bookings", "duffel_car")):
        try:
            for b in _duffel("GET", path, params={"limit": 50}) or []:
                day = b.get("check_in_date") or b.get("pickup_date") or ""
                if b.get("cancelled_at") or b.get("status") == "cancelled" or day < today:
                    continue
                out.append({"provider": prov, "id": b["id"], "name": (b.get("accommodation") or {}).get("name")
                            or (b.get("car") or {}).get("name") or "Booking", "day": day, "time": b.get("pickup_time") or "",
                            "time_label": day, "reference": b.get("reference") or b["id"], "cancel_allowed": True,
                            "test_mode": not b.get("live_mode", False)})
        except RuntimeError:
            continue  # stays / cars not enabled on his Duffel account
    return out


def reservations_list() -> dict:
    """His upcoming reservations and bookings (Resy tables + Duffel flights/hotels/cars), shown as a display."""
    items, errors = [], []
    try:
        items += _resy_reservations()
    except RuntimeError as e:
        errors.append(str(e))
    items += _duffel_bookings()
    items.sort(key=lambda r: (r["day"] or "", r.get("time") or ""))
    res = {"kind": "reservations", "key": "travel:my-reservations", "items": items, "errors": errors}
    store.record_result("reservations_list", None, {}, res)
    return {"count": len(items), "errors": errors,
            "reservations": [{"provider": r["provider"], "reservation_id": r["id"], "name": r["name"], "day": r["day"],
                              "time": r.get("time_label"), "party_size": r.get("party_size"),
                              "cancel_allowed": r["cancel_allowed"]} for r in items],
            "shown": "his reservations are on screen"}


def reservation_cancel(reservation_id: str, provider: str = "resy") -> dict:
    """Put up a confirm card to cancel one of his reservations. Cancelled only when he authorizes it."""
    rid = str(reservation_id)
    if provider == "resy":
        r = next((x for x in _resy_reservations() if x["id"] == rid), None)
        if not r:
            raise ValueError("I can't find that Resy reservation among his upcoming ones; call reservations_list.")
        if not r["cancel_allowed"]:
            raise RuntimeError(f"Resy no longer allows cancelling {r['name']} online; he'd need to call the restaurant.")
        lines = [["When", f"{dt.date.fromisoformat(r['day']).strftime('%a %b %-d')} · {r['time_label']}"],
                 ["Party", f"{r['party_size']} people"]]
        if r["address"]:
            lines.append(["Where", r["address"]])
        preview = {"type": "booking", "category": "cancel", "title": f"Cancel · {r['name']}", "photo": r["photo"],
                   "lines": lines, "total": r["cancel_fee"] or "No cancellation fee", "total_label": "Fee",
                   "policy": r["policy"] or "Cancelling releases the table.", "danger": "This gives up the table.",
                   "test_mode": False, "action_label": "Cancel reservation"}
        params = {"provider": "resy", "token": r["token"], "name": r["name"], "reservation_id": rid}
        summary = f"Cancel {r['name']}, {r['time_label']} on {r['day']}"
        if not r["cancel_fee"]:  # free to cancel right now: do it (his standing rule)
            out = store.run_now("reservation_cancel", "resy", params, summary, preview, "no_fees")
            if not out.get("ok"):
                raise RuntimeError(out.get("error") or "Resy cancel failed")
            return {"status": "cancelled", "no_fees": True, **out["result"],
                    "note": "Cancelled immediately (no fee, his standing rule). Tell him it's done."}
        return store.propose("reservation_cancel", "resy", params, summary, preview)
    if provider in ("duffel_flight", "duffel_hotel", "duffel_car"):
        b = next((x for x in _duffel_bookings() if x["id"] == rid), None)
        if not b:
            raise ValueError("I can't find that booking; call reservations_list.")
        lines, total, quote_id = [["Booking", b["name"]], ["Reference", str(b.get("reference") or "")]], "", ""
        if provider == "duffel_flight":
            q = _duffel("POST", "/air/order_cancellations", {"order_id": rid})  # a quote; not confirmed yet
            quote_id = q["id"]
            total = _money(q.get("refund_amount"), q.get("refund_currency"))
            lines.append(["Refund to", str(q.get("refund_to") or "").replace("_", " ")])
        preview = {"type": "booking", "category": "cancel", "title": f"Cancel · {b['name']}", "lines": lines,
                   "total": total or "See policy", "total_label": "Refund" if provider == "duffel_flight" else "Total",
                   "policy": "Cancellation follows the fare / rate rules.", "danger": "This cancels the booking.",
                   "test_mode": b.get("test_mode"), "action_label": "Cancel booking"}
        return store.propose("reservation_cancel", "travel", {"provider": provider, "booking_id": rid, "quote_id": quote_id,
                                                              "name": b["name"]},
                             f"Cancel {b['name']}", preview)
    raise ValueError(f"unknown provider {provider!r}")


def _exec_cancel(account: str, provider: str, name: str = "", token: str = "", reservation_id: str = "",
                 booking_id: str = "", quote_id: str = "") -> dict:
    if provider == "resy":
        r = httpx.post(f"{RESY}/3/cancel", headers={**_resy_headers(auth=True),
                                                    "Content-Type": "application/x-www-form-urlencoded"},
                       data={"resy_token": token}, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Resy cancel failed ({r.status_code}): {r.text[:200]}")
        j = r.json() if r.text.strip().startswith("{") else {}
        refund = (j.get("payment") or {}).get("transaction") or {}
        out = {"category": "cancel", "name": name, "reference": reservation_id,
               "refund": _money(refund.get("refund_amount")) if refund.get("refund_amount") else ""}
    elif provider == "duffel_flight":
        c = _duffel("POST", f"/air/order_cancellations/{quote_id}/actions/confirm")
        out = {"category": "cancel", "name": name, "reference": booking_id,
               "refund": _money(c.get("refund_amount"), c.get("refund_currency"))}
    else:
        path = "/stays/bookings" if provider == "duffel_hotel" else "/cars/bookings"
        _duffel("POST", f"{path}/{booking_id}/actions/cancel")
        out = {"category": "cancel", "name": name, "reference": booking_id}
    store.record_result("booking", None, {"category": "cancel"}, {"key": f"cancel:{reservation_id or booking_id}", **out})
    return out


_EXECUTORS = {"reservation_cancel": _exec_cancel, "flight_book": _exec_flight, "hotel_book": _exec_hotel, "car_rental_book": _exec_car,
              "restaurant_book": _exec_resy}


def register_executors() -> None:
    """Booking executors run ONLY via store.execute_action, after Stephen authorizes the confirm card."""
    from . import store as S
    S.EXECUTORS.update(_EXECUTORS)


register_executors()
