"""Tests for agents.runner crontab installation hardening.

subprocess is mocked — we test the install-cron control flow (fail-closed on a
real `crontab -l` error) and cron-line sanitisation, not real crontab I/O.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from agents import runner


def _mock_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _Agent:
    def __init__(self, name, schedule):
        self.name = name
        self.schedule = schedule


# --- fail-closed on crontab -l error ---

class TestInstallCronFailClosed:
    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_aborts_without_writing_on_crontab_l_error(self, mock_agents, mock_run):
        """A real (non 'no crontab') `crontab -l` failure must abort BEFORE writing,
        so a transient error never wipes the user's existing crontab."""
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        # crontab -l fails with a genuine error (not the empty-crontab case)
        mock_run.return_value = _mock_completed(returncode=1, stderr="crontab: permission denied")

        with pytest.raises(SystemExit) as exc:
            runner.cmd_install_cron(SimpleNamespace())

        assert exc.value.code != 0
        # crontab -l was called, but `crontab -` (the write) must NOT have been
        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert write_calls == []

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_empty_crontab_is_not_an_error(self, mock_agents, mock_run):
        """`no crontab for user` (rc=1) is the legitimate empty case — proceed to write."""
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        mock_run.side_effect = [
            _mock_completed(returncode=1, stderr="no crontab for cian"),  # crontab -l
            _mock_completed(returncode=0),                                 # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert len(write_calls) == 1

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_missing_crontab_binary_is_not_an_error(self, mock_agents, mock_run):
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        mock_run.side_effect = [
            FileNotFoundError("crontab"),       # crontab -l
            _mock_completed(returncode=0),      # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert len(write_calls) == 1


# --- cron line sanitisation ---

class TestInstallCronSanitises:
    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_rejects_injection_in_schedule(self, mock_agents, mock_run):
        """A subclass with a shell-injection schedule must abort the install."""
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * * ; rm -rf /")]
        mock_run.return_value = _mock_completed(returncode=0, stdout="")

        with pytest.raises(SystemExit) as exc:
            runner.cmd_install_cron(SimpleNamespace())

        assert exc.value.code != 0
        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert write_calls == []

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_rejects_injection_in_name(self, mock_agents, mock_run):
        mock_agents.return_value = [_Agent("bad$(whoami)", "5 7 * * *")]
        mock_run.return_value = _mock_completed(returncode=0, stdout="")

        with pytest.raises(SystemExit) as exc:
            runner.cmd_install_cron(SimpleNamespace())

        assert exc.value.code != 0
        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert write_calls == []

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_accepts_valid_schedule_and_name(self, mock_agents, mock_run):
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        mock_run.side_effect = [
            _mock_completed(returncode=0, stdout=""),  # crontab -l
            _mock_completed(returncode=0),             # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert len(write_calls) == 1
        written = write_calls[0][1]["input"]
        assert "daily-briefing" in written
