"""Tests for the Antigravity CLI conversational backend.

subprocess is mocked throughout — no real CLI calls. Verifies command
construction (plain `agy --dangerously-skip-permissions`, prompt via stdin),
system-prompt prepending, and graceful failure (returns None) so the caller
can fall back to Claude.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

from antigravity_backend import ask_antigravity, _OUTPUT_RULE


def _ok(stdout="hi"):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def test_returns_stripped_stdout():
    with patch("antigravity_backend.subprocess.run", return_value=_ok("  Your plants are fine.  \n")):
        reply = ask_antigravity(42, "how are my plants?")
    assert reply == "Your plants are fine."


def test_command_is_agy_skip_permissions():
    with patch("antigravity_backend.subprocess.run", return_value=_ok()) as run:
        ask_antigravity(42, "hi")
    cmd = run.call_args.args[0]
    assert cmd == ["agy", "--dangerously-skip-permissions"]


def test_prompt_passed_via_stdin_not_argv():
    # Guard against the `-p` regression: the prompt must go through input=,
    # never as an argv element.
    with patch("antigravity_backend.subprocess.run", return_value=_ok()) as run:
        ask_antigravity(42, "water the monstera")
    cmd = run.call_args.args[0]
    assert "-p" not in cmd
    assert "water the monstera" not in cmd
    assert "water the monstera" in run.call_args.kwargs["input"]


def test_prompt_includes_system_prompt_and_output_rule():
    with patch("antigravity_backend.subprocess.run", return_value=_ok()) as run:
        ask_antigravity(42, "hello there")
    sent = run.call_args.kwargs["input"]
    assert "hello there" in sent
    assert _OUTPUT_RULE in sent
    assert "Summary of Work" in sent


def test_returns_none_on_nonzero_exit():
    with patch("antigravity_backend.subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
        assert ask_antigravity(42, "hi") is None


def test_returns_none_on_empty_stdout():
    with patch("antigravity_backend.subprocess.run", return_value=MagicMock(returncode=0, stdout="   \n", stderr="")):
        assert ask_antigravity(42, "hi") is None


def test_returns_none_on_timeout():
    with patch("antigravity_backend.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=120)):
        assert ask_antigravity(42, "hi") is None


def test_returns_none_on_os_error():
    with patch("antigravity_backend.subprocess.run", side_effect=OSError("agy not found")):
        assert ask_antigravity(42, "hi") is None
