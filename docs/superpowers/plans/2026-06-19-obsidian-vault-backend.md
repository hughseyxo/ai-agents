# Obsidian Vault Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the project's markdown into token-efficient, curated, queryable Obsidian notes and host them as a Tailscale-only vault synced to native Obsidian on phone/PC.

**Architecture:** Notes stay plain `.md` that agents read/write directly. On-disk structure is made token-lean first (frontmatter projections, curated sections, `status`-filtered atomic notes). Then CouchDB + livesync-bridge — added as two services to the **existing yopflix seedbox Docker stack** (`~/git/yopflix/seedbox/docker-compose.yaml`) — mirror the on-disk vault ⇄ native Obsidian clients over Tailscale. No standalone stack, no new systemd unit; the services come up with the rest of the seedbox via its existing `run-seedbox.sh`. SQLite stays canonical for operational data; markdown is the intelligence layer.

**Tech Stack:** Python 3 (agents), PyYAML (frontmatter), pytest, the existing yopflix seedbox Docker Compose stack, CouchDB, vrtmrz/livesync-bridge (Deno), Claude Code hooks.

## Global Constraints

- **Token-efficiency is the success metric** — frontmatter projection, curated "current state" sections (rewritten not appended), `status:` field so consumers load only `active`, bounded history.
- **SQLite (`data/agents.db`) stays canonical** for plant data; frontmatter is a regenerated projection — never hand-edit it. FloraPulse PWA stays task source of truth.
- **TDD** — write the failing test first (memory `feedback_tdd`).
- **Dual-CLI rule** — agent Python must run under both Claude and Antigravity; no CLI-specific deps.
- **No secrets in git** — CouchDB creds in gitignored `.env`.
- **CouchDB never binds `0.0.0.0`** — Tailscale IP (`100.96.86.73`) only.
- **Commit after each task.** Do not push (security-audit-before-push rule applies separately).
- Spec: `docs/superpowers/specs/2026-06-19-obsidian-vault-backend-design.md`.

---

## Phase 1 — Plant profile token-efficient restructure (Python, TDD)

### Task 1: Frontmatter read/write helpers in `plant_profiles.py`

**Files:**
- Modify: `agents/plant_profiles.py`
- Test: `tests/test_plant_profiles.py`

**Interfaces:**
- Produces: `parse_frontmatter(text: str) -> tuple[dict, str]` (returns `(meta, body)`; `({}, text)` if no frontmatter); `upsert_frontmatter(plant_name: str, fields: dict) -> bool` (writes/replaces the leading `---` YAML block, preserves body).
- Consumes: existing `profile_path()`, `write_profile_atomic()`.

- [ ] **Step 1: Verify PyYAML is available**

Run: `python3 -c "import yaml; print(yaml.__version__)"`
Expected: a version prints. If `ModuleNotFoundError`, add `pyyaml` to `plant_ui/requirements.txt` and `pip install pyyaml` in `.venv`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_plant_profiles.py
import agents.plant_profiles as pp

def test_parse_frontmatter_roundtrip():
    text = "---\ntype: plant\nlocation: indoor\n---\n# Monstera\nbody\n"
    meta, body = pp.parse_frontmatter(text)
    assert meta == {"type": "plant", "location": "indoor"}
    assert body == "# Monstera\nbody\n"

def test_parse_frontmatter_absent():
    meta, body = pp.parse_frontmatter("# No fm\n")
    assert meta == {}
    assert body == "# No fm\n"

def test_upsert_frontmatter_preserves_body(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "monstera.md"
    p.write_text("# Monstera\n\n## Current Observations\n- vigorous\n")
    pp.upsert_frontmatter("Monstera", {"type": "plant", "needs_photo": False})
    out = p.read_text()
    assert out.startswith("---\n")
    assert "## Current Observations" in out
    assert "- vigorous" in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plant_profiles.py -k frontmatter -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'parse_frontmatter'`).

- [ ] **Step 4: Implement**

```python
# agents/plant_profiles.py — add near top
import yaml

def parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end]) or {}
            return meta, text[end + 5:]
    return {}, text

def upsert_frontmatter(plant_name: str, fields: dict) -> bool:
    path = profile_path(plant_name)
    if not path.exists():
        return False
    _, body = parse_frontmatter(path.read_text())
    fm = yaml.safe_dump(fields, sort_keys=False, default_flow_style=False).strip()
    write_profile_atomic(path, f"---\n{fm}\n---\n{body}")
    return True
```

Note: `profile_path()` already resolves under the profiles dir; confirm the module exposes `PROFILES_DIR` (the dir `profile_path` uses). If it's a different name, alias it so the test monkeypatch target matches.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_plant_profiles.py -k frontmatter -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add agents/plant_profiles.py tests/test_plant_profiles.py plant_ui/requirements.txt
git commit -m "feat(plants): frontmatter parse/upsert helpers for profiles"
```

### Task 2: `rewrite_section()` — curate a named section in place

**Files:**
- Modify: `agents/plant_profiles.py`
- Test: `tests/test_plant_profiles.py`

**Interfaces:**
- Produces: `rewrite_section(plant_name: str, section: str, new_body: str) -> bool` — replaces the content under `## <section>` (up to the next `## ` or EOF) with `new_body`; creates the section at end if absent. Preserves frontmatter and all other sections.

- [ ] **Step 1: Write the failing test**

```python
def test_rewrite_existing_section(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "monstera.md"
    p.write_text("# M\n\n## Current Observations\n- old\n\n## History\n- 2026-05 repot\n")
    pp.rewrite_section("Monstera", "Current Observations", "- new fact\n")
    out = p.read_text()
    assert "- new fact" in out and "- old" not in out
    assert "## History\n- 2026-05 repot" in out   # other section intact

def test_rewrite_creates_absent_section(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "m.md"; p.write_text("# M\n")
    pp.rewrite_section("M", "Care Research", "- tolerates 7-14d\n")
    assert "## Care Research\n- tolerates 7-14d" in p.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plant_profiles.py -k rewrite -v`
Expected: FAIL (no attribute `rewrite_section`).

- [ ] **Step 3: Implement**

```python
import re

def rewrite_section(plant_name: str, section: str, new_body: str) -> bool:
    path = profile_path(plant_name)
    if not path.exists():
        return False
    text = path.read_text()
    if not new_body.endswith("\n"):
        new_body += "\n"
    pattern = re.compile(rf"(^## {re.escape(section)}\n)(.*?)(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{new_body}", text)
    else:
        text = text.rstrip("\n") + f"\n\n## {section}\n{new_body}"
    write_profile_atomic(path, text)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_plant_profiles.py -k rewrite -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/plant_profiles.py tests/test_plant_profiles.py
git commit -m "feat(plants): rewrite_section for in-place profile curation"
```

### Task 3: `read_profile_context()` — token-lean profile slice for assessments

**Files:**
- Modify: `agents/plant_profiles.py`
- Test: `tests/test_plant_profiles.py`

**Interfaces:**
- Produces: `read_profile_context(plant_name: str, max_assessments: int = 2) -> str` — returns frontmatter (as compact text) + `## Current Observations` + the most recent `max_assessments` dated entries under `## Health Assessments`. Returns `""` if profile absent.

- [ ] **Step 1: Write the failing test**

```python
def test_read_profile_context(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    (tmp_path / "m.md").write_text(
        "---\ntype: plant\nneeds_photo: false\n---\n# M\n"
        "## Current Observations\n- repot overdue\n\n"
        "## Health Assessments\n"
        "### 2026-06-18 — Healthy\n- delta a\n"
        "### 2026-06-06 — Healthy\n- delta b\n"
        "### 2026-05-01 — Healthy\n- old\n"
    )
    ctx = pp.read_profile_context("M", max_assessments=2)
    assert "repot overdue" in ctx
    assert "2026-06-18" in ctx and "2026-06-06" in ctx
    assert "2026-05-01" not in ctx        # bounded
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_plant_profiles.py -k profile_context -v` → FAIL.

- [ ] **Step 3: Implement**

```python
def read_profile_context(plant_name: str, max_assessments: int = 2) -> str:
    path = profile_path(plant_name)
    if not path.exists():
        return ""
    meta, body = parse_frontmatter(path.read_text())
    parts = []
    if meta:
        parts.append(", ".join(f"{k}={v}" for k, v in meta.items()))
    obs = re.search(r"^## Current Observations\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if obs:
        parts.append("## Current Observations\n" + obs.group(1).strip())
    ha = re.search(r"^## Health Assessments\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if ha:
        entries = re.split(r"(?=^### )", ha.group(1), flags=re.M)
        entries = [e for e in entries if e.strip()][:max_assessments]
        if entries:
            parts.append("## Recent Assessments\n" + "".join(entries).strip())
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/plant_profiles.py tests/test_plant_profiles.py
git commit -m "feat(plants): read_profile_context for assessment input"
```

### Task 4: Backfill script — restructure the 10 existing profiles

**Files:**
- Create: `scripts/backfill_plant_frontmatter.py`
- Test: manual (idempotent, one-time).

**Interfaces:**
- Consumes: `AgentDB` (read plant rows from SQLite state), `plant_profiles.upsert_frontmatter`, `rewrite_section`.

- [ ] **Step 1: Write the script**

```python
# scripts/backfill_plant_frontmatter.py
"""One-time: inject frontmatter projection (from SQLite) into existing plant profiles."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agents.db import AgentDB
from agents import plant_profiles as pp

def main():
    db = AgentDB()
    plants = db.get_state("plant_agent", "plants") or []   # adjust to actual plant-state accessor
    for plant in plants:
        fields = {
            "type": "plant",
            "location": plant["location"],
            "sunlight": plant.get("sunlight", "unknown"),
            "water_sensitivity": plant.get("water_sensitivity", "medium"),
            "baseline_frequency_days": plant["baseline_frequency_days"],
            "effective_frequency_days": plant["frequency_days"],
            "last_watered": plant.get("last_watered"),
            "needs_photo": plant.get("needs_photo", False),
            "tags": ["plant", plant["location"], f"sensitivity/{plant.get('water_sensitivity','medium')}"],
        }
        if pp.upsert_frontmatter(plant["name"], fields):
            print(f"frontmatter: {plant['name']}")

if __name__ == "__main__":
    main()
```

Note: replace `db.get_state(...)` with the actual accessor the plant agent uses to load plants (check `agents/plant_agent.py` for how it reads the plant list from `AgentDB`).

- [ ] **Step 2: Dry-run on a copy**

Run: `cp -r docs/plants /tmp/plants_bak && .venv/bin/python scripts/backfill_plant_frontmatter.py`
Expected: prints one line per plant; `git diff docs/plants/monstera-deliciosa.md` shows only an added frontmatter block, body unchanged.

- [ ] **Step 3: Verify PWA still renders**

Run: `.venv/bin/pytest tests/test_plant_ui_api.py -v`
Expected: PASS (frontmatter doesn't break profile read).

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_plant_frontmatter.py docs/plants/
git commit -m "feat(plants): backfill frontmatter projection into existing profiles"
```

### Task 5: `intelligence_run` regenerates frontmatter + curates

**Files:**
- Modify: `agents/plant_agent.py` (the `intelligence_run` step)
- Modify: `agents/prompts/plant_intelligence.md`
- Test: `tests/test_plant_agent.py`

**Interfaces:**
- Consumes: `plant_profiles.upsert_frontmatter`, `rewrite_section`.
- Produces: after a run, each profile has a regenerated frontmatter projection and a rewritten `## Current Observations`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plant_agent.py
def test_intelligence_run_refreshes_frontmatter(plant_agent, monkeypatch, tmp_profiles):
    # stub synthesize() to return a curated observations block
    monkeypatch.setattr(plant_agent, "synthesize",
        lambda *a, **k: "## Current Observations\n- curated single fact\n")
    plant_agent._intelligence_run()
    text = (tmp_profiles / "monstera-deliciosa.md").read_text()
    assert text.startswith("---\n") and "effective_frequency_days" in text
    assert text.count("- curated single fact") == 1   # rewritten, not appended
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement** — in `intelligence_run`, after computing per-plant data from SQLite, call `upsert_frontmatter(name, projection)` then `rewrite_section(name, "Current Observations", llm_observations)`. Parse the LLM output for the observations block.

- [ ] **Step 4: Update `plant_intelligence.md`** — instruct the model to: emit a concise `## Current Observations` (current/relevant only, no re-narration), prune routine "no change" assessments, roll older events into one-line `## History`, add `[[wikilinks]]` to related plants (same species/location) and `#tags`, and — when a horticultural knowledge gap blocks a decision — perform a web search and record cited findings under `## Care Research`.

- [ ] **Step 5: Run to verify pass** → PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/plant_agent.py agents/prompts/plant_intelligence.md tests/test_plant_agent.py
git commit -m "feat(plants): intelligence_run regenerates frontmatter + curates observations"
```

### Task 6: Assessment-context loop — feed profile into photo assessment

**Files:**
- Modify: `agents/prompts/plant_photo_assessment.md` (add `{{PROFILE_CONTEXT}}`)
- Modify: `telegram-bot/claude_backend.py` (`assess_image`), `plant_ui/server.py` (assessment endpoint), `telegram-bot/tools.py` / concierge `save_plant_assessment`
- Test: `telegram-bot/test_claude_backend.py`

**Interfaces:**
- Consumes: `plant_profiles.read_profile_context(name)`.

- [ ] **Step 1: Write the failing test** — assert `assess_image` injects the profile context string into the prompt passed to the CLI when a plant name is known.

```python
def test_assess_image_includes_profile_context(monkeypatch):
    monkeypatch.setattr(cb.plant_profiles, "read_profile_context", lambda n, **k: "KNOWN: repot overdue")
    captured = {}
    monkeypatch.setattr(cb, "_run_claude", lambda prompt, **k: captured.setdefault("p", prompt) or "ok")
    cb.assess_image("/tmp/x.jpg", plant_name="Monstera")
    assert "KNOWN: repot overdue" in captured["p"]
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement** — in `assess_image`, if `plant_name` given, read `read_profile_context(plant_name)` and substitute into the `{{PROFILE_CONTEXT}}` placeholder (empty string if none). Thread `plant_name` through the PWA assessment endpoint and concierge `save_plant_assessment`. After assessment, refresh `latest_health`/`latest_assessment` via `upsert_frontmatter`.

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/prompts/plant_photo_assessment.md telegram-bot/claude_backend.py plant_ui/server.py telegram-bot/tools.py telegram-bot/test_claude_backend.py
git commit -m "feat(plants): inject profile context into photo assessments + refresh health frontmatter"
```

---

## Phase 2 — Librarian atomic notes (Python, TDD)

### Task 7: Atomic-note writer for learnings

**Files:**
- Modify: `agents/librarian.py` (`_apply_learnings`)
- Create: `tests/test_librarian_notes.py`

**Interfaces:**
- Produces: `_write_learning_note(agent: str, entry: str, confidence: float, slug: str, related: list[str]) -> Path` writing `docs/agent-learnings/<agent>/<date>-<slug>.md` with frontmatter (`type, agent, confidence, status: active, date, tags, related`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_librarian_notes.py
def test_write_learning_note(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    p = lib._write_learning_note("news-briefing", "Don't report truncation.",
                                 0.9, "truncation-not-a-defect", ["[[security-audit]]"])
    text = p.read_text()
    assert "status: active" in text and "agent: news-briefing" in text
    assert "confidence: 0.9" in text and "Don't report truncation." in text
    assert p.parent.name == "news-briefing"
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement** `_write_learning_note` (yaml frontmatter + body), and call it from `_apply_learnings` for `conf >= 0.8 and ft == "learnings"` instead of appending to the flat file. For `memory_update`, write to `docs/librarian-memory/<slug>.md` with `type: memory`.

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/librarian.py tests/test_librarian_notes.py
git commit -m "feat(librarian): write learnings as atomic status-tagged notes"
```

### Task 8: `_collect_data` recursive glob + `status: active` filter

**Files:**
- Modify: `agents/librarian.py` (`_collect_data`, extract `_collect_learnings`)
- Test: `tests/test_librarian_notes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_collect_loads_only_active(tmp_path, monkeypatch):
    import agents.librarian as lib
    monkeypatch.setattr(lib, "REPO_ROOT", tmp_path)
    d = tmp_path / "docs" / "agent-learnings" / "news-briefing"; d.mkdir(parents=True)
    (d / "a.md").write_text("---\nstatus: active\n---\nKEEP\n")
    (d / "b.md").write_text("---\nstatus: superseded\n---\nDROP\n")
    blob = "\n".join(lib._collect_learnings(tmp_path / "docs" / "agent-learnings").values())
    assert "KEEP" in blob and "DROP" not in blob
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement** — extract a module-level `_collect_learnings(dir) -> dict[str,str]` that globs `**/*.md`, parses each file's frontmatter, and includes the body only when `status == "active"` (missing status ⇒ active for back-compat). Call it from `_collect_data`.

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/librarian.py tests/test_librarian_notes.py
git commit -m "feat(librarian): collect only status:active learnings (token-lean audit input)"
```

### Task 9: Audit/watch prompts emit slug/tags/related/status; migrate flat learnings

**Files:**
- Modify: `agents/prompts/librarian_audit.md`, `agents/prompts/librarian_watch.md`
- Create: `scripts/migrate_librarian_learnings.py`

- [ ] **Step 1: Update prompts** — extend the findings JSON schema each prompt requests to include `slug`, `tags`, `related` (array of `[[name]]`), and `status` (default `active`); instruct the model to mark a learning `superseded` when a newer finding replaces it.

- [ ] **Step 2: Write migration script** — read each existing `docs/agent-learnings/<agent>.md`, split bullet lines into atomic notes via `_write_learning_note` (confidence `0.8`, slug from first words), then delete the flat file. Same for `docs/librarian-memory.md`.

- [ ] **Step 3: Run migration**

Run: `.venv/bin/python scripts/migrate_librarian_learnings.py`
Expected: atomic files created under `docs/agent-learnings/<agent>/`; flat files removed.

- [ ] **Step 4: Run full librarian test suite**

Run: `.venv/bin/pytest tests/test_librarian_notes.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/prompts/librarian_audit.md agents/prompts/librarian_watch.md scripts/migrate_librarian_learnings.py docs/agent-learnings/
git commit -m "feat(librarian): atomic-note prompts + migrate flat learnings"
```

---

## Phase 3 — Daily session notes + hooks

### Task 10: Daily-note writer

**Files:**
- Create: `scripts/daily_note.py`
- Create: `tests/test_daily_note.py`

**Interfaces:**
- Produces: `ensure_today(date_str=None) -> Path` (creates `docs/daily/YYYY-MM-DD.md` with frontmatter + `## Index` if absent; idempotent); `append_session(summary: str, topics: list[str], files: list[str], decisions: list[str], open_threads: list[str])` (appends a `## Session N` block, updates the index + frontmatter arrays).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daily_note.py
def test_ensure_today_idempotent(tmp_path, monkeypatch):
    import scripts.daily_note as dn
    monkeypatch.setattr(dn, "DAILY_DIR", tmp_path)
    p1 = dn.ensure_today("2026-06-19"); p2 = dn.ensure_today("2026-06-19")
    assert p1 == p2
    text = p1.read_text()
    assert text.startswith("---\n") and "## Index" in text and "type: daily" in text
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement** `ensure_today` + `append_session` per the daily-note template in the spec.

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_note.py tests/test_daily_note.py
git commit -m "feat(daily): per-session daily note writer with parse-friendly index"
```

### Task 11: Wire SessionStart + Stop hooks

**Files:**
- Modify: `.claude/settings.json` (project hooks)

- [ ] **Step 1: Add hooks** — `SessionStart` runs `.venv/bin/python -m scripts.daily_note ensure`; `Stop` runs `.venv/bin/python -m scripts.daily_note append` with a short auto-summary (full summary may come from the `session-log` skill). Add a `__main__` dispatch to `scripts/daily_note.py` for `ensure`/`append`.

- [ ] **Step 2: Verify** — start a fresh Claude Code session in the repo; confirm `docs/daily/<today>.md` is created. End it; confirm a `## Session` block is appended.

- [ ] **Step 3: Commit**

```bash
git add .claude/settings.json scripts/daily_note.py
git commit -m "feat(daily): hook-driven daily session notes"
```

---

## Phase 4 — Dashboards, MOCs, identity notes (static markdown)

### Task 12: Dashboards + Home MOC + folder indexes

**Files:**
- Create: `docs/_dashboards/Plant Health.md`, `docs/_dashboards/Librarian Intelligence.md`, `docs/_dashboards/Memory.md`, `docs/_dashboards/Home.md`
- Create: `docs/plants/_index.md`, `docs/agent-learnings/_index.md`

- [ ] **Step 1: Write the Dataview dashboards** — each is a markdown note with a ```dataview``` block. Plant Health: `TABLE location, last_watered, effective_frequency_days, needs_photo, latest_health FROM "plants" WHERE type = "plant"` plus a "needs attention" `WHERE needs_photo OR latest_health != "healthy"`. Librarian Intelligence: `TABLE agent, confidence, status, date FROM "agent-learnings" WHERE status = "active" SORT confidence DESC`. Memory: `TABLE metadata.type FROM "_memory"`.

- [ ] **Step 2: Write Home MOC** — `docs/_dashboards/Home.md` linking `[[CLAUDE]]` (project source of truth), `[[feedback_personality]]` (Eagna: blunt, dry, no sycophancy), the project memories, recent daily notes (`dataview FROM "daily" SORT date DESC LIMIT 5`), and the three dashboards.

- [ ] **Step 3: Verify** — these render only in Obsidian (Dataview); confirm no Python references them; files are valid markdown.

- [ ] **Step 4: Commit**

```bash
git add docs/_dashboards "docs/plants/_index.md" docs/agent-learnings/_index.md
git commit -m "feat(vault): Dataview dashboards + Eagna Home MOC + folder indexes"
```

---

## Phase 5 — Sync infra (CouchDB + livesync-bridge in the yopflix seedbox stack, Tailscale-only)

> The on-disk vault structure (Phases 1–4) is now settled; the bridge mirrors it. The two services are added to the **existing** stack at `~/git/yopflix/seedbox/docker-compose.yaml` (a private repo) — **not** a new `vault/` stack — following its `services/` (config) + `env/` (secrets) conventions, and come up with the rest of the seedbox via `run-seedbox.sh`. **All Phase 5 commits target the yopflix repo**, except the device-setup doc + CLAUDE.md (ai-agents). Exact CouchDB tuning + bridge config schema are fetched from upstream at execution time (spec caveat) — do not invent values.

### Task 13: Add CouchDB service to the seedbox stack, Tailscale-bound

**Files (in `~/git/yopflix`):**
- Modify: `seedbox/docker-compose.yaml` (add `couchdb` service + a `couchdb_data` named volume)
- Create: `seedbox/services/couchdb/local.ini`
- Modify: `seedbox/env/` (add CouchDB admin creds following the stack's existing env-file pattern)

- [ ] **Step 1: Inspect the stack's conventions** — read `seedbox/docker-compose.yaml` for how services declare `networks`, `restart`, `env_file`, and volumes; match them. Note whether Traefik fronts services (it does) — but CouchDB will bind the Tailscale IP directly, not route via Traefik, to keep the "Tailscale-only" guarantee explicit.

- [ ] **Step 2: Fetch upstream config** — read the Self-hosted LiveSync "Setup CouchDB" docs (vrtmrz/obsidian-livesync) for the required `seedbox/services/couchdb/local.ini` (CORS, `single_node`, `require_valid_user`, `max_http_request_size`). Cite the URL in a comment.

- [ ] **Step 3: Add the `couchdb` service** — image `couchdb:3`, mount `seedbox/services/couchdb/local.ini` → `/opt/couchdb/etc/local.d/`, named volume `couchdb_data` → `/opt/couchdb/data`, admin creds from the seedbox env file, restart policy matching siblings, and **port published to the Tailscale IP only**: `ports: ["100.96.86.73:5984:5984"]` (never `0.0.0.0`).

- [ ] **Step 4: Bring up + verify bind**

Run: `cd ~/git/yopflix && ./run-seedbox.sh up -d couchdb && ss -tlnp | grep 5984`
Expected: bound to `100.96.86.73:5984`, **not** `0.0.0.0:5984`. If `0.0.0.0` appears, fix the port mapping before proceeding.

- [ ] **Step 5: Apply LiveSync DB init** — run the LiveSync-documented one-time CouchDB setup (`curl` the `_cluster_setup` / config endpoints per docs).

- [ ] **Step 6: Commit (yopflix repo)**

```bash
cd ~/git/yopflix && git add seedbox/docker-compose.yaml seedbox/services/couchdb/local.ini seedbox/env/ \
  && git commit -m "feat(seedbox): CouchDB sync hub for Obsidian vault, Tailscale-bound"
```

### Task 14: Add livesync-bridge service with three vault mappings

**Files (in `~/git/yopflix`):**
- Modify: `seedbox/docker-compose.yaml` (add `livesync-bridge` service)
- Create: `seedbox/services/livesync-bridge/config.json` (paths only; secrets via env)

- [ ] **Step 1: Fetch upstream bridge config schema** — read vrtmrz/livesync-bridge README for `config.json` structure (peers: storage + couchDB). Cite URL.

- [ ] **Step 2: Add the service + bind-mount the ai-agents paths** — the bridge container mounts host paths read-write: `/home/cian/git/ai-agents/docs`, `/home/cian/.claude/projects/-home-cian-git-ai-agents/memory`, and `/home/cian/git/ai-agents/CLAUDE.md`. It reaches CouchDB over the stack's internal network (service name `couchdb:5984`), not the Tailscale port.

- [ ] **Step 3: Configure three storage mappings → one database** — `…/ai-agents/docs` → `/`; `…/memory` → `/_memory/`; repo-root filtered to `CLAUDE.md`/`.antigravity.md` → `/_project/`. Ignore patterns: `.obsidian/`, `.trash/`, `__pycache__/`, `*.pyc`. Same `passphrase`/E2EE settings the Obsidian clients will use.

- [ ] **Step 4: Bring up + round-trip verify**

Run: `cd ~/git/yopflix && ./run-seedbox.sh up -d livesync-bridge`; create `~/git/ai-agents/docs/daily/_synctest.md` on disk; confirm it appears in CouchDB (`curl` the DB), then (after Task 15) on a device; create a note on the device, confirm it lands on disk.

- [ ] **Step 5: Commit (yopflix repo)**

```bash
cd ~/git/yopflix && git add seedbox/docker-compose.yaml seedbox/services/livesync-bridge/config.json \
  && git commit -m "feat(seedbox): livesync-bridge mirrors ai-agents docs + memory + CLAUDE.md"
```

### Task 15: Device setup doc + round-trip + mark migration complete

**Files (in `~/git/ai-agents`):**
- Create: `docs/obsidian-vault-setup.md`
- Modify: `.gitignore` (`docs/.obsidian/`), `CLAUDE.md`

- [ ] **Step 1: Gitignore Obsidian config** — add `docs/.obsidian/` and `docs/.trash/` to the ai-agents `.gitignore` (the bridge already excludes them from sync, but a device's first connect can still drop them on disk).

- [ ] **Step 2: Write device setup doc** — `docs/obsidian-vault-setup.md`: install Obsidian on phone/PC, install Self-hosted LiveSync plugin, point at `https://100.96.86.73:5984` over Tailscale, DB name + passphrase, initial sync direction. Note the stack is part of the yopflix seedbox (started by `run-seedbox.sh`). Cite the LiveSync quick-setup URL.

- [ ] **Step 3: Full round-trip verification** — edit a plant profile on phone → appears on disk → run the plant intelligence step, confirm it reads the edit; trigger an agent write → appears on phone. Confirm `docs/.obsidian/` is gitignored and not committed.

- [ ] **Step 4: Update CLAUDE.md** — replace the "In-Progress" section with a permanent "Obsidian Vault" section: vault layout, Tailscale port `100.96.86.73:5984`, **services live in the yopflix seedbox stack** (`~/git/yopflix/seedbox/docker-compose.yaml`, started by `run-seedbox.sh`), and "frontmatter is a projection — don't hand-edit."

- [ ] **Step 5: Commit (ai-agents repo)**

```bash
git add docs/obsidian-vault-setup.md .gitignore CLAUDE.md
git commit -m "docs(obsidian): device setup + mark vault migration complete"
```

---

## Self-Review

- **Spec coverage:** sync infra → Tasks 13–15; vault layout/3 roots → Task 14; plant frontmatter/curation/assessment-context/research → Tasks 1–6; librarian atomic+status+collect → Tasks 7–9; daily notes+hooks → Tasks 10–11; dashboards/MOC/identity → Task 12; CLAUDE.md inclusion → Tasks 12 (link) + 15 (doc); security (Tailscale bind, gitignored creds) → Task 13; migration backfills → Tasks 4, 9. All spec sections mapped.
- **Type consistency:** `parse_frontmatter`/`upsert_frontmatter`/`rewrite_section`/`read_profile_context` consistent across Tasks 1–6; `_write_learning_note`/`_collect_learnings` consistent across Tasks 7–9; `ensure_today`/`append_session` consistent across Tasks 10–11.
- **Known verification points (not placeholders):** the SQLite plant-state accessor (Task 4) and the `synthesize`/`_run_claude`/`_intelligence_run` symbol names (Tasks 5–6) must be matched to the actual current code when implementing — flagged inline. CouchDB/bridge config values are deliberately fetched from upstream (spec caveat), not invented.
