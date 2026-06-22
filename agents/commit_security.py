"""Commit Security Agent.

Scans git diff for security issues (secrets, vulnerabilities, sensitive data).
Used as a blocking pre-push git hook: run_hook() returns 0 (allow) or 1 (block).
Also runnable on-demand: python3 -m agents commit-security
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .base import BaseAgent, REPO_ROOT

MAX_DIFF_BYTES = 80 * 1024  # 80KB cap to stay within LLM context

BLOCKING_SEVERITIES = ("critical", "high")


def _is_blocking(finding: dict) -> bool:
    """True if a finding's severity blocks the push. Case-insensitive: the LLM
    may emit "Critical"/"HIGH" etc., and a case-sensitive compare would fail open."""
    return str(finding.get("severity", "")).strip().lower() in BLOCKING_SEVERITIES


class CommitSecurityAgent(BaseAgent):
    name = "commit-security"
    schedule = ""  # on-demand only
    model = "claude-haiku-4-5"
    providers = list(reversed(BaseAgent.PROVIDERS))  # Claude-first, Antigravity fallback

    # --- Git hook entry point ---

    def run_hook(self) -> int:
        """Entry point for git pre-push hook. Returns 0 (allow) or 1 (block)."""
        diff_range = os.environ.get("GIT_PUSH_RANGE", "@{u}..HEAD")
        diff = self._get_diff(diff_range)
        if not diff.strip():
            print("[commit-security] No diff to scan.")
            return 0
        findings = self._analyze(diff)
        self._print_report(findings)
        blocked = any(_is_blocking(f) for f in findings)
        return 1 if blocked else 0

    # --- Core logic (shared by hook and agent step) ---

    def _get_diff(self, diff_range: str) -> str:
        """Get git patch for the given range, excluding binaries, capped at MAX_DIFF_BYTES."""
        result = subprocess.run(
            [
                "git", "diff", diff_range,
                "--diff-filter=ACMR",
                "--",
                ":!*.png", ":!*.jpg", ":!*.jpeg", ":!*.gif",
                ":!*.mp4", ":!*.mkv", ":!*.webp", ":!*.ico",
                ":!*.pdf", ":!*.zip", ":!*.tar.gz",
            ],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            return ""
        diff = result.stdout
        if len(diff) > MAX_DIFF_BYTES:
            diff = diff[:MAX_DIFF_BYTES]
            diff += f"\n\n[diff truncated at {MAX_DIFF_BYTES // 1024}KB — remaining content not scanned]"
        return diff

    def _analyze(self, diff: str) -> list[dict]:
        """Call LLM to analyze diff. Returns [] on any failure (never blocks on LLM error)."""
        prompt_path = REPO_ROOT / "agents" / "prompts" / "commit_security.md"
        prompt = prompt_path.read_text() + "\n\n" + diff

        try:
            output = self.synthesize(prompt)
        except Exception as e:
            print(f"[commit-security] LLM analysis failed ({e}) — allowing push.", file=sys.stderr)
            return []

        text = output.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text.strip())
        text = text.strip()

        # Empty after stripping = LLM returned an empty code block → no findings
        if not text:
            return []

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[commit-security] Could not parse LLM response ({e}) — allowing push.", file=sys.stderr)
            return []

        if isinstance(data, list):
            return data
        return data.get("findings", []) if isinstance(data, dict) else []

    def _print_report(self, findings: list[dict]):
        if not findings:
            print("[commit-security] No security issues found.")
            return

        by_severity = {"critical": [], "high": [], "medium": [], "low": []}
        for f in findings:
            sev = f.get("severity", "low").lower()
            by_severity.setdefault(sev, []).append(f)

        labels = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
        for sev in ("critical", "high", "medium", "low"):
            for f in by_severity.get(sev, []):
                file_ = f.get("file", "?")
                issue = f.get("issue", "?")
                rec = f.get("recommendation", "")
                print(f"  [{labels[sev]}] {file_}: {issue}")
                if rec:
                    print(f"           → {rec}")

    # --- Normal agent path (for manual / on-demand runs via CLI) ---

    def steps(self):
        return [{"name": "scan", "fn": self._scan_step}]

    def _scan_step(self):
        diff_range = os.environ.get("GIT_PUSH_RANGE", "@{u}..HEAD")
        diff = self._get_diff(diff_range)
        if not diff.strip():
            return {"findings": [], "diff_range": diff_range, "empty": True}
        findings = self._analyze(diff)
        return {"findings": findings, "diff_range": diff_range}

    def report(self) -> str:
        scan = self.context.get("scan") or {}
        if scan.get("empty"):
            return "commit-security: no diff to scan"
        findings = scan.get("findings", [])
        blocked = any(_is_blocking(f) for f in findings)
        self._print_report(findings)
        if blocked:
            critical_high = [f for f in findings if _is_blocking(f)]
            return f"BLOCKED: {len(critical_high)} critical/high finding(s)"
        return f"OK: {len(findings)} finding(s) (none critical/high)"
