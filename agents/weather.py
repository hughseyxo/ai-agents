"""Open-Meteo weather client for plant watering adjustments.

No API key required. Returns current conditions + 3-day forecast
for Leiden (default) or custom coordinates.
"""

import json
import sys
from urllib.request import urlopen

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_weather(lat: float = 52.16, lon: float = 4.49) -> dict | None:
    """Fetch current conditions + 3-day forecast from Open-Meteo.

    Returns None on any network/parsing error (caller should treat
    as "no weather data available" and skip adjustments).
    """
    params = (
        f"latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,precipitation"
        f"&hourly=precipitation"
        f"&daily=temperature_2m_max,precipitation_sum"
        f"&forecast_days=3"
        f"&past_days=1"
        f"&timezone=Europe%2FAmsterdam"
    )
    url = f"{BASE_URL}?{params}"

    try:
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[weather] Failed to fetch: {e}", file=sys.stderr)
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
        print(f"[weather] Failed to parse response: {e}", file=sys.stderr)
        return None
