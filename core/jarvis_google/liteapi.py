"""Hotels through LiteAPI (Nuitée Connect): live rates, prebook -> confirm card -> book, list, cancel.

Key: LITEAPI_KEY in ~/.hermes/.env (sand_... = sandbox, which books against a hidden test card; a production key
books for real on the card attached to his LiteAPI account, payment method ACC_CREDIT_CARD). margin 0 asks for
net rates (no commission added on top), since he's booking for himself.

Safety: hotel_book only PREBOOKS (locks the price, returns the policy) and puts up a confirm card; the booking
call runs in the executor after he authorizes it. The executor refuses if the re-checked price rose.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from typing import Any

import httpx

from . import store

API = "https://api.liteapi.travel/v3.0"
BOOK = "https://book.liteapi.travel/v3.0"
LEDGER = store.STATE_DIR / "liteapi_bookings.json"


def _key() -> str:
    from .travel import _env
    return _env("LITEAPI_KEY")


def configured() -> bool:
    return bool(_key())


def sandbox() -> bool:
    return _key().startswith("sand_")


def _req(method: str, url: str, body: dict | None = None, params: dict | None = None, timeout: float = 45) -> Any:
    k = _key()
    if not k:
        raise RuntimeError("Hotel booking needs a LiteAPI key: add LITEAPI_KEY to ~/.hermes/.env (liteapi.travel "
                           "dashboard > API keys).")
    r = httpx.request(method, url, headers={"X-API-Key": k, "accept": "application/json",
                                            "content-type": "application/json"},
                      json=body, params=params, timeout=timeout)
    if r.status_code >= 300:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.text
        except Exception:
            msg = r.text
        raise RuntimeError(f"LiteAPI {r.status_code}: {str(msg)[:200]}")
    return r.json()


def _money(v: float | None, cur: str | None = "USD") -> str:
    if v is None:
        return ""
    return f"${v:,.2f}" if (cur or "USD") == "USD" else f"{v:,.2f} {cur}"


def _policy(rate: dict) -> tuple[bool, str]:
    cp = rate.get("cancellationPolicies") or {}
    refundable = cp.get("refundableTag") == "RFN"
    infos = [i for i in cp.get("cancelPolicyInfos") or [] if float(i.get("amount") or 0) > 0]
    if refundable and infos:
        first = min(infos, key=lambda i: i.get("cancelTime") or "")
        return True, f"Free cancellation until {first.get('cancelTime', '')[:16]} {first.get('timezone', 'GMT')}"
    if refundable:
        return True, "Free cancellation"
    if infos:
        return False, "Non-refundable (partial refund possible: " + ", ".join(
            f"{_money(float(i['amount']), i.get('currency'))} kept after {i.get('cancelTime', '')[:10]}" for i in infos[:2]) + ")"
    return False, "Non-refundable"


def search(lat: float, lng: float, check_in: str, check_out: str, adults: int = 2, rooms: int = 1,
           radius_m: int = 6000, limit: int = 30, near: str = "") -> dict:
    """Live, bookable hotels around a point with the cheapest offer per hotel."""
    hotels = _req("GET", f"{API}/data/hotels", params={"latitude": lat, "longitude": lng, "radius": radius_m,
                                                         "limit": limit}).get("data") or []
    if not hotels:
        return {"hotels": [], "count": 0}
    info = {h.get("id"): h for h in hotels}
    per = max(1, round(adults / max(1, rooms)))
    body = {"hotelIds": list(info)[:limit], "occupancies": [{"adults": per} for _ in range(max(1, rooms))],
            "currency": "USD", "guestNationality": "US", "checkin": check_in, "checkout": check_out,
            "timeout": 10, "margin": 0}
    try:
        rates = _req("POST", f"{API}/hotels/rates", body, timeout=60).get("data") or []
    except RuntimeError as e:
        if "margin" not in str(e).lower():
            raise
        body.pop("margin")
        rates = _req("POST", f"{API}/hotels/rates", body, timeout=60).get("data") or []
    nights = max(1, (dt.date.fromisoformat(check_out) - dt.date.fromisoformat(check_in)).days)
    out = []
    for h in rates:
        offers = h.get("roomTypes") or []
        if not offers:
            continue
        def due(o: dict) -> float:  # taxes / resort fees NOT in the prepaid price (paid at the hotel)
            return sum(float(t.get("amount") or 0) for r in o.get("rates") or []
                       for t in ((r.get("retailRate") or {}).get("taxesAndFees") or []) if t and t.get("included") is False)
        # Rank by what he actually pays: a $6 "rate" with a $100 resort fee due at the hotel isn't cheap.
        best = min(offers, key=lambda o: float((o.get("offerRetailRate") or {}).get("amount") or 1e12) + due(o))
        prepaid = float((best.get("offerRetailRate") or {}).get("amount") or 0)
        cur = (best.get("offerRetailRate") or {}).get("currency") or "USD"
        rate0 = (best.get("rates") or [{}])[0]
        refundable, policy = _policy(rate0)
        due_at_hotel = due(best)
        total = prepaid + due_at_hotel
        d = info.get(h.get("hotelId")) or {}
        out.append({
            "id": best.get("offerId"), "offer_id": best.get("offerId"), "hotel_id": h.get("hotelId"),
            "name": d.get("name") or "Hotel", "stars": d.get("stars"), "review_score": d.get("rating"),
            "reviews": d.get("reviewCount"), "photo": d.get("main_photo") or d.get("thumbnail"),
            "address": d.get("address"), "lat": d.get("latitude"), "lng": d.get("longitude"),
            "total": round(total, 2), "per_night": round(total / nights, 2), "currency": cur, "prepaid": _money(prepaid, cur),
            "price": _money(total, cur), "nightly": _money(total / nights, cur), "room": rate0.get("name"),
            "board": rate0.get("boardName"), "refundable": refundable, "policy": policy,
            "due_at_hotel": _money(due_at_hotel, cur) if due_at_hotel else "", "source": "liteapi",
        })
    out.sort(key=lambda x: (-(x["review_score"] or 0) * 25 + x["per_night"]))
    return {"hotels": out, "count": len(out), "nights": nights, "near": near, "check_in": check_in,
            "check_out": check_out, "sandbox": sandbox(), "searched_at": time.time()}


def propose_book(offer_id: str) -> dict:
    """Prebook (locks price + policy), then a confirm card. Nothing is charged until he authorizes it."""
    from .travel import _need
    p = _need("given_name", "family_name", "email")
    pb = (_req("POST", f"{BOOK}/rates/prebook", {"offerId": offer_id, "usePaymentSdk": False}, timeout=60) or {}).get("data") or {}
    if not pb.get("prebookId"):
        raise RuntimeError("That room is no longer available; search again.")
    rt = (pb.get("roomTypes") or [{}])[0]
    rate = (rt.get("rates") or [{}])[0]
    refundable, policy = _policy(rate)
    total, cur = float(pb.get("price") or 0), pb.get("currency") or "USD"
    hotel = pb.get("hotel") or {}
    name = hotel.get("name") or pb.get("hotelName") or "Hotel"
    lines = [["Dates", f"{pb.get('checkin', '')} → {pb.get('checkout', '')}"],
             ["Room", f"{rate.get('name', '')} · {rate.get('boardName', '')}".strip(" ·")],
             ["Guest", f"{p['given_name']} {p['family_name']}"]]
    due = sum(float(t.get("amount") or 0) for t in ((rate.get("retailRate") or {}).get("taxesAndFees") or [])
              if t and t.get("included") is False)
    if due:
        lines.append(["Charged now", _money(total, cur)])
        lines.append(["Due at hotel", f"{_money(due, cur)} (taxes / resort fees)"])
        lines.append(["All-in", _money(total + due, cur)])
    if pb.get("priceDifferencePercent"):
        lines.append(["Price change", f"{pb['priceDifferencePercent']}% since search"])
    preview = {"type": "booking", "category": "hotel", "title": name, "photo": hotel.get("main_photo"),
               "lines": lines, "total": _money(total, cur), "policy": policy, "test_mode": sandbox()}
    return store.propose("hotel_book_liteapi", "travel",
                         {"prebook_id": pb["prebookId"], "offer_id": offer_id, "total": total, "name": name},
                         f"Book {name} for {_money(total, cur)}", preview)


def _ledger() -> list[dict]:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return []


def _exec_book(account: str, prebook_id: str, offer_id: str, total: float, name: str) -> dict:
    from .travel import _need
    p = _need("given_name", "family_name", "email")
    holder = {"firstName": p["given_name"], "lastName": p["family_name"], "email": p["email"]}
    if p.get("phone_number"):
        holder["phone"] = p["phone_number"]
    body = {"prebookId": prebook_id, "holder": holder, "clientReference": "jarvis-" + uuid.uuid4().hex[:12],
            "guests": [{"occupancyNumber": 1, "firstName": p["given_name"], "lastName": p["family_name"],
                        "email": p["email"]}], "payment": {"method": "ACC_CREDIT_CARD"}}
    try:
        b = (_req("POST", f"{BOOK}/rates/book", body, timeout=120) or {}).get("data") or {}
    except RuntimeError as e:
        if "prebook" not in str(e).lower() and "expired" not in str(e).lower():
            raise
        pb = (_req("POST", f"{BOOK}/rates/prebook", {"offerId": offer_id, "usePaymentSdk": False}) or {}).get("data") or {}
        if float(pb.get("price") or 1e12) > float(total) * 1.005:
            raise RuntimeError(f"The price changed to {_money(float(pb.get('price') or 0), pb.get('currency'))}; "
                               "nothing was booked. Search again to see the new rate.")
        b = (_req("POST", f"{BOOK}/rates/book", {**body, "prebookId": pb["prebookId"]}, timeout=120) or {}).get("data") or {}
    out = {"category": "hotel", "reference": b.get("hotelConfirmationCode") or b.get("bookingId"),
           "booking_id": b.get("bookingId"), "name": (b.get("hotel") or {}).get("name") or name,
           "check_in": b.get("checkin"), "check_out": b.get("checkout"), "status": b.get("status"),
           "total": _money(float(b.get("price") or total), b.get("currency") or "USD"), "test_mode": sandbox()}
    led = _ledger() + [{**out, "booked_at": time.time()}]
    LEDGER.write_text(json.dumps(led))
    LEDGER.chmod(0o600)
    store.record_result("booking", None, {"category": "hotel"}, {"key": f"booking:{out['booking_id']}", **out})
    return out


def bookings() -> list[dict]:
    """Upcoming hotels he booked through JARVIS (LiteAPI), for reservations_list."""
    if not configured():
        return []
    today = dt.date.today().isoformat()
    out = []
    for b in _ledger():
        if (b.get("check_out") or "") < today or b.get("status") == "CANCELLED":
            continue
        try:
            live = (_req("GET", f"{BOOK}/bookings/{b['booking_id']}") or {}).get("data") or {}
            if (live.get("status") or "").upper().startswith("CANCEL"):
                continue
        except RuntimeError:
            pass
        out.append({"provider": "liteapi_hotel", "id": b["booking_id"], "name": b.get("name") or "Hotel",
                    "day": b.get("check_in") or "", "time": "", "time_label": b.get("check_in") or "",
                    "reference": b.get("reference"), "total": b.get("total"), "cancel_allowed": True,
                    "test_mode": b.get("test_mode")})
    return out


def cancel(booking_id: str) -> dict:
    r = (_req("PUT", f"{BOOK}/bookings/{booking_id}", timeout=60) or {}).get("data") or {}
    led = _ledger()
    for b in led:
        if b.get("booking_id") == booking_id:
            b["status"] = "CANCELLED"
    LEDGER.write_text(json.dumps(led))
    return {"status": r.get("status"), "refund": _money(r.get("refund_amount"), r.get("currency")) if r.get("refund_amount") is not None else "",
            "fee": _money(r.get("cancellation_fee"), r.get("currency")) if r.get("cancellation_fee") else ""}


def register_executors() -> None:
    store.EXECUTORS["hotel_book_liteapi"] = _exec_book


register_executors()
