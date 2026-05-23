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
                await status_msg.edit_text(parse_vidqueue_output(result.stdout))
            else:
                combined = f"{result.stdout}\n{result.stderr}".strip()
                error_match = re.search(r'^ERROR:\s*(.*)$', combined, re.MULTILINE)
                error_msg = error_match.group(1).strip() if error_match else combined[-400:].strip()
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
