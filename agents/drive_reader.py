"""Fetch the user's newest CV from Google Drive via the claude.ai Google Drive
connector, run through a headless `claude -p`.

No Google Cloud OAuth client or token file: the connector is already
authorised on the user's claude.ai account, and headless `claude -p` loads it.
The CLI runs WITHOUT --dangerously-skip-permissions and with --allowedTools
limited to the two read-only Drive tools (+ ToolSearch, which MCP tools are
deferred behind), so any other tool call — update/share/trash, Bash, Write —
is denied automatically in non-interactive mode.

The model only drives the two calls. File metadata and CV text are taken from
the raw tool results in the stream-json output, never from the model's reply,
and Python checks the file it read is the newest CV in the search results.
"""

import json
import subprocess
import sys
import tempfile

from .base import DENIED_TOOLS

SEARCH_TOOL = "mcp__claude_ai_Google_Drive__search_files"
READ_TOOL = "mcp__claude_ai_Google_Drive__read_file_content"
ALLOWED_TOOLS = ["ToolSearch", SEARCH_TOOL, READ_TOOL]
# Belt and braces: user-level permission allow-rules apply to headless runs
# too, so explicitly deny the dangerous built-ins (Bash/Write/Cron/SendMessage…)
# rather than relying on non-allowed tools being refused.
DISALLOWED_TOOLS = [t for t in DENIED_TOOLS if t not in ALLOWED_TOOLS] + [
    # Other claude.ai connectors are loaded too; whole-server rules cover them.
    "mcp__claude_ai_Gmail", "mcp__claude_ai_Google_Calendar",
    "mcp__claude_ai_Google_Drive__update_file", "mcp__claude_ai_Google_Drive__trash_file",
    "mcp__claude_ai_Google_Drive__share_file", "mcp__claude_ai_Google_Drive__copy_file",
    "mcp__claude_ai_Google_Drive__create_file",
]
GDOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED_MIMES = (GDOC_MIME, DOCX_MIME)
CV_PREFIX = "Cian_Hughes_CV_General"
MODEL = "claude-haiku-4-5"
TIMEOUT = 300

PROMPT = f"""Fetch a CV from Google Drive using exactly two tool calls, then stop.

1. Call {SEARCH_TOOL} with query "title contains '{CV_PREFIX}' and owner = 'me' and trashed = false",
   excludeContentSnippets true, pageSize 50.
2. From the results, pick the most recently modified file (by modifiedTime) whose mimeType is
   "{GDOC_MIME}" or "{DOCX_MIME}", and call {READ_TOOL} with its id.

Do not call any other tool. Reply only DONE.
"""


def _tool_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def parse_stream(stdout: str) -> dict:
    """Extract {id, name, modifiedTime, text} for the newest CV from
    `claude -p --output-format stream-json` output. Raises RuntimeError unless
    the search succeeded and the file read is the newest supported CV."""
    uses, results = {}, {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") not in ("assistant", "user"):
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                uses[block.get("id")] = block
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                results[block.get("tool_use_id")] = _tool_text(block.get("content"))

    def results_for(tool):
        return [(uses[uid].get("input") or {}, results[uid])
                for uid in uses if uses[uid].get("name") == tool and uid in results]

    files = []
    for _, text in results_for(SEARCH_TOOL):
        try:
            files += json.loads(text).get("files", [])
        except (ValueError, AttributeError):
            continue
    candidates = [f for f in files if isinstance(f, dict)
                  and f.get("mimeType") in SUPPORTED_MIMES
                  and (f.get("title") or "").startswith(CV_PREFIX)]
    if not candidates:
        raise RuntimeError(f"No CV matching {CV_PREFIX}* found in Drive search results")
    newest = max(candidates, key=lambda f: f.get("modifiedTime") or "")

    for inp, text in results_for(READ_TOOL):
        if inp.get("fileId") != newest["id"]:
            continue
        try:
            content = (json.loads(text).get("fileContent") or "").strip()
        except (ValueError, AttributeError):
            content = ""
        if not content:
            raise RuntimeError(f"CV {newest['title']} came back empty")
        return {"id": newest["id"], "name": newest["title"],
                "modifiedTime": newest.get("modifiedTime", ""), "text": content}
    raise RuntimeError(f"The newest CV ({newest['title']}) was not read")


def fetch_latest_cv() -> dict:
    """Newest owned Cian_Hughes_CV_General* Google Doc / .docx as
    {id, name, modifiedTime, text}. Raises RuntimeError on any failure."""
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "default", "--max-turns", "6",
           "--disallowedTools", *DISALLOWED_TOOLS, "--allowedTools", *ALLOWED_TOOLS]
    try:
        # Neutral cwd so the repo's CLAUDE.md isn't loaded into this small call.
        proc = subprocess.run(cmd, input=PROMPT, capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Drive CV fetch timed out after {TIMEOUT}s") from e
    except OSError as e:
        raise RuntimeError(f"Could not start claude CLI: {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"Drive CV fetch failed (rc={proc.returncode}): {(proc.stderr or '')[:300]}")
    try:
        return parse_stream(proc.stdout)
    except RuntimeError as e:
        print(f"[drive_reader] {e}; stderr: {(proc.stderr or '')[:300]}", file=sys.stderr)
        raise
