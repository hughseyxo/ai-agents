"""Tests for CommitSecurityAgent.run_hook()."""

import json
import pytest

from agents.commit_security import CommitSecurityAgent

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
index 1234567..abcdefg 100644
--- a/app.py
+++ b/app.py
@@ -1,5 +1,8 @@
 import os
+import requests

+API_KEY = "sk-proj-abc123secretkey"
+
 def main():
     pass
"""

CRITICAL_FINDING = {"severity": "critical", "file": "app.py", "issue": "Hardcoded API key", "recommendation": "Use env var"}
HIGH_FINDING = {"severity": "high", "file": "app.py", "issue": "SQL injection risk", "recommendation": "Use parameterised queries"}
MEDIUM_FINDING = {"severity": "medium", "file": "app.py", "issue": "Bare except clause", "recommendation": "Catch specific exceptions"}
LOW_FINDING = {"severity": "low", "file": "app.py", "issue": "Missing input validation", "recommendation": "Validate user input"}


def _make_agent(synthesize_return=None, synthesize_raises=None):
    agent = CommitSecurityAgent(db_path=":memory:")
    if synthesize_raises:
        def _raise(prompt):
            raise synthesize_raises
        agent.synthesize = _raise
    elif synthesize_return is not None:
        agent.synthesize = lambda prompt: synthesize_return
    return agent


def _findings_json(findings):
    return json.dumps({"findings": findings, "summary": "test"})


class TestRunHook:
    def test_returns_0_when_no_findings(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_return=_findings_json([]))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 0

    def test_returns_1_on_critical_finding(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_return=_findings_json([CRITICAL_FINDING]))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 1

    def test_returns_1_on_high_finding(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_return=_findings_json([HIGH_FINDING]))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 1

    def test_returns_0_on_medium_only(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_return=_findings_json([MEDIUM_FINDING, LOW_FINDING]))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 0

    def test_returns_0_on_llm_failure(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_raises=RuntimeError("LLM timeout"))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 0  # never block on LLM failure

    def test_returns_0_on_empty_diff(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent()
        agent._get_diff = lambda r: ""
        assert agent.run_hook() == 0

    def test_returns_0_on_whitespace_diff(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent()
        agent._get_diff = lambda r: "   \n  "
        assert agent.run_hook() == 0

    def test_returns_0_on_bad_json(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = _make_agent(synthesize_return="not valid json at all")
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 0  # bad JSON → treat as no findings

    def test_strips_markdown_fences(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        fenced = f"```json\n{_findings_json([CRITICAL_FINDING])}\n```"
        agent = _make_agent(synthesize_return=fenced)
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 1

    def test_mixed_severities_blocks_on_critical(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        findings = [MEDIUM_FINDING, CRITICAL_FINDING, LOW_FINDING]
        agent = _make_agent(synthesize_return=_findings_json(findings))
        agent._get_diff = lambda r: SAMPLE_DIFF
        assert agent.run_hook() == 1


class TestGetDiff:
    def test_truncates_large_diff(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = CommitSecurityAgent(db_path=":memory:")
        big_diff = "x" * (100 * 1024)  # 100KB

        import unittest.mock as mock
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=big_diff, stderr="")
            result = agent._get_diff("HEAD~1..HEAD")

        assert len(result) <= 80 * 1024 + 200  # 80KB cap + truncation notice
        assert "truncated" in result.lower()

    def test_returns_empty_on_subprocess_failure(self, monkeypatch):
        monkeypatch.setenv("GIT_PUSH_RANGE", "HEAD~1..HEAD")
        agent = CommitSecurityAgent(db_path=":memory:")

        import unittest.mock as mock
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="error")
            result = agent._get_diff("HEAD~1..HEAD")

        assert result == ""
