"""Tests for BaseAgent.synthesize() and _adapt_prompt_for_gemini().

subprocess.run is mocked because we're testing failover logic,
not the actual CLI binaries.
"""

import subprocess
from unittest.mock import patch, call

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
# _adapt_prompt_for_gemini — pure function tests (no mocks needed)
# ===================================================================

class TestAdaptPromptForGemini:
    def test_strips_toolsearch_step1(self):
        prompt = (
            "## Step 1: Import MCP tools\n"
            "Use ToolSearch to load required tools:\n"
            "- Search todoist\n"
            "- Search gmail\n\n"
            "## Step 2: Get data\n"
            "Do things here."
        )
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "ToolSearch" not in result
        assert "Import MCP tools" not in result
        assert "Tools are available" in result
        assert "## Step 2: Get data" in result
        assert "Do things here." in result

    def test_remaps_todoist_tool_names(self):
        prompt = "Call mcp__todoist__find-tasks with projectId"
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "mcp_todoist_find-tasks" in result
        assert "mcp__todoist__" not in result

    def test_remaps_gmail_tool_names(self):
        prompt = "Send via mcp__gmail__gmail_send"
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "mcp_gmail_gmail_send" in result
        assert "mcp__gmail__" not in result

    def test_remaps_calendar_tool_names(self):
        prompt = "Call mcp__google_calendar__gcal_list_events"
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "mcp_google-calendar_gcal_list_events" in result
        assert "mcp__google_calendar__" not in result

    def test_replaces_webfetch(self):
        prompt = "Fetch all feeds below via WebFetch"
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "WebFetch" not in result
        assert "curl" in result

    def test_replaces_toolsearch_outside_step1(self):
        prompt = "Use ToolSearch to find the tool"
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "ToolSearch" not in result
        assert "the appropriate tool" in result

    def test_preserves_unrelated_content(self):
        prompt = "## Step 2: Fetch data\nDo something normal."
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert result == prompt

    def test_handles_multiple_tool_references(self):
        prompt = (
            "Call mcp__todoist__find-tasks then mcp__gmail__gmail_send "
            "and mcp__google_calendar__gcal_list_events"
        )
        result = BaseAgent._adapt_prompt_for_gemini(prompt)
        assert "mcp_todoist_find-tasks" in result
        assert "mcp_gmail_gmail_send" in result
        assert "mcp_google-calendar_gcal_list_events" in result
        assert "mcp__" not in result


# ===================================================================
# synthesize — failover logic tests (subprocess mocked)
# ===================================================================

class TestSynthesize:
    @patch("agents.base.subprocess.run")
    def test_claude_succeeds_first_try(self, mock_run):
        mock_run.return_value = mock_result(stdout="briefing output")
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "briefing output"
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"

    @patch("agents.base.subprocess.run")
    def test_falls_back_to_gemini_on_claude_failure(self, mock_run):
        mock_run.side_effect = [
            mock_result(returncode=1, stdout="", stderr="rate limit exceeded"),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "gemini output"
        assert mock_run.call_count == 2
        first_cmd = mock_run.call_args_list[0][0][0]
        second_cmd = mock_run.call_args_list[1][0][0]
        assert first_cmd[0] == "claude"
        assert second_cmd[0] == "gemini"

    @patch("agents.base.subprocess.run")
    def test_falls_back_on_empty_stdout(self, mock_run):
        mock_run.side_effect = [
            mock_result(returncode=0, stdout="", stderr=""),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "gemini output"
        assert mock_run.call_count == 2

    @patch("agents.base.subprocess.run")
    def test_falls_back_on_whitespace_only_stdout(self, mock_run):
        mock_run.side_effect = [
            mock_result(returncode=0, stdout="   \n  ", stderr=""),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "gemini output"

    @patch("agents.base.subprocess.run")
    def test_claude_timeout_is_terminal_no_failover(self, mock_run):
        """Timeouts must NOT trigger Gemini failover: if Claude was mid-way
        through MCP side effects (sending email, creating tasks) when killed,
        running Gemini would duplicate those side effects."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=600),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()

        with pytest.raises(RuntimeError, match="timed out"):
            agent.synthesize("test prompt")

        # Critically: Gemini must NOT be invoked
        assert mock_run.call_count == 1

    @patch("agents.base.subprocess.run")
    def test_raises_when_both_providers_fail(self, mock_run):
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="rate limit"),
            mock_result(returncode=1, stderr="quota exceeded"),
        ]
        agent = make_agent()

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            agent.synthesize("test prompt")

        assert mock_run.call_count == 2

    @patch("agents.base.subprocess.run")
    def test_non_retriable_error_raises_immediately(self, mock_run):
        mock_run.return_value = mock_result(
            returncode=1, stderr="context_length exceeded"
        )
        agent = make_agent()

        with pytest.raises(RuntimeError, match="non-retriable"):
            agent.synthesize("test prompt")

        # Should NOT try gemini
        assert mock_run.call_count == 1

    @patch("agents.base.subprocess.run")
    def test_non_retriable_invalid_request(self, mock_run):
        mock_run.return_value = mock_result(
            returncode=1, stderr="invalid_request: prompt malformed"
        )
        agent = make_agent()

        with pytest.raises(RuntimeError, match="non-retriable"):
            agent.synthesize("test prompt")

        assert mock_run.call_count == 1

    @patch("agents.base.subprocess.run")
    def test_non_retriable_too_long(self, mock_run):
        mock_run.return_value = mock_result(
            returncode=1, stderr="Error: prompt too long for model"
        )
        agent = make_agent()

        with pytest.raises(RuntimeError, match="non-retriable"):
            agent.synthesize("test prompt")

        assert mock_run.call_count == 1

    @patch("agents.base.subprocess.run")
    def test_gemini_prompt_is_adapted(self, mock_run):
        """When falling back to Gemini, the prompt should be adapted."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="429 rate limit"),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()

        prompt = "Call mcp__todoist__find-tasks via WebFetch"
        agent.synthesize(prompt)

        # Check the prompt passed to gemini was adapted
        gemini_call = mock_run.call_args_list[1]
        gemini_cmd = gemini_call[0][0]
        # The prompt is the element after "-p" in the command
        p_index = gemini_cmd.index("-p")
        gemini_prompt = gemini_cmd[p_index + 1]
        assert "mcp_todoist_find-tasks" in gemini_prompt
        assert "mcp__todoist__" not in gemini_prompt
        assert "WebFetch" not in gemini_prompt

    @patch("agents.base.subprocess.run")
    def test_claude_prompt_is_not_adapted(self, mock_run):
        """When Claude succeeds, the prompt should NOT be adapted."""
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()

        prompt = "Call mcp__todoist__find-tasks"
        agent.synthesize(prompt)

        claude_cmd = mock_run.call_args[0][0]
        p_index = claude_cmd.index("-p")
        claude_prompt = claude_cmd[p_index + 1]
        assert "mcp__todoist__find-tasks" in claude_prompt

    @patch("agents.base.subprocess.run")
    def test_timeout_is_600_seconds(self, mock_run):
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()

        agent.synthesize("test")

        assert mock_run.call_args[1]["timeout"] == 600

    @patch("agents.base.subprocess.run")
    def test_default_model_passes_no_model_flag(self, mock_run):
        """BaseAgent.model is None by default — preserves current behaviour."""
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        assert agent.model is None

        agent.synthesize("test")

        cmd = mock_run.call_args[0][0]
        assert "--model" not in cmd

    @patch("agents.base.subprocess.run")
    def test_model_attribute_injects_claude_model_flag(self, mock_run):
        mock_run.return_value = mock_result(stdout="output")
        agent = make_agent()
        agent.model = "claude-sonnet-4-6"

        agent.synthesize("test")

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"

    @patch("agents.base.subprocess.run")
    def test_model_attribute_does_not_pollute_gemini_cmd(self, mock_run):
        """When Claude fails and Gemini takes over, the Claude --model flag
        must not leak into the Gemini command."""
        mock_run.side_effect = [
            mock_result(returncode=1, stderr="rate limit"),
            mock_result(stdout="gemini output"),
        ]
        agent = make_agent()
        agent.model = "claude-haiku-4-5"

        agent.synthesize("test")

        gemini_cmd = mock_run.call_args_list[1][0][0]
        assert gemini_cmd[0] == "gemini"
        assert "--model" not in gemini_cmd
        assert "claude-haiku-4-5" not in gemini_cmd
