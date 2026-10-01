"""Used-car listings near Stephen (read-only), from iSeeCars' public search pages (schema.org data + listing cards).

cars_nearby("Mercedes-Benz", "CLS-Class", trim="CLS 550 4MATIC") fetches a few result pages for the home area, keeps the
exact trim, dedupes by VIN, adds a straight-line distance from home, and records a `car_listings` card.
"""
from __future__ import annotations

import html as H
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from . import store
from .weather import _geocode, _here

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"}
# iSeeCars model ids (their URL scheme: /used_cars-t<id>-<make>-<model>-<city>-<st>)
MODEL_IDS = {("mercedes-benz", "cls-class"): 10341}
PAGES = 4
_cache: dict[str, tuple[float, dict]] = {}
_geo: dict[str, tuple[float, float] | None] = {}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<script.*?</script>|<svg.*?</svg>|<[^>]+>", " ", s, flags=re.S))).strip()


def parse(page: str) -> list[dict]:
    """Listing cards on one results page."""
    out = []
    for b in re.split(r'(?=<div[^>]*class="\s*article-search-result)', page)[1:]:
        title = re.search(r'srp-listing-title">([^<]+)', b)
        if not title:
            continue
        price = re.search(r'listing-price">\s*\$([\d,]+)', b)
        miles = re.search(r'([\d,]+)\s+Miles', b)
        vin = re.search(r'"vehicleIdentificationNumber":"(\w{17})"', b)
        lis = [_txt(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", b, re.S)]
        loc = next((x for x in lis if re.fullmatch(r"[A-Za-z .'\-]+, [A-Z]{2}", x)), "")
        ld = re.search(r'<script type="application/ld\+json">(\{"@context":"http://schema.org/","@type":"Vehicle".*?)</script>', b, re.S)
        color = ""
        if ld:
            try:
                color = json.loads(ld.group(1)).get("color") or ""
            except Exception:
                pass
        deal = re.search(r"\b(GREAT|GOOD|FAIR|HIGH)\s+(?:DEAL|PRICE)\b", _txt(b), re.I)
        below = re.search(r"\$([\d,]+)\s+Below market", _txt(b), re.I)
        t = H.unescape(title.group(1)).strip()
        y = re.match(r"(\d{4})", t)
        url = re.search(r'data-listing-url="([^"]+)"', b)
        out.append({
            "title": t, "year": int(y.group(1)) if y else None,
            "price": int(price.group(1).replace(",", "")) if price else None,
            "miles": int(miles.group(1).replace(",", "")) if miles else None,
            "location": loc, "color": color, "vin": vin.group(1) if vin else "",
            "photo": (re.search(r'src="(https://t\d\.iseecars\.com/img/[^"]+)"', b) or [None, ""])[1],
            "url": H.unescape(url.group(1)) if url else "",
            "deal": (deal.group(1).title() + " deal") if deal else "",
            "below_market": int(below.group(1).replace(",", "")) if below else None,
        })
    return out


def _dist(home: dict, loc: str) -> float | None:
    if not loc:
        return None
    if loc not in _geo:
        try:
            with httpx.Client() as h:
                g = _geocode(h, loc)
            _geo[loc] = (g["lat"], g["lon"])
        except Exception:
            _geo[loc] = None
    p = _geo[loc]
    if not p:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (home["lat"], home["lon"], p[0], p[1]))
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(3958.8 * 2 * math.asin(math.sqrt(a)))


def cars_nearby(make: str = "Mercedes-Benz", model: str = "CLS-Class", trim: str = "CLS 550 4MATIC",
                record: bool = True) -> dict:
    key = f"{make}|{model}|{trim}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        res = hit[1]
    else:
        mid = MODEL_IDS.get((_slug(make), _slug(model)))
        if not mid:
            return {"error": f"Car search isn't set up for {make} {model} yet."}
        with httpx.Client(headers=UA, timeout=15, follow_redirects=True) as h:
            home = _here(h)
            city = f"{_slug(home.get('name', 'new-york'))}-{_slug(_state_code(home.get('region', '')) or 'ny')}"
            base = f"https://www.iseecars.com/used_cars-t{mid}-{_slug(make)}-{_slug(model)}-{city}"

            def page(n: int) -> str:
                try:
                    return h.get(base + (f"?page={n}" if n > 1 else "")).text  # (served with a 410 status, body intact)
                except Exception:
                    return ""
            with ThreadPoolExecutor(PAGES) as ex:
                pages = list(ex.map(page, range(1, PAGES + 1)))
        want = _slug(trim)
        seen, cars = set(), []
        for p in pages:
            for c in parse(p):
                if want and want not in _slug(c["title"]):
                    continue
                k = c["vin"] or (c["title"], c["price"], c["miles"])
                if k in seen:
                    continue
                seen.add(k)
                cars.append(c)
        with ThreadPoolExecutor(6) as ex:
            for c, d in zip(cars, ex.map(lambda c: _dist(home, c["location"]), cars)):
                c["distance_mi"] = d
        cars.sort(key=lambda c: (c["distance_mi"] if c["distance_mi"] is not None else 999, c["price"] or 0))
        prices = [c["price"] for c in cars if c["price"]]
        res = {"query": f"{make} {trim}".strip(), "near": f"{home.get('name')}, {_state_code(home.get('region', ''))}",
               "count": len(cars), "cars": cars[:24], "source": "iSeeCars", "search_url": base,
               "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None,
               "price_median": sorted(prices)[len(prices) // 2] if prices else None}
        _cache[key] = (time.time(), res)
    if record and not res.get("error"):
        store.record_result("cars_nearby", None, {"make": make, "model": model, "trim": trim}, res)
    return res


_STATES = {"new york": "NY", "new jersey": "NJ", "connecticut": "CT", "pennsylvania": "PA", "massachusetts": "MA"}


def _state_code(region: str) -> str:
    r = (region or "").strip()
    return r if len(r) == 2 else _STATES.get(r.lower(), r[:2].upper())


def brief(r: dict) -> dict:
    if r.get("error"):
        return r
    return {k: r.get(k) for k in ("query", "near", "count", "price_min", "price_max", "price_median")} | {
        "nearest": [{k: c.get(k) for k in ("title", "price", "miles", "location", "distance_mi", "deal")}
                    for c in r.get("cars", [])[:6]], "card": "car listings card shown"}


__all__ = ["cars_nearby", "parse", "brief"]
_ = Any
