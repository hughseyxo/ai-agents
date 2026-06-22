"""Tests for scripts/daily_note.py — ensure_today + append_session + stop flow."""
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def dn(tmp_path, monkeypatch):
    if "scripts.daily_note" in sys.modules:
        del sys.modules["scripts.daily_note"]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.daily_note as _dn
    monkeypatch.setattr(_dn, "DAILY_DIR", tmp_path)
    return _dn


def test_ensure_today_creates_file(dn, tmp_path):
    p = dn.ensure_today("2026-06-19")
    assert p.exists()
    text = p.read_text()
    assert text.startswith("---\n")
    assert "type: daily" in text
    assert "## Index" in text
    assert "2026-06-19" in text


def test_ensure_today_idempotent(dn, tmp_path):
    p1 = dn.ensure_today("2026-06-19")
    p2 = dn.ensure_today("2026-06-19")
    assert p1 == p2
    assert p1.read_text().count("## Index") == 1


def test_ensure_today_default_date(dn):
    from datetime import date
    today = date.today().isoformat()
    p = dn.ensure_today()
    assert p.name == f"{today}.md"


def test_append_session_adds_block(dn, tmp_path):
    p = dn.ensure_today("2026-06-20")
    dn.append_session(
        date_str="2026-06-20",
        summary="Implemented daily notes.",
        topics=["obsidian", "daily-notes"],
        files=["scripts/daily_note.py"],
        decisions=["hook-driven"],
        open_threads=["phase-4"],
    )
    text = p.read_text()
    assert "## Session 1" in text
    assert "Implemented daily notes." in text
    assert "obsidian" in text


def test_append_session_increments(dn, tmp_path):
    dn.ensure_today("2026-06-20")
    dn.append_session("2026-06-20", "First.", [], [], [], [])
    dn.append_session("2026-06-20", "Second.", [], [], [], [])
    text = (tmp_path / "2026-06-20.md").read_text()
    assert "## Session 1" in text
    assert "## Session 2" in text


def test_append_session_updates_index(dn, tmp_path):
    dn.ensure_today("2026-06-20")
    dn.append_session("2026-06-20", "Topic summary.", ["vault"], [], [], [])
    text = (tmp_path / "2026-06-20.md").read_text()
    index_pos = text.index("## Index")
    session_pos = text.index("## Session 1")
    index_section = text[index_pos:session_pos]
    assert "Session 1" in index_section


# --- transcript parsing + stop flow ---

def _write_transcript(tmp_path, lines):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(o) for o in lines) + "\n")
    return p


def test_parse_transcript_extracts_prompts_and_files(dn, tmp_path):
    t = _write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "Fix the daily note hook"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/scripts/daily_note.py"}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "message": {"role": "user",
            "content": "<system-reminder>ignore me</system-reminder>"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/x/a.md"}},
        ]}},
    ])
    prompts, files = dn.parse_transcript(t)
    assert prompts == ["Fix the daily note hook"]
    assert files == ["/x/scripts/daily_note.py", "/x/a.md"]


def test_parse_transcript_missing_file(dn, tmp_path):
    prompts, files = dn.parse_transcript(tmp_path / "nope.jsonl")
    assert prompts == []
    assert files == []


def test_run_stop_writes_rich_session(dn, tmp_path, monkeypatch):
    t = _write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "Wire rich daily notes"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/scripts/daily_note.py"}},
        ]}},
    ])
    monkeypatch.setattr(dn, "_llm_summary", lambda ctx: {
        "summary": "Wired LLM-backed session summaries into the Stop hook.",
        "topics": ["daily-notes", "hooks"],
        "decisions": ["use haiku"],
        "open_threads": ["verify on next stop"],
    })
    dn.run_stop({"transcript_path": str(t)}, date_str="2026-06-21")
    text = (tmp_path / "2026-06-21.md").read_text()
    assert "Wired LLM-backed session summaries" in text
    assert "daily-notes" in text
    assert "use haiku" in text
    assert "/x/scripts/daily_note.py" in text


def test_run_stop_falls_back_when_llm_fails(dn, tmp_path, monkeypatch):
    t = _write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "First real prompt here"}},
    ])
    monkeypatch.setattr(dn, "_llm_summary", lambda ctx: None)
    dn.run_stop({"transcript_path": str(t)}, date_str="2026-06-21")
    text = (tmp_path / "2026-06-21.md").read_text()
    assert "## Session 1" in text
    # falls back to the first user prompt, never an empty/placeholder block
    assert "First real prompt here" in text


def test_run_stop_guard_prevents_recursion(dn, tmp_path, monkeypatch):
    # The nested `claude -p` we spawn runs with the guard set; its own Stop hook
    # must no-op so we don't fork-bomb.
    monkeypatch.setenv("DAILY_NOTE_STOP_GUARD", "1")
    t = _write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "x"}}])
    dn.run_stop({"transcript_path": str(t)}, date_str="2026-06-21")
    assert not (tmp_path / "2026-06-21.md").exists()


def test_llm_summary_sets_guard_env(dn, monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        stdout = '{"summary":"ok","topics":[],"decisions":[],"open_threads":[]}'
        stderr = ""

    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        return _Proc()

    monkeypatch.setattr(dn.subprocess, "run", fake_run)
    out = dn._llm_summary("ctx")
    assert out["summary"] == "ok"
    assert captured["env"]["DAILY_NOTE_STOP_GUARD"] == "1"
