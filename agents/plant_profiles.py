"""File-I/O helpers for plant profile docs (docs/plants/<slug>.md)."""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANTS_DIR = REPO_ROOT / "docs" / "plants"

_TABLE_HEADER = "| Date | Change | Reason |\n|---|---|---|\n"
_HEALTH_SECTION = "## Health Assessments"
_HEALTH_COMMENT = "<!-- Photo assessments appended here -->"


def _write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically (temp file + os.replace) so concurrent writers can't corrupt it."""
    directory = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_profile_atomic(path: Path, content: str) -> None:
    """Public wrapper — write a plant profile doc atomically."""
    _write_atomic(path, content)


def profile_path(plant_name: str) -> Path:
    slug = plant_name.lower().replace(" ", "-").replace("/", "-")
    return PLANTS_DIR / f"{slug}.md"


def write_health_assessment(plant_name: str, profile_notes: str) -> bool:
    """Append profile_notes to the ## Health Assessments section of a plant profile.
    Returns False if the profile doc does not exist."""
    path = profile_path(plant_name)
    if not path.exists():
        return False
    content = path.read_text()
    entry = f"\n{profile_notes.strip()}\n"
    if _HEALTH_COMMENT in content:
        content = content.replace(_HEALTH_COMMENT, f"{_HEALTH_COMMENT}{entry}", 1)
    elif f"{_HEALTH_SECTION}\n" in content:
        content = content.replace(f"{_HEALTH_SECTION}\n", f"{_HEALTH_SECTION}\n{entry}", 1)
    else:
        content += f"\n{_HEALTH_SECTION}\n{entry}"
    _write_atomic(path, content)
    return True


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
    _write_atomic(path, content)
    return True
