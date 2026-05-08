#!/usr/bin/env python3
"""Free Time Advisor Telegram Bot.

Send a number of minutes and get task suggestions from Todoist.
Locked to a single Telegram user ID for security.
"""

import json
import os
import re
import subprocess
from datetime import date

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["TELEGRAM_USER_ID"])
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# Keyword → estimated minutes for task duration
DURATION_KEYWORDS = {
    "water": 5,
    "email": 10,
    "book": 10,
    "schedule": 10,
    "call": 15,
    "buy": 15,
    "replace": 15,
    "review": 15,
    "sell": 20,
    "update": 20,
    "organize": 20,
    "cv": 25,
    "fix": 25,
    "clean": 30,
    "apply": 30,
    "research": 30,
    "write": 30,
    "course": 45,
}
DEFAULT_ESTIMATE = 15


def authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID


def parse_minutes(text: str) -> int | None:
    """Extract minutes from user message."""
    text = text.strip().lower()

    # Plain number: "30"
    if re.fullmatch(r"\d+", text):
        return int(text)

    # "an hour", "1 hour", "2 hours"
    m = re.search(r"(\d+)\s*hours?", text)
    if m:
        return int(m.group(1)) * 60

    if "an hour" in text:
        return 60
    if "half an hour" in text or "half hour" in text:
        return 30

    # "45 minutes", "45 mins", "45 min"
    m = re.search(r"(\d+)\s*min", text)
    if m:
        return int(m.group(1))

    # "a couple of hours"
    if "couple" in text and "hour" in text:
        return 120

    return None


def estimate_duration(title: str) -> int:
    """Estimate task duration in minutes from title keywords."""
    lower = title.lower()
    for keyword, minutes in DURATION_KEYWORDS.items():
        if keyword in lower:
            return minutes
    return DEFAULT_ESTIMATE


def fetch_inbox_tasks() -> list[dict]:
    """Fetch inbox tasks via Claude CLI + Todoist MCP."""
    prompt = (
        "Use find-tasks to get all incomplete tasks from the Inbox project. "
        "Return ONLY a valid JSON array, no other text or markdown. "
        "Each element must have these exact keys: "
        '"id" (string), "content" (string), "priority" (integer 1-4 where 4=highest), '
        '"due_date" (ISO date string or null), "is_overdue" (boolean).'
    )
    result = subprocess.run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {result.stderr[:300]}")

    # Extract JSON array from output (handle potential wrapping text)
    output = result.stdout.strip()
    start = output.find("[")
    end = output.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array in Claude output: {output[:200]}")
    return json.loads(output[start : end + 1])


def rank_and_filter(tasks: list[dict], available_minutes: int) -> list[dict]:
    """Add estimates, filter by time, rank by urgency."""
    for task in tasks:
        task["estimated_minutes"] = estimate_duration(task["content"])

    eligible = [t for t in tasks if t["estimated_minutes"] <= available_minutes]

    def sort_key(t):
        # Sort: overdue first (True > False, negate for DESC), priority DESC, due_date ASC (nulls last)
        overdue = not t.get("is_overdue", False)
        priority = -(t.get("priority", 1))
        due = t.get("due_date") or "9999-12-31"
        return (overdue, priority, due)

    eligible.sort(key=sort_key)
    return eligible[:3]


def format_results(tasks: list[dict], minutes: int) -> str:
    """Format task list for Telegram message."""
    if not tasks:
        return f"Nothing fits in {minutes} minutes \u2014 enjoy your break! \u2615"

    lines = [f"You have {minutes} minutes. Here's what I'd suggest:\n"]
    for i, task in enumerate(tasks, 1):
        overdue = " \U0001f534" if task.get("is_overdue") else ""
        due = f" (due {task['due_date']})" if task.get("due_date") else ""
        lines.append(f"{i}. {task['content']} (~{task['estimated_minutes']} min){due}{overdue}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Hey! Tell me how many minutes you have free and I'll suggest tasks.\n"
        "e.g. '30' or 'I have an hour'"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    minutes = parse_minutes(update.message.text)
    if minutes is None:
        await update.message.reply_text(
            "Send a number of minutes (e.g. '30') or say 'I have 45 minutes'"
        )
        return

    await update.message.reply_text(f"Looking for tasks that fit in {minutes} minutes...")

    try:
        tasks = fetch_inbox_tasks()
        ranked = rank_and_filter(tasks, minutes)
        await update.message.reply_text(format_results(ranked, minutes))
    except Exception as e:
        await update.message.reply_text(f"Error fetching tasks: {e}")


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Free Time Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
