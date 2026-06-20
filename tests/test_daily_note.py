"""Tests for scripts/daily_note.py — ensure_today + append_session."""
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
