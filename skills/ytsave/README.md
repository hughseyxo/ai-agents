# ytsave

Extracts YouTube video recommendations from a TikTok photo slideshow and adds them
to a fixed YouTube playlist ("TikTok Finds"), in one command.

**Supports:** TikTok photo-mode slideshows (screenshots of recommended videos) + caption text.
Regular (spoken/video) TikToks are rejected with a clear error — not supported in v1.

No Google Cloud project or OAuth consent screen needed. YouTube search uses `yt-dlp`
(public, no API key); playlist writes use `ytmusicapi` with cookies copied from a
logged-in browser session.

---

## Setup

### 1. Install system dependency

OCR requires `tesseract` (already installed on this machine for mealsave; if not):

```bash
sudo apt update
sudo apt install tesseract-ocr
```

### 2. Create the Python venv and install dependencies

```bash
cd ~/git/ai-agents/skills/ytsave
python3 -m venv .venv
.venv/bin/pip install --quiet yt-dlp pytesseract Pillow requests ytmusicapi
```

### 3. One-time YouTube Music auth (cookie-based, no Google Cloud)

1. Open [music.youtube.com](https://music.youtube.com) in your browser, logged in.
2. Open DevTools (F12) → **Network** tab, reload the page.
3. Click any request to `music.youtube.com/browse` (or similar), find the **Request Headers**
   section, and copy the full raw header block (right-click → Copy → Copy request headers,
   or copy the "cookie" + full header text depending on browser).
4. Run the interactive setup, which prompts you to paste those headers:
   ```bash
   mkdir -p ~/.config/ytsave
   ~/git/ai-agents/skills/ytsave/.venv/bin/ytmusicapi browser --file ~/.config/ytsave/browser.json
   ```
5. Confirm the file was created: `cat ~/.config/ytsave/browser.json`

This step needs re-running if the browser session cookies expire (ytsave will report a
clear auth error telling you to redo it — see Troubleshooting below).

### 4. Smoke test

```bash
/home/cian/git/ai-agents/skills/ytsave/.venv/bin/python \
  /home/cian/git/ai-agents/skills/ytsave/ytsave.py \
  https://vm.tiktok.com/ZNRE2STBx/
```

You should see the "TikTok Finds" playlist URL, plus which videos were added or skipped.

---

## Usage

### In Claude Code / Terminal
```
/ytsave https://vm.tiktok.com/ZNRE2STBx/
```

### Via Telegram (concierge bot)
Send the TikTok link to the concierge bot and ask it to save the recommendations to
your YouTube playlist.

---

## How it works

1. Fetches TikTok metadata (caption) via `yt-dlp`.
2. Downloads slideshow images via `yt-dlp`; falls back to the `tikwm.com` API if
   `yt-dlp` can't extract a photo post. Rejects regular video TikToks.
3. OCRs each image with Tesseract.
4. Extracts a list of recommended video titles via LLM (Antigravity `agy` CLI first,
   `claude` CLI fallback). If OCR yields nothing usable, falls back to Claude vision
   (reading the images directly).
5. Searches YouTube for each title via `yt-dlp ytsearch` (no auth, no quota).
6. An LLM call verifies which search result (if any) is a confident match per title —
   unsure titles are skipped, not guessed.
7. Adds confident matches to the "TikTok Finds" playlist (auto-created on first run),
   skipping anything already in the playlist.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `ytmusicapi auth not found` | Run the step-3 setup above. |
| `ytmusicapi auth failed` | Browser cookies have expired — redo step 3. |
| `This TikTok is a regular video, not a photo slideshow` | Expected — v1 only handles slideshows. |
| `Could not find any recommended YouTube videos` | OCR + vision fallback both found nothing — the slideshow may not contain clear video titles. |
| Videos reported as skipped | No confident match was found on YouTube search, or they're already in the playlist. Search links are printed so you can add manually. |

`ytmusicapi browser` auth is marked "deprecated" upstream in favor of an OAuth flow —
that OAuth flow requires a Google Cloud project, which is exactly what this skill avoids
(an unpublished/Testing GCP app gets 7-day refresh token expiry). Cookie auth keeps
working regardless; if it's ever removed upstream, revisit the design doc for alternatives.
