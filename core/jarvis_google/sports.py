"""Sports for JARVIS: scores, box scores, player stats, scoring plays, highlight clips and articles (ESPN public feed).

No API key. ``sports_game`` resolves a league / team / date to one game and returns a plain dict the HUD renders as a
game card; if several games match (e.g. "NFL scores" on a Sunday) it returns a scoreboard card instead, and clicking a
game there opens the full card through ``game_open``. Read-only; results are cached briefly (live games 30 s).
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from . import store

BASE = "https://site.api.espn.com/apis/site/v2/sports/"
TZ = ZoneInfo("America/New_York")  # ESPN scoreboard dates are US Eastern game days

# key -> (espn path, label). Order matters for team search when no league is given.
LEAGUES: dict[str, tuple[str, str]] = {
    "nfl": ("football/nfl", "NFL"),
    "nba": ("basketball/nba", "NBA"),
    "mlb": ("baseball/mlb", "MLB"),
    "nhl": ("hockey/nhl", "NHL"),
    "wnba": ("basketball/wnba", "WNBA"),
    "ncaaf": ("football/college-football", "College Football"),
    "ncaab": ("basketball/mens-college-basketball", "College Basketball"),
    "mls": ("soccer/usa.1", "MLS"),
    "epl": ("soccer/eng.1", "Premier League"),
    "laliga": ("soccer/esp.1", "La Liga"),
    "ucl": ("soccer/uefa.champions", "Champions League"),
}
LEAGUE_ALIASES = {
    "football": "nfl", "pro football": "nfl", "monday night football": "nfl", "sunday night football": "nfl",
    "thursday night football": "nfl", "basketball": "nba", "baseball": "mlb", "hockey": "nhl",
    "college football": "ncaaf", "cfb": "ncaaf", "ncaa football": "ncaaf", "college basketball": "ncaab",
    "cbb": "ncaab", "march madness": "ncaab", "premier league": "epl", "english premier league": "epl",
    "premiership": "epl", "soccer": "mls", "major league soccer": "mls", "la liga": "laliga",
    "champions league": "ucl", "uefa champions league": "ucl",
}
TEAM_SEARCH_ORDER = ["nfl", "nba", "mlb", "nhl", "wnba", "mls", "epl"]

_local = threading.local()
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _http() -> httpx.Client:
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = httpx.Client(timeout=12, headers={"accept": "application/json"}, follow_redirects=True)
    return c


def _get(path: str, ttl: float = 60, **params) -> Any:
    key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    r = _http().get(BASE + path, params=params or None)
    r.raise_for_status()
    data = r.json()
    with _lock:
        _cache[key] = (now, data)
    return data


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def resolve_league(q: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", (q or "").lower()).strip()
    if not s:
        return ""
    if s in LEAGUES:
        return s
    if s in LEAGUE_ALIASES:
        return LEAGUE_ALIASES[s]
    for label_key, (_, label) in LEAGUES.items():
        if s == label.lower():
            return label_key
    for alias, key in LEAGUE_ALIASES.items():
        if alias in s:
            return key
    return ""


def parse_date(s: str, today: dt.date | None = None) -> dt.date | None:
    """'2026-09-28', '9/28', 'today', 'yesterday', 'last night', 'monday' (most recent, today included)."""
    s = (s or "").strip().lower()
    today = today or dt.datetime.now(TZ).date()
    if not s:
        return None
    if s in ("today", "tonight"):
        return today
    if s in ("yesterday", "last night"):
        return today - dt.timedelta(days=1)
    if s == "tomorrow":
        return today + dt.timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)
    if m:
        y = int(m[3]) if m[3] else today.year
        return dt.date(y + 2000 if y < 100 else y, int(m[1]), int(m[2]))
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, d in enumerate(days):
        if s.replace("last ", "").startswith(d):
            back = (today.weekday() - i) % 7
            if s.startswith("last ") and back == 0:
                back = 7
            return today - dt.timedelta(days=back)
    return None


# ------------------------------------------------------------------ teams
def _teams(league: str) -> list[dict]:
    path = LEAGUES[league][0]
    d = _get(f"{path}/teams", ttl=86400, limit=1000)
    out = []
    for t in ((d.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams", []):
        t = t.get("team", t)
        out.append({"id": t.get("id"), "abbr": t.get("abbreviation", ""), "name": t.get("displayName", ""),
                    "short": t.get("shortDisplayName", ""), "nick": t.get("name") or t.get("nickname", ""),
                    "location": t.get("location", ""), "league": league})
    return out


def _team_score(q: str, t: dict) -> float:
    q = q.lower().strip()
    names = [t["name"], t["short"], t["nick"], t["location"], t["abbr"]]
    names = [n.lower() for n in names if n]
    if q in names:
        return 1.0 if q != t["abbr"].lower() or len(q) > 2 else 0.95
    if any(q == n.split()[-1] for n in names) or any(n in q.split() for n in names if len(n) > 3):
        return 0.9
    return max(difflib.SequenceMatcher(None, q, n).ratio() for n in names) * 0.85


def find_teams(q: str, league: str = "") -> list[dict]:
    q = re.sub(r"\b(the|game|match|score)\b", "", (q or "").lower()).strip()
    if not q:
        return []
    leagues = [league] if league else TEAM_SEARCH_ORDER
    with ThreadPoolExecutor(4) as ex:
        lists = list(ex.map(lambda lg: _safe(lambda: _teams(lg), []), leagues))
    scored = [(s, t) for ts in lists for t in ts if (s := _team_score(q, t)) >= 0.72]
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored]


# ------------------------------------------------------------------ helpers
def _img(x: Any) -> str:
    if isinstance(x, dict):
        return x.get("href") or x.get("url") or ""
    return x or ""


def _logo(team: dict) -> str:
    if team.get("logo"):
        return team["logo"]
    logos = team.get("logos") or []
    return _img(logos[0]) if logos else ""


def _record(c: dict) -> str:
    for key in ("record", "records"):
        rec = c.get(key)
        if isinstance(rec, list) and rec:
            r0 = next((r for r in rec if r.get("type") in ("total", "overall")), rec[0])
            return r0.get("displayValue") or r0.get("summary") or ""
        if isinstance(rec, str):
            return rec
    return ""


def _competitor(c: dict) -> dict:
    t = c.get("team") or {}
    score = c.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue")
    return {
        "id": t.get("id"), "abbr": t.get("abbreviation", ""), "name": t.get("displayName", ""),
        "short": t.get("shortDisplayName") or t.get("name", ""), "logo": _logo(t),
        "color": ("#" + t["color"]) if t.get("color") else "", "alt_color": ("#" + t["alternateColor"]) if t.get("alternateColor") else "",
        "score": score, "winner": bool(c.get("winner")), "home_away": c.get("homeAway", ""), "record": _record(c),
        "linescores": [l.get("displayValue", l.get("value")) for l in c.get("linescores") or []],
        "hits": c.get("hits"), "errors": c.get("errors"),
    }


def _status(s: dict | None) -> dict:
    t = (s or {}).get("type") or {}
    return {"state": t.get("state", ""), "completed": bool(t.get("completed")), "detail": t.get("detail") or t.get("description", ""),
            "short": t.get("shortDetail", ""), "clock": (s or {}).get("displayClock"), "period": (s or {}).get("period")}


def _game_row(e: dict, league: str) -> dict:
    co = (e.get("competitions") or [{}])[0]
    comps = sorted((_competitor(c) for c in co.get("competitors", [])), key=lambda c: c["home_away"] != "away")
    bc = [n for b in co.get("broadcasts") or [] for n in b.get("names", [])]
    return {"event_id": e.get("id"), "league": league, "name": e.get("name", ""), "short_name": e.get("shortName", ""),
            "date": e.get("date"), "status": _status(co.get("status") or e.get("status")), "teams": comps,
            "broadcast": ", ".join(dict.fromkeys(bc)), "venue": (co.get("venue") or {}).get("fullName", "")}


def scoreboard(league: str, day: dt.date | None = None) -> dict:
    path, label = LEAGUES[league]
    params = {"dates": day.strftime("%Y%m%d")} if day else {}
    d = _get(f"{path}/scoreboard", ttl=30, **params)
    games = [_game_row(e, league) for e in d.get("events", [])]
    return {"league": league, "league_label": label, "date": day.isoformat() if day else None,
            "week": (d.get("week") or {}).get("number"), "games": games}


def _team_games(team: dict) -> list[dict]:
    path = LEAGUES[team["league"]][0]
    d = _get(f"{path}/teams/{team['id']}/schedule", ttl=120)
    return [_game_row(e, team["league"]) for e in d.get("events", [])]


def _local_day(iso: str | None) -> dt.date | None:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).date()
    except ValueError:
        return None


def _pick_team_game(games: list[dict], day: dt.date | None, when: str) -> dict | None:
    if not games:
        return None
    if day:
        same = [g for g in games if _local_day(g["date"]) == day]
        return same[0] if same else None
    live = [g for g in games if g["status"]["state"] == "in"]
    if live:
        return live[0]
    past = [g for g in games if g["status"]["state"] == "post"]
    future = [g for g in games if g["status"]["state"] == "pre"]
    if when == "next":
        return future[0] if future else (past[-1] if past else None)
    return past[-1] if past else (future[0] if future else None)


# ------------------------------------------------------------------ game summary
def _team_stats(box: dict, order: list[str]) -> list[dict]:
    teams = {t["team"]["abbreviation"]: t for t in box.get("teams", []) if t.get("team")}
    if len(teams) < 2:
        return []
    a, h = (teams.get(order[0]) or {}), (teams.get(order[1]) or {})
    rows: list[dict] = []
    av = {s.get("name"): s for s in a.get("statistics", [])}
    for s in h.get("statistics", []):
        if "stats" in s:  # baseball: grouped (batting / pitching); take a few key ones
            continue
        other = av.get(s.get("name")) or {}
        rows.append({"label": s.get("label") or s.get("name"), "away": other.get("displayValue"), "home": s.get("displayValue")})
    if not rows:  # baseball-style grouped stats
        ag = {g["name"]: {x.get("name"): x for x in g.get("stats", [])} for g in a.get("statistics", []) if "stats" in g}
        for g in h.get("statistics", []):
            if "stats" not in g or g["name"] not in ("batting", "pitching"):
                continue
            want = {"batting": ["hits", "homeRuns", "RBIs", "walks", "strikeouts", "avg", "OBP", "SLGPCT"],
                    "pitching": ["ERA", "strikeouts", "walks", "pitches"]}[g["name"]]
            for x in g["stats"]:
                if x.get("name") in want:
                    o = ag.get(g["name"], {}).get(x["name"], {})
                    lab = x.get("displayName") or x.get("abbreviation") or x["name"]
                    rows.append({"label": f"{lab} ({g['name']})" if g["name"] == "pitching" else lab,
                                 "away": o.get("displayValue"), "home": x.get("displayValue")})
    return rows[:24]


def _players(box: dict) -> list[dict]:
    out = []
    for p in box.get("players", []):
        groups = []
        for g in p.get("statistics", []):
            rows = []
            for a in g.get("athletes", []):
                ath = a.get("athlete") or {}
                if not a.get("stats") or not any(str(v).strip() for v in a["stats"]):
                    continue
                pos = a.get("position") or ath.get("position") or {}
                rows.append({"id": ath.get("id"), "name": ath.get("shortName") or ath.get("displayName", ""),
                             "full": ath.get("displayName", ""), "pos": pos.get("abbreviation", "") if isinstance(pos, dict) else "",
                             "headshot": _img(ath.get("headshot")), "stats": a["stats"], "starter": a.get("starter")})
            if rows:
                groups.append({"name": g.get("name") or g.get("type") or "", "title": (g.get("text") or g.get("name") or "").title(),
                               "labels": g.get("labels") or [], "rows": rows[:30], "totals": g.get("totals") or []})
        out.append({"team": (p.get("team") or {}).get("abbreviation", ""), "groups": groups})
    return out


def _leaders(s: dict, featured: list[dict]) -> list[dict]:
    out = []
    for t in s.get("leaders", []):
        abbr = (t.get("team") or {}).get("abbreviation", "")
        for cat in t.get("leaders", []):
            top = (cat.get("leaders") or [None])[0]
            if not top:
                continue
            ath = top.get("athlete") or {}
            out.append({"team": abbr, "category": cat.get("displayName") or cat.get("name", ""),
                        "name": ath.get("displayName", ""), "headshot": _img(ath.get("headshot")),
                        "pos": (ath.get("position") or {}).get("abbreviation", ""), "value": top.get("displayValue", "")})
    for f in featured or []:  # baseball: winning / losing / saving pitcher
        ath = f.get("athlete") or {}
        stats = {x.get("abbreviation"): x.get("displayValue") for x in f.get("statistics", [])}
        val = f"{stats.get('W', '0')}-{stats.get('L', '0')}, {stats.get('ERA', '')} ERA" if "ERA" in stats else ""
        if f.get("name") == "savingPitcher":
            val = f"{stats.get('SV', '')} SV"
        out.append({"team": "", "category": f.get("displayName", ""), "name": ath.get("displayName", ""),
                    "headshot": _img(ath.get("headshot")), "pos": ath.get("position", ""), "value": val})
    return out[:12]


def _scoring(s: dict, abbr_by_id: dict[str, str]) -> list[dict]:
    plays = s.get("scoringPlays") or [p for p in s.get("plays", []) if p.get("scoringPlay")]
    if not plays and s.get("keyEvents"):
        plays = [k for k in s["keyEvents"] if k.get("scoringPlay")]
    out = []
    for p in plays:
        team = p.get("team") or {}
        out.append({"period": (p.get("period") or {}).get("number"), "clock": (p.get("clock") or {}).get("displayValue", ""),
                    "team": team.get("abbreviation") or abbr_by_id.get(str(team.get("id")), ""), "text": p.get("text", ""),
                    "away": p.get("awayScore"), "home": p.get("homeScore"),
                    "type": (p.get("scoringType") or {}).get("abbreviation") or (p.get("type") or {}).get("text", "")})
    return out[:40]


def _videos(s: dict) -> list[dict]:
    out = []
    for v in s.get("videos", []):
        src = (v.get("links") or {}).get("source") or {}
        mp4 = (src.get("HD") or {}).get("href") or src.get("href") or (src.get("mezzanine") or {}).get("href")
        web = ((v.get("links") or {}).get("web") or {}).get("href", "")
        if not (mp4 or web):
            continue
        out.append({"title": v.get("headline", ""), "description": v.get("description", ""), "duration": v.get("duration"),
                    "thumb": v.get("thumbnail", ""), "mp4": mp4 if (mp4 or "").endswith(".mp4") else "", "web": web})
    return out[:10]


def _article(a: dict) -> dict:
    links = a.get("links") or {}
    url = (links.get("web") or {}).get("href", "") if isinstance(links, dict) else ""
    imgs = a.get("images") or []
    return {"headline": a.get("headline", ""), "description": a.get("description", ""), "url": url,
            "image": _img(imgs[0]) if imgs else "", "published": a.get("published") or a.get("lastModified"),
            "type": a.get("type", ""), "byline": a.get("byline", "")}


def _articles(league: str, teams: list[dict], game_day: dt.date | None) -> list[dict]:
    path = LEAGUES[league][0]
    with ThreadPoolExecutor(2) as ex:
        lists = list(ex.map(lambda t: _safe(lambda: _get(f"{path}/news", ttl=600, team=t["abbr"].lower(), limit=8), {}), teams))
    seen, out = set(), []
    for d in lists:
        for a in (d or {}).get("articles", []):
            art = _article(a)
            if not art["url"] or art["url"] in seen:
                continue
            day = _local_day(art["published"])
            if game_day and day and day < game_day - dt.timedelta(days=1):
                continue
            seen.add(art["url"])
            out.append(art)
    out.sort(key=lambda a: a.get("published") or "", reverse=True)
    return out[:8]


def _win_prob(s: dict) -> list[float]:
    wp = [p.get("homeWinPercentage") for p in s.get("winprobability", []) if p.get("homeWinPercentage") is not None]
    if len(wp) > 80:
        step = len(wp) / 80
        wp = [wp[int(i * step)] for i in range(80)] + [wp[-1]]
    return [round(float(x), 3) for x in wp]


def _pregame(s: dict) -> float | None:
    """ESPN's pregame projection (home win chance 0-1), normalized so home + away = 1."""
    p = s.get("predictor") or {}
    try:
        h = float((p.get("homeTeam") or {}).get("gameProjection"))
        a = float((p.get("awayTeam") or {}).get("gameProjection"))
        return round(h / (h + a), 3) if h + a > 0 else None
    except (TypeError, ValueError):
        return None


def game_summary(league: str, event_id: str) -> dict:
    path, label = LEAGUES[league]
    s = _get(f"{path}/summary", ttl=30, event=event_id)
    hc = ((s.get("header") or {}).get("competitions") or [{}])[0]
    status = _status(hc.get("status"))
    if status["completed"]:  # final games don't change; keep them longer
        with _lock:
            k = f"{path}/summary?event={event_id}"
            if k in _cache:
                _cache[k] = (time.time() + 3600, _cache[k][1])
    teams = sorted((_competitor(c) for c in hc.get("competitors", [])), key=lambda c: c["home_away"] != "away")
    order = [t["abbr"] for t in teams]
    abbr_by_id = {str(t["id"]): t["abbr"] for t in teams}
    gi = s.get("gameInfo") or {}
    venue = gi.get("venue") or {}
    addr = venue.get("address") or {}
    bc = [b.get("media", {}).get("shortName") or b.get("station") for b in hc.get("broadcasts") or []]
    art = s.get("article") or {}
    recap = _article(art) if art.get("headline") else None
    if recap and not recap["url"] and art.get("links"):
        recap["url"] = ((art.get("links") or {}).get("web") or {}).get("href", "")
    game_day = _local_day(hc.get("date"))
    n_periods = max((len(t["linescores"]) for t in teams), default=0)
    sport = path.split("/")[0]
    return {
        "event_id": event_id, "league": league, "league_label": label, "sport": sport,
        "name": " at ".join(t["name"] for t in teams), "date": hc.get("date"), "status": status, "teams": teams,
        "periods": _period_labels(sport, n_periods),
        "venue": venue.get("fullName", ""), "city": ", ".join(x for x in (addr.get("city"), addr.get("state")) if x),
        "attendance": gi.get("attendance"), "broadcast": ", ".join(x for x in dict.fromkeys(bc) if x),
        "leaders": _leaders(s, (hc.get("status") or {}).get("featuredAthletes") or []),
        "team_stats": _team_stats(s.get("boxscore") or {}, order),
        "players": _players(s.get("boxscore") or {}),
        "scoring": _scoring(s, abbr_by_id),
        "win_prob": _win_prob(s),
        "pregame_prob": _pregame(s),
        "videos": _videos(s),
        "recap": recap,
        "articles": _articles(league, teams, game_day) if teams else [],
        "web_url": f"https://www.espn.com/{league if league in ('nfl', 'nba', 'mlb', 'nhl', 'wnba') else path.split('/')[0]}/game/_/gameId/{event_id}",
    }


def _period_labels(sport: str, n: int) -> list[str]:
    if sport == "baseball":
        return [str(i + 1) for i in range(n)]
    if sport == "soccer":
        return ["1H", "2H", "ET1", "ET2", "PK"][:n]
    if sport == "hockey":
        return (["1", "2", "3"] + ["OT"] + [f"{i}OT" for i in range(2, 10)])[:n] if n <= 4 else ["1", "2", "3"] + ["OT"] * (n - 3)
    base = ["1", "2", "3", "4"]  # football / basketball quarters
    if n <= 4:
        return base[:n]
    return base + (["OT"] if n == 5 else [f"OT{i}" for i in range(1, n - 3)])


# ------------------------------------------------------------------ public entry points
def sports_game(league: str = "", team: str = "", date: str = "", when: str = "", game_id: str = "",
                record: bool = True) -> dict:
    """Resolve to one game (full card) or a scoreboard (several games)."""
    lg = resolve_league(league)
    if league and not lg:
        return {"error": f"Unknown league '{league}'. Try one of: {', '.join(v[1] for v in LEAGUES.values())}."}
    day = parse_date(date)
    if date and day is None:
        return {"error": f"Couldn't read the date '{date}'. Use YYYY-MM-DD."}
    when = (when or "").lower()
    result: dict | None = None
    if game_id:
        if not lg:
            return {"error": "game_id needs a league."}
        result = game_summary(lg, str(game_id))
    elif team:
        cands = find_teams(team, lg)
        if not cands:
            return {"error": f"No team matching '{team}'" + (f" in the {LEAGUES[lg][1]}" if lg else "") + "."}
        best = None
        tried = 0
        for t in cands[:4]:
            tried += 1
            g = _safe(lambda t=t: _pick_team_game(_team_games(t), day, when))
            if g and (best is None or _prefer(g, best[1], day)):
                best = (t, g)
            if best and not lg and cands[0]["league"] == best[0]["league"] and tried >= 1 and _team_score(team, cands[0]) >= 0.95:
                break
        if not best:
            t = cands[0]
            on = f" on {day:%A %B %-d}" if day else ""
            return {"error": f"No {LEAGUES[t['league']][1]} game found for the {t['name']}{on}."}
        result = game_summary(best[0]["league"], best[1]["event_id"])
    else:
        if not lg:
            return {"error": "Tell me the league (NFL, NBA, MLB, NHL, ...) or a team."}
        sb = scoreboard(lg, day)
        games = sb["games"]
        if not games:
            on = f" on {day:%A %B %-d}" if day else " right now"
            return {"error": f"No {LEAGUES[lg][1]} games{on}."}
        if len(games) == 1:
            result = game_summary(lg, games[0]["event_id"])
        else:
            result = {**sb, "kind": "scoreboard"}
    if record and result and not result.get("error"):
        store.record_result("sports_scoreboard" if result.get("kind") == "scoreboard" else "sports_game", None,
                            {"league": league, "team": team, "date": date, "when": when}, result)
    return result


def _prefer(g: dict, other: dict, day: dt.date | None) -> bool:
    """Across leagues (no league given): prefer a live game, then the most recent one."""
    if g["status"]["state"] == "in" and other["status"]["state"] != "in":
        return True
    if other["status"]["state"] == "in":
        return False
    return (g.get("date") or "") > (other.get("date") or "")


def game_open(league: str, event_id: str) -> dict:
    """HUD click on a scoreboard row: push the full game card through the feed."""
    lg = resolve_league(league)
    r = game_summary(lg, str(event_id))
    store.record_result("sports_game", None, {"league": lg, "game_id": event_id}, r)
    return r


def brief(r: dict) -> dict:
    """Compact version for the model (the card has everything)."""
    if not isinstance(r, dict) or r.get("error"):
        return r
    if r.get("kind") == "scoreboard":
        return {"league": r.get("league_label"), "date": r.get("date"), "week": r.get("week"),
                "games": [{"event_id": g["event_id"], "matchup": g["short_name"], "status": g["status"]["short"] or g["status"]["detail"],
                           "score": " - ".join(f"{t['abbr']} {t['score'] or ''}".strip() for t in g["teams"])} for g in r["games"]],
                "card": "scoreboard shown; he can click any game for the full box score"}
    return {
        "league": r.get("league_label"), "matchup": r.get("name"), "date": r.get("date"), "status": r["status"]["detail"],
        "venue": r.get("venue"), "attendance": r.get("attendance"),
        "teams": [{k: t.get(k) for k in ("name", "abbr", "score", "record", "winner", "linescores", "home_away")} for t in r["teams"]],
        "leaders": [f"{l['team']} {l['category']}: {l['name']} {l['value']}".strip() for l in r.get("leaders", [])],
        "team_stats": [f"{s['label']}: {s['away']} / {s['home']}" for s in r.get("team_stats", [])[:10]],
        "scoring": [f"Q{p['period']} {p['clock']} {p['team']}: {p['text']}" for p in r.get("scoring", [])[:14]],
        "recap": {k: (r.get("recap") or {}).get(k) for k in ("headline", "description")} if r.get("recap") else None,
        "highlights": [v["title"] for v in r.get("videos", [])[:5]],
        "headlines": [a["headline"] for a in r.get("articles", [])[:4]],
        "card": "full game card shown (box score, player stats, scoring plays, highlight videos, articles)",
    }
