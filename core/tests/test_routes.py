"""Routes response parsing (no network)."""
from jarvis_google import routes as RT

SAMPLE = {"routes": [
    {"description": "I-287 W", "duration": "1500s", "staticDuration": "1080s", "distanceMeters": 20921,
     "routeLabels": ["DEFAULT_ROUTE"], "travelAdvisory": {"tollInfo": {}},
     "legs": [{"steps": [
         {"distanceMeters": 120, "staticDuration": "40s", "travelMode": "DRIVE",
          "navigationInstruction": {"maneuver": "DEPART", "instructions": "Head north on Fifth Ave"}},
         {"distanceMeters": 16093, "staticDuration": "900s", "travelMode": "DRIVE",
          "navigationInstruction": {"maneuver": "MERGE", "instructions": "Merge onto I-287 W"}},
         {"distanceMeters": 30, "staticDuration": "5s", "travelMode": "DRIVE", "navigationInstruction": {}},
     ]}]},
    {"description": "Hutchinson River Pkwy", "duration": "1320s", "staticDuration": "1260s", "distanceMeters": 19000,
     "legs": [{"steps": []}]},
]}

TRANSIT = {"routes": [{"duration": "2700s", "distanceMeters": 30000, "legs": [{"steps": [
    {"distanceMeters": 500, "staticDuration": "360s", "travelMode": "WALK",
     "navigationInstruction": {"instructions": "Walk to Pelham"}},
    {"distanceMeters": 25000, "staticDuration": "1800s", "travelMode": "TRANSIT",
     "transitDetails": {"headsign": "Grand Central", "stopCount": 6,
                        "stopDetails": {"departureStop": {"name": "Pelham"}, "arrivalStop": {"name": "Grand Central"}},
                        "localizedValues": {"departureTime": {"time": {"text": "8:05 AM"}}, "arrivalTime": {"time": {"text": "8:35 AM"}}},
                        "transitLine": {"name": "New Haven Line", "nameShort": "NH", "color": "#ee0034",
                                        "vehicle": {"name": {"text": "Train"}}}}},
]}]}]}


def test_driving_traffic_and_steps():
    r = RT.parse(SAMPLE, "driving")
    assert len(r) == 2
    a = r[0]
    assert a["duration"] == "25 min" and a["typical"] == "18 min" and a["delay_min"] == 7
    assert a["traffic"] == "heavy"  # 7 min on 18 = 39%
    assert a["distance"] == "13 mi" and a["tolls"] is True
    assert [s["text"] for s in a["steps"]] == ["Head north on Fifth Ave", "Merge onto I-287 W"]  # empty step dropped
    assert a["steps"][1]["distance"] == "10.0 mi" and a["steps"][1]["duration"] == "15 min"
    assert a["steps"][0]["distance"] == "390 ft"
    assert r[1]["traffic"] == "light" and r[1]["delay_min"] == 1


def test_transit_leg():
    r = RT.parse(TRANSIT, "transit")[0]
    t = r["steps"][1]["transit"]
    assert t["line"] == "NH" and t["vehicle"] == "Train" and t["stops"] == 6 and t["depart"] == "8:05 AM"
    assert r["traffic"] == "" and r["duration"] == "45 min"


def test_durations():
    assert RT._dur(20) == "1 min" and RT._dur(3600) == "1 hr" and RT._dur(5400) == "1 hr 30 min"
