"""Antigravity CLI conversational backend for the concierge bot.

Runs the installed `agy` (Antigravity) CLI as the PRIMARY chat backend —
Gemini-quality discussion with no API billing, native tool use via agy's
GLOBAL MCP config (concierge tools already wired in). Returns None on any
failure so the caller can fall back to the Claude backend.

Stateless: agy's plain-text output gives no parseable session id, and
`--continue` is unsafe because cron agents share agy's conversation history.
The `chat_id` parameter is kept only for signature parity with `ask_claude`.
"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONCIERGE_MD = Path(__file__).parent / "CONCIERGE.md"

TIMEOUT_SECONDS = 120

_SYSTEM_PROMPT = CONCIERGE_MD.read_text() if CONCIERGE_MD.exists() else (
    "You are a concierge assistant for Cian's home server. Be concise and direct."
)

# agy tends to append a "### Summary of Work" section listing tools it called;
# this instruction suppresses that so the user sees only their answer.
_OUTPUT_RULE = (
    "Reply with only the answer for the user. Do not include a 'Summary of Work' "
    "section or describe which tools or files you used."
)


def _run_agy(prompt: str, timeout: int = TIMEOUT_SECONDS) -> str | None:
    """Run `agy --dangerously-skip-permissions` with the prompt on stdin.

    The prompt MUST go via stdin (input=), not a `-p` flag — `-p` triggers a
    sandbox-workspace mode that ignores the global MCP tools. Returns the
    stripped stdout, or None on rc≠0 / timeout / not-runnable / empty output.
    """
    try:
        result = subprocess.run(
            ["agy", "--dangerously-skip-permissions"],
            input=prompt, capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("agy CLI timed out after %ss", timeout)
        return None
    except OSError as e:
        logger.warning("agy CLI not runnable: %s", e)
        return None

    if result.returncode != 0:
        logger.warning("agy CLI failed (rc=%s): %s", result.returncode, result.stderr[:200])
        return None

    if not result.stdout or not result.stdout.strip():
        logger.warning("agy CLI returned empty output")
        return None

    return result.stdout.strip()


def ask_antigravity(chat_id: int, user_message: str) -> str | None:
    """Send a message to the agy CLI. Returns the reply, or None on failure.

    Stateless — `chat_id` is accepted for parity with `ask_claude` but unused.
    """
    prompt = f"{_SYSTEM_PROMPT}\n\n{_OUTPUT_RULE}\n\n## User message\n{user_message}"
    return _run_agy(prompt)
