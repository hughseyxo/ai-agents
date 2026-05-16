# Design Doc: Mealsave TikTok Support & Telegram Bot

## Problem
The `mealsave` skill currently handles schema.org sites, YouTube (via transcript), and generic pages (via trafilatura). However:
1. **TikTok** is unsupported. TikToks often lack captions, requiring audio transcription and OCR for recipe extraction.
2. **Mobile UX** is poor. Users often find recipes on their phones (TikTok/YouTube apps) but need to manually copy URLs to a terminal or Claude session to save them.

## Design Decisions
- **Local TikTok Extraction:** Use `yt-dlp` for downloads, `whisper` for audio, and `ffmpeg` + `tesseract` for OCR. This maintains the "local-first" principle and avoids expensive multimodal API calls for every save.
- **Scene-Based OCR:** Instead of OCR on every frame, use `ffmpeg` scene detection to identify keyframes where text (ingredients/steps) is likely to be displayed.
- **Telegram Bot Integration:** Create a lightweight bot that acts as a remote trigger for the `mealsave.py` script.
- **Security:** Strict user ID filtering for the Telegram bot to prevent unauthorized Mealie spam.

## Architecture

### TikTok Extraction Pipeline (`mealsave.py`)
1. **URL Recognition:** Detect `tiktok.com` links.
2. **Caption-First (Path 2a):** `yt-dlp --dump-json --skip-download` fetches metadata (title, description, uploader). If the description is non-empty, send title+description to the LLM. If a complete recipe comes back (both ingredients and instructions), save it and exit — no video download needed. Most TikTok recipe creators put the full recipe in the caption, making this the highest-signal/lowest-cost path.
3. **AV Fallback (Path 2b):** Only if caption-first yields an incomplete recipe (or no caption):
   - **Download:** `yt-dlp` fetches the video.
   - **Audio Path:** `whisper` (base model) transcribes speech to text.
   - **Visual Path:** `ffmpeg` extracts frames where scene change > 0.1; `pytesseract` performs OCR on each frame; text is cleaned and deduplicated.
   - **Synthesis:** Caption + transcript + OCR text -> `claude` CLI -> Structured JSON. Caption is always included to give the LLM extra signal even when the AV pipeline is noisy.
4. **Failure mode:** if AV extraction also returns an empty recipe, the script dies with a "save manually in Mealie's UI" message.

### Telegram Bot (`mealsave_bot.py`)
- **Library:** `python-telegram-bot`.
- **Logic:** Listen for messages containing URLs.
- **Execution:** Run `python3 mealsave.py <url>` as a subprocess.
- **Response:** Send the resulting Mealie URL or error message back to the chat.

## Data Model
No new database tables are required. Mealie remains the primary data store. The bot uses environment variables for configuration.

## File List
- `skills/mealsave/mealsave.py` (Modified: Added TikTok logic)
- `skills/mealsave/mealsave_bot.py` (New: Telegram bot)
- `skills/mealsave/README.md` (Modified: Updated setup/usage)
- `skills/mealsave/SKILL.md` (Modified: Updated description)
- `CLAUDE.md` (Modified: Added new dependencies/bot)
- `mealsave-bot.service` (New: systemd unit)

## System Dependencies
- `ffmpeg`
- `tesseract-ocr`
- `libtesseract-dev` (for pytesseract)

## Python Dependencies
- `openai-whisper`
- `pytesseract`
- `python-telegram-bot`
- `yt-dlp` (already present)
