# vidqueue

Extracts YouTube video essay recommendations from a TikTok link and adds them to a YouTube playlist.

**Mobile use:** Send a TikTok link to the concierge Telegram bot — it calls `queue_tiktok` automatically.

**Supports:**
- TikTok captions/descriptions with direct YouTube links (fast path, no download needed)
- Slideshows (OCR via Tesseract on keyframes)
- Videos (Whisper audio transcription)
- LLM synthesis → YouTube Data API search for unresolved titles

---

## Setup

### 1. System dependencies

```bash
sudo apt install ffmpeg tesseract-ocr libtesseract-dev
```

### 2. Python venv

```bash
cd ~/git/ai-agents/skills/vidqueue
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Enable YouTube Data API and add scope

1. [Google Cloud Console](https://console.cloud.google.com/) → your existing project (same one used for Calendar/Gmail)
2. APIs & Services → Library → "YouTube Data API v3" → Enable
3. APIs & Services → OAuth consent screen → Add scope: `youtube.force-ssl`

### 4. Config file

```bash
mkdir -p ~/.config/vidqueue
cat > ~/.config/vidqueue/.env << 'EOF'
YOUTUBE_PLAYLIST_NAME=TikTok Recommendations
EOF
chmod 600 ~/.config/vidqueue/.env
```

### 5. First-run OAuth

```bash
.venv/bin/python vidqueue.py --auth
```

Opens browser → grant YouTube access → token saved to `~/.config/vidqueue/youtube_token.json`.

### 6. Test

```bash
.venv/bin/python vidqueue.py "https://www.tiktok.com/@someuser/video/123456789"
```

---

## Output format

```
PLAYLIST:<id>:<url>
ADDED:<video_id>:<title>
SKIPPED:<video_id>:<title>
UNRESOLVED:<title>
```

## Concierge bot integration

`telegram-bot/tools.py` has `queue_tiktok(url)` which calls this script and formats the result. No separate bot service needed.

## Troubleshooting

- **"YouTube auth failed"** → `.venv/bin/python vidqueue.py --auth`
- **"No content extracted"** → yt-dlp may be stale: `.venv/bin/pip install -U yt-dlp`
- **Whisper/OCR slow** → Expected; only triggered when caption has no direct YouTube URLs
