"""Daily session note writer for the Obsidian vault.

Creates docs/daily/YYYY-MM-DD.md on SessionStart (idempotent).
Appends a ## Session N block on Stop.

CLI:
    python -m scripts.daily_note ensure [date]
    python -m scripts.daily_note append [date] [summary]
"""
import re
import sys
from datetime import date as _date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = REPO_ROOT / "docs" / "daily"

_TEMPLATE = """\
---
{frontmatter}
---
# {date}

## Index

"""


def _build_frontmatter(date_str: str) -> str:
    fm = {
        "type": "daily",
        "date": date_str,
        "sessions": 0,
        "topics": [],
        "files_touched": [],
        "decisions": [],
        "open_threads": [],
        "tags": ["daily"],
    }
    return yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip()


def ensure_today(date_str: str | None = None) -> Path:
    """Create today's daily note if it doesn't exist. Idempotent."""
    if date_str is None:
        date_str = _date.today().isoformat()
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{date_str}.md"
    if not path.exists():
        path.write_text(_TEMPLATE.format(
            frontmatter=_build_frontmatter(date_str),
            date=date_str,
        ))
    return path


def append_session(
    date_str: str,
    summary: str,
    topics: list,
    files: list,
    decisions: list,
    open_threads: list,
) -> None:
    """Append a ## Session N block and update the Index + frontmatter arrays."""
    path = ensure_today(date_str)
    text = path.read_text()

    existing = re.findall(r"^## Session \d+", text, re.M)
    n = len(existing) + 1

    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        fm["sessions"] = n
        fm.setdefault("topics", [])
        fm.setdefault("files_touched", [])
        fm.setdefault("decisions", [])
        fm.setdefault("open_threads", [])
        for t in topics:
            if t not in fm["topics"]:
                fm["topics"].append(t)
        for f in files:
            if f not in fm["files_touched"]:
                fm["files_touched"].append(f)
        for d in decisions:
            if d not in fm["decisions"]:
                fm["decisions"].append(d)
        for o in open_threads:
            if o not in fm["open_threads"]:
                fm["open_threads"].append(o)
        new_fm = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip()
        text = f"---\n{new_fm}\n---\n" + text[fm_match.end():]

    index_match = re.search(r"^## Index\n", text, re.M)
    if index_match:
        insert_at = index_match.end()
        index_line = f"- [[#Session {n}]] — {summary}\n"
        text = text[:insert_at] + index_line + text[insert_at:]

    session_block = f"\n## Session {n}\n\n{summary}\n"
    if topics:
        session_block += f"\nTopics: {', '.join(topics)}\n"
    text = text.rstrip("\n") + "\n" + session_block

    path.write_text(text)


def _main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "ensure"
    if cmd == "ensure":
        d = argv[1] if len(argv) > 1 else None
        p = ensure_today(d)
        print(f"daily note: {p}")
    elif cmd == "append":
        d = argv[1] if len(argv) > 1 else _date.today().isoformat()
        summary = argv[2] if len(argv) > 2 else "Session ended."
        append_session(d, summary, [], [], [], [])
        print(f"appended session to {d}")
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main(sys.argv[1:])
