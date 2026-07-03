# Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every finding from `docs/reviews/2026-07-03-agents-skills-systematic-review.md` (FloraPulse auth excluded — accepted risk).

**Architecture:** Twelve independent tasks in five batches: security quick wins (1–3), cron consolidation (4), deterministic email delivery (5–7), plant data/pipeline consolidation (8–10), intelligence-run + hygiene fixes (11–12). Each task is separately shippable; batches C and D depend on nothing in A/B.

**Tech Stack:** Python 3.11, pytest, sqlite3, pydantic, urllib (no new deps).

## Global Constraints

- Run tests with `.venv/bin/pytest tests/ telegram-bot/ -x -q` (repo root).
- TDD: write the failing test first, watch it fail, then implement (user rule).
- No secrets in tracked files; recipient/token config via env vars already exported by `run-agent.sh` (`TODOIST_API_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
- Dual-CLI rule: no Claude- or Antigravity-specific deps in agent Python; LLM specifics live in `BaseAgent.synthesize()` only.
- Update CLAUDE.md when structure changes (Task 12 collects all edits).
- Commit after every task; small atomic commits.

---

### Task 1: Fail-closed Telegram auth in the concierge bot

**Files:**
- Modify: `telegram-bot/bot.py:28-29,517-520`
- Test: `telegram-bot/test_bot.py`

**Interfaces:**
- Produces: `bot.main()` exits with an error log when `TELEGRAM_USER_ID` is unset/empty. Handlers unchanged.

- [ ] **Step 1: Write the failing test** (append to `telegram-bot/test_bot.py`)

```python
def test_main_refuses_to_start_without_allowed_user(monkeypatch, caplog):
    import bot as bot_mod
    monkeypatch.setattr(bot_mod, "TELEGRAM_TOKEN", "dummy-token")
    monkeypatch.setattr(bot_mod, "ALLOWED_USER_ID", "")
    called = {}
    monkeypatch.setattr(
        bot_mod, "ApplicationBuilder",
        lambda: (_ for _ in ()).throw(AssertionError("must not build app")),
    )
    bot_mod.main()  # should return early, never touching ApplicationBuilder
    assert "TELEGRAM_USER_ID" in caplog.text
```

- [ ] **Step 2: Run it — expect FAIL** (`.venv/bin/pytest telegram-bot/test_bot.py::test_main_refuses_to_start_without_allowed_user -q` → AssertionError "must not build app")

- [ ] **Step 3: Implement** — in `bot.py` `main()`, after the `TELEGRAM_TOKEN` check:

```python
    if not ALLOWED_USER_ID:
        logger.error("Missing TELEGRAM_USER_ID — refusing to start unauthenticated (fail closed).")
        return
```

- [ ] **Step 4: Run test — expect PASS**; run full bot suite `.venv/bin/pytest telegram-bot/ -q`.

- [ ] **Step 5: Commit** — `git commit -m "fix(bot): fail closed when TELEGRAM_USER_ID is unset"`

---

### Task 2: Free-time consolidation (Todoist REST, concierge tool, retire standalone bot)

Kills two findings: the over-privileged Claude CLI fetch (all MCP servers + untrusted task titles) and the free-time triplication.

**Files:**
- Create: `agents/free_time.py`, `tests/test_free_time.py`
- Modify: `telegram-bot/tools.py` (new tool fn), `telegram-bot/tool_specs.py` (new spec)
- Delete: `free_time_bot.py`, `free-time-bot.service`

**Interfaces:**
- Produces: `agents.free_time.fetch_inbox_tasks() -> list[dict]` (keys: `id, content, priority(int 1-4), due_date(str|None), is_overdue(bool)`); `agents.free_time.suggest(minutes: int) -> str` (formatted suggestion text); concierge tool `suggest_free_time_tasks(minutes: int) -> str`.
- Consumes: env `TODOIST_API_TOKEN`.

- [ ] **Step 1: Write failing tests** (`tests/test_free_time.py`)

```python
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
```

- [ ] **Step 2: Run — expect FAIL** (module missing).

- [ ] **Step 3: Implement `agents/free_time.py`** — port `DURATION_KEYWORDS`, `DEFAULT_ESTIMATE`, `estimate_duration`, `rank_and_filter`, `format_results` verbatim from `free_time_bot.py`, replacing the Claude-CLI fetch:

```python
"""Free-time task suggestions from the Todoist REST API (no LLM)."""
import json
import os
import urllib.parse
import urllib.request
from datetime import date

API = "https://api.todoist.com/rest/v2"

# (DURATION_KEYWORDS, DEFAULT_ESTIMATE, estimate_duration, rank_and_filter,
#  format_results — copied unchanged from free_time_bot.py lines 32-157)


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
```

- [ ] **Step 4: Run tests — expect PASS.**

- [ ] **Step 5: Add concierge tool.** `telegram-bot/tools.py`:

```python
def suggest_free_time_tasks(minutes: int) -> str:
    try:
        from agents.free_time import suggest
        return suggest(int(minutes))
    except Exception as e:
        return f"Free-time suggestions unavailable: {e}"
```

`telegram-bot/tool_specs.py` — add to SPECS following the existing entry shape:

```python
{
    "name": "suggest_free_time_tasks",
    "description": "Suggest the best Todoist inbox tasks that fit a free time window of N minutes.",
    "input_schema": {"type": "object", "required": ["minutes"],
                     "properties": {"minutes": {"type": "integer", "description": "Available minutes"}}},
    "func": suggest_free_time_tasks,
},
```

(Match the exact key names used by existing SPECS entries — check `tool_specs.py` before editing; `test_tool_specs.py` will catch mismatches.)

- [ ] **Step 6: Delete the standalone bot** — `git rm free_time_bot.py free-time-bot.service`; `systemctl --user disable --now free-time-bot.service 2>/dev/null || true`. Keep `skills/free-time/SKILL.md` (interactive path, already Todoist-MCP-scoped).

- [ ] **Step 7: Run full suite** `.venv/bin/pytest tests/ telegram-bot/ -q` — expect PASS.

- [ ] **Step 8: Commit** — `git commit -m "feat(free-time): Todoist REST + concierge tool; retire over-privileged standalone bot"`

---

### Task 3: BaseAgent untrusted-input guard (no agy for untrusted prompts)

**Files:**
- Modify: `agents/base.py` (class attr + `synthesize`), `agents/news_briefing.py` (set flag)
- Test: `tests/test_synthesize.py`

**Interfaces:**
- Produces: `BaseAgent.untrusted_input: bool = False`. When True and no explicit `providers` override, `synthesize()` uses only the `claude` provider (sandboxed path).

- [ ] **Step 1: Failing test** (append to `tests/test_synthesize.py`, reuse its existing subprocess-mock fixture pattern):

```python
def test_untrusted_input_skips_antigravity(monkeypatch):
    from agents.base import BaseAgent

    class A(BaseAgent):
        name = "t-untrusted"
        untrusted_input = True

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        m = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        return m

    monkeypatch.setattr("agents.base.subprocess.run", fake_run)
    A(db_path=":memory:").synthesize("hello")
    assert calls and all(c == "claude" for c in calls)
```

- [ ] **Step 2: Run — expect FAIL** (agy called first).

- [ ] **Step 3: Implement** in `agents/base.py`:

```python
    # Class attr, next to `providers`:
    untrusted_input: bool = False  # True → prompt embeds external content; never route to agy (no tool allowlist there)
```

In `synthesize()`, replace `for provider in (self.providers or self.PROVIDERS):` with:

```python
        if self.providers:
            providers = self.providers
        elif self.untrusted_input:
            providers = [p for p in self.PROVIDERS if p["name"] == "claude"]
        else:
            providers = self.PROVIDERS
        for provider in providers:
```

- [ ] **Step 4: Set `untrusted_input = True`** on `NewsBriefingAgent` (RSS content in prompts; belt-and-braces with its existing Claude-first `providers`).

- [ ] **Step 5: Run tests — PASS; commit** `git commit -m "feat(base): untrusted_input guard routes prompts away from unsandboxed agy"`

---

### Task 4: Cron consolidation — one owner, args support

**Files:**
- Modify: `agents/base.py` (add `cron_entries`), `agents/librarian.py` (override), `agents/runner.py:113-196` (`cmd_install_cron`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `BaseAgent.cron_entries() -> list[tuple[str, str]]` classmethod returning `(schedule, argv_suffix)` pairs; default `[(cls.schedule, cls.name)]` when scheduled. `install-cron` emits every pair inside the managed block and warns about `run-agent.sh` lines outside it.

- [ ] **Step 1: Failing tests** (append to `tests/test_runner.py`):

```python
def test_librarian_cron_entries_cover_both_modes():
    from agents.librarian import LibrarianAgent
    assert LibrarianAgent.cron_entries() == [
        ("0 6 * * 0", "librarian --mode audit"),
        ("0 6 * * 1-6", "librarian --mode watch"),
    ]


def test_default_cron_entries_from_schedule():
    from agents.plant_agent import PlantAgent
    assert PlantAgent.cron_entries() == [("0 * * * *", "plant-agent")]
```

- [ ] **Step 2: Run — FAIL** (no `cron_entries`).

- [ ] **Step 3: Implement.** `agents/base.py`:

```python
    @classmethod
    def cron_entries(cls) -> list[tuple[str, str]]:
        """(cron_schedule, argv_suffix) pairs for install-cron. Override for agents
        needing multiple entries or extra args (e.g. librarian --mode)."""
        return [(cls.schedule, cls.name)] if cls.schedule else []
```

`agents/librarian.py` (`LibrarianAgent`, keep `schedule = ""` so nothing double-emits):

```python
    @classmethod
    def cron_entries(cls) -> list[tuple[str, str]]:
        return [("0 6 * * 0", f"{cls.name} --mode audit"),
                ("0 6 * * 1-6", f"{cls.name} --mode watch")]
```

`agents/runner.py` `cmd_install_cron`: iterate `cls.cron_entries()` instead of `cls.schedule/cls.name`; validate the schedule with `_CRON_SCHEDULE_RE` and **each whitespace-separated token** of the suffix against `re.fullmatch(r"--?[a-z\-]+|[a-z0-9\-]+", token)` (fail closed as now); emit `f"{sched} {run_agent} {suffix} >> {log_file} 2>&1"`. After building `filtered`, add:

```python
    strays = [l for l in filtered if "run-agent.sh" in l]
    if strays:
        print("WARNING: unmanaged run-agent.sh crontab lines (move into managed block):", file=sys.stderr)
        for l in strays:
            print(f"  {l}", file=sys.stderr)
```

- [ ] **Step 4: Run tests — PASS.**

- [ ] **Step 5: Reconcile the live crontab** (one-time, after review of output):

```bash
crontab -l | grep -v '^0 \* \* \* \* /home/cian/git/ai-agents/run-agent.sh plant-agent' \
           | grep -v '^0 \* \* \* \* /home/cian/git/ai-agents/run-agent.sh agent-health' \
           | grep -v 'run-agent.sh librarian' | crontab -
python3 -m agents install-cron
crontab -l   # verify: all 5 agents (7 entries) inside the managed block, no duplicates
```

- [ ] **Step 6: Commit** — `git commit -m "feat(cron): cron_entries() classmethod; install-cron owns all entries incl. librarian modes"`

---

### Task 5: Extract `agents/gmail_client.py` (direct send, no MCP)

**Files:**
- Create: `agents/gmail_client.py`
- Modify: `mcp-servers/gmail_server.py` (import from the new module, delete moved code)
- Test: `tests/test_gmail_client.py` (move/adapt `tests/test_token_save_atomic.py` target)

**Interfaces:**
- Produces: `agents.gmail_client.send_email(to: str, subject: str, body: str, mime_type: str = "text/html") -> dict`; also `get_access_token()`, `gmail_request(method, path, params=None, body=None)`, `load_tokens()`, `save_tokens(tokens)`, `get_profile()` — signatures identical to today's `gmail_server.py:24-131`.

- [ ] **Step 1: Failing test** (`tests/test_gmail_client.py`):

```python
import base64
import json
from unittest.mock import patch
from agents import gmail_client


def test_send_email_posts_base64_mime(monkeypatch):
    monkeypatch.setattr(gmail_client, "get_access_token", lambda: "tok")
    monkeypatch.setattr(gmail_client, "_get_sender_address", lambda: "me@x.ie")
    captured = {}

    def fake_request(method, path, params=None, body=None):
        captured.update(method=method, path=path, body=body)
        return {"id": "sent-1"}

    monkeypatch.setattr(gmail_client, "gmail_request", fake_request)
    out = gmail_client.send_email("to@x.ie", "Subj", "<b>hi</b>")
    assert out == {"id": "sent-1"}
    assert captured["method"] == "POST" and captured["path"] == "/users/me/messages/send"
    raw = base64.urlsafe_b64decode(captured["body"]["raw"] + "==")
    assert b"Subject: Subj" in raw and b"to@x.ie" in raw
```

- [ ] **Step 2: Run — FAIL** (module missing).

- [ ] **Step 3: Implement** — move, verbatim, from `mcp-servers/gmail_server.py` into `agents/gmail_client.py`: `TOKEN_FILE`, `CLIENT_ID`, `CLIENT_SECRET`, `load_tokens`, `save_tokens`, `get_access_token`, `_SENDER_ADDRESS`, `_get_sender_address`, `gmail_request`, `get_profile`, `send_email`, `create_draft` (imports: `base64, json, os, tempfile, time, urllib.request, urllib.parse, urllib.error`, MIME classes). In `gmail_server.py`, delete the moved code and add at top:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.gmail_client import (  # noqa: E402
    load_tokens, save_tokens, get_access_token, gmail_request,
    get_profile, send_email, create_draft, _get_sender_address,
)
```

Update `tests/test_token_save_atomic.py` imports to `agents.gmail_client` (it currently imports from the server file).

- [ ] **Step 4: Run — PASS**, including `tests/test_token_save_atomic.py`.

- [ ] **Step 5: Commit** — `git commit -m "refactor(gmail): extract gmail_client for direct Python sends; MCP server imports it"`

---

### Task 6: Plant status email — deterministic send

**Files:**
- Modify: `agents/plant_agent.py:180-198` (`_send_status_email`), delete `agents/prompts/plant_status_email.md` usage
- Test: `tests/test_plant_agent.py`

**Interfaces:**
- Consumes: `agents.gmail_client.send_email` (Task 5). Recipient: `os.environ.get("AGENT_EMAIL_TO", "cianohughes@gmail.com")`.
- Produces: `_build_status_html(plants, weather_cache, today) -> str` module function in `plant_agent.py`.

- [ ] **Step 1: Failing tests** (append to `tests/test_plant_agent.py`):

```python
def test_build_status_html_contains_rows():
    from agents.plant_agent import _build_status_html
    plants = [{"name": "Aloe", "frequency_days": 7, "last_watered": "2026-06-30"}]
    html = _build_status_html(plants, {}, date(2026, 7, 3))
    assert "<table" in html and "Aloe" in html and "Overdue" not in html


def test_send_status_email_uses_gmail_client(monkeypatch, agent):  # reuse existing agent fixture
    sent = {}
    monkeypatch.setattr("agents.plant_agent.send_email",
                        lambda to, subject, body, **kw: sent.update(to=to, subject=subject) or {"id": "1"})
    agent.context["plan"] = {"plants": [], "weather_cache": {}}
    agent.set_state("last_send_status_email", None) if False else None
    out = agent._send_status_email()
    assert out == {"sent": True, "plants": 0} and "Plant Status" in sent["subject"]
```

(Adapt fixture names to what `tests/test_plant_agent.py` already defines — it has step-gating tests with a constructed agent.)

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** in `plant_agent.py` — add `import os` and `from .gmail_client import send_email`; add:

```python
def _build_status_html(plants: list, weather_cache: dict, today) -> str:
    md = _build_status_table(plants, weather_cache, today)
    rows = [l for l in md.splitlines() if l.startswith("|") and "---" not in l]
    if not rows:
        return f"<p>{md}</p>"
    cells = lambda r: "".join(f"<td style='padding:4px 10px'>{c.strip()}</td>"
                              for c in r.strip("|").split("|"))
    head = rows[0].strip("|").split("|")
    thead = "".join(f"<th align='left' style='padding:4px 10px'>{c.strip()}</th>" for c in head)
    body = "".join(f"<tr>{cells(r)}</tr>" for r in rows[1:])
    return (f"<h3>🌿 Plant status — {today}</h3>"
            f"<table border='0' cellspacing='0'><tr>{thead}</tr>{body}</table>")
```

Replace the body of `_send_status_email` after the gate check:

```python
        plants = self.context["plan"]["plants"]
        weather_cache = self.context["plan"]["weather_cache"]
        today = datetime.now(timezone.utc).date()
        html = _build_status_html(plants, weather_cache, today)
        send_email(os.environ.get("AGENT_EMAIL_TO", "cianohughes@gmail.com"),
                   f"🌿 Plant Status — {today.isoformat()}", html)
        self._mark_ran("send_status_email")
        return {"sent": True, "plants": len(plants)}
```

Delete `agents/prompts/plant_status_email.md` (`git rm`). Keep `side_effects: True` on the step (an HTTP send can still time out after delivery).

- [ ] **Step 4: Run — PASS** (`.venv/bin/pytest tests/test_plant_agent.py -q`).

- [ ] **Step 5: Commit** — `git commit -m "feat(plant): send status email directly via gmail_client — no LLM transport"`

---

### Task 7: News briefing — deterministic send

**Files:**
- Modify: `agents/news_briefing.py:477-505` (`_run_briefing`), `agents/prompts/news_briefing.md` (delete)
- Test: `tests/test_news_briefing.py`

**Interfaces:**
- Consumes: `agents.gmail_client.send_email`; existing `_build_html_email(news_data, today) -> str`.

- [ ] **Step 1: Failing test** (append to `tests/test_news_briefing.py`, matching its existing agent-construction pattern):

```python
def test_run_briefing_sends_html_directly(monkeypatch, agent_with_news):
    sent = {}
    monkeypatch.setattr("agents.news_briefing.send_email",
                        lambda to, subject, body, **kw: sent.update(subject=subject, body=body) or {"id": "1"})
    result = agent_with_news._run_briefing()
    assert result["sent"] is True
    assert "<html" in sent["body"] or "<h" in sent["body"]
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** — in `_run_briefing`, replace the prompt-template + `self.synthesize(prompt)` block with:

```python
        html_email = self._build_html_email(news_data, today)
        send_email(os.environ.get("AGENT_EMAIL_TO", "cianohughes@gmail.com"),
                   f"📰 News Briefing — {today}", html_email)
        return {"sent": True, "articles": sum(len(v) for v in news_data.values())}
```

(add `import os` and `from .gmail_client import send_email`). Keep the translate/score steps and their `synthesize()` calls — those are genuine LLM work. `git rm agents/prompts/news_briefing.md`. Note: the step keeps `side_effects: True`.

- [ ] **Step 4: Run — PASS**, full suite green.

- [ ] **Step 5: Commit** — `git commit -m "feat(news): direct Gmail send for briefing; LLM reserved for translation/scoring"`

---

### Task 8: PlantStore — row-per-plant storage, plant-agent namespace, field updates

Fixes the cross-process lost-update race and the legacy `daily-briefing` namespace in one encapsulated place.

**Files:**
- Modify: `agents/db.py` (SCHEMA + row helpers), `agents/plant_model.py` (PlantStore internals)
- Test: `tests/test_plant_model.py`

**Interfaces:**
- Produces (new): `AgentDB.get_plant_rows() -> list[dict]`, `AgentDB.upsert_plant_row(name: str, data: dict)`, `AgentDB.delete_plant_row(name: str)`, `AgentDB.replace_plant_rows(rows: list[dict])`; `PlantStore.update_fields(name: str, **fields) -> Plant | None`; `PlantStore.add(plant: Plant) -> None`; `PlantStore.remove(name: str) -> bool`; `PlantStore.get_plants_raw() -> list[dict]`.
- Unchanged: `get_plants()`, `save_plants()`, `get_plant()`, `update_plant()` keep their signatures so `plant_ui/server.py` keeps working untouched.

- [ ] **Step 1: Failing tests** (append to `tests/test_plant_model.py`):

```python
def test_store_migrates_legacy_blob_to_rows(tmp_path):
    from agents.db import AgentDB
    from agents.plant_model import PlantStore
    db = AgentDB(tmp_path / "t.db")
    db.set_state("daily-briefing", "plants", [{"name": "Aloe", "frequency_days": 10}])
    db.close()
    store = PlantStore(tmp_path / "t.db")
    assert [p.name for p in store.get_plants()] == ["Aloe"]
    assert store._db.get_plant_rows()  # rows populated, legacy blob no longer authoritative
    store.close()


def test_update_fields_touches_only_one_row(tmp_path):
    from agents.plant_model import PlantStore, Plant
    from datetime import date
    store = PlantStore(tmp_path / "t.db")
    store.add(Plant(name="Aloe", frequency_days=10, baseline_frequency_days=10, last_watered=date.today()))
    store.add(Plant(name="Yucca", frequency_days=14, baseline_frequency_days=14, last_watered=date.today()))
    out = store.update_fields("aloe", frequency_days=5)
    assert out.frequency_days == 5
    assert store.get_plant("Yucca").frequency_days == 14
    store.close()
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement.** `agents/db.py` — append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS plants (
    name    TEXT PRIMARY KEY,
    data    TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT (datetime('now'))
);
```

and add methods (same lock/commit pattern as the weather cache):

```python
    def get_plant_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM plants ORDER BY name").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def upsert_plant_row(self, name: str, data: dict):
        with self._lock:
            self._conn.execute(
                "INSERT INTO plants (name, data, updated) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                (name.lower(), json.dumps(data)))
            self._conn.commit()

    def delete_plant_row(self, name: str):
        with self._lock:
            self._conn.execute("DELETE FROM plants WHERE name = ?", (name.lower(),))
            self._conn.commit()

    def replace_plant_rows(self, rows: list[dict]):
        with self._lock:
            self._conn.execute("DELETE FROM plants")
            for data in rows:
                self._conn.execute("INSERT INTO plants (name, data) VALUES (?, ?)",
                                   (data["name"].lower(), json.dumps(data)))
            self._conn.commit()
```

`agents/plant_model.py` — PlantStore internals:

```python
    LEGACY_AGENT, LEGACY_KEY = "daily-briefing", "plants"

    def _ensure_migrated(self) -> None:
        if self._db.get_plant_rows():
            return
        legacy = self._db.get_state(self.LEGACY_AGENT, self.LEGACY_KEY) or []
        if legacy:
            self._db.replace_plant_rows([self._migrate(r).model_dump(mode="json") for r in legacy])
            # keep the legacy blob for rollback; rows are now authoritative

    def get_plants(self) -> list[Plant]:
        self._ensure_migrated()
        return [self._migrate(r) for r in self._db.get_plant_rows()]

    def get_plants_raw(self) -> list[dict]:
        return [p.model_dump(mode="json") for p in self.get_plants()]

    def save_plants(self, plants: list[Plant]) -> None:
        self._db.replace_plant_rows([p.model_dump(mode="json") for p in plants])

    def add(self, plant: Plant) -> None:
        self._ensure_migrated()
        self._db.upsert_plant_row(plant.name, plant.model_dump(mode="json"))

    def remove(self, name: str) -> bool:
        p = self.get_plant(name)
        if not p:
            return False
        self._db.delete_plant_row(p.name)
        return True

    def update_plant(self, updated: Plant) -> bool:
        if not self.get_plant(updated.name):
            return False
        self._db.upsert_plant_row(updated.name, updated.model_dump(mode="json"))
        return True

    def update_fields(self, name: str, **fields) -> Optional[Plant]:
        p = self.get_plant(name)
        if not p:
            return None
        updated = p.model_copy(update=fields)
        if updated.name.lower() != p.name.lower():
            self._db.delete_plant_row(p.name)
        self._db.upsert_plant_row(updated.name, updated.model_dump(mode="json"))
        return updated
```

Delete the `DB_AGENT`/`DB_KEY` constants (the store no longer writes state-blob).

- [ ] **Step 4: Run — PASS**, plus `tests/test_plant_ui_api.py` (PWA still green via unchanged public API) and `tests/test_db_concurrency.py`.

- [ ] **Step 5: Commit** — `git commit -m "feat(plants): row-per-plant storage with legacy migration; update_fields kills blob lost-updates"`

---

### Task 9: Route all plant access through PlantStore

**Files:**
- Modify: `telegram-bot/tools.py` (10 functions), `agents/plant_agent.py:147,164,177,318`, `agents/daily_briefing.py:68`
- Test: `telegram-bot/test_tools.py` (existing tests keep passing — they assert on returned strings)

**Interfaces:**
- Consumes: Task 8's `PlantStore` (`get_plants_raw`, `update_fields`, `add`, `remove`).

- [ ] **Step 1: Run existing suites first** (`.venv/bin/pytest telegram-bot/test_tools.py tests/test_plant_agent.py tests/test_daily_briefing.py -q`) — green baseline; these are the regression net for a mechanical rewire.

- [ ] **Step 2: Rewire `tools.py`.** Add `from agents.plant_model import PlantStore, Plant` and helper:

```python
def _store() -> PlantStore:
    return PlantStore(DB_PATH)
```

Transformation rule (apply to every function that reads/writes plants): replace the `db = AgentDB(DB_PATH) … db.get_state("daily-briefing","plants") … db.set_state(…) … db.close()` block with PlantStore calls; dict access stays possible via `get_plants_raw()`. The full new bodies:

```python
def get_all_plants() -> list[dict]:
    try:
        s = _store(); out = s.get_plants_raw(); s.close(); return out
    except Exception:
        return []

def water_plant(plant_name: str) -> str:
    try:
        s = _store()
        p = s.update_fields(plant_name, last_watered=date.today())
        names = ", ".join(x.name for x in s.get_plants()); s.close()
        if not p:
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        return f"{p.name} marked as watered today ({p.last_watered.isoformat()})."
    except Exception as e:
        return f"Failed to update plant: {e}"

def water_plants(location: str) -> str:
    try:
        s = _store()
        targets = [p for p in s.get_plants() if p.location == location]
        for p in targets:
            s.update_fields(p.name, last_watered=date.today())
        s.close()
        if not targets:
            return f"No {location} plants found."
        names = ", ".join(p.name for p in targets)
        return f"Marked {len(targets)} {location} plant{'s' if len(targets) != 1 else ''} as watered today: {names}."
    except Exception as e:
        return f"Failed to update plants: {e}"

def remove_plant(plant_name: str) -> str:
    try:
        s = _store()
        p = s.get_plant(plant_name)
        if not p:
            names = ", ".join(x.name for x in s.get_plants()); s.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        s.remove(p.name); s.close()
        return f"{p.name} removed from plant tracker."
    except Exception as e:
        return f"Failed to remove plant: {e}"
```

`add_plant`: build a `Plant(...)` (same fields as today incl. researched sensitivity) and call `s.add(plant)` after the duplicate check via `s.get_plant(name)` — exact-match check: `any(p.name.lower() == name_lower for p in s.get_plants())`. `update_plant`: collect the same `changes` list, then one `s.update_fields(match.name, **kwargs)`. `set_plant_frequency`: `s.update_fields(match.name, baseline_frequency_days=target, frequency_days=eff)` after computing `eff, _ = weather_adjusted_frequency(match.model_dump(mode="json"), fetch_weather())`. `save_plant_assessment`: `s.update_fields(match.name, last_assessment={"date": today, "summary": summary})` (Pydantic coerces the dict) + keep the frontmatter refresh. `get_plant_status` and `get_plant`: read via `get_plants_raw()`; keep `_find_plant` for dict matching.

- [ ] **Step 3: Rewire agents.** `plant_agent.py`: `plan()` → `PlantStore(self.db.db_path)`-backed `get_plants_raw()`; every `self.db.set_state("daily-briefing", "plants", plants)` → loop `store.update_fields(p["name"], frequency_days=..., baseline_frequency_days=..., needs_photo=...)` for only the plants actually changed (track changed names in a set instead of the `changed` bool). `daily_briefing.py:68` → `PlantStore(self.db.db_path).get_plants_raw()`. Also in `plant_agent.py`, replace both inline slug constructions (`plant["name"].lower().replace(" ", "-").replace("/", "-")` at lines 211 and 266) with `from .plant_profiles import profile_path` and `profile_path(plant["name"])` — one slug implementation.

- [ ] **Step 4: Run full suite — PASS.** Some `test_tools.py` tests monkeypatch `AgentDB`; update those fixtures to seed via `PlantStore.add` instead.

- [ ] **Step 5: Commit** — `git commit -m "refactor(plants): single PlantStore data layer across tools, agents, briefing"`

---

### Task 10: Shared `agents/plant_assessment.py`

**Files:**
- Create: `agents/plant_assessment.py`, `tests/test_plant_assessment.py`
- Modify: `telegram-bot/bot.py` (delete lines 82-306 duplicates, import), `plant_ui/server.py` (delete lines 138-236 duplicates, import)

**Interfaces:**
- Produces: `load_species_context(plant_name: str) -> str`; `parse_assessment_response(raw: str, plant_name: str) -> tuple[str, dict | None]` (display_text, parsed — fence-strip + JSON extract + markdown salvage in one call); `build_assessment_display(parsed: dict, plant_name: str) -> str`; `format_care_action_lines(actions: list) -> list[str]`; `format_care_actions_for_profile(actions: list) -> str`; `extract_assessment_from_text(raw: str) -> dict | None`.

- [ ] **Step 1: Failing tests** (`tests/test_plant_assessment.py`):

```python
from agents.plant_assessment import (
    parse_assessment_response, build_assessment_display, format_care_actions_for_profile,
)

def test_parse_json_in_code_fence():
    raw = '```json\n{"status": "Healthy", "summary": "fine", "observations": [], "care_actions": []}\n```'
    display, parsed = parse_assessment_response(raw, plant_name="Aloe")
    assert parsed["status"] == "Healthy" and "Aloe" in display

def test_salvages_markdown_prose():
    raw = "**Status:** Stressed\n**Summary:** droopy leaves"
    display, parsed = parse_assessment_response(raw, plant_name="Aloe")
    assert parsed["status"] == "Stressed" and "droopy" in display

def test_care_actions_sorted_by_priority():
    block = format_care_actions_for_profile([
        {"action": "b", "priority": "low"}, {"action": "a", "priority": "high"},
    ])
    assert block.index("a") < block.index("b")
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** — move the `telegram-bot/bot.py` versions (the more refined copies: `_WATERING_REC_MAP`, `_extract_assessment_from_text`, `_load_species_context`, `_STATUS_EMOJI`, `_PRIORITY_EMOJI`, `_PRIORITY_ORDER`, `_sorted_care_actions`, `_format_care_action_lines`, `_format_care_actions_for_profile`, `_build_assessment_display`) into `agents/plant_assessment.py` with the leading underscores dropped, plus the JSON-parse/salvage block from `bot._analyze_plant_image` as:

```python
def parse_assessment_response(raw: str, plant_name: str) -> tuple[str, dict | None]:
    """(display_text, parsed) — parsed None if even markdown salvage fails."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = extract_assessment_from_text(raw)
    if parsed:
        return build_assessment_display(parsed, plant_name), parsed
    return f"*{plant_name}*\n\n{raw}", None
```

(`build_assessment_display` takes `plant_name: str` — bot call sites pass `plant["name"]`.) In `bot.py` and `plant_ui/server.py`, delete the local copies and import from `agents.plant_assessment`; `bot._analyze_plant_image` and the PWA `upload_photo` parse blocks each collapse to one `parse_assessment_response(raw_response, plant.name)` call.

- [ ] **Step 4: Run full suite — PASS** (`test_bot.py` and `test_plant_ui_api.py` exercise both call sites).

- [ ] **Step 5: Commit** — `git commit -m "refactor(assessment): single plant_assessment module; bot + PWA are thin adapters"`

---

### Task 11: Intelligence run fixes — care-task lifecycle + bounded context

**Files:**
- Modify: `agents/plant_agent.py:200-330`, `plant_ui/server.py:453-460` (`complete_care_task`)
- Test: `tests/test_plant_agent.py`, `tests/test_plant_ui_api.py`

**Interfaces:**
- Consumes: `plant_profiles.read_profile_context(plant_name, max_assessments=2)` (exists, used by claude_backend); `AgentDB.mark_seen/check_dedup`.
- Produces: completed care tasks recorded as `seen(agent='plant-agent', category='completed_action', identifier=f"{plant}:{action}")`.

- [ ] **Step 1: Failing tests:**

```python
# tests/test_plant_agent.py
def test_pending_actions_cleared_when_no_pruning(agent):
    agent.set_state("pending_plant_actions", [{"plant": "Aloe", "action": "old"}])
    agent.context["weather"] = None
    agent._apply_intelligence_output('{"plants": [], "pruning": []}', [])
    assert agent.get_state("pending_plant_actions") == []

def test_completed_action_not_resurrected(agent):
    agent.db.mark_seen("plant-agent", "completed_action", "Aloe:prune dead leaf")
    agent.context["weather"] = None
    agent._apply_intelligence_output(
        '{"plants": [], "pruning": [{"name": "Aloe", "action": "prune dead leaf", "reason": ""}]}', [])
    assert agent.get_state("pending_plant_actions") == []

# tests/test_plant_ui_api.py
def test_complete_care_task_marks_seen(client, db):
    db.set_state("plant-agent", "pending_plant_actions", [{"plant": "Aloe", "action": "prune"}])
    r = client.post("/api/care-tasks/complete", json={"plant": "Aloe", "action": "prune"})
    assert r.status_code == 200
    assert db.check_dedup("plant-agent", "completed_action", "Aloe:prune")
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement.** `plant_agent._apply_intelligence_output` — replace the `if result.pruning:` block with an unconditional write plus dedup:

```python
        fresh = [
            {"plant": p.name, "action": p.action, "reason": p.reason, "date": today}
            for p in result.pruning
            if not self.db.check_dedup(self.name, "completed_action", f"{p.name}:{p.action}")
        ]
        self.set_state("pending_plant_actions", fresh)
```

`plant_ui/server.py` `complete_care_task` — after filtering `updated`, add:

```python
    db.mark_seen("plant-agent", "completed_action", f"{data.plant}:{data.action}")
```

Bounded context in `_intelligence_run` — replace the full-profile read loop with:

```python
        from .plant_profiles import read_profile_context
        profiles = []
        for plant in plants:
            ctx = read_profile_context(plant["name"])
            profiles.append(f"### {plant['name']}\n" + (ctx or
                f"No profile yet. Location: {plant.get('location', 'unknown')}, "
                f"frequency: {plant['frequency_days']} days, "
                f"sensitivity: {plant.get('water_sensitivity', 'medium')}."))
```

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** — `git commit -m "fix(plant): care-task lifecycle (clear+dedup) and token-bounded intelligence context"`

---

### Task 12: Hygiene sweep

**Files:**
- Delete: `skills/vidqueue/`
- Modify: `plant_ui/server.py:440`, `plant_ui/static/app.js:223`, `tests/test_plant_ui_api.py:159`, `telegram-bot/tools.py` (combined research), `CLAUDE.md`

**Interfaces:**
- Produces: `research_plant_traits(plant_name: str) -> dict` in `tools.py` with keys `frequency_days:int|None, sunlight:str, water_sensitivity:str` from ONE agy call; `add_plant` consumes it.

- [ ] **Step 1: Failing test** (append to `telegram-bot/test_tools.py`):

```python
def test_research_plant_traits_single_call(monkeypatch):
    import tools
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": "",
                              "stdout": '{"frequency_days": 12, "sunlight": "full sun", "water_sensitivity": "high"}'})()
    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.research_plant_traits("Aloe Vera")
    assert out == {"frequency_days": 12, "sunlight": "full sun", "water_sensitivity": "high"}
    assert len(calls) == 1
```

- [ ] **Step 2: Run — FAIL; implement** in `tools.py`:

```python
def research_plant_traits(plant_name: str) -> dict:
    """One agy call for frequency + sunlight + sensitivity. Missing/invalid → safe defaults."""
    default = {"frequency_days": None, "sunlight": "", "water_sensitivity": "medium"}
    prompt = (
        f"For a {plant_name} houseplant kept indoors, reply with ONLY minified JSON, no prose: "
        '{"frequency_days": <int days between waterings>, '
        '"sunlight": <"full sun"|"partial shade"|"shade">, '
        '"water_sensitivity": <"high"|"medium"|"low">}'
    )
    try:
        res = subprocess.run(["agy", "-y", "-o", "text"], input=prompt,
                             capture_output=True, text=True, timeout=45, cwd=str(REPO_ROOT))
        if res.returncode != 0 or not res.stdout.strip():
            return default
        m = re.search(r"\{.*\}", res.stdout, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        return {
            "frequency_days": int(data["frequency_days"]) if str(data.get("frequency_days", "")).isdigit() else None,
            "sunlight": data.get("sunlight") if data.get("sunlight") in SUNLIGHT_VALUES else "",
            "water_sensitivity": data.get("water_sensitivity") if data.get("water_sensitivity") in SENSITIVITY_VALUES else "medium",
        }
    except Exception:
        return default
```

(add `import json` at top of tools.py). In `add_plant`, replace the `research_plant_water_sensitivity(name)` call with `traits = research_plant_traits(name)`; use `traits["water_sensitivity"]`, and when `sunlight` wasn't supplied use `traits["sunlight"]`. Keep the three single-trait functions (still exposed as concierge tools for ad-hoc questions).

- [ ] **Step 3: Fix the typo** — `waterED_count` → `watered_count` in `plant_ui/server.py:440`, `plant_ui/static/app.js:223`, `tests/test_plant_ui_api.py:159` (all three together, one grep to confirm zero remaining: `grep -rn waterED_count .`).

- [ ] **Step 4: Delete vidqueue** — `git rm -r --cached skills/vidqueue 2>/dev/null; rm -rf skills/vidqueue` (it's only `__pycache__`, likely untracked — `rm -rf` suffices).

- [ ] **Step 5: Update CLAUDE.md** —
  - Skills section: keep `mealsave`, `free-time`; note free-time is now a concierge tool + skill (bot retired, Task 2).
  - Dashboards line: remove the claim of "Design Docs" and "Systems" hubs (they don't exist) or create them — remove, YAGNI.
  - Plant tracker section: plants now live in a dedicated `plants` table (row per plant) managed by `PlantStore`; the `daily-briefing` state blob is legacy/rollback only.
  - Email: agents send email directly via `agents/gmail_client.py`; LLM+MCP no longer used as transport.
  - Cron: `install-cron` owns ALL entries via `cron_entries()`, including librarian modes.

- [ ] **Step 6: Regenerate the codebase map** — `python3 scripts/gen_codebase_map.py` (new files: `agents/free_time.py`, `agents/gmail_client.py`, `agents/plant_assessment.py`; deletions).

- [ ] **Step 7: Full suite + security audit** — `.venv/bin/pytest tests/ telegram-bot/ -q` then `./run-agent.sh security-audit` before any push (project rule).

- [ ] **Step 8: Commit** — `git commit -m "chore: hygiene sweep — vidqueue removal, watered_count, single-call plant research, CLAUDE.md sync"`

---

## Execution order & independence

| Batch | Tasks | Depends on |
|---|---|---|
| A Security | 1, 2, 3 | — |
| B Cron | 4 | — |
| C Email | 5 → 6, 7 | 5 |
| D Plant data | 8 → 9 → 10 | 8 |
| E Intelligence + hygiene | 11, 12 | 12 partially on 2 (CLAUDE.md wording) |

## Verification (whole plan)

1. `.venv/bin/pytest tests/ telegram-bot/ -q` — all green.
2. `crontab -l` — 7 entries, all inside the managed block, no duplicates.
3. `./run-agent.sh plant-agent` on the server — status email arrives; run recorded `success`; no `synthesize` call in the send step (check `output/cron.log`).
4. Telegram: message the concierge (agy path works), send a plant photo (assessment via shared module), `suggest free time tasks 30` (new tool).
5. FloraPulse: water a plant, complete a care task, re-run intelligence — task does not resurrect.
6. `./run-agent.sh security-audit` — no Critical/High on the diff before push.
