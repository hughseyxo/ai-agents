"""Free-time task suggestions from the Todoist REST API (no LLM)."""
import json
import os
import urllib.parse
import urllib.request
from datetime import date

API = "https://api.todoist.com/rest/v2"

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


def estimate_duration(title: str) -> int:
    """Estimate task duration in minutes from title keywords."""
    lower = title.lower()
    for keyword, minutes in DURATION_KEYWORDS.items():
        if keyword in lower:
            return minutes
    return DEFAULT_ESTIMATE


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
    return eligible[:8]


def format_results(tasks: list[dict], minutes: int) -> str:
    """Format task list for Telegram message."""
    if not tasks:
        return f"Nothing fits in {minutes} minutes — enjoy your break! ☕"

    lines = [f"You have {minutes} minutes. Here's what I'd suggest:\n"]
    for i, task in enumerate(tasks, 1):
        overdue = " \U0001f534" if task.get("is_overdue") else ""
        due = f" (due {task['due_date']})" if task.get("due_date") else ""
        lines.append(f"{i}. {task['content']} (~{task['estimated_minutes']} min){due}{overdue}")
    return "\n".join(lines)


def _get(path: str, params: dict | None = None) -> list | dict:
    token = os.environ["TODOIST_API_TOKEN"]
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_inbox_tasks() -> list[dict]:
    inbox = next(p for p in _get("/projects") if p.get("is_inbox_project"))
    today = date.today().isoformat()
    out = []
    for t in _get("/tasks", {"project_id": inbox["id"]}):
        due = (t.get("due") or {}).get("date")
        out.append({
            "id": t["id"], "content": t["content"],
            "priority": t.get("priority", 1),
            "due_date": due, "is_overdue": bool(due and due < today),
        })
    return out


def suggest(minutes: int) -> str:
    tasks = fetch_inbox_tasks()
    return format_results(rank_and_filter(tasks, minutes), minutes)
