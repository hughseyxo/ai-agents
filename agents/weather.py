"""Weather client for plant watering adjustments.

No API key required. Returns current conditions + 3-day forecast
for Leiden (default) or custom coordinates. Open-Meteo is primary;
wttr.in is a fallback for when Open-Meteo 502s/503s (observed to
happen for extended stretches — see agent-notes.md).
"""

import json
import sys
from urllib.request import urlopen

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WTTR_URL = "https://wttr.in"


def fetch_weather(lat: float = 52.16, lon: float = 4.49) -> dict | None:
    """Fetch current conditions + 3-day forecast, trying Open-Meteo then wttr.in.

    Returns None only if both providers fail (caller should treat
    as "no weather data available" and skip adjustments).
    """
    weather = _fetch_open_meteo(lat, lon)
    if weather is not None:
        return weather
    return _fetch_wttr(lat, lon)


def _fetch_open_meteo(lat: float, lon: float) -> dict | None:
    params = (
        f"latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,precipitation"
        f"&hourly=precipitation"
        f"&daily=temperature_2m_max,precipitation_sum"
        f"&forecast_days=3"
        f"&past_days=1"
        f"&timezone=Europe%2FAmsterdam"
    )
    url = f"{OPEN_METEO_URL}?{params}"

    try:
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[weather] open-meteo failed to fetch: {e}", file=sys.stderr)
        return None

    try:
        current = data["current"]
        hourly = data["hourly"]
        daily = data["daily"]

        # With past_days=1 the hourly arrays start at the PAST 24h window,
        # followed by the forecast. Sum the leading 24 points (past day),
        # NOT the trailing tail (which is future forecast rain).
        recent_precip = sum(hourly["precipitation"][:24])

        forecast = []
        for i in range(len(daily["time"])):
            forecast.append({
                "date": daily["time"][i],
                "temp_max_c": daily["temperature_2m_max"][i],
                "precip_mm": daily["precipitation_sum"][i],
            })

        return {
            "current": {
                "temp_c": current["temperature_2m"],
                "humidity_pct": current["relative_humidity_2m"],
                "precip_mm": current["precipitation"],
            },
            "forecast": forecast,
            "recent_precip_mm": recent_precip,
        }
    except (KeyError, TypeError, IndexError) as e:
        print(f"[weather] open-meteo failed to parse response: {e}", file=sys.stderr)
        return None


def _fetch_wttr(lat: float, lon: float) -> dict | None:
    url = f"{WTTR_URL}/{lat},{lon}?format=j1"

    try:
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[weather] wttr.in failed to fetch: {e}", file=sys.stderr)
        return None

    try:
        current = data["current_condition"][0]
        daily = data["weather"]

        forecast = []
        for day in daily:
            day_precip = sum(float(h["precipMM"]) for h in day["hourly"])
            forecast.append({
                "date": day["date"],
                "temp_max_c": float(day["maxtempC"]),
                "precip_mm": day_precip,
            })

        # wttr.in's free tier has no "past 24h actuals" endpoint, so this
        # approximates recent rainfall with the current hour's reading only
        # (conservative undercount vs. Open-Meteo's true past-24h sum).
        return {
            "current": {
                "temp_c": float(current["temp_C"]),
                "humidity_pct": float(current["humidity"]),
                "precip_mm": float(current["precipMM"]),
            },
            "forecast": forecast,
            "recent_precip_mm": float(current["precipMM"]),
        }
    except (KeyError, TypeError, IndexError, ValueError) as e:
        print(f"[weather] wttr.in failed to parse response: {e}", file=sys.stderr)
        return None
