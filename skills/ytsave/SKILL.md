---
name: ytsave
description: Use when user runs /ytsave <url>, says "save this to my YouTube playlist", "add these to my playlist", or shares a TikTok slideshow recommending YouTube videos and asks to import them. Do NOT trigger on general TikTok links unrelated to YouTube video recommendations, or on regular (non-slideshow) TikTok videos.
allowed-tools: Bash,Read,Write
---

# ytsave

Extracts YouTube video recommendations from a TikTok photo slideshow and adds them to
the user's "TikTok Finds" YouTube playlist, with no manual intervention.
Returns the playlist URL plus added/skipped videos on success, or fails with a clear error.

## When Invoked

Run the script with the venv Python:

```bash
/home/cian/git/ai-agents/skills/ytsave/.venv/bin/python /home/cian/git/ai-agents/skills/ytsave/ytsave.py <url>
```

## Interpreting Output

- **Success**: first line is the playlist URL; following lines report added/skipped videos — relay all of it to the user
- **ERROR: ...** on stderr + non-zero exit: surface the error message directly to the user; do not retry silently
- **"This TikTok is a regular video, not a photo slideshow"**: tell the user v1 only handles slideshow posts

## What the Script Does

1. Validates the URL is a TikTok link
2. Downloads the slideshow images (yt-dlp, with a tikwm.com fallback) and OCRs them with Tesseract
3. Extracts recommended video titles via LLM (Antigravity first, Claude fallback; Claude vision as a last resort if OCR finds nothing)
4. Searches YouTube for each title (yt-dlp, no API key)
5. LLM-verifies matches — unsure titles are skipped and reported with a manual search link, never guessed
6. Adds confident matches to the "TikTok Finds" playlist (auto-created on first run), skipping duplicates

## Setup Check

If the user hasn't set up the skill yet (venv missing, `~/.config/ytsave/browser.json` missing),
direct them to `skills/ytsave/README.md`.
