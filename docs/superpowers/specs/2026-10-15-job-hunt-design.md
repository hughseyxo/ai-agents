# Job Hunt — CV-matched LinkedIn tracker + Gmail application pipeline — Design

**Date:** 2026-10-15
**Status:** Implemented 2026-10-15 (Drive access revised — see Revision 1)
**Author:** eagna (Claude Code)

## Related
- [[Systems]] · [[Design Docs]]
- Supersedes the unshipped `job-alert` agent (fintech/pharma briefing section) — its LinkedIn parser/sanitiser is salvaged into `agents/linkedin_jobs.py`; the rest is deleted.
- Sibling skeleton: Wedding Budget PWA (`wedding_ui/`) — [[2026-06-27-wedding-budget-pwa-design]].

## Problem

Job hunting. Seed interests are SRE / infrastructure / cloud, but the user is **explicitly open to any title or
industry that is a good fit for their CV** — so nothing about roles or sectors is hard-coded. Wanted:

1. Check LinkedIn daily for new Netherlands postings (remote + hybrid).
2. Rank them against the current CV in Google Drive and recommend the good fits.
3. Track applications, rejections and interviews from Gmail, plus manual entries.
4. A web app to browse matches and the application pipeline.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Existing `job-alert` agent | Replace | Its fintech/pharma hard filter and briefing-section delivery don't fit; parser is reusable |
| Role/industry filter | None hard-coded | LLM derives LinkedIn search queries from the CV; fit score does the filtering |
| Location | NL, remote + hybrid (`f_WT=2`, `f_WT=3`) | User preference |
| Architecture | Host cron agent + thin web app | LLM calls and Google/Claude creds stay on the host; container only needs the DB |
| Hosting | Docker, bound to `<TAILSCALE_IP>:8774`, Tailscale-only | Private data (CV, inbox-derived pipeline); no public hostname, so no login |
| CV source | Newest `Cian_Hughes_CV_General*` in Drive | Auto-tracks CV revisions; re-scores open jobs when it changes |
| Drive access | claude.ai Google Drive connector via headless `claude -p` (Haiku), `--allowedTools` = the 2 read tools + ToolSearch, no `--dangerously-skip-permissions` | Already authorised on the user's claude.ai account; a custom OAuth client asking for `drive.readonly` was blocked by Google ("This app's request is invalid"). Metadata + text are parsed from raw tool results in stream-json, not the model's reply (Revision 1) |
| Gmail access | Python via existing `gmail_client` token | Already has `gmail.modify`; no new consent |
| Applications | Auto from Gmail + manual "Mark applied" | Not every application sends a confirmation email |
| Gmail backfill | 90 days on first run, then incremental (`gmail_seen`) | |
| Notifications | Telegram ping only for new jobs scoring ≥ 75 | Quiet by default — no "0 new jobs" pings |
| Gmail labels | Not applied | YAGNI |

## Architecture

```
job-hunt agent (host cron, 04:00 UTC)                 jobs_ui (Docker, <TAILSCALE_IP>:8774)
 1 cv:      Drive connector (claude -p) → text         FastAPI + Alpine PWA, no LLM
            changed? → LLM derives search queries      reads/writes data/jobs/jobs.db via JobStore
 2 fetch:   LinkedIn guest API per query × WT × page   tabs: Matches | Applications | Search/CV
 3 triage:  Haiku batch on title+company → drop junk
 4 score:   fetch description → Sonnet batch vs CV
 5 gmail:   search threads → Haiku classify → upsert applications/events
 6 notify:  Telegram if new score ≥ 75
            all state → data/jobs/jobs.db (JobStore)
```

### Steps (`JobHuntAgent`)

1. **cv** — `drive_reader.find_latest_cv()` picks the most recently modified `Cian_Hughes_CV_General*` (Google Doc
   or `.docx`; PDF skipped). Text is hashed; an unchanged hash reuses the last `cv_snapshots` row. On change: insert
   a snapshot, and one LLM call returns 8–12 LinkedIn search queries (mix of titles and skill terms). Each is
   validated in Python (≤ 40 chars, `[\w .+/#-]` only) and replaces the `source='cv'` rows; `source='manual'` rows
   (added in the UI) are kept.
2. **fetch** — enabled queries × `f_WT` {2, 3} × 2 pages against the guest search endpoint, `f_TPR=r172800`,
   2 s sleep between requests. A 429 raises `RateLimitedError`, aborts the step, and marks the run partial.
   Already-known `job_id`s are skipped.
3. **triage** — one batched Haiku call over title + company of new jobs → keep/drop. Cheap pre-filter so only
   plausible jobs cost a description fetch and a Sonnet scoring call.
4. **score** — for kept jobs (capped at 40 per run), fetch the description from
   `jobs-guest/jobs/api/jobPosting/{id}` (HTML → text, ~6k char cap). Batched Sonnet calls against the CV text
   return `{job_id, score, matched_skills, gaps, rationale}`. If the CV changed this run, open jobs from the last
   14 days are re-scored too.
5. **gmail** — `search_threads` with a job-related query (application/interview/unfortunately subjects, ATS sender
   domains, `jobs-noreply@linkedin.com`), `newer_than:90d`, skipping message ids in `gmail_seen`. Batched Haiku call
   classifies each into `applied | interview | rejected | offer | other` with company and role. Match an existing
   application by normalised company + fuzzy role (`difflib`); otherwise create one. Every classified email adds an
   `application_events` row with the thread id. A matched application whose job is in `jobs` flips that job to
   `status='applied'`.
6. **notify** — Telegram (`agents/telegram_client.send_telegram`) listing new jobs with score ≥ 75. Built in Python
   from validated fields.

`--dry-run` (existing `runner.py` flag): runs everything and writes a markdown report to `output/`, but skips the
Telegram ping and doesn't mark Gmail messages seen.

### Security

LinkedIn descriptions and email bodies are third-party text, so `untrusted_input = True`: Claude-only provider,
`mcp_config = None` (zero MCP servers), no `allowed_tools` — the same confinement as `news_briefing`. Every LLM
output field is re-validated in Python: score clamped to 0–100, status coerced to the enum, strings sanitised
(`sanitize_title`) and length-capped, unknown job ids ignored. No untrusted text reaches an MCP-armed prompt. The UI
renders all text with Alpine `x-text`, never `x-html`. Residual: `Read`/`Glob`/`Grep` remain available to the CLI,
as documented in CLAUDE.md for other untrusted-input agents; output never leaves the user's own DB/Telegram.

## Data model (`data/jobs/jobs.db`, `agents/job_store.py` → `JobStore`)

| Table | Columns |
|---|---|
| `cv_snapshots` | `id, file_id, name, modified_time, text_hash, text, created_at` |
| `search_queries` | `id, query, source ('cv'\|'manual'), enabled, created_at` |
| `jobs` | `job_id PK, title, company, location, url, posted, description, triage ('keep'\|'drop'), score, matched_skills JSON, gaps JSON, rationale, cv_snapshot_id, status ('new'\|'seen'\|'dismissed'\|'applied'), first_seen, scored_at` |
| `applications` | `id, company, role, job_id NULL, status ('applied'\|'interview'\|'rejected'\|'offer'\|'withdrawn'), source ('gmail'\|'manual'), notes, created_at, updated_at` |
| `application_events` | `id, application_id, status, gmail_thread_id, subject, occurred_at` |
| `gmail_seen` | `message_id PK` |

`JobStore` is shared by the agent (writer of jobs/snapshots/gmail data) and the UI (writes job status,
manual applications, notes, manual queries). Independent of `agents.db`.

## Web app (`jobs_ui/`)

Fork of the `wedding_ui/` skeleton (FastAPI + Alpine PWA, SQLite, no LLM).

- **Matches** — jobs sorted by score; score badge, matched skills, gaps, rationale, link to LinkedIn; filter by
  min score / status; actions: dismiss, mark applied (creates a manual application linked to the job).
- **Applications** — pipeline grouped by status; event timeline per application; status override + notes; add
  manual application.
- **Search/CV** — current CV snapshot (file name, modified time), CV-derived queries (read-only), manual queries
  (add/remove/toggle).

API: `GET /api/jobs?status=&min_score=`, `PATCH /api/jobs/{id}`, `GET /api/applications`,
`POST /api/applications`, `PATCH /api/applications/{id}`, `GET/POST/DELETE /api/queries`, `GET /api/cv`,
`GET /healthz`.

Hosting: seedbox `services/jobs.yaml` + `config.yaml` entry (separate `~/git/yopflix/seedbox` repo), port bound to
`<TAILSCALE_IP>:8774`, `traefik.enabled: false`, `data/jobs/jobs.db` bind-mounted. Rebuild the image before
`./run-seedbox.sh` (it doesn't rebuild existing images).

## Files

**New**
- `agents/linkedin_jobs.py` — salvaged parser/sanitisers/429 handling + `fetch_search`, `fetch_description`
- `agents/drive_reader.py` — `fetch_latest_cv()`: headless `claude -p` + claude.ai Drive connector; `parse_stream()` takes file metadata/text from raw tool results and verifies the newest CV was read
- `agents/job_store.py` — `JobStore`
- `agents/job_hunt.py` — `JobHuntAgent`
- `jobs_ui/` — `server.py`, `templates/index.html`, `static/*`, `Dockerfile`
- Tests: `tests/test_linkedin_jobs.py`, `tests/test_drive_reader.py`, `tests/test_job_store.py`, `tests/test_job_hunt.py`, `tests/test_jobs_ui_api.py`

**Modified**
- `agents/gmail_client.py` — `search_threads`, `get_thread`
- `agents/daily_briefing.py`, `agents/prompts/daily_briefing.md`, `tests/test_daily_briefing.py` — job-alert section removed (calendar-block work kept)
- `CLAUDE.md` — Job Hunt section

**Deleted**
- `agents/job_alert.py`, `tests/test_job_alert.py`, `docs/superpowers/specs/2026-08-06-job-alert-design.md`

## Risks
- LinkedIn guest API is unofficial — markup changes or rate limits. Parser tests catch breakage; 429 aborts cleanly.
- Gmail classification is heuristic — UI status override is the correction path.
- CV naming convention: a CV not prefixed `Cian_Hughes_CV_General` won't be picked up.

## Revision 1 (2026-10-15) — Drive via the claude.ai connector

The original design read the CV through a custom Google OAuth client with a new `drive.readonly`
token. Google's consent screen rejected that request ("Access blocked: This app's request is invalid").
Rather than debug the unverified-app OAuth setup, `drive_reader` now uses the claude.ai Google
Drive connector, which is already authorised and which headless `claude -p` loads (verified live).
Confinement: no `--dangerously-skip-permissions`; `--allowedTools ToolSearch search_files
read_file_content`, so every other tool is auto-denied in non-interactive mode. Python parses
`--output-format stream-json`, takes the file list and `fileContent` from the raw tool results, and
refuses unless the file read is the newest owned Doc/.docx matching the prefix. The docx/XML
parsing, the `--readonly` flag on `scripts/setup_drive_oauth.py`, and the `token_file` parameter on
`drive_client` were all removed. Trade-off: the CV fetch depends on the claude.ai login and the
connector rather than a Google token.
