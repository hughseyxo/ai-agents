"""Shared plant-photo-assessment pipeline: species context lookup, JSON/markdown
parsing of the vision model's response, and display/profile formatting.

Consolidates what used to be near-identical, slowly-diverging copies in
telegram-bot/bot.py and plant_ui/server.py. Both are now thin adapters that
call into this module.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECIES_REFERENCE_PATH = REPO_ROOT / "docs" / "plants" / "species_reference.md"

WATERING_REC_MAP = {
    "immediate": "immediate", "now": "immediate", "water now": "immediate",
    "on schedule": "on_schedule", "on_schedule": "on_schedule", "schedule": "on_schedule",
    "delay": "delay", "delay watering": "delay",
}

WATERING_LABELS = {"immediate": "💧 Water now", "on_schedule": "✅ On schedule", "delay": "⏳ Delay watering"}
STATUS_EMOJI = {"Healthy": "🟢", "Stressed": "🟡", "Concerning": "🟠", "Underwatered": "🔵", "Overwatered": "🔴"}
PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def load_species_context(plant_name: str) -> str:
    """Extract the section for this plant from species_reference.md. Returns '' on any failure."""
    try:
        text = SPECIES_REFERENCE_PATH.read_text()
        heading = f"## {plant_name}"
        start = text.find(heading)
        if start == -1:
            return ""
        # Find next ## heading after the start
        next_heading = text.find("\n## ", start + len(heading))
        section = text[start:next_heading].strip() if next_heading != -1 else text[start:].strip()
        return section
    except Exception:
        return ""


def extract_assessment_from_text(raw: str) -> dict | None:
    """Try to salvage structured fields from a non-JSON markdown assessment response."""
    # `\*{0,2}` appears both before and after the `[:\-]` separator because LLM
    # markdown output puts the closing bold marker inconsistently on either side
    # of the colon (e.g. "**Status:** X" vs "**Status**: X").
    status_m = re.search(r'\*{0,2}[Ss]tatus\*{0,2}\s*[:\-]\s*\*{0,2}\s*([A-Za-z]+)', raw)
    summary_m = re.search(r'\*{0,2}[Ss]ummary\*{0,2}\s*[:\-]\s*\*{0,2}\s*(.+?)(?=\n\*{0,2}[A-Z]|\Z)', raw, re.DOTALL)
    watering_m = re.search(r'\*{0,2}[Ww]atering[^:\n]*\*{0,2}\s*[:\-]\s*\*{0,2}\s*([A-Za-z _]+)', raw)
    obs_m = re.search(r'\*{0,2}[Oo]bservations?\*{0,2}\s*[:\-]\s*\*{0,2}\s*(.+?)(?=\n\*{0,2}[A-Z]|\Z)', raw, re.DOTALL)

    status = status_m.group(1).strip() if status_m else "Assessment"
    summary = summary_m.group(1).strip().rstrip("*").strip() if summary_m else raw[:300].strip()
    rec_raw = watering_m.group(1).strip().lower() if watering_m else ""
    rec = next((v for k, v in WATERING_REC_MAP.items() if k in rec_raw), None)
    obs_text = obs_m.group(1).strip() if obs_m else ""
    obs = [l.lstrip("•*- ").strip() for l in obs_text.splitlines() if l.strip()] if obs_text else []

    if not (status_m or summary_m):
        return None
    return {"status": status, "summary": summary, "observations": obs,
            "watering_recommendation": rec, "care_actions": [],
            "frequency_suggestion": None, "profile_notes": ""}


def sorted_care_actions(actions: list) -> list:
    return sorted(
        (a for a in (actions or []) if isinstance(a, dict) and a.get("action")),
        key=lambda a: PRIORITY_ORDER.get(str(a.get("priority", "")).lower(), 1),
    )


def format_care_action_lines(actions: list) -> list[str]:
    out = []
    for a in sorted_care_actions(actions):
        emoji = PRIORITY_EMOJI.get(str(a.get("priority", "")).lower(), "•")
        reason = a.get("reason", "")
        out.append(f"{emoji} {a['action']}" + (f" — {reason}" if reason else ""))
    return out


def format_care_actions_for_profile(actions: list) -> str:
    rows = []
    for a in sorted_care_actions(actions):
        prio = str(a.get("priority", "")).lower() or "medium"
        reason = a.get("reason", "")
        rows.append(f"- ({prio}) {a['action']}" + (f" — {reason}" if reason else ""))
    return "\n\n**Recommended next steps:**\n" + "\n".join(rows) if rows else ""


def build_assessment_display(parsed: dict, plant_name: str) -> str:
    status = parsed.get("status", "Assessment")
    summary = parsed.get("summary", "")
    obs = parsed.get("observations", [])
    rec = parsed.get("watering_recommendation", "")
    freq = parsed.get("frequency_suggestion")
    emoji = STATUS_EMOJI.get(status, "⚪")
    lines = [f"{emoji} *{plant_name}* — {status}", "", summary]
    if obs:
        lines += ["", "*Observations:*"] + [f"• {o}" for o in obs]
    if rec:
        lines += ["", WATERING_LABELS.get(rec, f"Watering: {rec}")]
    action_lines = format_care_action_lines(parsed.get("care_actions"))
    if action_lines:
        lines += ["", "*Next steps:*"] + action_lines
    if freq and isinstance(freq, dict):
        lines += [f"📅 Suggested frequency: every {freq.get('days')} days"]
    return "\n".join(lines)


def parse_assessment_response(raw: str, plant_name: str) -> tuple[str, dict | None]:
    """(display_text, parsed) — parsed None if even markdown salvage fails."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = extract_assessment_from_text(raw)
    if parsed:
        return build_assessment_display(parsed, plant_name), parsed
    return f"*{plant_name}*\n\n{raw}", None
