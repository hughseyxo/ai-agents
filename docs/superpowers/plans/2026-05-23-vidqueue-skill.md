# vidqueue Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot skill (`skills/vidqueue/`) that receives TikTok links and auto-imports the recommended YouTube video essays into a YouTube playlist.

**Architecture:** A standalone skill mirroring the mealsave pattern. `vidqueue_bot.py` receives TikTok links via Telegram and calls `vidqueue.py` as a subprocess. `vidqueue.py` extracts YouTube recommendations via TikTok caption URLs + OCR + Whisper + LLM, resolves missing video IDs via YouTube search, then adds them to a managed playlist using the YouTube Data API v3.

**Tech Stack:** Python 3.10+, yt-dlp, openai-whisper, pytesseract, ffmpeg, google-api-python-client, google-auth-oauthlib, python-telegram-bot, Claude CLI / Antigravity CLI (LLM fallback)

**Design spec:** `docs/superpowers/specs/2026-05-23-vidqueue-skill-design.md`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `skills/vidqueue/requirements.txt` | Create | Python deps |
| `skills/vidqueue/vidqueue.py` | Create | Core extractor + YouTube API logic |
| `skills/vidqueue/vidqueue_bot.py` | Create | Telegram bot |
| `skills/vidqueue/SKILL.md` | Create | Claude Code skill definition |
| `skills/vidqueue/vidqueue-bot.service` | Create | systemd user service |
| `tests/test_vidqueue.py` | Create | Unit + integration tests |
| `CLAUDE.md` | Modify | Add vidqueue to project structure |

---

## Task 1: Scaffold

**Files:**
- Create: `skills/vidqueue/requirements.txt`
- Create: `tests/test_vidqueue.py`

- [ ] **Step 1: Create skill directory and requirements.txt**

```bash
mkdir -p /home/cian/git/ai-agents/skills/vidqueue
```

Create `skills/vidqueue/requirements.txt`:
```
yt-dlp>=2024.11.18
openai-whisper>=20231117
pytesseract>=0.3.13
Pillow>=10.0.0
python-telegram-bot>=20.7
google-api-python-client>=2.108.0
google-auth-oauthlib>=1.1.0
requests>=2.31.0
```

- [ ] **Step 2: Create stub test file**

Create `tests/test_vidqueue.py`:
```python
"""Tests for vidqueue skill."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "vidqueue"
sys.path.insert(0, str(SKILL_DIR))
```

- [ ] **Step 3: Verify test file is discovered**

Run: `pytest tests/test_vidqueue.py -v`
Expected: `no tests ran` (0 errors, 0 failures)

- [ ] **Step 4: Commit**

```bash
git add skills/vidqueue/requirements.txt tests/test_vidqueue.py
git commit -m "feat: scaffold vidqueue skill directory"
```

---

## Task 2: Core utilities

**Files:**
- Create: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
# ---- Tests run after vidqueue.py is imported ----

class TestLoadConfig:
    def test_dies_when_config_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import vidqueue
        with pytest.raises(SystemExit):
            vidqueue.load_config()

    def test_parses_env_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg_dir = tmp_path / ".config" / "vidqueue"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / ".env").write_text(
            'TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_USER_ID=123\nYOUTUBE_PLAYLIST_NAME=My Queue\n'
        )
        import vidqueue
        cfg = vidqueue.load_config()
        assert cfg["TELEGRAM_BOT_TOKEN"] == "abc"
        assert cfg["YOUTUBE_PLAYLIST_NAME"] == "My Queue"


class TestIsTiktok:
    def test_recognises_standard_url(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://www.tiktok.com/@user/video/123")

    def test_recognises_vm_shortlink(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://vm.tiktok.com/ZABCdef/")

    def test_recognises_vt_shortlink(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://vt.tiktok.com/ZABCdef/")

    def test_rejects_youtube(self):
        import vidqueue
        assert not vidqueue.is_tiktok("https://youtube.com/watch?v=abc")

    def test_rejects_instagram(self):
        import vidqueue
        assert not vidqueue.is_tiktok("https://instagram.com/reel/abc")
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py -v
```
Expected: `ModuleNotFoundError: No module named 'vidqueue'`

- [ ] **Step 3: Create vidqueue.py with core utilities**

Create `skills/vidqueue/vidqueue.py`:
```python
#!/usr/bin/env python3
"""vidqueue.py — Extract YouTube video recommendations from a TikTok URL and add to playlist.

Usage:
  python vidqueue.py <tiktok_url>
  python vidqueue.py --auth       # first-run OAuth setup

Config: ~/.config/vidqueue/.env  (TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, YOUTUBE_PLAYLIST_NAME)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config() -> dict:
    env_path = Path.home() / ".config" / "vidqueue" / ".env"
    if not env_path.exists():
        die(
            f"Config not found at {env_path}. "
            "Create it with TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, "
            "and optionally YOUTUBE_PLAYLIST_NAME."
        )
    config = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                config[key.strip()] = val.strip().strip('"').strip("'")
    return config


def is_tiktok(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^(www\.|m\.|vm\.|vt\.)", "", host)
    return "tiktok.com" in host
```

- [ ] **Step 4: Install venv so imports work**

```bash
cd /home/cian/git/ai-agents/skills/vidqueue
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py -v
```
Expected: 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add vidqueue core utilities (die, load_config, is_tiktok)"
```

---

## Task 3: TikTok extraction functions

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

These are adapted from `skills/mealsave/mealsave.py`. The key difference: `fetch_tiktok_video` returns `None` on failure (non-fatal) rather than calling `die()`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestFetchTiktokMetadata:
    def test_parses_yt_dlp_json(self):
        import vidqueue
        fake = {"title": "Watch these essays", "description": "Must watch: great video essay", "uploader": "creator"}
        with patch("vidqueue.subprocess.run", return_value=_completed(json.dumps(fake))):
            meta = vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/ZABCdef/")
        assert meta["title"] == "Watch these essays"
        assert meta["uploader"] == "creator"

    def test_returns_empty_on_error(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["yt-dlp"])):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/bad/") == {}

    def test_returns_empty_on_timeout(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.TimeoutExpired(["yt-dlp"], 30)):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/slow/") == {}

    def test_returns_empty_on_bad_json(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", return_value=_completed("not-json{")):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/bad/") == {}

    def test_coerces_null_fields_to_empty_string(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", return_value=_completed(
            json.dumps({"title": None, "description": None, "uploader": None})
        )):
            meta = vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/null/")
        assert meta == {"title": "", "description": "", "uploader": ""}


class TestFetchTiktokVideoNonFatal:
    def test_returns_none_on_download_failure(self, tmp_path):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["yt-dlp"])):
            result = vidqueue.fetch_tiktok_video("https://vm.tiktok.com/bad/", str(tmp_path))
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.TimeoutExpired(["yt-dlp"], 120)):
            result = vidqueue.fetch_tiktok_video("https://vm.tiktok.com/slow/", str(tmp_path))
        assert result is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestFetchTiktokMetadata tests/test_vidqueue.py::TestFetchTiktokVideoNonFatal -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute 'fetch_tiktok_metadata'`

- [ ] **Step 3: Add TikTok extraction functions to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after `is_tiktok`):
```python
# ---------------------------------------------------------------------------
# TikTok extraction
# ---------------------------------------------------------------------------

def fetch_tiktok_metadata(url: str) -> dict:
    """Fetch TikTok metadata via yt-dlp. Returns {} on any failure (non-fatal)."""
    base_dir = Path(__file__).parent
    venv_ytdlp = base_dir / ".venv" / "bin" / "yt-dlp"
    ytdlp_bin = str(venv_ytdlp) if venv_ytdlp.exists() else "yt-dlp"
    cmd = [ytdlp_bin, "--dump-json", "--skip-download", "--quiet", "--no-warnings", url]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=30, text=True)
        info = json.loads(result.stdout)
        return {
            "title": info.get("title") or "",
            "description": info.get("description") or "",
            "uploader": info.get("uploader") or "",
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"[vidqueue] TikTok metadata fetch failed (non-fatal): {e}", file=sys.stderr)
        return {}


def fetch_tiktok_video(url: str, tmpdir: str) -> str | None:
    """Download TikTok video via yt-dlp. Returns path or None on failure (non-fatal)."""
    base_dir = Path(__file__).parent
    venv_ytdlp = base_dir / ".venv" / "bin" / "yt-dlp"
    ytdlp_bin = str(venv_ytdlp) if venv_ytdlp.exists() else "yt-dlp"
    output_tmpl = os.path.join(tmpdir, "tiktok.%(ext)s")
    cmd = [ytdlp_bin, "--quiet", "--no-warnings", "--output", output_tmpl, url]
    try:
        subprocess.run(cmd, check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[vidqueue] TikTok download failed (non-fatal): {e}", file=sys.stderr)
        return None
    for f in os.listdir(tmpdir):
        if f.startswith("tiktok."):
            return os.path.join(tmpdir, f)
    print("[vidqueue] TikTok file not found after download (non-fatal)", file=sys.stderr)
    return None


def transcribe_audio(video_path: str) -> str:
    """Transcribe audio using whisper base model. Returns empty string on failure."""
    try:
        import whisper
        import warnings
        warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")
    except ImportError:
        print("[vidqueue] openai-whisper not installed, skipping transcription", file=sys.stderr)
        return ""
    try:
        print("[vidqueue] Transcribing audio with Whisper (base model)...", file=sys.stderr)
        model = whisper.load_model("base")
        result = model.transcribe(video_path)
        return result.get("text", "").strip()
    except Exception as e:
        print(f"[vidqueue] Whisper transcription failed: {e}", file=sys.stderr)
        return ""


def extract_text_from_video(video_path: str, tmpdir: str) -> str:
    """Extract keyframes via ffmpeg and run OCR with tesseract. Returns empty string on failure."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        print("[vidqueue] pytesseract/Pillow not installed, skipping OCR", file=sys.stderr)
        return ""

    frames_dir = os.path.join(tmpdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    print("[vidqueue] Extracting keyframes with ffmpeg...", file=sys.stderr)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", "select='gt(scene,0.1)'",
        "-vsync", "vfr",
        os.path.join(frames_dir, "frame_%04d.jpg"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[vidqueue] ffmpeg frame extraction failed: {e}", file=sys.stderr)
        return ""

    print("[vidqueue] Running OCR on keyframes...", file=sys.stderr)
    all_text = []
    seen_lines: set[str] = set()
    for frame_file in sorted(f for f in os.listdir(frames_dir) if f.endswith(".jpg")):
        try:
            text = pytesseract.image_to_string(Image.open(os.path.join(frames_dir, frame_file)))
            for line in text.splitlines():
                line = line.strip()
                if len(line) > 5 and line not in seen_lines:
                    seen_lines.add(line)
                    all_text.append(line)
        except Exception as e:
            print(f"[vidqueue] OCR failed for {frame_file}: {e}", file=sys.stderr)

    return "\n".join(all_text)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestFetchTiktokMetadata tests/test_vidqueue.py::TestFetchTiktokVideoNonFatal -v
```
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add TikTok extraction functions to vidqueue"
```

---

## Task 4: YouTube URL utilities

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
class TestExtractYoutubeUrls:
    def test_extracts_youtu_be_shortlink(self):
        import vidqueue
        urls = vidqueue.extract_youtube_urls("Check out https://youtu.be/dQw4w9WgXcQ")
        assert urls == ["https://youtu.be/dQw4w9WgXcQ"]

    def test_extracts_full_youtube_url(self):
        import vidqueue
        urls = vidqueue.extract_youtube_urls("https://www.youtube.com/watch?v=9bZkp7q19f0")
        assert urls == ["https://youtu.be/9bZkp7q19f0"]

    def test_extracts_multiple_urls(self):
        import vidqueue
        text = "Watch https://youtu.be/dQw4w9WgXcQ and https://www.youtube.com/watch?v=9bZkp7q19f0"
        urls = vidqueue.extract_youtube_urls(text)
        assert len(urls) == 2

    def test_returns_empty_list_for_no_urls(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("no links here") == []

    def test_ignores_non_youtube_urls(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("https://tiktok.com/abc") == []


class TestGetVideoIdFromUrl:
    def test_extracts_from_youtu_be(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extracts_from_long_form(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://www.youtube.com/watch?v=9bZkp7q19f0") == "9bZkp7q19f0"

    def test_returns_none_for_non_youtube(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://tiktok.com/abc") is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestExtractYoutubeUrls tests/test_vidqueue.py::TestGetVideoIdFromUrl -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute 'extract_youtube_urls'`

- [ ] **Step 3: Add YouTube URL utilities to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after `extract_text_from_video`):
```python
# ---------------------------------------------------------------------------
# YouTube URL utilities
# ---------------------------------------------------------------------------

# Matches both youtu.be/<id> and youtube.com/watch?v=<id>; captures the 11-char ID
_YT_URL_RE = re.compile(
    r'https?://(?:www\.|m\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)'
    r'([A-Za-z0-9_-]{11})'
)


def extract_youtube_urls(text: str) -> list[str]:
    """Return canonical youtu.be URLs for every YouTube video ID found in text."""
    return [f"https://youtu.be/{m}" for m in _YT_URL_RE.findall(text)]


def get_video_id_from_url(url: str) -> str | None:
    """Extract 11-char video ID from a YouTube URL. Returns None if not found."""
    m = _YT_URL_RE.search(url)
    return m.group(1) if m else None
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestExtractYoutubeUrls tests/test_vidqueue.py::TestGetVideoIdFromUrl -v
```
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add YouTube URL extraction utilities to vidqueue"
```

---

## Task 5: LLM video extraction

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
class TestParseVideoList:
    def test_parses_valid_json_array(self):
        import vidqueue
        raw = '[{"title": "Why Kubrick Matters", "channel": "Some Channel", "youtube_url": "https://youtu.be/abc123"}]'
        result = vidqueue._parse_video_list(raw)
        assert len(result) == 1
        assert result[0]["title"] == "Why Kubrick Matters"
        assert result[0]["channel"] == "Some Channel"
        assert result[0]["youtube_url"] == "https://youtu.be/abc123"

    def test_strips_markdown_fences(self):
        import vidqueue
        raw = '```json\n[{"title": "Test Video", "channel": null, "youtube_url": null}]\n```'
        result = vidqueue._parse_video_list(raw)
        assert result[0]["title"] == "Test Video"
        assert result[0]["channel"] is None

    def test_returns_empty_list_on_bad_json(self):
        import vidqueue
        assert vidqueue._parse_video_list("not json at all") == []

    def test_filters_items_without_title(self):
        import vidqueue
        raw = '[{"title": "", "channel": "X"}, {"title": "Real Video", "channel": null, "youtube_url": null}]'
        result = vidqueue._parse_video_list(raw)
        assert len(result) == 1
        assert result[0]["title"] == "Real Video"

    def test_coerces_null_channel_to_none(self):
        import vidqueue
        raw = '[{"title": "Essay", "channel": null, "youtube_url": null}]'
        result = vidqueue._parse_video_list(raw)
        assert result[0]["channel"] is None


class TestLlmExtractVideos:
    def test_returns_parsed_list_on_claude_success(self):
        import vidqueue
        raw = '[{"title": "Philosophy of Inception", "channel": "Nerdwriter1", "youtube_url": null}]'
        with patch("vidqueue.subprocess.run", return_value=_completed(raw)):
            result = vidqueue.llm_extract_videos("some tiktok text", "TikTok caption")
        assert result[0]["title"] == "Philosophy of Inception"

    def test_falls_back_to_antigravity_on_claude_failure(self):
        import vidqueue
        raw = '[{"title": "Fallback Video", "channel": null, "youtube_url": null}]'
        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # Claude call
                return _completed("", returncode=1)
            return _completed(raw)  # Antigravity call

        with patch("vidqueue.subprocess.run", side_effect=fake_run):
            result = vidqueue.llm_extract_videos("text")
        assert result[0]["title"] == "Fallback Video"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestParseVideoList tests/test_vidqueue.py::TestLlmExtractVideos -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute '_parse_video_list'`

- [ ] **Step 3: Add LLM video extraction to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after YouTube URL utilities):
```python
# ---------------------------------------------------------------------------
# LLM video extraction
# ---------------------------------------------------------------------------

_VIDQUEUE_LLM_PROMPT = """\
You are analyzing TikTok content that recommends YouTube videos to watch.
Extract ALL YouTube video recommendations from the text below{hint}.

Return ONLY a valid JSON array. Each item must have:
{{
  "title": "Full video title as mentioned",
  "channel": "Channel or creator name, or null if not mentioned",
  "youtube_url": "Full YouTube URL if present in text, or null"
}}

Rules:
- Include every recommended video mentioned, even vaguely
- Do NOT invent videos not mentioned in the source text
- If a YouTube URL is present in the source, always include it in youtube_url
- Return ONLY the JSON array — no markdown fences, no explanation

TEXT:
{text}"""


def _parse_video_list(output: str) -> list[dict]:
    output = re.sub(r"^```(?:json)?\s*", "", output, flags=re.MULTILINE)
    output = re.sub(r"\s*```\s*$", "", output, flags=re.MULTILINE)
    output = output.strip()
    match = re.search(r"\[.*\]", output, re.DOTALL)
    if match:
        output = match.group(0)
    try:
        data = json.loads(output)
        if not isinstance(data, list):
            return []
        return [
            {
                "title": str(item.get("title") or ""),
                "channel": item.get("channel") or None,
                "youtube_url": item.get("youtube_url") or None,
            }
            for item in data
            if isinstance(item, dict) and item.get("title")
        ]
    except json.JSONDecodeError:
        return []


def llm_extract_videos(text: str, source_hint: str = "") -> list[dict]:
    """Extract YouTube video recommendations from combined TikTok text via LLM.

    Returns list of dicts: [{title, channel, youtube_url}].
    Claude is tried first; Antigravity is the fallback. Dies if both fail.
    """
    hint = f" ({source_hint})" if source_hint else ""
    prompt = _VIDQUEUE_LLM_PROMPT.format(hint=hint, text=text[:8000])

    try:
        print("[vidqueue] Extracting recommendations with Claude Sonnet...", file=sys.stderr)
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "--model", "sonnet", "-p", prompt],
            capture_output=True, text=True, timeout=120, cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_video_list(result.stdout)
        print(f"[vidqueue] Claude failed (rc={result.returncode}), trying Antigravity...", file=sys.stderr)
    except Exception as e:
        print(f"[vidqueue] Claude error: {e}, trying Antigravity...", file=sys.stderr)

    try:
        result = subprocess.run(
            ["antigravity", "-y", "-p", prompt, "-o", "text"],
            capture_output=True, text=True, timeout=120, cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_video_list(result.stdout)
        die(f"Both LLMs failed. Antigravity error: {result.stderr.strip()[:200]}")
    except SystemExit:
        raise
    except Exception as e:
        die(f"Both LLMs failed. Antigravity exception: {e}")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestParseVideoList tests/test_vidqueue.py::TestLlmExtractVideos -v
```
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add LLM video extraction to vidqueue"
```

---

## Task 6: YouTube OAuth client

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
class TestYoutubeAuth:
    def test_dies_when_no_token_and_no_credentials(self, tmp_path, monkeypatch):
        import vidqueue
        monkeypatch.setattr(vidqueue, "_token_path", lambda: tmp_path / "token.json")
        monkeypatch.setattr(vidqueue, "_credentials_path", lambda: tmp_path / "missing_credentials.json")
        with pytest.raises(SystemExit):
            vidqueue.get_youtube_client()

    def test_token_path_is_in_config_dir(self):
        import vidqueue
        path = vidqueue._token_path()
        assert ".config/vidqueue/youtube_token.json" in str(path)

    def test_credentials_path_points_to_repo_root(self):
        import vidqueue
        path = vidqueue._credentials_path()
        assert path.name == "credentials.json"
        assert "ai-agents" in str(path)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestYoutubeAuth -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute '_token_path'`

- [ ] **Step 3: Add YouTube OAuth client to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after LLM extraction):
```python
# ---------------------------------------------------------------------------
# YouTube OAuth client
# ---------------------------------------------------------------------------

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def _token_path() -> Path:
    return Path.home() / ".config" / "vidqueue" / "youtube_token.json"


def _credentials_path() -> Path:
    # skills/vidqueue/vidqueue.py -> parents[2] = repo root (ai-agents/)
    return Path(__file__).resolve().parents[2] / "credentials.json"


def get_youtube_client():
    """Return an authenticated YouTube Data API v3 resource.

    Uses token at ~/.config/vidqueue/youtube_token.json. On first run (or expired
    token with no refresh), opens a browser OAuth flow. Call `vidqueue.py --auth`
    to trigger this manually.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        die("google-api-python-client / google-auth-oauthlib not installed. Run: .venv/bin/pip install -r requirements.txt")

    token_path = _token_path()
    creds_path = _credentials_path()

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                die(
                    f"credentials.json not found at {creds_path}. "
                    "Add youtube.force-ssl scope in Google Cloud Console, then run: "
                    "python vidqueue.py --auth"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestYoutubeAuth -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add YouTube OAuth client to vidqueue"
```

---

## Task 7: Playlist management

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
class TestGetOrCreatePlaylist:
    def test_returns_existing_playlist_id(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLabc123", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        result = vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations")
        assert result == "PLabc123"
        mock_yt.playlists().insert.assert_not_called()

    def test_creates_playlist_when_missing(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {"items": []}
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLnew456"}
        result = vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations")
        assert result == "PLnew456"
        mock_yt.playlists().insert.assert_called_once()

    def test_does_not_match_different_playlist_name(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLother", "snippet": {"title": "Other Playlist"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLcreated"}
        result = vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations")
        assert result == "PLcreated"


class TestGetPlaylistVideoIds:
    def test_returns_set_of_video_ids(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().list().execute.return_value = {
            "items": [
                {"contentDetails": {"videoId": "abc123"}},
                {"contentDetails": {"videoId": "def456"}},
            ]
        }
        mock_yt.playlistItems().list_next.return_value = None
        result = vidqueue.get_playlist_video_ids(mock_yt, "PLabc123")
        assert result == {"abc123", "def456"}

    def test_returns_empty_set_for_empty_playlist(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        result = vidqueue.get_playlist_video_ids(mock_yt, "PLempty")
        assert result == set()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestGetOrCreatePlaylist tests/test_vidqueue.py::TestGetPlaylistVideoIds -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute 'get_or_create_playlist'`

- [ ] **Step 3: Add playlist management functions to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after YouTube OAuth client):
```python
# ---------------------------------------------------------------------------
# Playlist management
# ---------------------------------------------------------------------------

def get_or_create_playlist(yt, name: str) -> str:
    """Return playlist ID for `name` (mine=True). Creates it if it doesn't exist."""
    request = yt.playlists().list(part="snippet", mine=True, maxResults=50)
    while request:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"] == name:
                return item["id"]
        request = yt.playlists().list_next(request, response)

    response = yt.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": name,
                "description": "YouTube videos recommended in TikToks",
            },
            "status": {"privacyStatus": "private"},
        },
    ).execute()
    return response["id"]


def get_playlist_video_ids(yt, playlist_id: str) -> set[str]:
    """Return set of video IDs currently in the playlist (for dedup)."""
    video_ids: set[str] = set()
    request = yt.playlistItems().list(
        part="contentDetails", playlistId=playlist_id, maxResults=50
    )
    while request:
        response = request.execute()
        for item in response.get("items", []):
            video_ids.add(item["contentDetails"]["videoId"])
        request = yt.playlistItems().list_next(request, response)
    return video_ids
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestGetOrCreatePlaylist tests/test_vidqueue.py::TestGetPlaylistVideoIds -v
```
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add playlist management to vidqueue"
```

---

## Task 8: YouTube search and video insertion

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
class TestYoutubeSearch:
    def test_returns_video_id_on_hit(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {
            "items": [{"id": {"videoId": "xyz789"}}]
        }
        result = vidqueue.youtube_search(mock_yt, "Why Kubrick Matters", "Some Channel")
        assert result == "xyz789"

    def test_returns_none_on_empty_results(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {"items": []}
        result = vidqueue.youtube_search(mock_yt, "Obscure Video", None)
        assert result is None

    def test_returns_none_on_api_exception(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.side_effect = Exception("quota exceeded")
        result = vidqueue.youtube_search(mock_yt, "Some Video", None)
        assert result is None

    def test_includes_channel_in_query(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {"items": []}
        vidqueue.youtube_search(mock_yt, "Essay Title", "Nerdwriter1")
        call_kwargs = mock_yt.search().list.call_args[1]
        assert "Nerdwriter1" in call_kwargs["q"]


class TestInsertVideo:
    def test_calls_playlist_items_insert(self):
        import vidqueue
        mock_yt = MagicMock()
        vidqueue.insert_video(mock_yt, "PLabc123", "vid999")
        mock_yt.playlistItems().insert.assert_called_once()
        body = mock_yt.playlistItems().insert.call_args[1]["body"]
        assert body["snippet"]["playlistId"] == "PLabc123"
        assert body["snippet"]["resourceId"]["videoId"] == "vid999"
        assert body["snippet"]["resourceId"]["kind"] == "youtube#video"

    def test_dies_on_api_exception(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().insert().execute.side_effect = Exception("API error")
        with pytest.raises(SystemExit):
            vidqueue.insert_video(mock_yt, "PLabc123", "vid999")
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestYoutubeSearch tests/test_vidqueue.py::TestInsertVideo -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute 'youtube_search'`

- [ ] **Step 3: Add search and insert functions to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (after playlist management):
```python
# ---------------------------------------------------------------------------
# YouTube search and insertion
# ---------------------------------------------------------------------------

def youtube_search(yt, title: str, channel: str | None) -> str | None:
    """Search YouTube for a video. Returns video ID or None on no results / API error."""
    q = f"{title} {channel}" if channel else title
    try:
        response = yt.search().list(part="snippet", q=q, type="video", maxResults=1).execute()
        items = response.get("items", [])
        if items:
            return items[0]["id"]["videoId"]
    except Exception as e:
        print(f"[vidqueue] YouTube search failed for '{q}': {e}", file=sys.stderr)
    return None


def insert_video(yt, playlist_id: str, video_id: str):
    """Add video_id to playlist_id. Calls die() on API error."""
    try:
        yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
    except Exception as e:
        die(f"Could not add video {video_id} to playlist: {e}")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestYoutubeSearch tests/test_vidqueue.py::TestInsertVideo -v
```
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add YouTube search and insertion to vidqueue"
```

---

## Task 9: Main pipeline

**Files:**
- Modify: `skills/vidqueue/vidqueue.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_vidqueue.py`:
```python
class TestMainPipeline:
    def test_adds_video_from_caption_url(self, capsys, monkeypatch):
        import vidqueue

        monkeypatch.setattr(vidqueue, "load_config", lambda: {"YOUTUBE_PLAYLIST_NAME": "TikTok Recommendations"})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {
            "title": "Watch these!",
            "uploader": "creator",
            "description": "Great essay https://youtu.be/dQw4w9WgXcQ",
        })
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [])

        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLtest", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)

        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/123"]
        vidqueue.main()

        captured = capsys.readouterr()
        assert "ADDED:dQw4w9WgXcQ:" in captured.out
        assert "PLAYLIST:PLtest:" in captured.out

    def test_skips_already_queued_video(self, capsys, monkeypatch):
        import vidqueue

        monkeypatch.setattr(vidqueue, "load_config", lambda: {"YOUTUBE_PLAYLIST_NAME": "TikTok Recommendations"})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {
            "title": "Watch these!", "uploader": "creator",
            "description": "Already queued https://youtu.be/existing1",
        })
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [])

        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLtest", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlistItems().list().execute.return_value = {
            "items": [{"contentDetails": {"videoId": "existing1"}}]
        }
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)

        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/456"]
        vidqueue.main()

        captured = capsys.readouterr()
        assert "SKIPPED:existing1:" in captured.out
        mock_yt.playlistItems().insert.assert_not_called()

    def test_marks_unresolvable_title_as_unresolved(self, capsys, monkeypatch):
        import vidqueue

        monkeypatch.setattr(vidqueue, "load_config", lambda: {})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {"title": "T", "uploader": "u", "description": ""})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [
            {"title": "Obscure Essay", "channel": None, "youtube_url": None}
        ])
        monkeypatch.setattr(vidqueue, "youtube_search", lambda yt, title, channel: None)

        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {"items": []}
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLnew"}
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)

        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/789"]
        vidqueue.main()

        captured = capsys.readouterr()
        assert "UNRESOLVED:Obscure Essay" in captured.out
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestMainPipeline -v
```
Expected: `AttributeError: module 'vidqueue' has no attribute 'main'`

- [ ] **Step 3: Add main() to vidqueue.py**

Append to `skills/vidqueue/vidqueue.py` (at end of file):
```python
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="Extract YouTube recommendations from a TikTok and add to playlist")
    parser.add_argument("url", nargs="?", help="TikTok URL to process")
    parser.add_argument("--auth", action="store_true", help="Run YouTube OAuth flow and exit")
    args = parser.parse_args()

    if args.auth:
        get_youtube_client()
        print("YouTube auth successful. Token saved to ~/.config/vidqueue/youtube_token.json")
        sys.exit(0)

    if not args.url:
        die("Usage: vidqueue.py <tiktok_url>  or  vidqueue.py --auth")

    url = args.url
    if not is_tiktok(url):
        die(f"Not a TikTok URL: {url}")

    config = load_config()
    playlist_name = config.get("YOUTUBE_PLAYLIST_NAME", "TikTok Recommendations")

    # --- Extraction phase ---
    meta = fetch_tiktok_metadata(url)
    caption_text = ""
    direct_video_ids: list[str] = []

    if meta:
        caption_text = (
            f"TITLE: {meta['title']}\n"
            f"UPLOADER: {meta['uploader']}\n"
            f"DESCRIPTION:\n{meta['description']}"
        )
        for yt_url in extract_youtube_urls(meta.get("description", "")):
            vid_id = get_video_id_from_url(yt_url)
            if vid_id and vid_id not in direct_video_ids:
                direct_video_ids.append(vid_id)

    ocr_text = ""
    transcript = ""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = fetch_tiktok_video(url, tmpdir)
        if video_path:
            ocr_text = extract_text_from_video(video_path, tmpdir)
            transcript = transcribe_audio(video_path)

    combined_text = "\n\n".join(filter(None, [
        caption_text,
        f"AUDIO TRANSCRIPT:\n{transcript}" if transcript.strip() else "",
        f"VIDEO OCR TEXT:\n{ocr_text}" if ocr_text.strip() else "",
    ]))

    if not combined_text.strip():
        die("No content could be extracted from this TikTok.")

    # --- LLM extraction ---
    video_refs = llm_extract_videos(combined_text, "TikTok content")

    # --- YouTube API ---
    yt = get_youtube_client()
    playlist_id = get_or_create_playlist(yt, playlist_name)
    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
    print(f"PLAYLIST:{playlist_id}:{playlist_url}")

    existing_ids = get_playlist_video_ids(yt, playlist_id)

    # Process videos found directly in caption (no search needed)
    for video_id in direct_video_ids:
        if video_id in existing_ids:
            print(f"SKIPPED:{video_id}:from caption URL")
        else:
            insert_video(yt, playlist_id, video_id)
            existing_ids.add(video_id)
            print(f"ADDED:{video_id}:from caption URL")

    # Process LLM-extracted recommendations
    for ref in video_refs:
        title = ref["title"]
        channel = ref.get("channel")
        yt_url = ref.get("youtube_url")

        video_id = None
        if yt_url:
            video_id = get_video_id_from_url(yt_url)
        if not video_id:
            video_id = youtube_search(yt, title, channel)

        if not video_id:
            print(f"UNRESOLVED:{title}")
            continue

        if video_id in existing_ids:
            print(f"SKIPPED:{video_id}:{title}")
        else:
            insert_video(yt, playlist_id, video_id)
            existing_ids.add(video_id)
            print(f"ADDED:{video_id}:{title}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
pytest tests/test_vidqueue.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/vidqueue.py tests/test_vidqueue.py
git commit -m "feat: add main pipeline to vidqueue"
```

---

## Task 10: Telegram bot

**Files:**
- Create: `skills/vidqueue/vidqueue_bot.py`
- Modify: `tests/test_vidqueue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vidqueue.py`:
```python
BOT_DIR = Path(__file__).resolve().parent.parent / "skills" / "vidqueue"
sys.path.insert(0, str(BOT_DIR))


class TestParseVidqueueOutput:
    def test_formats_added_videos(self):
        import vidqueue_bot
        stdout = (
            "PLAYLIST:PLtest123:https://www.youtube.com/playlist?list=PLtest123\n"
            "ADDED:dQw4w9WgXcQ:Rick Astley Never Gonna Give You Up\n"
        )
        msg = vidqueue_bot.parse_vidqueue_output(stdout)
        assert "✅ Added 1 video" in msg
        assert "Rick Astley" in msg
        assert "youtu.be/dQw4w9WgXcQ" in msg
        assert "youtube.com/playlist" in msg

    def test_formats_skipped_and_unresolved(self):
        import vidqueue_bot
        stdout = (
            "PLAYLIST:PL1:https://www.youtube.com/playlist?list=PL1\n"
            "ADDED:abc123:Some Video\n"
            "SKIPPED:def456:Already There\n"
            "UNRESOLVED:Vague Title Without Channel\n"
        )
        msg = vidqueue_bot.parse_vidqueue_output(stdout)
        assert "⏭️ Already in playlist: 1" in msg
        assert "❓ Couldn't resolve 1 title" in msg
        assert "Vague Title Without Channel" in msg

    def test_returns_no_recommendations_message_for_empty_output(self):
        import vidqueue_bot
        msg = vidqueue_bot.parse_vidqueue_output("")
        assert "No YouTube recommendations" in msg

    def test_plural_titles_message(self):
        import vidqueue_bot
        stdout = "PLAYLIST:PL1:https://x\nUNRESOLVED:Title A\nUNRESOLVED:Title B\n"
        msg = vidqueue_bot.parse_vidqueue_output(stdout)
        assert "2 titles" in msg
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_vidqueue.py::TestParseVidqueueOutput -v
```
Expected: `ModuleNotFoundError: No module named 'vidqueue_bot'`

- [ ] **Step 3: Create vidqueue_bot.py**

Create `skills/vidqueue/vidqueue_bot.py`:
```python
#!/usr/bin/env python3
"""Vidqueue Telegram Bot.

Send a TikTok link and get YouTube video essay recommendations saved to your playlist.
Locked to a single Telegram user ID for security.
"""

import re
import subprocess
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


def load_bot_config() -> dict:
    env_path = Path.home() / ".config" / "vidqueue" / ".env"
    if not env_path.exists():
        print(f"Error: Config not found at {env_path}", file=sys.stderr)
        sys.exit(1)
    config = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                config[key.strip()] = val.strip().strip('"').strip("'")
    return config


CONFIG = load_bot_config()
TOKEN = CONFIG.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(CONFIG.get("TELEGRAM_USER_ID", 0))
VIDQUEUE_PY = Path(__file__).parent / "vidqueue.py"
VENV_PYTHON = Path(__file__).parent / ".venv" / "bin" / "python"

if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not set in ~/.config/vidqueue/.env", file=sys.stderr)
    sys.exit(1)
if not ALLOWED_USER_ID:
    print("Error: TELEGRAM_USER_ID not set in ~/.config/vidqueue/.env", file=sys.stderr)
    sys.exit(1)


def authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID


def extract_urls(text: str) -> list[str]:
    return re.findall(r'(https?://[^\s]+)', text)


def parse_vidqueue_output(stdout: str) -> str:
    """Parse structured stdout lines from vidqueue.py into a Telegram message."""
    added: list[str] = []
    skipped: list[str] = []
    unresolved: list[str] = []
    playlist_url: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("ADDED:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                video_id, title = parts[1], parts[2]
                added.append(f"• {title} → youtu.be/{video_id}")
        elif line.startswith("SKIPPED:"):
            skipped.append(line)
        elif line.startswith("UNRESOLVED:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                unresolved.append(f'  • "{parts[1]}" — search manually')
        elif line.startswith("PLAYLIST:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                playlist_url = parts[2]

    if not added and not unresolved:
        return "No YouTube recommendations found in this TikTok."

    lines: list[str] = []
    if added:
        count = len(added)
        lines.append(f"✅ Added {count} video{'s' if count != 1 else ''}:")
        lines.extend(added)
    if skipped:
        lines.append(f"\n⏭️ Already in playlist: {len(skipped)}")
    if unresolved:
        count = len(unresolved)
        lines.append(f"\n❓ Couldn't resolve {count} title{'s' if count != 1 else ''}:")
        lines.extend(unresolved)
    if playlist_url:
        lines.append(f"\n📋 {playlist_url}")

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Vidqueue Bot ready. Send me a TikTok link and I'll add the recommended "
        "YouTube video essays to your playlist."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    text = update.message.text or update.message.caption
    if not text:
        return

    urls = extract_urls(text)
    if not urls:
        return

    for url in urls:
        status_msg = await update.message.reply_text(f"Processing: {url}...")
        try:
            cmd = [str(VENV_PYTHON), str(VIDQUEUE_PY), url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                reply = parse_vidqueue_output(result.stdout)
                await status_msg.edit_text(reply)
            else:
                combined = f"{result.stdout}\n{result.stderr}".strip()
                error_match = re.search(r'^ERROR:\s*(.*)$', combined, re.MULTILINE)
                if error_match:
                    error_msg = error_match.group(1).strip()
                else:
                    error_msg = combined[-400:].strip()
                await status_msg.edit_text(f"❌ Error:\n{error_msg}")

        except subprocess.TimeoutExpired:
            await status_msg.edit_text("❌ Timed out (> 5 minutes).")
        except Exception as e:
            await status_msg.edit_text(f"❌ Unexpected error: {e}")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message))
    print("Vidqueue Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_vidqueue.py::TestParseVidqueueOutput -v
```
Expected: 4 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_vidqueue.py -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add skills/vidqueue/vidqueue_bot.py tests/test_vidqueue.py
git commit -m "feat: add Telegram bot for vidqueue"
```

---

## Task 11: SKILL.md, systemd service, and CLAUDE.md update

**Files:**
- Create: `skills/vidqueue/SKILL.md`
- Create: `skills/vidqueue/vidqueue-bot.service`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create SKILL.md**

Create `skills/vidqueue/SKILL.md`:
```markdown
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

## Auth Bootstrap (first run only)

```bash
/home/cian/git/ai-agents/skills/vidqueue/.venv/bin/python /home/cian/git/ai-agents/skills/vidqueue/vidqueue.py --auth
```

Opens browser → grant YouTube access → token saved to `~/.config/vidqueue/youtube_token.json`.

## What the Script Does

1. Reads `~/.config/vidqueue/.env` for `YOUTUBE_PLAYLIST_NAME` (default: "TikTok Recommendations")
2. Fetches TikTok metadata via yt-dlp; scans description for direct YouTube URLs
3. Downloads video/slideshow; runs ffmpeg keyframe OCR (Tesseract) + Whisper transcription
4. Sends all extracted text to Claude Sonnet (Antigravity fallback) → list of `{title, channel, youtube_url}`
5. Searches YouTube Data API for titles without a direct URL
6. Creates playlist if it doesn't exist; skips videos already in playlist
7. Inserts new videos; prints structured output lines
```

- [ ] **Step 2: Create systemd service file**

Create `skills/vidqueue/vidqueue-bot.service`:
```ini
[Unit]
Description=Vidqueue Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/cian/git/ai-agents/skills/vidqueue
ExecStart=/home/cian/git/ai-agents/skills/vidqueue/.venv/bin/python -u /home/cian/git/ai-agents/skills/vidqueue/vidqueue_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Update CLAUDE.md project structure**

In `CLAUDE.md`, add `vidqueue.py` under the `skills/` section and add the service file entry. Find the `skills/` section and add:

```
│   └── vidqueue/               # Queue YouTube video essays from TikTok recommendations
│       ├── SKILL.md            # Skill definition (trigger: /vidqueue <url>)
│       ├── vidqueue.py         # Core: TikTok extraction + YouTube Data API playlist management
│       ├── vidqueue_bot.py     # Telegram bot (same pattern as mealsave_bot.py)
│       └── vidqueue-bot.service # systemd user service
```

Also add to the Skills section at the bottom:
```
- `vidqueue` — queue YouTube video essays from TikTok recommendations (`/vidqueue <url>`)
```

- [ ] **Step 4: Symlink and enable the service**

```bash
ln -sf /home/cian/git/ai-agents/skills/vidqueue/vidqueue-bot.service ~/.config/systemd/user/vidqueue-bot.service
systemctl --user daemon-reload
systemctl --user enable vidqueue-bot.service
```

- [ ] **Step 5: Commit**

```bash
git add skills/vidqueue/SKILL.md skills/vidqueue/vidqueue-bot.service CLAUDE.md
git commit -m "feat: add SKILL.md, systemd service, and CLAUDE.md entry for vidqueue"
```

---

## Task 12: README and setup guide

**Files:**
- Create: `skills/vidqueue/README.md`

- [ ] **Step 1: Create README.md**

Create `skills/vidqueue/README.md`:
```markdown
# vidqueue

Sends TikTok video/slideshow links to a Telegram bot → extracts YouTube video essay recommendations → adds them to a YouTube playlist.

**Supports:**
- TikTok captions/descriptions with direct YouTube links
- Slideshows (OCR via Tesseract on keyframes)
- TikTok videos (Whisper audio transcription)
- LLM synthesis → YouTube Data API search for unresolved titles

---

## Setup

### 1. System dependencies (likely already installed for mealsave)

```bash
sudo apt install ffmpeg tesseract-ocr libtesseract-dev
```

### 2. Create the Python venv

```bash
cd ~/git/ai-agents/skills/vidqueue
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Enable YouTube Data API and add scope to existing credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → your existing project
2. APIs & Services → Library → search "YouTube Data API v3" → Enable
3. APIs & Services → OAuth consent screen → Add scope: `youtube.force-ssl`
4. (No new credentials.json needed — the existing `~/git/ai-agents/credentials.json` is reused)

### 4. Create config file

```bash
mkdir -p ~/.config/vidqueue
cat > ~/.config/vidqueue/.env << 'EOF'
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
TELEGRAM_USER_ID=YOUR_TELEGRAM_USER_ID_HERE
YOUTUBE_PLAYLIST_NAME=TikTok Recommendations
EOF
chmod 600 ~/.config/vidqueue/.env
```

### 5. Run first-time OAuth (opens browser)

```bash
.venv/bin/python vidqueue.py --auth
```

### 6. Test manually

```bash
.venv/bin/python vidqueue.py "https://www.tiktok.com/@someuser/video/123456789"
```

### 7. Enable the Telegram bot service

```bash
ln -sf ~/git/ai-agents/skills/vidqueue/vidqueue-bot.service ~/.config/systemd/user/vidqueue-bot.service
systemctl --user daemon-reload
systemctl --user enable --now vidqueue-bot.service
systemctl --user status vidqueue-bot.service
```

---

## Usage

Send any TikTok link to the bot. It replies:

```
✅ Added 3 videos:
• The Philosophy of Inception → youtu.be/abc123
• Why Kubrick Matters → youtu.be/def456
• Every Frame a Painting → youtu.be/ghi789

⏭️ Already in playlist: 1
❓ Couldn't resolve 1 title:
  • "Vague Essay Name" — search manually

📋 https://www.youtube.com/playlist?list=PLyourplaylist
```

## Troubleshooting

- **"YouTube auth failed"** → Run `.venv/bin/python vidqueue.py --auth` to refresh token
- **"No content could be extracted"** → yt-dlp may be blocked; try updating: `.venv/bin/pip install -U yt-dlp`
- **Bot offline** → Check: `systemctl --user status vidqueue-bot.service` and `loginctl enable-linger $USER`
```

- [ ] **Step 2: Commit**

```bash
git add skills/vidqueue/README.md
git commit -m "docs: add vidqueue README with setup instructions"
```

---

## Verification

**Unit tests:**
```bash
pytest tests/test_vidqueue.py -v
```
Expected: all tests pass, no failures.

**Manual smoke test:**
```bash
cd ~/git/ai-agents/skills/vidqueue
.venv/bin/python vidqueue.py --auth   # first run only
.venv/bin/python vidqueue.py "https://www.tiktok.com/@someuser/video/REALID"
```
Expected: `PLAYLIST:...` line + `ADDED:...` or `UNRESOLVED:...` lines.

**Bot test:**
1. `systemctl --user start vidqueue-bot.service`
2. Send a TikTok link to the bot from Telegram
3. Bot replies with formatted summary within 5 minutes
4. Check YouTube playlist contains the added videos
