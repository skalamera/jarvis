"""Turn-by-turn directions via Google Routes API (computeRoutes) for the JARVIS directions card.

Traffic-aware for driving (duration vs staticDuration -> delay), alternatives, per-step instructions with maneuvers,
transit legs (line, headsign, stops, times). Origin defaults to where this Mac is (IP location). The API key stays
server-side. If Routes isn't enabled, raise a clear error so the model falls back to the plain map card.
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from . import store
from .places import _key
from .weather import _here

API = "https://routes.googleapis.com/directions/v2:computeRoutes"
MODES = {"driving": "DRIVE", "walking": "WALK", "transit": "TRANSIT", "bicycling": "BICYCLE"}
FIELDS = ",".join((
    "routes.description", "routes.duration", "routes.staticDuration", "routes.distanceMeters", "routes.localizedValues",
    "routes.warnings", "routes.routeLabels", "routes.travelAdvisory.tollInfo", "routes.polyline.encodedPolyline",
    "routes.legs.startLocation", "routes.legs.endLocation",
    "routes.legs.steps.distanceMeters", "routes.legs.steps.staticDuration", "routes.legs.steps.localizedValues",
    "routes.legs.steps.navigationInstruction", "routes.legs.steps.travelMode", "routes.legs.steps.transitDetails",
    "routes.legs.stepsOverview",
))
_cache: dict[str, tuple[float, dict]] = {}


def _secs(v: str | None) -> int:
    return int(float(v.rstrip("s"))) if v else 0


def _dur(s: int) -> str:
    m = round(s / 60)
    if m < 60:
        return f"{max(m, 1)} min"
    h, m = divmod(m, 60)
    return f"{h} hr {m} min" if m else f"{h} hr"


def _miles(m: int) -> str:
    mi = m / 1609.344
    if mi < 0.1:
        return f"{round(m * 3.28084 / 10) * 10} ft"
    return f"{mi:.1f} mi" if mi < 10 else f"{round(mi)} mi"


def _wp(s: str, here: dict | None) -> dict:
    s = (s or "").strip()
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", s)
    if m:
        return {"location": {"latLng": {"latitude": float(m[1]), "longitude": float(m[2])}}}
    if not s or s.lower() in {"here", "current location", "my location", "me"}:
        if not here:
            raise ValueError("I don't know where you are right now.")
        return {"location": {"latLng": {"latitude": here["lat"], "longitude": here["lon"]}}}
    return {"address": s}


def _t(s: str) -> str:
    return (s or "").replace("\u202f", " ").replace("\u2009", " ")


def parse(j: dict, mode: str) -> list[dict]:
    """Routes API response -> card routes (pure; unit-tested)."""
    out = []
    for r in j.get("routes") or []:
        dur, static = _secs(r.get("duration")), _secs(r.get("staticDuration"))
        dist = int(r.get("distanceMeters") or 0)
        steps = []
        for leg in r.get("legs") or []:
            for st in leg.get("steps") or []:
                ni = st.get("navigationInstruction") or {}
                sd = _secs(st.get("staticDuration"))
                step: dict[str, Any] = {
                    "text": ni.get("instructions") or "", "maneuver": ni.get("maneuver") or "",
                    "distance": _miles(int(st.get("distanceMeters") or 0)) if st.get("distanceMeters") else "",
                    "duration": _dur(sd) if sd >= 30 else "", "mode": st.get("travelMode", ""),
                }
                td = st.get("transitDetails")
                if td:
                    ln = td.get("transitLine") or {}
                    sdp = td.get("stopDetails") or {}
                    tl = td.get("localizedValues") or {}
                    step["transit"] = {
                        "line": ln.get("nameShort") or ln.get("name") or "", "line_name": ln.get("name") or "",
                        "color": ln.get("color") or "", "text_color": ln.get("textColor") or "",
                        "vehicle": ((ln.get("vehicle") or {}).get("name") or {}).get("text") or "",
                        "headsign": td.get("headsign") or "", "stops": td.get("stopCount"),
                        "from": ((sdp.get("departureStop") or {}).get("name")) or "",
                        "to": ((sdp.get("arrivalStop") or {}).get("name")) or "",
                        "depart": _t(((tl.get("departureTime") or {}).get("time") or {}).get("text") or ""),
                        "arrive": _t(((tl.get("arrivalTime") or {}).get("time") or {}).get("text") or ""),
                    }
                if not step["text"] and not td:
                    continue
                steps.append(step)
        delay = dur - static if mode == "driving" and static else 0
        legs = [s["transit"] for s in steps if s.get("transit")]
        summary = r.get("description") or " → ".join(f"{t['vehicle']} {t['line']}".strip() for t in legs)
        out.append({
            "summary": summary, "labels": r.get("routeLabels") or [],
            "duration": _dur(dur), "duration_s": dur, "distance": _miles(dist), "distance_m": dist,
            "typical": _dur(static) if static and mode == "driving" else "", "delay_min": round(delay / 60) if delay > 0 else 0,
            "traffic": ("heavy" if delay >= 900 or (static and delay / static >= 0.3) else
                        "moderate" if delay >= 240 else "light") if mode == "driving" and static else "",
            "tolls": "tollInfo" in (r.get("travelAdvisory") or {}),  # present (even empty) = route has tolls
            "warnings": r.get("warnings") or [], "steps": steps,
        })
    return out


def directions(destination: str, origin: str = "", mode: str = "driving", avoid_tolls: bool = False,
               avoid_highways: bool = False, record: bool = True) -> dict:
    mode = (mode or "driving").lower()
    mode = {"drive": "driving", "car": "driving", "walk": "walking", "bike": "bicycling", "cycling": "bicycling",
            "train": "transit", "subway": "transit", "bus": "transit", "public transit": "transit"}.get(mode, mode)
    if mode not in MODES:
        raise ValueError(f"Unknown travel mode {mode}")
    if not destination.strip():
        raise ValueError("Where to?")
    here = None
    if not origin.strip() or origin.strip().lower() in {"here", "current location", "my location", "me"}:
        with httpx.Client() as h:
            here = _here(h)
    ck = f"{origin}|{destination}|{mode}|{avoid_tolls}|{avoid_highways}"
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 120:
        res = hit[1]
    else:
        body: dict[str, Any] = {
            "origin": _wp(origin, here), "destination": _wp(destination, here), "travelMode": MODES[mode],
            "computeAlternativeRoutes": mode != "transit", "units": "IMPERIAL", "languageCode": "en-US",
        }
        if mode == "driving":
            body["routingPreference"] = "TRAFFIC_AWARE_OPTIMAL"
            body["routeModifiers"] = {"avoidTolls": avoid_tolls, "avoidHighways": avoid_highways}
        if mode == "transit":
            body["computeAlternativeRoutes"] = True
        r = httpx.post(API, json=body, timeout=25, headers={
            "X-Goog-Api-Key": _key(), "Content-Type": "application/json", "X-Goog-FieldMask": FIELDS})
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = r.text[:200]
            if r.status_code == 403:
                raise RuntimeError("Google Routes API isn't enabled for this key yet (Cloud console: enable "
                                   "'Routes API' and allow it on the key). " + msg[:120])
            raise RuntimeError(f"Directions failed: {msg[:200]}")
        routes = parse(r.json(), mode)
        if not routes:
            raise ValueError(f"No {mode} route found to {destination}.")
        res = {
            "origin": origin.strip() or (f"{here['name']}, {here.get('region', '')}".strip(", ") if here else ""),
            "origin_is_here": here is not None,
            "origin_latlng": f"{here['lat']},{here['lon']}" if here else "",
            "destination": destination.strip(), "mode": mode, "routes": routes,
            "avoid_tolls": avoid_tolls, "avoid_highways": avoid_highways,
        }
        _cache[ck] = (time.time(), res)
    if record:
        store.record_result("directions", None, {"destination": destination, "origin": origin, "mode": mode}, res)
    return res


def directions_mode(destination: str, origin: str = "", mode: str = "driving", avoid_tolls: bool = False,
                    avoid_highways: bool = False) -> dict:
    """HUD click on the card's mode / avoid toggles: recompute in place (no new card)."""
    return directions(destination, origin, mode, avoid_tolls, avoid_highways, record=False)
