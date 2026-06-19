"""One-time: inject frontmatter projection (from SQLite) into existing plant profiles."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.db import AgentDB
from agents import plant_profiles as pp


def main():
    db = AgentDB()
    plants = db.get_state("daily-briefing", "plants") or []
    if not plants:
        print("No plants found in DB — run the plant agent first.")
        return

    updated, skipped = 0, 0
    for plant in plants:
        fields = {
            "type": "plant",
            "location": plant["location"],
            "sunlight": plant.get("sunlight", "unknown"),
            "water_sensitivity": plant.get("water_sensitivity", "medium"),
            "baseline_frequency_days": plant["baseline_frequency_days"],
            "effective_frequency_days": plant["frequency_days"],
            "last_watered": plant.get("last_watered"),
            "needs_photo": plant.get("needs_photo", False),
            "latest_health": plant.get("last_assessment"),
            "tags": [
                "plant",
                plant["location"],
                f"sensitivity/{plant.get('water_sensitivity', 'medium')}",
            ],
        }
        if pp.upsert_frontmatter(plant["name"], fields):
            print(f"  frontmatter: {plant['name']}")
            updated += 1
        else:
            print(f"  skipped (no profile): {plant['name']}")
            skipped += 1

    print(f"\nDone: {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    main()
