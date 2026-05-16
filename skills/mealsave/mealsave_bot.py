#!/usr/bin/env python3
"""Mealsave Telegram Bot.

Forward a recipe URL and get it saved to Mealie.
Locked to a single Telegram user ID for security.
"""

import os
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

# Load config from .env
def load_bot_config():
    env_path = Path.home() / ".config" / "mealsave" / ".env"
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
MEALSAVE_PY = Path(__file__).parent / "mealsave.py"
VENV_PYTHON = Path(__file__).parent / ".venv" / "bin" / "python"

if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not set in ~/.config/mealsave/.env", file=sys.stderr)
    sys.exit(1)
if not ALLOWED_USER_ID:
    print("Error: TELEGRAM_USER_ID not set in ~/.config/mealsave/.env", file=sys.stderr)
    sys.exit(1)

def authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID

def extract_urls(text: str) -> list[str]:
    return re.findall(r'(https?://[^\s]+)', text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Welcome to Mealsave Bot! Forward me any recipe link (TikTok, YouTube, blog, etc.) "
        "and I'll save it to your Mealie instance."
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
            # Run mealsave.py as a subprocess
            cmd = [str(VENV_PYTHON), str(MEALSAVE_PY), url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                # Find the URL in the output
                mealie_url_match = re.search(r'(https?://[^\s]+)', output)
                if mealie_url_match:
                    await status_msg.edit_text(f"✅ Saved! {mealie_url_match.group(0)}")
                else:
                    await status_msg.edit_text(f"✅ Processed, but couldn't find Mealie URL in output:\n{output}")
            else:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                combined_output = f"{stdout}\n{stderr}".strip()
                
                # Try to find a specific ERROR: line
                error_match = re.search(r'^ERROR:\s*(.*)$', combined_output, re.MULTILINE)
                if error_match:
                    error_msg = error_match.group(1).strip()
                else:
                    # Fallback to the last 400 chars (usually more useful than the first 400)
                    error_msg = combined_output[-400:].strip()
                
                await status_msg.edit_text(f"❌ Error saving recipe:\n{error_msg}")
        
        except subprocess.TimeoutExpired:
            await status_msg.edit_text("❌ Error: Extraction timed out (took > 5 minutes).")
        except Exception as e:
            await status_msg.edit_text(f"❌ Unexpected error: {str(e)}")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message))
    print("Mealsave Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
