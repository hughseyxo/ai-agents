"""One-time: migrate flat docs/agent-learnings/<agent>.md files and
docs/librarian-memory.md into atomic status-tagged notes.

Reads each bullet line as a separate learning, derives a slug from the first
words, writes an atomic note via _write_learning_note, then removes the flat file.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agents.librarian import _write_learning_note

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LEARNINGS_DIR = REPO_ROOT / "docs" / "agent-learnings"
MEMORY_FILE = REPO_ROOT / "docs" / "librarian-memory.md"


def _slugify(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text[:max_len].lower()).strip("-")


def _migrate_flat_file(agent: str, path: pathlib.Path, note_type: str = "learnings") -> int:
    lines = [l.lstrip("- ").strip() for l in path.read_text().splitlines() if l.strip()]
    count = 0
    seen_slugs: set = set()
    for line in lines:
        if not line:
            continue
        base_slug = _slugify(line)
        slug = base_slug
        n = 1
        while slug in seen_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        seen_slugs.add(slug)
        _write_learning_note(agent, line, 0.8, slug, [], note_type=note_type)
        print(f"  migrated: {slug}")
        count += 1
    return count


def main() -> None:
    total = 0

    # Flat per-agent files
    for md in sorted(LEARNINGS_DIR.glob("*.md")):
        agent = md.stem
        print(f"Migrating {agent} ({md.name})…")
        n = _migrate_flat_file(agent, md, note_type="learnings")
        md.unlink()
        print(f"  -> {n} notes written, flat file removed")
        total += n

    # librarian-memory.md
    if MEMORY_FILE.exists():
        print("Migrating librarian-memory.md…")
        n = _migrate_flat_file("global", MEMORY_FILE, note_type="memory")
        MEMORY_FILE.unlink()
        print(f"  -> {n} notes written, flat file removed")
        total += n

    print(f"\nDone. {total} atomic notes written.")


if __name__ == "__main__":
    main()
