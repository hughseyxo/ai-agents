"""Tests for the canonical tool spec source.

tool_specs.py is the single source of truth for the concierge bot's tools.
It must produce three consistent views: OpenAI function format (for the
OpenRouter fallback path), MCP format (for the concierge MCP server), and a
name→callable map (for dispatch).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tool_specs import SPECS, openai_tools, mcp_tools, func_map


EXPECTED_TOOLS = {
    "get_agent_status", "get_plant_status", "get_yopflix_status",
    "get_system_health", "get_cron_schedule", "get_agent_logs",
    "run_travel_agent", "get_travel_report", "water_plants", "water_plant",
    "add_plant", "update_plant", "research_plant_sunlight",
    "research_plant_watering", "save_recipe", "get_plant", "get_all_plants",
    "remove_plant",
}


def test_func_map_covers_all_expected_tools():
    fm = func_map()
    assert set(fm.keys()) == EXPECTED_TOOLS


def test_func_map_values_all_callable():
    assert all(callable(fn) for fn in func_map().values())


def test_openai_tools_format():
    for t in openai_tools():
        assert t["type"] == "function"
        fn = t["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_mcp_tools_format():
    for t in mcp_tools():
        assert "name" in t and "description" in t and "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"


def test_all_three_views_have_identical_names():
    openai_names = {t["function"]["name"] for t in openai_tools()}
    mcp_names = {t["name"] for t in mcp_tools()}
    fm_names = set(func_map().keys())
    assert openai_names == mcp_names == fm_names == EXPECTED_TOOLS


def test_water_plants_spec_has_location_enum():
    spec = next(t for t in mcp_tools() if t["name"] == "water_plants")
    assert spec["inputSchema"]["properties"]["location"]["enum"] == ["indoor", "outdoor"]
    assert spec["inputSchema"]["required"] == ["location"]


def test_every_spec_has_required_keys():
    for s in SPECS:
        assert "name" in s and "description" in s and "parameters" in s and "func" in s
