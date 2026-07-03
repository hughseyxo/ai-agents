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
    def __init__(self, name, schedule, entries=None):
        self.name = name
        self.schedule = schedule
        self._entries = entries

    def cron_entries(self):
        if self._entries is not None:
            return self._entries
        return [(self.schedule, self.name)] if self.schedule else []


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


# --- multi-entry emission, multi-token sanitisation, stray warning ---

_RUN_AGENT = runner.REPO_ROOT / "run-agent.sh"
_LOG_FILE = runner.REPO_ROOT / "output" / "cron.log"


class TestInstallCronEntries:
    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_emits_multi_entry_agent_inside_managed_block(self, mock_agents, mock_run):
        """An agent with two multi-token cron_entries() must emit both lines,
        exactly formatted, inside the managed block."""
        mock_agents.return_value = [
            _Agent("librarian", "", entries=[
                ("0 6 * * 0", "librarian --mode audit"),
                ("0 6 * * 1-6", "librarian --mode watch"),
            ]),
        ]
        mock_run.side_effect = [
            _mock_completed(returncode=0, stdout=""),  # crontab -l
            _mock_completed(returncode=0),             # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert len(write_calls) == 1
        written = write_calls[0][1]["input"].splitlines()
        start = written.index("# --- ai-agents managed ---")
        end = written.index("# --- end ai-agents ---")
        block = written[start + 1:end]
        assert block == [
            f"0 6 * * 0 {_RUN_AGENT} librarian --mode audit >> {_LOG_FILE} 2>&1",
            f"0 6 * * 1-6 {_RUN_AGENT} librarian --mode watch >> {_LOG_FILE} 2>&1",
        ]

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_rejects_shell_metacharacter_in_suffix_token(self, mock_agents, mock_run):
        """A cron_entries() suffix containing a shell metacharacter must abort
        before any crontab write (fail closed)."""
        mock_agents.return_value = [
            _Agent("librarian", "", entries=[("0 6 * * 0", "librarian; rm -rf /")]),
        ]
        mock_run.return_value = _mock_completed(returncode=0, stdout="")

        with pytest.raises(SystemExit) as exc:
            runner.cmd_install_cron(SimpleNamespace())

        assert exc.value.code != 0
        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert write_calls == []

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_rejects_dollar_token_in_suffix(self, mock_agents, mock_run):
        mock_agents.return_value = [
            _Agent("librarian", "", entries=[("0 6 * * 0", "librarian --mode $(whoami)")]),
        ]
        mock_run.return_value = _mock_completed(returncode=0, stdout="")

        with pytest.raises(SystemExit) as exc:
            runner.cmd_install_cron(SimpleNamespace())

        assert exc.value.code != 0
        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert write_calls == []

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_warns_about_stray_run_agent_lines_and_preserves_them(self, mock_agents, mock_run, capsys):
        """An unmanaged run-agent.sh line outside the managed block must trigger
        a stderr WARNING and survive the rewrite (not be deleted)."""
        stray = f"0 * * * * {_RUN_AGENT} plant-agent >> {_LOG_FILE} 2>&1"
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        mock_run.side_effect = [
            _mock_completed(returncode=0, stdout=stray + "\n"),  # crontab -l
            _mock_completed(returncode=0),                        # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        err = capsys.readouterr().err
        assert "WARNING: unmanaged run-agent.sh crontab lines" in err
        assert stray in err

        write_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["crontab", "-"]]
        assert len(write_calls) == 1
        written = write_calls[0][1]["input"].splitlines()
        # stray line preserved, outside the managed block
        assert stray in written
        assert written.index(stray) < written.index("# --- ai-agents managed ---")

    @patch("agents.runner.subprocess.run")
    @patch("agents.runner._all_agents")
    def test_no_warning_when_no_stray_lines(self, mock_agents, mock_run, capsys):
        mock_agents.return_value = [_Agent("daily-briefing", "5 7 * * *")]
        mock_run.side_effect = [
            _mock_completed(returncode=0, stdout="MAILTO=x@example.com\n"),  # crontab -l
            _mock_completed(returncode=0),                                    # crontab -
        ]

        runner.cmd_install_cron(SimpleNamespace())

        assert "WARNING" not in capsys.readouterr().err


# --- cron_entries() ---

def test_librarian_cron_entries_cover_both_modes():
    from agents.librarian import LibrarianAgent
    assert LibrarianAgent.cron_entries() == [
        ("0 6 * * 0", "librarian --mode audit"),
        ("0 6 * * 1-6", "librarian --mode watch"),
    ]


def test_default_cron_entries_from_schedule():
    from agents.plant_agent import PlantAgent
    assert PlantAgent.cron_entries() == [("0 * * * *", "plant-agent")]
