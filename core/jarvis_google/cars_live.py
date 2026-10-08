"""Live rental-car prices via OctoTrip's free rental-car MCP server (DiscoverCars inventory, no key).

Search only: it returns real offers with an affiliate booking link. Booking happens on the provider's own site,
opened inside JARVIS (web-app card) when he clicks Book, so he completes payment himself.
"""
from __future__ import annotations

import json
import time
import threading

import httpx

URL = "https://mcp.octotrip.app/rental-cars/mcp"
_H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _rpc(method: str, params: dict, timeout: float = 90) -> dict:
    r = httpx.post(URL, headers=_H, json={"jsonrpc": "2.0", "id": int(time.time() * 1000) % 10**9, "method": method,
                                          "params": params}, timeout=timeout)
    r.raise_for_status()
    for line in r.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return r.json()


def search(location: str, pickup_date: str, dropoff_date: str, pickup_time: str = "12:00",
           dropoff_time: str = "12:00", max_results: int = 10) -> dict:
    """Cheapest live offer per car category (economy, compact, SUV...) at a location for the dates."""
    ck = f"{location}|{pickup_date}|{dropoff_date}|{pickup_time}|{dropoff_time}"
    with _lock:
        hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    _rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "jarvis", "version": "1"}})
    res = _rpc("tools/call", {"name": "search", "arguments": {
        "location": location, "pickup_date": pickup_date, "dropoff_date": dropoff_date, "pickup_time": pickup_time,
        "dropoff_time": dropoff_time, "currency": "USD"}})
    text = ((res.get("result") or {}).get("content") or [{}])[0].get("text") or "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"rental-car search failed: {text[:160]}")
    if data.get("error"):
        out = {"cars": [], "count": 0, "message": data.get("message"), "location": data.get("pickup_location_resolved")}
    else:
        rows = data.get("results") or []
        best: dict[str, dict] = {}
        for c in rows:
            k = c.get("category") or c.get("sipp") or "Other"
            if k not in best or float(c.get("price") or 1e9) < float(best[k].get("price") or 1e9):
                best[k] = c
        cars = sorted(best.values(), key=lambda c: float(c.get("price") or 1e9))[:max_results]
        out = {"count": len(rows), "location": data.get("pickup_location_resolved") or location, "cars": [{
            "id": c.get("booking_url"), "name": c.get("name"), "category": c.get("category"), "supplier": c.get("vendor"),
            "seats": c.get("passengers"), "bags": c.get("bags"), "transmission": c.get("transmission"),
            "photo": c.get("image_url"), "total": float(c.get("price") or 0), "per_day": c.get("price_per_day"),
            "price": f"${float(c.get('price') or 0):,.2f}", "currency": c.get("currency") or "USD",
            "free_cancellation": c.get("free_cancellation"), "mileage": c.get("mileage"), "deposit": c.get("deposit"),
            "fuel_policy": c.get("fuel_policy"), "protections": (c.get("included_protections") or [])[:4],
            "booking_url": c.get("booking_url"), "source": "octotrip"} for c in cars]}
    out["searched_at"] = time.time()
    with _lock:
        _cache[ck] = (time.time(), out)
    return out
