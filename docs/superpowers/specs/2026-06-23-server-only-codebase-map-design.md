# Server-only Obsidian codebase map — design

**Date:** 2026-06-23
**Status:** Approved, implementing on branch `feat/server-only-codebase-map`
**Related:** `docs/superpowers/specs/2026-06-19-obsidian-vault-backend-design.md`

## Problem

`CLAUDE.md` is ~214 lines / ~27 KB (~6,800 tokens) and is injected into context at the
**start of every session**. Roughly half of it — the `# Project Structure` file tree plus
per-module descriptions (lines ~43–145, ~9 KB / ~2,300 tokens) — is reference material the
agent rarely needs in full on a given turn, yet it costs tokens every session.

Two goals:
1. **Token reduction** — stop injecting the detailed map every session.
2. **Security** — the git remote is **public** (`github.com/hughseyxo/ai-agents`); the detailed
   internal architecture map should not be exposed there.

## Design decisions

- **Scope:** move *only* the Project Structure tree + per-module descriptions. All rules,
  conventions, and subsystem prose (concierge bot, FloraPulse, plant tracker, MCP) stay in CLAUDE.md.
- **Home:** a new **server-only, gitignored** Obsidian folder `docs/_map/`. The livesync-bridge
  already syncs `docs/` to the Tailscale-only CouchDB **independently of git** (~30s), so a
  gitignored file under `docs/` is browsable in Obsidian + present on disk for both CLIs, but never
  reaches the public remote. Established pattern: `docs/agent-notes.md` (gitignored, server-synced).
- **Structure:** MOC + per-area atomic notes — best Obsidian graph value and best token locality
  (an agent reads only the subsystem it needs).
- **Maintenance:** script-scaffolded skeleton, prose by hand. A generator regenerates the file
  lists; descriptions are hand-written and preserved across regenerations.

## Architecture

### `docs/_map/` (gitignored)
- `index.md` — MOC. A managed `MAP:FILES` block of `[[area]]` wikilinks; prose outside preserved.
- Per-area notes: `agents.md`, `telegram-bot.md`, `plant-ui.md`, `mcp-servers.md`, `tests.md`,
  `skills.md`, `scripts-and-root.md`.
- Each area note holds a **script-managed block** between markers:

  ```
  <!-- MAP:FILES:START -->
  - `agents/base.py` — BaseAgent class — lifecycle, retry, state, LLM failover.
  <!-- MAP:FILES:END -->
  ```

  Rows are `` - `relpath` — description ``. New files get `_TODO: describe_`. Any prose
  **outside** the markers is left untouched.

### `scripts/gen_codebase_map.py`
- Config `AREAS: dict[str, list[str]]` mapping area → repo-relative globs:
  - `agents` → `agents/*.py`, `agents/prompts/*.md`
  - `telegram-bot` → `telegram-bot/*.py`
  - `plant-ui` → `plant_ui/**/*`
  - `mcp-servers` → `mcp-servers/*.py`
  - `tests` → `tests/*.py`
  - `skills` → `skills/**/*`
  - `scripts-and-root` → `scripts/*`, `triggers/*`, `*.sh`, `*.py`, `*.service`
- Per area: discover current files (files only; skip dotfiles + `__pycache__`), parse existing
  descriptions from the managed block, rebuild the block (keep existing description, else TODO,
  drop vanished files), splice it back preserving outside prose. Print added/removed counts.
- Creates `docs/_map/` + notes on first run. Idempotent: a second run with no tree change is a no-op.

### CLAUDE.md
The `# Project Structure` tree block is replaced with a short pointer to `docs/_map/index.md`
(server-only, gitignored, regenerate with the script). Everything else stays.

## Data model

Managed block markers: `<!-- MAP:FILES:START -->` / `<!-- MAP:FILES:END -->`.
Row regex: `^- \`(?P<path>[^\`]+)\` — (?P<desc>.*)$`. Placeholder: `_TODO: describe_`.

## File list

- `docs/superpowers/specs/2026-06-23-server-only-codebase-map-design.md` (this doc)
- `scripts/gen_codebase_map.py` (new)
- `tests/test_gen_codebase_map.py` (new)
- `docs/_map/*` (new, gitignored — not committed)
- `.gitignore` (add `docs/_map/`)
- `CLAUDE.md` (slim Project Structure → pointer)

## Verification

- `.venv/bin/pytest tests/test_gen_codebase_map.py` green.
- Generator idempotent (second run = no diff).
- `git status --ignored` shows `docs/_map/` ignored; not staged.
- `wc -l CLAUDE.md` drops ~100 lines.
- `docs/_map/index.md` appears in Obsidian after ~30s (manual).

## Risks

- **Drift:** generator catches file add/remove; prose hand-updated (accepted).
- **Antigravity parity:** CLAUDE.md pointer covers agy; files on disk for both CLIs despite gitignore.
- **CouchDB deletion lag:** on-disk deletions reconcile only on bridge restart (known limit) — fine
  for a mostly-additive map.
