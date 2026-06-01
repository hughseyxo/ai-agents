"""Claude CLI conversational backend for the concierge bot.

Runs the installed `claude` CLI in print mode as the primary chat backend —
Claude-quality discussion on the user's Pro subscription (no API billing), with
native tool use via the concierge MCP server and multi-turn memory via session
resume. Returns None on any failure so the caller can fall back to OpenRouter.
"""
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONCIERGE_MD = Path(__file__).parent / "CONCIERGE.md"
MCP_CONFIG = "telegram-bot/concierge_mcp.json"  # relative to REPO_ROOT (cwd)

CLAUDE_MODEL = "claude-sonnet-4-6"
TIMEOUT_SECONDS = 120

# Per-chat conversation continuity: {chat_id: session_id}. In-memory only —
# a bot restart starts fresh threads, which is fine for a concierge.
_SESSIONS: dict[int, str] = {}

_SYSTEM_PROMPT = CONCIERGE_MD.read_text() if CONCIERGE_MD.exists() else (
    "You are a concierge assistant for Cian's home server. Be concise and direct."
)


def _build_command(chat_id: int) -> list[str]:
    cmd = [
        "claude", "-p",
        "--dangerously-skip-permissions",
        "--output-format", "json",
        "--model", CLAUDE_MODEL,
        "--append-system-prompt", _SYSTEM_PROMPT,
        "--mcp-config", MCP_CONFIG,
        "--strict-mcp-config",
        "--allowedTools", "mcp__concierge",
        "--disallowedTools", "Bash", "Write", "Edit",
    ]
    session_id = _SESSIONS.get(chat_id)
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def ask_claude(chat_id: int, user_message: str) -> str | None:
    """Send a message to the claude CLI. Returns the reply, or None on failure."""
    cmd = _build_command(chat_id)
    try:
        result = subprocess.run(
            cmd, input=user_message, capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("claude CLI timed out after %ss", TIMEOUT_SECONDS)
        return None
    except OSError as e:
        logger.warning("claude CLI not runnable: %s", e)
        return None

    if result.returncode != 0:
        logger.warning("claude CLI failed (rc=%s): %s", result.returncode, result.stderr[:200])
        return None

    try:
        data = json.loads(result.stdout)
        reply = data["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("claude CLI returned unparseable output: %s", e)
        return None

    session_id = data.get("session_id")
    if session_id:
        _SESSIONS[chat_id] = session_id
    return reply
