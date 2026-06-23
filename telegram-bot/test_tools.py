"""Tests for server concierge tool functions.

All external dependencies (AgentDB, subprocess, psutil, file I/O) are mocked.
Each test describes the expected output contract, not implementation details.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import copy
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
    water_plants,
    update_plant,
    remove_plant,
    save_recipe,
    get_all_plants,
    get_plant,
    save_plant_assessment,
    research_plant_watering,
    research_plant_sunlight,
    research_plant_water_sensitivity,
    add_plant,
    set_plant_frequency,
    create_observation_note,
    create_knowledge_note,
    list_garden_notes,
    read_garden_note,
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
# update_plant
# ---------------------------------------------------------------------------

def test_update_plant_location():
    plants = [{"name": "Gazania", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Gazania", location="outdoor")
    assert "outdoor" in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["location"] == "outdoor"


def test_update_plant_frequency():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Monstera", frequency_days=14)
    assert "14" in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["frequency_days"] == 14


def test_update_plant_case_insensitive():
    plants = [{"name": "Gazania", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("gazania", location="outdoor")
    assert "Gazania" in result


def test_update_plant_not_found():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Cactus", location="outdoor")
    assert "No plant" in result
    mock_db.set_state.assert_not_called()


def test_update_plant_no_changes():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Monstera")
    assert "nothing" in result.lower() or "specify" in result.lower()
    mock_db.set_state.assert_not_called()


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


def test_research_plant_watering_antigravity_failure_returns_error(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="retry exhausted")
    result = research_plant_watering("Monstera")
    assert "could not" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# research_plant_sunlight
# ---------------------------------------------------------------------------

def test_research_plant_sunlight_returns_valid_value(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="partial shade\n")
    result = research_plant_sunlight("Monstera")
    assert result == "partial shade"
    assert "Monstera" in mock_run.call_args.kwargs["input"]


def test_research_plant_sunlight_extracts_from_verbose_response(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="Monstera prefers partial shade conditions.\n")
    result = research_plant_sunlight("Monstera")
    assert result == "partial shade"


def test_research_plant_sunlight_failure_returns_error(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    result = research_plant_sunlight("Monstera")
    assert "could not" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# update_plant sunlight
# ---------------------------------------------------------------------------

def test_update_plant_sunlight():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Monstera", sunlight="partial shade")
    assert "partial shade" in result
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["sunlight"] == "partial shade"


def test_update_plant_invalid_sunlight_ignored():
    plants = [{"name": "Monstera", "frequency_days": 7, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = update_plant("Monstera", sunlight="bright indirect")
    assert "nothing" in result.lower() or "specify" in result.lower()


# ---------------------------------------------------------------------------
# get_plant_status sunlight display
# ---------------------------------------------------------------------------

def test_get_plant_status_shows_sunlight():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Monstera", "frequency_days": 10, "last_watered": "2026-05-20",
         "location": "indoor", "sunlight": "partial shade"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "partial shade" in result


def test_get_plant_status_no_sunlight_omits_field():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Aloe", "frequency_days": 14, "last_watered": "2026-05-20", "location": "indoor"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "unknown" not in result
    assert "full sun" not in result


def test_get_plant_status_shows_adjusted_date_and_reason():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-17", "location": "indoor"}
    ]
    mock_db.get_plant_weather_cache.return_value = [
        {"plant_name": "Monstera", "adjusted_date": "2026-05-26", "adjustment_reason": "2d later — cold & humid"}
    ]
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "2026-05-26" in result
    assert "cold" in result.lower() or "humid" in result.lower()


def test_get_plant_status_uses_base_date_when_no_cache():
    mock_db = MagicMock()
    mock_db.get_state.return_value = [
        {"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-17", "location": "indoor"}
    ]
    mock_db.get_plant_weather_cache.return_value = []
    with patch("tools.AgentDB", return_value=mock_db):
        result = get_plant_status()
    assert "2026-05-24" in result  # base date: 2026-05-17 + 7 days


# ---------------------------------------------------------------------------
# research_plant_water_sensitivity
# ---------------------------------------------------------------------------

def test_research_plant_water_sensitivity_returns_valid_value(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="high\n")
    result = research_plant_water_sensitivity("Cactus")
    assert result == "high"
    assert mock_run.call_args.kwargs["input"] is not None
    assert "Cactus" in mock_run.call_args.kwargs["input"]


def test_research_plant_water_sensitivity_extracts_from_verbose_response(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="The cactus has high water sensitivity.\n")
    result = research_plant_water_sensitivity("Cactus")
    assert result == "high"


def test_research_plant_water_sensitivity_returns_medium_on_unknown(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="I don't know\n")
    result = research_plant_water_sensitivity("Cactus")
    assert "could not" in result.lower() or "failed" in result.lower() or result not in ("high", "medium", "low")


def test_research_plant_water_sensitivity_failure_returns_error(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="timeout")
    result = research_plant_water_sensitivity("Cactus")
    assert "could not" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# add_plant with water_sensitivity
# ---------------------------------------------------------------------------

def test_add_plant_stores_water_sensitivity(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="high\n")
    mock_db = MagicMock()
    mock_db.get_state.return_value = []
    with patch("tools.AgentDB", return_value=mock_db):
        result = add_plant("Cactus", 14, "indoor")
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["water_sensitivity"] == "high"
    assert "sensitivity: high" in result


def test_add_plant_defaults_sensitivity_to_medium_on_research_failure(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    mock_db = MagicMock()
    mock_db.get_state.return_value = []
    with patch("tools.AgentDB", return_value=mock_db):
        result = add_plant("Cactus", 14, "indoor")
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["water_sensitivity"] == "medium"


# ---------------------------------------------------------------------------
# water_plants (bulk by location)
# ---------------------------------------------------------------------------

MIXED_PLANTS = [
    {"name": "Gazania", "frequency_days": 3, "last_watered": "2026-01-01", "location": "outdoor"},
    {"name": "Lavender", "frequency_days": 7, "last_watered": "2026-01-01", "location": "outdoor"},
    {"name": "Monstera", "frequency_days": 10, "last_watered": "2026-01-01", "location": "indoor"},
]


def test_water_plants_outdoor_updates_all_outdoor():
    mock_db = _make_mock_db(copy.deepcopy(MIXED_PLANTS))
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plants("outdoor")
    saved = mock_db.set_state.call_args[0][2]
    outdoor = [p for p in saved if p["location"] == "outdoor"]
    assert all(p["last_watered"] == date.today().isoformat() for p in outdoor)
    assert "Gazania" in result
    assert "Lavender" in result


def test_water_plants_outdoor_does_not_touch_indoor():
    mock_db = _make_mock_db(copy.deepcopy(MIXED_PLANTS))
    with patch("tools.AgentDB", return_value=mock_db):
        water_plants("outdoor")
    saved = mock_db.set_state.call_args[0][2]
    indoor = next(p for p in saved if p["location"] == "indoor")
    assert indoor["last_watered"] == "2026-01-01"


def test_water_plants_indoor_updates_only_indoor():
    mock_db = _make_mock_db(copy.deepcopy(MIXED_PLANTS))
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plants("indoor")
    saved = mock_db.set_state.call_args[0][2]
    indoor = [p for p in saved if p["location"] == "indoor"]
    outdoor = [p for p in saved if p["location"] == "outdoor"]
    assert all(p["last_watered"] == date.today().isoformat() for p in indoor)
    assert all(p["last_watered"] == "2026-01-01" for p in outdoor)
    assert "Monstera" in result


def test_water_plants_no_match_returns_not_found():
    plants = [{"name": "Monstera", "frequency_days": 10, "last_watered": "2026-01-01", "location": "indoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plants("outdoor")
    assert "no outdoor plants" in result.lower()
    mock_db.set_state.assert_not_called()


def test_water_plants_empty_list_returns_not_found():
    mock_db = _make_mock_db([])
    with patch("tools.AgentDB", return_value=mock_db):
        result = water_plants("outdoor")
    assert "no outdoor plants" in result.lower()
    mock_db.set_state.assert_not_called()


def test_water_plants_db_error_returns_error():
    with patch("tools.AgentDB", side_effect=Exception("db locked")):
        result = water_plants("outdoor")
    assert "failed" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# set_plant_frequency
# ---------------------------------------------------------------------------

def test_set_plant_frequency_clamps_and_logs():
    plants = [{"name": "Lantana", "frequency_days": 7, "last_watered": "2026-05-31", "location": "outdoor"}]
    mock_db = _make_mock_db(plants)
    with patch("tools.AgentDB", return_value=mock_db), \
         patch("tools.fetch_weather", return_value=None), \
         patch("tools.append_frequency_history") as mock_hist:
        result = set_plant_frequency("Lantana", 99, "user request")
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["baseline_frequency_days"] == 30   # clamped from 99
    assert saved[0]["frequency_days"] == 30            # no weather -> baseline
    assert "Lantana" in result and "30" in result
    mock_hist.assert_called_once()
    args = mock_hist.call_args[0]
    assert args[0] == "Lantana" and args[1] == 7 and args[2] == 30
    assert "user request" in args[3]


def test_set_plant_frequency_not_found():
    mock_db = _make_mock_db([])
    with patch("tools.AgentDB", return_value=mock_db):
        result = set_plant_frequency("Ghost", 5)
    assert "No plant" in result


# ---------------------------------------------------------------------------
# Garden notes tools
# ---------------------------------------------------------------------------

def test_create_observation_note_tool(monkeypatch):
    """Test wrapper for create_observation_note from garden_notes."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "create_observation_note",
                        lambda plant_slug, date, title, status, body: "docs/plant-observations/test/2026-06-23-test.md")
    monkeypatch.setattr(tools.garden_notes, "append_linked_note", lambda plant_slug, rel, title: True)

    out = tools.create_observation_note(plant_name="Test", title="Test Title", body="Test body")
    assert "plant-observations" in out
    assert "Saved observation note" in out


def test_create_knowledge_note_tool(monkeypatch):
    """Test wrapper for create_knowledge_note from garden_notes."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "create_knowledge_note",
                        lambda topic, body, related_plants=(): "docs/garden-knowledge/test-topic.md")

    out = tools.create_knowledge_note(topic="Test Topic", body="Test body")
    assert "garden-knowledge" in out
    assert "Saved knowledge note" in out


def test_create_knowledge_note_with_related_plants(monkeypatch):
    """Test create_knowledge_note with comma-separated plant list."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "create_knowledge_note",
                        lambda topic, body, related_plants=(): f"docs/garden-knowledge/{topic}.md")

    out = tools.create_knowledge_note(topic="Watering Tips", body="content", related_plants="Monstera, Aloe, Lavender")
    assert "Watering Tips" in out


def test_list_garden_notes_tool(monkeypatch):
    """Test wrapper for list_garden_notes from garden_notes."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "list_garden_notes",
                        lambda: [
                            {"path": "docs/plant-observations/monstera/2026-06-23-test.md", "type": "observation", "title": "test"},
                            {"path": "docs/garden-knowledge/watering.md", "type": "knowledge", "title": "watering"},
                        ])

    out = tools.list_garden_notes()
    assert "[observation]" in out
    assert "[knowledge]" in out
    assert "docs/plant-observations" in out
    assert "docs/garden-knowledge" in out


def test_list_garden_notes_empty(monkeypatch):
    """Test list_garden_notes when no notes exist."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "list_garden_notes", lambda: [])

    out = tools.list_garden_notes()
    assert "No garden notes" in out


def test_read_garden_note_tool(monkeypatch):
    """Test wrapper for read_garden_note from garden_notes."""
    import tools
    test_content = "---\ntype: observation\n---\n# Test Note\n\nContent here"
    monkeypatch.setattr(tools.garden_notes, "read_garden_note",
                        lambda path: test_content)

    out = tools.read_garden_note(path="docs/plant-observations/test/2026-06-23-test.md")
    assert "Test Note" in out
    assert test_content == out


def test_read_garden_note_not_found(monkeypatch):
    """Test read_garden_note when note doesn't exist."""
    import tools
    monkeypatch.setattr(tools.garden_notes, "read_garden_note",
                        lambda path: (_ for _ in ()).throw(ValueError("note not found or outside note dirs: invalid.md")))

    out = tools.read_garden_note(path="invalid.md")
    assert "Error" in out or "not found" in out
