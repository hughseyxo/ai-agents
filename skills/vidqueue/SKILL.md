---
name: vidqueue
description: Use when user runs /vidqueue <url>, says "queue this TikTok", "save video recommendations", or asks to add TikTok-recommended YouTube videos to their playlist. Do NOT trigger on general TikTok pastes or non-video-recommendation content.
allowed-tools: Bash,Read
---

# vidqueue

Extracts YouTube video essay recommendations from a TikTok link (video or slideshow) and adds them to a managed YouTube playlist.

## When Invoked

Run the script with the venv Python:

```bash
/home/cian/git/ai-agents/skills/vidqueue/.venv/bin/python /home/cian/git/ai-agents/skills/vidqueue/vidqueue.py <tiktok_url>
```

## Interpreting Output

Stdout lines (one per video):
- `ADDED:<video_id>:<title>` — added to playlist
- `SKIPPED:<video_id>:<title>` — already in playlist
- `UNRESOLVED:<title>` — could not find a matching YouTube video
- `PLAYLIST:<id>:<url>` — the target playlist URL

**Success**: summarise what was added and show the playlist URL.
**`ERROR: ...`** on stderr + non-zero exit: surface the error directly to the user; do not retry.

## Mobile Use

Send a TikTok link to the **server concierge Telegram bot** — it has a `queue_tiktok` tool that calls this script automatically.

## Auth Bootstrap (first run only)

```bash
/home/cian/git/ai-agents/skills/vidqueue/.venv/bin/python /home/cian/git/ai-agents/skills/vidqueue/vidqueue.py --auth
```

Opens browser → grant YouTube access → token saved to `~/.config/vidqueue/youtube_token.json`.

## What the Script Does

1. Reads `~/.config/vidqueue/.env` for `YOUTUBE_PLAYLIST_NAME` (default: "TikTok Recommendations")
2. Fetches TikTok metadata via yt-dlp; scans description for direct YouTube URLs
3. Downloads video/slideshow; runs ffmpeg keyframe OCR (Tesseract) + Whisper transcription
4. Sends all extracted text to Claude Sonnet (Gemini fallback) → list of `{title, channel, youtube_url}`
5. Searches YouTube Data API for titles without a direct URL
6. Creates playlist if it doesn't exist; skips videos already in playlist
7. Inserts new videos; prints structured output lines
