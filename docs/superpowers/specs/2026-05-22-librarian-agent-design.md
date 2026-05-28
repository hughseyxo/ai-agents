# Librarian Agent — Design Spec

**Date:** 2026-05-22  
**Status:** Approved for implementation

---

## Problem

Agents fail silently and degrade quietly. The DB shows `news-briefing.news_briefing` failing 37% of runs and `daily-briefing.briefing` failing 36% — mostly Claude CLI timeouts and empty responses. There is no mechanism to detect these patterns, learn from them, or improve prompts over time without manual intervention. The librarian is that mechanism.

---

## Goals

- Detect reliability and quality regressions across all agents automatically
- Apply low-risk improvements (learnings files) autonomously
- Propose higher-risk prompt changes via email with one-click approve/reject
- Do all of this with minimal Claude Pro quota burn

---

## Architecture

### Two Run Modes, One Agent Class

`LibrarianAgent` in `agents/librarian.py` with `schedule = ""` (cron-managed externally):

| Mode | Cron | LLM calls | Purpose |
|------|------|-----------|---------|
| `audit` | `0 6 * * 0` (Sunday) | 1 analysis + 1 email | Full reliability + quality review |
| `watch` | `0 6 * * 1-6` (Mon–Sat) | 0 or 1 + 1 email | DB check only; LLM fires if 2+ consecutive failures in last 24h |

Mode is passed via CLI args: `run-agent.sh librarian --mode audit` / `--mode watch`.

Both modes use `model = "claude-haiku-4-5"`, Antigravity-first (default `PROVIDERS`) to protect Claude Pro quota.

---

## Steps

### Audit Mode

1. **`collect_data`** — Pure Python, no LLM:
   - Query DB: run counts, failure rates, step error messages per agent (last 30 days)
   - Read last 5 output files per agent from `output/` (daily briefings, news briefings, security reports)
   - Read current prompt templates from `agents/prompts/`
   - Read existing learnings files from `docs/agent-learnings/` (if any)
   - Build a single structured JSON summary

2. **`analyze`** — One LLM call (prompt: `librarian_audit.md`):
   - Input: the structured JSON summary (~3–5k tokens)
   - Output: JSON array of findings, each with:
     ```json
     {
       "agent": "news-briefing",
       "type": "reliability | quality",
       "description": "...",
       "confidence": 0.85,
       "fix_type": "learnings | prompt_edit | report_only",
       "suggested_fix": "...",
       "learnings_entry": "- Keep HTML under 50KB to avoid CLI timeout"
     }
     ```
   - Confidence thresholds:
     - `≥ 0.8` → apply as learnings file entry directly
     - `0.5–0.79` → save as proposal, include in email with approve/reject buttons
     - `< 0.5` → report only, no file changes

3. **`apply_learnings`** — For `confidence ≥ 0.8` findings:
   - Appends `learnings_entry` to `docs/agent-learnings/<agent>.md`
   - Creates file if it doesn't exist

4. **`propose_changes`** — For `0.5 ≤ confidence < 0.8` + `fix_type = prompt_edit`:
   - Saves proposal to `output/librarian/proposals/<uuid>.json`
   - Generates human-readable diff for email

5. **`send_report`** — `side_effects: True`:
   - Builds HTML email: summary stats → quality findings → reliability findings → learnings applied → proposals pending approval (with buttons)
   - Sends via Gmail MCP

### Watch Mode

1. **`check_failures`** — Pure Python:
   - Query DB for any agent with 2+ consecutive `error`/`partial_failure` runs in last 24h
   - Stores result in `context["check_failures"]`; subsequent steps check this and no-op if empty (BaseAgent has no native short-circuit — the no-ops are cheap dict lookups, no LLM is called)

2. **`analyze_failures`** — Small LLM call (prompt: `librarian_watch.md`):
   - Input: just the failing agent name + its recent error messages
   - Output: single finding JSON (same schema as above)

3. **`apply_learnings`** — Same as audit mode, confidence ≥ 0.8 only

4. **`alert`** — `side_effects: True`:
   - Brief HTML email: which agent failed, error pattern, what (if anything) was auto-fixed

---

## Data Model

### Proposal File

Path: `output/librarian/proposals/<uuid>.json`

```json
{
  "id": "abc123-...",
  "created_at": "2026-05-25T06:00:00Z",
  "agent": "news-briefing",
  "finding": "news_briefing prompt generates HTML too large for CLI; causes timeout",
  "fix_type": "prompt_edit",
  "file": "agents/prompts/news_briefing.md",
  "original": "... full original content ...",
  "proposed": "... full proposed content ...",
  "status": "pending | approved | rejected"
}
```

### Learnings Files

Path: `docs/agent-learnings/<agent-name>.md` (gitignored)

Free-form Markdown bullet list prepended to every `synthesize()` call for that agent. Example (`docs/agent-learnings/news-briefing.md`):
```markdown
- Keep HTML email body under 50KB to avoid CLI timeout
- NOS Dutch translation: always return a plain JSON array, no preamble or markdown fences
```

---

## Learnings Injection in BaseAgent

`agents/base.py` — `synthesize()` gains ~5 lines at the top:

```python
learnings_path = REPO_ROOT / "docs" / "agent-learnings" / f"{self.name}.md"
if learnings_path.exists():
    learnings = learnings_path.read_text().strip()
    if learnings:
        prompt = f"## Agent Learnings (apply these)\n{learnings}\n\n---\n\n{prompt}"
```

Non-breaking: only activates when a learnings file exists for that agent.

---

## Approve / Reject Flow

The bridge server (`mcp-servers/bridge_server.py`) gains two GET routes:

```
GET /librarian/approve?id=<uuid>&token=<BRIDGE_TOKEN>
GET /librarian/reject?id=<uuid>&token=<BRIDGE_TOKEN>
```

**On approve:**
1. Validate token (same `MCP_BRIDGE_TOKEN` as existing auth)
2. Read proposal JSON from `output/librarian/proposals/<id>.json`
3. Write `proposed` content to `file`
4. `git add <file> && git commit -m "librarian: apply proposal <id>"`
5. Set `status = "approved"`, return 200 with confirmation HTML page

**On reject:**
1. Validate token
2. Set `status = "rejected"` in proposal JSON
3. Return 200 with confirmation HTML page

Email buttons are HTML anchor tags pointing to the Tailscale hostname — accessible only from your own devices.

---

## Token Budget

| Run | Input tokens (est.) | Output tokens | Model | Quota |
|-----|---------------------|---------------|-------|-------|
| Weekly audit analysis | ~4k | ~1k | Haiku | Antigravity (free) |
| Weekly audit email send | ~3k | ~0.1k | Haiku | Antigravity (free) |
| Watch analysis (when fires) | ~800 | ~300 | Haiku | Antigravity (free) |
| Watch alert email | ~1k | ~0.1k | Haiku | Antigravity (free) |

Claude Pro quota only burns on Antigravity failure. Expected: near-zero Claude usage from this agent.

---

## Files

| File | Change |
|------|--------|
| `agents/librarian.py` | New — `LibrarianAgent` (audit + watch modes) |
| `agents/prompts/librarian_audit.md` | New — weekly analysis prompt |
| `agents/prompts/librarian_watch.md` | New — compact failure-analysis prompt |
| `agents/base.py` | Add learnings injection to `synthesize()` (~5 lines) |
| `mcp-servers/bridge_server.py` | Add approve/reject routes + `AGENT_REGISTRY` update |
| `tests/test_librarian.py` | New — data collection, confidence routing, proposal I/O, learnings injection |
| `CLAUDE.md` | Add librarian.py + prompts to Project Structure; note bridge routes |
| `docs/superpowers/specs/2026-05-22-librarian-agent-design.md` | This file |

---

## Verification

1. `pytest tests/test_librarian.py -v` — all tests pass
2. `python3 -m agents run librarian --mode watch` with no recent failures → exits with 0 LLM calls
3. Manually insert a fake failure run in the DB → watch mode fires LLM + sends alert
4. `python3 -m agents run librarian --mode audit` → produces report in `output/`, email delivered
5. Click approve link in email → verify file changed + git commit created
6. Click reject link → verify proposal JSON updated, no file change
