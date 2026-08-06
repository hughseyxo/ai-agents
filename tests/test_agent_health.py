"""Tests for AgentHealthAgent — deterministic staleness detection."""

from datetime import datetime, timedelta, timezone

import pytest

from agents.agent_health import (
    AgentHealthAgent,
    cron_interval_seconds,
    evaluate_staleness,
    diff_alerts,
)


class TestAlertPersistence:
    """The 'alerted' state must only record agents we actually notified."""

    def _agent(self, tmp_path):
        return AgentHealthAgent(db_path=tmp_path / "h.db")

    def test_failed_send_not_persisted(self, tmp_path, monkeypatch):
        import agents.agent_health as agent_health_module

        monkeypatch.setattr(agent_health_module, "TEXTFILE_COLLECTOR_DIR", tmp_path / "textfile_collector")
        agent = self._agent(tmp_path)
        monkeypatch.setattr(agent, "_monitored", lambda: {"plant-agent": ("0 * * * *", None)})
        monkeypatch.setattr(agent, "_send_telegram", lambda msg: False)
        result = agent._check()
        assert "plant-agent" in result["stale"]
        assert result["alerts_sent"] == 0
        # Failed send is NOT recorded, so the next run retries instead of going silent.
        assert agent.get_state("alerted") == []

    def test_successful_send_persisted(self, tmp_path, monkeypatch):
        import agents.agent_health as agent_health_module

        monkeypatch.setattr(agent_health_module, "TEXTFILE_COLLECTOR_DIR", tmp_path / "textfile_collector")
        agent = self._agent(tmp_path)
        monkeypatch.setattr(agent, "_monitored", lambda: {"plant-agent": ("0 * * * *", None)})
        monkeypatch.setattr(agent, "_send_telegram", lambda msg: True)
        result = agent._check()
        assert result["alerts_sent"] == 1
        assert agent.get_state("alerted") == ["plant-agent"]


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


class TestWriteHealthMetric:
    def test_writes_metric_file_atomically(self, tmp_path):
        from agents.agent_health import write_health_metric

        write_health_metric(tmp_path, 1234567890.0)

        metric_file = tmp_path / "agent_health.prom"
        assert metric_file.exists()
        content = metric_file.read_text()
        assert content == "agent_health_last_success_timestamp 1234567890\n"
        # No leftover tempfile from the atomic-write pattern.
        assert list(tmp_path.iterdir()) == [metric_file]

    def test_overwrites_existing_metric_file(self, tmp_path):
        from agents.agent_health import write_health_metric

        write_health_metric(tmp_path, 1000.0)
        write_health_metric(tmp_path, 2000.0)

        content = (tmp_path / "agent_health.prom").read_text()
        assert content == "agent_health_last_success_timestamp 2000\n"
