import json
from unittest.mock import patch, MagicMock
from agents import free_time


def _resp(payload):
    m = MagicMock()
    m.read.return_value = json.dumps(payload).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_fetch_inbox_tasks_uses_rest_api(monkeypatch):
    monkeypatch.setenv("TODOIST_API_TOKEN", "tok")
    projects = [{"id": "42", "is_inbox_project": True}]
    tasks = [{"id": "1", "content": "email accountant", "priority": 4,
              "due": {"date": "2026-01-01"}}]
    with patch("agents.free_time.urllib.request.urlopen",
               side_effect=[_resp(projects), _resp(tasks)]) as u:
        out = free_time.fetch_inbox_tasks()
    assert out == [{"id": "1", "content": "email accountant", "priority": 4,
                    "due_date": "2026-01-01", "is_overdue": True}]
    auth = u.call_args_list[0].args[0].get_header("Authorization")
    assert auth == "Bearer tok"


def test_suggest_ranks_and_filters(monkeypatch):
    monkeypatch.setattr(free_time, "fetch_inbox_tasks", lambda: [
        {"id": "1", "content": "research topic", "priority": 1, "due_date": None, "is_overdue": False},
        {"id": "2", "content": "email accountant", "priority": 4, "due_date": "2026-01-01", "is_overdue": True},
    ])
    text = free_time.suggest(15)
    assert "email accountant" in text and "research topic" not in text
