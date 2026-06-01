"""Tests for agents.plant_agent — gate logic, short-circuit, rate limiting, output parsing."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(tmp_path):
    from agents.plant_agent import PlantAgent
    a = PlantAgent(db_path=tmp_path / "test.db")
    a.run_id = a.db.start_run(a.name)
    return a


def _make_plant(name="Fern", frequency_days=7, last_watered="2026-05-01",
                location="indoor", needs_photo=False):
    return {
        "name": name,
        "frequency_days": frequency_days,
        "last_watered": last_watered,
        "location": location,
        "sunlight": "indirect",
        "water_sensitivity": "medium",
        "needs_photo": needs_photo,
    }


# ---------------------------------------------------------------------------
# 1. _gate() logic
# ---------------------------------------------------------------------------

class TestGate:
    def test_returns_true_when_no_state_exists(self, agent):
        assert agent._gate("weather", 12) is True

    def test_returns_true_when_last_ran_beyond_window(self, agent):
        # Store a timestamp > 13 hours ago; gate window = 12h
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        agent.set_state("last_weather", old_ts)
        assert agent._gate("weather", 12) is True

    def test_returns_false_when_last_ran_within_window(self, agent):
        # Store a timestamp 1 hour ago; gate window = 12h
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        agent.set_state("last_weather", recent_ts)
        assert agent._gate("weather", 12) is False

    def test_returns_true_exactly_at_boundary(self, agent):
        # _gate uses strict >, so exactly 12h elapsed returns True (just crossed the threshold)
        # In practice there are always a few ms of execution time, so this is rarely False.
        # We test the edge: exactly 12h - 1s → still within window → False.
        just_under_ts = (datetime.now(timezone.utc) - timedelta(hours=12) + timedelta(seconds=1)).isoformat()
        agent.set_state("last_weather", just_under_ts)
        assert agent._gate("weather", 12) is False

    def test_naive_datetime_treated_as_utc(self, agent):
        # Naive ISO string (no tzinfo) — stored without 'Z' suffix
        old_naive = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(tzinfo=None).isoformat()
        agent.set_state("last_sync", old_naive)
        assert agent._gate("sync", 24) is True


# ---------------------------------------------------------------------------
# 2. _create_tasks() short-circuit
# ---------------------------------------------------------------------------

class TestCreateTasksShortCircuit:

    def _setup_context(self, agent, plants, weather=None):
        agent.context["plan"] = {"plants": plants, "weather_cache": {}}

    def test_returns_skipped_when_no_plants_due(self, agent):
        # Plant watered today, due in 7 days — not due
        plant = _make_plant(last_watered="2026-05-28", frequency_days=7)
        self._setup_context(agent, [plant])

        with patch.object(agent, "synthesize") as mock_synth, \
             patch("agents.plant_agent.fetch_weather", return_value=None):
            result = agent._create_tasks()

        assert result.get("skipped") is True
        assert result.get("reason") == "no_due_plants"
        mock_synth.assert_not_called()

    def test_does_not_call_synthesize_on_short_circuit(self, agent):
        plant = _make_plant(last_watered="2026-05-28", frequency_days=7)
        self._setup_context(agent, [plant])

        synthesize_spy = MagicMock()
        agent.synthesize = synthesize_spy

        with patch("agents.plant_agent.fetch_weather", return_value=None):
            agent._create_tasks()

        synthesize_spy.assert_not_called()

    def test_gate_skips_without_calling_synthesize(self, agent):
        """If the gate blocks (ran recently), synthesize is also not called."""
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        agent.set_state("last_create_tasks", recent_ts)

        plant = _make_plant(last_watered="2026-05-01", frequency_days=7)
        self._setup_context(agent, [plant])

        synthesize_spy = MagicMock()
        agent.synthesize = synthesize_spy

        with patch("agents.plant_agent.fetch_weather", return_value=None):
            result = agent._create_tasks()

        assert result.get("skipped") is True
        synthesize_spy.assert_not_called()


# ---------------------------------------------------------------------------
# 3. _photo_requests() rate limiting
# ---------------------------------------------------------------------------

class TestPhotoRequests:

    def _setup_context(self, agent, plants):
        agent.context["plan"] = {"plants": plants, "weather_cache": {}}

    def test_skips_when_no_telegram_token(self, agent):
        plant = _make_plant(needs_photo=True)
        self._setup_context(agent, [plant])

        with patch.dict(os.environ, {}, clear=True):
            # Ensure both vars absent
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_USER_ID", None)
            result = agent._photo_requests()

        assert result.get("skipped") is True
        assert result.get("reason") == "no_telegram_config"

    def test_skips_when_only_token_missing(self, agent):
        plant = _make_plant(needs_photo=True)
        self._setup_context(agent, [plant])

        env = {"TELEGRAM_USER_ID": "123456"}
        with patch.dict(os.environ, env, clear=True):
            result = agent._photo_requests()

        assert result.get("skipped") is True

    def test_skips_plant_if_photo_requested_within_3_days(self, agent):
        plant = _make_plant(needs_photo=True)
        self._setup_context(agent, [plant])

        # Record a request 1 day ago
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        agent.set_state("photo_request_rates", {"Fern": recent_ts})

        mock_resp = MagicMock()
        mock_resp.ok = True

        env = {"CONCIERGE_BOT_TOKEN": "fake-token", "TELEGRAM_USER_ID": "123456"}
        with patch.dict(os.environ, env), \
             patch("agents.plant_agent.requests.post", return_value=mock_resp) as mock_post:
            result = agent._photo_requests()

        mock_post.assert_not_called()
        assert result["sent"] == 0

    def test_sends_request_when_no_rate_limit_entry(self, agent):
        # No previous request recorded — plant needs_photo=True, no assessment
        plant = _make_plant(needs_photo=True)
        self._setup_context(agent, [plant])

        mock_resp = MagicMock()
        mock_resp.ok = True

        env = {"CONCIERGE_BOT_TOKEN": "fake-token", "TELEGRAM_USER_ID": "123456"}
        with patch.dict(os.environ, env), \
             patch("agents.plant_agent.requests.post", return_value=mock_resp) as mock_post:
            result = agent._photo_requests()

        mock_post.assert_called_once()
        assert result["sent"] == 1

    def test_sends_request_when_rate_limit_expired(self, agent):
        plant = _make_plant(needs_photo=True)
        self._setup_context(agent, [plant])

        # Last request 5 days ago — past the 3-day limit
        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        agent.set_state("photo_request_rates", {"Fern": old_ts})

        mock_resp = MagicMock()
        mock_resp.ok = True

        env = {"CONCIERGE_BOT_TOKEN": "fake-token", "TELEGRAM_USER_ID": "123456"}
        with patch.dict(os.environ, env), \
             patch("agents.plant_agent.requests.post", return_value=mock_resp) as mock_post:
            result = agent._photo_requests()

        mock_post.assert_called_once()
        assert result["sent"] == 1

    def test_no_request_sent_when_plant_does_not_need_photo_and_has_recent_assessment(self, agent):
        last_assessed = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        plant = {**_make_plant(), "needs_photo": False, "last_assessment": {"date": last_assessed}}
        self._setup_context(agent, [plant])

        mock_resp = MagicMock()
        mock_resp.ok = True

        env = {"CONCIERGE_BOT_TOKEN": "fake-token", "TELEGRAM_USER_ID": "123456"}
        with patch.dict(os.environ, env), \
             patch("agents.plant_agent.requests.post", return_value=mock_resp) as mock_post:
            result = agent._photo_requests()

        mock_post.assert_not_called()
        assert result["sent"] == 0


# ---------------------------------------------------------------------------
# 3b. _build_status_table() — chronological ordering
# ---------------------------------------------------------------------------

class TestBuildStatusTable:
    today = datetime(2026, 6, 1, tzinfo=timezone.utc).date()

    def _rows(self, table):
        # Drop header + separator, return the plant-name column of each data row
        lines = [l for l in table.splitlines() if l.startswith("|")][2:]
        return [l.split("|")[1].strip() for l in lines]

    def test_rows_sorted_by_next_water_date(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Late", frequency_days=10, last_watered="2026-05-30"),    # next 2026-06-09
            _make_plant(name="Soon", frequency_days=3, last_watered="2026-05-30"),      # next 2026-06-02
            _make_plant(name="Overdue", frequency_days=2, last_watered="2026-05-25"),   # next 2026-05-27
        ]
        table = _build_status_table(plants, {}, self.today)
        assert self._rows(table) == ["Overdue", "Soon", "Late"]

    def test_weather_adjusted_date_drives_order(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Base", frequency_days=2, last_watered="2026-05-31"),  # next 2026-06-02
            _make_plant(name="Adjusted", frequency_days=10, last_watered="2026-05-31"),
        ]
        cache = {"Adjusted": {"adjusted_date": "2026-06-01", "adjustment_reason": "heatwave"}}
        table = _build_status_table(plants, cache, self.today)
        assert self._rows(table) == ["Adjusted", "Base"]

    def test_ties_broken_by_name(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Zinnia", frequency_days=5, last_watered="2026-05-30"),
            _make_plant(name="Aster", frequency_days=5, last_watered="2026-05-30"),
        ]
        table = _build_status_table(plants, {}, self.today)
        assert self._rows(table) == ["Aster", "Zinnia"]

    def test_no_plants_returns_placeholder(self):
        from agents.plant_agent import _build_status_table
        assert _build_status_table([], {}, self.today) == "No plants tracked."


# ---------------------------------------------------------------------------
# 4. _apply_intelligence_output() — output parsing
# ---------------------------------------------------------------------------

class TestApplyIntelligenceOutput:

    def _make_profile(self, path: Path, plant: dict):
        """Write a minimal profile doc to path."""
        from agents.plant_agent import _create_profile_doc
        _create_profile_doc(path, plant)

    def test_profile_block_appends_notes_to_existing_doc(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Peace Lily")
        profile_path = plants_dir / "peace-lily.md"
        self._make_profile(profile_path, plant)
        assert "## Intelligence Notes" in profile_path.read_text()

        output = "[PROFILE:Peace Lily]Leaves look healthy, no yellowing.[/PROFILE]"

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        content = profile_path.read_text()
        assert "Leaves look healthy, no yellowing." in content

    def test_profile_block_creates_doc_if_missing(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Spider Plant")
        profile_path = plants_dir / "spider-plant.md"
        assert not profile_path.exists()

        output = "[PROFILE:Spider Plant]First observation: trailing stems.[/PROFILE]"

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        assert profile_path.exists()
        content = profile_path.read_text()
        assert "First observation: trailing stems." in content

    def test_profile_block_skipped_for_unknown_plant(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        # No matching plant in the list
        output = "[PROFILE:Ghost Plant]Some notes.[/PROFILE]"
        profile_path = plants_dir / "ghost-plant.md"

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [])

        # No doc created if plant not in list
        assert not profile_path.exists()

    def test_needs_photo_sets_flag_on_matching_plant(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Orchid", needs_photo=False)

        output = "[NEEDS_PHOTO]Orchid[/NEEDS_PHOTO]"

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        # Check that DB was updated with needs_photo=True
        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        assert plants_in_db is not None
        orchid = next(p for p in plants_in_db if p["name"] == "Orchid")
        assert orchid["needs_photo"] is True

    def test_needs_photo_clears_flag_for_unlisted_plants(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        # Plant had needs_photo=True but is NOT in [NEEDS_PHOTO] block
        plant = _make_plant(name="Cactus", needs_photo=True)

        output = "[NEEDS_PHOTO][/NEEDS_PHOTO]"  # Empty — nobody flagged

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        assert plants_in_db is not None
        cactus = next(p for p in plants_in_db if p["name"] == "Cactus")
        assert cactus["needs_photo"] is False

    def test_empty_needs_photo_block_is_handled_gracefully(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Fern", needs_photo=False)
        output = "[NEEDS_PHOTO]   [/NEEDS_PHOTO]"

        # Should not raise
        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

    def test_no_needs_photo_block_leaves_db_unchanged(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Fern", needs_photo=True)
        # Pre-seed DB state
        agent.db.set_state("daily-briefing", "plants", [plant])

        output = "Just a profile block, nothing else."

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        # DB state should be unchanged since no NEEDS_PHOTO block was present
        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        assert plants_in_db[0]["needs_photo"] is True

    def test_multiple_profile_blocks_all_applied(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        fern = _make_plant(name="Fern")
        cactus = _make_plant(name="Cactus")
        for plant in [fern, cactus]:
            self._make_profile(plants_dir / f"{plant['name'].lower()}.md", plant)

        output = (
            "[PROFILE:Fern]Fern looks lush.[/PROFILE]\n"
            "[PROFILE:Cactus]Cactus is thriving.[/PROFILE]"
        )

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [fern, cactus])

        assert "Fern looks lush." in (plants_dir / "fern.md").read_text()
        assert "Cactus is thriving." in (plants_dir / "cactus.md").read_text()


# ---------------------------------------------------------------------------
# Weather recompute + baseline migration (Task 4)
# ---------------------------------------------------------------------------

from datetime import date


class TestWeatherRecompute:
    HOT = {"current": {"temp_c": 28, "humidity_pct": 50},
           "recent_precip_mm": 0.0,
           "forecast": [{"temp_max_c": 33, "precip_mm": 0.0},
                        {"temp_max_c": 34, "precip_mm": 0.0}]}

    def _agent(self, plants, weather, monkeypatch, tmp_path):
        from agents import plant_agent as mod
        monkeypatch.setattr(mod, "fetch_weather", lambda: weather)
        a = mod.PlantAgent(db_path=tmp_path / "wr.db")
        a.context = {"plan": {"plants": plants, "weather_cache": {}}}
        return a

    def test_migrates_baseline(self, monkeypatch, tmp_path):
        plants = [{"name": "X", "frequency_days": 7, "location": "indoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["baseline_frequency_days"] == 7

    def test_folds_weather(self, monkeypatch, tmp_path):
        plants = [{"name": "X", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "sunlight": "full sun", "last_watered": "2026-05-31"}]
        self._agent(plants, self.HOT, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["frequency_days"] == 4   # 7-3

    def test_weather_failure_resets_to_baseline(self, monkeypatch, tmp_path):
        plants = [{"name": "X", "frequency_days": 4, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["frequency_days"] == 7


# ---------------------------------------------------------------------------
# due_water_tasks (Task 5)
# ---------------------------------------------------------------------------

from agents.plant_agent import due_water_tasks


class TestDueWaterTasks:
    HOT = TestWeatherRecompute.HOT  # heatwave: 2 days >30, dry

    def test_includes_overdue(self):
        today = date(2026, 6, 1)
        plants = [{"name": "X", "frequency_days": 4, "last_watered": "2026-05-27", "location": "indoor"}]
        assert due_water_tasks(plants, today, None) == [{"name": "X", "due": "2026-05-31"}]

    def test_excludes_future(self):
        today = date(2026, 6, 1)
        plants = [{"name": "Y", "frequency_days": 7, "last_watered": "2026-05-31", "location": "indoor"}]
        assert due_water_tasks(plants, today, None) == []

    def test_heatwave_creates_one_day_early(self):
        today = date(2026, 6, 1)
        plants = [{"name": "Z", "frequency_days": 3, "last_watered": "2026-05-30", "location": "outdoor"}]
        assert due_water_tasks(plants, today, self.HOT) == [{"name": "Z", "due": "2026-06-02", "heatwave": True}]


# ---------------------------------------------------------------------------
# Intelligence [FREQUENCY] application (Task 6)
# ---------------------------------------------------------------------------

class TestIntelligenceFrequency:
    def test_applies_with_step_limit_and_logs(self, tmp_path, monkeypatch):
        from agents import plant_agent as mod
        from agents import plant_profiles as pp
        monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
        monkeypatch.setattr(mod, "fetch_weather", lambda: None)
        (tmp_path / "lantana.md").write_text(
            "# Lantana\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n")
        a = mod.PlantAgent(db_path=tmp_path / "intel.db")
        plants = [{"name": "Lantana", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        a.context = {"plan": {"plants": plants}}
        a._apply_intelligence_output("[FREQUENCY]\nLantana — 3 — wilting\n[/FREQUENCY]", plants)
        assert plants[0]["baseline_frequency_days"] == 5   # 7 -> 5 (step -2)
        assert plants[0]["frequency_days"] == 5            # no weather -> baseline
        assert "7→5 days" in (tmp_path / "lantana.md").read_text()
