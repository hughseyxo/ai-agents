# Design: Address Librarian Audit Findings — 2026-05-28

## Problem

Six findings from the librarian audit run after adding plant-agent to the librarian's watch list and fixing `run-agent.sh plant-agent` hanging. Two are direct consequences of the `synthesize()` stdin refactor; the rest are pre-existing issues.

---

## Finding 1 — [RELIABILITY] tests/test_synthesize.py broken (95%)

**Root cause:** Tests extract the prompt via `cmd.index("-p") + 1`, but `synthesize()` now passes the prompt via `input=p_prompt` (stdin). `-p` is no longer in `cmd`.

**Fix — `tests/test_synthesize.py`:**
- 4 affected tests: `test_agy_prompt_is_adapted_by_default`, `test_claude_prompt_is_not_adapted_on_fallback`, `test_learnings_file_prepended_to_prompt`, `test_no_learnings_file_leaves_prompt_unchanged`
- Replace `cmd[cmd.index("-p") + 1]` with `mock_run.call_args[1]["input"]`
- For call-indexed assertions use `mock_run.call_args_list[N][1]["input"]`
- Add: `test_oserror_falls_back_to_next_provider` — `OSError` on agy, success on claude

---

## Finding 2 — [RELIABILITY] OSError not caught in synthesize() (95%)

**Root cause:** Only `subprocess.TimeoutExpired` is caught. `OSError` (e.g. `FileNotFoundError` when a CLI binary is missing) bubbles up and crashes the step rather than triggering provider failover.

**Fix — `agents/base.py`, in `synthesize()` after the `TimeoutExpired` except:**
```python
except OSError as e:
    print(f"[synthesize] {provider['name']} OS error: {e}", file=sys.stderr)
    last_error = str(e)
    break  # Try next provider — no side effects executed, safe to fall over
```

---

## Finding 3 — [LOGIC] librarian OUTPUT_PATTERNS wrong extension (95%)

**Root cause:** `OUTPUT_PATTERNS["daily-briefing"] = "daily-briefing-*.md"` but `daily_briefing.py:59` writes `.html`. Librarian reads stale old files.

**Fix — `agents/librarian.py`:**
```python
"daily-briefing": "daily-briefing-*.html",
```

---

## Finding 4 — [QUALITY] Dead code in OUTPUT_PATTERNS (90%)

**Root cause:** `OUTPUT_PATTERNS["librarian"] = "librarian-report-*.md"` — librarian sends reports by email and never writes to `output/`.

**Fix — `agents/librarian.py`:** Remove the `"librarian"` entry from `OUTPUT_PATTERNS`.

---

## Finding 5 — [QUALITY] Conflicting instructions in daily_briefing.md (90%)

**Root cause:** Line 5 says output "ONLY the markdown report" — a leftover from before the agent sent HTML email. Lines 82 and 168 correctly require HTML output. The contradiction causes markdown to be saved with `.html` extension.

**Fix — `agents/prompts/daily_briefing.md`:** Remove the line:
```
**CRITICAL:** Your final text output must be ONLY the markdown report. No preamble, no "Done", no summary. Just the report content.
```

---

## Finding 6 — [LOGIC] Security audit wrong remediation for bash history leaks (90%)

**Root cause:** `fix_commands=["chmod 600 .env"]` is emitted when secrets are found in bash history, but `chmod 600 .env` does nothing to clear history.

**Fix — `agents/security_audit.py`:**
```python
fix_cmds = []
if env_file.exists():
    fix_cmds.append("chmod 600 .env")
if any("bash history" in issue for issue in issues):
    fix_cmds.append("history -c && history -w")
self._finding(..., fix_commands=fix_cmds or None)
```

---

## Files Modified

| File | Findings |
|---|---|
| `tests/test_synthesize.py` | 1 |
| `agents/base.py` | 2 |
| `agents/librarian.py` | 3, 4 |
| `agents/prompts/daily_briefing.md` | 5 |
| `agents/security_audit.py` | 6 |

## Verification

```bash
pytest tests/test_synthesize.py -v
python3 -c "from agents.librarian import OUTPUT_PATTERNS; print(OUTPUT_PATTERNS)"
bash run-agent.sh plant-agent 2>&1
```
