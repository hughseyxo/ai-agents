"""Generate/refresh the server-only Obsidian codebase map under docs/_map/.

The map is **gitignored** (server-only, CouchDB-synced via livesync-bridge). It replaces
the big `# Project Structure` tree that used to live in CLAUDE.md, so that map no longer
costs tokens every session and no longer reaches the public git remote.

Each area note (docs/_map/<area>.md) holds a script-managed file-list block:

    <!-- MAP:FILES:START -->
    - `agents/base.py` — BaseAgent class — lifecycle, retry, state, LLM failover.
    <!-- MAP:FILES:END -->

This generator rebuilds ONLY that block: it preserves existing per-file descriptions,
inserts a TODO placeholder for new/renamed files, drops vanished files, and leaves any
prose OUTSIDE the markers untouched. It is idempotent — a second run with no tree change
produces no diff.

Run:
    python3 scripts/gen_codebase_map.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAP_DIR = REPO_ROOT / "docs" / "_map"

START = "<!-- MAP:FILES:START -->"
END = "<!-- MAP:FILES:END -->"
TODO = "_TODO: describe_"

_ROW_RE = re.compile(r"^- `(?P<path>[^`]+)` — (?P<desc>.*)$")

# area -> repo-relative globs. Mirrors the old CLAUDE.md Project Structure section.
AREAS: dict[str, list[str]] = {
    "agents": ["agents/*.py", "agents/prompts/*.md"],
    "telegram-bot": ["telegram-bot/*.py"],
    "plant-ui": ["plant_ui/**/*"],
    "mcp-servers": ["mcp-servers/*.py"],
    "tests": ["tests/*.py"],
    "skills": ["skills/**/*"],
    "scripts-and-root": ["scripts/*", "triggers/*", "*.sh", "*.py", "*.service"],
}

_SCAFFOLD = (
    "*Server-only codebase map — gitignored, CouchDB-synced. "
    "Regenerate file lists with `python3 scripts/gen_codebase_map.py`. "
    "Add/edit the prose descriptions by hand; the generator preserves them.*"
)


def _filter_git_ignored(paths: list[str], root: Path) -> list[str]:
    """Drop paths that git ignores (vendored deps, build output, etc.).

    Falls back to returning all paths when `root` is not a git repo or git is
    unavailable, so filesystem-only callers (e.g. unit tests) are unaffected.
    """
    if not paths:
        return paths
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(paths),
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return paths
    if proc.returncode not in (0, 1):  # 0=some ignored, 1=none; 128=not a repo
        return paths
    ignored = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
    return [p for p in paths if p not in ignored]


def discover_files(globs: list[str], root: Path) -> list[str]:
    """Return sorted repo-relative POSIX paths of files matching any glob.

    Directories, dotfiles/dot-dirs, __pycache__, and git-ignored paths are excluded.
    """
    found: set[str] = set()
    for pattern in globs:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                continue
            found.add(rel.as_posix())
    return _filter_git_ignored(sorted(found), root)


def parse_block(text: str) -> dict[str, str]:
    """Extract {path: description} from the managed block, or {} if absent."""
    if START not in text or END not in text:
        return {}
    inner = text[text.index(START) + len(START): text.index(END)]
    out: dict[str, str] = {}
    for line in inner.splitlines():
        m = _ROW_RE.match(line.strip())
        if m:
            out[m.group("path")] = m.group("desc").strip()
    return out


def render_rows(paths: list[str], existing: dict[str, str]) -> list[str]:
    """Build row lines, keeping existing descriptions and TODO-ing new files."""
    rows = []
    for path in paths:
        desc = existing.get(path, "").strip() or TODO
        rows.append(f"- `{path}` — {desc}")
    return rows


def splice(text: str, inner_lines: list[str], title: str | None = None) -> str:
    """Replace the managed block with inner_lines, preserving everything outside it.

    If markers are absent the block is appended after any existing prose (or a fresh
    scaffold when the note is empty).
    """
    block = "\n".join([START, *inner_lines, END])
    if START in text and END in text:
        before = text[: text.index(START)]
        after = text[text.index(END) + len(END):]
        return before + block + after
    body = text.rstrip("\n")
    if body:
        return f"{body}\n\n{block}\n"
    header = f"# {title}\n\n" if title else ""
    return f"{header}{_SCAFFOLD}\n\n{block}\n"


def update_area_note(note_path: Path, area: str, paths: list[str]) -> tuple[int, int]:
    """Rewrite an area note's managed block. Returns (added, removed) counts."""
    text = note_path.read_text() if note_path.exists() else ""
    existing = parse_block(text)
    added = sum(1 for p in paths if p not in existing)
    removed = sum(1 for p in existing if p not in paths)
    new_text = splice(text, render_rows(paths, existing), title=area)
    if new_text != text:
        note_path.write_text(new_text)
    return added, removed


def write_index(map_dir: Path, area_names: list[str]) -> None:
    """Write/refresh the MOC index linking each area note, preserving prose."""
    path = map_dir / "index.md"
    text = path.read_text() if path.exists() else ""
    links = [f"- [[{a}]]" for a in area_names]
    new_text = splice(text, links, title="Codebase map (server-only)")
    if new_text != text:
        path.write_text(new_text)


def generate(root: Path, map_dir: Path, areas: dict[str, list[str]] = AREAS) -> dict[str, tuple[int, int]]:
    """Regenerate the whole map under map_dir. Returns per-area (added, removed)."""
    map_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[int, int]] = {}
    for area, globs in areas.items():
        paths = discover_files(globs, root)
        results[area] = update_area_note(map_dir / f"{area}.md", area, paths)
    write_index(map_dir, list(areas))
    return results


def main() -> None:
    results = generate(REPO_ROOT, MAP_DIR)
    for area, (added, removed) in results.items():
        print(f"{area}: +{added} / -{removed}")
    print(f"Map written to {MAP_DIR} (gitignored, server-only).")


if __name__ == "__main__":
    main()
