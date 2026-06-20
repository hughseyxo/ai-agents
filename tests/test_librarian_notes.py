"""Tests for librarian atomic-note writing and status-filtered collection."""
import pytest
from pathlib import Path


def test_write_learning_note(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    p = lib._write_learning_note(
        "news-briefing", "Don't report truncation.",
        0.9, "truncation-not-a-defect", ["[[security-audit]]"]
    )
    text = p.read_text()
    assert "status: active" in text
    assert "agent: news-briefing" in text
    assert "confidence: 0.9" in text
    assert "Don't report truncation." in text
    assert p.parent.name == "news-briefing"


def test_write_learning_note_memory_type(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    p = lib._write_learning_note(
        "global", "Truncation is expected for long feeds.",
        0.85, "truncation-expected", [], note_type="memory"
    )
    text = p.read_text()
    assert "type: memory" in text
    assert p.parent.name == "librarian-memory"


def test_write_learning_note_idempotent(tmp_path, monkeypatch):
    """Writing the same slug twice should overwrite, not duplicate."""
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    lib._write_learning_note("news-briefing", "First.", 0.9, "same-slug", [])
    p = lib._write_learning_note("news-briefing", "Updated.", 0.9, "same-slug", [])
    assert p.read_text().count("## Note") == 1
    assert "Updated." in p.read_text()


# --- _collect_learnings ---

def test_collect_loads_only_active(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    d = tmp_path / "docs" / "agent-learnings" / "news-briefing"
    d.mkdir(parents=True)
    (d / "a.md").write_text("---\nstatus: active\n---\nKEEP\n")
    (d / "b.md").write_text("---\nstatus: superseded\n---\nDROP\n")
    blob = "\n".join(lib._collect_learnings(tmp_path / "docs" / "agent-learnings").values())
    assert "KEEP" in blob and "DROP" not in blob


def test_collect_missing_status_treated_as_active(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    d = tmp_path / "docs" / "agent-learnings" / "plant-agent"
    d.mkdir(parents=True)
    (d / "c.md").write_text("---\nagent: plant-agent\n---\nBACKCOMPAT\n")
    blob = "\n".join(lib._collect_learnings(tmp_path / "docs" / "agent-learnings").values())
    assert "BACKCOMPAT" in blob


def test_collect_empty_dir_returns_empty(tmp_path):
    import agents.librarian as lib
    d = tmp_path / "no-learnings"
    d.mkdir()
    assert lib._collect_learnings(d) == {}
