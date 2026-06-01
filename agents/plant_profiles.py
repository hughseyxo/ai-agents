"""File-I/O helpers for plant profile docs (docs/plants/<slug>.md)."""

from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANTS_DIR = REPO_ROOT / "docs" / "plants"

_TABLE_HEADER = "| Date | Change | Reason |\n|---|---|---|\n"


def profile_path(plant_name: str) -> Path:
    slug = plant_name.lower().replace(" ", "-").replace("/", "-")
    return PLANTS_DIR / f"{slug}.md"


def append_frequency_history(plant_name: str, old: int, new: int, reason: str) -> bool:
    """Insert a row into the plant profile's Frequency History table.
    Returns False if the profile doc does not exist."""
    path = profile_path(plant_name)
    if not path.exists():
        return False
    content = path.read_text()
    today = datetime.now(timezone.utc).date().isoformat()
    row = f"| {today} | {old}→{new} days | {reason} |\n"
    if _TABLE_HEADER in content:
        content = content.replace(_TABLE_HEADER, _TABLE_HEADER + row, 1)
    elif "## Frequency History" in content:
        content = content.replace("## Frequency History\n",
                                  "## Frequency History\n" + _TABLE_HEADER + row, 1)
    else:
        content += f"\n## Frequency History\n{_TABLE_HEADER}{row}"
    path.write_text(content)
    return True
