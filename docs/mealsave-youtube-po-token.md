# Design Doc: Mealsave YouTube PO Token Automation

## Problem

YouTube has escalated bot-detection on `yt-dlp`. Two issues stack:

1. **JS challenge requirement.** Recent yt-dlp builds require a JavaScript runtime (`deno`, `node`) to solve YouTube's player challenge. Without one, every request — even with valid cookies — fails with `Sign in to confirm you're not a bot`.
2. **PO token requirement for subtitles.** Even with cookies + a JS runtime, the auto-subtitle endpoint (which mealsave depends on) requires a PoToken (Proof-of-Origin token). The browser mints these via WebAssembly; yt-dlp cannot generate them natively.

Current state, tested 2026-05-15 against `https://www.youtube.com/watch?v=Bkd0LxBd2L8`:
- Cookieless: fails (bot wall on server IP).
- Cookies only: fails (no JS runtime).
- Cookies + `--js-runtimes node`: metadata extraction works (title returned).
- Cookies + node + subtitle download: fails (PO token required for caption endpoint).

The existing manual cookie-export workflow (`check-yt-auth.sh` + browser extension + `scp`) keeps breaking and is the friction point we want to eliminate.

## Design Decisions

- **Adopt `bgutil-ytdlp-pot-provider`.** Community plugin that mints PO tokens locally via a small Node helper. Currently the only viable cookieless-or-low-friction path. Updated frequently in response to YouTube changes.
- **Script-mode provider, not Docker sidecar.** Two deployment modes exist: a long-running HTTP server (Docker) or a per-request Node script. Script mode is simpler on this single-user box — no extra service to monitor, restart, or expose. Acceptable cost: ~300ms Node startup per recipe save.
- **Keep cookies as fallback.** PO token does not fully replace cookies for all video types (age-gated, members-only). Keep the existing cookies file and `check-yt-auth.sh`; pass both to yt-dlp. If PO token alone works, cookies are unused harmlessly.
- **Always pass `--js-runtimes node`.** Node is already installed (v20). Avoids `deno` install. Required regardless of PO token.
- **Pin plugin version.** Plugin changes frequently and breaks; pin in requirements so updates are deliberate.
- **No fallback to manual flow.** If PO token plugin is missing or broken, fail loudly with a clear error — silent degradation to the old manual-cookie path hides the regression.

## Architecture

### Components

1. **`yt-dlp` plugin** (`bgutil-ytdlp-pot-provider`, Python) — installed into the mealsave venv. Registers a PoToken provider that yt-dlp invokes during YouTube extraction.
2. **Node helper script** (`bgutil-pot-generator`, npm package) — installed globally or into a local `node_modules/`. Called by the Python plugin as a subprocess; returns a PO token JSON blob.
3. **`mealsave.py`** — modified to add `--js-runtimes node` to every `yt-dlp` invocation for YouTube URLs.

### Flow

```
mealsave.py --url <youtube_url>
  └─> yt-dlp --js-runtimes node --cookies <file> ...
       ├─> [plugin hook] PoToken needed for player_client=X
       │    └─> exec: node bgutil-pot-generator.js
       │         └─> returns: {"poToken": "...", "visitorData": "..."}
       ├─> player API request with PO token → 200 OK
       └─> subtitle endpoint with PO token → returns VTT
```

### Failure modes

| Failure | Behavior |
|---|---|
| Plugin not installed | yt-dlp falls back to no-PoToken extraction → bot wall → mealsave errors with install instructions |
| Node helper missing | Plugin logs error and skips → same as above |
| PO token rejected (YouTube changes) | yt-dlp error surfaces; user updates plugin |
| Cookies expired AND PO token insufficient (age-gated content) | Existing cookies-expired message from `check-yt-auth.sh` |

## Data Model

No DB changes. No new state. PO tokens are minted per-request and discarded.

Config additions:
- Mealsave venv: `bgutil-ytdlp-pot-provider` Python package
- System: `bgutil-pot-generator` npm package (global) OR `~/.claude/skills/mealsave/node_modules/`

## File List

- `skills/mealsave/mealsave.py` (Modified: add `--js-runtimes node` to YouTube `yt-dlp` calls in `fetch_youtube_transcript`)
- `skills/mealsave/requirements.txt` or venv install step (Modified/New: pin `bgutil-ytdlp-pot-provider`)
- `skills/mealsave/README.md` (Modified: setup instructions for plugin + Node helper)
- `skills/mealsave/check-yt-auth.sh` (Modified: also verify plugin importable and Node helper resolvable; warn if not)
- `CLAUDE.md` (Modified: note PO-token dependency for mealsave/YouTube path)

## Open Questions

1. **Global npm vs local `node_modules/`?** Global is simpler; local is more reproducible. Lean local since the skill already has its own `.venv/`.
2. **Should we drop cookies entirely if PO token proves reliable for non-restricted content?** Defer — keep cookies for now, revisit after 30 days of telemetry. If `check-yt-auth.sh` shows no cookie-only successes in a month, remove cookie handling.
3. **Cron-check the plugin version?** Plugin breaks frequently. Could add a weekly `pip list --outdated | grep bgutil` check via cron. Probably premature; revisit if it breaks more than once a quarter.

## Test Plan

1. Install plugin into mealsave venv + Node helper.
2. Test URL: `https://www.youtube.com/watch?v=Bkd0LxBd2L8` (Babish — Chicken Tikka Masala).
   - Expect: subtitle VTT downloaded, recipe synthesized, Mealie save succeeds.
3. Test cookieless (temporarily move cookies file): does PO token alone unblock the bot wall?
4. Test an age-gated video (find one): confirm cookies fallback still works.
5. Run `check-yt-auth.sh`: confirm new plugin-presence check works.

## Rollback

Single-commit change. Revert + reinstall old venv if plugin causes regressions. Cookies file remains untouched, so old manual path still works as a backup.
