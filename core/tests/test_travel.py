"""Travel & dining: search shapes, confirm-before-book, executors (Duffel / Resy faked)."""
import json

import pytest

from jarvis_core import visuals as V
from jarvis_google import store, travel as TR


@pytest.fixture()
def tv(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    monkeypatch.setattr(TR, "PROFILE", tmp_path / "traveler.json")
    monkeypatch.setenv("DUFFEL_ACCESS_TOKEN", "duffel_test_x")
    return tmp_path


def _seg(o, d, dep, arr, num="100"):
    return {"origin": {"iata_code": o}, "destination": {"iata_code": d}, "departing_at": dep, "arriving_at": arr,
            "marketing_carrier": {"iata_code": "AA"}, "marketing_carrier_flight_number": num,
            "passengers": [{"cabin_class_marketing_name": "Economy", "baggages": [{"type": "checked", "quantity": 1}]}]}


def _offer(oid, origin, price, stops=0):
    segs = [_seg(origin, "MIA", "2026-10-22T16:15:00", "2026-10-22T19:11:00")]
    if stops:
        segs = [_seg(origin, "CLT", "2026-10-22T08:00:00", "2026-10-22T10:00:00", "1"),
                _seg("CLT", "MIA", "2026-10-22T11:00:00", "2026-10-22T13:00:00", "2")]
    return {"id": oid, "total_amount": str(price), "total_currency": "USD", "owner": {"name": "American", "iata_code": "AA"},
            "slices": [{"origin": {"iata_code": origin, "city_name": "New York"}, "destination": {"iata_code": "MIA"},
                        "duration": "PT2H56M", "segments": segs}],
            "conditions": {"refund_before_departure": {"allowed": True}}, "passengers": [{"id": "pas_1"}]}


def test_flights_prefer_laguardia_and_nonstop(tv, monkeypatch):
    calls = []

    def duffel(method, path, body=None, params=None, timeout=60):
        calls.append((path, body))
        org = body["slices"][0]["origin"]
        return {"offers": {"LGA": [_offer("o_lga", "LGA", 160), _offer("o_lga_stop", "LGA", 120, stops=1)],
                           "JFK": [_offer("o_jfk", "JFK", 150)], "EWR": [_offer("o_ewr", "EWR", 140)]}[org]}
    monkeypatch.setattr(TR, "_duffel", duffel)
    with store.capture() as items:
        r = TR.flights_search("MIA", "2026-10-22")
    assert [c[1]["slices"][0]["origin"] for c in calls] == ["LGA", "JFK", "EWR"]
    # LGA nonstop $160 beats JFK $150 (+30), EWR $140 (+45) and the cheaper 1-stop LGA $120 (+40)
    assert [b["offer_id"] for b in r["best"]][:2] == ["o_lga", "o_lga_stop"]
    assert V.cards_from_feed(items[0])[0]["kind"] == "travel_flights"


def test_flight_book_needs_profile_then_confirms(tv, monkeypatch):
    monkeypatch.setattr(TR, "_duffel", lambda m, p, b=None, params=None, timeout=60: _offer("o1", "LGA", 92.2))
    with pytest.raises(ValueError, match="given_name"):
        TR.flight_book("o1")
    TR.traveler_profile_set("Tony", "Stark", "1980-07-24", "m", "mr", "t@example.com", "4155550123")
    p = TR.flight_book("o1")
    assert p["status"] == "awaiting_user_confirmation"  # nothing booked yet
    a = store.get_action(p["action_id"])
    assert a["preview"]["type"] == "booking" and a["preview"]["total"] == "$92.20" and a["preview"]["test_mode"]
    assert "Refundable" in a["preview"]["policy"]
    assert json.loads((tv / "traveler.json").read_text())["phone_number"] == "+14155550123"


def test_flight_executor_books_with_profile(tv, monkeypatch):
    TR.traveler_profile_set("Tony", "Stark", "1980-07-24", "m", "mr", "t@example.com", "+14155550123")
    sent = {}

    def duffel(m, path, body=None, params=None, timeout=60):
        if path == "/air/orders":
            sent.update(body)
            return {"id": "ord_1", "booking_reference": "ABC123", "total_amount": "92.20", "total_currency": "USD",
                    "slices": _offer("o1", "LGA", 92.2)["slices"]}
        return _offer("o1", "LGA", 92.2)
    monkeypatch.setattr(TR, "_duffel", duffel)
    p = TR.flight_book("o1")
    with store.capture():
        out = store.execute_action(p["action_id"], "test")
    assert out["ok"], out
    assert out["result"]["reference"] == "ABC123"
    assert sent["passengers"][0]["born_on"] == "1980-07-24" and sent["payments"][0]["amount"] == "92.2"


def test_restaurant_search_and_book_proposal(tv, monkeypatch):
    class R:
        def __init__(self, j, code=200):
            self._j, self.status_code, self.text = j, code, json.dumps(j)

        def json(self):
            return self._j
    slots = [{"date": {"start": f"2026-10-03 {t}:00"}, "config": {"type": "Dining Room", "token": f"tok{t}"}}
             for t in ("17:30", "19:15", "19:45", "21:00")]
    monkeypatch.setattr(TR, "_where", lambda loc: {"name": "New Rochelle", "lat": 40.91, "lng": -73.78})
    monkeypatch.setattr(TR.httpx, "post", lambda url, **kw: R({"search": {"hits": [{
        "id": {"resy": 64846}, "name": "Encore Bistro", "cuisine": ["French"], "price_range_id": 2,
        "rating": {"average": 4.8, "count": 2500}, "availability": {"slots": slots}, "_geoloc": {"lat": 40.93, "lng": -73.75},
        "location": {"url_slug": "larchmont-ny"}, "url_slug": "encore-bistro"}]}}) if "venuesearch" in url else R({
        "cancellation": {"display": {"policy": ["Cancel 24h ahead."]}, "fee": None}, "payment": {"amounts": {"total": 0}}}))
    monkeypatch.setattr(TR.httpx, "get", lambda url, **kw: R({"results": {"venues": [{"slots": slots,
                                                                                      "venue": {"name": "Encore Bistro"}}]}}))
    with store.capture() as items:
        r = TR.restaurants_search("", "", "2026-10-03", "19:30", 2)
    assert r["options"][0]["times"][:2] == ["7:15 PM", "7:45 PM"]
    assert V.cards_from_feed(items[0])[0]["kind"] == "travel_restaurants"
    p = TR.restaurant_book(64846, "2026-10-03", "19:30", 2)
    a = store.get_action(p["action_id"])
    assert a["params"]["config_token"] == "tok19:15" and a["preview"]["total"] == "No charge to reserve"
    assert a["preview"]["policy"] == "Cancel 24h ahead."


def test_resy_booking_needs_login(tv, monkeypatch):
    monkeypatch.delenv("RESY_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(TR, "_env", lambda n: "")
    with pytest.raises(RuntimeError, match="RESY_REFRESH_TOKEN"):
        TR._resy_headers(auth=True)


def test_missing_duffel_token_is_explained(tv, monkeypatch):
    monkeypatch.setattr(TR, "_env", lambda n: "")
    with pytest.raises(RuntimeError, match="DUFFEL_ACCESS_TOKEN"):
        TR.test_mode()



class _R:
    def __init__(self, j, code=200):
        self._j, self.status_code, self.text = j, code, json.dumps(j)

    def json(self):
        return self._j


def _resy_list():
    return {"reservations": [
        {"reservation_id": 1, "resy_token": "tok_old", "day": "2020-01-01", "time_slot": "19:00:00", "num_seats": 2,
         "venue": {"id": 9}, "cancellation": {"allowed": False}, "status": {"finished": 1}},
        {"reservation_id": 2, "resy_token": "tok_new", "day": "2099-10-02", "time_slot": "19:45:00", "num_seats": 2,
         "venue": {"id": 7}, "cancellation": {"allowed": True, "fee": {"amount": 25}},
         "cancellation_policy": ["Cancel 24h ahead or a $25 fee applies."], "status": {"finished": 0}}],
        "venues": {"7": {"name": "Encore Bistro", "images": ["x.jpg"], "location": {"address_1": "1 Main",
                                                                                  "locality": "Larchmont"}}}}


def test_reservations_list_and_cancel_needs_confirmation(tv, monkeypatch):
    monkeypatch.setattr(TR, "_resy_token", lambda force=False: "auth")
    monkeypatch.setattr(TR, "_duffel_bookings", lambda: [])
    monkeypatch.setattr(TR.httpx, "get", lambda url, **kw: _R(_resy_list()))
    posted = []
    monkeypatch.setattr(TR.httpx, "post", lambda url, **kw: posted.append((url, kw.get("data"))) or _R({}))
    with store.capture() as items:
        r = TR.reservations_list()
    assert [x["name"] for x in r["reservations"]] == ["Encore Bistro"]  # past / finished ones are hidden
    assert V.cards_from_feed(items[0])[0]["kind"] == "travel_reservations"
    p = TR.reservation_cancel("2", "resy")
    assert p["status"] == "awaiting_user_confirmation" and not posted  # nothing cancelled yet
    a = store.get_action(p["action_id"])
    assert a["preview"]["total"] == "$25" and "24h" in a["preview"]["policy"] and a["preview"]["danger"]
    with store.capture() as done:
        out = store.execute_action(p["action_id"], "test")
    assert out["ok"], out
    assert posted == [("https://api.resy.com/3/cancel", {"resy_token": "tok_new"})]
    assert V.cards_from_feed(done[-1])[0]["title"].startswith("Cancelled")


def test_cancel_unknown_reservation_is_refused(tv, monkeypatch):
    monkeypatch.setattr(TR, "_resy_token", lambda force=False: "auth")
    monkeypatch.setattr(TR.httpx, "get", lambda url, **kw: _R(_resy_list()))
    with pytest.raises(ValueError, match="reservations_list"):
        TR.reservation_cancel("1", "resy")  # the past one


def test_persona_forbids_credential_workarounds():
    from jarvis_core import persona as P
    txt = P.build_instructions("Stephen", "America/New_York")
    assert "NEVER work around these tools" in txt and "~/.hermes/.env" in txt
