"""LiteAPI hotels + OctoTrip cars: response shaping, the confirm gate, and the price-rise guard."""
import json

import pytest

from jarvis_google import cars_live, liteapi, store, trips


@pytest.fixture()
def lite(monkeypatch, tmp_path):
    monkeypatch.setattr(liteapi, "_key", lambda: "sand_test")
    monkeypatch.setattr(liteapi, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(store, "record_result", lambda *a, **k: 0)
    calls = []

    def fake(method, url, body=None, params=None, timeout=45):
        calls.append((method, url.split("v3.0")[-1], body))
        if url.endswith("/data/hotels"):
            return {"data": [{"id": "h1", "name": "Harbor Inn", "rating": 8.9, "stars": 4, "main_photo": "p.jpg",
                              "address": "1 Bay St", "latitude": 32.7, "longitude": -79.9}]}
        if url.endswith("/hotels/rates"):
            def offer(oid, amt, tag):
                return {"offerId": oid, "offerRetailRate": {"amount": amt, "currency": "USD"},
                        "rates": [{"name": "King", "boardName": "Room Only", "cancellationPolicies": {
                            "refundableTag": tag, "cancelPolicyInfos": [{"cancelTime": "2026-10-20 12:00:00", "amount": amt,
                                                                         "currency": "USD", "timezone": "GMT"}]},
                                   "retailRate": {"taxesAndFees": [{"included": False, "amount": 30}]}}]}
            return {"data": [{"hotelId": "h1", "roomTypes": [offer("o-exp", 900, "RFN"), offer("o-cheap", 600, "NRFN")]}]}
        if url.endswith("/rates/prebook"):
            return {"data": {"prebookId": "pb1", "price": fake.price, "currency": "USD", "checkin": "2026-10-22",
                             "checkout": "2026-10-25", "hotel": {"name": "Harbor Inn"}, "roomTypes": [{"rates": [{"name": "King"}]}]}}
        if url.endswith("/rates/book"):
            if fake.expire:
                fake.expire = False
                raise RuntimeError("LiteAPI 400: prebook expired")
            return {"data": {"bookingId": "B1", "hotelConfirmationCode": "HC9", "status": "CONFIRMED",
                             "checkin": "2026-10-22", "checkout": "2026-10-25", "price": fake.price, "currency": "USD"}}
        raise AssertionError(url)
    fake.price, fake.expire = 600, False
    monkeypatch.setattr(liteapi, "_req", fake)
    monkeypatch.setattr("jarvis_google.travel._need", lambda *f: {"given_name": "Alex", "family_name": "Test", "email": "a@example.com"})
    return fake, calls


def test_search_picks_cheapest_offer_and_reports_policy(lite):
    r = liteapi.search(32.7, -79.9, "2026-10-22", "2026-10-25", adults=2)
    h = r["hotels"][0]
    assert h["offer_id"] == "o-cheap" and h["per_night"] == 210 and h["refundable"] is False  # all-in: 600 + 30 at hotel
    assert h["due_at_hotel"] == "$30.00" and r["nights"] == 3


def test_book_only_proposes(lite, monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "propose", lambda kind, acct, params, summary, preview: seen.update(kind=kind, params=params) or {"status": "awaiting_user_confirmation"})
    out = liteapi.propose_book("o-cheap")
    assert out["status"] == "awaiting_user_confirmation" and seen["kind"] == "hotel_book_liteapi"
    assert not any(u.endswith("/rates/book") for _, u, _ in lite[1])      # nothing booked before he authorizes


def test_executor_rebooks_expired_prebook_only_at_same_price(lite):
    fake, _ = lite
    fake.expire = True
    out = liteapi._exec_book("travel", "pb-old", "o-cheap", 600.0, "Harbor Inn")
    assert out["booking_id"] == "B1" and json.loads(liteapi.LEDGER.read_text())[0]["booking_id"] == "B1"
    fake.expire, fake.price = True, 700
    with pytest.raises(RuntimeError, match="price changed"):
        liteapi._exec_book("travel", "pb-old", "o-cheap", 600.0, "Harbor Inn")


def test_executor_registered():
    liteapi.register_executors()  # other tests reset the registry; this checks the hook wires the right function
    assert store.EXECUTORS.get("hotel_book_liteapi") is liteapi._exec_book


def test_cars_cheapest_per_category(monkeypatch):
    cars_live._cache.clear()
    rows = [{"name": "Kia Soul", "category": "Compact", "price": 220, "vendor": "Budget", "booking_url": "u1"},
            {"name": "Nissan Versa", "category": "Compact", "price": 200, "vendor": "Avis", "booking_url": "u2"},
            {"name": "Ford Explorer", "category": "SUV", "price": 410, "vendor": "Hertz", "booking_url": "u3"}]
    def rpc(method, params, timeout=90):
        if method == "initialize":
            return {}
        return {"result": {"content": [{"text": json.dumps({"results": rows, "pickup_location_resolved": "CHS"})}]}}
    monkeypatch.setattr(cars_live, "_rpc", rpc)
    r = cars_live.search("CHS", "2026-10-22", "2026-10-25")
    assert [c["name"] for c in r["cars"]] == ["Nissan Versa", "Ford Explorer"] and r["count"] == 3


def test_merge_hotels_attaches_live_rates_by_name():
    places = [{"name": "The Harbor Inn", "lat": 1, "lng": 2}, {"name": "Other Hotel"}]
    live = {"hotels": [{"name": "Harbor Inn", "hotel_id": "h1", "offer_id": "o1", "nightly": "$200.00", "price": "$600.00"},
                       {"name": "Live Only", "hotel_id": "h2", "offer_id": "o2", "nightly": "$150.00", "price": "$450.00"}]}
    m = trips._merge_hotels(places, live)
    assert m[0]["live"]["offer_id"] == "o1" and "live" not in m[1]
    assert m[2]["name"] == "Live Only" and m[2]["live_only"]
