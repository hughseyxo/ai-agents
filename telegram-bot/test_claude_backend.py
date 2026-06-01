"""Tests for the claude CLI conversational backend.

subprocess is mocked throughout — no real CLI calls. Verifies command
construction, JSON parsing, per-chat session caching (multi-turn via --resume),
and graceful failure (returns None) so the caller can fall back.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import claude_backend
from claude_backend import ask_claude, CLAUDE_MODEL


@pytest.fixture(autouse=True)
def clear_sessions():
    claude_backend._SESSIONS.clear()
    yield
    claude_backend._SESSIONS.clear()


def _ok(result="hi", session_id="sess-123"):
    return MagicMock(returncode=0, stdout=json.dumps({"result": result, "session_id": session_id}), stderr="")


def test_returns_result_text():
    with patch("claude_backend.subprocess.run", return_value=_ok("Your plants are fine.")) as run:
        reply = ask_claude(42, "how are my plants?")
    assert reply == "Your plants are fine."
    assert run.call_args.kwargs["input"] == "how are my plants?"


def test_command_includes_model_and_strict_config():
    with patch("claude_backend.subprocess.run", return_value=_ok()) as run:
        ask_claude(42, "hi")
    cmd = run.call_args.args[0]
    assert "claude" == cmd[0]
    assert CLAUDE_MODEL in cmd
    assert "--strict-mcp-config" in cmd
    assert "--output-format" in cmd and "json" in cmd


def test_first_call_has_no_resume():
    with patch("claude_backend.subprocess.run", return_value=_ok()) as run:
        ask_claude(42, "hi")
    assert "--resume" not in run.call_args.args[0]


def test_second_call_same_chat_resumes_session():
    with patch("claude_backend.subprocess.run", return_value=_ok(session_id="abc")) as run:
        ask_claude(42, "first")
        ask_claude(42, "second")
    cmd = run.call_args.args[0]
    assert "--resume" in cmd
    assert "abc" in cmd


def test_different_chats_have_separate_sessions():
    with patch("claude_backend.subprocess.run", return_value=_ok(session_id="s-a")) as run:
        ask_claude(1, "hi")
    with patch("claude_backend.subprocess.run", return_value=_ok(session_id="s-b")) as run:
        ask_claude(2, "hi")  # chat 2's first message → no resume
        assert "--resume" not in run.call_args.args[0]


def test_returns_none_on_nonzero_exit():
    with patch("claude_backend.subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
        assert ask_claude(42, "hi") is None


def test_returns_none_on_timeout():
    with patch("claude_backend.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120)):
        assert ask_claude(42, "hi") is None


def test_returns_none_on_bad_json():
    with patch("claude_backend.subprocess.run", return_value=MagicMock(returncode=0, stdout="not json", stderr="")):
        assert ask_claude(42, "hi") is None


def test_failed_call_does_not_poison_session_cache():
    # A failure should not store a session; next success starts fresh (no stale --resume)
    with patch("claude_backend.subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="x")):
        ask_claude(42, "hi")
    with patch("claude_backend.subprocess.run", return_value=_ok(session_id="fresh")) as run:
        ask_claude(42, "retry")
    assert "--resume" not in run.call_args.args[0]
