# vidqueue Skill Design

**Date:** 2026-05-23  
**Status:** Approved for implementation

## Problem

When browsing TikTok, I find videos/slideshows that recommend YouTube video essays. There's no friction-free way to capture those recommendations into a watch queue — I have to manually copy each URL. A Telegram bot that accepts TikTok links and auto-imports the recommended videos into a YouTube playlist removes that friction.

## Design Decisions

- **Standalone skill** (`skills/vidqueue/`) — mirrors mealsave structure exactly; clean separation from recipe logic
- **Telegram bot as entry point** — phone-first; same auth-gated single-user pattern as mealsave_bot.py
- **Multi-source extraction** — caption URLs (fast path) + OCR (slideshows) + Whisper (audio) + LLM synthesis; all sources combined before LLM call
- **YouTube Data API v3** — playlist management (create, dedup, insert, search)
- **Reuse existing OAuth credentials** — add `youtube.force-ssl` scope to existing `credentials.json`; token stored separately at `~/.config/vidqueue/youtube_token.json`
- **Auto-create playlist** — on first run, create "TikTok Recommendations" (or configured name) if it doesn't exist; cache playlist ID in config

## Architecture

```
[Telegram]
    │  TikTok URL
    ▼
vidqueue_bot.py
    │  subprocess: python vidqueue.py <url>
    ▼
vidqueue.py
    ├── fetch_tiktok_metadata()     yt-dlp --dump-json (30s, non-fatal)
    │   └── scan description for YouTube URLs + titles
    ├── fetch_tiktok_video()        yt-dlp download (120s, non-fatal for OCR/Whisper)
    │   ├── extract_text_from_video()  ffmpeg keyframes + pytesseract OCR
    │   └── transcribe_audio()         whisper "base" (non-fatal)
    ├── llm_extract_videos()        Claude sonnet → Antigravity fallback
    │   └── returns [{title, channel, youtube_url|null}]
    ├── youtube_search()            Data API search.list (per unresolved title)
    ├── get_or_create_playlist()    playlists.list → playlists.insert if missing
    ├── dedup_against_playlist()    playlistItems.list → filter already-added
    └── insert_videos()             playlistItems.insert × N
    │
    stdout: structured result lines
    ▼
vidqueue_bot.py → edits Telegram message with summary
```

## Data Flow

**Input:** TikTok URL (video or slideshow)

**Extraction pipeline (in order, all combined before LLM):**
1. Caption scan — yt-dlp metadata; extract direct `youtube.com`/`youtu.be` URLs from description
2. OCR — ffmpeg keyframes (scene change > 0.1) + pytesseract; catches slide text
3. Whisper — audio transcription ("base" model); catches spoken recommendations

**LLM prompt (vidqueue):**  
Input: combined caption + OCR + transcript (max 8000 chars)  
Output JSON: `[{"title": str, "channel": str | null, "youtube_url": str | null}]`  
Model: Claude sonnet (Antigravity fallback)

**Resolution:**
- Direct URL found → extract video ID, skip search
- Title only → `search.list?q={title}+{channel}&type=video` → take top result

**Dedup:** `playlistItems.list` fetches all existing video IDs in playlist; skip any match

**Output (stdout, one line per type):**
```
ADDED:<video_id>:<title>
SKIPPED:<video_id>:<title>
UNRESOLVED:<raw_title>
PLAYLIST:<playlist_id>:<playlist_url>
```

## YouTube API Calls

| Method | Purpose |
|--------|---------|
| `playlists.list?mine=true` | Find existing playlist by name |
| `playlists.insert` | Create playlist on first run |
| `playlistItems.list` | Fetch all video IDs for dedup |
| `playlistItems.insert` | Add video to playlist |
| `search.list` | Resolve title → video ID |

**OAuth scope:** `https://www.googleapis.com/auth/youtube.force-ssl`

## Telegram Reply Format

```
✅ Added 3 videos to "TikTok Recommendations":
• The Philosophy of Inception → youtu.be/abc123
• Why Kubrick Matters → youtu.be/def456
• Every Frame a Painting → youtu.be/ghi789

⏭️ Already in playlist: 1
❓ Couldn't resolve: "some vague title" — search manually
```

## Configuration

**`~/.config/vidqueue/.env`**
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
YOUTUBE_PLAYLIST_NAME=TikTok Recommendations   # optional, default shown
```

**`~/.config/vidqueue/youtube_token.json`** — OAuth token (auto-created on first run via browser flow)

**Credentials:** `~/git/ai-agents/credentials.json` — shared with calendar/gmail; needs `youtube.force-ssl` scope added in Google Cloud Console

## File Layout

```
skills/vidqueue/
├── SKILL.md                 # Skill definition (trigger: /vidqueue <url>)
├── vidqueue.py              # Core extractor + YouTube API logic
├── vidqueue_bot.py          # Telegram bot (mirrors mealsave_bot.py)
├── requirements.txt         # Python deps
├── .venv/                   # Local virtualenv (gitignored)
└── vidqueue-bot.service     # systemd user service
```

## Reused from mealsave

| Function | Source file | Notes |
|----------|------------|-------|
| `fetch_tiktok_metadata` | mealsave.py | Copy verbatim (same yt-dlp call) |
| `fetch_tiktok_video` | mealsave.py | Copy verbatim |
| `transcribe_audio` | mealsave.py | Copy verbatim (whisper base) |
| `extract_text_from_video` | mealsave.py | Copy verbatim (ffmpeg + tesseract) |
| `llm_extract` (adapted) | mealsave.py | Rename to `llm_extract_videos`, change prompt and output schema |
| Telegram bot structure | mealsave_bot.py | Copy and adapt (load_bot_config, authorized, extract_urls, handle_message) |

## Error Handling

- TikTok metadata fetch fails → continue (non-fatal); log warning
- Video download fails → skip OCR + Whisper; proceed with caption only
- LLM fails both providers → exit with `ERROR: LLM extraction failed`
- YouTube search returns no results → mark title as UNRESOLVED (non-fatal)
- YouTube API auth fails → exit with `ERROR: YouTube auth failed — run vidqueue.py --auth`
- Playlist insert fails → exit with `ERROR: Could not add video <id>`

## Auth Bootstrap

First-run OAuth flow:
```bash
python vidqueue.py --auth
```
Opens browser → user grants YouTube scope → token saved to `~/.config/vidqueue/youtube_token.json`

## Testing

- Unit tests: `tests/test_vidqueue.py`
  - `test_extract_youtube_urls_from_caption` — regex parsing of description
  - `test_llm_extract_videos` — mock LLM output parsing
  - `test_dedup_filter` — already-in-playlist filtering
  - `test_output_format` — stdout line format
- Manual: `python vidqueue.py <real_tiktok_url>` against a test playlist
- Bot: send TikTok link to Telegram bot, verify reply and playlist

## Dependencies

**New Python packages:**
- `google-api-python-client` — YouTube Data API v3
- `google-auth-oauthlib` — OAuth 2.0 flow

**Shared with mealsave (install in own .venv):**
- `yt-dlp`, `openai-whisper`, `pytesseract`, `python-telegram-bot`

**System (likely already installed):**
- `ffmpeg`, `tesseract-ocr`
