# Batch Plant Photo Upload with Trend-Aware Assessment & Rate-Limit Pause/Resume

## Problem

FloraPulse's photo upload is per-plant, per-photo (`POST /api/plants/{name}/photo`) — you have to open each plant's page and upload one photo at a time to get a health assessment. With ~15 plants, walking the house/garden and doing this one by one is a real pain point.

Two related gaps surfaced during design:

- **Photos are never persisted.** Every assessment call writes to an ephemeral `tempfile.TemporaryDirectory` deleted immediately after the Opus call. There's no photo history, so the agent can only compare against past *text* summaries in the profile markdown — it can't visually detect things like progressive wilting over time.
- **The vision path has zero retry/rate-limit handling.** `assess_image()` (`telegram-bot/claude_backend.py`) has no retry, backoff, or failover — unlike `BaseAgent.synthesize()`, which has a documented retry/failover chain for text-based LLM calls. A batch of N back-to-back Opus calls is much more likely to hit a Claude subscription usage limit than today's one-off calls.

A frontend audit also found FloraPulse has no toast/notification component — every action feeds back via blocking `alert()`/`confirm()`, which can't represent N independent async per-photo statuses in a batch.

## Goals

- Upload a pile of unlabeled plant photos at once; the agent identifies each against known plants and queues a health assessment per match.
- If a Claude usage limit is hit mid-batch, pause automatically and resume once the limit window has passed (a "ping" retry loop), without losing progress on already-processed photos.
- Persist photos so assessments can reason about trends (e.g. wilting) across time, not just a single snapshot — for both the new batch flow and the existing single-photo flow.
- Surface the new photo history visually (previously invisible backend data) and give batch processing proper async feedback (toast), without disturbing FloraPulse's existing visual identity.

## Design decisions

1. **Surface:** FloraPulse PWA only. The Telegram bot already handles photos one-per-message, which isn't the pain point; batching is not added there.
2. **Identification:** AI auto-identifies each photo against existing plants — no pre-labeling required. Unmatched photos get manual assignment in the UI.
3. **Call shape:** a combined identify+assess call (not the bot's existing two-separate-calls pattern), to keep per-photo Opus calls to a minimum.
4. **Trend handling (two-stage):** call 1 identifies the plant and gives a same-photo-only assessment. If a confident match is found, call 2 re-assesses using the last 3 stored photos of *that* plant for trend language — this is the only way to include trend images, since which plant's history to load isn't known until after identification. The existing single-photo endpoint (plant already known from the URL) skips call 1 entirely and always trend-refines in one call.
5. **Rate-limit detection:** parse CLI stdout/stderr for known usage-limit signals ("usage limit", "rate limit", "429") via a new helper. This distinguishes "the whole batch must pause" from "this one photo just failed" (bad image, transient error) — only the former pauses the job.
6. **Resume cadence:** fixed 5-minute ping while paused, capped at 2 hours of cumulative pause time before the job is marked `failed` for manual retry. Chosen over honoring a parsed reset-time hint (adds parsing complexity for an unreliable format) or exponential backoff (risks hammering right after a long limit window).
7. **Persistence:** job state lives in `AgentDB`, not memory — a `plant_ui` service restart mid-batch doesn't lose progress; the job resumes automatically on startup.
8. **Photo storage location:** `data/plant-photos/<slug>/`, outside `docs/` — `docs/` is synced to Obsidian via CouchDB livesync-bridge, built for markdown notes; binary images there would bloat that sync unnecessarily.
9. **Retention:** last 10 photos per plant, oldest pruned automatically after each new save.
10. **Scope:** trend-aware storage/assessment applies to **both** the batch and single-photo endpoints — one consistent mechanism.
11. **Trend window:** last 3 stored photos per plant, unbounded by date — bounds every trend-refine call to ≤4 images, regardless of upload frequency.
12. **Frontend scope:** match FloraPulse's existing design tokens/patterns exactly (glassmorphism, forest-green palette, emoji iconography, Alpine.js conventions — none of that needs reinventing). Add a toast component scoped to the new batch/manual-assign feedback only (not a full `alert()` migration), fix the pre-existing `.btn-sm` CSS gap and stale `sw.js` cache list (touched incidentally), and add a photo-timeline strip to the plant detail view as the visible payoff of the new photo-history data.

## Architecture

New domain module `agents/photo_batch.py` (batch job model, sequential processing loop, pause/resume state machine), driven from `plant_ui/server.py`, reusing the vision path already shared between the PWA and the Telegram bot in `telegram-bot/claude_backend.py` (imported into `plant_ui` today via `sys.path.insert(REPO_ROOT / "telegram-bot")`).

No new services, no new cron entries, no new dependencies. Everything rides the existing FastAPI process (`asyncio.create_task` for the background runner) and the existing `AgentDB` (`agents/db.py`).

```
Upload N photos → POST /api/plants/batch-photos
                     │
                     ▼
        photo_batch_jobs row created (status=running)
                     │
                     ▼
        asyncio.create_task(run_batch_job)  ──── returns job_id immediately
                     │
                     ▼
   for each item (from current_index):
        identify_and_assess(photo)            [call 1]
             │
             ├─ usage-limit signal? ─────► status=paused, next_ping_at=+5m
             │                              (retry same item on wake, cap 2h)
             │
             ├─ no confident match ──────► item.status=unmatched (continue)
             │
             └─ matched ──► trend-refine using last 3 stored photos  [call 2]
                             │
                             ▼
                     persist assessment (profile md, observation note,
                     plant_photos row, plant.last_assessment)
                     item.status=done, current_index += 1
```

## Data model

New tables in `agents/db.py`:

```sql
CREATE TABLE IF NOT EXISTS plant_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_name  TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    taken_at    TEXT NOT NULL DEFAULT (datetime('now')),
    assessment_summary TEXT,
    assessment_status  TEXT
);
CREATE INDEX IF NOT EXISTS idx_plant_photos_lookup ON plant_photos(plant_name, taken_at DESC);

CREATE TABLE IF NOT EXISTS photo_batch_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'running',  -- running|paused|done|failed
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    items_json    TEXT NOT NULL,   -- [{temp_path, status, matched_plant, confidence, result, error}]
    current_index INTEGER NOT NULL DEFAULT 0,
    pause_reason  TEXT,
    next_ping_at  TEXT
);
```

New `AgentDB` methods (following the existing key-value/row patterns — `get_state`/`set_state`, `upsert_plant_row`): `add_plant_photo`, `get_recent_plant_photos(plant_name, limit=3)`, `get_plant_photo_history(plant_name, limit=10)` (for the timeline UI), `prune_plant_photos(plant_name, keep=10)`, `create_batch_job`, `get_batch_job`, `update_batch_job`, `list_active_batch_jobs()`.

Photo bytes: `data/plant-photos/<slug>/<ISO-timestamp>.jpg` (mirrors the existing slugging convention in `agents/plant_profiles.py`). Batch-upload originals: `data/plant-photo-batches/<job_id>/<index>.jpg` (survives a restart, unlike today's `tempfile.TemporaryDirectory`), deleted once the job reaches `done`/`failed`.

## API surface (new/changed, `plant_ui/server.py`)

- `POST /api/plants/{name}/photo` (existing, extended) — now persists the photo and folds the last 3 stored photos in as trend context before the single Opus call.
- `POST /api/plants/batch-photos` — accepts multiple files, creates a job, returns `{job_id}` immediately.
- `GET /api/plants/batch-photos/{job_id}` — job status + per-item progress, for polling.
- `POST /api/plants/batch-photos/{job_id}/items/{index}/assign` — manual plant assignment for an unmatched item.
- `GET /api/plants/{name}/photos` — last 10 stored photos (id, date, status) for the timeline strip.
- `GET /api/plants/{name}/photos/{photo_id}` — streams a stored photo file.
- Startup hook — resumes any `running`/`paused` job via `agents.photo_batch.resume_active_jobs()`.

## `telegram-bot/claude_backend.py` changes

- `assess_image()` gains `extra_image_paths: list[str] | None` — passes multiple `--add-dir` flags and lists each path chronologically in the prompt.
- New `detect_usage_limit(stdout, stderr) -> bool` — pattern match for known limit phrasing.
- New `identify_and_assess(image_path, plants, system_prompt) -> dict | None` — combined call-1 (identify + preliminary assessment).
- `_run_claude` gains a detailed variant surfacing raw stdout/stderr (needed for limit detection) without changing the existing return contract used by `ask_claude` and the current `assess_image` callers.

## Frontend

All new markup/CSS follows FloraPulse's existing conventions exactly: `.glass-panel` surfaces, `--font-heading`/`--font-body` tokens, existing `.btn-*` variants, emoji-in-`<span>` iconography, the established loading-state trio (`:class`/`:disabled`/paired `x-show`), and the single flat `plantApp()` Alpine root.

- **Batch upload:** new dashboard entry point (`<input type="file" multiple>`), `batchItems[]` Alpine state, polling every ~3s against the job-status endpoint, manual-assign dropdown for unmatched items.
- **Toast component:** `.toast-container`/`.toast.glass-panel`, reusing existing `--status-*` tokens for success/error/info variants. Scoped to batch + manual-assign feedback only — existing `alert()`/`confirm()` calls elsewhere are untouched.
- **Photo timeline strip:** horizontal scroll strip on the plant detail view, thumbnails from the new photo-serving endpoint, status-colored ring per photo, date caption.
- **Incidental fixes:** `.btn-sm` CSS definition (referenced in HTML, never defined), `sw.js` `CACHE_NAME` bump + `ASSETS_TO_CACHE` sync with actual pinned CDN versions.

## File list

- `agents/db.py` — schema + new methods
- `telegram-bot/claude_backend.py` — multi-image support, `detect_usage_limit`, `identify_and_assess`
- `agents/photo_batch.py` — new
- `plant_ui/server.py` — extended `upload_photo`, 5 new endpoints, startup resume hook
- `plant_ui/templates/index.html` — batch upload UI, toast container, photo timeline markup
- `plant_ui/static/app.js` — batch state/polling, toast methods, photo-history loading
- `plant_ui/static/style.css` — batch uploader variant, toast styles, timeline styles, `.btn-sm` fix
- `plant_ui/static/sw.js` — cache version bump + asset-list sync
- `tests/test_plant_ui_api.py` — new coverage
- `CLAUDE.md` — FloraPulse section update

## Verification

- Manual batch upload of 3-4 real photos (mix of matchable + unrecognizable), confirming matches, manual-assign path, persisted assessments, populated `data/plant-photos/`, new DB rows, and a rendering timeline strip.
- Simulated pause (force `detect_usage_limit()` to return `True`) confirming `paused` status, `next_ping_at`, and auto-resume without losing progress.
- Mid-batch service restart confirming `resume_active_jobs()` recovery.
- PWA install/update sanity check after the `sw.js` cache-version bump.
- `.venv/bin/pytest tests/test_plant_ui_api.py` (extended).
- `ecc:python-review` on the Python diff.

## Related

- [[2026-05-28-master-plant-agent-design]] — original plant agent / assessment design this extends.
