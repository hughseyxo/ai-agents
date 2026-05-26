"""Weather-based watering adjustment logic.

Pure functions — no side effects, no I/O. Fully testable.
"""

from datetime import date, timedelta

MAX_ADJUSTMENT = 3


def calculate_adjustment(plant: dict, weather: dict) -> int:
    """Return days to shift watering. Negative=earlier, positive=later."""
    location = plant.get("location", "indoor")

    if location == "outdoor":
        adj = _outdoor_adjustment(weather)
    else:
        adj = _indoor_adjustment(weather)

    return max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, adj))


def _indoor_adjustment(weather: dict) -> int:
    """Indoor plants: subtle adjustments based on temp + humidity proxy."""
    temp = weather["current"]["temp_c"]
    humidity = weather["current"]["humidity_pct"]

    # Very hot and very dry → water 2 days earlier
    if temp > 32 and humidity < 35:
        return -2
    # Hot and dry → water 1 day earlier
    if temp > 28 and humidity < 40:
        return -1
    # Very cold and very humid → water 2 days later
    if temp < 5 and humidity > 85:
        return 2
    # Cold and humid → water 1 day later
    if temp < 10 and humidity > 80:
        return 1

    return 0


def _outdoor_adjustment(weather: dict) -> int:
    """Outdoor plants: rain-aware, larger adjustments."""
    recent_rain = weather["recent_precip_mm"]
    forecast = weather["forecast"]
    temp = weather["current"]["temp_c"]

    forecast_rain_day1 = forecast[0]["precip_mm"] if forecast else 0
    total_rain = recent_rain + forecast_rain_day1

    # Rain adjustments (positive = defer watering)
    rain_adj = 0
    if total_rain > 10:
        rain_adj = 3
    elif recent_rain > 10:
        rain_adj = 2
    elif recent_rain > 5:
        rain_adj = 2
    elif forecast_rain_day1 > 5:
        rain_adj = 1

    # If there's meaningful rain, don't also adjust for heat
    if rain_adj > 0:
        return rain_adj

    # Heat adjustments (negative = water earlier)
    hot_days = sum(1 for f in forecast if f["temp_max_c"] > 30)
    no_rain_forecast = all(f["precip_mm"] < 1 for f in forecast)

    if hot_days >= 2:
        return -2
    if no_rain_forecast and temp > 25:
        return -1

    return 0


def adjust_watering_date(base_date: date, frequency_days: int,
                         plant: dict, weather: dict) -> tuple[date, str]:
    """Apply weather adjustment to a watering date.

    Returns (adjusted_date, reason_string).
    Reason is empty string if no adjustment was made.
    """
    adj = calculate_adjustment(plant, weather)

    if adj == 0:
        return base_date, ""

    adjusted = base_date + timedelta(days=adj)

    reason = _build_reason(adj, plant, weather)
    return adjusted, reason


def is_heatwave_incoming(weather: dict) -> bool:
    """Return True when ≥2 forecast days >30°C and no forecast day has ≥5mm rain."""
    forecast = weather.get("forecast", [])
    hot_days = sum(1 for f in forecast if f["temp_max_c"] > 30)
    all_dry = all(f["precip_mm"] < 5 for f in forecast)
    return hot_days >= 2 and all_dry


def _build_reason(adj: int, plant: dict, weather: dict) -> str:
    """Build a human-readable reason for the adjustment."""
    location = plant.get("location", "indoor")
    direction = "earlier" if adj < 0 else "later"
    days = abs(adj)

    if location == "outdoor":
        recent_rain = weather["recent_precip_mm"]
        forecast_rain = weather["forecast"][0]["precip_mm"] if weather["forecast"] else 0

        if adj > 0:
            parts = []
            if recent_rain > 5:
                parts.append(f"{recent_rain:.0f}mm recent rain")
            if forecast_rain > 5:
                parts.append(f"{forecast_rain:.0f}mm forecast rain")
            cause = " + ".join(parts) if parts else "rain"
            return f"{days}d {direction} — {cause}"
        else:
            hot_days = sum(1 for f in weather["forecast"] if f["temp_max_c"] > 30)
            if hot_days >= 2:
                return f"{days}d {direction} — heatwave ({hot_days} days >30°C)"
            return f"{days}d {direction} — dry spell, no rain forecast"
    else:
        temp = weather["current"]["temp_c"]
        humidity = weather["current"]["humidity_pct"]
        if adj < 0:
            return f"{days}d {direction} — hot dry conditions ({temp:.0f}°C, {humidity:.0f}% humidity)"
        else:
            return f"{days}d {direction} — cold humid conditions ({temp:.0f}°C, {humidity:.0f}% humidity)"
