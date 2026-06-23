"""Controlled writers for AI-authored Obsidian notes.

Two note kinds:
  - observation notes  → docs/plant-observations/<slug>/YYYY-MM-DD-<title>.md
  - knowledge notes     → docs/garden-knowledge/<topic>.md

The plant AI never writes arbitrary files: it calls these helpers (exposed as
concierge MCP tools), so writes stay confined to the two directories below.
Reuses plant_profiles' atomic writer and bounds-checked profile resolver.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agents.plant_profiles import write_profile_atomic, safe_profile_path

REPO_ROOT = Path(__file__).resolve().parent.parent
OBSERVATIONS_DIR = REPO_ROOT / "docs" / "plant-observations"
KNOWLEDGE_DIR = REPO_ROOT / "docs" / "garden-knowledge"


def slugify(text: str) -> str:
    """Convert text to a kebab-case slug. Empty/whitespace-only text becomes 'note'."""
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower().strip())
    return text.strip("-") or "note"


def _render(frontmatter: dict, title: str, body: str) -> str:
    """Render a note with YAML frontmatter + markdown body."""
    fm = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n# {title}\n\n{body.strip()}\n"


def _confine(path: Path, base: Path) -> Path:
    """Ensure a path stays within base, raising ValueError on traversal."""
    resolved = path.resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"note path escapes {base}: {path}")
    return resolved


def create_observation_note(plant_slug: str, date: str, title: str, status: str, body: str) -> str:
    """Create a plant observation note under docs/plant-observations/<slug>/YYYY-MM-DD-<title>.md.

    Returns repo-relative path as string.
    """
    slug = slugify(plant_slug)
    note_dir = _confine(OBSERVATIONS_DIR / slug, OBSERVATIONS_DIR)
    path = _confine(note_dir / f"{date}-{slugify(title)}.md", OBSERVATIONS_DIR)
    fm = {
        "type": "observation",
        "plant": slug,
        "date": date,
        "status": status,
        "tags": ["observation", f"plant/{slug}"],
        "related": [f"[[{slug}]]"],
    }
    note_dir.mkdir(parents=True, exist_ok=True)
    write_profile_atomic(path, _render(fm, title, body))
    return str(path.relative_to(REPO_ROOT))


def create_knowledge_note(topic: str, body: str, related_plants: tuple = ()) -> str:
    """Create a knowledge note under docs/garden-knowledge/<topic>.md.

    Returns repo-relative path as string.
    """
    path = _confine(KNOWLEDGE_DIR / f"{slugify(topic)}.md", KNOWLEDGE_DIR)
    fm = {
        "type": "knowledge",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "tags": ["knowledge"],
        "related": [f"[[{slugify(p)}]]" for p in related_plants],
    }
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    write_profile_atomic(path, _render(fm, topic, body))
    return str(path.relative_to(REPO_ROOT))


def list_garden_notes() -> list:
    """List all observation and knowledge notes as dicts with path, type, and title."""
    out = []
    for base, kind in ((OBSERVATIONS_DIR, "observation"), (KNOWLEDGE_DIR, "knowledge")):
        if base.exists():
            for p in sorted(base.rglob("*.md")):
                out.append({"path": str(p.relative_to(REPO_ROOT)), "type": kind, "title": p.stem})
    return out


def read_garden_note(rel_path: str) -> str:
    """Read a note by repo-relative path. Raises ValueError on traversal or missing."""
    candidate = REPO_ROOT / rel_path
    for base in (OBSERVATIONS_DIR, KNOWLEDGE_DIR):
        try:
            resolved = _confine(candidate, base)
        except ValueError:
            continue
        if resolved.exists():
            return resolved.read_text()
    raise ValueError(f"note not found or outside note dirs: {rel_path}")


def append_linked_note(plant_slug: str, note_rel_path: str, title: str) -> bool:
    """Append a linked note reference to a plant profile. Returns False if profile doesn't exist."""
    try:
        path = safe_profile_path(plant_slug)
    except ValueError:
        return False
    if not path.exists():
        return False
    line = f"- [[{Path(note_rel_path).stem}|{title}]]"
    # Known narrow TOCTOU: reads then writes non-atomically; concurrent intelligence runs could lose the link.
    content = path.read_text()

    # Check for duplicate before inserting
    stem = Path(note_rel_path).stem
    if stem in content:
        return True  # already linked

    if "## Linked Notes" in content:
        content = content.replace("## Linked Notes\n", f"## Linked Notes\n{line}\n", 1)
    else:
        content = content.rstrip("\n") + f"\n\n## Linked Notes\n{line}\n"
    write_profile_atomic(path, content)
    return True


def maybe_create_observation_note(plant_slug: str, parsed: dict) -> str | None:
    """If a parsed photo assessment is flagged noteworthy, create an observation
    note and link it from the plant profile. Best-effort: returns None otherwise."""
    if not parsed or not parsed.get("noteworthy"):
        return None
    title = parsed.get("note_title") or "Observation"
    body = parsed.get("note_body") or parsed.get("summary") or ""
    today = datetime.now(timezone.utc).date().isoformat()
    rel = create_observation_note(plant_slug, today, title, parsed.get("status", "Observation"), body)
    append_linked_note(plant_slug, rel, title)
    return rel
