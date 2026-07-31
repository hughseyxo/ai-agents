"""Base agent class with lifecycle, retry, and state management."""

import json
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .db import AgentDB, DEFAULT_DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tools denied to every agent unless it explicitly opts back in via
# `allowed_tools`. Agent prompts embed external content — RSS items, web pages,
# email subjects — so a prompt injection that reaches Bash or Write is a host
# compromise, not a bad briefing. The CLI runs with --dangerously-skip-permissions,
# so these flags are the only thing enforcing the boundary.
DENIED_TOOLS = [
    # Code execution and file mutation.
    "Bash", "Write", "Edit", "NotebookEdit", "Task",
    # Outbound fetches steered by prompt content.
    "WebFetch", "WebSearch",
    # Persistence and out-of-band messaging. An injection's goal is to survive
    # the run or reach someone else, and none of these need Bash to do it.
    "CronCreate", "CronDelete", "ScheduleWakeup", "RemoteTrigger",
    "SendMessage", "PushNotification",
    # ToolSearch unlocks every deferred tool above, so denying those without
    # denying it achieves nothing. Agents that need MCP re-enable it via
    # allowed_tools — MCP tools are themselves deferred.
    "ToolSearch", "Skill",
    # Gmail beyond sending. No agent reads mail; read access would turn a
    # prompt injection into mailbox exfiltration.
    "mcp__gmail__gmail_list_messages", "mcp__gmail__gmail_get_message",
    "mcp__gmail__gmail_create_draft", "mcp__gmail__gmail_get_profile",
]


class LLMTimeoutError(RuntimeError):
    """Raised when an LLM CLI call times out."""
    pass


class BaseAgent:
    """
    Base for all scheduled agents.
    Subclasses implement: steps(), report(), and optionally plan().
    """

    name: str = ""
    schedule: str = ""  # cron expression, e.g. "5 7 * * *"
    max_retries: int = 2
    model: str | None = None  # Claude model override (e.g. "claude-sonnet-4-6"); None = CLI default
    providers: list | None = None  # Override provider order/set; None = use PROVIDERS class default
    untrusted_input: bool = False  # True → prompt embeds external content; never route to agy (no tool allowlist there)

    # --- LLM tool surface (claude provider only; agy has no equivalent flags) ---
    # Path to an MCP config relative to REPO_ROOT. None (the default) means the
    # CLI is launched with --strict-mcp-config and no config, i.e. zero MCP
    # servers — an agent that doesn't need Gmail/Todoist/Calendar can't reach them.
    mcp_config: str | None = None
    # Tools to allow back in on top of the read-only defaults, e.g.
    # ["mcp__gmail__gmail_send"] or ["WebSearch"]. Anything listed here is
    # removed from DENIED_TOOLS for this agent. Tuple so the shared class-level
    # default can never be mutated by one agent on behalf of all the others.
    allowed_tools: Sequence[str] = ()

    @classmethod
    def cron_entries(cls) -> list[tuple[str, str]]:
        """(cron_schedule, argv_suffix) pairs for install-cron. Override for agents
        needing multiple entries or extra args (e.g. librarian --mode)."""
        return [(cls.schedule, cls.name)] if cls.schedule else []

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db = AgentDB(db_path)
        self.run_id: int | None = None
        self.context: dict = {}
        self.errors: list = []
        self._failed_steps: list = []
        self.started_at: float | None = None

    def run(self):
        """Main entrypoint — full lifecycle."""
        self.run_id = self.db.start_run(self.name)
        self.started_at = time.time()
        try:
            self.pre_check()
            plan = self.plan()
            self.context["plan"] = plan
            for step in self.steps():
                self._execute_step(step)
            output = self.report()
            if self._failed_steps:
                status = "partial_failure"
                error_msg = f"Steps failed: {', '.join(self._failed_steps)}"
                self.db.complete_run(self.run_id, status=status, output_summary=output, error=error_msg)
            else:
                self.db.complete_run(self.run_id, status="success", output_summary=output)
            return output
        except Exception as e:
            self.db.complete_run(self.run_id, status="error", error=str(e))
            raise

    def _execute_step(self, step: dict):
        """Execute a single step with retry + fallback."""
        name = step["name"]
        fn = step["fn"]
        fallback = step.get("fallback")
        retries = step.get("retries", self.max_retries)
        side_effects = step.get("side_effects", False)

        for attempt in range(retries + 1):
            try:
                result = fn()
                self.context[name] = result
                self.db.record_step(self.run_id, name, "success")
                return
            except LLMTimeoutError as e:
                # If a step with side effects times out, do NOT retry.
                # The side effect (e.g. sending an email) might have already happened.
                self.errors.append((name, attempt, str(e)))
                self.db.record_step(self.run_id, name, "error", error=str(e))
                if side_effects:
                    print(f"[{self.name}] Step '{name}' timed out and has side effects. Skipping retry to avoid duplication.", file=sys.stderr)
                    break
                
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
            except Exception as e:
                self.errors.append((name, attempt, str(e)))
                self.db.record_step(self.run_id, name, "error", error=str(e))
                if attempt < retries:
                    time.sleep(2 ** attempt)  # exponential backoff
                    continue
                if fallback:
                    try:
                        result = fallback()
                        self.context[name] = result
                        self.db.record_step(self.run_id, name, "fallback_success")
                        return
                    except Exception as fe:
                        self.errors.append((name, "fallback", str(fe)))
                        self.db.record_step(self.run_id, name, "fallback_error", error=str(fe))

        # Step fully failed
        self.context[name] = None
        self._failed_steps.append(name)
        print(f"[{self.name}] Step '{name}' failed after {retries + 1} attempts", file=sys.stderr)

    def pre_check(self):
        """Override for pre-flight checks (e.g. token refresh)."""
        pass

    def plan(self) -> dict:
        """Override to make dynamic decisions before execution."""
        return {}

    def steps(self) -> list[dict]:
        """Return ordered list of step dicts. Override in subclass."""
        raise NotImplementedError

    def report(self) -> str:
        """Format + deliver results. Override in subclass."""
        raise NotImplementedError

    # --- LLM CLI synthesis (Antigravity → Claude failover) ---

    PROVIDERS = [
        {
            "name": "antigravity",
            "cmd_prefix": ["agy", "--dangerously-skip-permissions"],
            "cmd_suffix": [],
            "adapt_prompt": True,
        },
        {
            "name": "claude",
            "cmd_prefix": ["claude", "-p", "--dangerously-skip-permissions"],
            "cmd_suffix": ["--output-format", "text"],
            "adapt_prompt": False,
        },
    ]

    # Errors that will fail on any provider — don't bother retrying
    _NON_RETRIABLE = ["context_length", "invalid_request", "too long"]

    def _tool_restriction_flags(self) -> list[str]:
        """Confine the claude CLI to the tools this agent actually needs.

        --strict-mcp-config is unconditional: without it the CLI also loads the
        user's global MCP servers, so an agent would silently inherit whatever
        is configured outside this repo.
        """
        flags = ["--strict-mcp-config"]
        if self.mcp_config:
            flags += ["--mcp-config", str(REPO_ROOT / self.mcp_config)]
        denied = [t for t in DENIED_TOOLS if t not in self.allowed_tools]
        if denied:
            flags += ["--disallowedTools", *denied]
        if self.allowed_tools:
            flags += ["--allowedTools", *self.allowed_tools]
        return flags

    def synthesize(self, prompt: str) -> str:
        """Invoke LLM CLI with MCP access. Tries Antigravity first, falls back to Claude.

        Timeouts are terminal — they do NOT trigger failover. A killed CLI may
        have already executed MCP side effects (sent email, created tasks);
        retrying on a second provider would duplicate them.
        """
        learnings_path = REPO_ROOT / "docs" / "agent-learnings" / f"{self.name}.md"
        if learnings_path.exists():
            learnings = learnings_path.read_text().strip()
            if learnings:
                prompt = f"## Agent Learnings (apply these)\n{learnings}\n\n---\n\n{prompt}"

        last_error = None
        providers = self.providers or self.PROVIDERS
        if self.untrusted_input:
            # Hard-exclude agy from whatever list is in play (class default or
            # per-agent override): it has no --allowedTools/--strict-mcp-config
            # equivalent, so its tool surface cannot be confined at all. The
            # remaining claude path is NOT sandboxed either — it is constrained
            # by _tool_restriction_flags(), which is what actually contains a
            # prompt injection carried in external content.
            providers = [p for p in providers if p["name"] != "antigravity"]
            if not providers:
                msg = (
                    f"[{self.name}] untrusted_input=True left no safe LLM provider "
                    "(all configured providers are unsandboxed); refusing to call agy"
                )
                print(f"[synthesize] {msg}", file=sys.stderr)
                raise RuntimeError(msg)
        for provider in providers:
            p_prompt = self._adapt_prompt_for_antigravity(prompt) if provider["adapt_prompt"] else prompt
            cmd = list(provider["cmd_prefix"]) + list(provider["cmd_suffix"])

            if provider["name"] == "claude":
                if self.model:
                    cmd += ["--model", self.model]
                cmd += self._tool_restriction_flags()

            # For each provider, we allow up to 3 attempts for transient CLI failures
            for attempt in range(3):
                try:
                    print(f"[synthesize] Calling {provider['name']} (attempt {attempt+1})...", file=sys.stderr)
                    result = subprocess.run(
                        cmd, input=p_prompt, capture_output=True, text=True,
                        cwd=str(REPO_ROOT), timeout=600,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        print(f"[synthesize] {provider['name']} succeeded", file=sys.stderr)
                        return result.stdout

                    stderr = result.stderr or ""
                    # rc==0 but empty/whitespace stdout: surface the full stderr so a
                    # silent "successful exit, no output" is diagnosable, not just retried.
                    if result.returncode == 0:
                        print(
                            f"[synthesize] {provider['name']} returned rc=0 with empty stdout; "
                            f"stderr: {stderr}",
                            file=sys.stderr,
                        )
                    # Non-retriable errors (e.g. context length) — move to next provider immediately
                    if any(s in stderr.lower() for s in self._NON_RETRIABLE):
                        print(f"[synthesize] {provider['name']} failed (non-retriable): {stderr[:200]}", file=sys.stderr)
                        last_error = stderr[:500]
                        break

                    # Transient or unknown error — retry current provider with backoff
                    print(f"[synthesize] {provider['name']} attempt {attempt+1} failed (rc={result.returncode}): {stderr[:200]}", file=sys.stderr)
                    last_error = stderr[:500]
                    
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        # Exhausted attempts for this provider
                        break

                except subprocess.TimeoutExpired as e:
                    # Kill + reap the child so a timed-out CLI doesn't linger
                    # holding MCP sockets in long-running executors (e.g. the bot).
                    proc = getattr(e, "process", None)
                    if proc is not None:
                        try:
                            proc.kill()
                            proc.wait()
                        except Exception as kill_err:
                            print(
                                f"[synthesize] failed to reap timed-out child: {kill_err}",
                                file=sys.stderr,
                            )
                    msg = f"{provider['name']} timed out after 600s"
                    print(f"[synthesize] {msg}", file=sys.stderr)
                    raise LLMTimeoutError(msg)
                except OSError as e:
                    print(f"[synthesize] {provider['name']} OS error: {e}", file=sys.stderr)
                    last_error = str(e)
                    break  # Try next provider — no side effects executed, safe to fall over

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    @staticmethod
    def _adapt_prompt_for_antigravity(prompt: str) -> str:
        """Adapt Claude-specific prompt features for Antigravity CLI."""
        # Strip ToolSearch instructions — Antigravity loads all tools immediately
        prompt = re.sub(
            r'## Step 1: Import MCP tools.*?(?=## Step 2)',
            '## Step 1: Tools are available\n'
            'All MCP tools are already loaded and available. Proceed to Step 2.\n\n',
            prompt, flags=re.DOTALL,
        )
        # Remap tool name prefixes: mcp__name__tool → mcp_name_tool
        prompt = prompt.replace('mcp__google_calendar__', 'mcp_google-calendar_')
        prompt = prompt.replace('mcp__todoist__', 'mcp_todoist_')
        prompt = prompt.replace('mcp__gmail__', 'mcp_gmail_')
        # WebFetch → curl via shell (Antigravity has shell access in -y mode)
        prompt = prompt.replace('WebFetch', 'the shell tool with curl')
        # ToolSearch references outside Step 1
        prompt = prompt.replace('ToolSearch', 'the appropriate tool')
        return prompt

    # --- State helpers ---

    def get_state(self, key: str, default=None):
        return self.db.get_state(self.name, key, default)

    def set_state(self, key: str, value):
        self.db.set_state(self.name, key, value)

    def is_duplicate(self, category: str, identifier: str) -> bool:
        return self.db.check_dedup(self.name, category, identifier)

    def mark_seen(self, category: str, identifier: str):
        self.db.mark_seen(self.name, category, identifier)

    def last_run(self) -> dict | None:
        return self.db.get_last_run(self.name)
