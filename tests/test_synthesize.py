"""Tests for BaseAgent.synthesize() and _adapt_prompt_for_antigravity().

subprocess.run is mocked because we're testing failover logic,
not the actual CLI binaries.
"""

import subprocess
from unittest.mock import patch, call, MagicMock

import pytest

from agents.base import BaseAgent


# --- Helpers ---

def make_agent():
    """Create a BaseAgent with an in-memory DB (no disk state needed)."""
    return BaseAgent(db_path=":memory:")


def mock_result(returncode=0, stdout="output", stderr=""):
    """Build a subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ===================================================================
# _adapt_prompt_for_antigravity — pure function tests (no mocks needed)
# ===================================================================

class TestAdaptPromptForAntigravity:
    def test_strips_toolsearch_step1(self):
        prompt = (
            "## Step 1: Import MCP tools\n"
            "Use ToolSearch to load required tools:\n"
            "- Search todoist\n"
            "- Search gmail\n\n"
            "## Step 2: Get data\n"
            "Do things here."
        )
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "ToolSearch" not in result
        assert "Import MCP tools" not in result
        assert "Tools are available" in result
        assert "## Step 2: Get data" in result
        assert "Do things here." in result

    def test_remaps_todoist_tool_names(self):
        prompt = "Call mcp__todoist__find-tasks with projectId"
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "mcp_todoist_find-tasks" in result
        assert "mcp__todoist__" not in result

    def test_remaps_gmail_tool_names(self):
        prompt = "Send via mcp__gmail__gmail_send"
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "mcp_gmail_gmail_send" in result
        assert "mcp__gmail__" not in result

    def test_remaps_calendar_tool_names(self):
        prompt = "Call mcp__google_calendar__gcal_list_events"
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "mcp_google-calendar_gcal_list_events" in result
        assert "mcp__google_calendar__" not in result

    def test_replaces_webfetch(self):
        prompt = "Fetch all feeds below via WebFetch"
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "WebFetch" not in result
        assert "curl" in result

    def test_replaces_toolsearch_outside_step1(self):
        prompt = "Use ToolSearch to find the tool"
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "ToolSearch" not in result
        assert "the appropriate tool" in result

    def test_preserves_unrelated_content(self):
        prompt = "## Step 2: Fetch data\nDo something normal."
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert result == prompt

    def test_handles_multiple_tool_references(self):
        prompt = (
            "Call mcp__todoist__find-tasks then mcp__gmail__gmail_send "
            "and mcp__google_calendar__gcal_list_events"
        )
        result = BaseAgent._adapt_prompt_for_antigravity(prompt)
        assert "mcp_todoist_find-tasks" in result
        assert "mcp_gmail_gmail_send" in result
        assert "mcp_google-calendar_gcal_list_events" in result
        assert "mcp__" not in result


# ===================================================================
# synthesize — failover logic tests (subprocess mocked)
# ===================================================================

class TestSynthesize:
    @patch("agents.base.subprocess.run")
    def test_agy_succeeds_first_try(self, mock_run):
        mock_run.return_value = mock_result(stdout="briefing output")
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "briefing output"
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "agy"

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_falls_back_to_claude_on_agy_failure(self, mock_sleep, mock_run):
        """Antigravity fails 3 times, then Claude succeeds."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="transient error"),
            mock_result(returncode=1, stderr="transient error"),
            mock_result(returncode=1, stderr="transient error"),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"
        assert mock_run.call_count == 4
        assert mock_run.call_args_list[0][0][0][0] == "agy"
        assert mock_run.call_args_list[3][0][0][0] == "claude"

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_falls_back_on_empty_stdout(self, mock_sleep, mock_run):
        """Empty stdout is treated as transient failure, retries 3 times then fails over."""
        mock_run.side_effect = [
            mock_result(stdout=""),
            mock_result(stdout=""),
            mock_result(stdout=""),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"
        assert mock_run.call_count == 4

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_falls_back_on_whitespace_only_stdout(self, mock_sleep, mock_run):
        mock_run.side_effect = [
            mock_result(stdout="  "),
            mock_result(stdout="  "),
            mock_result(stdout="  "),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_empty_stdout_rc0_logs_stderr(self, mock_sleep, mock_run, capsys):
        """rc==0 with empty stdout must log the provider's stderr, not silently retry."""
        mock_run.side_effect = [
            mock_result(returncode=0, stdout="", stderr="some diagnostic from the CLI"),
            mock_result(returncode=0, stdout="", stderr="some diagnostic from the CLI"),
            mock_result(returncode=0, stdout="", stderr="some diagnostic from the CLI"),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"
        captured = capsys.readouterr()
        assert "some diagnostic from the CLI" in captured.err

    @patch("agents.base.subprocess.run")
    def test_timeout_kills_and_reaps_child(self, mock_run):
        """The TimeoutExpired path must kill + reap the child process to free resources."""
        proc = MagicMock()
        exc = subprocess.TimeoutExpired(cmd="agy", timeout=600)
        exc.process = proc  # simulate an exception carrying the child handle
        mock_run.side_effect = exc
        agent = make_agent()

        with pytest.raises(RuntimeError, match="timed out"):
            agent.synthesize("test prompt")

        proc.kill.assert_called_once()
        proc.wait.assert_called_once()

    @patch("agents.base.subprocess.run")
    def test_agy_timeout_is_terminal_no_failover(self, mock_run):
        """Timeouts must NOT trigger Claude failover or retries."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="agy", timeout=600),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        with pytest.raises(RuntimeError, match="timed out"):
            agent.synthesize("test prompt")

        assert mock_run.call_count == 1

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_oserror_falls_back_to_next_provider(self, mock_sleep, mock_run):
        """OSError (e.g. binary not found) should trigger provider failover, not crash."""
        mock_run.side_effect = [
            OSError("No such file or directory: 'agy'"),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0][0] == "agy"
        assert mock_run.call_args_list[1][0][0][0] == "claude"

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_raises_when_both_providers_fail(self, mock_sleep, mock_run):
        """Both fail 3 times each."""
        mock_run.side_effect = [mock_result(returncode=1, stderr="err")] * 6
        agent = make_agent()

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            agent.synthesize("test prompt")

        assert mock_run.call_count == 6

    @patch("agents.base.subprocess.run")
    def test_non_retriable_error_raises_immediately(self, mock_run):
        """Non-retriable error (e.g. context length) skips retries and falls back to next provider."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="context_length exceeded"),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        # Should skip Antigravity retries and succeed on Claude
        assert result == "claude output"
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0][0] == "agy"
        assert mock_run.call_args_list[1][0][0][0] == "claude"

    @patch("agents.base.subprocess.run")
    def test_agy_prompt_is_adapted_by_default(self, mock_run):
        """Antigravity is tried first, and its prompt should be adapted."""
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()

        prompt = "Call mcp__todoist__find-tasks"
        agent.synthesize(prompt)

        agy_cmd = mock_run.call_args[0][0]
        assert "-p" not in agy_cmd
        agy_prompt = mock_run.call_args[1]["input"]
        assert "mcp_todoist_find-tasks" in agy_prompt

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_claude_prompt_is_not_adapted_on_fallback(self, mock_sleep, mock_run):
        """When Antigravity fails 3 times, Claude gets the UNADAPTED prompt."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(stdout="output"),
        ]
        agent = make_agent()

        prompt = "Call mcp__todoist__find-tasks"
        agent.synthesize(prompt)

        claude_cmd = mock_run.call_args_list[3][0][0]
        assert "-p" in claude_cmd
        claude_prompt = mock_run.call_args_list[3][1]["input"]
        assert "mcp__todoist__find-tasks" in claude_prompt

    @patch("agents.base.subprocess.run")
    def test_providers_override_uses_claude_first(self, mock_run):
        """When providers is reversed, Claude is tried first."""
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        agent.providers = list(reversed(BaseAgent.PROVIDERS))

        agent.synthesize("test prompt")

        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_providers_override_falls_back_to_agy(self, mock_sleep, mock_run):
        """When Claude primary fails 3 times, Antigravity is the fallback."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(stdout="agy output"),
        ]
        agent = make_agent()
        agent.providers = list(reversed(BaseAgent.PROVIDERS))

        result = agent.synthesize("test prompt")

        assert result == "agy output"
        assert mock_run.call_count == 4
        assert mock_run.call_args_list[0][0][0][0] == "claude"
        assert mock_run.call_args_list[3][0][0][0] == "agy"

    @patch("agents.base.subprocess.run")
    def test_timeout_is_600_seconds(self, mock_run):
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()

        agent.synthesize("test")

        assert mock_run.call_args[1]["timeout"] == 600

    @patch("agents.base.subprocess.run")
    def test_model_attribute_does_not_inject_flag_to_agy_by_default(self, mock_run):
        """Antigravity CLI uses its default model unless mapped."""
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        agent.model = "claude-haiku-4-5"

        agent.synthesize("test")

        cmd = mock_run.call_args[0][0]
        assert "--model" not in cmd

    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_model_attribute_injects_claude_model_flag_on_fallback(self, mock_sleep, mock_run):
        """When Antigravity fails 3 times, the fallback Claude call should include the --model flag."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(returncode=1, stderr="error"),
            mock_result(stdout="output"),
        ]
        agent = make_agent()
        agent.model = "claude-sonnet-4-6"

        agent.synthesize("test")

        # Index 3 is the 4th call (Antigravity x3, then Claude)
        claude_cmd = mock_run.call_args_list[3][0][0]
        assert "--model" in claude_cmd
        assert claude_cmd[claude_cmd.index("--model") + 1] == "claude-sonnet-4-6"

    @patch("agents.base.subprocess.run")
    def test_learnings_file_prepended_to_prompt(self, mock_run, tmp_path):
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        agent.name = "test-agent"
        learnings_dir = tmp_path / "docs" / "agent-learnings"
        learnings_dir.mkdir(parents=True)
        (learnings_dir / f"{agent.name}.md").write_text("- Keep responses short\n")
        with patch("agents.base.REPO_ROOT", tmp_path):
            agent.synthesize("Do a thing")
        prompt = mock_run.call_args[1]["input"]
        assert "Agent Learnings" in prompt
        assert "Keep responses short" in prompt
        assert "Do a thing" in prompt

    @patch("agents.base.subprocess.run")
    def test_no_learnings_file_leaves_prompt_unchanged(self, mock_run, tmp_path):
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        agent.name = "test-agent"
        with patch("agents.base.REPO_ROOT", tmp_path):
            agent.synthesize("Do a thing")
        prompt = mock_run.call_args[1]["input"]
        assert "Agent Learnings" not in prompt
        assert prompt == "Do a thing"


def test_untrusted_input_skips_antigravity(monkeypatch):
    from agents.base import BaseAgent

    class A(BaseAgent):
        name = "t-untrusted"
        untrusted_input = True

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        m = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        return m

    monkeypatch.setattr("agents.base.subprocess.run", fake_run)
    A(db_path=":memory:").synthesize("hello")
    assert calls and all(c == "claude" for c in calls)
