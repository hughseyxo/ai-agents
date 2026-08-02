# Phase 1 — Images + CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the two container images the k3s migration depends on (`ai-agents-wedding-ui`, `ai-agents-runner`), a minimal but real `k8s/` manifest tree to give CI something to lint, and a GitHub Actions pipeline that builds, tests, lints, and — on `main` only — pushes digest-pinned images to GHCR with SBOM/provenance attestation.

**Architecture:** No new runtime services. Two Dockerfiles (one hardened, one new), a `k8s/base/` skeleton (just enough for kubeconform to have real input — the workload manifests themselves are Phase 2–5's job), and one GitHub Actions workflow with a PR-safe validate job and a `main`-only, digest-pinned release job. Local verification substitutes for actually running GitHub Actions (no push happens as part of this plan) by using the same tools directly: `docker build`, `docker run` as the target non-root UID, and `kubeconform`'s official container image (no local binary needed — none of `kubeconform`/`actionlint`/`syft`/`cosign` are installed on this host).

**Tech Stack:** Docker, GitHub Actions, `kubeconform` (via `ghcr.io/yannh/kubeconform` image), `actions/attest-build-provenance` (GitHub-native SLSA provenance + Sigstore-backed attestation, covers the "SBOM + provenance" requirement without a separate `syft`/`cosign` install), `cosign` (documented verification command only, not installed here).

## Global Constraints

- GitHub repo is `hughseyxo/ai-agents` (public) — GHCR images are `ghcr.io/hughseyxo/ai-agents-wedding-ui` and `ghcr.io/hughseyxo/ai-agents-runner`.
- Images are referenced by `@sha256:` digest, never a mutable tag (design doc Architecture section).
- `ai-agents-runner`: repo baked in at `/home/cian/git/ai-agents` (not `/app` — design decision 8, `~/.claude.json` keys trust to this exact path), user `cian` uid/gid 1001 (decision 7 — `data/agents.db` is `0644 cian:cian`, and the `claude` CLI refuses `--dangerously-skip-permissions` as root).
- `ai-agents-wedding-ui`: multi-stage build, non-root (Architecture section — the current `wedding_ui/Dockerfile` is neither; Task 1 fixes this before any other Phase 1 work).
- `claude` CLI pinned to `2.1.92` (`@anthropic-ai/claude-code`, npm) — the exact version already proven working in the Phase 0 credential-isolation spike (`docs/superpowers/specs/2026-08-01-k3s-migration-design.md`, Phase 0 task 6 status note).
- Build context for every image is the repo root (existing convention, see `wedding_ui/Dockerfile:2` comment); rely on the Phase 0 `.dockerignore`.
- `agents/requirements.txt` (Phase 0) is the pinned dependency list `ai-agents-runner` installs from.
- `ai-agents-runner` copies only `agents/` and `mcp-servers/` — not `telegram-bot/` or `plant_ui/`. Migration modes (design doc) only cut over `wedding-ui`, `couchdb`, `livesync-bridge`, and the cron agents; the concierge bot and plant-ui stay host-side.
- CI: explicit `permissions:` blocks on every job (least privilege, not the repo default). `on: pull_request`, never `pull_request_target` (design doc Phase 1 bullet — the latter runs with base-branch secrets against PR-controlled code). Image push gated to `push` events on `main` only.
- `kubeconform --ignore-missing-schemas` in CI, not `-strict` — `-strict` errors on CRDs and there are none to validate against yet.
- No tool in this plan may be installed globally on the host beyond what a `docker run` of an official image provides — matches "none of kubeconform/actionlint/syft/cosign are installed" from this session's own check.

---

### Task 1: Harden `wedding_ui/Dockerfile` — multi-stage, non-root

**Files:**
- Modify: `wedding_ui/Dockerfile`

**Interfaces:**
- Consumes: `wedding_ui/requirements.txt` (existing), `wedding_ui/` package, `agents/__init__.py` + `agents/db.py` (existing `COPY` targets).
- Produces: an image where `whoami` reports a non-root user and `/healthz` (Phase 0) still returns 200 — this is what Phase 2's liveness probe and Phase 3's `runAsNonRoot` pod security context depend on.

- [ ] **Step 1: Read the current Dockerfile**

Run: `cat wedding_ui/Dockerfile` — confirm it's still the single-stage, root-user version (no `USER` line, single `FROM python:3.12-slim`). If it has already changed, stop and reconcile with whoever changed it before continuing.

- [ ] **Step 2: Rewrite as a multi-stage, non-root build**

Replace the full contents of `wedding_ui/Dockerfile` with:

```dockerfile
# Wedding Budget PWA — self-contained image for the k3s cluster.
# Build context = the ai-agents repo root:  docker build -f wedding_ui/Dockerfile -t wedding-fund .
FROM python:3.12-slim AS builder
WORKDIR /app
COPY wedding_ui/requirements.txt /app/wedding_ui/requirements.txt
RUN pip install --no-cache-dir --user -r /app/wedding_ui/requirements.txt

FROM python:3.12-slim
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/app/.local/bin:$PATH

COPY --from=builder /root/.local /home/app/.local

# App package + the AgentDB helper it imports.
COPY wedding_ui/ /app/wedding_ui/
COPY agents/__init__.py agents/db.py /app/agents/

# wedding.db lives here; mount a named volume/PVC at /app/data to persist it.
RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "wedding_ui.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build and verify non-root + working health check**

Run:
```bash
docker build -f wedding_ui/Dockerfile -t wedding-ui-phase1-test .
docker run --rm -d --name wedding-ui-phase1-test -p 18000:8000 wedding-ui-phase1-test
sleep 2
docker exec wedding-ui-phase1-test whoami
curl -sf http://localhost:18000/healthz
docker logs wedding-ui-phase1-test
docker stop wedding-ui-phase1-test
```
Expected: `whoami` prints `app` (not `root`); the `curl` prints `{"status":"ok"}`; no tracebacks in `docker logs`.

- [ ] **Step 4: Clean up test image and commit**

```bash
docker rmi wedding-ui-phase1-test
git add wedding_ui/Dockerfile
git commit -m "build(wedding-ui): multi-stage, non-root image for k3s"
```

---

### Task 2: `Dockerfile.runner` for `ai-agents-runner`

**Files:**
- Create: `Dockerfile.runner`

**Interfaces:**
- Consumes: `agents/requirements.txt` (Phase 0), `agents/` package, `mcp-servers/` package.
- Produces: an image where `python3 -m agents --list` succeeds, `claude --version` reports `2.1.92`, `id -u` reports `1001`, and `pwd` inside the image at the app's working directory is exactly `/home/cian/git/ai-agents` — the last two are what Phase 3+'s pod specs and `~/.claude.json` trust resolution depend on (Global Constraints, decisions 7–8).

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# ai-agents-runner — runs the cron agents (agents/) plus the MCP servers
# they call (mcp-servers/). Does NOT include telegram-bot/ or plant_ui/ —
# those stay host-side (see the k3s migration design doc's Migration modes
# table). Based on node:20-slim (not python:3.12-slim) because the `claude`
# CLI install is what's actually proven working (Phase 0's credential-
# isolation spike used this exact base + `npm install -g
# @anthropic-ai/claude-code@2.1.92` successfully); adding Python on top via
# apt is one `apt-get install`, versus reconstructing Node's install layout
# by hand via a fragile multi-stage COPY.
# Build context = the ai-agents repo root:
#   docker build -f Dockerfile.runner -t ai-agents-runner .
FROM node:20-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.92

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 cian && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash cian

WORKDIR /home/cian/git/ai-agents
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY agents/requirements.txt /home/cian/git/ai-agents/agents/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r agents/requirements.txt

COPY agents/ /home/cian/git/ai-agents/agents/
COPY mcp-servers/ /home/cian/git/ai-agents/mcp-servers/
COPY .mcp.json /home/cian/git/ai-agents/.mcp.json

RUN mkdir -p /home/cian/git/ai-agents/data /home/cian/git/ai-agents/output && \
    chown -R cian:cian /home/cian/git/ai-agents /home/cian

USER cian
ENTRYPOINT ["python3", "-m", "agents"]
```

Note on `--break-system-packages`: Debian's system Python (installed via `apt` in a `node:20-slim` base) marks itself PEP 668 "externally managed" — `pip install` refuses without this flag or a venv. A venv is unnecessary complexity for a single-purpose container image with no competing Python installs.

- [ ] **Step 2: Build and verify**

Run:
```bash
docker build -f Dockerfile.runner -t ai-agents-runner-phase1-test .
docker run --rm --entrypoint id ai-agents-runner-phase1-test
docker run --rm --entrypoint pwd ai-agents-runner-phase1-test
docker run --rm --entrypoint claude ai-agents-runner-phase1-test --version
docker run --rm ai-agents-runner-phase1-test --list
```
Expected: `id` shows `uid=1001(cian) gid=1001(cian)`; `pwd` prints `/home/cian/git/ai-agents`; `claude --version` prints `2.1.92 (Claude Code)`; `--list` prints the `AGENT_REGISTRY` table (`daily-briefing`, `news-briefing`, `security-audit`, `travel-agent`, `librarian`, `plant-agent`, `agent-health`) without a Python traceback.

- [ ] **Step 3: Confirm the image never contains `data/`, `docs/`, or `.git`**

Run:
```bash
docker run --rm --entrypoint find ai-agents-runner-phase1-test /home/cian/git/ai-agents -maxdepth 1
```
Expected: only `agents`, `mcp-servers`, `.mcp.json`, `data` (empty, created by `mkdir`), `output` (empty). No `docs`, no `.git`, no `telegram-bot`, no `plant_ui` — confirms the `.dockerignore` (Phase 0) plus this Dockerfile's explicit `COPY` list together keep the image minimal, same second-boundary pattern as Task 2 of the Phase 0 plan.

- [ ] **Step 4: Clean up test image and commit**

```bash
docker rmi ai-agents-runner-phase1-test
git add Dockerfile.runner
git commit -m "build: add Dockerfile.runner for ai-agents-runner image"
```

---

### Task 3: `k8s/base/` skeleton — namespace + parameterised overlay pattern

Design decision 5 requires manifests to be parameterised, never hardcoded, with a committed `prod.example` overlay and a gitignored real one. The namespace is the one manifest every later phase needs regardless of which workload lands first, so it's a real (not throwaway) Phase 1 deliverable — everything workload-specific (Deployments, CronJobs, the wedding-ui Service) belongs to Phases 3–5, not here.

**Files:**
- Create: `k8s/base/namespace.yaml`
- Create: `k8s/base/kustomization.yaml`
- Create: `k8s/overlays/prod.example/kustomization.yaml`
- Modify: `.gitignore` (add `k8s/overlays/prod/`)

**Interfaces:**
- Consumes: nothing.
- Produces: `ai-agents` namespace with PSA `baseline` label (agent pods need hostPath, per design doc "Pod security" section — `restricted` is Phase 3's job to apply at the workload level for `wedding-ui` specifically, not the shared namespace). Establishes the overlay directory layout (`k8s/base` committed, `k8s/overlays/prod` gitignored, `prod.example` committed as the template) that Phases 2–5 add workload manifests into.

- [ ] **Step 1: Write the namespace manifest**

```yaml
# k8s/base/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-agents
  labels:
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: baseline
```

- [ ] **Step 2: Write the base kustomization**

```yaml
# k8s/base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
```

- [ ] **Step 3: Write the example overlay**

```yaml
# k8s/overlays/prod.example/kustomization.yaml
# Copy this directory to k8s/overlays/prod/ (gitignored) and fill in
# host-specific values there. Never commit k8s/overlays/prod/ — see
# CLAUDE.md's <TAILSCALE_IP> pattern for why.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
```

- [ ] **Step 4: Gitignore the real overlay**

Add to `.gitignore`, near the other host-specific-secret entries:
```
k8s/overlays/prod/
```

- [ ] **Step 5: Validate with kubeconform (container image, nothing installed locally)**

Run:
```bash
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest -ignore-missing-schemas -summary /k8s/base/namespace.yaml
```
Expected: summary line reporting `Resources: 1, Skipped: 0, Errors: 0`.

- [ ] **Step 6: Commit**

```bash
git add k8s/base/namespace.yaml k8s/base/kustomization.yaml k8s/overlays/prod.example/kustomization.yaml .gitignore
git commit -m "feat(k8s): add namespace manifest and parameterised overlay skeleton"
```

---

### Task 4: GitHub Actions workflow — validate (PR) + release (main)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `tests/` (existing pytest suite, run via `.venv`-equivalent `pip install -r wedding_ui/requirements.txt -r agents/requirements.txt pytest`), `wedding_ui/Dockerfile` (Task 1), `Dockerfile.runner` (Task 2), `k8s/base/` (Task 3).
- Produces: on every PR — pytest + `kubeconform` + a build-only (`--push=false`) Docker build of both images, so a broken Dockerfile fails the PR before merge. On push to `main` — the same builds, but pushed to GHCR digest-pinned, with `actions/attest-build-provenance` generating SBOM + SLSA provenance attached to each digest.

- [ ] **Step 1: Write the workflow file**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          pip install -r wedding_ui/requirements.txt -r agents/requirements.txt pytest
      - name: Run tests
        run: pytest tests/ -v

  validate-manifests:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: kubeconform
        run: |
          docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest \
            -ignore-missing-schemas -summary -recursive /k8s/base

  build:
    needs: [test, validate-manifests]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    strategy:
      matrix:
        image: [wedding-ui, runner]
        include:
          - image: wedding-ui
            dockerfile: wedding_ui/Dockerfile
          - image: runner
            dockerfile: Dockerfile.runner
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Log in to GHCR
        if: github.event_name == 'push'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build (and push on main only)
        id: build
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ${{ matrix.dockerfile }}
          push: ${{ github.event_name == 'push' }}
          tags: ghcr.io/hughseyxo/ai-agents-${{ matrix.image }}:latest
          outputs: type=image,name=ghcr.io/hughseyxo/ai-agents-${{ matrix.image }},push-by-digest=true,name-canonical=true,push=${{ github.event_name == 'push' }}
      - name: Attest build provenance
        if: github.event_name == 'push'
        uses: actions/attest-build-provenance@v1
        with:
          subject-name: ghcr.io/hughseyxo/ai-agents-${{ matrix.image }}
          subject-digest: ${{ steps.build.outputs.digest }}
          push-to-registry: true
```

- [ ] **Step 2: Validate the YAML is well-formed**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "valid YAML"
```
Expected: `valid YAML`, no exception. (This cannot exercise the actions themselves — GitHub Actions only runs server-side on a push. Actual pipeline execution is verified after this plan's changes are pushed, which needs your explicit go-ahead per this session's git-safety rules — not part of this plan's local verification.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add validate (PR) and digest-pinned release (main) workflow"
```

---

### Task 5: Document GHCR visibility and the `cosign verify-attestation` step

The design doc's Phase 1 bullet requires stating GHCR visibility (public vs. private, and an `imagePullSecret` if private) and a documented `cosign verify-attestation` command — `cosign` itself isn't installed on this host (confirmed in this plan's header), so this is a documentation deliverable, not a script.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Append a Phase 1 status note**

Add to the `## Status` section, after the existing Phase 0 status notes:

```markdown
**Phase 1 — Images + CI — in progress (2026-08-02):** `wedding_ui/Dockerfile` hardened to multi-stage/non-root; `Dockerfile.runner` added for `ai-agents-runner` (`claude` CLI pinned `2.1.92`, matching the Phase 0 spike); `k8s/base/namespace.yaml` + parameterised overlay skeleton added; `.github/workflows/ci.yml` added (PR: pytest + kubeconform + build-only; `main` push: build, push to GHCR by digest, `actions/attest-build-provenance` for SBOM + SLSA provenance).

**GHCR visibility:** `ghcr.io/hughseyxo/ai-agents-*` inherits the parent repo's visibility. `hughseyxo/ai-agents` is public, so the images are public by default — no `imagePullSecret` needed for the k3s cluster to pull them. If visibility is ever flipped to private, a `kubernetes.io/dockerconfigjson` Secret (from a GHCR PAT with `read:packages`) must be created in the `ai-agents` namespace and referenced as `imagePullSecrets` on every workload — not done here since it isn't needed yet.

**Verifying an image's provenance attestation** (once a `main` push has actually run this pipeline):
```bash
cosign verify-attestation \
  --type slsaprovenance \
  --certificate-identity-regexp "^https://github.com/hughseyxo/ai-agents" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/hughseyxo/ai-agents-runner@sha256:<digest>
```
This confirms the image was built by this repo's GitHub Actions workflow (not pushed by hand with a stolen token) — `actions/attest-build-provenance` signs it keylessly via Sigstore/Fulcio using the workflow's own OIDC identity, which is what the `--certificate-identity-regexp`/`--certificate-oidc-issuer` pair checks.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 1 GHCR visibility decision and cosign verify-attestation step"
```

---

## Self-Review

**Spec coverage:** design doc Phase 1 bullet items — `kubeconform --ignore-missing-schemas` (Task 3 step 5, Task 4's `validate-manifests` job) ✓; explicit `permissions:` blocks (Task 4, every job) ✓; `on: pull_request` never `pull_request_target` (Task 4) ✓; image push only on `main` (Task 4's `push: ${{ github.event_name == 'push' }}` gate, and `on: push: branches: [main]`) ✓; GHCR visibility stated + `imagePullSecret` note (Task 5) ✓; SBOM + provenance + documented `cosign verify-attestation` (Task 4's `attest-build-provenance` step + Task 5's doc) ✓. Architecture section — two images to GHCR by digest (Task 2's `docker/build-push-action` uses `push-by-digest=true`; Task 1 hardens the existing wedding-ui image) ✓; `ai-agents-wedding-ui` multi-stage/non-root (Task 1) ✓; `ai-agents-runner` repo path + uid 1001 (Task 2, verified in step 2) ✓. Design decision 5 (parameterised manifests, `prod.example` pattern) — Task 3 ✓.

**Placeholder scan:** no TBDs. Every step has literal file contents or an exact command with a stated expected output, including the two non-code tasks (3, 5).

**Type/interface consistency:** Task 4's `matrix.dockerfile` values (`wedding_ui/Dockerfile`, `Dockerfile.runner`) match the exact filenames Tasks 1–2 create. Task 4's `validate-manifests` job path (`/k8s/base`) matches Task 3's actual directory. Task 5's digest-verification command is illustrative (real digest only exists after a push) and explicitly flagged as such rather than presented as something this plan itself produces.

**Known limitation, stated honestly:** this plan cannot prove the GitHub Actions workflow actually runs correctly — only YAML well-formedness is checked locally. The real test is the first `main` push (or a PR) after these commits land, which needs your explicit go-ahead to push per this session's git-safety rules. Recommend a scratch PR against a throwaway branch first if you want to see the `validate` job go green before the first real merge to `main`.
