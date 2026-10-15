#!/usr/bin/env python3
"""
Google Calendar REST client for agents.

Sibling of agents/gmail_client.py: the daily briefing fetches calendar data in
Python rather than spending an LLM turn on MCP calls. That is deliberate — the
calendar ids and time windows used to live as prose in the briefing prompt, and
when the on-call calendar id went stale the resulting 404 was absorbed by the
model and never surfaced. Here a dead calendar produces an explicit error string.

OAuth is shared with Gmail (same ~/.google_tokens.json; the grant already carries
the calendar.readonly scope), so token load/refresh/save is reused rather than
duplicated.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .gmail_client import REQUEST_TIMEOUT, get_access_token

API_BASE = "https://www.googleapis.com/calendar/v3"

PRIMARY_CALENDAR_ID = "cianohughes@gmail.com"
# PagerDuty-style shift feed ("Cian Hughes [Ocean]"). The previous id
# (8ubfqbcooeks9np5aufgu7g3mm0gj1rh@import.calendar.google.com) was removed from
# the account and returned HTTP 404 on every briefing run.
ONCALL_CALENDAR_ID = "5es8ffc6b48k844ng166q0u240mfo3s1@import.calendar.google.com"

DEFAULT_TIME_ZONE = "Europe/Amsterdam"


def calendar_request(path, params=None):
    token = get_access_token()
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def list_calendars():
    return calendar_request("/users/me/calendarList")


def list_events(calendar_id, time_min, time_max, max_results=50,
                time_zone=DEFAULT_TIME_ZONE):
    """Raw events call. time_min/time_max must be RFC3339 with a `Z` suffix —
    an offset suffix such as `+00:00` is rejected with HTTP 401."""
    params = {
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
        "timeZone": time_zone,
    }
    path = f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events"
    return calendar_request(path, params)


def fetch_events(calendar_id, time_min, time_max, max_results=50,
                 time_zone=DEFAULT_TIME_ZONE):
    """(items, error) — never raises. A single unreachable calendar must not
    abort the other calendars or the briefing as a whole; the caller renders the
    error so a stale id is visible instead of silently yielding nothing."""
    try:
        result = list_events(calendar_id, time_min, time_max, max_results, time_zone)
        return result.get("items", []), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:120]
        except Exception:
            pass
        return [], f"HTTP {e.code}{f' — {detail}' if detail else ''}"
    except urllib.error.URLError as e:
        return [], f"network error — {e.reason}"
    except Exception as e:  # OAuth refresh failures, malformed JSON, ...
        return [], f"{type(e).__name__}: {e}"


def day_bounds(start_day, end_day, time_zone=DEFAULT_TIME_ZONE):
    """(time_min, time_max) as RFC3339 `Z` instants covering local midnight on
    start_day through local 23:59:59 on end_day.

    timeMin/timeMax are absolute instants, so naively sending `{day}T00:00:00Z`
    shifts the window by the UTC offset — in Amsterdam that silently drops
    early-morning events and pulls in the next day's. The `Z` suffix itself is
    kept: an explicit offset suffix is rejected with HTTP 401.
    """
    tz = ZoneInfo(time_zone)
    start = datetime.combine(date.fromisoformat(start_day), time.min, tzinfo=tz)
    end = datetime.combine(date.fromisoformat(end_day), time.max, tzinfo=tz)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (start.astimezone(timezone.utc).strftime(fmt),
            end.astimezone(timezone.utc).strftime(fmt))


def parse_event(item):
    """Normalise a Calendar API event into a flat dict, or None if it has no
    usable start/end. Google reports all-day `end.date` exclusively (a one-day
    event ends the following day), so it is normalised back to the last day the
    event actually covers."""
    start = item.get("start") or {}
    end = item.get("end") or {}
    summary = (item.get("summary") or "").strip() or "(no title)"

    if start.get("date"):
        try:
            start_date = date.fromisoformat(start["date"])
            end_raw = end.get("date") or start["date"]
            end_date = date.fromisoformat(end_raw) - timedelta(days=1)
        except ValueError:
            return None
        if end_date < start_date:
            end_date = start_date
        return {
            "summary": summary,
            "all_day": True,
            "date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "start_time": None,
            "end_time": None,
            "duration_minutes": None,
        }

    if start.get("dateTime"):
        try:
            start_dt = datetime.fromisoformat(start["dateTime"])
            end_dt = datetime.fromisoformat(end.get("dateTime") or start["dateTime"])
        except ValueError:
            return None
        duration = int((end_dt - start_dt).total_seconds() // 60)
        return {
            "summary": summary,
            "all_day": False,
            "date": start_dt.date().isoformat(),
            "end_date": end_dt.date().isoformat(),
            "start_time": start_dt.strftime("%H:%M"),
            "end_time": end_dt.strftime("%H:%M"),
            "duration_minutes": max(duration, 0),
        }

    return None
