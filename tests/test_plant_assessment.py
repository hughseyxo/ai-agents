"""Tests for agents.plant_assessment — shared assessment parse/format pipeline
used by both the Telegram bot and the FloraPulse PWA."""

from agents.plant_assessment import (
    parse_assessment_response,
    build_assessment_display,
    format_care_actions_for_profile,
)


def test_parse_json_in_code_fence():
    raw = '```json\n{"status": "Healthy", "summary": "fine", "observations": [], "care_actions": []}\n```'
    display, parsed = parse_assessment_response(raw, plant_name="Aloe")
    assert parsed["status"] == "Healthy" and "Aloe" in display


def test_salvages_markdown_prose():
    raw = "**Status:** Stressed\n**Summary:** droopy leaves"
    display, parsed = parse_assessment_response(raw, plant_name="Aloe")
    assert parsed["status"] == "Stressed" and "droopy" in display


def test_care_actions_sorted_by_priority():
    block = format_care_actions_for_profile([
        {"action": "b", "priority": "low"}, {"action": "a", "priority": "high"},
    ])
    assert block.index("a") < block.index("b")
