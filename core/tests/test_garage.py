"""Garage (his CL600): maintenance due math, document search, logging, plans, card mapping (no network)."""
import datetime as dt
import json

import pytest

from jarvis_core import visuals as V
from jarvis_google import garage as GA, store


@pytest.fixture()
def gar(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    g = tmp_path / "garage"
    for name, val in {"G": g, "FILES": g / "files", "TEXT": g / "text", "DOCS": g / "docs.json",
                      "PROFILE": g / "profile.json", "PLANS": g / "plans.json", "LOG": g / "log.json",
                      "FACTS": g / "owner_facts.json"}.items():
        monkeypatch.setattr(GA, name, val)
    GA._chunks_cache.clear()
    today = dt.date.today()
    GA._save(GA.PROFILE, {
        "vehicle": {"year": 2003, "model": "CL600", "current_mileage": 80000,
                    "mileage_as_of": (today - dt.timedelta(days=365)).isoformat()},
        "configuration_notes": ["ABC deleted for coil-overs"],
        "service_history": [{"date": "2024-10-19", "mileage": 79000, "title": "Oil change", "items": ["oil"], "cost": 120}],
        "mods": [{"name": "Eurocharged Stage 1 tune"}],
        "maintenance": [
            {"item": "Engine oil", "interval_miles": 5000, "last_mileage": 76000, "last_date": (today - dt.timedelta(days=30)).isoformat()},
            {"item": "Brake fluid", "interval_months": 24, "last_date": (today - dt.timedelta(days=200)).isoformat()},
            {"item": "Spark plugs", "interval_miles": 60000}],
    })
    GA._save(GA.DOCS, [{"id": "doc1", "name": "Fuse chart.pdf", "title": "2003 CL600 Fuse Chart", "doc_type": "diagram",
                        "summary": "fuse assignments", "facts": ["Fuse 12: COMAND head unit 15A"], "file": "doc1.pdf",
                        "link": "https://drive/x"}])
    (g / "text").mkdir(parents=True, exist_ok=True)
    (g / "text" / "doc1.txt").write_text("[page 3]\nFuse 12 15A COMAND / radio head unit\nFuse 13 10A instrument cluster")
    GA._save(GA.DOCS, GA._load(GA.DOCS, []) + [{"id": "doc2", "name": "Oil change.pdf", "title": "Oil service invoice",
                                                "file": "doc2.pdf", "summary": "oil service"}])
    (g / "text" / "doc2.txt").write_text("[page 1]\nROWE 5W40 9 x 13.22\n275 180 00 09, Oil Filter Kit 22.00\nLabor 95.00")
    return g


def test_maintenance_status(gar):
    p = GA.profile()
    m = {x["item"]: x for x in p["maintenance"]}
    assert p["estimated_mileage"] == 80000  # last recorded reading, never extrapolated
    assert m["Engine oil"]["status"] == "ok" and m["Engine oil"]["miles_left"] == 1000
    assert m["Brake fluid"]["months_left"] > 0
    assert m["Brake fluid"]["status"] == "ok" and m["Spark plugs"]["status"] == "unknown"


def test_search_cites_pages(gar):
    with store.capture() as items:
        r = GA.search("head unit fuse")
    assert r["hits"][0]["doc"] == "2003 CL600 Fuse Chart" and r["hits"][0]["page"] == 3
    assert "Fuse 12" in r["hits"][0]["excerpt"]
    assert V.cards_from_feed(items[0])[0]["kind"] == "garage_search"


def test_car_profile_card_and_due(gar):
    with store.capture() as items:
        r = GA.car_profile("maintenance")
    assert r["last_recorded_mileage"] == 80000 and "ABC" in " ".join(r["owner_facts"])
    c = V.cards_from_feed(items[0])[0]
    assert c["kind"] == "garage" and c["data"]["tab"] == "maintenance" and c["data"]["key"] == "garage:cl600"


def test_log_work_updates_history_and_mileage(gar):
    GA.log_work("Oil change", date=dt.date.today().isoformat(), mileage=86000, vendor="Family Auto", cost=140)
    p = GA.profile()
    assert p["service_history"][0]["title"] == "Oil change" and p["vehicle"]["current_mileage"] == 86000
    assert json.loads(GA.LOG.read_text())[0]["source"] == "logged"


def test_plan_totals(gar):
    with store.capture() as items:
        r = GA.plan_save("Wheels", "Lighter wheels", [{"name": "Wheels", "items": [{"part": "Forged 19s", "est_cost": 3200},
                                                                                  {"part": "Tires", "est_cost": 1400}]},
                                                       {"name": "Alignment", "items": [{"part": "Alignment", "est_cost": 150}]}])
    assert r["total"] == 4750 and r["stages"] == 2
    assert V.cards_from_feed(items[0])[0]["kind"] == "garage_plan"


def test_no_profile_explains(tmp_path, monkeypatch):
    monkeypatch.setattr(GA, "PROFILE", tmp_path / "none.json")
    monkeypatch.setattr(GA, "FACTS", tmp_path / "facts.json")
    with pytest.raises(RuntimeError, match="car_sync"):
        GA.profile()


def test_costs_grounded_in_receipts():
    links = {"coil": {"title": "Coil packs + 24 spark plugs", "total_cost": 5000.0, "doc_type": "receipt"},
             "ac": {"title": "A/C compressor", "summary": "replace a/c compressor clutch", "total_cost": 2057.32, "doc_type": "invoice"},
             "sum": {"title": "Services performed (insurance)", "summary": "coil packs compressor", "total_cost": 17446.33, "doc_type": "other"},
             "trans": {"title": "Transmission service", "total_cost": 675.57, "doc_type": "invoice"},
             "tune": {"title": "Eurocharged stage 1 tune", "total_cost": 1039.2, "doc_type": "receipt"},
             "oil1": {"title": "Oil service", "total_cost": 450.0, "doc_type": "invoice"},
             "oil2": {"title": "Oil service photo", "total_cost": 450.0, "doc_type": "receipt"},
             "est": {"title": "Collision estimate", "total_cost": 8775.72, "doc_type": "estimate"},
             "bos": {"title": "Bill of sale", "total_cost": 12000.0, "doc_type": "receipt"}}
    prof = {"service_history": [
        {"title": "Coil packs and spark plugs", "cost": 17446, "doc_ids": ["coil", "ac", "sum"]},
        {"title": "A/C compressor and clutch", "cost": 17446, "doc_ids": ["ac", "sum"]},
        {"title": "Transmission service and Eurocharged tune", "cost": 1039.2, "doc_ids": ["trans", "tune"]},
        {"title": "Oil service", "cost": 999, "doc_ids": ["oil1", "oil2"]},
        {"title": "Collision estimate", "cost": 1, "doc_ids": ["est"]},
        {"title": "Vehicle Purchase", "cost": 12000, "doc_ids": ["bos"]},
        {"title": "No paperwork", "cost": 300, "doc_ids": []}]}
    GA._ground_costs(prof, links)
    assert [e["cost"] for e in prof["service_history"]] == [5000.0, 2057.32, 1714.77, 450.0, 8775.72, 12000.0, None]
    assert prof["service_history"][4]["estimate"] and prof["service_history"][5]["purchase"]
    assert prof["totals"]["documented_spend"] == 5000 + 2057.32 + 1714.77 + 450


def test_owner_facts_seeded_and_added(gar):
    assert any("Strutmasters" in f for f in GA.owner_facts())
    GA.add_fact("Running 19-inch AMG Monoblocks.")
    assert GA.owner_facts()[-1] == "Running 19-inch AMG Monoblocks."


def test_search_part_number_ignores_spacing(gar):
    for q in ("275 180 00 09", "2751800009", "275-180-00-09"):
        h = GA.search(q, record=False)["hits"][0]
        assert h["doc"] == "Oil service invoice" and "Oil Filter Kit" in h["excerpt"], q


def test_plan_accepts_loose_model_shapes(gar):
    r = GA.plan_save("Brakes", "Bigger brakes", [
        {"name": "Stage 1", "description": "AMG 8-pot", "est_cost": "$1,800 - $2,600", "parts": ["Calipers", "Rotors"]},
        {"name": "Stage 2", "items": [{"name": "Wheels", "est_cost": "2.4k-3.8k"}, {"part": "Caps", "price": 60}]}])
    plan = json.loads(GA.PLANS.read_text())[-1]
    assert [s["est_total"] for s in plan["stages"]] == [1800, 2460]
    assert plan["stages"][0]["items"][0]["part"] == "Calipers" and plan["stages"][0]["notes"] == "AMG 8-pot"
    assert r["total"] == 4260 and r["total_high"] == 6460
