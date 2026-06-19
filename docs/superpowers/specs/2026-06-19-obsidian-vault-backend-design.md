# Obsidian Vault Backend — Design Spec

**Date:** 2026-06-19
**Status:** Approved design → pending implementation plan
**Author:** Eagna (Claude Code)

## Problem

The project's knowledge lives as plain markdown the agents read/write directly:
`docs/` (design docs, plant profiles, librarian learnings), `docs/librarian-memory.md`,
and the Claude auto-memory folder (`~/.claude/projects/-home-cian-git-ai-agents/memory/`).
This works as storage but fails as *intelligence*:

- **Not navigable** — no backlinks/graph; the `[[wikilinks]]` already used in memory frontmatter are invisible.
- **Not queryable** — no frontmatter, so no Dataview views over plant health or librarian findings.
- **Token-bloated** — append-only growth (e.g. `monstera-deliciosa.md` re-narrates the full canopy every assessment). These notes get fed back into prompts (plant profile → photo-assessment context; learnings → librarian audit), so bloat = real recurring token cost.
- **No cross-session memory** — nothing captures per-session working context for the next session.
- **No cross-device access** — notes are server-only; no phone/PC view.

## Goals

1. Host an Obsidian vault **on this server, reachable only over Tailscale**, with **native** Obsidian on phone + PC (no headless Obsidian).
2. Restructure plant + librarian intelligence to be **token-efficient and easy for Claude to parse** — the primary success metric.
3. Curated, current notes: keep *relevant* history, drop noise.
4. Plant profiles provide **context to photo-based health assessments**; the plant agent may **web-research** species as needed (cited).
5. Per-session **daily notes** with a parse-friendly index for cross-session context.
6. Weave the agent's identity — `CLAUDE.md`, personality, project memory — into the vault graph.

## Non-Goals

- Moving operational state into markdown. **SQLite (`data/agents.db`) stays canonical** for plant data; the FloraPulse PWA stays the task source of truth. Markdown is the *intelligence/narrative* layer only.
- Running Obsidian (or its plugins) server-side. Dataview renders on the device clients.

## Guiding Principle — token-efficient, easy to parse

Every structure optimizes for cheap parsing + minimal context tokens:

1. **Frontmatter projection** — status as machine-readable key-values; readable without parsing prose.
2. **Curated "current state" sections** — facts stated once, never re-narrated.
3. **`status:` field** (`active`/`resolved`/`superseded`) — consumers load only `active`, skip the rest.
4. **Bounded recent history + compact rollup** — relevant past kept, routine noise dropped.
5. **Atomic notes + `## Index`** — load only the unit needed.

## Architecture — sync stack

```
 Phone (native Obsidian + Self-hosted LiveSync plugin) ─┐
 PC    (native Obsidian + Self-hosted LiveSync plugin) ─┤
                                                        ▼
                       CouchDB  (Docker, bound to Tailscale IP only)
                                                        ▲
                       livesync-bridge (Docker, Deno)  ─┘
                          mirrors on-disk vault paths ⇄ CouchDB
                                                        ▲
        agents read/write plain .md ──────────────────┘  (filesystem unchanged)
```

- **CouchDB** (Docker) — the sync hub. Published **bound to the Tailscale IP only**
  (`-p 100.96.86.73:5984:5984`), never `0.0.0.0`. Apply Self-hosted LiveSync's documented
  CouchDB config (CORS, `single_node`, `require_valid_user`, `max_http_request_size`) — exact
  values per LiveSync upstream docs at implementation time. Admin password in gitignored `.env`.
- **livesync-bridge** (vrtmrz/livesync-bridge, Docker) — bridges on-disk vault folders ⇄ CouchDB
  so agents keep writing plain files while devices run native Obsidian. Config (`dat/config.json`)
  maps storage paths → one CouchDB database; exact schema per upstream docs. Ignores
  `.obsidian/`, `.trash/`, `__pycache__/`.
- **Devices** — native Obsidian + Self-hosted LiveSync plugin → `https://100.96.86.73:5984` over Tailscale.
- **Service** — added as two services to the **existing yopflix seedbox Docker stack**
  (`~/git/yopflix/seedbox/docker-compose.yaml`, private repo), started by its `run-seedbox.sh`.
  No standalone stack and no new systemd unit; they come up with the rest of the seedbox.

## Vault layout — one vault, multiple on-disk roots

livesync-bridge maps three storage entries into one CouchDB database → one vault on devices:

| On-disk path | Vault path | Notes |
|---|---|---|
| `~/git/ai-agents/docs/` | `/` | design docs, plants, learnings, dashboards, daily |
| `~/.claude/.../memory/` | `/_memory/` | auto-memory (already has frontmatter + `[[links]]`) |
| `~/git/ai-agents/CLAUDE.md` (+ `.antigravity.md`) | `/_project/` | filtered: repo-root markdown only, no code |

## Data model

### Plant profile (`docs/plants/<slug>.md`) — curated, one note per plant

```markdown
---
type: plant
location: indoor
sunlight: partial-shade
water_sensitivity: medium
baseline_frequency_days: 10
effective_frequency_days: 10
last_watered: 2026-06-08
needs_photo: false
latest_health: healthy
latest_assessment: 2026-06-18
tags: [plant, indoor, sensitivity/medium]
---
# Monstera Deliciosa

## Current Observations        <!-- LIVING: intelligence_run REWRITES; current/relevant only -->
- Repot overdue (flagged 2026-05-28) — slightly larger pot, well-draining mix.
- Leans toward window; rotate periodically, consider moss pole.
- Vigorous, fenestrated, firm turgor; 10-day cadence suits it.

## Health Assessments          <!-- recent ~2-3 as DELTAS, not full re-descriptions -->
### 2026-06-18 — Healthy
- No change vs 2026-06-06; repot still outstanding.

## History                     <!-- one-line rollups of older significant events -->
- 2026-05: repot first flagged.

## Care Research               <!-- web-researched, CITED, curated -->
- Tolerates 7–14 day cadence indoors; aerial roots normal (source: …).

## Frequency History           <!-- compact table (unchanged) -->
| Date | Change | Reason |
|---|---|---|
```

- **Frontmatter is a projection from SQLite** — regenerated by `intelligence_run`; **do not hand-edit**.
- **Curation:** `intelligence_run` rewrites `## Current Observations`, prunes redundant assessments
  (keep relevant history, drop routine "still healthy, no change"), rolls older events into `## History`.
- **Assessment-context loop:** the photo-assessment path injects the profile (frontmatter + current
  observations + recent assessments) into the vision prompt, so Opus assesses *with history*.
- **Web research:** `intelligence_run` may web-search a species when it hits a knowledge gap; durable
  cited findings go to `## Care Research`. Uses the LLM CLI's web tool (Antigravity built-in /
  Claude `WebSearch`) — same path as add-time `water_sensitivity` research; no new Python plumbing.

### Librarian learnings — atomic notes (`docs/agent-learnings/<agent>/<date>-<slug>.md`)

```markdown
---
type: learning
agent: news-briefing
confidence: 0.9
status: active            # active | superseded | resolved
date: 2026-06-19
tags: [learning, news-briefing]
related: ["[[security-audit]]"]
---
Output samples are truncated to 2000 chars by `_collect_data`; don't report
truncation as a defect unless verified in source. See [[librarian]].
```

- `librarian-memory.md` → atomic notes `docs/librarian-memory/<slug>.md`, same frontmatter shape (`type: memory`).
- **Curation:** audit/watch runs mark stale learnings `status: superseded` rather than letting them pile up.
- **Token win:** `_collect_data` loads only `status: active` bodies (filters on frontmatter), shrinking the librarian's own audit prompt.

### Daily session note (`docs/daily/YYYY-MM-DD.md`) — one per Claude Code session day

```markdown
---
type: daily
date: 2026-06-19
sessions: 1
topics: [obsidian-migration]
files_touched: [docs/superpowers/specs/2026-06-19-obsidian-vault-backend-design.md]
decisions: [chose-livesync-bridge, full-graph-atomic, token-efficiency-principle]
open_threads: [implement-vault]
tags: [daily]
---
# 2026-06-19

## Index                       <!-- parse-friendly: scan this first, drill in as needed -->
- [[#Session 1]] — Obsidian vault backend design
- Decisions: LiveSync+bridge; token-efficient curated notes
- Open: write implementation plan

## Session 1
- Designed Obsidian second-brain backend… [[obsidian-vault-backend-migration]]
```

- **Trigger (automatic):** SessionStart hook ensures today's note exists; Stop hook appends the
  session summary. Scoped to **Claude Code sessions only** (Eagna), not the hourly agents.
- Complements the memory folder: daily = working context, memory = durable facts.

### Agent identity in the vault

- `CLAUDE.md` mapped into the vault (`/_project/`) as the project's source-of-truth node.
- **Eagna Home MOC** (`docs/_dashboards/Home.md`) — links CLAUDE.md, personality (`[[feedback_personality]]`),
  project memories, recent daily notes, and the plant/librarian dashboards. The agent's "front page."
- Personality stays defined by the existing memory `feedback_personality.md` (blunt, dry, disagree
  when warranted, no sycophancy) — now a first-class linked graph node, not a buried file.
- Memory files already carry `metadata.type` frontmatter → Dataview-queryable as-is.

### Dashboards (Dataview, render on device)

- `docs/_dashboards/Plant Health.md` — table (name, location, last_watered, effective_frequency, needs_photo, health) + "needs attention" view (`needs_photo or health != healthy`).
- `docs/_dashboards/Librarian Intelligence.md` — `status: active` learnings by agent/confidence; recent findings.
- `docs/_dashboards/Memory.md` — memories grouped by `metadata.type`.
- `docs/_dashboards/Home.md` — the Eagna MOC (above), incl. recent daily notes + open threads.

## Component / file changes

**Infra (in the yopflix seedbox stack, `~/git/yopflix/seedbox/docker-compose.yaml`):** add `couchdb` + `livesync-bridge` services, `services/couchdb/local.ini`, `services/livesync-bridge/config.json`, seedbox env creds. No standalone stack or systemd unit.
**New (notes):** `docs/_dashboards/*`, `docs/daily/`, per-folder `_index.md` MOCs.
**Modified (plants):**
- `agents/plant_profiles.py` — add `upsert_frontmatter()`, `rewrite_section()`, `read_profile_context()`; existing `## `-section helpers already preserve frontmatter (verified).
- `agents/plant_agent.py` `intelligence_run` — regenerate frontmatter from SQLite; curate/prune; optional web research.
- `agents/prompts/plant_intelligence.md` — curation + pruning + `[[links]]`/`#tags` + research instructions.
- `agents/prompts/plant_photo_assessment.md` — add `{{PROFILE_CONTEXT}}`.
- `telegram-bot/claude_backend.py` `assess_image` + `plant_ui/server.py` assessment endpoint + concierge `save_plant_assessment` — read profile, pass as context; refresh `latest_health`/`latest_assessment` frontmatter.
**Modified (librarian):**
- `agents/librarian.py` — `_collect_data` recursive glob + `status: active` filter; `_apply_learnings` writes atomic notes; memory atomic.
- `agents/prompts/librarian_audit.md`, `librarian_watch.md` — emit slug/tags/related/status JSON.
**Modified (project):** `.gitignore` (`docs/.obsidian/`, bridge/CouchDB data), `CLAUDE.md` (document the system).
**Hooks:** SessionStart + Stop hooks for daily notes.

## Security

- CouchDB **never** on `0.0.0.0` — Tailscale IP bind only; strong admin password in gitignored `.env`.
- Memory folder (personal facts) syncs only over private Tailscale + authenticated CouchDB.
- Consistent with memory `librarian-memory`: lock network surfaces tightly; no broad exposure.

## Migration

1. Stand up CouchDB + bridge (Tailscale-only); verify device round-trip.
2. One-time backfill script: inject frontmatter into the 10 existing plant profiles (from SQLite);
   curate each into the new section layout; convert flat `agent-learnings/*.md` → atomic notes.
3. Add dashboards, MOCs, daily-note hooks.
4. Update `CLAUDE.md`.

## Verification

- `pytest tests/test_plant_profiles.py` (frontmatter upsert + `rewrite_section` preserve `## ` sections) and librarian atomic-write tests — **tests first** (TDD, per memory `feedback_tdd`).
- CouchDB reachable over Tailscale; **confirm `0.0.0.0` is NOT bound** (`ss -tlnp | grep 5984`).
- Round-trip: edit a note on phone → lands on disk via bridge → agent reads it; agent writes → appears on phone.
- PWA still renders plant profiles with frontmatter present.
- Photo assessment includes profile context (inspect the assembled prompt).
- Daily note auto-created on session start, summarized on stop.

## Open questions / future

- Exact livesync-bridge `config.json` schema + CouchDB tuning values — pin against upstream docs during implementation.
- Whether Care Research warrants its own atomic notes later if it grows (defer — YAGNI).
