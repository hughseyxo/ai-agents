#!/usr/bin/env python3
"""
mealsave.py — Save a recipe URL to a self-hosted Mealie instance.
Usage: python mealsave.py <url>

Config: ~/.config/mealsave/.env  (MEALIE_URL, MEALIE_TOKEN)
LLM:    claude CLI (used for transcript/HTML → recipe extraction)
"""

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


# ---------------------------------------------------------------------------
# Config + helpers
# ---------------------------------------------------------------------------

def load_config():
    env_path = Path.home() / ".config" / "mealsave" / ".env"
    if not env_path.exists():
        die(
            f"Config not found at {env_path}. "
            "See ~/.claude/skills/mealsave/README.md for setup."
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

    url = config.get("MEALIE_URL", "").rstrip("/")
    token = config.get("MEALIE_TOKEN", "")
    if not url:
        die("MEALIE_URL not set in ~/.config/mealsave/.env")
    if not token:
        die("MEALIE_TOKEN not set in ~/.config/mealsave/.env")
    return url, token


def youtube_cookies_path() -> Path | None:
    """Return path to YouTube cookies file if configured, else None."""
    cookies = os.environ.get("MEALSAVE_YT_COOKIES")
    if cookies:
        p = Path(cookies)
        return p if p.exists() else None
    default = Path.home() / ".config" / "mealsave" / "youtube-cookies.txt"
    return default if default.exists() else None


def check_cookie_expiry(cookies_path: Path):
    """
    Parse the Netscape cookies file and warn (or die) based on expiry.
    YouTube cookies typically last 6-12 months.
    """
    import time as _time

    now = _time.time()
    warn_threshold = 30 * 86400   # warn 30 days before expiry
    soonest_expiry = None

    try:
        with open(cookies_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                try:
                    exp = int(parts[4])
                except ValueError:
                    continue
                if exp == 0:
                    continue  # session cookie
                if soonest_expiry is None or exp < soonest_expiry:
                    soonest_expiry = exp
    except OSError:
        return  # non-fatal

    if soonest_expiry is None:
        return

    remaining = soonest_expiry - now
    if remaining <= 0:
        die(
            "YouTube cookies have expired. Re-export from your browser:\n"
            "  1. Go to youtube.com (logged in)\n"
            "  2. Use 'Get cookies.txt LOCALLY' extension → Export\n"
            f"  3. scp youtube-cookies.txt {os.uname().nodename}:{cookies_path}"
        )
    elif remaining < warn_threshold:
        days_left = int(remaining / 86400)
        print(
            f"WARNING: YouTube cookies expire in {days_left} days. "
            "Re-export soon to avoid interruption.",
            file=sys.stderr,
        )


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Mealie connectivity + idempotency
# ---------------------------------------------------------------------------

def check_mealie_alive(mealie_url: str):
    try:
        r = requests.get(f"{mealie_url}/api/app/about", timeout=5)
        if r.status_code != 200:
            die(
                f"Mealie at {mealie_url} returned HTTP {r.status_code}. "
                "Is the container running?"
            )
    except requests.exceptions.ConnectionError:
        die(f"Cannot connect to Mealie at {mealie_url}. Is the container running?")
    except requests.exceptions.Timeout:
        die(f"Mealie at {mealie_url} timed out.")


def find_existing(mealie_url: str, token: str, org_url: str):
    """Return slug of first recipe whose orgURL matches, or None."""
    h = auth_headers(token)
    page = 1
    while True:
        try:
            r = requests.get(
                f"{mealie_url}/api/recipes",
                headers=h,
                params={"page": page, "perPage": 100},
                timeout=10,
            )
        except requests.exceptions.RequestException:
            return None  # Non-fatal: skip duplicate check on network error

        if r.status_code == 401:
            die("Mealie rejected the token (401 Unauthorized). Check MEALIE_TOKEN in your .env.")
        if r.status_code != 200:
            return None  # Non-fatal

        data = r.json()
        for item in data.get("items", []):
            if item.get("orgURL") == org_url:
                return item.get("slug")

        total = data.get("total", 0)
        if page * 100 >= total:
            break
        page += 1

    return None


# ---------------------------------------------------------------------------
# Mealie scraper (path 1)
# ---------------------------------------------------------------------------

def try_mealie_scraper(mealie_url: str, token: str, url: str):
    """
    Try Mealie's built-in URL scraper.
    Returns slug string on success, None on failure.
    """
    h = auth_headers(token)
    try:
        r = requests.post(
            f"{mealie_url}/api/recipes/create/url",
            headers=h,
            json={"url": url, "include_tags": False},
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        print(f"[mealsave] Scraper request failed: {e}", file=sys.stderr)
        return None

    if r.status_code == 401:
        die("Mealie rejected the token (401 Unauthorized). Check MEALIE_TOKEN in your .env.")

    if r.status_code == 201:
        slug = r.json()
        # Mealie returns slug as a bare JSON string
        if isinstance(slug, str) and slug:
            return slug
        # Some versions may wrap it
        if isinstance(slug, dict):
            return slug.get("slug") or slug.get("name")

    return None


def patch_recipe(mealie_url: str, token: str, slug: str, body: dict):
    h = auth_headers(token)
    r = requests.patch(
        f"{mealie_url}/api/recipes/{slug}",
        headers=h,
        json=body,
        timeout=10,
    )
    if r.status_code not in (200, 201):
        die(
            f"Failed to update recipe '{slug}': "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# YouTube transcript (path 2)
# Uses yt-dlp to download auto-generated subtitles, which works reliably
# from server IPs. youtube-transcript-api is blocked by YouTube on most
# cloud/VPS IPs.
# ---------------------------------------------------------------------------

def get_video_id(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower().lstrip("www.").lstrip("m.")
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("?")[0]
    qs = parse_qs(parsed.query)
    ids = qs.get("v", [])
    return ids[0] if ids else None


def fetch_youtube_transcript(url: str) -> str:
    """
    Download subtitles via yt-dlp (more reliable than youtube-transcript-api
    from server/VPS IPs where YouTube blocks direct API requests).
    """
    import tempfile
    import glob as _glob

    video_id = get_video_id(url)
    if not video_id:
        die(f"Could not extract YouTube video ID from: {url}")

    venv_ytdlp = Path.home() / ".claude/skills/mealsave/.venv/bin/yt-dlp"
    ytdlp_bin = str(venv_ytdlp) if venv_ytdlp.exists() else "yt-dlp"

    cookies = youtube_cookies_path()
    if cookies:
        check_cookie_expiry(cookies)

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            ytdlp_bin,
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            # Use Node.js to solve YouTube's n-challenge (required on VPS/server IPs).
            # Deno is the yt-dlp default but is usually not installed on servers.
            "--no-js-runtimes",
            "--js-runtimes", "node",
            "--output", f"{tmpdir}/sub",
        ]
        if cookies:
            cmd += ["--cookies", str(cookies)]
        cmd.append(url)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            die("yt-dlp not found. Run: ~/.claude/skills/mealsave/.venv/bin/pip install yt-dlp")
        except subprocess.TimeoutExpired:
            die("yt-dlp timed out fetching subtitles.")

        # Find downloaded .vtt file
        vtt_files = _glob.glob(f"{tmpdir}/*.vtt")
        if not vtt_files:
            stderr = result.stderr.strip()
            if "no subtitles" in stderr.lower() or "no captions" in stderr.lower():
                die("No captions available — can't extract a recipe from this video.")
            if "cookies" in stderr.lower() or "sign in" in stderr.lower() or "bot" in stderr.lower():
                die(
                    "YouTube is blocking requests from this IP. "
                    "Export your YouTube cookies to ~/.config/mealsave/youtube-cookies.txt "
                    "using the 'Get cookies.txt LOCALLY' browser extension, then retry. "
                    "See README for details."
                )
            die(f"No subtitles downloaded. yt-dlp said: {stderr[-300:]}")

        with open(vtt_files[0], encoding="utf-8") as f:
            vtt = f.read()

        # Parse VTT: strip header, timestamps, and de-dupe repeated lines
        lines = []
        seen = set()
        for line in vtt.splitlines():
            line = line.strip()
            if not line or line.startswith("WEBVTT") or "-->" in line:
                continue
            # Strip VTT tags like <00:00:00.000><c>...</c>
            line = re.sub(r"<[^>]+>", "", line).strip()
            if line and line not in seen:
                seen.add(line)
                lines.append(line)

        text = " ".join(lines)
        if not text.strip():
            die("Subtitles were empty after parsing — can't extract a recipe.")
        return text


# ---------------------------------------------------------------------------
# Generic HTML extraction via trafilatura (path 3)
# ---------------------------------------------------------------------------

BYPARR_URL = "http://localhost:8191/v1"


def fetch_via_byparr(url: str) -> str | None:
    """
    Fetch HTML via byparr (FlareSolverr-compatible) running locally on port 8191.
    Returns HTML string on success, None if byparr is unavailable or fails.
    """
    try:
        r = requests.post(
            BYPARR_URL,
            json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
            timeout=90,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "ok":
                return data.get("solution", {}).get("response")
    except requests.exceptions.RequestException:
        pass
    return None


def fetch_generic_text(url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        die(
            "trafilatura not installed. "
            "Run: ~/.claude/skills/mealsave/.venv/bin/pip install trafilatura lxml_html_clean"
        )

    # Try trafilatura's built-in fetcher first
    html = trafilatura.fetch_url(url)

    # Fall back to byparr (handles Cloudflare-protected sites)
    if not html:
        print("[mealsave] Direct fetch failed — trying byparr...", file=sys.stderr)
        html = fetch_via_byparr(url)

    if not html:
        die(f"Failed to download URL: {url} (tried direct fetch and byparr)")

    text = trafilatura.extract(html)
    if not text:
        die(f"Could not extract readable text from: {url}")
    return text


# ---------------------------------------------------------------------------
# LLM extraction via claude CLI
# ---------------------------------------------------------------------------

LLM_PROMPT = """\
Extract a recipe from the text below{hint}.

Return ONLY a valid JSON object with these fields (omit any you cannot find):
{{
  "name": "Recipe name",
  "description": "1-2 sentence description",
  "recipeYield": "e.g. 4 servings",
  "prepTime": "ISO 8601 duration e.g. PT15M",
  "performTime": "ISO 8601 duration e.g. PT30M",
  "totalTime": "ISO 8601 duration e.g. PT45M",
  "recipeIngredient": ["2 cups flour", "1 tsp salt"],
  "recipeInstructions": ["Preheat oven to 350F.", "Mix dry ingredients."]
}}

Rules:
- Do NOT invent ingredients or steps not in the source text
- Do NOT add tags, categories, or nutritional info
- recipeIngredient and recipeInstructions are the most important fields
- Return ONLY the JSON object — no markdown fences, no explanation

TEXT:
{text}"""


def llm_extract(text: str, source_hint: str = "") -> dict:
    hint = f" ({source_hint})" if source_hint else ""
    prompt = LLM_PROMPT.format(hint=hint, text=text[:8000])

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError:
        die("'claude' CLI not found in PATH. Is Claude Code installed?")
    except subprocess.TimeoutExpired:
        die("Claude CLI timed out during recipe extraction (>90s).")

    if result.returncode != 0:
        err = result.stderr.strip()[:300]
        die(f"Claude CLI exited with error: {err}")

    output = result.stdout.strip()

    # Strip markdown code fences if claude wrapped the JSON
    output = re.sub(r"^```(?:json)?\s*", "", output, flags=re.MULTILINE)
    output = re.sub(r"\s*```\s*$", "", output, flags=re.MULTILINE)
    output = output.strip()

    # Find JSON object in output (in case there's leading prose)
    match = re.search(r"\{.*\}", output, re.DOTALL)
    if match:
        output = match.group(0)

    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        die(f"LLM returned invalid JSON ({e}). Got: {output[:300]}")


# ---------------------------------------------------------------------------
# Recipe creation from extracted data
# ---------------------------------------------------------------------------

def create_from_data(mealie_url: str, token: str, data: dict, org_url: str) -> str:
    """Create a recipe stub, then patch it with the extracted data. Returns slug."""
    h = auth_headers(token)
    name = data.get("name") or "Untitled Recipe"

    r = requests.post(
        f"{mealie_url}/api/recipes",
        headers=h,
        json={"name": name},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        die(f"Failed to create recipe: HTTP {r.status_code} — {r.text[:200]}")

    resp_body = r.json()
    # Mealie may return slug as a bare string or inside an object
    if isinstance(resp_body, str):
        slug = resp_body
    elif isinstance(resp_body, dict):
        slug = resp_body.get("slug") or resp_body.get("name")
    else:
        die(f"Unexpected response from recipe create: {r.text[:200]}")

    if not slug:
        die(f"Could not determine slug from: {r.text[:200]}")

    patch = {"orgURL": org_url}

    for field in ("description", "recipeYield"):
        if data.get(field):
            patch[field] = data[field]

    for time_field in ("prepTime", "performTime", "totalTime"):
        if data.get(time_field):
            patch[time_field] = data[time_field]

    ingredients = data.get("recipeIngredient", [])
    if ingredients:
        patch["recipeIngredient"] = [
            {"note": str(i), "referenceId": str(uuid.uuid4())}
            for i in ingredients
        ]

    instructions = data.get("recipeInstructions", [])
    if instructions:
        patch["recipeInstructions"] = [
            {"id": str(uuid.uuid4()), "title": "", "summary": "", "text": str(s), "ingredientReferences": []}
            for s in instructions
        ]

    patch_recipe(mealie_url, token, slug, patch)
    return slug


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

def is_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    # strip www. and m.
    host = re.sub(r"^(www\.|m\.)", "", host)
    return host in ("youtube.com", "youtu.be")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        die("Usage: mealsave.py <url>")

    url = sys.argv[1]
    mealie_url, token = load_config()

    check_mealie_alive(mealie_url)

    # Idempotency: skip if already saved
    existing = find_existing(mealie_url, token, url)
    if existing:
        print(f"Recipe already in Mealie: {mealie_url}/g/home/r/{existing}")
        print("Edit it in Mealie's UI, or delete it there first to re-import.")
        sys.exit(0)

    # Path 1: Mealie's built-in scraper (handles schema.org/Recipe sites natively)
    slug = try_mealie_scraper(mealie_url, token, url)
    if slug:
        patch_recipe(mealie_url, token, slug, {"orgURL": url})
        print(f"{mealie_url}/g/home/r/{slug}")
        sys.exit(0)

    print("[mealsave] Mealie scraper didn't handle this URL, falling back to extraction...")

    # Path 2: YouTube — transcript → LLM
    if is_youtube(url):
        print("[mealsave] Fetching YouTube transcript...")
        transcript = fetch_youtube_transcript(url)
        print("[mealsave] Extracting recipe with Claude...")
        data = llm_extract(transcript, "YouTube video transcript")

        if not data.get("recipeIngredient") and not data.get("recipeInstructions"):
            die(
                "LLM extraction produced an empty recipe "
                "(no ingredients, no instructions). "
                "Try saving it manually in Mealie's UI."
            )

        slug = create_from_data(mealie_url, token, data, url)
        print(f"{mealie_url}/g/home/r/{slug}")
        sys.exit(0)

    # Path 3: Generic page — trafilatura → LLM
    print("[mealsave] Extracting text with trafilatura...")
    text = fetch_generic_text(url)
    print("[mealsave] Extracting recipe with Claude...")
    data = llm_extract(text, "web page")

    if not data.get("recipeIngredient") and not data.get("recipeInstructions"):
        die(
            "LLM extraction produced an empty recipe "
            "(no ingredients, no instructions). "
            "Try saving it manually in Mealie's UI."
        )

    slug = create_from_data(mealie_url, token, data, url)
    print(f"{mealie_url}/g/home/r/{slug}")


if __name__ == "__main__":
    main()
