"""Slack company directory: fuzzy name matching (offline, fixed member list)."""
import pytest

from jarvis_google import slack_dir as S

PEOPLE = [
    {"name": "Ben Carter", "display_name": "", "email": "ben@x.com", "title": "", "phone": "", "tz": "", "guest": False, "slack_id": "U1", "avatar": ""},
    {"name": "Denise Bueller", "display_name": "", "email": "denise@x.com", "title": "Design", "phone": "", "tz": "", "guest": False, "slack_id": "U2", "avatar": ""},
    {"name": "Rajesh Kumarasamy", "display_name": "raj", "email": "rajesh@x.com", "title": "engineer", "phone": "", "tz": "", "guest": False, "slack_id": "U3", "avatar": ""},
    {"name": "Arjun Mehta", "display_name": "", "email": "arjun@x.com", "title": "FDE", "phone": "", "tz": "", "guest": False, "slack_id": "U4", "avatar": ""},
]


@pytest.fixture(autouse=True)
def fixed_members(monkeypatch):
    monkeypatch.setattr(S, "members", lambda force=False: PEOPLE)


@pytest.mark.parametrize("q,want", [
    ("Ben", "Ben Carter"), ("carter", "Ben Carter"), ("ben carter", "Ben Carter"),
    ("rajsh kumarsamy", "Rajesh Kumarasamy"),  # misspelled both words
    ("arjun", "Arjun Mehta"), ("raj", "Rajesh Kumarasamy"),
])
def test_search_finds_person(q, want):
    assert S.search(q)[0]["name"] == want


def test_clear_hit_drops_lookalikes():
    assert [h["name"] for h in S.search("Ben")] == ["Ben Carter"]  # not "Denise Bueller"


def test_misheard_name_is_not_a_confident_match():
    assert S.search("Talman") == []
    near = S.nearest("Talman")
    assert near and near[0]["match"] < 0.9  # only a guess to ask about
