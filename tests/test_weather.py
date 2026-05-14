"""Tests for agents.weather — Open-Meteo weather fetching.

urllib.request.urlopen is mocked because we're testing parsing and
error handling, not the HTTP transport.
"""

import json
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

from agents.weather import fetch_weather


# --- Helpers ---

def _mock_response(data: dict, status=200):
    """Build a mock urllib response."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


SAMPLE_API_RESPONSE = {
    "current": {
        "temperature_2m": 22.5,
        "relative_humidity_2m": 55,
        "precipitation": 0.0,
    },
    "hourly": {
        "time": [
            # 24 hours of timestamps (only need the array length to matter)
            f"2026-05-14T{h:02d}:00" for h in range(24)
        ],
        "precipitation": [0.0] * 20 + [1.2, 0.8, 0.0, 0.0],  # 2mm in last 4 hours
    },
    "daily": {
        "time": ["2026-05-14", "2026-05-15", "2026-05-16"],
        "temperature_2m_max": [24.0, 28.5, 31.0],
        "precipitation_sum": [0.0, 2.5, 0.0],
    },
}


class TestFetchWeather:
    @patch("agents.weather.urlopen")
    def test_returns_current_conditions(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        result = fetch_weather()

        assert result["current"]["temp_c"] == 22.5
        assert result["current"]["humidity_pct"] == 55
        assert result["current"]["precip_mm"] == 0.0

    @patch("agents.weather.urlopen")
    def test_returns_forecast(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        result = fetch_weather()

        assert len(result["forecast"]) == 3
        assert result["forecast"][0]["date"] == "2026-05-14"
        assert result["forecast"][0]["temp_max_c"] == 24.0
        assert result["forecast"][0]["precip_mm"] == 0.0
        assert result["forecast"][1]["precip_mm"] == 2.5

    @patch("agents.weather.urlopen")
    def test_calculates_recent_precipitation(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        result = fetch_weather()

        # Last 24h of hourly precip: sum of all 24 values = 2.0mm
        assert result["recent_precip_mm"] == pytest.approx(2.0)

    @patch("agents.weather.urlopen")
    def test_uses_default_leiden_coordinates(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        fetch_weather()

        url = mock_urlopen.call_args[0][0]
        assert "latitude=52.16" in url
        assert "longitude=4.49" in url

    @patch("agents.weather.urlopen")
    def test_accepts_custom_coordinates(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        fetch_weather(lat=48.85, lon=2.35)

        url = mock_urlopen.call_args[0][0]
        assert "latitude=48.85" in url
        assert "longitude=2.35" in url

    @patch("agents.weather.urlopen")
    def test_returns_none_on_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")

        result = fetch_weather()

        assert result is None

    @patch("agents.weather.urlopen")
    def test_returns_none_on_malformed_json(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = fetch_weather()

        assert result is None

    @patch("agents.weather.urlopen")
    def test_requests_correct_api_fields(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(SAMPLE_API_RESPONSE)

        fetch_weather()

        url = mock_urlopen.call_args[0][0]
        assert "current=" in url
        assert "temperature_2m" in url
        assert "relative_humidity_2m" in url
        assert "precipitation" in url
        assert "hourly=precipitation" in url
        assert "daily=" in url
        assert "temperature_2m_max" in url
        assert "precipitation_sum" in url
