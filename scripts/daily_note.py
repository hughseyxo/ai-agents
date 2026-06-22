"""Daily session note writer for the Obsidian vault.

Creates docs/daily/YYYY-MM-DD.md on SessionStart (idempotent).
Appends a ## Session N block on Stop.

CLI:
    python -m scripts.daily_note ensure [date]
    python -m scripts.daily_note append [date] [summary]
"""
import json
import os
import re
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

import yaml

# Tool names whose `input.file_path` counts as a file touched this session.
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_SUMMARY_PROMPT = """\
You are writing one entry in a developer's daily engineering log, summarising a \
single Claude Code coding session from its transcript context below.

Output ONLY minified JSON (no markdown, no code fences) with these keys:
- "summary": one sentence, <=140 chars, past tense, what was actually accomplished.
- "topics": array of 1-4 short kebab-case tags.
- "decisions": array of short strings for notable choices made (may be empty).
- "open_threads": array of short strings for unfinished follow-ups (may be empty).

If the session did nothing substantive, return summary "No substantive work." and \
empty arrays."""

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


def _block_text(content) -> str:
    """Flatten a message `content` (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def parse_transcript(path) -> tuple[list[str], list[str]]:
    """Pull genuine user prompts and edited file paths from a Claude Code JSONL.

    Returns (user_prompts, files_touched). Filters out tool-result turns and
    harness-injected wrapper messages (those whose text starts with '<').
    """
    path = Path(path)
    prompts: list[str] = []
    files: list[str] = []
    if not path.exists():
        return prompts, files
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        role = msg.get("role") or obj.get("type")
        content = msg.get("content")
        if role == "user":
            text = _block_text(content).strip()
            if text and not text.startswith("<"):
                prompts.append(text)
        elif role == "assistant" and isinstance(content, list):
            for b in content:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") in _EDIT_TOOLS):
                    fp = (b.get("input") or {}).get("file_path")
                    if fp and fp not in files:
                        files.append(fp)
    return prompts, files


def _llm_summary(context: str) -> dict | None:
    """Ask the claude CLI (haiku) for a structured session summary.

    Returns a dict with summary/topics/decisions/open_threads, or None on any
    failure (so the Stop hook degrades to a deterministic fallback).
    """
    # Guard the nested CLI's own Stop hook so it no-ops (no fork-bomb).
    env = {**os.environ, "DAILY_NOTE_STOP_GUARD": "1"}
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5", "--strict-mcp-config"],
            input=f"{_SUMMARY_PROMPT}\n\n---\n{context}\n",
            capture_output=True, text=True, timeout=120, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    raw = proc.stdout.strip()
    m = re.search(r"\{.*\}", raw, re.S)  # tolerate stray prose around the JSON
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("summary"):
        return None
    return data


def run_stop(payload: dict, date_str: str | None = None) -> None:
    """Stop-hook entrypoint: summarise the session and append it to today's note."""
    if os.environ.get("DAILY_NOTE_STOP_GUARD") == "1":
        return  # we are the nested summariser CLI — do nothing, avoid recursion
    if date_str is None:
        date_str = _date.today().isoformat()
    transcript = payload.get("transcript_path", "")
    prompts, files = parse_transcript(transcript)

    context = (
        "User requests this session:\n"
        + ("\n".join(f"- {p[:300]}" for p in prompts[:20]) or "- (none captured)")
        + "\n\nFiles edited: "
        + (", ".join(files[:40]) or "(none)")
    )
    data = _llm_summary(context) if prompts or files else None
    if data:
        summary = str(data.get("summary", "")).strip() or prompts[0][:140]
        topics = [str(t) for t in (data.get("topics") or [])]
        decisions = [str(d) for d in (data.get("decisions") or [])]
        open_threads = [str(o) for o in (data.get("open_threads") or [])]
    else:
        # Deterministic fallback — never write an empty placeholder block.
        summary = (prompts[0][:140] if prompts else "Session ended.")
        topics, decisions, open_threads = [], [], []

    append_session(date_str, summary, topics, files, decisions, open_threads)


def _main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "ensure"
    if cmd == "ensure":
        d = argv[1] if len(argv) > 1 else None
        p = ensure_today(d)
        print(f"daily note: {p}")
    elif cmd == "stop":
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            payload = {}
        run_stop(payload)
        print("appended session summary")
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
