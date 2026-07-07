#!/usr/bin/env python3
"""
ytsave.py — Extract YouTube video recommendations from a TikTok photo
slideshow and add them to a fixed YouTube playlist.
Usage: python ytsave.py <tiktok_url>

Config:  ~/.config/ytsave/config.json  (playlist_id cache)
Auth:    ~/.config/ytsave/browser.json (ytmusicapi browser auth — see README.md)
Search:  yt-dlp ytsearch (no API key, no quota)
LLM:     agy CLI first, claude CLI fallback (title extraction + match verification)
Vision:  claude CLI (Read tool) fallback when OCR yields no titles
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

CONFIG_DIR = Path.home() / ".config" / "ytsave"
CONFIG_PATH = CONFIG_DIR / "config.json"
BROWSER_AUTH_PATH = CONFIG_DIR / "browser.json"
DEFAULT_PLAYLIST_NAME = "TikTok Finds"
TIKWM_API = "https://www.tikwm.com/api/"


# ---------------------------------------------------------------------------
# Config + generic helpers
# ---------------------------------------------------------------------------

def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"playlist_name": DEFAULT_PLAYLIST_NAME, "playlist_id": None}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        return {"playlist_name": DEFAULT_PLAYLIST_NAME, "playlist_id": None}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _parse_json_response(output: str):
    """Strip markdown fences / leading prose and parse the first JSON value."""
    output = re.sub(r"^```(?:json)?\s*", "", output, flags=re.MULTILINE)
    output = re.sub(r"\s*```\s*$", "", output, flags=re.MULTILINE)
    output = output.strip()

    match = re.search(r"(\{.*\}|\[.*\])", output, re.DOTALL)
    if match:
        output = match.group(0)

    return json.loads(output)  # raises json.JSONDecodeError on failure


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

def is_tiktok(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^(www\.|m\.|vm\.|vt\.)", "", host)
    return host == "tiktok.com" or host.endswith(".tiktok.com")


# ---------------------------------------------------------------------------
# TikTok metadata + slideshow image extraction (yt-dlp)
# ---------------------------------------------------------------------------

def run_ytdlp_dump_json(url: str) -> dict:
    """Run yt-dlp --dump-json against a TikTok URL. Returns {} on any failure."""
    base_dir = Path(__file__).parent
    venv_ytdlp = base_dir / ".venv" / "bin" / "yt-dlp"
    ytdlp_bin = str(venv_ytdlp) if venv_ytdlp.exists() else "yt-dlp"
    cmd = [ytdlp_bin, "--dump-json", "--skip-download", "--quiet", "--no-warnings", "--", url]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=30, text=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"[ytsave] yt-dlp dump-json failed (non-fatal): {e}", file=sys.stderr)
        return {}


def extract_caption(info: dict) -> dict:
    """Pull title/description/uploader out of a yt-dlp info dict, coercing nulls to ''."""
    return {
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "uploader": info.get("uploader") or "",
    }


def is_photo_post(info: dict) -> bool:
    """True if the yt-dlp info dict describes a TikTok photo/slideshow post."""
    if not info:
        return False
    if info.get("images"):
        return True
    return info.get("_type") == "playlist"


def extract_slideshow_image_urls(info: dict) -> list:
    """Pull the highest-resolution image URL for each slide from a yt-dlp info dict."""
    urls = []
    for image in info.get("images") or []:
        url_list = image.get("url_list") or []
        if url_list:
            urls.append(url_list[-1])  # last entry is typically highest-res
        elif image.get("url"):
            urls.append(image["url"])
    return urls


# ---------------------------------------------------------------------------
# tikwm.com fallback (when yt-dlp can't extract a photo post)
# ---------------------------------------------------------------------------

def fetch_tikwm_data(url: str) -> dict:
    """Fetch post data from the tikwm.com API. Returns {} on any failure."""
    try:
        r = requests.get(TIKWM_API, params={"url": url}, timeout=20)
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"[ytsave] tikwm fetch failed (non-fatal): {e}", file=sys.stderr)
        return {}
    if body.get("code") != 0:
        print(f"[ytsave] tikwm returned error: {body.get('msg')}", file=sys.stderr)
        return {}
    return body.get("data") or {}


def parse_tikwm_images(data: dict) -> list:
    """Extract slideshow image URLs from a tikwm 'data' payload.

    Raises ValueError if the post has no images (i.e. it's a regular video).
    """
    images = data.get("images") or []
    if not images:
        raise ValueError("tikwm response has no images — this looks like a video post, not a slideshow")
    return images


# ---------------------------------------------------------------------------
# Image download + OCR
# ---------------------------------------------------------------------------

def download_images(urls: list, tmpdir: str) -> list:
    """Download each image URL into tmpdir. Skips (with a warning) any that fail."""
    paths = []
    for i, url in enumerate(urls):
        dest = os.path.join(tmpdir, f"slide-{i}.jpg")
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
            paths.append(dest)
        except requests.RequestException as e:
            print(f"[ytsave] failed to download slide {i} (non-fatal): {e}", file=sys.stderr)
    return paths


def ocr_images_text(image_paths: list) -> str:
    """OCR each image with Tesseract, deduping repeated lines across slides."""
    import pytesseract
    from PIL import Image

    seen_lines = set()
    all_text = []
    for path in image_paths:
        try:
            text = pytesseract.image_to_string(Image.open(path))
        except Exception as e:
            print(f"[ytsave] OCR failed for {path}: {e}", file=sys.stderr)
            continue
        for line in text.splitlines():
            line = line.strip()
            if len(line) > 3 and line not in seen_lines:
                seen_lines.add(line)
                all_text.append(line)
    return "\n".join(all_text)


# ---------------------------------------------------------------------------
# LLM call 1: title extraction (OCR text + caption -> title list)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
The text below was OCR'd from a TikTok photo slideshow that recommends \
YouTube videos, plus the TikTok's caption. Extract the YouTube video titles \
being recommended.

Return ONLY a valid JSON array, one object per recommended video:
[
  {{"title": "exact or best-guess video title", "channel": "channel name if visible, else null"}}
]

Rules:
- Only include videos that are clearly being recommended/shown, not unrelated OCR noise
- If you can't find any recommended videos, return []
- Return ONLY the JSON array — no markdown fences, no explanation

CAPTION:
{caption}

OCR TEXT:
{ocr_text}"""


def parse_title_list(output: str) -> list:
    parsed = _parse_json_response(output)
    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
    titles = []
    for item in parsed:
        if isinstance(item, dict) and item.get("title"):
            titles.append({"title": str(item["title"]), "channel": item.get("channel")})
    return titles


def _run_llm(prompt: str, timeout: int = 120):
    """Run the prompt through agy first, falling back to claude. Returns stdout or None."""
    try:
        print("[ytsave] Calling Antigravity (agy)...", file=sys.stderr)
        result = subprocess.run(
            ["agy", "-y", "-o", "text"],
            input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        print(f"[ytsave] agy failed (rc={result.returncode}), trying Claude fallback...", file=sys.stderr)
    except Exception as e:
        print(f"[ytsave] agy error: {e}, trying Claude fallback...", file=sys.stderr)

    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", "--model", "sonnet"],
            input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        print(f"[ytsave] claude failed too (rc={result.returncode}): {result.stderr.strip()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[ytsave] claude error: {e}", file=sys.stderr)
    return None


def llm_extract_titles(ocr_text: str, caption: str) -> list:
    prompt = EXTRACTION_PROMPT.format(caption=caption[:2000], ocr_text=ocr_text[:6000])
    output = _run_llm(prompt)
    if output is None:
        die("Both LLMs failed while extracting titles from the slideshow.")
    try:
        return parse_title_list(output)
    except (json.JSONDecodeError, ValueError) as e:
        die(f"LLM returned unparseable title list ({e}). Got: {output[:300]}")


VISION_PROMPT = """\
Use the Read tool to look at each of these TikTok slideshow images, which \
recommend YouTube videos:
{image_list}

Caption: {caption}

Extract the YouTube video titles being recommended. Return ONLY a valid \
JSON array, one object per video:
[{{"title": "exact or best-guess video title", "channel": "channel name if visible, else null"}}]

If you can't find any, return []. Return ONLY the JSON array — no markdown fences, no explanation."""


def llm_extract_titles_vision(image_paths: list, caption: str) -> list:
    prompt = VISION_PROMPT.format(
        image_list="\n".join(image_paths),
        caption=caption[:2000],
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", "--model", "opus",
             "--allowedTools", "Read"],
            input=prompt, capture_output=True, text=True, timeout=180, cwd=str(Path.home()),
        )
    except Exception as e:
        print(f"[ytsave] vision fallback error: {e}", file=sys.stderr)
        return []
    if result.returncode != 0 or not result.stdout.strip():
        print(f"[ytsave] vision fallback failed (rc={result.returncode})", file=sys.stderr)
        return []
    try:
        return parse_title_list(result.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ytsave] vision fallback returned unparseable output: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# YouTube search (yt-dlp, no auth/quota)
# ---------------------------------------------------------------------------

def run_ytdlp_search(query: str, max_results: int = 5) -> str:
    base_dir = Path(__file__).parent
    venv_ytdlp = base_dir / ".venv" / "bin" / "yt-dlp"
    ytdlp_bin = str(venv_ytdlp) if venv_ytdlp.exists() else "yt-dlp"
    cmd = [
        ytdlp_bin, "--dump-json", "--flat-playlist", "--quiet", "--no-warnings",
        f"ytsearch{max_results}:{query}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=30, text=True)
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[ytsave] YouTube search failed for {query!r} (non-fatal): {e}", file=sys.stderr)
        return ""


def parse_ytsearch_ndjson(stdout: str) -> list:
    """Parse yt-dlp's newline-delimited JSON search output into candidate dicts."""
    candidates = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        candidates.append({
            "video_id": video_id,
            "title": entry.get("title") or "",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),
        })
    return candidates


# ---------------------------------------------------------------------------
# LLM call 2: batched match verification
# ---------------------------------------------------------------------------

MATCH_PROMPT = """\
For each recommended video below, decide which (if any) of its YouTube \
search candidates is the actual video being recommended. Match on title \
similarity and channel name.

{blocks}

Return ONLY a valid JSON array with one entry per recommended video, in \
the same order, each either {{"index": N, "video_id": "the chosen id"}} or \
{{"index": N, "video_id": null}} if none of the candidates are a confident \
match. Return ONLY the JSON array — no markdown fences, no explanation."""


def build_match_prompt(titles_with_candidates: list) -> str:
    blocks = []
    for i, item in enumerate(titles_with_candidates):
        channel_hint = f" (channel: {item['channel']})" if item.get("channel") else ""
        lines = [f"{i}. RECOMMENDED: \"{item['title']}\"{channel_hint}"]
        for c in item["candidates"]:
            lines.append(f"   - id={c['video_id']} title=\"{c['title']}\" channel=\"{c['channel']}\"")
        blocks.append("\n".join(lines))
    return MATCH_PROMPT.format(blocks="\n\n".join(blocks))


def parse_match_response(output: str, expected_count: int) -> list:
    parsed = _parse_json_response(output)
    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
    result = [None] * expected_count
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        if not isinstance(idx, int) or not (0 <= idx < expected_count):
            continue
        video_id = entry.get("video_id")
        result[idx] = video_id if isinstance(video_id, str) and video_id else None
    return result


def llm_verify_matches(titles_with_candidates: list) -> list:
    if not titles_with_candidates:
        return []
    prompt = build_match_prompt(titles_with_candidates)
    output = _run_llm(prompt)
    if output is None:
        die("Both LLMs failed while verifying YouTube search matches.")
    try:
        return parse_match_response(output, len(titles_with_candidates))
    except (json.JSONDecodeError, ValueError) as e:
        die(f"LLM returned unparseable match verification ({e}). Got: {output[:300]}")


# ---------------------------------------------------------------------------
# Playlist ops (ytmusicapi)
# ---------------------------------------------------------------------------

def get_ytmusic_client():
    if not BROWSER_AUTH_PATH.exists():
        die(
            f"ytmusicapi auth not found at {BROWSER_AUTH_PATH}. "
            "See skills/ytsave/README.md to run the one-time 'ytmusicapi browser' setup."
        )
    from ytmusicapi import YTMusic
    try:
        return YTMusic(str(BROWSER_AUTH_PATH))
    except Exception as e:
        die(
            f"ytmusicapi auth failed ({e}). Cookies may have expired — "
            "re-run the 'ytmusicapi browser' setup in skills/ytsave/README.md."
        )


def resolve_playlist_id_from_library(playlists: list, name: str) -> str | None:
    target = name.strip().lower()
    for p in playlists:
        if (p.get("title") or "").strip().lower() == target:
            return p.get("playlistId")
    return None


def ensure_playlist(ytmusic, config: dict, name: str) -> str:
    cached_id = config.get("playlist_id")
    if cached_id:
        try:
            ytmusic.get_playlist(cached_id, limit=1)
            return cached_id
        except Exception:
            print(f"[ytsave] cached playlist {cached_id} no longer exists, re-resolving...", file=sys.stderr)

    playlists = ytmusic.get_library_playlists(limit=200)
    playlist_id = resolve_playlist_id_from_library(playlists, name)
    if not playlist_id:
        print(f"[ytsave] Creating playlist '{name}'...", file=sys.stderr)
        playlist_id = ytmusic.create_playlist(name, "Auto-imported from TikTok recommendations")

    config["playlist_name"] = name
    config["playlist_id"] = playlist_id
    save_config(config)
    return playlist_id


def existing_video_ids(playlist: dict) -> set:
    return {t["videoId"] for t in playlist.get("tracks", []) if t.get("videoId")}


def classify_matches(titles: list, matched: list, existing: set) -> tuple:
    """Pair each title with its match outcome.

    Returns (new_video_ids, added_titles, skipped_titles):
    - new_video_ids: deduped ids to add to the playlist, in order
    - added_titles: the title responsible for each newly-added id (same order/length as new_video_ids)
    - skipped_titles: titles with no confident match, or whose id was already
      in the playlist / a duplicate of an earlier title in this run
    """
    new_ids = []
    added_titles = []
    skipped_titles = []
    seen = set(existing)
    for title, video_id in zip(titles, matched):
        if video_id and video_id not in seen:
            seen.add(video_id)
            new_ids.append(video_id)
            added_titles.append(title)
        else:
            skipped_titles.append(title)
    return new_ids, added_titles, skipped_titles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        die("Usage: ytsave.py <tiktok_url>")
    url = sys.argv[1]

    if not is_tiktok(url):
        die("Not a TikTok URL. ytsave only handles TikTok slideshows.")

    info = run_ytdlp_dump_json(url)
    caption = extract_caption(info)
    caption_text = f"{caption['title']}\n{caption['description']}"

    with tempfile.TemporaryDirectory() as tmpdir:
        if is_photo_post(info):
            image_urls = extract_slideshow_image_urls(info)
        else:
            print("[ytsave] yt-dlp didn't report a slideshow, trying tikwm fallback...", file=sys.stderr)
            tikwm_data = fetch_tikwm_data(url)
            try:
                image_urls = parse_tikwm_images(tikwm_data)
            except ValueError:
                die("This TikTok is a regular video, not a photo slideshow — v1 only handles slideshows.")

        if not image_urls:
            die("Could not extract any slideshow images from this TikTok.")

        image_paths = download_images(image_urls, tmpdir)
        if not image_paths:
            die("Failed to download any slideshow images.")

        ocr_text = ocr_images_text(image_paths)

        titles = llm_extract_titles(ocr_text, caption_text) if ocr_text.strip() else []
        if not titles:
            print("[ytsave] OCR extraction found nothing, trying vision fallback...", file=sys.stderr)
            titles = llm_extract_titles_vision(image_paths, caption_text)

        if not titles:
            die("Could not find any recommended YouTube videos in this TikTok.")

        print(f"[ytsave] Extracted {len(titles)} candidate title(s): "
              f"{', '.join(t['title'] for t in titles)}", file=sys.stderr)

        titles_with_candidates = []
        for t in titles:
            search_output = run_ytdlp_search(t["title"])
            candidates = parse_ytsearch_ndjson(search_output)
            titles_with_candidates.append({**t, "candidates": candidates})

        matched = llm_verify_matches(
            [t for t in titles_with_candidates if t["candidates"]]
        )
        # re-align matched results back onto the full (possibly candidate-less) list
        matched_iter = iter(matched)
        aligned = [
            next(matched_iter) if t["candidates"] else None
            for t in titles_with_candidates
        ]

    config = load_config()
    playlist_name = config.get("playlist_name") or DEFAULT_PLAYLIST_NAME
    ytmusic = get_ytmusic_client()
    playlist_id = ensure_playlist(ytmusic, config, playlist_name)
    try:
        playlist = ytmusic.get_playlist(playlist_id, limit=None)
        existing = existing_video_ids(playlist)
    except Exception as e:
        print(f"[ytsave] could not fetch existing playlist contents (treating as empty): {e}", file=sys.stderr)
        existing = set()

    titles_only = [t["title"] for t in titles_with_candidates]
    new_ids, added_titles, skipped_titles = classify_matches(titles_only, aligned, existing)
    if new_ids:
        ytmusic.add_playlist_items(playlist_id, new_ids)

    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
    print(playlist_url)
    if added_titles:
        print(f"Added {len(added_titles)} video(s):")
        for title in added_titles:
            print(f"  - {title}")
    if skipped_titles:
        print(f"Skipped {len(skipped_titles)} (no confident match or already in playlist):")
        for title in skipped_titles:
            search_url = "https://www.youtube.com/results?search_query=" + requests.utils.quote(title)
            print(f"  - {title} -> {search_url}")


if __name__ == "__main__":
    main()
