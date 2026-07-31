"""Tests for agents.plant_digest — consolidated garden digest for external sync."""

import agents.plant_profiles as pp
import agents.plant_digest as pd


def _plant(**overrides):
    base = {
        "name": "Monstera",
        "location": "indoor",
        "sunlight": "bright indirect",
        "water_sensitivity": "medium",
        "frequency_days": 7,
        "baseline_frequency_days": 7,
        "last_watered": "2026-07-20",
    }
    base.update(overrides)
    return base


def test_empty_plant_list_still_produces_title():
    out = pd.build_digest([], [])
    assert out.startswith("# Plant Health Digest")


def test_summary_table_and_section_include_plant_name():
    out = pd.build_digest([_plant()], [])
    assert "Monstera" in out
    assert "## Monstera" in out


def test_weather_adjusted_next_water_included():
    weather_cache = [{
        "plant_name": "Monstera",
        "adjusted_date": "2026-07-28",
        "adjustment_reason": "heatwave",
    }]
    out = pd.build_digest([_plant()], weather_cache)
    assert "2026-07-28" in out
    assert "heatwave" in out


def test_missing_weather_cache_entry_does_not_crash():
    out = pd.build_digest([_plant(name="Yucca")], [])
    assert "## Yucca" in out


def test_profile_context_included_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    (tmp_path / "monstera.md").write_text(
        "---\ntype: plant\n---\n"
        "## Current Observations\n- new growth this week\n"
    )
    out = pd.build_digest([_plant()], [])
    assert "new growth this week" in out


def test_multiple_plants_each_get_own_section():
    plants = [_plant(name="Monstera"), _plant(name="Yucca", location="outdoor")]
    out = pd.build_digest(plants, [])
    assert "## Monstera" in out
    assert "## Yucca" in out
