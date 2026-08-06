# Phase 6 — plant-ui Full Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan pushes to `origin/main`, touches live production Traefik routing (outside this repo, in `~/git/yopflix/seedbox`), and does a real cutover window on the highest-daily-use workload in this whole migration — a PWA installed to a home screen.** Tasks 6, 7, 8, 9, 10 are marked STOP — do not execute them, or resume past them, without the human partner's explicit go-ahead in this session, given separately for each STOP (not one blanket yes covering all of them). No subagent may be dispatched for any STOP task.

**Goal:** Move `plant-ui` from its current systemd service onto k3s permanently — dedicated image, hostPath onto the real shared `data/`/`docs/` (not a PVC copy, since host-side cron agents still write the same SQLite db), Traefik cutover to a new Tailscale-only hostname — proven with real writes (chat continuity across a pod restart, a photo assessment, a watering record) before the live switch, and a rollback path proven to actually work.

**Architecture:** New namespace `plant-ui`, PSA `privileged` (required by the hostPath mounts — no way around it, unlike wedding-ui which is PVC-backed and fully `restricted`). New dedicated image `ai-agents-plant-ui`, not an extension of `ai-agents-runner`. Storage is entirely hostPath onto the real `data/`, three specific `docs/` subdirectories, and the already-provisioned `/srv/k3s-claude-home` identity — no PVC anywhere in this phase, because `data/agents.db` is still live shared state with host-side cron agents that haven't cut over. Networking is a `NodePort` Service (Traefik, still running in Docker on the host, can only reach a node-bound port — the same mechanism wedding-ui's cutover already proved) fronted by a new Traefik route at `plants.yopflix.world`, restricted to Tailscale-only via a new `ipAllowList` middleware rather than reusing the exact `<TAILSCALE_IP>:8765` address (accepting a PWA-reinstall cost across every device, per explicit preference during design).

**Tech Stack:** Kubernetes Deployment/Service/Namespace/NetworkPolicy/Secret, k3s NodePort (bound cluster-wide to `<TAILSCALE_IP>` since Phase 2), GitHub Actions (`ci.yml`'s existing build matrix, extended), GHCR private image + `imagePullSecret`, Traefik `file` provider (separate repo `~/git/yopflix/seedbox`) + a new `ipAllowList` middleware, `kind` for local manifest smoke-testing before the real cluster.

## Global Constraints

- **PSA:** `plant-ui` namespace must be `privileged`, not `baseline` or `restricted` — every hostPath mount in Task 5 would otherwise be rejected at admission. Same reasoning as `ai-agents-cron` (Phase 4).
- **No PVC, anywhere, in this phase.** `data/agents.db` is read/written today by host-side cron agents (`PlantAgent`, 5 of 6 still-crontab agents) that have not cut over. A PVC would fork that state on day one. Every data mount in Task 5 is `hostPath`, `type:` set explicitly (an unset `type` lets kubelet create a missing path as root).
- **Image identity:** `ghcr.io/hughseyxo/ai-agents-plant-ui`, private (same GHCR visibility policy as `ai-agents-wedding-ui`/`ai-agents-runner`), referenced by `@sha256:` digest, never `:latest`.
- **`telegram-bot/` dependency is three files, not one:** `claude_backend.py` (vision), `tool_specs.py`, `tools.py` — `mcp-servers/concierge_server.py` imports `tool_specs.py`, which imports `tools.py`. Confirmed via `grep` during design: `tools.py`'s own imports are just `agents.*`, `psutil`, `yaml`, stdlib — no `python-telegram-bot`, no Gmail/Calendar/Drive client code. Do not copy the rest of `telegram-bot/`.
- **Repo path inside the image is `/home/cian/git/ai-agents`**, not `/app` (migration decision 8 — MCP config resolution is keyed to this exact path).
- **User:** `runAsUser`/`runAsGroup`: `1001` (migration decision 7 — matches `ai-agents-runner`'s existing convention, distinct from `wedding_ui`'s `1000`).
- **NodePort:** `30801` — `30800` is wedding-ui's, confirmed via `kubectl get svc -A` showing only that one NodePort in use before this phase.
- **Traefik config lives in a separate repo**, `~/git/yopflix/seedbox` (not `ai-agents`). The `ipAllowList` middleware goes in the already-tracked `~/git/yopflix/seedbox/traefik/custom/middlewares.yaml` (alongside the existing `common-auth`/`redirect-to-https` middlewares) since the CIDR it uses (`100.64.0.0/10`, Tailscale's global CGNAT range) isn't host-specific and is safe to track. The router+service definition goes in a **new, gitignored** `~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml` (matches the existing `custom-*.yaml` gitignore pattern — keeps the literal `<TAILSCALE_IP>` backend URL out of tracked files).
- **`plants.yopflix.world` resolves via Tailscale MagicDNS or a hosts-file entry, never public DNS** — the whole point of the `ipAllowList` middleware is that this route must not be publicly reachable even though Traefik itself listens on the public IP (confirmed: `services/traefik.yaml` publishes `80:80`/`443:443` with no host-IP restriction).
- **No basic auth on this route.** Plant-ui has no auth layer today (Tailscale reachability is the access control); the `ipAllowList` middleware replicates that exact trust boundary rather than adding new friction.
- **Existing CI (`​.github/workflows/ci.yml`) already installs `plant_ui/requirements.txt` and runs `pytest tests/`** — Task 1/2's new tests run in that existing job with no CI changes needed for the test step itself, only the `build` job's matrix (Task 4) is new.
- Every STOP task requires the human partner's own explicit go-ahead, given separately per task.

---

### Task 1: Fix `chat_backend.py`'s tool-surface confinement

**Files:**
- Modify: `plant_ui/chat_backend.py`
- Test: `tests/test_chat_backend.py`

**Interfaces:**
- Consumes: `plant_ui/chat_backend.py`'s existing `_ALLOWED_TOOLS` list (9 entries: `Read`, `Glob`, and 7 `mcp__concierge__*` tools — see the file for the exact list).
- Produces: `_DISALLOWED_TOOLS` (new module-level list, exact `mcp__concierge__*` names for every `func_map()` entry in `telegram-bot/tools.py` NOT in `_ALLOWED_TOOLS`), appended to the `claude` CLI command via `--disallowedTools` in `chat()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chat_backend.py`:

```python
def test_chat_disallows_tools_outside_whitelist(monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _fake_run(json.dumps({"result": "hi", "session_id": "s1"}))
    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    cb.chat("hello", scope="garden", plant_name=None, session_id=None)
    cmd = seen["cmd"]
    assert "--disallowedTools" in cmd
    idx = cmd.index("--disallowedTools")
    disallowed = cmd[idx + 1:]
    # Every dangerous tool from the full concierge surface must be blocked.
    for dangerous in (
        "mcp__concierge__run_travel_agent",
        "mcp__concierge__save_recipe",
        "mcp__concierge__save_youtube_playlist",
        "mcp__concierge__remove_plant",
        "mcp__concierge__water_plants",
        "mcp__concierge__get_system_health",
        "mcp__concierge__get_agent_logs",
        "mcp__concierge__add_plant",
        "mcp__concierge__update_plant",
        "mcp__concierge__set_plant_frequency",
        "mcp__concierge__water_plant",
        "mcp__concierge__get_agent_status",
        "mcp__concierge__get_yopflix_status",
        "mcp__concierge__get_cron_schedule",
        "mcp__concierge__research_plant_watering",
        "mcp__concierge__research_plant_sunlight",
        "mcp__concierge__research_plant_water_sensitivity",
        "mcp__concierge__research_plant_traits",
        "mcp__concierge__get_travel_report",
        "mcp__concierge__suggest_free_time_tasks",
    ):
        assert dangerous in disallowed, f"{dangerous} must be in --disallowedTools"
    # None of the intended tools should be blocked.
    for allowed in cb._ALLOWED_TOOLS:
        if allowed.startswith("mcp__"):
            assert allowed not in disallowed, f"{allowed} should stay allowed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_backend.py::test_chat_disallows_tools_outside_whitelist -v`
Expected: FAIL — `assert "--disallowedTools" in cmd` (the flag doesn't exist yet).

- [ ] **Step 3: Add `_DISALLOWED_TOOLS` and wire it into the command**

In `plant_ui/chat_backend.py`, after the existing `_ALLOWED_TOOLS` list, add:

```python
# Every mcp__concierge__* tool NOT in _ALLOWED_TOOLS above. --allowedTools
# alone does not restrict the CLI's actual tool surface (verified against
# the real CLI elsewhere in this project — see agents/base.py's
# DENIED_TOOLS for the same finding); --disallowedTools does. Without this,
# garden chat could reach the full concierge toolset (travel agent, recipe/
# playlist saving, plant removal, system health) via an adversarial prompt.
_DISALLOWED_TOOLS = [
    "mcp__concierge__get_agent_status",
    "mcp__concierge__get_yopflix_status",
    "mcp__concierge__get_system_health",
    "mcp__concierge__get_cron_schedule",
    "mcp__concierge__get_agent_logs",
    "mcp__concierge__run_travel_agent",
    "mcp__concierge__research_plant_watering",
    "mcp__concierge__research_plant_sunlight",
    "mcp__concierge__research_plant_water_sensitivity",
    "mcp__concierge__research_plant_traits",
    "mcp__concierge__add_plant",
    "mcp__concierge__update_plant",
    "mcp__concierge__water_plant",
    "mcp__concierge__water_plants",
    "mcp__concierge__remove_plant",
    "mcp__concierge__save_recipe",
    "mcp__concierge__save_youtube_playlist",
    "mcp__concierge__get_travel_report",
    "mcp__concierge__set_plant_frequency",
    "mcp__concierge__suggest_free_time_tasks",
]
```

Then in `chat()`, change:

```python
        "--allowedTools", *_ALLOWED_TOOLS,
        "--disallowedTools", "Write", "Edit", "Bash",
```

to:

```python
        "--allowedTools", *_ALLOWED_TOOLS,
        "--disallowedTools", "Write", "Edit", "Bash", *_DISALLOWED_TOOLS,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_chat_backend.py -v`
Expected: all tests PASS, including the new one.

- [ ] **Step 5: Commit**

```bash
git add plant_ui/chat_backend.py tests/test_chat_backend.py
git commit -m "fix(plant-ui): enforce chat tool surface via --disallowedTools

--allowedTools alone doesn't restrict the CLI (same finding already
documented for agents/base.py) — the full concierge toolset was reachable
from garden chat despite the code's own comment claiming otherwise."
```

---

### Task 2: Add `/healthz` to `plant_ui/server.py`

**Files:**
- Modify: `plant_ui/server.py`
- Test: `tests/test_plant_ui_api.py`

**Interfaces:**
- Consumes: `get_db()` (existing generator dependency, `plant_ui/server.py:123`), `AgentDB.get_state(agent: str, key: str, default=None)` (`agents/db.py:158`).
- Produces: `GET /healthz` → `200 {"status": "ok"}` on a working DB connection, `503` on failure. Consumed by Task 5's liveness/readiness probes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_plant_ui_api.py` (uses the existing `client` fixture already in that file):

```python
def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthz_reports_db_failure(client, monkeypatch):
    from plant_ui import server as srv
    def broken_get_state(self, agent, key, default=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(srv.AgentDB, "get_state", broken_get_state)
    resp = client.get("/healthz")
    assert resp.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plant_ui_api.py::test_healthz_ok -v`
Expected: FAIL — `404` (route doesn't exist).

- [ ] **Step 3: Implement `/healthz`**

In `plant_ui/server.py`, add near the other route handlers (e.g. just before the `# Serve Frontend SPA` comment at line 586):

```python
@app.get("/healthz")
def healthz(db: AgentDB = Depends(get_db)):
    try:
        db.get_state("plant-ui", "healthz-check")
    except Exception:
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_plant_ui_api.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add plant_ui/server.py tests/test_plant_ui_api.py
git commit -m "feat(plant-ui): add /healthz endpoint for k8s probes"
```

---

### Task 3: `plant_ui/Dockerfile` + fix `Dockerfile.runner`'s stale comment

**Files:**
- Create: `plant_ui/Dockerfile`
- Modify: `Dockerfile.runner` (header comment only)

**Interfaces:**
- Produces: image built as `ghcr.io/hughseyxo/ai-agents-plant-ui`, entrypoint `uvicorn plant_ui.server:app --host 0.0.0.0 --port 8765`, non-root user `cian` (uid/gid 1001), repo baked in at `/home/cian/git/ai-agents`. Consumed by Task 4 (CI build), Task 5 (Deployment's `image:` field).

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# ai-agents-plant-ui — FloraPulse PWA (chat + Opus vision diagnostics).
# Runs the claude CLI directly (chat_backend.py, claude_backend.py), same
# runtime shape as Dockerfile.runner but a separate image: different
# entrypoint (a long-running web service, not a one-shot agent run) and a
# narrower dependency set (no python-telegram-bot).
# Build context = the ai-agents repo root:
#   docker build -f plant_ui/Dockerfile -t ai-agents-plant-ui .
FROM node:20-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.92

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip curl && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 cian && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash cian

WORKDIR /home/cian/git/ai-agents
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY agents/requirements.txt /home/cian/git/ai-agents/agents/requirements.txt
COPY plant_ui/requirements.txt /home/cian/git/ai-agents/plant_ui/requirements.txt
RUN pip install --no-cache-dir --break-system-packages \
      -r agents/requirements.txt -r plant_ui/requirements.txt

COPY agents/ /home/cian/git/ai-agents/agents/
COPY mcp-servers/ /home/cian/git/ai-agents/mcp-servers/
COPY plant_ui/ /home/cian/git/ai-agents/plant_ui/
# Only the three telegram-bot/ files plant-ui's dependency chain actually
# needs (see this plan's Global Constraints) — not the rest of telegram-bot/.
COPY telegram-bot/claude_backend.py telegram-bot/tool_specs.py telegram-bot/tools.py \
     /home/cian/git/ai-agents/telegram-bot/

RUN mkdir -p /home/cian/git/ai-agents/data /home/cian/git/ai-agents/output && \
    chown -R cian:cian /home/cian/git/ai-agents /home/cian

USER cian
EXPOSE 8765
CMD ["python3", "-m", "uvicorn", "plant_ui.server:app", "--host", "0.0.0.0", "--port", "8765"]
```

- [ ] **Step 2: Fix `Dockerfile.runner`'s stale comment**

Find the header comment line:
```
# ai-agents-runner — runs the cron agents (agents/) plus the MCP servers
# they call (mcp-servers/). Does NOT include telegram-bot/ or plant_ui/ —
# those stay host-side (see the k3s migration design doc's Migration modes
# table). Based on node:20-slim ...
```
Change to:
```
# ai-agents-runner — runs the cron agents (agents/) plus the MCP servers
# they call (mcp-servers/). Does NOT include telegram-bot/ or plant_ui/ —
# plant-ui gets its own image (plant_ui/Dockerfile, Phase 6); the rest of
# telegram-bot/ (the Telegram concierge bot itself) still stays host-side.
# Based on node:20-slim ...
```

- [ ] **Step 3: Build locally to verify it compiles**

```bash
cd /home/cian/git/ai-agents
docker build -f plant_ui/Dockerfile -t ai-agents-plant-ui:local .
```
Expected: `Successfully tagged ai-agents-plant-ui:local` (or buildkit's equivalent final `naming to docker.io/library/ai-agents-plant-ui:local` line).

- [ ] **Step 4: Smoke-test the built image starts and serves `/healthz`**

```bash
docker run --rm -d --name plant-ui-smoke -p 18765:8765 ai-agents-plant-ui:local
sleep 3
curl -sf http://localhost:18765/healthz
docker logs plant-ui-smoke
docker stop plant-ui-smoke
```
Expected: `curl` prints `{"status":"ok"}`, no Python tracebacks in `docker logs`.

- [ ] **Step 5: Commit**

```bash
git add plant_ui/Dockerfile Dockerfile.runner
git commit -m "feat(plant-ui): add dedicated Dockerfile for k8s image"
```

---

### Task 4: Extend CI's build matrix for `ai-agents-plant-ui`

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `plant_ui/Dockerfile` (Task 3).
- Produces: on a `main` push, `ghcr.io/hughseyxo/ai-agents-plant-ui` built, pushed by digest, provenance-attested — same as the existing `wedding-ui`/`runner` matrix entries. Consumed by Task 6 (real digest lookup).

- [ ] **Step 1: Add the matrix entry**

In `.github/workflows/ci.yml`'s `build` job, change:

```yaml
    strategy:
      matrix:
        image: [wedding-ui, runner]
        include:
          - image: wedding-ui
            dockerfile: wedding_ui/Dockerfile
          - image: runner
            dockerfile: Dockerfile.runner
```

to:

```yaml
    strategy:
      matrix:
        image: [wedding-ui, runner, plant-ui]
        include:
          - image: wedding-ui
            dockerfile: wedding_ui/Dockerfile
          - image: runner
            dockerfile: Dockerfile.runner
          - image: plant-ui
            dockerfile: plant_ui/Dockerfile
```

- [ ] **Step 2: Validate the workflow YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "valid YAML"
```
Expected: `valid YAML`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ai-agents-plant-ui to the build/push matrix"
```

---

### Task 5: k8s manifests for `plant-ui`

**Files:**
- Create: `k8s/base/plant-ui/namespace.yaml`
- Create: `k8s/base/plant-ui/networkpolicy.yaml`
- Create: `k8s/base/plant-ui/deployment.yaml`
- Create: `k8s/base/plant-ui/service.yaml`
- Create: `k8s/base/plant-ui/kustomization.yaml`

**Interfaces:**
- Consumes: image name `ghcr.io/hughseyxo/ai-agents-plant-ui` (placeholder digest until Task 6), Secret name `ghcr-pull-secret` (created in Task 6), hostPath sources from Global Constraints.
- Produces: `Service plant-ui` on `NodePort 30801` (Global Constraints), `Deployment plant-ui` with `/healthz` probes (Task 2). Consumed by Task 6 (real Secret + digest), Task 7 (dry-run deploy), Task 8 (Traefik backend URL).

- [ ] **Step 1: `namespace.yaml`**

```yaml
# PSA privileged is required here, not a default left too loose: every
# hostPath mount below (data/, three docs/ subdirs, /srv/k3s-claude-home)
# is forbidden outright under both `baseline` and `restricted`, only
# `privileged` allows them. Same reasoning as the ai-agents-cron namespace
# (Phase 4). Scoped narrowly to this one workload.
apiVersion: v1
kind: Namespace
metadata:
  name: plant-ui
  labels:
    pod-security.kubernetes.io/enforce: privileged
    pod-security.kubernetes.io/warn: privileged
```

- [ ] **Step 2: `networkpolicy.yaml`**

```yaml
# Default-deny egress for the plant-ui namespace, same shape as
# k8s/base/networkpolicy.yaml's ai-agents policy — DNS + outbound HTTPS
# allow-listed (the claude CLI needs 443 to reach the Anthropic API),
# everything else unreachable even if this pod is compromised.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: plant-ui
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - ports:
        - protocol: TCP
          port: 443
```

- [ ] **Step 3: `deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: plant-ui
  namespace: plant-ui
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: plant-ui
  template:
    metadata:
      labels:
        app: plant-ui
    spec:
      imagePullSecrets:
        - name: ghcr-pull-secret
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        runAsGroup: 1001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: plant-ui
          image: ghcr.io/hughseyxo/ai-agents-plant-ui@sha256:PLACEHOLDER
          ports:
            - containerPort: 8765
          env:
            - name: HOME
              value: /srv/k3s-claude-home
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8765
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8765
            initialDelaySeconds: 2
            periodSeconds: 5
          volumeMounts:
            - name: agents-data
              mountPath: /home/cian/git/ai-agents/data
            - name: docs-readonly
              mountPath: /home/cian/git/ai-agents/docs
              readOnly: true
            - name: docs-plants
              mountPath: /home/cian/git/ai-agents/docs/plants
            - name: docs-plant-observations
              mountPath: /home/cian/git/ai-agents/docs/plant-observations
            - name: docs-garden-knowledge
              mountPath: /home/cian/git/ai-agents/docs/garden-knowledge
            - name: claude-home
              mountPath: /srv/k3s-claude-home
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 1000m
              memory: 1Gi
      volumes:
        - name: agents-data
          hostPath:
            path: /home/cian/git/ai-agents/data
            type: Directory
        - name: docs-readonly
          hostPath:
            path: /home/cian/git/ai-agents/docs
            type: Directory
        - name: docs-plants
          hostPath:
            path: /home/cian/git/ai-agents/docs/plants
            type: Directory
        - name: docs-plant-observations
          hostPath:
            path: /home/cian/git/ai-agents/docs/plant-observations
            type: Directory
        - name: docs-garden-knowledge
          hostPath:
            path: /home/cian/git/ai-agents/docs/garden-knowledge
            type: Directory
        - name: claude-home
          hostPath:
            path: /srv/k3s-claude-home
            type: Directory
```

Note: `docs-plants`/`docs-plant-observations`/`docs-garden-knowledge` mount at nested paths *under* the read-only `docs-readonly` mount — a more specific `mountPath` overlays the broader one (standard Kubernetes behavior), giving write access to exactly those three subdirs while the rest of `docs/` stays read-only. This exact pattern has no precedent elsewhere in this repo (confirmed via the design doc's self-review — `k8s/base/cronjobs/agent-health.yaml` only mounts `data/`), which is exactly what Step 5 (kind validation) below exists to catch if it's wrong.

- [ ] **Step 4: `service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: plant-ui
  namespace: plant-ui
spec:
  type: NodePort
  selector:
    app: plant-ui
  ports:
    - port: 8765
      targetPort: 8765
      nodePort: 30801
```

- [ ] **Step 5: `kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - deployment.yaml
  - service.yaml
```

- [ ] **Step 6: Validate with kubeconform**

```bash
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:v0.8.0 \
  -ignore-missing-schemas -summary /k8s/base
```
Expected: summary shows the 4 new resources under `plant-ui`, 0 errors.

- [ ] **Step 7: Validate the nested-mount pattern against a local `kind` cluster**

**Two `kind`-specific gotchas found while actually running this** (not obvious from the manifest alone): (1) `kind` nodes are themselves Docker containers, so hostPath sources must be created *inside the node container* (`docker exec <node> mkdir ...`), not on the real host — `/tmp/...` on the real host isn't visible there. (2) A naive `sed` that replaces the literal path string everywhere rewrites `mountPath:` (the container-side destination, must stay `/home/cian/git/ai-agents/...`) as well as `hostPath.path:` (the source, the only thing that should change) — anchor the substitution to lines starting with `path:` specifically, or it silently produces a pod that mounts everything at the wrong location inside the container.

```bash
kind create cluster --name phase6-smoke
kubectl --context kind-phase6-smoke apply -f k8s/base/plant-ui/namespace.yaml
kubectl --context kind-phase6-smoke create secret docker-registry ghcr-pull-secret \
  -n plant-ui --docker-server=ghcr.io --docker-username=x --docker-password=x

# Directories must exist inside the kind node container, not the real host.
docker exec phase6-smoke-control-plane mkdir -p \
  /tmp/phase6-smoke/{data,docs/plants,docs/plant-observations,docs/garden-knowledge,claude-home}
docker exec phase6-smoke-control-plane sh -c 'echo "{\"test\":\"ok\"}" > /tmp/phase6-smoke/docs/plants/probe.md'
# uid 1001 (the container's runAsUser) needs write access — root-owned by default.
docker exec phase6-smoke-control-plane chown -R 1001:1001 \
  /tmp/phase6-smoke/data /tmp/phase6-smoke/claude-home /tmp/phase6-smoke/docs

# Swap hostPath *sources* only — anchored to "path:" at line start so
# "mountPath:" lines (container-side, must stay /home/cian/git/ai-agents/...)
# are never touched.
sed -E '/^[[:space:]]+path: \/home\/cian\/git\/ai-agents\/data$/s#/home/cian/git/ai-agents/data#/tmp/phase6-smoke/data#;
     /^[[:space:]]+path: \/home\/cian\/git\/ai-agents\/docs/s#/home/cian/git/ai-agents/docs#/tmp/phase6-smoke/docs#;
     /^[[:space:]]+path: \/srv\/k3s-claude-home$/s#/srv/k3s-claude-home#/tmp/phase6-smoke/claude-home#' \
  k8s/base/plant-ui/deployment.yaml > /tmp/phase6-smoke/deployment.yaml
sed -i 's#image:.*#image: ai-agents-plant-ui:local#' /tmp/phase6-smoke/deployment.yaml

kind load docker-image ai-agents-plant-ui:local --name phase6-smoke
kubectl --context kind-phase6-smoke apply -f /tmp/phase6-smoke/deployment.yaml
kubectl --context kind-phase6-smoke -n plant-ui wait --for=condition=ready pod -l app=plant-ui --timeout=60s

kubectl --context kind-phase6-smoke -n plant-ui exec deploy/plant-ui -- cat /home/cian/git/ai-agents/docs/plants/probe.md
kubectl --context kind-phase6-smoke -n plant-ui exec deploy/plant-ui -- sh -c "echo write-test > /home/cian/git/ai-agents/docs/plants/write-probe.md"
docker exec phase6-smoke-control-plane cat /tmp/phase6-smoke/docs/plants/write-probe.md
kubectl --context kind-phase6-smoke -n plant-ui exec deploy/plant-ui -- sh -c "echo blocked > /home/cian/git/ai-agents/docs/should-fail.md" 2>&1 || echo "correctly read-only"

kind delete cluster --name phase6-smoke
rm -rf /tmp/phase6-smoke
kubectl config use-context default
```
Expected: `probe.md` content readable from inside the pod (proves the read-only `docs/` mount works); `write-probe.md` appears back on the kind node at `/tmp/phase6-smoke/docs/plants/` (proves the nested read-write mount overlays correctly); the write to `docs/should-fail.md` (outside the three writable subdirs) fails with a read-only-filesystem error, printing `correctly read-only`. **Actually run 2026-08-05 — all three confirmed**, plus `kubectl config current-context` confirmed back on `default` (the real cluster) afterward, not left stuck on the deleted kind context (a real bug hit earlier in this migration, on Phase 5).

- [ ] **Step 8: Commit**

```bash
git add k8s/base/plant-ui/
git commit -m "feat(k8s): add plant-ui manifests (namespace, netpol, deployment, service)"
```

---

### Task 6: STOP — push, real image digest, real Secret

**Files:** `k8s/base/plant-ui/deployment.yaml` (digest update only)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed past this point without the human partner's explicit go-ahead in this session.

- [ ] **Step 2: Push to `origin/main`**

```bash
git push origin main
```

- [ ] **Step 3: Wait for the Actions run and get the real digest**

```bash
gh run list --branch main --limit 1
gh run watch $(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId')
```
Expected: run concludes `success`. Then:
```bash
gh api /users/hughseyxo/packages/container/ai-agents-plant-ui/versions --jq '.[0].name'
```
Expected: a `sha256:...` digest string (the just-pushed image).

- [ ] **Step 4: Flip the new GHCR package to private**

Manual, one-time, same as wedding-ui's first push: `github.com/users/hughseyxo/packages/container/ai-agents-plant-ui` → Package settings → Danger Zone → Change visibility → Private. Confirm by checking the package page shows "Private".

- [ ] **Step 5: Update the Deployment with the real digest**

Replace `@sha256:PLACEHOLDER` in `k8s/base/plant-ui/deployment.yaml` with the digest from Step 3.

- [ ] **Step 6: Create the real imagePullSecret**

```bash
read -rsp "GHCR PAT (read:packages): " GHCR_PAT
echo
sudo k3s kubectl create secret docker-registry ghcr-pull-secret \
  --namespace=plant-ui \
  --docker-server=ghcr.io \
  --docker-username=hughseyxo \
  --docker-password="$GHCR_PAT"
unset GHCR_PAT
```
Expected: `secret/ghcr-pull-secret created`. (Reuse the PAT from Phase 3/4 if still valid — same `read:packages` scope, no need to generate a fresh one.)

- [ ] **Step 7: Verify without printing the secret**

```bash
sudo k3s kubectl get secret -n plant-ui ghcr-pull-secret
```
Expected: shows the secret, `TYPE kubernetes.io/dockerconfigjson`.

- [ ] **Step 8: Commit the digest update**

```bash
git add k8s/base/plant-ui/deployment.yaml
git commit -m "chore(k8s): pin plant-ui to real GHCR digest"
git push origin main
```

---

### Task 7: STOP — dry run against the live cluster, real writes

**Files:** none (cluster + real repo data)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 6's.

- [ ] **Step 2: Deploy alongside the still-live systemd service**

```bash
sudo k3s kubectl apply -k k8s/base/plant-ui/
sudo k3s kubectl -n plant-ui rollout status deployment/plant-ui --timeout=60s
```
Expected: `deployment "plant-ui" successfully rolled out`.

- [ ] **Step 3: `/healthz` check**

```bash
curl -sf http://<TAILSCALE_IP>:30801/healthz
```
Expected: `{"status":"ok"}`. (`plant_ui.service` is still bound to `:8765` on the same host — no port conflict, since the pod's NodePort is `30801`.)

- [ ] **Step 4: Real chat write + `--resume` continuity across a pod restart**

```bash
curl -s -X POST http://<TAILSCALE_IP>:30801/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Remember the word FROBNICATE for our next message.", "scope": "garden"}' | tee /tmp/chat1.json
SESSION_ID=$(python3 -c "import json;print(json.load(open('/tmp/chat1.json'))['session_id'])")
sudo k3s kubectl -n plant-ui delete pod -l app=plant-ui
sudo k3s kubectl -n plant-ui rollout status deployment/plant-ui --timeout=60s
curl -s -X POST http://<TAILSCALE_IP>:30801/api/chat \
  -H 'Content-Type: application/json' \
  -d "{\"message\": \"What word did I ask you to remember?\", \"scope\": \"garden\", \"session_id\": \"$SESSION_ID\"}"
```
Expected: the second response mentions "FROBNICATE" — proves `/srv/k3s-claude-home`'s session transcripts survived the pod restart via the hostPath mount.

- [ ] **Step 5: Real photo upload → observation note**

Pick a real plant name already in `data/agents.db` (check with `curl -s http://<TAILSCALE_IP>:30801/api/plants | python3 -m json.tool` first) and a real photo file, then:
```bash
curl -s -X POST "http://<TAILSCALE_IP>:30801/api/plants/<PLANT_NAME>/photo" \
  -F "file=@/path/to/real/photo.jpg"
ls -la docs/plant-observations/<plant-slug>/ | tail -3
```
Expected: a new `docs/plant-observations/<plant-slug>/YYYY-MM-DD-*.md` file appears on the real host disk (not a copy — this is the hostPath mount, so the pod's write lands directly in the repo's tracked `docs/`).

- [ ] **Step 6: Real watering-record write, checked against host-side readers**

```bash
curl -s -X POST "http://<TAILSCALE_IP>:30801/api/plants/<PLANT_NAME>/water"
sqlite3 data/agents.db "SELECT name, last_watered FROM plants WHERE name = '<PLANT_NAME>';"
```
Expected: `last_watered` shows today's date — proves the pod wrote into the *same* `data/agents.db` file the host-side `PlantAgent`/cron agents read, not a forked copy (the whole point of the hostPath-not-PVC decision).

- [ ] **Step 7: Clean up the test note (don't leave test content in production docs)**

Delete the observation note file created in Step 5 if it was clearly a test artifact, or leave it — use judgment based on whether the test photo was of a real plant with real content worth keeping.

---

### Task 8: STOP — Traefik cutover

**Files (outside this repo):**
- Modify: `~/git/yopflix/seedbox/traefik/custom/middlewares.yaml`
- Create: `~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml` (gitignored)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 7's. This touches live Traefik config fronting 21+ other services on the same box.

- [ ] **Step 2: Positive-reachability baseline, before any change**

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w "%{http_code}" http://<TAILSCALE_IP>/  # Jellyfin or whatever's default-routed
```
Record both numbers.

- [ ] **Step 3: Add the `ipAllowList` middleware**

In `~/git/yopflix/seedbox/traefik/custom/middlewares.yaml`, add under `http.middlewares`:

```yaml
    tailscale-only:
      ipAllowList:
        sourceRange:
          - "100.64.0.0/10"
```

- [ ] **Step 4: Add the router + service**

Create `~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml`:

```yaml
http:
  routers:
    plant-ui-k8s:
      rule: 'Host(`plants.yopflix.world`)'
      middlewares:
        - tailscale-only@file
      service: plant-ui-k8s
  services:
    plant-ui-k8s:
      loadBalancer:
        servers:
          - url: "http://<TAILSCALE_IP>:30801"
```

Replace `<TAILSCALE_IP>` with the real value (`tailscale ip -4` on the host, or the `TAILSCALE_IP` var in `.env`) — this file is gitignored, so the literal IP is safe here.

- [ ] **Step 5: Verify Traefik picked up the change**

```bash
docker logs traefik --tail 20 2>&1 | grep -i "plant-ui\|error"
```
Expected: no error lines; if Traefik logs router additions at this level, `plant-ui-k8s` appears.

- [ ] **Step 6: Resolve `plants.yopflix.world` for testing on this host**

```bash
grep -q plants.yopflix.world /etc/hosts || echo "<TAILSCALE_IP> plants.yopflix.world" | sudo tee -a /etc/hosts
```

- [ ] **Step 7: Positive check — Tailscale-side reachability**

```bash
curl -sf http://plants.yopflix.world/healthz
```
Expected: `{"status":"ok"}`.

- [ ] **Step 8: Negative check — confirm it's NOT reachable off-Tailscale**

From a device NOT on the tailnet (e.g. the same off-Tailscale Windows laptop used in Phase 2's negative exposure test, or a mobile connection with Tailscale disabled), attempt:
```
curl -sf http://plants.yopflix.world/healthz
```
Expected: connection fails or times out (public DNS doesn't resolve `plants.yopflix.world` at all, since it was never added there — this is belt-and-suspenders on top of the `ipAllowList`). Additionally, from any machine, test the `ipAllowList` itself directly against the public IP with a forged Host header:
```bash
curl -sf -H "Host: plants.yopflix.world" https://37.187.226.57/healthz -k
```
Expected: `403 Forbidden` (proves the `ipAllowList` middleware itself blocks non-Tailscale source IPs, not just DNS obscurity).

- [ ] **Step 9: Positive-reachability regression check**

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w "%{http_code}" http://<TAILSCALE_IP>/
```
Expected: both numbers match Step 2's baseline exactly — confirms the Traefik config change didn't break anything else on the box.

---

### Task 9: STOP — cutover window

**Files:** none (systemd + verification only)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 8's.

- [ ] **Step 2: Stop the systemd service**

```bash
systemctl --user stop plant_ui.service
```

- [ ] **Step 3: Confirm the k8s pod is now the only thing serving requests**

```bash
curl -sf http://plants.yopflix.world/healthz
systemctl --user status plant_ui.service | head -3
```
Expected: `/healthz` still returns `{"status":"ok"}` (from the k8s pod, since `plant_ui.service` is now stopped and both share the same `data/`/`docs/` — this proves the pod alone is now authoritative), `systemctl status` shows `inactive (dead)`.

- [ ] **Step 4: Disable the systemd service so it doesn't restart on next boot**

```bash
systemctl --user disable plant_ui.service
```

**Discovered running this:** `plant_ui.service`'s unit file at `~/.config/systemd/user/plant_ui.service` is a symlink to the real source in this repo (`plant_ui/plant_ui.service`, same pattern as `telegram-bot/concierge-bot.service`) — `disable` removed *both* that top-level symlink and the `default.target.wants/` enablement symlink, leaving the unit completely unloadable (`could not be found`), not just non-autostarting. The repo source file is untouched, but restore the top-level symlink afterward so the unit stays manually startable for Task 10's rollback drill:
```bash
ln -s /home/cian/git/ai-agents/plant_ui/plant_ui.service /home/cian/.config/systemd/user/plant_ui.service
systemctl --user daemon-reload
```
Expected `systemctl --user status plant_ui.service` after this: `Loaded: loaded (...; linked; ...)`, `Active: inactive (dead)` — loadable, not enabled.

- [ ] **Step 5: Positive-reachability regression check, one more time**

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w "%{http_code}" http://<TAILSCALE_IP>/
```
Expected: matches Task 8 Step 2's baseline.

---

### Task 10: STOP — rollback drill (actually executed)

**Files:** none

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 9's.

- [ ] **Step 2: Revert to systemd**

```bash
systemctl --user enable --now plant_ui.service
sleep 3
curl -sf http://localhost:8765/healthz
```
Expected: `{"status":"ok"}` from the systemd service.

- [ ] **Step 3: Confirm real, untouched data still serves correctly**

```bash
curl -s http://localhost:8765/api/plants | python3 -m json.tool | head -20
```
Expected: real plant list, including the watering-record update from Task 7 Step 6 (proves the rollback isn't looking at a stale/forked copy — same file, same data, either serving process).

- [ ] **Step 4: Re-cut-over to the k8s pod**

```bash
systemctl --user stop plant_ui.service
systemctl --user disable plant_ui.service
curl -sf http://plants.yopflix.world/healthz
```
Expected: `{"status":"ok"}`, confirming the pod is serving again after the rollback-and-recut sequence.

---

### Task 11: Record completion

**Files:**
- Modify: `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md`
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Add a completion status block to the Phase 6 design doc**

Summarize what was built, any deviations from the design found during implementation (NodePort vs ClusterIP, the three-file `telegram-bot/` dependency, the `chat_backend.py` fix), and the verification evidence from Tasks 7-10.

- [ ] **Step 2: Add a Phase 6 status entry to the parent migration design doc**

Same format as the existing Phase 2/3/4 status paragraphs in that doc's Status section — one paragraph, what's done, what (if anything) deviated from plan.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 6 (plant-ui cutover) completion"
git push origin main
```

- [ ] **Step 4: Invoke `superpowers:finishing-a-development-branch`**

If Tasks 1-11 were executed on a dedicated `k3s-migration-phase6` branch (per this project's established per-phase branching pattern), run the finishing-a-development-branch skill now to merge it back.

---

## Self-Review

**Spec coverage:** design doc decision 1 (dedicated image) → Task 3. Decision 2 (namespace/PSA) → Task 5 Step 1. Decision 3 (hostPath storage, all four mount categories, the credential-class judgment call) → Task 5 Step 3. Decision 4 (Traefik/NodePort/ipAllowList/hostname, including the plan-writing-time correction) → Tasks 5 Step 4, 8. Decision 5 (`/healthz`) → Task 2. Decision 6 (imagePullSecret) → Task 6. Decision 7 (tool-surface fix) → Task 1. Verification section's five checkpoints → Task 7 (chat/photo/watering writes), Task 8 Steps 7-9 (Traefik positive/negative + regression), Task 10 (rollback drill).

**Placeholder scan:** no TBD/TODO; the one literal `PLACEHOLDER` in Task 5's `deployment.yaml` is intentional and explicitly replaced in Task 6 Step 5, not a plan gap.

**Type/interface consistency:** `NodePort 30801` identical across Task 5 Step 4's Service, Task 7 Steps 3-6's curls, Task 8 Step 4's Traefik backend, Task 9 Step 3's verification. Namespace `plant-ui` identical across every `kubectl ... -n plant-ui` invocation. Secret name `ghcr-pull-secret` matches between Task 6 Step 6's creation and Task 5 Step 3's Deployment `imagePullSecrets`. `/healthz`'s `{"status": "ok"}` shape matches between Task 2's implementation/test and every later task's curl checks.

## Related

- `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md` — design this plan implements.
- `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` — parent migration design.
- `docs/superpowers/plans/2026-08-03-k3s-phase3-wedding-ui-cutover.md` — NodePort + Traefik file-provider pattern this plan reuses.
- `docs/superpowers/plans/2026-08-03-k3s-phase4-cronjobs-cutover.md` — hostPath + PSA privileged pattern this plan reuses.
