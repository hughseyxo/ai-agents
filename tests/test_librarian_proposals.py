"""Tests for the librarian proposal apply/reject CLI (replaces the deleted MCP bridge)."""

import json

import pytest

import agents.librarian as lib


@pytest.fixture
def proposals_dir(tmp_path, monkeypatch):
    d = tmp_path / "proposals"
    d.mkdir()
    monkeypatch.setattr(lib, "_proposals_dir", lambda: d)
    # Neutralise the git commit in apply_proposal so tests don't touch the repo.
    monkeypatch.setattr(lib, "_git_commit", lambda *a, **k: None)
    return d


def _write_proposal(d, pid, **over):
    p = {
        "id": pid,
        "agent": "daily-briefing",
        "finding": "x",
        "fix_type": "prompt_edit",
        "file": "agents/prompts/daily_briefing.md",
        "original": "old",
        "proposed": "new prompt body",
        "status": "pending",
    }
    p.update(over)
    (d / f"{pid}.json").write_text(json.dumps(p))
    return p


def test_apply_writes_file_and_marks_approved(proposals_dir, tmp_path, monkeypatch):
    target = tmp_path / "agents" / "prompts" / "daily_briefing.md"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    _write_proposal(proposals_dir, "abc12345")

    rc = lib.apply_proposal("abc12345")

    assert rc == 0
    assert target.read_text() == "new prompt body"
    saved = json.loads((proposals_dir / "abc12345.json").read_text())
    assert saved["status"] == "approved"


def test_reject_marks_rejected_no_write(proposals_dir, tmp_path, monkeypatch):
    target = tmp_path / "agents" / "prompts" / "daily_briefing.md"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    _write_proposal(proposals_dir, "abc12345")

    rc = lib.reject_proposal("abc12345")

    assert rc == 0
    assert target.read_text() == "old"  # untouched
    saved = json.loads((proposals_dir / "abc12345.json").read_text())
    assert saved["status"] == "rejected"


def test_apply_unknown_id_nonzero(proposals_dir):
    assert lib.apply_proposal("deadbeef") != 0


def test_apply_already_applied_nonzero(proposals_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    _write_proposal(proposals_dir, "abc12345", status="approved")
    assert lib.apply_proposal("abc12345") != 0


def test_apply_rejects_malformed_id(proposals_dir):
    # Path-traversal / bad id must be refused before any file access.
    assert lib.apply_proposal("../../etc/passwd") != 0
    assert lib.reject_proposal("..") != 0


def test_apply_rejects_file_outside_repo(proposals_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    _write_proposal(proposals_dir, "abc12345", file="../../../etc/cron.d/evil")
    assert lib.apply_proposal("abc12345") != 0
