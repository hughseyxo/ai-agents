"""Tests for AgentHealthAgent — deterministic staleness detection."""

from datetime import datetime, timedelta, timezone

import pytest

from agents.agent_health import (
    cron_interval_seconds,
    evaluate_staleness,
    diff_alerts,
)


class TestCronInterval:
    def test_hourly(self):
        assert cron_interval_seconds("0 * * * *") == 3600

    def test_daily(self):
        assert cron_interval_seconds("5 4 * * *") == 86400
        assert cron_interval_seconds("0 4 * * *") == 86400

    def test_weekly(self):
        assert cron_interval_seconds("0 6 * * 0") == 7 * 86400

    def test_step_minute(self):
        assert cron_interval_seconds("*/15 * * * *") == 900

    def test_step_hour(self):
        assert cron_interval_seconds("0 */2 * * *") == 7200

    def test_every_minute(self):
        assert cron_interval_seconds("* * * * *") == 60


class TestEvaluateStaleness:
    def _now(self):
        return datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_fresh_hourly_not_stale(self):
        now = self._now()
        state = {"plant-agent": ("0 * * * *", now - timedelta(minutes=30))}
        assert evaluate_staleness(state, now) == []

    def test_hourly_stale_after_2x(self):
        now = self._now()
        # last ran 2.5h ago, interval 1h, factor 2 -> threshold 2h -> stale
        state = {"plant-agent": ("0 * * * *", now - timedelta(hours=2, minutes=30))}
        assert evaluate_staleness(state, now) == ["plant-agent"]

    def test_hourly_within_grace_not_stale(self):
        now = self._now()
        # 1.5h < 2h threshold
        state = {"plant-agent": ("0 * * * *", now - timedelta(hours=1, minutes=30))}
        assert evaluate_staleness(state, now) == []

    def test_never_ran_is_stale(self):
        now = self._now()
        state = {"daily-briefing": ("5 4 * * *", None)}
        assert evaluate_staleness(state, now) == ["daily-briefing"]

    def test_daily_stale_after_two_days(self):
        now = self._now()
        state = {"daily-briefing": ("5 4 * * *", now - timedelta(days=2, hours=1))}
        assert evaluate_staleness(state, now) == ["daily-briefing"]

    def test_sorted_output(self):
        now = self._now()
        state = {
            "zeta": ("0 * * * *", now - timedelta(hours=5)),
            "alpha": ("0 * * * *", None),
        }
        assert evaluate_staleness(state, now) == ["alpha", "zeta"]


class TestDiffAlerts:
    def test_new_alert(self):
        new, recovered = diff_alerts(["plant-agent"], [])
        assert new == ["plant-agent"]
        assert recovered == []

    def test_already_alerted_no_duplicate(self):
        new, recovered = diff_alerts(["plant-agent"], ["plant-agent"])
        assert new == []
        assert recovered == []

    def test_recovery(self):
        new, recovered = diff_alerts([], ["plant-agent"])
        assert new == []
        assert recovered == ["plant-agent"]

    def test_mixed(self):
        new, recovered = diff_alerts(["b"], ["a"])
        assert new == ["b"]
        assert recovered == ["a"]
