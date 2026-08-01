# Phase 0 — Prereqs & Go/No-Go Spikes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear every Phase 0 go/no-go item from `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` so Phase 1 (images + CI) has a known-good base: a working Obsidian sync round-trip, a proven credential-isolation pattern for the `claude` CLI, real capacity numbers, and the container-build hygiene (`.dockerignore`, pinned deps, health/metrics endpoints) Phase 1's images depend on.

**Architecture:** No new services. This phase touches existing files (`wedding_ui/server.py`, repo root) plus one new host directory (`/srv/k3s-claude-home`) used only for the CLI spike — it is not wired into any running workload yet.

**Tech Stack:** Python 3.12, FastAPI, `prometheus-client`, pytest, Docker.

## Global Constraints

- Python version floor matches the existing image: `python:3.12-slim` (`wedding_ui/Dockerfile:3`).
- Dependency versions pin to what's already installed and proven working in `.venv`, not to floor ranges: `psutil==7.2.2`, `pydantic==2.13.4`, `python-dotenv==1.2.2`, `python-telegram-bot==22.8`, `PyYAML==6.0.3`, `requests==2.34.2`.
- TDD per CLAUDE.md — failing test before implementation, for every task that produces code.
- `agents/base.py`'s `DENIED_TOOLS` / `_tool_restriction_flags()` (Phase −1, already shipped) is the tool-surface control the Task 6 spike is validating a *second, independent* boundary on top of — it does not replace it.

---

### Task 1: Capacity table

Not code — a measurement task. Numbers already gathered this session; this task is recording them so Phase 1's resource-limit decisions cite real data instead of re-measuring later.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` (append a `## Capacity` section)

- [ ] **Step 1: Re-confirm the numbers are still current**

Run:
```bash
df -h /
free -h
du -sh data/ docs/
docker system df
nproc
```
Expected (measured 2026-08-01, re-run to confirm no drift before recording): `/` at 87% (1.4T free of 11T), 25Gi memory available, `data/` 229M, `docs/` 1.5M, Docker images 17.04GB across 21 images, 8 CPUs.

- [ ] **Step 2: Append the table to the design doc**

Add to `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`, after the `## Verification` section:

```markdown
## Capacity (measured 2026-08-01)

| Resource | Total | Used | Available | Note |
|---|---|---|---|---|
| Disk (`/`) | 11T | 9.0T (87%) | 1.4T | Plenty of headroom despite the high %; don't let the percentage alone drive sizing decisions. |
| Memory | 31Gi | 4.5Gi + 16Gi cache | 25Gi available | Matches the design doc's earlier "26 GB" figure. Size k8s resource limits generously — over-tight limits cause OOMKills, not safety. |
| CPU | 8 cores | — | — | |
| `data/` | 229M | — | — | hostPath mount size for agent pods. |
| `docs/` | 1.5M | — | — | hostPath mount size for the Obsidian-synced subdirectories. |
| Docker images | — | 17.04GB / 21 images | — | Existing footprint before any k3s/GHCR images are added. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 0 capacity table"
```

---

### Task 2: `.dockerignore` at repo root

**Files:**
- Create: `.dockerignore`

**Interfaces:**
- Consumes: nothing.
- Produces: build-context exclusion list every future `docker build -f <image>/Dockerfile .` in this repo relies on (Phase 1's `ai-agents-wedding-ui` and `ai-agents-runner` images both build from repo root per `wedding_ui/Dockerfile`'s existing comment: "Build context = the ai-agents repo root").

- [ ] **Step 1: Write the file**

```
# .dockerignore — repo-root build context exclusions.
# Personal data and secrets that must never reach a Docker build context,
# even though a Dockerfile's explicit COPY list wouldn't otherwise pick
# them up — this is a second, independent boundary.
.venv/
__pycache__/
*.pyc
.git/
.env
.env.*
data/
docs/daily/
docs/agent-learnings/
docs/plant-observations/
output/
*.db
*.db-journal
```

- [ ] **Step 2: Verify it's actually honoured**

Run:
```bash
docker build -f wedding_ui/Dockerfile -t wedding-fund-dockerignore-test . 2>&1 | grep -c "COPY wedding_ui"
docker run --rm --entrypoint find wedding-fund-dockerignore-test /app -maxdepth 1
```
Expected: the `find` output lists `/app/wedding_ui` and `/app/agents` (the Dockerfile's explicit `COPY` targets) — it must **not** list `/app/data`, `/app/docs`, or `/app/.git`, confirming the ignore rules apply to the build context docker-compose/CI would send, not just to what the Dockerfile happens to `COPY`.

- [ ] **Step 3: Clean up the test image and commit**

```bash
docker rmi wedding-fund-dockerignore-test
git add .dockerignore
git commit -m "build: add repo-root .dockerignore"
```

---

### Task 3: `agents/requirements.txt`

No requirements file currently exists anywhere covering `agents/`, `telegram-bot/`, or `mcp-servers/` — only `wedding_ui/requirements.txt` and `plant_ui/requirements.txt` exist. This file is what the Phase 1 `ai-agents-runner` image installs from.

**Files:**
- Create: `agents/requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: the pinned dependency list Phase 1's `ai-agents-runner` Dockerfile's `RUN pip install -r agents/requirements.txt` will reference.

- [ ] **Step 1: Write the file**

```
# Runtime dependencies for agents/, telegram-bot/, and mcp-servers/ — the
# packages the ai-agents-runner image installs. Pinned to versions already
# proven working in .venv, not floor ranges. pytest is deliberately excluded:
# it's a dev/CI-only dependency, not part of the runtime image.
psutil==7.2.2
pydantic==2.13.4
python-dotenv==1.2.2
python-telegram-bot==22.8
PyYAML==6.0.3
requests==2.34.2
```

- [ ] **Step 2: Verify it installs cleanly in isolation**

Run:
```bash
python3 -m venv /tmp/req-check-venv
/tmp/req-check-venv/bin/pip install --quiet -r agents/requirements.txt
echo $?
rm -rf /tmp/req-check-venv
```
Expected: `0`, no resolver errors.

- [ ] **Step 3: Commit**

```bash
git add agents/requirements.txt
git commit -m "build: add pinned agents/requirements.txt"
```

---

### Task 4: `wedding_ui` `/healthz` endpoint

**Files:**
- Modify: `wedding_ui/server.py`
- Test: `tests/test_wedding_ui_api.py`

**Interfaces:**
- Consumes: `get_store` (existing dependency, `wedding_ui/server.py:29`), `BudgetStore.get_config()` (existing, `wedding_ui/budget_model.py:202`).
- Produces: `GET /healthz` — 200 `{"status": "ok"}` when the DB is reachable, 503 when it isn't. This is what Phase 2's k3s liveness/readiness probes will target.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wedding_ui_api.py`:

```python
def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_reports_db_failure(client):
    class BrokenStore:
        def get_config(self):
            raise RuntimeError("db unreachable")

    app.dependency_overrides[get_store] = lambda: BrokenStore()
    resp = client.get("/healthz")
    assert resp.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_wedding_ui_api.py -k healthz -v`
Expected: FAIL — `404 Not Found` (no `/healthz` route exists yet).

- [ ] **Step 3: Implement the endpoint**

In `wedding_ui/server.py`, add near the other `@app.get` routes (after the `/api/budget` block, before `/`):

```python
@app.get("/healthz")
def healthz(store: BudgetStore = Depends(get_store)):
    try:
        store.get_config()
    except Exception:
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wedding_ui_api.py -k healthz -v`
Expected: PASS (2 tests). Also run the full file to confirm no regression: `.venv/bin/pytest tests/test_wedding_ui_api.py -v`.

- [ ] **Step 5: Commit**

```bash
git add wedding_ui/server.py tests/test_wedding_ui_api.py
git commit -m "feat(wedding-ui): add /healthz endpoint"
```

---

### Task 5: `wedding_ui` `/metrics` endpoint

**Files:**
- Modify: `wedding_ui/server.py`
- Modify: `wedding_ui/requirements.txt`
- Test: `tests/test_wedding_ui_api.py`

**Interfaces:**
- Consumes: `prometheus_client.Counter`, `prometheus_client.make_asgi_app` (new third-party dependency).
- Produces: `GET /metrics` — Prometheus text-format output including a `wedding_ui_requests_total` counter. This is what Phase 6's kube-prometheus-stack scrapes.

- [ ] **Step 1: Add the dependency**

In `wedding_ui/requirements.txt`, add:
```
prometheus-client>=0.19.0
```

Install it locally: `.venv/bin/pip install "prometheus-client>=0.19.0"`

- [ ] **Step 2: Write the failing test**

Add to `tests/test_wedding_ui_api.py`:

```python
def test_metrics_exposes_request_counter(client):
    client.get("/api/budget")  # generate at least one counted request
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "wedding_ui_requests_total" in resp.text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_wedding_ui_api.py -k metrics -v`
Expected: FAIL — `404 Not Found` (no `/metrics` route yet).

- [ ] **Step 4: Implement the endpoint**

In `wedding_ui/server.py`:

Add to the imports:
```python
from prometheus_client import Counter, make_asgi_app
```

Add after the `app = FastAPI(...)` line:
```python
REQUEST_COUNT = Counter(
    "wedding_ui_requests_total", "Total HTTP requests", ["method", "path", "status"]
)


@app.middleware("http")
async def _count_requests(request, call_next):
    response = await call_next(request)
    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    return response
```

Add near the existing `app.mount("/static", ...)` line (`wedding_ui/server.py:129`):
```python
app.mount("/metrics", make_asgi_app())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wedding_ui_api.py -v`
Expected: PASS, full file green (all prior tests plus the two new `/healthz` tests plus this one).

- [ ] **Step 6: Commit**

```bash
git add wedding_ui/server.py wedding_ui/requirements.txt tests/test_wedding_ui_api.py
git commit -m "feat(wedding-ui): add /metrics endpoint for Prometheus scraping"
```

---

### Task 6: `claude` CLI credential-isolation spike (`/srv/k3s-claude-home`)

Not code — a go/no-go spike per the design doc's decision 3 and Phase 0 description: "the spike must test *this* configuration; if it only tests the permissive one it will pass and ship the vulnerable design." This validates that a `claude` CLI process confined to a separate home directory genuinely cannot see `~/.claude` (hooks, plugins, the leaked-secret transcripts) — the boundary Phase 3+ pod specs depend on for never mounting the real `~/.claude`.

**Files:** none in this repo — this provisions `/srv/k3s-claude-home` on the host, outside the repo.

- [ ] **Step 1: Provision the directory**

```bash
sudo mkdir -p /srv/k3s-claude-home
sudo chown 1001:1001 /srv/k3s-claude-home
ls -la /srv/k3s-claude-home
```
Expected: owned by `cian:cian` (uid/gid 1001), empty.

- [ ] **Step 2: Authenticate a separate Anthropic session there — requires you interactively**

```bash
HOME=/srv/k3s-claude-home claude
```
This launches an interactive login flow (browser-based OAuth) separate from your normal `~/.claude` session. Complete it, then exit. This step cannot be scripted — it needs your own Anthropic account consent.

Expected after completion: `/srv/k3s-claude-home/.credentials.json` exists.

- [ ] **Step 3: Strip it down to the minimal footprint the design calls for**

```bash
cat /srv/k3s-claude-home/settings.json 2>/dev/null
```
If a `settings.json` was created by the login flow, replace its contents with:
```json
{"disableAllHooks": true}
```
Then confirm no `projects/` transcript directory or plugin cache leaked in:
```bash
ls /srv/k3s-claude-home
```
Expected: only `.credentials.json` and `settings.json` — no `projects/`, no `plugins/`. If either exists, delete it (`rm -rf /srv/k3s-claude-home/projects /srv/k3s-claude-home/plugins`) — those are exactly the exfiltration surface (leaked secret, private transcripts) decision 3 exists to avoid.

- [ ] **Step 4: Prove the isolation — this is the actual go/no-go test**

```bash
HOME=/srv/k3s-claude-home claude -p "List every file you can currently see under any path containing the string 'claude/projects', and separately confirm whether a file literally named .claude.json exists in your home directory." --dangerously-skip-permissions --strict-mcp-config --disallowedTools Bash Write Edit
```
Expected: the response confirms it cannot see `~/.claude/projects` content (no project transcripts, no leaked secret) and that `/srv/k3s-claude-home/.claude.json` doesn't exist (trust/MCP config is scoped to the real `~/.claude`, so a process using this HOME starts with zero pre-established trust — expected and fine, since Phase 3+ pods pass `--strict-mcp-config` explicitly anyway per the existing `_tool_restriction_flags()` pattern).

If it can see anything under the real `~/.claude/projects` — **stop, this is a no-go** for hostPath credential isolation as designed, and Phase 3+ needs rethinking before proceeding (the design doc's mitigation for exposure item 3 would be unproven).

- [ ] **Step 5: Record the result**

Append to `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`, in the Phase 0 status note: pass/fail, and the exact command output confirming isolation. Commit.

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 0 claude CLI credential-isolation spike result"
```

---

### Task 7: Prove the Obsidian round-trip (CouchDB → disk direction)

Not code — an operational checkpoint. Task 1 of Phase 0 (already done) proved disk → CouchDB sync works (the 79-file backlog drained). This proves the *other* direction, which needs a write that genuinely originates outside this host — something only your own Obsidian device can do; it cannot be faked by writing to CouchDB directly with `curl`, since the LiveSync protocol chunks and encrypts content rather than storing plain document bodies.

**Files:** none — verification only, against `docs/` on this host.

- [ ] **Step 1: On your phone or laptop's Obsidian app, with the vault's LiveSync plugin pointed at this CouchDB, create or edit a note**

E.g. create a new note titled `phase0-roundtrip-test.md` with any content, in a folder that maps into a synced group (`docs/`, per the three-peer config: `docs-docs` group covers the main vault root).

- [ ] **Step 2: Watch it land on disk**

```bash
docker logs -f livesync-bridge --tail 0
```
Expected within ~30s (per the documented sync latency in CLAUDE.md): a log line showing the new file being received and written, e.g. `[couchdb-docs] --> phase0-roundtrip-test.md change detected` followed by a `WATCH: PROCESS DONE` or storage-side save line.

```bash
ls -la /home/cian/git/ai-agents/docs/phase0-roundtrip-test.md
cat /home/cian/git/ai-agents/docs/phase0-roundtrip-test.md
```
Expected: file exists on disk with the content you wrote on-device.

- [ ] **Step 3: Clean up the test note**

Delete `phase0-roundtrip-test.md` from the Obsidian device (not just on disk — deleting on-disk only doesn't propagate; per CLAUDE.md, "on-disk deletions only reconcile on bridge restart", so delete from the client side to avoid a stray file surviving both directions).

- [ ] **Step 4: Record the result and close out Phase 0**

Append to `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`'s Phase 0 status note: round-trip confirmed both directions, dated. This is the last open item in Phase 0 — once recorded, Phase 0 is complete and Phase 1 (images + CI) can get its own plan.

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: confirm Obsidian round-trip both directions, close Phase 0"
```

---

## Self-Review

**Spec coverage:** all four remaining Phase 0 items from the design doc are covered — capacity table (Task 1), `.dockerignore`/`agents/requirements.txt`/health+metrics (Tasks 2–5), `claude` CLI credential-isolation spike (Task 6), Obsidian round-trip (Task 7). Task order front-loads everything independently completable (1–5) before the two tasks that need your direct interaction (6–7).

**Placeholder scan:** no TBDs; every step has an exact command or code block and a stated expected result, including the two operational tasks (6, 7) where "verify it works" is replaced with specific log lines / file checks / CLI output to look for.

**Type/interface consistency:** `get_store` and `BudgetStore.get_config()` (Task 4) match their existing signatures in `wedding_ui/server.py` and `wedding_ui/budget_model.py`; `REQUEST_COUNT` (Task 5) is defined and used within the same task, no cross-task naming drift.
