"""Base agent class with lifecycle, retry, and state management."""

import json
import subprocess
import sys
import time
from pathlib import Path

from .db import AgentDB, DEFAULT_DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent


class BaseAgent:
    """
    Base for all scheduled agents.
    Subclasses implement: steps(), report(), and optionally plan().
    """

    name: str = ""
    schedule: str = ""  # cron expression, e.g. "5 7 * * *"
    max_retries: int = 2

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db = AgentDB(db_path)
        self.run_id: int | None = None
        self.context: dict = {}
        self.errors: list = []
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

        for attempt in range(retries + 1):
            try:
                result = fn()
                self.context[name] = result
                self.db.record_step(self.run_id, name, "success")
                return
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

        # Step fully failed — continue with None
        self.context[name] = None
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

    # --- Claude CLI synthesis ---

    def synthesize(self, prompt: str) -> str:
        """Invoke Claude CLI with MCP access for data fetching and synthesis."""
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {result.stderr[:500]}")
        return result.stdout

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
