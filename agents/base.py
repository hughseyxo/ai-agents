"""Base agent class with lifecycle, retry, and state management."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .db import AgentDB, DEFAULT_DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent


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
            "cmd_prefix": ["claude", "--dangerously-skip-permissions"],
            "cmd_suffix": ["--output-format", "text"],
            "adapt_prompt": False,
        },
    ]

    # Errors that will fail on any provider — don't bother retrying
    _NON_RETRIABLE = ["context_length", "invalid_request", "too long"]

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
        for provider in (self.providers or self.PROVIDERS):
            p_prompt = self._adapt_prompt_for_antigravity(prompt) if provider["adapt_prompt"] else prompt
            cmd = list(provider["cmd_prefix"]) + list(provider["cmd_suffix"])

            if provider["name"] == "claude" and self.model:
                cmd += ["--model", self.model]

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

                except subprocess.TimeoutExpired:
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
