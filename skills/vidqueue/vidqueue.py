#!/usr/bin/env python3
"""vidqueue.py — Extract YouTube video recommendations from a TikTok URL and add to playlist.

Usage:
  python vidqueue.py <tiktok_url>
  python vidqueue.py --auth       # first-run YouTube OAuth setup

Config: ~/.config/vidqueue/.env  (TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, YOUTUBE_PLAYLIST_NAME)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Core utilities
# ---------------------------------------------------------------------------

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
        import warnings
        import whisper
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
    """Extract keyframes via ffmpeg and OCR with tesseract. Returns empty string on failure."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        print("[vidqueue] pytesseract/Pillow not installed, skipping OCR", file=sys.stderr)
        return ""

    frames_dir = os.path.join(tmpdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

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

    all_text = []
    seen: set[str] = set()
    for frame_file in sorted(f for f in os.listdir(frames_dir) if f.endswith(".jpg")):
        try:
            text = pytesseract.image_to_string(Image.open(os.path.join(frames_dir, frame_file)))
            for line in text.splitlines():
                line = line.strip()
                if len(line) > 5 and line not in seen:
                    seen.add(line)
                    all_text.append(line)
        except Exception as e:
            print(f"[vidqueue] OCR failed for {frame_file}: {e}", file=sys.stderr)
    return "\n".join(all_text)


# ---------------------------------------------------------------------------
# YouTube URL utilities
# ---------------------------------------------------------------------------

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
    """Extract YouTube recommendations from TikTok text via LLM. Claude first, Gemini fallback."""
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
        print(f"[vidqueue] Claude failed (rc={result.returncode}), trying Gemini...", file=sys.stderr)
    except Exception as e:
        print(f"[vidqueue] Claude error: {e}, trying Gemini...", file=sys.stderr)

    try:
        result = subprocess.run(
            ["gemini", "-y", "-p", prompt, "-o", "text"],
            capture_output=True, text=True, timeout=120, cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_video_list(result.stdout)
        die(f"Both LLMs failed. Gemini error: {result.stderr.strip()[:200]}")
    except SystemExit:
        raise
    except Exception as e:
        die(f"Both LLMs failed. Gemini exception: {e}")


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
    """Return an authenticated YouTube Data API v3 resource."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
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


# ---------------------------------------------------------------------------
# YouTube search and insertion
# ---------------------------------------------------------------------------

def youtube_search(yt, title: str, channel: str | None) -> str | None:
    """Search YouTube for a video. Returns video ID or None."""
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


# ---------------------------------------------------------------------------
# Main pipeline
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

    # Extraction phase
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

    # LLM extraction
    video_refs = llm_extract_videos(combined_text, "TikTok content")

    # YouTube API
    yt = get_youtube_client()
    playlist_id = get_or_create_playlist(yt, playlist_name)
    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
    print(f"PLAYLIST:{playlist_id}:{playlist_url}")

    existing_ids = get_playlist_video_ids(yt, playlist_id)

    for video_id in direct_video_ids:
        if video_id in existing_ids:
            print(f"SKIPPED:{video_id}:from caption URL")
        else:
            insert_video(yt, playlist_id, video_id)
            existing_ids.add(video_id)
            print(f"ADDED:{video_id}:from caption URL")

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
