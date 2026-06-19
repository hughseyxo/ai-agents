"""File-I/O helpers for plant profile docs (docs/plants/<slug>.md)."""

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANTS_DIR = REPO_ROOT / "docs" / "plants"
# Alias used by the frontmatter/section/context helpers below. Kept separate so
# those helpers resolve paths via a monkeypatch-friendly name without disturbing
# the legacy helpers that bind PLANTS_DIR.
PROFILES_DIR = PLANTS_DIR


def _slug(plant_name: str) -> str:
    return plant_name.lower().replace(" ", "-").replace("/", "-")


def _profile_path_pd(plant_name: str) -> Path:
    """Resolve a profile path under PROFILES_DIR (frontmatter/section helpers)."""
    return PROFILES_DIR / f"{_slug(plant_name)}.md"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading `---` YAML block from the body. Returns ({}, text) if absent."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end]) or {}
            return meta, text[end + 5:]
    return {}, text


def upsert_frontmatter(plant_name: str, fields: dict) -> bool:
    """Write/replace the leading `---` YAML block, preserving the body.
    Returns False if the profile doc does not exist."""
    path = _profile_path_pd(plant_name)
    if not path.exists():
        return False
    _, body = parse_frontmatter(path.read_text())
    fm = yaml.safe_dump(fields, sort_keys=False, default_flow_style=False).strip()
    write_profile_atomic(path, f"---\n{fm}\n---\n{body}")
    return True


def rewrite_section(plant_name: str, section: str, new_body: str) -> bool:
    """Replace the content under `## <section>` (to next `## ` or EOF) with new_body;
    create the section at end if absent. Preserves frontmatter and other sections.
    Returns False if the profile doc does not exist."""
    path = _profile_path_pd(plant_name)
    if not path.exists():
        return False
    text = path.read_text()
    if not new_body.endswith("\n"):
        new_body += "\n"
    pattern = re.compile(rf"(^## {re.escape(section)}\n)(.*?)(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{new_body}", text)
    else:
        text = text.rstrip("\n") + f"\n\n## {section}\n{new_body}"
    write_profile_atomic(path, text)
    return True


def read_profile_context(plant_name: str, max_assessments: int = 2) -> str:
    """Return a token-lean slice: frontmatter (compact) + Current Observations +
    the most recent `max_assessments` Health Assessments. Returns '' if absent."""
    path = _profile_path_pd(plant_name)
    if not path.exists():
        return ""
    meta, body = parse_frontmatter(path.read_text())
    parts = []
    if meta:
        parts.append(", ".join(f"{k}={v}" for k, v in meta.items()))
    obs = re.search(r"^## Current Observations\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if obs:
        parts.append("## Current Observations\n" + obs.group(1).strip())
    ha = re.search(r"^## Health Assessments\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if ha:
        entries = re.split(r"(?=^### )", ha.group(1), flags=re.M)
        entries = [e for e in entries if e.strip()][:max_assessments]
        if entries:
            parts.append("## Recent Assessments\n" + "".join(entries).strip())
    return "\n\n".join(parts)

_TABLE_HEADER = "| Date | Change | Reason |\n|---|---|---|\n"
_HEALTH_SECTION = "## Health Assessments"
_HEALTH_COMMENT = "<!-- Photo assessments appended here -->"
_INTELLIGENCE_SECTION = "## Intelligence Notes"
_INTELLIGENCE_COMMENT = "<!-- Appended by each intelligence run -->"


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


def append_intelligence_note(plant_name: str, note: str) -> bool:
    """Prepend a timestamped note to the ## Intelligence Notes section of a plant profile.
    Returns False if the profile doc does not exist or the resolved path escapes PLANTS_DIR."""
    path = profile_path(plant_name)
    try:
        path.resolve().relative_to(PLANTS_DIR.resolve())
    except ValueError:
        return False
    if not path.exists():
        return False
    content = path.read_text()
    entry = f"\n{note.strip()}\n"
    if _INTELLIGENCE_COMMENT in content:
        content = content.replace(_INTELLIGENCE_COMMENT, f"{_INTELLIGENCE_COMMENT}{entry}", 1)
    elif f"{_INTELLIGENCE_SECTION}\n" in content:
        content = content.replace(f"{_INTELLIGENCE_SECTION}\n", f"{_INTELLIGENCE_SECTION}\n{entry}", 1)
    else:
        content += f"\n{_INTELLIGENCE_SECTION}\n{_INTELLIGENCE_COMMENT}{entry}"
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
