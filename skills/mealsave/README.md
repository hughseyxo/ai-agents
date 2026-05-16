# mealsave

Saves recipe URLs to your self-hosted Mealie instance in one command.

**Supports:**
- Recipe websites with schema.org markup (handled by Mealie's built-in scraper)
- YouTube videos with captions (transcript → Claude LLM extraction)
- TikTok videos (whisper audio transcript + tesseract OCR keyframes → Claude LLM extraction)
- Generic blog posts and recipe pages (trafilatura → Claude LLM extraction)

---

## Setup

### 1. Generate a Mealie API token

- Open Mealie in your browser
- Click your user icon → **Manage Your API Tokens**
- Create a token named `mealsave`, copy it

### 2. Install System Dependencies

TikTok extraction requires `ffmpeg` and `tesseract`:

```bash
sudo apt update
sudo apt install ffmpeg tesseract-ocr libtesseract-dev
```

### 3. Create the config file

```bash
mkdir -p ~/.config/mealsave
cp ~/.claude/skills/mealsave/.env.example ~/.config/mealsave/.env
chmod 600 ~/.config/mealsave/.env
```

Edit `~/.config/mealsave/.env`:
```
MEALIE_URL=http://localhost:9000
MEALIE_TOKEN=YOUR_MEALIE_TOKEN_HERE
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
TELEGRAM_USER_ID=YOUR_TELEGRAM_ID_HERE
```

### 4. Create the Python venv and install dependencies

```bash
cd ~/git/ai-agents/skills/mealsave
python3 -m venv .venv
.venv/bin/pip install --quiet requests youtube-transcript-api trafilatura lxml_html_clean yt-dlp openai-whisper pytesseract python-telegram-bot bgutil-ytdlp-pot-provider
```

### 5. Set up YouTube PO Token Provider (Required for YouTube)

YouTube blocks most automated subtitle downloads. The `bgutil` provider bypasses this by generating Proof-of-Origin tokens.

1. **Install Node helper:**
   ```bash
   cd ~/git/ai-agents/skills/mealsave
   git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git pot-provider
   cd pot-provider/server
   npm ci
   npx tsc
   ```

2. **Verify setup:**
   ```bash
   ~/git/ai-agents/skills/mealsave/check-yt-auth.sh
   ```
   You should see `OK` for both the Python plugin and Node helper.

### 6. Set up YouTube cookies (Backup/Fallback)

YouTube blocks subtitle downloads from server/VPS IPs unless you pass cookies from a logged-in browser session. This is a one-time setup:

1. Install the **"Get cookies.txt LOCALLY"** extension in your browser ([Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) / [Firefox](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/))
2. Go to [youtube.com](https://youtube.com) while logged in
3. Click the extension icon → Export cookies → save as `youtube-cookies.txt`
4. Copy to the server:
   ```bash
   scp youtube-cookies.txt yourserver:~/.config/mealsave/youtube-cookies.txt
   chmod 600 ~/.config/mealsave/youtube-cookies.txt
   ```

Without this file, YouTube URLs will fail with a clear error telling you what to do.

### 7. Smoke test

```bash
/home/cian/git/ai-agents/skills/mealsave/.venv/bin/python \
  /home/cian/git/ai-agents/skills/mealsave/mealsave.py \
  https://www.seriouseats.com/the-best-roast-potatoes-ever-recipe
```

You should see a URL like `http://localhost:9000/g/home/r/the-best-roast-potatoes-ever-recipe`.

---

## Usage

### In Claude Code / Terminal
```
/mealsave https://www.seriouseats.com/the-best-roast-potatoes-ever-recipe
/mealsave https://www.youtube.com/watch?v=<video-id>
/mealsave https://www.tiktok.com/@user/video/<id>
```

### Via Telegram Bot
Forward any recipe link to your `mealsave_bot`. It will process it in the background and reply with the Mealie link.

---

## How it works

1. **Mealie scraper first** — `POST /api/recipes/create-url`. Handles most schema.org recipe sites natively and is instant. Sets `orgURL` so you can trace back to the source.
2. **TikTok path** — uses `yt-dlp` to download, `whisper` for audio, and `ffmpeg` + `tesseract` for OCR on keyframes.
3. **YouTube path** — fetches captions via `yt-dlp`.
4. **Generic page path** — `trafilatura` strips ads/nav to get article text, then Claude extracts the recipe.

If extraction produces zero ingredients *and* zero instructions, the recipe is not saved — you get a clear error instead of junk in Mealie.

Duplicate detection: if a recipe with the same source URL already exists in Mealie, the script surfaces that URL and exits without creating a duplicate.

---

## LLM note

Recipe extraction uses the `claude` CLI (`claude -p`). This is already available in your Claude Code session — no separate API key needed.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Cannot connect to Mealie` | Check `docker ps` — is the mealie container running? |
| `401 Unauthorized` | Regenerate the API token in Mealie UI and update `.env` |
| `No captions available` | The YouTube video has no transcripts — try importing manually in Mealie's UI |
| `LLM extraction produced an empty recipe` | The page text didn't contain a recipe Claude could parse — save manually |
| `claude CLI not found` | Ensure you're running from within a Claude Code session |
