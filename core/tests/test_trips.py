"""Trip planner: storage/versioning, enrichment from real place lists, PDF HTML, and the click-only export rule."""
import json

import pytest

from jarvis_google import trips


@pytest.fixture()
def tdir(tmp_path, monkeypatch):
    monkeypatch.setattr(trips, "DIR", tmp_path / "trips")
    monkeypatch.setattr(trips.store, "record_result", lambda *a, **k: 0)
    return tmp_path


def _trip(tid="abc12345"):
    g = {"hotels": [{"name": "Harbor Inn", "photo": "http://p/h.jpg", "rating": 4.5, "short_address": "1 Bay St",
                     "est_nightly": "$200"}],
         "sights": [{"name": "Old Fort", "photo": "http://p/f.jpg", "rating": 4.7}],
         "dining": [{"name": "Crab Shack", "photo": None, "rating": 4.2}], "nightlife": [],
         "weather": {"source": "typical", "days": []}, "research": {"events": [{"name": "Oyster Fest", "date": "2026-10-24"}]}}
    plan = {"title": "Bay Weekend", "summary": "s", "itineraries": [
        {"id": "A", "name": "Classic", "hotel": "harbor inn", "est_total": "$900",
         "days": [{"date": "2026-10-24", "title": "Arrive", "items": [
             {"time": "08:00", "type": "breakfast", "title": "Eat", "place": "Crab Shack!", "detail": "d", "cost": "$20"},
             {"time": "10:00", "type": "activity", "title": "Fort", "place": "Old Fort", "detail": "<b>x</b>", "cost": "free"},
             {"time": "12:00", "type": "transit", "title": "Drive", "place": "", "detail": "", "cost": ""}]}]}]}
    b = {"destination": "Bayville", "origin": "New Rochelle, NY", "start_date": "2026-10-24", "end_date": "2026-10-25",
         "nights": 1, "adults": 2, "budget": "moderate", "themes": [], "avoid": [], "must_do": [], "assumptions": []}
    return trips._assemble(tid, "a bay weekend", b, g, plan, [{"at": 0, "change": "Planned"}], None)


def test_enrich_matches_places_by_normalized_name(tdir):
    t = _trip()
    it = t["itineraries"][0]
    assert it["hotel_info"]["photo"] == "http://p/h.jpg"
    items = it["days"][0]["items"]
    assert items[0]["rating"] == 4.2            # "Crab Shack!" matched "Crab Shack"
    assert items[1]["photo"] == "http://p/f.jpg"
    assert "photo" not in items[2]              # transit has no place


def test_versions_and_undo(tdir):
    t = trips._save(_trip())
    t["itineraries"][0]["hotel"] = "Other"
    trips._save(t)
    assert len(json.loads(trips._path(t["id"]).read_text())["versions"]) == 2
    back = trips.trip_undo(t["id"])
    assert back["itineraries"][0]["hotel"] == "harbor inn"
    with pytest.raises(ValueError):
        trips.trip_undo(t["id"])


def test_card_strips_internal_fields(tdir):
    c = trips._card(trips._save(_trip()))
    assert c["kind"] == "trip" and c["key"] == "trip:abc12345"
    assert "versions" not in c and "_gathered" not in c


def test_choose_and_latest(tdir):
    trips._save(_trip("first0001"))
    trips._save(_trip("second001"))
    assert trips._load("")["id"] == "second001"
    assert trips.trip_choose("first0001", 0)["chosen"] == 0
    assert trips.trip_choose("first0001", 9)["chosen"] is None


def test_bad_ids_rejected(tdir):
    for bad in ("../etc", "a", "x/y"):
        with pytest.raises(ValueError):
            trips._path(bad)


def test_html_escapes_and_lists_days(tdir):
    h = trips._html(_trip(), 0)
    assert "Saturday, October 24" in h and "&lt;b&gt;x&lt;/b&gt;" in h and "Oyster Fest" in h


def test_progress_file_roundtrip(tdir):
    trips._step("prog00001", "one")
    trips._step("prog00001", "two", done=True)
    p = trips.trip_progress("prog00001")
    assert p["steps"] == ["one", "two"] and p["done"]


def test_export_is_click_only():
    src = open(trips.__file__.replace("trips.py", "server.py")).read()
    assert "trip_export" not in src and "trip_undo" not in src
    assert set(trips.CLICK_OPS) == {"trip_choose", "trip_undo", "trip_export"}


def test_overlaps_counts_cross_itinerary_repeats_only():
    def it(i, hotel, places):
        return {"id": i, "hotel": hotel, "days": [{"items": [{"type": "dinner", "place": p} for p in places]
                                                    + [{"type": "hotel", "place": hotel}]}]}
    plan = {"itineraries": [it("A", "Inn", ["Husk", "Fort", "Husk"]), it("B", "Inn", ["husk!", "Pier"]),
                            it("C", "Lodge", ["Fort", "Market"])]}
    o = trips.overlaps(plan)
    assert o == {"Husk": ["A", "B"], "Fort": ["A", "C"]}      # repeats within A and the shared hotel don't count
    assert trips.overlaps(plan, must=["Fort"]) == {"Husk": ["A", "B"]}


def test_gather_end_to_end_with_stubbed_sources(tdir, monkeypatch):
    """The whole research step runs (guards against wiring slips like a list popped twice)."""
    monkeypatch.setattr(trips, "_places", lambda q, n=8: [{"name": f"{q[:12]} #{i}", "lat": 1.0, "lng": 2.0} for i in range(3)])
    monkeypatch.setattr(trips, "_weather", lambda *a: None)
    monkeypatch.setattr(trips, "_research", lambda *a, **k: {"events": [], "climate": {"summary": "mild"}})
    monkeypatch.setattr(trips, "_live_flights", lambda b: None)
    monkeypatch.setattr(trips, "_live_cars", lambda b: {"cars": [{"name": "Kia"}], "source": "octotrip"})
    monkeypatch.setattr(trips, "_live_hotels", lambda b, h: {"count": 1, "hotels": [
        {"name": "Live Inn", "hotel_id": "h9", "offer_id": "o9", "nightly": "$99.00", "price": "$198.00"}]})
    b = {"destination": "Bayville", "budget": "moderate", "themes": [], "start_date": "2026-11-12", "end_date": "2026-11-14"}
    g = trips._gather("gather001", b)
    assert len(g["hotels"]) == 7 and g["hotels"][-1]["live"]["offer_id"] == "o9"   # 3 + 3 Places + 1 live-only
    assert g["live_cars"]["cars"] and g["weather"]["source"] == "typical" and g["hotels_live_at"]
