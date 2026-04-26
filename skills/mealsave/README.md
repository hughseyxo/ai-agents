# mealsave

Saves recipe URLs to your self-hosted Mealie instance in one command.

**Supports:**
- Recipe websites with schema.org markup (handled by Mealie's built-in scraper)
- YouTube videos with captions (transcript → Claude LLM extraction)
- Generic blog posts and recipe pages (trafilatura → Claude LLM extraction)

**v1 does not support TikTok.** Most TikToks have no captions and recipes are baked into video frames — extraction is unreliable. Use Mealie's UI for those.

---

## Setup

### 1. Generate a Mealie API token

- Open Mealie in your browser
- Click your user icon → **Manage Your API Tokens**
- Create a token named `mealsave`, copy it

### 2. Create the config file

```bash
mkdir -p ~/.config/mealsave
cp ~/.claude/skills/mealsave/.env.example ~/.config/mealsave/.env
chmod 600 ~/.config/mealsave/.env
```

Edit `~/.config/mealsave/.env`:
```
MEALIE_URL=http://localhost:9000
MEALIE_TOKEN=<paste your token here>
```

### 3. Create the Python venv and install dependencies

```bash
cd ~/.claude/skills/mealsave
python3 -m venv .venv
.venv/bin/pip install --quiet requests youtube-transcript-api trafilatura lxml_html_clean yt-dlp
```

This installs only what's needed into an isolated venv — nothing system-wide.

### 4. Set up YouTube cookies (required for YouTube URLs on VPS/server IPs)

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

### 4. Smoke test

```bash
~/.claude/skills/mealsave/.venv/bin/python \
  ~/.claude/skills/mealsave/mealsave.py \
  https://www.seriouseats.com/the-best-roast-potatoes-ever-recipe
```

You should see a URL like `http://localhost:9000/g/home/r/the-best-roast-potatoes-ever-recipe`.

---

## Usage

In Claude Code:

```
/mealsave https://www.seriouseats.com/the-best-roast-potatoes-ever-recipe
/mealsave https://www.youtube.com/watch?v=<video-id>
```

Or in natural language:
- "save this recipe: <url>"
- "add to mealie: <url>"

---

## How it works

1. **Mealie scraper first** — `POST /api/recipes/create-url`. Handles most schema.org recipe sites natively and is instant. Sets `orgURL` so you can trace back to the source.
2. **YouTube fallback** — fetches captions via `youtube-transcript-api`, then runs a Claude extraction pass (`claude -p`) to produce structured recipe JSON.
3. **Generic page fallback** — `trafilatura` strips ads/nav to get article text, then Claude extracts the recipe.

If extraction produces zero ingredients *and* zero instructions, the recipe is not saved — you get a clear error instead of junk in Mealie.

Duplicate detection: if a recipe with the same source URL already exists in Mealie, the script surfaces that URL and exits without creating a duplicate.

---

## LLM note

Recipe extraction uses the `claude` CLI (`claude -p`). This is already available in your Claude Code session — no separate API key needed.

---

## TikTok (v2 — not implemented)

TikTok extraction would require:
- `yt-dlp` to download video
- `openai-whisper` for audio transcription
- `tesseract` for OCR on video frames

This is gated as a future v2 feature due to reliability concerns (most cooking TikToks have no captions, recipes are demonstrated visually). Use Mealie's native URL import for any TikTok that has a recipe URL in the description.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Cannot connect to Mealie` | Check `docker ps` — is the mealie container running? |
| `401 Unauthorized` | Regenerate the API token in Mealie UI and update `.env` |
| `No captions available` | The YouTube video has no transcripts — try importing manually in Mealie's UI |
| `LLM extraction produced an empty recipe` | The page text didn't contain a recipe Claude could parse — save manually |
| `claude CLI not found` | Ensure you're running from within a Claude Code session |
