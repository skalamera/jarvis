"""Sports (ESPN) parsing: league/date/team resolution, game summary shape, cards, model brief. Offline (fixtures)."""
import datetime as dt

import pytest

from jarvis_core import visuals
from jarvis_google import sports as S

MON = dt.date(2026, 9, 30)  # a Wednesday


@pytest.mark.parametrize("q,key", [("nfl", "nfl"), ("Monday Night Football", "nfl"), ("premier league", "epl"),
                                   ("College Football", "ncaaf"), ("hockey", "nhl"), ("", ""), ("curling", "")])
def test_resolve_league(q, key):
    assert S.resolve_league(q) == key


@pytest.mark.parametrize("s,want", [("monday", dt.date(2026, 9, 28)), ("yesterday", dt.date(2026, 9, 29)),
                                    ("last night", dt.date(2026, 9, 29)), ("wednesday", MON),
                                    ("last wednesday", dt.date(2026, 9, 23)), ("2026-09-28", dt.date(2026, 9, 28)),
                                    ("9/28", dt.date(2026, 9, 28)), ("today", MON), ("nonsense", None)])
def test_parse_date(s, want):
    assert S.parse_date(s, today=MON) == want


TEAMS = [{"id": "3", "abbr": "CHI", "name": "Chicago Bears", "short": "Bears", "nick": "Bears", "location": "Chicago", "league": "nfl"},
         {"id": "21", "abbr": "PHI", "name": "Philadelphia Eagles", "short": "Eagles", "nick": "Eagles", "location": "Philadelphia", "league": "nfl"},
         {"id": "19", "abbr": "NYG", "name": "New York Giants", "short": "Giants", "nick": "Giants", "location": "New York", "league": "nfl"}]


def test_find_teams(monkeypatch):
    monkeypatch.setattr(S, "_teams", lambda lg: TEAMS if lg == "nfl" else [])
    assert S.find_teams("Bears", "nfl")[0]["abbr"] == "CHI"
    assert S.find_teams("the eagles game")[0]["abbr"] == "PHI"
    assert S.find_teams("chicago bears")[0]["abbr"] == "CHI"
    assert S.find_teams("zzzz") == []


SUMMARY = {
    "header": {"competitions": [{"date": "2026-09-29T00:15Z", "status": {"type": {"state": "post", "completed": True, "detail": "Final"}},
        "competitors": [
            {"homeAway": "home", "winner": True, "score": "27", "linescores": [{"displayValue": "7"}, {"displayValue": "3"}, {"displayValue": "10"}, {"displayValue": "7"}],
             "record": [{"type": "total", "displayValue": "2-1"}], "team": {"id": "3", "abbreviation": "CHI", "displayName": "Chicago Bears", "color": "0b1c3a", "logos": [{"href": "chi.png"}]}},
            {"homeAway": "away", "winner": False, "score": "7", "linescores": [{"displayValue": "0"}, {"displayValue": "7"}, {"displayValue": "0"}, {"displayValue": "0"}],
             "record": [{"type": "total", "displayValue": "2-1"}], "team": {"id": "21", "abbreviation": "PHI", "displayName": "Philadelphia Eagles", "color": "06424d", "logos": [{"href": "phi.png"}]}}]}]},
    "gameInfo": {"venue": {"fullName": "Soldier Field", "address": {"city": "Chicago", "state": "IL"}}, "attendance": 59588},
    "boxscore": {
        "teams": [{"team": {"abbreviation": "PHI"}, "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "248"}]},
                  {"team": {"abbreviation": "CHI"}, "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "375"}]}],
        "players": [{"team": {"abbreviation": "CHI"}, "statistics": [{"name": "passing", "labels": ["C/ATT", "YDS"], "totals": ["24/34", "247"],
                     "athletes": [{"athlete": {"id": "1", "displayName": "Case Keenum", "headshot": {"href": "k.png"}}, "stats": ["24/34", "247"]},
                                  {"athlete": {"id": "2", "displayName": "Nobody"}, "stats": []}]}]}]},
    "leaders": [{"team": {"abbreviation": "CHI"}, "leaders": [{"displayName": "Passing Yards", "leaders": [
        {"displayValue": "24/34, 247 YDS, 2 TD", "athlete": {"displayName": "Case Keenum", "position": {"abbreviation": "QB"}}}]}]}],
    "scoringPlays": [{"period": {"number": 1}, "clock": {"displayValue": "8:47"}, "team": {"abbreviation": "CHI"},
                      "text": "8 Yd pass", "awayScore": 0, "homeScore": 7, "scoringType": {"abbreviation": "TD"}}],
    "winprobability": [{"homeWinPercentage": 0.35}, {"homeWinPercentage": 0.6}, {"homeWinPercentage": 1.0}],
    "videos": [{"headline": "Bears strike first", "duration": 18, "thumbnail": "t.jpg",
                "links": {"source": {"HD": {"href": "https://espnmedia-cdn.akamaized.net/x.mp4"}}, "web": {"href": "https://espn.com/v"}}}],
    "article": {"headline": "Keenum drives Chicago", "description": "Bears beat Eagles 27-7", "links": {"web": {"href": "https://espn.com/recap"}}},
}


def test_game_summary_shape(monkeypatch):
    monkeypatch.setattr(S, "_get", lambda path, ttl=60, **p: SUMMARY)
    monkeypatch.setattr(S, "_articles", lambda lg, teams, day: [{"headline": "A", "url": "u"}])
    r = S.game_summary("nfl", "401872963")
    assert [t["abbr"] for t in r["teams"]] == ["PHI", "CHI"]  # away first
    assert r["teams"][1]["winner"] and r["teams"][1]["linescores"] == ["7", "3", "10", "7"]
    assert r["periods"] == ["1", "2", "3", "4"]
    assert r["team_stats"] == [{"label": "Total Yards", "away": "248", "home": "375"}]
    assert r["players"][0]["groups"][0]["rows"][0]["name"] == "Case Keenum"
    assert len(r["players"][0]["groups"][0]["rows"]) == 1  # empty stat lines dropped
    assert r["leaders"][0]["value"].startswith("24/34")
    assert r["scoring"][0]["type"] == "TD" and r["videos"][0]["mp4"].endswith(".mp4")
    assert r["recap"]["url"] == "https://espn.com/recap" and r["city"] == "Chicago, IL"
    b = S.brief(r)
    assert b["teams"][1]["score"] == "27" and "Passing Yards" in b["leaders"][0] and b["highlights"] == ["Bears strike first"]
    cards = visuals.cards_from_feed({"tool": "sports_game", "result": r})
    assert cards[0]["kind"] == "sports_game" and "PHI vs CHI" in cards[0]["title"]


def test_period_labels():
    assert S._period_labels("football", 5) == ["1", "2", "3", "4", "OT"]
    assert S._period_labels("baseball", 10)[-1] == "10"
    assert S._period_labels("soccer", 2) == ["1H", "2H"]


def test_scoreboard_card_and_errors(monkeypatch):
    sb = {"league": "mlb", "league_label": "MLB", "date": "2026-09-29", "games": [{"event_id": "1"}, {"event_id": "2"}]}
    monkeypatch.setattr(S, "scoreboard", lambda lg, day=None: sb)
    r = S.sports_game(league="mlb", date="2026-09-29", record=False)
    assert r["kind"] == "scoreboard"
    assert visuals.cards_from_feed({"tool": "sports_scoreboard", "result": r})[0]["kind"] == "sports_scoreboard"
    assert "Unknown league" in S.sports_game(league="curling", record=False)["error"]
    assert "Couldn't read the date" in S.sports_game(league="nfl", date="soonish", record=False)["error"]
    assert "league" in S.sports_game(record=False)["error"]
