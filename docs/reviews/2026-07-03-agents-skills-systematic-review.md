---
type: review
status: active
date: 2026-07-03
title: Agents & Skills Systematic Review
tags: [review, architecture, security, efficiency]
---

# Agents & Skills Systematic Review — 2026-07-03

Whole-repo review across four lenses: functionality, efficiency, security, and what-I'd-do-differently. Evidence gathered notes-first ([[_map/index|codebase map]], [[CLAUDE]]) with targeted code reads. Severity tags: **High** = fix soon, **Med** = fix when touching the area, **Low** = cosmetic/opportunistic.

Related: [[2026-05-07-agent-architecture-design]], [[concierge-antigravity-primary]], [[code-review-remediation-plan]], [[Home]]

## System inventory

- **Agent framework** (`agents/`): 7 registered agents on `BaseAgent` (lifecycle, SQLite state, retry, Antigravity→Claude failover). Deterministic logic in Python, LLM only for synthesis/delivery. Solid core; `db.py` uses WAL + lock correctly.
- **Concierge Telegram bot** (`telegram-bot/`): agy-primary / claude-fallback chat, Opus photo assessment, tools defined once in `tool_specs.py` and executed in the concierge MCP server. Well-hardened input validation.
- **MCP servers** (`mcp-servers/`): concierge (stdio JSON-RPC, thin dispatch), gmail, calendar.
- **FloraPulse PWA** (`plant_ui/`): FastAPI + Alpine, plant CRUD, photo→Opus assessment, Claude-only chat with `--resume`.
- **Wedding PWA** (`wedding_ui/`): calculator-only, Dockerised, Traefik basic-auth. The cleanest subsystem — no LLM, tight Pydantic validation.
- **Skills** (`skills/`): mealsave (mature, own venv + bot), free-time (SKILL.md), vidqueue (dead husk).
- **Standalone bots**: `free_time_bot.py` (service present, not running), `mealsave_bot.py` (running).
- **Tests**: 26 files, good coverage of pure logic and failure paths (failover, concurrency, path traversal, atomic writes).

## Functionality findings

- **High — cron ownership split.** `plant-agent`, `agent-health`, and `librarian` crontab entries live *outside* the `# --- ai-agents managed ---` block. Both hourly agents declare `schedule` attrs, so the next `install-cron` run writes them *inside* the block too → **double hourly runs**. Root cause: `install-cron` can't express librarian's `--mode audit/watch` args, so entries were added by hand and never reconciled.
- **High — cross-process lost updates on plant state.** The whole plants list is one JSON blob under `state(agent='daily-briefing', key='plants')`, read-modify-written by three processes (bot, FloraPulse, hourly PlantAgent). WAL + busy_timeout prevent corruption, not lost updates: watering a plant in the PWA while an intelligence run is in flight silently reverts it. `data/agents.db.corrupt-bak` (2026-06-22) shows this area has already bitten once.
- **Med — `pending_plant_actions` lifecycle.** `PlantAgent._apply_intelligence_output` only overwrites the key when a run returns pruning items: stale care tasks persist indefinitely, and tasks completed in the PWA reappear on the next run that re-suggests them (no dedup against completed items).
- **Med — duplicated assessment pipeline.** ~200 lines copied between `telegram-bot/bot.py` and `plant_ui/server.py` (`_load_species_context`, `_extract_assessment_from_text`, `_build_assessment_display`, care-action formatting, JSON salvage). Already diverging: bot passes `plant_name` to `assess_image` for token-lean profile context; the PWA hand-builds its own truncated context.
- **Med — two data layers, one store.** `tools.py` mutates raw dicts; `plant_ui` uses Pydantic `PlantStore`/`Plant`. Dict path doesn't set `needs_photo`; schema drift is one field away. Plants also still live under the legacy `daily-briefing` namespace though PlantAgent owns them, and `plant_agent.py` re-implements slug logic inline instead of using `plant_profiles` helpers.
- **Low — dead code / drift.** `skills/vidqueue/` contains only `__pycache__`. Free-time logic exists three ways (skill, standalone bot with different duration heuristics, absent from concierge tools) and the bot service isn't running. `waterED_count` typo in the water-all API response. CLAUDE.md documents "Design Docs" and "Systems" dashboards that don't exist in `docs/_dashboards/`.

## Efficiency findings

- **High — LLM as email transport.** The plant status email builds its table deterministically in Python, then spends a full `synthesize()` call (600s budget, MCP handshake, failover chain) solely so an LLM can call `gmail_send`. News briefing does the same for its send step. A direct Gmail API call from Python is faster, cheaper, and removes a duplicate-send failure mode (the whole side-effects/timeout dance in `BaseAgent` exists mostly to protect this pattern).
- **Med — `free_time_bot.fetch_inbox_tasks`** spawns a full Claude CLI with *all* configured MCP servers to fetch inbox tasks that the Todoist REST API returns in one authenticated GET.
- **Med — intelligence run context bloat.** `_intelligence_run` inlines **full** profile markdown for every plant daily — contradicting the vault's own token-efficiency principle (bounded history, curated observations). Frequency-history tables grow monotonically, so this prompt only gets bigger.
- **Low — plant-add research** makes three sequential `agy` calls (frequency, sunlight, sensitivity) that could be one structured call.

## Security findings

- **High — Antigravity path is unsandboxed while handling untrusted input.** Every claude-CLI path is properly locked down (`--allowedTools`, `--strict-mcp-config`, `--disallowedTools Bash Write Edit`, untrusted-input labelling). The **primary** agy path has none of that: `--dangerously-skip-permissions`, global MCP config, shell access (prompt adaptation literally rewrites WebFetch → "shell tool with curl"). Untrusted content — RSS article text, Telegram messages, plant-profile notes — flows into prompts for an unrestricted shell-capable CLI on cron. The failover asymmetry means the *less* trusted engine gets the *more* dangerous capability set.
- **High — `free_time_bot` Claude call is over-privileged.** No `--strict-mcp-config`, no `--allowedTools`: it loads every MCP server in `.mcp.json` (Gmail send, Calendar write) with skip-permissions, and its prompt contains untrusted Todoist task titles. Anything that can create a task (email-to-Todoist, shared projects) can inject instructions into a tool-armed CLI. Service is currently stopped, which caps the exposure — fix before re-enabling.
- **Accepted risk (owner decision 2026-07-03) — FloraPulse unauthenticated on `0.0.0.0:8765`.** Owner confirms access is Tailscale-only; no auth planned. Residual note: the bind is `0.0.0.0` (not the Tailscale IP like CouchDB), so this holds only while the box has no other reachable interface.
- **Med — concierge bot auth fails open.** `if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID` — an unset/empty `TELEGRAM_USER_ID` disables the gate entirely (message, photo, and callback handlers). `free_time_bot` gets this right by crashing on a missing var.
- **What's done well** (worth keeping on the record): SSRF + argv-flag-smuggling validation in `tools.py`; path-traversal guards (`safe_profile_path`, `_safe_component`) with defence-in-depth checks; atomic profile writes; `install-cron` fails closed on crontab errors and sanitises schedule/name; upload magic-byte + size validation in the PWA; prompt-injection labelling of untrusted input; deterministic security-audit agent (18 checks) + pre-push commit scan; `.env` 600 and properly gitignored.

## What I'd do differently

1. **Deterministic delivery, LLM synthesis only.** Send email via Gmail API directly from Python; let the LLM produce *content*, never be the transport. This deletes the biggest timeout/duplicate-send risk and most of the 600s budgets.
2. **One plant data layer.** Make `PlantStore` the only reader/writer (used by tools.py, bot, PWA, agent), add per-plant update methods (update one record, not the whole blob) to shrink the lost-update window, and migrate the state key to `agent="plant-agent"`.
3. **One assessment module.** Extract the shared pipeline into `agents/plant_assessment.py`; bot and PWA become thin adapters.
4. **One cron owner.** Teach `install-cron` per-agent args (e.g. `cron_variants` class attr for librarian's modes), move all entries into the managed block, and have `agent-health` alert on unmanaged `run-agent.sh` entries.
5. **Symmetric sandboxing.** Give the agy path a tool allowlist if it supports one; where it doesn't, route untrusted-input agents Claude-first (news_briefing already does this — extend the pattern) and reserve agy for trusted-input synthesis.
6. **Match the network convention.** Bind FloraPulse to the Tailscale IP like CouchDB, or put it behind the same Traefik basic-auth pattern as the wedding PWA.
7. **Fail closed on auth.** Concierge bot should refuse to start without `TELEGRAM_USER_ID`.
8. **Prune aggressively.** Delete `skills/vidqueue/`; collapse free-time to a single implementation (concierge tool + thin skill); fix or remove the stale `pending_plant_actions` flow.

## Prioritised recommendations

| # | Fix | Lens | Severity | Effort |
|---|-----|------|----------|--------|
| 1 | Allowlist/re-route untrusted-input LLM calls (agy path, free_time_bot) | Security | High | S–M |
| 2 | Fail-closed Telegram auth (FloraPulse auth: accepted risk, dropped) | Security | Med | S |
| 3 | Reconcile crontab into managed block; extend install-cron for args | Functionality | High | S |
| 4 | Direct Gmail send from Python (plant email, news send step) | Efficiency | High | M |
| 5 | Single PlantStore layer + per-plant updates + namespace migration | Functionality | High | M–L |
| 6 | Shared plant_assessment module | Functionality | Med | M |
| 7 | Care-task lifecycle (clear/dedup pending_plant_actions) | Functionality | Med | S |
| 8 | Bounded intelligence-run context (frontmatter + curated sections only) | Efficiency | Med | S–M |
| 9 | Delete vidqueue; consolidate free-time; fix waterED_count; update CLAUDE.md dashboards/skills list | Hygiene | Low | S |

## Knowledge gaps written back to the vault

- `docs/_map/skills.md` — vidqueue husk, free-time triplication.
- `docs/_map/scripts-and-root.md` — cron entries outside the managed block; free-time-bot service stopped.
- CLAUDE.md drift (missing Systems/Design Docs dashboards, skills list) flagged here rather than silently edited — see recommendation 9.
