"""Builds a consolidated Markdown digest of all plants for external consumption
(pushed to Google Drive so a Claude.ai Project can read current garden state)."""

from datetime import datetime, timezone

from .plant_profiles import read_profile_context

DIGEST_TITLE = "# Plant Health Digest"


def build_digest(plants: list[dict], weather_cache: list[dict]) -> str:
    """Assemble one Markdown doc: a summary table plus a per-plant section
    combining core fields, weather-adjusted status, and curated profile context."""
    cache_by_name = {r["plant_name"]: r for r in weather_cache}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [DIGEST_TITLE, "", f"_Generated {generated_at}_", ""]

    lines += [
        "| Plant | Location | Next Water | Frequency | Sensitivity |",
        "|---|---|---|---|---|",
    ]
    for plant in plants:
        cache = cache_by_name.get(plant["name"], {})
        next_water = cache.get("adjusted_date") or "—"
        lines.append(
            f"| {plant['name']} | {plant.get('location', '')} | {next_water} | "
            f"{plant.get('frequency_days', '')}d | {plant.get('water_sensitivity', '')} |"
        )
    lines.append("")

    for plant in plants:
        name = plant["name"]
        cache = cache_by_name.get(name, {})
        baseline = plant.get("baseline_frequency_days", plant.get("frequency_days", "?"))
        lines.append(f"## {name}")
        lines.append(
            f"- Location: {plant.get('location', 'unknown')} | "
            f"Sunlight: {plant.get('sunlight', 'unknown')} | "
            f"Water sensitivity: {plant.get('water_sensitivity', 'medium')}"
        )
        lines.append(
            f"- Last watered: {plant.get('last_watered', 'unknown')} | "
            f"Frequency: {plant.get('frequency_days', '?')}d (baseline {baseline}d)"
        )
        if cache.get("adjusted_date"):
            reason = cache.get("adjustment_reason") or "—"
            lines.append(f"- Next water (weather-adjusted): {cache['adjusted_date']} ({reason})")
        context = read_profile_context(name, max_assessments=2)
        if context:
            lines.append("")
            lines.append(context)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
