"""Weather for the HUD: Open-Meteo (no API key). Geocodes a place name, or uses the Mac's IP location when
no place is given, and returns current conditions + next 24h + multi-day forecast in one compact result."""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

UA = {"User-Agent": "JARVIS/0.1 (personal assistant)"}

# WMO weather interpretation codes -> (condition, icon key)
WMO: dict[int, tuple[str, str]] = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "partly"), 3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"), 57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Light showers", "rain"), 81: ("Showers", "rain"), 82: ("Violent showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Severe thunderstorm, hail", "storm"),
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _wmo(code: Any) -> tuple[str, str]:
    try:
        return WMO.get(int(code), ("Unknown", "cloudy"))
    except (TypeError, ValueError):
        return ("Unknown", "cloudy")


def _here(http: httpx.Client) -> dict:
    """Approximate location of this Mac from its public IP (two providers for resilience)."""
    try:
        j = http.get("http://ip-api.com/json/", timeout=6).json()
        if j.get("status") == "success":
            return {"name": j["city"], "region": j.get("regionName", ""), "country": j.get("countryCode", ""),
                    "lat": j["lat"], "lon": j["lon"], "source": "ip"}
    except Exception:
        pass
    j = http.get("https://ipinfo.io/json", timeout=6).json()
    lat, lon = (float(x) for x in j["loc"].split(","))
    return {"name": j.get("city", ""), "region": j.get("region", ""), "country": j.get("country", ""),
            "lat": lat, "lon": lon, "source": "ip"}


def _geocode(http: httpx.Client, place: str) -> dict:
    parts = [p.strip() for p in place.split(",") if p.strip()]
    name, qual = parts[0], [p.lower() for p in parts[1:]]
    qual = [US_STATES.get(q.upper(), q).lower() for q in qual]
    res = http.get("https://geocoding-api.open-meteo.com/v1/search",
                   params={"name": name, "count": 15, "language": "en"}, timeout=10).json().get("results") or []
    if not res and len(name.split()) > 1:  # "Pelham Manor NY" style without commas
        return _geocode(http, ", ".join([" ".join(name.split()[:-1]), name.split()[-1], *parts[1:]]))
    if not res:
        raise ValueError(f"Couldn't find a place called '{place}'.")

    def score(r: dict) -> tuple:
        fields = {str(r.get(k, "")).lower() for k in ("admin1", "admin2", "country", "country_code")}
        matched = sum(any(q == f or q in f for f in fields) for q in qual)
        return (matched, r.get("population") or 0)

    best = max(res, key=score)
    if qual and score(best)[0] == 0:
        raise ValueError(f"Couldn't find '{place}'. Closest: {best['name']}, {best.get('admin1', '')}.")
    return {"name": best["name"], "region": best.get("admin1", ""), "country": best.get("country_code", ""),
            "lat": best["latitude"], "lon": best["longitude"], "source": "search"}


def weather(location: str = "", days: int = 7, units: str = "") -> dict:
    days = max(1, min(int(days or 7), 14))
    with httpx.Client(headers=UA, follow_redirects=True) as http:
        loc = _geocode(http, location) if location.strip() else _here(http)
        imperial = (units or ("imperial" if loc.get("country") in ("US", "LR", "MM") else "metric")) == "imperial"
        f = http.get("https://api.open-meteo.com/v1/forecast", timeout=12, params={
            "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": days,
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh", "precipitation_unit": "inch" if imperial else "mm",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,"
                       "wind_direction_10m,wind_gusts_10m,is_day,precipitation",
            "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max",
        })
        f.raise_for_status()
        j = f.json()

    c, h, d = j["current"], j["hourly"], j["daily"]
    cond, icon = _wmo(c.get("weather_code"))
    now_key = c["time"][:13]  # hourly timestamps are on the hour, local time
    start = next((i for i, t in enumerate(h["time"]) if t[:13] >= now_key), 0)
    hourly = []
    for i in range(start, min(start + 24, len(h["time"]))):
        hc, hi = _wmo(h["weather_code"][i])
        hourly.append({"time": h["time"][i], "temp": round(h["temperature_2m"][i]), "condition": hc, "icon": hi,
                       "pop": h["precipitation_probability"][i], "is_day": bool(h["is_day"][i])})
    daily = []
    for i, date in enumerate(d["time"]):
        dc, di = _wmo(d["weather_code"][i])
        daily.append({"date": date, "hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
                      "condition": dc, "icon": di, "pop": d["precipitation_probability_max"][i],
                      "precip": d["precipitation_sum"][i], "sunrise": d["sunrise"][i], "sunset": d["sunset"][i],
                      "uv": d["uv_index_max"][i], "wind_max": d["wind_speed_10m_max"][i]})
    wd = c.get("wind_direction_10m")
    return {
        "location": loc, "timezone": j.get("timezone"), "units": "imperial" if imperial else "metric",
        "unit_temp": "°F" if imperial else "°C", "unit_wind": "mph" if imperial else "km/h",
        "current": {"time": c["time"], "temp": round(c["temperature_2m"]), "feels_like": round(c["apparent_temperature"]),
                    "humidity": c["relative_humidity_2m"], "wind": round(c["wind_speed_10m"]),
                    "gusts": round(c.get("wind_gusts_10m") or 0),
                    "wind_dir": _COMPASS[int((wd % 360) / 22.5 + 0.5) % 16] if wd is not None else "",
                    "precip": c.get("precipitation"), "condition": cond, "icon": icon, "is_day": bool(c.get("is_day"))},
        "today": daily[0] if daily else None,
        "hourly": hourly, "daily": daily,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
