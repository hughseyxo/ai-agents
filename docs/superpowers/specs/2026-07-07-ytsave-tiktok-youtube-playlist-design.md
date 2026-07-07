# ytsave — TikTok slideshow → YouTube playlist importer

## Problem

TikTok photo-mode slideshows often recommend YouTube videos (screenshots of video cards). Finding and saving each one manually is tedious. This mirrors the mealsave problem (recipe URL → Mealie) but for TikTok → YouTube playlist.

## Design decisions

- **Trigger:** both a Claude Code skill (`/ytsave <url>`) and a Telegram concierge tool from day one, sharing one script engine (`skills/ytsave/ytsave.py`), matching the mealsave pattern.
- **Target:** one fixed YouTube playlist, "TikTok Finds", auto-created on first run. The YouTube API can't write to the native Watch Later list, so a dedicated playlist is the only option.
- **Matching:** LLM-verified. Search candidates are compared against the extracted title/channel by an LLM call; only confident matches are added. Unsure titles are skipped and reported with a manual search link rather than silently adding the wrong video.
- **Scope v1:** photo-mode slideshows + caption text only. Regular (spoken/video) TikToks are rejected with a clear error — can be added later following mealsave's Whisper transcription path if needed.
- **Extraction:** Tesseract OCR per slideshow image + caption text → LLM cleanup into a title list (Antigravity-first, Claude fallback, per project provider-failover convention). If OCR yields zero usable titles, fall back to Claude vision (Read tool on the images) before giving up.
- **YouTube backend — no Google Cloud OAuth.** The user's GCP project is in Testing publishing status; the Data API's OAuth refresh tokens expire after 7 days in that mode, and publishing requires a Google review the user doesn't want to do. Alternative: **yt-dlp `ytsearch`** for video lookup (no API key, no quota, public) and **ytmusicapi with browser-cookie auth** (InnerTube, unofficial) for playlist create/read/write. YouTube Music playlists are ordinary YouTube playlists; `add_playlist_items` accepts any YouTube video ID. Auth is a one-time "paste request headers from a logged-in browser tab" step (`ytmusicapi browser`), the same class of setup the user already maintains for mealsave's YouTube cookies — no OAuth consent screen, no review, no expiry cycle, at the cost of depending on an unofficial API.

## Pipeline

1. Resolve/validate TikTok URL (expand `vm.tiktok.com` short links).
2. Fetch TikTok metadata via yt-dlp (`--dump-json --skip-download`) — title/description/uploader, reusing the approach in `skills/mealsave/mealsave.py:fetch_tiktok_metadata`.
3. Download slideshow images: yt-dlp first; fall back to the tikwm.com API (`images[]` field) if yt-dlp can't extract a photo post. If the post is a regular video, exit with a clear "not supported" error.
4. OCR each image with Tesseract (`pytesseract`).
5. LLM call #1: OCR text + caption → JSON list of `{title, channel?}` candidates. Provider chain: `agy` CLI, then `claude` CLI fallback.
6. Vision fallback: if step 5 returns zero titles, re-run extraction via Claude vision (Read tool on the images).
7. YouTube search per title: yt-dlp `ytsearch5:<title> --dump-json --flat-playlist` (no auth, no quota).
8. LLM call #2: batched match verification — for each title, choose the correct candidate video ID or `skip`.
9. Playlist ensure + dedup via ytmusicapi: find-or-create "TikTok Finds" (ID cached in `~/.config/ytsave/config.json`); list existing items, skip video IDs already present.
10. Insert new matches (`add_playlist_items`); report playlist URL, added videos, and skipped titles (with a manual YouTube-search link each).

Errors exit non-zero with a message on stderr — same contract as mealsave, so the skill and concierge wrappers just relay stdout/stderr.

## Auth setup

One-time: `ytmusicapi browser` walkthrough (paste headers copied from a logged-in `music.youtube.com` request) → `~/.config/ytsave/browser.json`. No Google Cloud project changes needed. Failure mode when cookies rot: a clear "auth expired, re-run setup" error (mirrors mealsave's cookie-expiry check), never a silent wrong write.

## Files

- `skills/ytsave/ytsave.py` — pipeline
- `skills/ytsave/SKILL.md`, `skills/ytsave/README.md` — skill definition + setup docs
- `tests/test_ytsave.py` — unit tests for all pure-logic parsing/dedup/matching, mocked I/O
- `telegram-bot/tools.py`, `telegram-bot/tool_specs.py` — `save_youtube_playlist` concierge tool
- `CLAUDE.md` — Skills section + concierge tool entry

## Known risks

- ytmusicapi is an unofficial API and could break on YouTube-side changes; it's actively maintained. Failures are surfaced as clear errors, not silent bad writes.
- yt-dlp photo-post extraction is version-dependent; tikwm fallback covers most cases.
- OCR-mangled titles could mismatch on search; mitigated by the LLM verification + skip-and-report step.

## Verification

- `pytest tests/test_ytsave.py` green.
- End-to-end manual run against a real TikTok slideshow URL, confirm playlist contents and dedup on re-run.
- Concierge: trigger via Telegram, confirm same result.
