"""Tests for agents.plant_weather — watering adjustment logic.

All pure functions, no mocks needed. Weather data is passed in directly.
"""

from datetime import date

import pytest

from agents.plant_weather import calculate_adjustment, adjust_watering_date, is_heatwave_incoming


# --- Weather data fixtures ---

def _weather(temp_c=20, humidity_pct=50, recent_precip_mm=0,
             forecast=None):
    """Build a weather dict with sensible defaults."""
    if forecast is None:
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 22, "precip_mm": 0},
            {"date": "2026-05-16", "temp_max_c": 23, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 21, "precip_mm": 0},
        ]
    return {
        "current": {
            "temp_c": temp_c,
            "humidity_pct": humidity_pct,
            "precip_mm": 0,
        },
        "forecast": forecast,
        "recent_precip_mm": recent_precip_mm,
    }


def _plant(name="Test Plant", frequency_days=7, location="indoor"):
    return {"name": name, "frequency_days": frequency_days,
            "last_watered": "2026-05-10", "location": location}


# ===================================================================
# Indoor plant adjustments
# ===================================================================

class TestIndoorAdjustment:
    def test_no_adjustment_normal_conditions(self):
        adj = calculate_adjustment(_plant(), _weather())
        assert adj == 0

    def test_hot_dry_waters_earlier(self):
        # >28°C and <40% humidity → -1 day
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=30, humidity_pct=35))
        assert adj == -1

    def test_very_hot_very_dry_waters_2_days_earlier(self):
        # >32°C and <35% humidity → -2 days
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=34, humidity_pct=30))
        assert adj == -2

    def test_cold_humid_waters_later(self):
        # <10°C and >80% humidity → +1 day
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=8, humidity_pct=85))
        assert adj == 1

    def test_very_cold_very_humid_waters_2_days_later(self):
        # <5°C and >85% humidity → +2 days
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=3, humidity_pct=90))
        assert adj == 2

    def test_hot_but_humid_no_adjustment(self):
        # Hot but not dry — no change
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=30, humidity_pct=60))
        assert adj == 0

    def test_cold_but_dry_no_adjustment(self):
        # Cold but not humid — no change
        adj = calculate_adjustment(
            _plant(), _weather(temp_c=5, humidity_pct=40))
        assert adj == 0


# ===================================================================
# Outdoor plant adjustments
# ===================================================================

class TestOutdoorAdjustment:
    def test_no_adjustment_normal_conditions(self):
        adj = calculate_adjustment(
            _plant(location="outdoor"), _weather())
        assert adj == 0

    def test_recent_rain_defers(self):
        # >5mm in last 24h → +1 to +2 days
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(recent_precip_mm=8))
        assert adj >= 1
        assert adj <= 2

    def test_heavy_recent_rain_defers_more(self):
        # >10mm recent (total >10mm) → +3 days
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(recent_precip_mm=15))
        assert adj == 3

    def test_forecast_rain_defers(self):
        # >5mm forecast in next day → +1 day
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 20, "precip_mm": 8},
            {"date": "2026-05-16", "temp_max_c": 20, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 20, "precip_mm": 0},
        ]
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(forecast=forecast))
        assert adj >= 1

    def test_combined_rain_defers_up_to_3(self):
        # Recent + forecast rain >10mm → +2 to +3 days
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 20, "precip_mm": 6},
            {"date": "2026-05-16", "temp_max_c": 20, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 20, "precip_mm": 0},
        ]
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(recent_precip_mm=8, forecast=forecast))
        assert adj >= 2
        assert adj <= 3

    def test_heatwave_waters_earlier(self):
        # >30°C for 2+ days → -1 to -2 days
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 32, "precip_mm": 0},
            {"date": "2026-05-16", "temp_max_c": 33, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 31, "precip_mm": 0},
        ]
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(temp_c=31, forecast=forecast))
        assert adj <= -1
        assert adj >= -2

    def test_dry_spell_waters_earlier(self):
        # No rain forecast + temp >25°C → -1 to -2 days
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 27, "precip_mm": 0},
            {"date": "2026-05-16", "temp_max_c": 28, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 26, "precip_mm": 0},
        ]
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(temp_c=27, recent_precip_mm=0, forecast=forecast))
        assert adj <= -1

    def test_rain_overrides_heat(self):
        # If it's hot BUT raining, rain takes precedence (net positive or zero)
        forecast = [
            {"date": "2026-05-15", "temp_max_c": 33, "precip_mm": 12},
            {"date": "2026-05-16", "temp_max_c": 32, "precip_mm": 0},
            {"date": "2026-05-17", "temp_max_c": 30, "precip_mm": 0},
        ]
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(temp_c=33, recent_precip_mm=5, forecast=forecast))
        assert adj >= 0  # rain should prevent watering earlier


# ===================================================================
# Clamping and edge cases
# ===================================================================

class TestClamping:
    def test_adjustment_clamped_to_max_3(self):
        # Even extreme conditions shouldn't shift more than 3 days
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(recent_precip_mm=50, forecast=[
                {"date": "2026-05-15", "temp_max_c": 15, "precip_mm": 30},
                {"date": "2026-05-16", "temp_max_c": 15, "precip_mm": 20},
                {"date": "2026-05-17", "temp_max_c": 15, "precip_mm": 10},
            ]))
        assert adj <= 3

    def test_negative_adjustment_clamped_to_minus_3(self):
        adj = calculate_adjustment(
            _plant(location="outdoor"),
            _weather(temp_c=40, humidity_pct=10, forecast=[
                {"date": "2026-05-15", "temp_max_c": 42, "precip_mm": 0},
                {"date": "2026-05-16", "temp_max_c": 41, "precip_mm": 0},
                {"date": "2026-05-17", "temp_max_c": 40, "precip_mm": 0},
            ]))
        assert adj >= -3

    def test_unknown_location_treated_as_indoor(self):
        adj = calculate_adjustment(
            _plant(location="unknown"), _weather())
        assert adj == 0

    def test_missing_location_treated_as_indoor(self):
        plant = {"name": "Old Plant", "frequency_days": 7,
                 "last_watered": "2026-05-10"}
        adj = calculate_adjustment(plant, _weather())
        assert adj == 0


# ===================================================================
# adjust_watering_date — integration of adjustment + date math
# ===================================================================

class TestAdjustWateringDate:
    def test_no_adjustment_returns_base_date(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(), _weather())
        assert adjusted == base
        assert reason == ""

    def test_hot_dry_shifts_date_earlier(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(),
            _weather(temp_c=34, humidity_pct=30))
        assert adjusted < base
        assert "hot" in reason.lower() or "dry" in reason.lower()

    def test_cold_humid_shifts_date_later(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(),
            _weather(temp_c=3, humidity_pct=90))
        assert adjusted > base
        assert "cold" in reason.lower() or "humid" in reason.lower()

    def test_heat_adjustment_applied_without_floor(self):
        # -2 adjustment on a 3-day cycle plant watered yesterday:
        # base = yesterday + 3 = today + 2, adjusted = today + 2 - 2 = today
        # With MIN_INTERVAL_DAYS removed, this lands on today (effective_interval=1).
        yesterday = date(2026, 5, 9)
        base = date(2026, 5, 12)  # yesterday + 3
        plant = {
            "name": "Test Plant",
            "frequency_days": 3,
            "last_watered": yesterday.isoformat(),
            "location": "indoor",
        }
        adjusted, reason = adjust_watering_date(
            base, 3, plant,
            _weather(temp_c=34, humidity_pct=30))
        assert adjusted == date(2026, 5, 10)
        assert reason != ""

    def test_reason_string_describes_adjustment(self):
        base = date(2026, 5, 17)
        _, reason = adjust_watering_date(
            base, 7, _plant(location="outdoor"),
            _weather(recent_precip_mm=10))
        assert reason != ""
        assert "rain" in reason.lower()


# ===================================================================
# is_heatwave_incoming
# ===================================================================

class TestIsHeatwaveIncoming:
    def test_heatwave_detected(self):
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 0},
            {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 31, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is True

    def test_heatwave_with_rain_returns_false(self):
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 8},
            {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 31, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is False

    def test_only_one_hot_day_returns_false(self):
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 0},
            {"date": "2026-05-27", "temp_max_c": 25, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 22, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is False

    def test_empty_forecast_returns_false(self):
        weather = _weather(forecast=[])
        assert is_heatwave_incoming(weather) is False
