# Librarian Audit Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 6 findings from the 2026-05-28 librarian audit: broken synthesize tests, missing OSError failover, wrong file glob extension, dead code, conflicting prompt instruction, and wrong security remediation suggestion.

**Architecture:** Five targeted surgical edits across five files. Findings 1 and 2 are coupled (both touch `synthesize()` behaviour) and are addressed together. Findings 3–6 are fully independent one-liners or small patches.

**Tech Stack:** Python 3.10, pytest, subprocess, agents/base.py BaseAgent

---

### Task 1: Fix broken synthesize tests (Finding 1)

**Files:**
- Modify: `tests/test_synthesize.py`

The `synthesize()` method no longer passes the prompt as a `-p` CLI argument — it uses `input=p_prompt` (stdin). Four tests still look up the prompt via `cmd.index("-p") + 1` and will fail with `ValueError`.

- [ ] **Step 1: Run the test suite to confirm failures**

```bash
pytest tests/test_synthesize.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: 4 failures — `test_agy_prompt_is_adapted_by_default`, `test_claude_prompt_is_not_adapted_on_fallback`, `test_learnings_file_prepended_to_prompt`, `test_no_learnings_file_leaves_prompt_unchanged`

- [ ] **Step 2: Fix `test_agy_prompt_is_adapted_by_default`**

Replace lines 217–220:
```python
        agy_cmd = mock_run.call_args[0][0]
        p_index = agy_cmd.index("-p")
        agy_prompt = agy_cmd[p_index + 1]
        assert "mcp_todoist_find-tasks" in agy_prompt
```
With:
```python
        agy_cmd = mock_run.call_args[0][0]
        assert "-p" not in agy_cmd
        agy_prompt = mock_run.call_args[1]["input"]
        assert "mcp_todoist_find-tasks" in agy_prompt
```

- [ ] **Step 3: Fix `test_claude_prompt_is_not_adapted_on_fallback`**

Replace lines 237–240:
```python
        claude_cmd = mock_run.call_args_list[3][0][0]
        p_index = claude_cmd.index("-p")
        claude_prompt = claude_cmd[p_index + 1]
        assert "mcp__todoist__find-tasks" in claude_prompt
```
With:
```python
        claude_cmd = mock_run.call_args_list[3][0][0]
        assert "-p" not in claude_cmd
        claude_prompt = mock_run.call_args_list[3][1]["input"]
        assert "mcp__todoist__find-tasks" in claude_prompt
```

- [ ] **Step 4: Fix `test_learnings_file_prepended_to_prompt`**

Replace lines 326–330:
```python
        cmd = mock_run.call_args[0][0]
        prompt = cmd[cmd.index("-p") + 1]
        assert "Agent Learnings" in prompt
        assert "Keep responses short" in prompt
        assert "Do a thing" in prompt
```
With:
```python
        prompt = mock_run.call_args[1]["input"]
        assert "Agent Learnings" in prompt
        assert "Keep responses short" in prompt
        assert "Do a thing" in prompt
```

- [ ] **Step 5: Fix `test_no_learnings_file_leaves_prompt_unchanged`**

Replace lines 339–342:
```python
        cmd = mock_run.call_args[0][0]
        prompt = cmd[cmd.index("-p") + 1]
        assert "Agent Learnings" not in prompt
        assert prompt == "Do a thing"
```
With:
```python
        prompt = mock_run.call_args[1]["input"]
        assert "Agent Learnings" not in prompt
        assert prompt == "Do a thing"
```

- [ ] **Step 6: Run the test suite to confirm all pass**

```bash
pytest tests/test_synthesize.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: all tests PASSED, 0 failures

- [ ] **Step 7: Commit**

```bash
git add tests/test_synthesize.py
git commit -m "fix: update synthesize tests to use stdin input kwarg instead of -p arg"
```

---

### Task 2: Add OSError failover to synthesize() + test (Finding 2)

**Files:**
- Modify: `agents/base.py` (synthesize method, ~line 196)
- Modify: `tests/test_synthesize.py` (add one test to TestSynthesize)

`OSError` (e.g. `FileNotFoundError` when `agy` binary is missing) currently bubbles out of `synthesize()` and crashes the agent step instead of falling over to the next provider.

- [ ] **Step 1: Write the failing test**

Add this test to `class TestSynthesize` in `tests/test_synthesize.py` (after `test_agy_timeout_is_terminal_no_failover`):

```python
    @patch("agents.base.subprocess.run")
    @patch("time.sleep", return_value=None)
    def test_oserror_falls_back_to_next_provider(self, mock_sleep, mock_run):
        """OSError (e.g. binary not found) should trigger provider failover, not crash."""
        mock_run.side_effect = [
            OSError("No such file or directory: 'agy'"),
            mock_result(stdout="claude output"),
        ]
        agent = make_agent()

        result = agent.synthesize("test prompt")

        assert result == "claude output"
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0][0] == "agy"
        assert mock_run.call_args_list[1][0][0][0] == "claude"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_synthesize.py::TestSynthesize::test_oserror_falls_back_to_next_provider -v
```

Expected: FAILED — `OSError` propagates and the test raises instead of returning `"claude output"`

- [ ] **Step 3: Add OSError catch to `agents/base.py`**

In `synthesize()`, after the `except subprocess.TimeoutExpired:` block (around line 196–200), add:

```python
                except OSError as e:
                    print(f"[synthesize] {provider['name']} OS error: {e}", file=sys.stderr)
                    last_error = str(e)
                    break  # Try next provider — no side effects executed, safe to fall over
```

The full try/except block should read:

```python
                except subprocess.TimeoutExpired:
                    msg = f"{provider['name']} timed out after 600s"
                    print(f"[synthesize] {msg}", file=sys.stderr)
                    raise LLMTimeoutError(msg)
                except OSError as e:
                    print(f"[synthesize] {provider['name']} OS error: {e}", file=sys.stderr)
                    last_error = str(e)
                    break  # Try next provider — no side effects executed, safe to fall over
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_synthesize.py::TestSynthesize::test_oserror_falls_back_to_next_provider -v
```

Expected: PASSED

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
pytest tests/test_synthesize.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add agents/base.py tests/test_synthesize.py
git commit -m "fix: catch OSError in synthesize() to trigger provider failover on missing binary"
```

---

### Task 3: Fix librarian OUTPUT_PATTERNS (Findings 3 & 4)

**Files:**
- Modify: `agents/librarian.py` (lines 18–23)

Two issues: wrong glob extension for daily-briefing, and a dead `"librarian"` entry that refers to files never written.

- [ ] **Step 1: Update OUTPUT_PATTERNS in `agents/librarian.py`**

Replace lines 18–23:
```python
OUTPUT_PATTERNS = {
    "daily-briefing": "daily-briefing-*.md",
    "news-briefing": "daily-news-briefing-*.md",
    "security-audit": "security-audit-*.md",
    "librarian": "librarian-report-*.md",
}
```
With:
```python
OUTPUT_PATTERNS = {
    "daily-briefing": "daily-briefing-*.html",
    "news-briefing": "daily-news-briefing-*.md",
    "security-audit": "security-audit-*.md",
}
```

- [ ] **Step 2: Verify the change picks up the right files**

```bash
python3 -c "
from agents.librarian import OUTPUT_PATTERNS
from pathlib import Path
output_dir = Path('output')
for name, pattern in OUTPUT_PATTERNS.items():
    files = sorted(output_dir.glob(pattern), reverse=True)[:2]
    print(name, [f.name for f in files])
"
```

Expected: `daily-briefing` shows `.html` files, no `librarian` entry present

- [ ] **Step 3: Commit**

```bash
git add agents/librarian.py
git commit -m "fix: correct daily-briefing output glob to .html, remove dead librarian pattern"
```

---

### Task 4: Remove conflicting instruction from daily_briefing.md (Finding 5)

**Files:**
- Modify: `agents/prompts/daily_briefing.md`

Line 5 instructs the model to output "ONLY the markdown report" — a leftover from before the agent sent HTML. Lines 82 and 168 correctly require the HTML output. The conflict causes markdown content to be saved with `.html` extension.

- [ ] **Step 1: Remove the conflicting line from `agents/prompts/daily_briefing.md`**

Find and delete this exact line (line 5):
```
**CRITICAL:** Your final text output must be ONLY the markdown report. No preamble, no "Done", no summary. Just the report content.
```

The blank line following it (line 6) should also be removed to avoid a double blank line.

- [ ] **Step 2: Verify the file no longer contains the conflicting instruction**

```bash
grep -n "markdown report" agents/prompts/daily_briefing.md
```

Expected: no output

- [ ] **Step 3: Commit**

```bash
git add agents/prompts/daily_briefing.md
git commit -m "fix: remove conflicting markdown output instruction from daily_briefing prompt"
```

---

### Task 5: Fix security audit bash history remediation (Finding 6)

**Files:**
- Modify: `agents/security_audit.py` (~lines 569–578)

When secrets are found in bash history, `fix_commands=["chmod 600 .env"]` is emitted. This only addresses `.env` permissions, not the history leak. The fix builds `fix_commands` dynamically from what was actually found.

- [ ] **Step 1: Update the `_finding` call in `agents/security_audit.py`**

Find the block around line 569 that reads:
```python
        if issues:
            self._finding(
                severity="Critical" if "git history" in str(issues) else "High",
                severity="High",
                check="Secrets hygiene",
                detail="; ".join(issues),
                context="Leaked secrets in version control or shell history persist even after rotation",
                risk="Credential theft from git history or history file; secrets may already be compromised",
                impact="Rotating secrets requires updating all services that use them",
                fix_commands=["chmod 600 .env"] if env_file.exists() else None,
            )
```

Replace with:
```python
        if issues:
            fix_cmds = []
            if env_file.exists():
                fix_cmds.append("chmod 600 .env")
            if any("bash history" in issue for issue in issues):
                fix_cmds.append("history -c && history -w")
            self._finding(
                severity="Critical" if "git history" in str(issues) else "High",
                severity="High",
                check="Secrets hygiene",
                detail="; ".join(issues),
                context="Leaked secrets in version control or shell history persist even after rotation",
                risk="Credential theft from git history or history file; secrets may already be compromised",
                impact="Rotating secrets requires updating all services that use them",
                fix_commands=fix_cmds or None,
            )
```

- [ ] **Step 2: Verify the change looks correct**

```bash
grep -A 20 "if issues:" agents/security_audit.py | head -25
```

Expected: `fix_cmds` list building visible, `history -c && history -w` present

- [ ] **Step 3: Run the full test suite as a final sanity check**

```bash
pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add agents/security_audit.py
git commit -m "fix: emit history -c remediation when secrets found in bash history"
```
