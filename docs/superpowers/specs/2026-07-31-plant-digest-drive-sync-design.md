# Plant Health Digest → Google Drive → Claude.ai Project Sync

## Problem

The garden's real state lives on the server: `data/agents.db` (canonical plant rows, weather cache) and `docs/plants/*.md` (curated profiles, health assessments, frequency history). Every consumer of that state — the concierge Telegram bot, the FloraPulse chat, the plant agent itself — reaches it through server-side Python.

That leaves one gap: asking about the garden from a phone or the web without going through the server. Claude.ai Projects can hold a knowledge base, but nothing was feeding garden state into one, so a Claude.ai chat had no idea when anything was last watered or what the passionflower's root problem was.

Claude.ai's consumer Project knowledge base has **no write API** — files can't be pushed into it directly. Its Google Drive knowledge source, however, re-reads linked Drive files. So Drive is the only available one-way channel from server to Project.

## Goals

- A Claude.ai Project on mobile/web can answer garden questions with current data, no server access.
- Zero manual upkeep after a one-time Drive link — the data refreshes itself.
- A Drive outage must never damage the plant agent's real work (profile writes, frequency updates, status email).
- Drive access must not risk the already-working Gmail/Calendar OAuth tokens.

## Design decisions

1. **One consolidated doc, not per-plant files.** A single `Plant Health Digest.md` overwritten in place, rather than N files mirroring `docs/plants/`. Project knowledge retrieval works better over one coherent doc than 15 fragments, and it sidesteps orphan cleanup when a plant is removed (lavender died this month — a per-file scheme would have left a stale file behind).
2. **Overwrite, never append.** Same principle as the curated `## Current Observations` sections: the digest is a *current-state* projection, not a log. History stays in the profiles and observation notes, which the digest samples via `read_profile_context(max_assessments=2)`.
3. **Pushed from the intelligence run, not its own agent/cron entry.** The intelligence run is exactly when plant state last changed; a separate schedule would either duplicate work or drift. `_push_digest` runs after `_apply_intelligence_output` so the digest reflects the profile writes that run just made.
4. **Best-effort, non-fatal.** `_push_digest` swallows every exception to stderr. It runs *before* `_mark_ran("intelligence_run")`, so letting a Drive error propagate would leave the gate unmarked and re-run the entire (expensive, LLM-driven) intelligence step on the next hourly tick just because Drive 500'd.
5. **Separate Drive token file.** `~/.google_tokens_drive.json` with only the `drive.file` scope, distinct from Gmail/Calendar's shared `~/.google_tokens.json`. Same OAuth client, different grant. A Drive re-consent (or a botched setup run) therefore cannot truncate or overwrite the tokens the daily briefing depends on.
6. **`drive.file` scope, not `drive`.** The app only ever sees files it created itself — it structurally cannot read the rest of the user's Drive.
7. **Hand-rolled `urllib` client, no `google-api-python-client`.** Matches `agents/gmail_client.py`, and honours the dual-CLI rule (`agy` must run this too) by keeping the dependency surface at zero.
8. **Random multipart boundary.** The digest body is partly LLM-authored profile text. A fixed boundary string could in principle appear in that text and truncate the upload, so each request uses `uuid4()`.
9. **QR code in the setup script.** The OAuth consent URL is 200+ characters; copy/pasting it out of an SSH terminal reliably corrupts it. The script prints a scannable QR and falls back to the plain URL if `qrcode` isn't installed.

## Architecture

```
PlantAgent.intelligence_run
  └─ _apply_intelligence_output(...)      # profile + DB writes (canonical)
  └─ _push_digest(plants)                 # best-effort, try/except → stderr
       ├─ db.get_plant_weather_cache()
       ├─ plant_digest.build_digest(plants, weather_cache)
       │    └─ plant_profiles.read_profile_context(name, max_assessments=2)
       └─ drive_client.upload_digest(content)
            ├─ find_or_create_folder("Plant Health Digest")
            └─ upload_or_create_file(...)  # PATCH if present, multipart POST if not
  └─ _mark_ran("intelligence_run")
```

Auth: `get_access_token()` loads `~/.google_tokens_drive.json`, refreshes when within 300s of expiry, and re-saves atomically (tempfile + `os.replace`, mode 0600) so a crash can't leave a truncated token file.

## Data model

No schema changes. The digest is a pure read-side projection over existing sources:

| Source | Fields used |
|---|---|
| `plants` table (via `PlantStore`) | `name`, `location`, `sunlight`, `water_sensitivity`, `last_watered`, `frequency_days`, `baseline_frequency_days` |
| `plant_weather_cache` table | `plant_name`, `adjusted_date`, `adjustment_reason` |
| `docs/plants/<slug>.md` | curated context via `read_profile_context` (last 2 assessments) |

Document shape: title + generated-at line → summary table (Plant / Location / Next Water / Frequency / Sensitivity) → one `## <Plant>` section each with core fields, weather-adjusted next-water date and reason, then profile context.

Persisted state outside the DB: `~/.google_tokens_drive.json` (refresh token, access token, `obtained_at`, `expires_in`).

## Files

- `agents/drive_client.py` — new. Token load/refresh/atomic-save, `find_or_create_folder`, `upload_or_update_file`, `upload_digest`.
- `agents/plant_digest.py` — new. `build_digest(plants, weather_cache)`.
- `agents/plant_agent.py` — `_push_digest`, called from `intelligence_run`.
- `scripts/setup_drive_oauth.py` — new. One-time interactive consent, QR-assisted.
- `tests/test_plant_digest.py` — new. Digest assembly coverage.
- `tests/test_plant_agent.py` — `TestPushDigest`: pushes built digest; Drive failure is non-fatal.
- `CLAUDE.md` — Plant Watering Tracker section.

## Manual, one-time, outside this repo

In the Claude.ai Project (mobile or web): add Google Drive as a knowledge source and select the "Plant Health Digest" folder. There is no API for this — it has to be clicked once.

## Verification

- `.venv/bin/pytest tests/` — 673 passing.
- `python3 scripts/setup_drive_oauth.py` — consent flow, token written to `~/.google_tokens_drive.json`, Gmail/Calendar tokens untouched.
- Live intelligence run — digest present in Drive, second run overwrites in place rather than creating a duplicate.

## Related

- [[2026-06-04-plant-workflow-overhaul-design]] — plant agent lifecycle this hooks into.
- [[2026-07-12-batch-plant-photo-upload-design]] — the other consumer of profile context.
- [[2026-06-19-obsidian-vault-backend-design]] — the *other* one-way sync out of `docs/`; CouchDB serves Obsidian clients, Drive serves Claude.ai Projects.
- [[plants/_index|Plants]]
