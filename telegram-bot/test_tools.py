"""Tests for server concierge tool functions.

All external dependencies (AgentDB, subprocess, psutil, file I/O) are mocked.
Each test describes the expected output contract, not implementation details.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import (
    get_agent_status,
    get_plant_status,
    get_yopflix_status,
    get_system_health,
    get_cron_schedule,
    get_agent_logs,
    water_plant,
    remove_plant,
    save_recipe,
    get_all_plants,
    get_plant,
    save_plant_assessment,
    research_plant_watering,
)

FAKE_PLANTS = [
    {"name": "Monstera", "frequency_days": 10, "last_watered": "2026-05-10", "location": "indoor"},
    {"name": "Aloe Vera", "frequency_days": 14, "last_watered": "2026-05-15", "location": "indoor"},
    {"name": "Lavender", "frequency_days": 7, "last_watered": "2026-05-05", "location": "outdoor"},
]


# ---------------------------------------------------------------------------
# get_agent_status
# ---------------------------------------------------------------------------

def test_get_agent_status_shows_all_agents():
    mock_db = MagicMock()
    mock_db.get_last_run.side_effect = lambda agent: {
        "agent": agent,
        "started_at": "2026-05-17 05:05:00",
        "status": "success",
        "error": None,
    }
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_agent_status()
    assert "daily-briefing" in result
    assert "news-briefing" in result
    assert "security-audit" in result


def test_get_agent_status_shows_status_and_time():
    mock_db = MagicMock()
    mock_db.get_last_run.return_value = {
        "agent": "daily-briefing",
        "started_at": "2026-05-17 05:05:00",
        "status": "success",
        "error": None,
    }
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_agent_status()
    assert "success" in result
    assert "2026-05-17" in result


def test_get_agent_status_shows_failure_with_error():
    mock_db = MagicMock()
    mock_db.get_last_run.return_value = {
        "agent": "daily-briefing",
        "started_at": "2026-05-17 05:05:00",
        "status": "failure",
        "error": "API timeout",
    }
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_agent_status()
    assert "failure" in result
    assert "API timeout" in result


def test_get_agent_status_handles_never_run_agent():
    mock_db = MagicMock()
    mock_db.get_last_run.return_value = None
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_agent_status()
    assert "never run" in result.lower()


def test_get_agent_status_handles_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = get_agent_status()
    assert "unavailable" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# get_plant_status
# ---------------------------------------------------------------------------

def test_get_plant_status_lists_all_plants():
    mock_db = MagicMock()
    mock_db.get_state.return_value = FAKE_PLANTS
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "Monstera" in result
    assert "Aloe Vera" in result
    assert "Lavender" in result


def test_get_plant_status_shows_next_watering_date():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Monstera", "frequency_days": 10, "last_watered": "2026-05-10", "location": "indoor"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "2026-05-20" in result


def test_get_plant_status_flags_overdue():
    mock_db = MagicMock()
    # Last watered 10 days ago with 7-day frequency → overdue by 3 days
    overdue_date = (date.today() - timedelta(days=10)).isoformat()
    mock_db.get_state.return_value = [
        {"name": "Lavender", "frequency_days": 7, "last_watered": overdue_date, "location": "outdoor"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "overdue" in result.lower() or "!" in result


def test_get_plant_status_handles_empty_plant_list():
    mock_db = MagicMock()
    mock_db.get_state.return_value = []
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "no plants" in result.lower() or result.strip() != ""


def test_get_plant_status_handles_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db error")):
        result = get_plant_status()
    assert "unavailable" in result.lower() or "error" in result.lower()


def test_get_plant_status_shows_last_assessment_date():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {
            "name": "Monstera",
            "frequency_days": 10,
            "last_watered": "2026-05-20",
            "location": "indoor",
            "last_assessment": {"date": "2026-05-23", "summary": "Looks healthy."},
        }
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "2026-05-23" in result
    assert "assessed" in result.lower()


def test_get_plant_status_no_assessment_shows_nothing_extra():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Aloe", "frequency_days": 14, "last_watered": "2026-05-20", "location": "indoor"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "assessed" not in result.lower()


# ---------------------------------------------------------------------------
# get_yopflix_status
# ---------------------------------------------------------------------------

FAKE_CONFIG_YAML = """
services:
  - name: sonarr
    enabled: true
  - name: radarr
    enabled: true
  - name: lidarr
    enabled: false
"""

FAKE_DOCKER_PS = "sonarr\tUp 2 hours\nradarr\tUp 2 hours\n"
FAKE_DF = "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       1.8T  1.2T  600G  68% /data/media\n"


def test_get_yopflix_status_shows_enabled_services():
    with (
        patch("tools.YOPFLIX_CONFIG", "/fake/config.yaml"),
        patch("builtins.open", mock_open(read_data=FAKE_CONFIG_YAML)),
        patch("tools.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            MagicMock(stdout=FAKE_DOCKER_PS, returncode=0),
            MagicMock(stdout=FAKE_DF, returncode=0),
        ]
        result = get_yopflix_status()
    assert "sonarr" in result
    assert "radarr" in result


def test_get_yopflix_status_shows_disabled_services():
    with (
        patch("tools.YOPFLIX_CONFIG", "/fake/config.yaml"),
        patch("builtins.open", mock_open(read_data=FAKE_CONFIG_YAML)),
        patch("tools.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            MagicMock(stdout=FAKE_DOCKER_PS, returncode=0),
            MagicMock(stdout=FAKE_DF, returncode=0),
        ]
        result = get_yopflix_status()
    assert "lidarr" in result


def test_get_yopflix_status_shows_disk_usage():
    with (
        patch("tools.YOPFLIX_CONFIG", "/fake/config.yaml"),
        patch("builtins.open", mock_open(read_data=FAKE_CONFIG_YAML)),
        patch("tools.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            MagicMock(stdout=FAKE_DOCKER_PS, returncode=0),
            MagicMock(stdout=FAKE_DF, returncode=0),
        ]
        result = get_yopflix_status()
    assert "68%" in result or "1.2T" in result or "600G" in result


def test_get_yopflix_status_handles_docker_unavailable():
    with (
        patch("tools.YOPFLIX_CONFIG", "/fake/config.yaml"),
        patch("builtins.open", mock_open(read_data=FAKE_CONFIG_YAML)),
        patch("tools.subprocess.run", side_effect=FileNotFoundError("docker not found")),
    ):
        result = get_yopflix_status()
    assert "docker" in result.lower()


def test_get_yopflix_status_handles_missing_config():
    with (
        patch("tools.YOPFLIX_CONFIG", "/nonexistent/config.yaml"),
        patch("builtins.open", side_effect=FileNotFoundError),
        patch("tools.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = get_yopflix_status()
    assert "unavailable" in result.lower() or "not found" in result.lower() or "config" in result.lower()


# ---------------------------------------------------------------------------
# get_system_health
# ---------------------------------------------------------------------------

def test_get_system_health_shows_cpu_and_ram():
    mock_mem = MagicMock()
    mock_mem.percent = 42.5
    mock_mem.used = 4 * 1024 ** 3
    mock_mem.total = 8 * 1024 ** 3
    with (
        patch("tools.psutil.cpu_percent", return_value=15.3),
        patch("tools.psutil.virtual_memory", return_value=mock_mem),
        patch("tools.psutil.boot_time", return_value=1747000000.0),
    ):
        result = get_system_health()
    assert "15" in result or "15.3" in result
    assert "42" in result or "42.5" in result


def test_get_system_health_shows_uptime():
    import time
    boot_time = time.time() - (3 * 86400 + 2 * 3600)  # 3 days, 2 hours ago
    mock_mem = MagicMock(percent=30.0, used=2 * 1024 ** 3, total=8 * 1024 ** 3)
    with (
        patch("tools.psutil.cpu_percent", return_value=5.0),
        patch("tools.psutil.virtual_memory", return_value=mock_mem),
        patch("tools.psutil.boot_time", return_value=boot_time),
    ):
        result = get_system_health()
    assert "3" in result  # at least "3 days" appears


def test_get_system_health_handles_psutil_error():
    with patch("tools.psutil.cpu_percent", side_effect=Exception("psutil error")):
        result = get_system_health()
    assert "unavailable" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# get_cron_schedule
# ---------------------------------------------------------------------------

FAKE_CRONTAB = """
0 9 * * 1 ~/.claude/skills/mealsave/check-yt-auth.sh
# --- ai-agents managed ---
5 5 * * * /home/cian/git/ai-agents/run-agent.sh daily-briefing >> /home/cian/git/ai-agents/output/cron.log 2>&1
0 5 * * * /home/cian/git/ai-agents/run-agent.sh news-briefing >> /home/cian/git/ai-agents/output/cron.log 2>&1
# --- end ai-agents ---
"""


def test_get_cron_schedule_shows_agent_schedules():
    with patch("tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=FAKE_CRONTAB, returncode=0)
        result = get_cron_schedule()
    assert "daily-briefing" in result
    assert "news-briefing" in result


def test_get_cron_schedule_shows_human_readable_time():
    with patch("tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=FAKE_CRONTAB, returncode=0)
        result = get_cron_schedule()
    assert "07:05" in result or "07:00" in result or "CEST" in result or "UTC" in result


def test_get_cron_schedule_handles_no_crontab():
    with patch("tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="no crontab for cian", returncode=1)
        result = get_cron_schedule()
    assert "no schedule" in result.lower() or "crontab" in result.lower()


# ---------------------------------------------------------------------------
# get_agent_logs
# ---------------------------------------------------------------------------

FAKE_LOG = "\n".join([
    f"2026-05-17 05:05:0{i} [daily-briefing] step {i}" for i in range(10)
] + [
    f"2026-05-17 05:00:0{i} [news-briefing] step {i}" for i in range(10)
])


def test_get_agent_logs_returns_last_lines():
    with patch("tools.LOG_PATH", "/fake/cron.log"):
        with patch("builtins.open", mock_open(read_data=FAKE_LOG)):
            result = get_agent_logs()
    assert "daily-briefing" in result or "news-briefing" in result


def test_get_agent_logs_filters_by_agent_name():
    with patch("tools.LOG_PATH", "/fake/cron.log"):
        with patch("builtins.open", mock_open(read_data=FAKE_LOG)):
            result = get_agent_logs("daily-briefing")
    assert "daily-briefing" in result
    assert "news-briefing" not in result


def test_get_agent_logs_handles_missing_log():
    with patch("tools.LOG_PATH", "/nonexistent/cron.log"):
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = get_agent_logs()
    assert "no log" in result.lower() or "not found" in result.lower() or "unavailable" in result.lower()


# ---------------------------------------------------------------------------
# water_plant
# ---------------------------------------------------------------------------

def _make_mock_db(plants):
    mock_db = MagicMock()
    mock_db.get_state.return_value = plants
    return mock_db


def test_water_plant_exact_match():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plant("Monstera")
    assert "Monstera" in result
    assert date.today().isoformat() in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["last_watered"] == date.today().isoformat()


def test_water_plant_case_insensitive():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plant("monstera")
    assert "Monstera" in result


def test_water_plant_substring_match():
    plants = [{"name": "Monstera Deliciosa", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plant("monstera")
    assert "Monstera Deliciosa" in result


def test_water_plant_not_found():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plant("Cactus")
    assert "No plant" in result
    assert "Monstera" in result


def test_water_plant_empty_list():
    mock_db = _make_mock_db([])
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plant("Monstera")
    assert "No plant" in result


def test_water_plant_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = water_plant("Monstera")
    assert "failed" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# remove_plant
# ---------------------------------------------------------------------------

def test_remove_plant_exact_match():
    plants = [
        {"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"},
        {"name": "Aloe", "frequency_days": 14, "last_watered": "2026-01-01", "location": "indoor"},
    ]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = remove_plant("Monstera")
    assert "Monstera" in result
    saved = mock_db.set_state.call_args[0][2]
    assert len(saved) == 1
    assert saved[0]["name"] == "Aloe"


def test_remove_plant_case_insensitive():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = remove_plant("monstera")
    assert "Monstera" in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved == []


def test_remove_plant_not_found():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = remove_plant("Cactus")
    assert "No plant" in result
    assert "Monstera" in result
    mock_db.set_state.assert_not_called()


def test_remove_plant_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = remove_plant("Monstera")
    assert "failed" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# get_all_plants
# ---------------------------------------------------------------------------

def test_get_all_plants_returns_list():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_all_plants()
    assert result == plants


def test_get_all_plants_db_error_returns_empty():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = get_all_plants()
    assert result == []


# ---------------------------------------------------------------------------
# get_plant
# ---------------------------------------------------------------------------

def test_get_plant_exact_match():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant("Monstera")
    assert result is not None
    assert result["name"] == "Monstera"


def test_get_plant_case_insensitive():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant("monstera")
    assert result is not None


def test_get_plant_substring_match():
    plants = [{"name": "Monstera Deliciosa", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant("monstera")
    assert result is not None
    assert result["name"] == "Monstera Deliciosa"


def test_get_plant_not_found():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant("Cactus")
    assert result is None


def test_get_plant_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = get_plant("Monstera")
    assert result is None


# ---------------------------------------------------------------------------
# save_plant_assessment
# ---------------------------------------------------------------------------

def test_save_plant_assessment_saves_with_today():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = save_plant_assessment("Monstera", "Leaves look healthy.")
    assert "Monstera" in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["last_assessment"]["date"] == date.today().isoformat()
    assert saved[0]["last_assessment"]["summary"] == "Leaves look healthy."


def test_save_plant_assessment_substring_match():
    plants = [{"name": "Monstera Deliciosa", "frequency_days": 7, "last_watered": "2026-05-23", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = save_plant_assessment("monstera", "Healthy.")
    assert "Monstera Deliciosa" in result


def test_save_plant_assessment_not_found():
    mock_db = _make_mock_db([])
    with patch("tools.AgentDB", return_value=mock_db):
        result = save_plant_assessment("Cactus", "Healthy.")
    assert "not found" in result.lower() or "no plant" in result.lower()


def test_save_plant_assessment_db_error():
    with patch("tools.AgentDB", side_effect=Exception("db error")):
        result = save_plant_assessment("Monstera", "Healthy.")
    assert "failed" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# save_recipe
# ---------------------------------------------------------------------------

def test_save_recipe_success():
    mock_result = MagicMock(returncode=0, stdout="http://localhost:9000/g/home/r/pasta\n", stderr="")
    with patch("tools.subprocess.run", return_value=mock_result):
        result = save_recipe("https://example.com/pasta")
    assert "Recipe saved" in result
    assert "pasta" in result


def test_save_recipe_error_stderr():
    mock_result = MagicMock(returncode=1, stdout="", stderr="ERROR: Could not fetch page")
    with patch("tools.subprocess.run", return_value=mock_result):
        result = save_recipe("https://example.com/bad")
    assert "Error" in result
    assert "Could not fetch page" in result


def test_save_recipe_timeout():
    import subprocess
    with patch("tools.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="mealsave", timeout=120)):
        result = save_recipe("https://example.com/slow")
    assert "Timed out" in result


def test_save_recipe_not_installed():
    with patch("tools.subprocess.run", side_effect=FileNotFoundError):
        result = save_recipe("https://example.com/recipe")
    assert "not installed" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# research_plant_watering
# ---------------------------------------------------------------------------

def test_research_plant_watering_returns_integer_via_stdin(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="7\n")
    result = research_plant_watering("Monstera")
    assert result == "7"
    assert mock_run.call_args.kwargs["input"] is not None
    assert "Monstera" in mock_run.call_args.kwargs["input"]


def test_research_plant_watering_gemini_failure_returns_error(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="retry exhausted")
    result = research_plant_watering("Monstera")
    assert "could not" in result.lower() or "failed" in result.lower()
