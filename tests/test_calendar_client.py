"""Tests for agents/calendar_client.py.

No network: urlopen and the OAuth token fetch are both stubbed.
"""

import io
import json
import urllib.error

import pytest

from agents import calendar_client as cc


@pytest.fixture(autouse=True)
def _no_oauth(monkeypatch):
    monkeypatch.setattr(cc, "get_access_token", lambda: "fake-token")


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://example", code=code, msg="Not Found", hdrs=None,
        fp=io.BytesIO(b"{}"),
    )


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class TestListEvents:
    def test_builds_singleevents_ordered_request(self, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            return _FakeResponse(json.dumps({"items": []}).encode())

        monkeypatch.setattr(cc.urllib.request, "urlopen", fake_urlopen)

        cc.list_events("cal@example.com", "2026-08-15T00:00:00Z", "2026-08-15T23:59:59Z")

        assert "singleEvents=true" in seen["url"]
        assert "orderBy=startTime" in seen["url"]
        assert "timeMin=2026-08-15T00%3A00%3A00Z" in seen["url"]
        assert "timeMax=2026-08-15T23%3A59%3A59Z" in seen["url"]
        # The calendar id must be path-escaped, not interpolated raw.
        assert "cal%40example.com/events" in seen["url"]
        assert seen["auth"] == "Bearer fake-token"

    def test_returns_parsed_items(self, monkeypatch):
        payload = {"items": [{"summary": "Standup"}]}
        monkeypatch.setattr(
            cc.urllib.request, "urlopen",
            lambda req, timeout=None: _FakeResponse(json.dumps(payload).encode()),
        )
        assert cc.list_events("c", "a", "b")["items"][0]["summary"] == "Standup"


class TestFetchEvents:
    """fetch_events must never raise — one dead calendar cannot abort the rest."""

    def test_success_returns_items_and_no_error(self, monkeypatch):
        payload = {"items": [{"summary": "Standup"}]}
        monkeypatch.setattr(
            cc.urllib.request, "urlopen",
            lambda req, timeout=None: _FakeResponse(json.dumps(payload).encode()),
        )
        items, error = cc.fetch_events("c", "a", "b")
        assert error is None
        assert items == [{"summary": "Standup"}]

    def test_404_returns_error_not_raise(self, monkeypatch):
        def boom(req, timeout=None):
            raise _http_error(404)

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        items, error = cc.fetch_events("stale@import.calendar.google.com", "a", "b")
        assert items == []
        assert error is not None
        assert "404" in error

    def test_network_failure_returns_error_not_raise(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("dns dead")

        monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
        items, error = cc.fetch_events("c", "a", "b")
        assert items == []
        assert "dns dead" in error

    def test_oauth_failure_returns_error_not_raise(self, monkeypatch):
        def boom():
            raise RuntimeError("No refresh_token in token file")

        monkeypatch.setattr(cc, "get_access_token", boom)
        items, error = cc.fetch_events("c", "a", "b")
        assert items == []
        assert "refresh_token" in error


class TestParseEvent:
    def test_timed_event(self):
        ev = cc.parse_event({
            "summary": "Michael Appointment",
            "start": {"dateTime": "2026-08-26T17:15:00+02:00"},
            "end": {"dateTime": "2026-08-26T18:15:00+02:00"},
        })
        assert ev["all_day"] is False
        assert ev["summary"] == "Michael Appointment"
        assert ev["date"] == "2026-08-26"
        assert ev["start_time"] == "17:15"
        assert ev["end_time"] == "18:15"
        assert ev["duration_minutes"] == 60

    def test_all_day_event(self):
        ev = cc.parse_event({
            "summary": "Alex Meade's birthday",
            "start": {"date": "2026-08-15"},
            "end": {"date": "2026-08-16"},
        })
        assert ev["all_day"] is True
        assert ev["date"] == "2026-08-15"
        assert ev["end_date"] == "2026-08-15"  # exclusive end normalised back
        assert ev["start_time"] is None
        assert ev["duration_minutes"] is None

    def test_multi_day_all_day_event(self):
        ev = cc.parse_event({
            "summary": "Niamhs Ma over",
            "start": {"date": "2026-08-18"},
            "end": {"date": "2026-08-27"},
        })
        assert ev["date"] == "2026-08-18"
        assert ev["end_date"] == "2026-08-26"

    def test_missing_summary_falls_back(self):
        ev = cc.parse_event({"start": {"date": "2026-08-15"}, "end": {"date": "2026-08-16"}})
        assert ev["summary"] == "(no title)"

    def test_unparseable_event_returns_none(self):
        assert cc.parse_event({"summary": "Broken"}) is None
        assert cc.parse_event({"summary": "Broken", "start": {}}) is None


class TestCalendarIds:
    def test_oncall_id_is_the_live_one(self):
        """The previous id (8ubfqbc...) was deleted from the account and 404'd
        silently for weeks — pin the live one so a regression is visible."""
        assert cc.ONCALL_CALENDAR_ID.startswith("5es8ffc6b48k844ng166q0u240mfo3s1@")
        assert "8ubfqbcooeks9np5aufgu7g3mm0gj1rh" not in cc.ONCALL_CALENDAR_ID


class TestDayBounds:
    """timeMin/timeMax are absolute instants — a naive `{day}T00:00:00Z` window
    is offset by the local UTC offset and silently drops early-morning events."""

    def test_summer_window_is_offset_to_local_midnight(self):
        # Amsterdam is UTC+2 in August.
        tmin, tmax = cc.day_bounds("2026-08-15", "2026-08-15")
        assert tmin == "2026-08-14T22:00:00Z"
        assert tmax == "2026-08-15T21:59:59Z"

    def test_winter_window_uses_correct_dst_offset(self):
        # Amsterdam is UTC+1 in January.
        tmin, _ = cc.day_bounds("2026-01-15", "2026-01-15")
        assert tmin == "2026-01-14T23:00:00Z"

    def test_multi_day_window(self):
        tmin, tmax = cc.day_bounds("2026-08-16", "2026-09-14")
        assert tmin == "2026-08-15T22:00:00Z"
        assert tmax == "2026-09-14T21:59:59Z"

    def test_keeps_z_suffix(self):
        """An explicit offset suffix is rejected by the API with HTTP 401."""
        tmin, tmax = cc.day_bounds("2026-08-15", "2026-08-15")
        assert tmin.endswith("Z") and tmax.endswith("Z")
        assert "+" not in tmin and "+" not in tmax
