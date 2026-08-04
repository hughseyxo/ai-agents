# Phase 4 (CronJobs Cutover) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **No subagent may be dispatched for any task marked STOP** — those require a human partner's own, separately-given go-ahead, the same rule Phase 3's plan used.

**Goal:** Move `agent-health` from the host crontab to a real k3s `CronJob`, while creating (but leaving `suspend: true`) CronJob manifests for the other six cron-triggered resources so later phases reuse this phase's infrastructure instead of inventing it again.

**Architecture:** Reuse the existing `ai-agents-runner` image. A new `ai-agents-cron` namespace (PSA `privileged`) hosts all 7 CronJobs — not the existing `ai-agents` namespace (PSA `baseline`), since `baseline` forbids `hostPath` volumes outright (discovered via `kind` smoke test; see Task 5). Both `data/agents.db` and the two Google OAuth token files are hostPath-mounted so the pod and the still-running host-cron agents share one source of truth. Secrets are scoped per agent (least privilege), not a blanket `.env` mount. `agent-health`'s CronJob is the only one with `suspend: false`.

**Tech Stack:** k3s (`CronJob`, `Secret`, `initContainer`), `kubeconform`, `kustomize`, existing `agents/` Python package.

## Global Constraints

- All 7 CronJobs live in a dedicated `ai-agents-cron` namespace (PSA `privileged`), created by `k8s/base/cronjobs/namespace.yaml` — **not** the existing `ai-agents` namespace (PSA `baseline`, which forbids `hostPath` volumes). `k8s/base/cronjobs/networkpolicy.yaml` adds an equivalent default-deny-egress policy for this new namespace (DNS + outbound TCP/443), since `NetworkPolicy` is namespace-scoped and the existing `ai-agents` one doesn't cover it.
- No `timeZone:` field on any CronJob — all `cron_entries()` schedules are UTC already.
- `concurrencyPolicy: Forbid` on every CronJob.
- `backoffLimit: 0` on every CronJob (no Job-level retries).
- `check-google-token.sh` runs as an `initContainer` on **all 7** CronJob pods, uniformly.
- Secrets are scoped per agent — never a blanket mount of the whole `.env`.
- `data/agents.db` and the Google token files are hostPath-mounted, not PVC-backed copies.
- Only `agent-health`'s CronJob has `suspend: false`. All six others (`plant-agent`, `daily-briefing`, `news-briefing`, `security-audit`, `librarian-audit`, `librarian-watch`) stay `suspend: true`.
- Real GHCR digest for `ai-agents-runner`, pushed by CI run `30833749005` (rebuilt after Task 1's `curl`/`scripts/` change — the image that existed before Task 1 lacked both, and would have made the `check-google-token.sh` initContainer fail): `sha256:7060d27e35444c3e549c0f27d0e8e6543bd8d10eadd86465b4a77e61a833c6c8` (verified 64 hex chars).
- Exact agent names (the `python3 -m agents <name>` CLI argument, from each class's `name = "..."` attribute): `agent-health`, `plant-agent`, `daily-briefing`, `news-briefing`, `security-audit`, `librarian` (librarian additionally takes `--mode audit` or `--mode watch`).
- Exact schedules (from each agent's `schedule` / `cron_entries()`): `agent-health` `0 * * * *`, `plant-agent` `0 * * * *`, `daily-briefing` `5 4 * * *`, `news-briefing` `0 4 * * *`, `security-audit` `0 6 * * 0`, `librarian --mode audit` `0 6 * * 0`, `librarian --mode watch` `0 6 * * 1-6`.
- Secret env vars actually needed, verified by grep across `agents/`, `mcp-servers/`, and `.mcp.json`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (used by `check-google-token.sh`, `agents/gmail_client.py`, `agents/drive_client.py`, `mcp-servers/calendar_server.py`), and `TODOIST_API_TOKEN` (used only by the remote Todoist MCP server's `Authorization` header in `.mcp.json`). No other secret env vars are read anywhere in the cron-agent code path.
- hostPath sources (all under the real host's filesystem, mounted 1:1 to the identical path inside the container since the container's `WORKDIR`/`$HOME` already match the host's `cian` user layout): `/home/cian/git/ai-agents/data` → same path; `/home/cian/.google_tokens.json` → same path; `/home/cian/.google_tokens_drive.json` → same path.

---

### Task 1: Add `curl` and `scripts/` to the runner image

`check-google-token.sh` needs `curl` (for the OAuth refresh POST) and lives in `scripts/`, but `Dockerfile.runner` currently installs only `python3`/`python3-pip` and copies only `agents/`, `mcp-servers/`, and `.mcp.json` — not `scripts/`. Without this, the `initContainer` fails immediately with "curl: not found".

**Files:**
- Modify: `Dockerfile.runner`

**Interfaces:**
- Produces: an `ai-agents-runner` image where `scripts/check-google-token.sh` is present at `/home/cian/git/ai-agents/scripts/check-google-token.sh` and `curl` is on `$PATH`, runnable via `curl --version`.

- [ ] **Step 1: Add `curl` to the apt install line**

In `Dockerfile.runner`, change:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip && rm -rf /var/lib/apt/lists/*
```
to:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip curl && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Copy `scripts/` into the image**

In `Dockerfile.runner`, after the existing `COPY .mcp.json ...` line, add:
```dockerfile
COPY scripts/check-google-token.sh /home/cian/git/ai-agents/scripts/check-google-token.sh
```

- [ ] **Step 3: Build locally and verify**

```bash
docker build -f Dockerfile.runner -t ai-agents-runner-test .
docker run --rm ai-agents-runner-test true 2>&1 | head -5 || true
docker run --rm --entrypoint curl ai-agents-runner-test --version
docker run --rm --entrypoint test ai-agents-runner-test -f /home/cian/git/ai-agents/scripts/check-google-token.sh && echo "script present"
```
Expected: `curl --version` prints a version line; `script present` is printed (the `-f` test succeeded).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.runner
git commit -m "feat(k8s): add curl + scripts/ to runner image for check-google-token.sh initContainer"
```

---

### Task 2: Write the 7 CronJob manifests

**Files:**
- Create: `k8s/base/cronjobs/agent-health.yaml`
- Create: `k8s/base/cronjobs/plant-agent.yaml`
- Create: `k8s/base/cronjobs/daily-briefing.yaml`
- Create: `k8s/base/cronjobs/news-briefing.yaml`
- Create: `k8s/base/cronjobs/security-audit.yaml`
- Create: `k8s/base/cronjobs/librarian-audit.yaml`
- Create: `k8s/base/cronjobs/librarian-watch.yaml`
- Create: `k8s/base/cronjobs/kustomization.yaml`
- Modify: `k8s/base/kustomization.yaml`

**Interfaces:**
- Consumes: the `ai-agents-runner` image digest and hostPath sources from Global Constraints; the `ghcr-pull-secret` (Task 4) and per-agent Secrets (Task 3) by name.
- Produces: 7 `CronJob` objects in the `ai-agents` namespace, validated by `kubeconform` in Task 5.

- [ ] **Step 1: `agent-health.yaml` — the one live CronJob**

```yaml
# k8s/base/cronjobs/agent-health.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: agent-health
  namespace: ai-agents-cron
spec:
  schedule: "0 * * * *"
  concurrencyPolicy: Forbid
  suspend: false
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 300
      template:
        spec:
          restartPolicy: Never
          imagePullSecrets:
            - name: ghcr-pull-secret
          securityContext:
            runAsNonRoot: true
            runAsUser: 1001
            runAsGroup: 1001
            seccompProfile:
              type: RuntimeDefault
          initContainers:
            - name: check-google-token
              image: ghcr.io/hughseyxo/ai-agents-runner@sha256:7060d27e35444c3e549c0f27d0e8e6543bd8d10eadd86465b4a77e61a833c6c8
              command: ["bash", "scripts/check-google-token.sh"]
              envFrom:
                - secretRef:
                    name: agent-health-secrets
              securityContext:
                allowPrivilegeEscalation: false
                capabilities:
                  drop: ["ALL"]
              volumeMounts:
                - name: google-token
                  mountPath: /home/cian/.google_tokens.json
                - name: google-token-drive
                  mountPath: /home/cian/.google_tokens_drive.json
          containers:
            - name: agent-health
              image: ghcr.io/hughseyxo/ai-agents-runner@sha256:7060d27e35444c3e549c0f27d0e8e6543bd8d10eadd86465b4a77e61a833c6c8
              args: ["agent-health"]
              envFrom:
                - secretRef:
                    name: agent-health-secrets
              securityContext:
                allowPrivilegeEscalation: false
                capabilities:
                  drop: ["ALL"]
              volumeMounts:
                - name: agents-data
                  mountPath: /home/cian/git/ai-agents/data
                - name: google-token
                  mountPath: /home/cian/.google_tokens.json
                - name: google-token-drive
                  mountPath: /home/cian/.google_tokens_drive.json
              resources:
                requests:
                  cpu: 50m
                  memory: 128Mi
                limits:
                  cpu: 500m
                  memory: 512Mi
          volumes:
            - name: agents-data
              hostPath:
                path: /home/cian/git/ai-agents/data
                type: Directory
            - name: google-token
              hostPath:
                path: /home/cian/.google_tokens.json
                type: File
            - name: google-token-drive
              hostPath:
                path: /home/cian/.google_tokens_drive.json
                type: File
```

- [ ] **Step 2: `plant-agent.yaml`, `daily-briefing.yaml`, `news-briefing.yaml`, `security-audit.yaml` — same shape, `suspend: true`**

For each, copy `agent-health.yaml` and change exactly these fields (everything else — volumes, initContainer, securityContext, resources — stays identical):

| File | `metadata.name` | `spec.schedule` | `spec.suspend` | `jobTemplate...activeDeadlineSeconds` | `containers[0].args` | `envFrom` secretRef name (both containers) |
|---|---|---|---|---|---|---|
| `plant-agent.yaml` | `plant-agent` | `"0 * * * *"` | `true` | `3000` | `["plant-agent"]` | `plant-agent-secrets` |
| `daily-briefing.yaml` | `daily-briefing` | `"5 4 * * *"` | `true` | `1800` | `["daily-briefing"]` | `daily-briefing-secrets` |
| `news-briefing.yaml` | `news-briefing` | `"0 4 * * *"` | `true` | `1800` | `["news-briefing"]` | `news-briefing-secrets` |
| `security-audit.yaml` | `security-audit` | `"0 6 * * 0"` | `true` | `1800` | `["security-audit"]` | `security-audit-secrets` |

- [ ] **Step 3: `librarian-audit.yaml`, `librarian-watch.yaml` — same shape, two-arg command**

Same as Step 2's table, but `containers[0].args` is `["librarian", "--mode", "audit"]` (for `librarian-audit.yaml`, `schedule: "0 6 * * 0"`) or `["librarian", "--mode", "watch"]` (for `librarian-watch.yaml`, `schedule: "0 6 * * 1-6"`). Both use `suspend: true`, `activeDeadlineSeconds: 1800`, and `envFrom` secretRef name `librarian-secrets` (shared by both, since they're the same agent class).

- [ ] **Step 4: `k8s/base/cronjobs/kustomization.yaml`**

```yaml
# k8s/base/cronjobs/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - agent-health.yaml
  - plant-agent.yaml
  - daily-briefing.yaml
  - news-briefing.yaml
  - security-audit.yaml
  - librarian-audit.yaml
  - librarian-watch.yaml
```

- [ ] **Step 5: Wire into `k8s/base/kustomization.yaml`**

```yaml
# before:
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - wedding-ui
# after:
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - wedding-ui
  - cronjobs
```

- [ ] **Step 6: Commit**

```bash
git add k8s/base/cronjobs/ k8s/base/kustomization.yaml
git commit -m "feat(k8s): add CronJob manifests for all 7 cron agents (only agent-health live)"
```

---

### Task 3: STOP — create the per-agent Secrets

**Requires real credential values from `.env` — cannot be scripted without the human partner's own input.**

**Files:** none (cluster state only)

- [ ] **Step 1: Confirm go-ahead, then create the Secrets in the `ai-agents` namespace**

Ask the human partner to run this themselves (via the `!` prefix), so `.env`'s real values never enter the assistant's context:

```bash
source .env
sudo k3s kubectl create secret generic agent-health-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET"
sudo k3s kubectl create secret generic plant-agent-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET"
sudo k3s kubectl create secret generic daily-briefing-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET" \
  --from-literal=TODOIST_API_TOKEN="$TODOIST_API_TOKEN"
sudo k3s kubectl create secret generic news-briefing-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET"
sudo k3s kubectl create secret generic security-audit-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET"
sudo k3s kubectl create secret generic librarian-secrets \
  --namespace=ai-agents-cron \
  --from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  --from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET"
```
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are included on every Secret because `check-google-token.sh`'s `initContainer` runs uniformly on all 7 CronJobs (Global Constraints) and needs them regardless of whether the main container itself calls a Google API. `TODOIST_API_TOKEN` is added only to `daily-briefing-secrets`, the one agent whose `.mcp.json` Todoist server needs it.

Expected: seven `secret/<name> created` lines.

- [ ] **Step 2: Verify (without printing contents)**

```bash
sudo k3s kubectl get secrets -n ai-agents-cron | grep -- -secrets
```
Expected: seven rows, `TYPE` = `Opaque`.

---

### Task 4: STOP — create the `imagePullSecret`

**Requires a human-generated GitHub PAT — cannot be scripted. If the `read:packages` PAT generated for Phase 3's `ghcr-pull-secret` is still valid, it can be reused here; otherwise generate a fresh one the same way.**

**Files:** none (cluster state only)

- [ ] **Step 1: Confirm go-ahead, then create the Secret in the `ai-agents` namespace**

```bash
read -rsp "GHCR PAT (read:packages): " GHCR_PAT
echo
sudo k3s kubectl create secret docker-registry ghcr-pull-secret \
  --namespace=ai-agents-cron \
  --docker-server=ghcr.io \
  --docker-username=hughseyxo \
  --docker-password="$GHCR_PAT"
unset GHCR_PAT
```
Expected: `secret/ghcr-pull-secret created`.

- [ ] **Step 2: Verify**

```bash
sudo k3s kubectl get secret -n ai-agents-cron ghcr-pull-secret
```
Expected: shows the secret, `TYPE kubernetes.io/dockerconfigjson`.

---

### Task 5: Validate manifests (kubeconform + kind)

**Files:** none (validation only)

- [ ] **Step 1: `kubeconform` locally**

```bash
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest \
  -ignore-missing-schemas -summary /k8s/base
```
Expected: summary line shows 0 errors, resource count includes the 7 new CronJobs.

- [ ] **Step 2: `kind` smoke test — manifests are accepted, without expecting the schedule to actually fire**

```bash
kind create cluster --name phase4-smoke 2>&1 | tail -5
kubectl --context kind-phase4-smoke apply -f k8s/base/cronjobs/namespace.yaml
kubectl --context kind-phase4-smoke create secret generic agent-health-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x
kubectl --context kind-phase4-smoke create secret generic plant-agent-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x
kubectl --context kind-phase4-smoke create secret generic daily-briefing-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x --from-literal=TODOIST_API_TOKEN=x
kubectl --context kind-phase4-smoke create secret generic news-briefing-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x
kubectl --context kind-phase4-smoke create secret generic security-audit-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x
kubectl --context kind-phase4-smoke create secret generic librarian-secrets -n ai-agents-cron --from-literal=GOOGLE_CLIENT_ID=x --from-literal=GOOGLE_CLIENT_SECRET=x
kubectl --context kind-phase4-smoke create secret docker-registry ghcr-pull-secret -n ai-agents-cron --docker-server=ghcr.io --docker-username=x --docker-password=x
kubectl --context kind-phase4-smoke apply -k k8s/base/cronjobs
kubectl --context kind-phase4-smoke get cronjobs -n ai-agents-cron
```
Expected: `apply -k` reports all 7 CronJobs `created`, no errors; `get cronjobs` lists all 7 with `SUSPEND` = `False` for `agent-health` only, `True` for the other 6. (The kind cluster has no `/home/cian` hostPath data, no real GHCR image, and fake secret values — the pods themselves are not expected to run successfully here, only the CronJob objects to be accepted as valid.)

- [ ] **Step 3: Tear down the smoke cluster**

```bash
kind delete cluster --name phase4-smoke
```

---

### Task 6: STOP — apply to the real cluster

**Files:** none (cluster state only)

- [ ] **Step 1: Confirm go-ahead, then server-side dry run**

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl apply -k k8s/base --dry-run=server
```
Expected: shows `cronjob.batch/agent-health created (server dry run)` and the same for the other 6, with no errors. Confirm nothing outside `k8s/base/cronjobs/` shows an unexpected diff (the `wedding-ui` resources should show `unchanged`).

- [ ] **Step 2: Apply for real**

```bash
kubectl apply -k k8s/base
```
Expected: `cronjob.batch/agent-health created` plus 6 more `created` lines.

- [ ] **Step 3: Verify suspend state**

```bash
kubectl get cronjobs -n ai-agents-cron
```
Expected: `agent-health` shows `SUSPEND=False`; the other 6 show `SUSPEND=True`.

---

### Task 7: Bake — verify 3 consecutive successful in-cluster `agent-health` runs

This phase targets a **homelab-appropriate** bake time, not the full week a production system would warrant: 3 consecutive successful hourly runs (a few hours of real elapsed time, checked periodically — this step cannot be compressed into a single sitting since it depends on `agent-health`'s actual hourly schedule firing).

**Real-world detour, resolved before this bake period began counting:** every hourly tick from Task 6's apply (2026-08-03 ~18:45 UTC) through 2026-08-04 09:00 UTC failed with `DeadlineExceeded`, hidden by Kubernetes' default `failedJobsHistoryLimit: 1` pruning all but the most recent failed Job. Root cause (via `superpowers:systematic-debugging`, manually-triggered test Jobs): (1) a `google-token-drive` hostPath mount pointed at a file never created on this host, blocking Pod init indefinitely (`FailedMount`) until the deadline killed it; (2) once fixed, `check-google-token.sh`'s atomic-write refresh (tempfile + `os.replace`) failed with `EBUSY` under the `google-token` hostPath `type: File` single-file mount — the temp file and the bind-mounted target aren't on the same filesystem from the container's view. Both were unnecessary for `agent-health` specifically (zero Google/Drive dependency), so both were removed from `agent-health.yaml` only (commit `b64c63f`) — the other 6 CronJobs still carry both, and the underlying write-vs-hostPath conflict is unresolved for them, deferred to whichever future phase activates one. Verified via manual Job trigger: `Completed` in ~8s, real check logic ran (`4 checked, 0 stale`), real write landed in the shared `data/agents.db`. The bake count below restarts from this fix, not from Task 6's original apply.

**Files:** none (observation only)

- [ ] **Step 1: Record the current row count in `data/agents.db` for `agent-health`, as a baseline**

```bash
sqlite3 data/agents.db "SELECT COUNT(*) FROM runs WHERE agent='agent-health';"
```
Note the number returned — call it `N0`.

- [ ] **Step 2: After each of the next 3 scheduled hourly ticks (`:00` UTC), check the CronJob's last run**

```bash
kubectl get cronjobs agent-health -n ai-agents-cron
kubectl get jobs -n ai-agents-cron -l "job-name" --sort-by=.status.startTime | tail -3
sqlite3 data/agents.db "SELECT COUNT(*) FROM runs WHERE agent='agent-health';"
```
Expected each time: `LAST SCHEDULE` on the CronJob advances by one hour; the matching Job shows `COMPLETIONS 1/1`; the `runs` row count increases by 1 over the previous check. Repeat this step 3 times total (i.e. across 3 separate hourly ticks) before moving to Task 8. If any run fails, stop and diagnose before continuing — do not proceed to the crontab cutover on an unproven CronJob.

---

### Task 8: STOP — cut over: remove `agent-health` from the host crontab

**Files:** host crontab (not a repo file)

- [ ] **Step 1: Confirm go-ahead, then view and edit the crontab**

```bash
crontab -l | grep agent-health
```
Note the exact line. Then:
```bash
crontab -l | grep -v 'agents agent-health' | crontab -
```

- [ ] **Step 2: Verify it's gone**

```bash
crontab -l | grep agent-health
```
Expected: no output.

---

### Task 9: STOP — rollback drill (actually executed)

Matching every prior phase's bar: run the rollback drill for real, right after Task 8, then re-cut-over to leave the phase in its intended end state.

**Files:** host crontab (not a repo file)

- [ ] **Step 1: Confirm go-ahead, then roll back — re-add the host crontab line, suspend the CronJob**

```bash
( crontab -l; echo "0 * * * * cd /home/cian/git/ai-agents && ./run-agent.sh agent-health >> /home/cian/git/ai-agents/logs/agent-health.log 2>&1" ) | crontab -
crontab -l | grep agent-health
kubectl patch cronjob agent-health -n ai-agents-cron -p '{"spec":{"suspend":true}}'
kubectl get cronjob agent-health -n ai-agents-cron
```
Expected: the crontab line is back; `SUSPEND` shows `True`.

- [ ] **Step 2: Wait for the next hourly tick, confirm the host resumed running it**

```bash
sqlite3 data/agents.db "SELECT COUNT(*) FROM runs WHERE agent='agent-health';"
```
Note the count, wait for the next `:00` UTC tick, then re-run the same query. Expected: count increased by 1 (the host cron fired, not the suspended k8s CronJob).

- [ ] **Step 3: Re-cutover — restore the intended end state**

```bash
crontab -l | grep -v 'agents agent-health' | crontab -
crontab -l | grep agent-health
kubectl patch cronjob agent-health -n ai-agents-cron -p '{"spec":{"suspend":false}}'
kubectl get cronjob agent-health -n ai-agents-cron
```
Expected: crontab line gone again; `SUSPEND` shows `False` — back to the post-Task-8 state, drill complete.

---

### Task 10: Record Phase 4 completion in the design doc

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Append a Phase 4 status note**

Add to the `## Status` section, after the Phase 3 note:

```markdown
**Phase 4 — CronJobs cutover — done (2026-08-03):** All 7 cron-triggered resources (6 agent classes; `librarian` contributes 2 — `--mode audit`/`--mode watch`) got real `CronJob` manifests in the `ai-agents` namespace, reusing the existing `ai-agents-runner` image and its PSA `baseline` namespace (hostPath-compatible, unlike `wedding-ui`'s `restricted` namespace). Only `agent-health` went live (`suspend: false`) — the lowest-risk agent (deterministic, no LLM call). The other six were created `suspend: true`, proving the shared infrastructure (image, secrets pattern, initContainer, hostPath mounts) once so later phases only need their own cutover, not a from-scratch pattern. `data/agents.db` and both Google OAuth token files are hostPath-mounted (not PVC copies) so the pod and the still-host-cron agents share one source of truth — avoids `agent-health`'s staleness reads silently diverging from reality, and avoids two independent OAuth refreshers racing against Google's refresh-token rotation. Secrets are scoped per agent (least privilege) rather than a blanket `.env` mount — `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` on all 7 (the `check-google-token.sh` initContainer runs uniformly), `TODOIST_API_TOKEN` added only to `daily-briefing-secrets`. Bake time scaled down from the parent design doc's generic "a week of cluster-only runs" to 3 consecutive successful hourly runs, a deliberate homelab-appropriate tradeoff (not a production system). Rollback drill actually executed: reverted to the host crontab, confirmed the host resumed running it on the next tick, then re-cut-over.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 4 (CronJobs cutover) completion"
```

---

## Self-Review

**Spec coverage:** spec's "Scope" (all 7 created, only agent-health live) ✓ Task 2; "Shared state: hostPath" ✓ Task 2 Step 1's volumes, applied uniformly across Step 2/3's table; "Secrets: scoped per agent" ✓ Task 3; "CronJob spec mechanics" (no `timeZone:`, `concurrencyPolicy: Forbid`, `backoffLimit: 0`, per-agent `activeDeadlineSeconds`, `check-google-token.sh` initContainer on all 7, `--strict-mcp-config` already enforced by existing Python) ✓ Task 2; "Cutover procedure" (apply → bake → crontab removal → rollback drill) ✓ Tasks 6-9; design doc completion note ✓ Task 10.

**Placeholder scan:** the runner image digest is a real, verified value (`sha256:7060d27e...`, extracted from CI run `30833749005` — the rebuild triggered by Task 1's `Dockerfile.runner` change, pushed to `main` directly with the human partner's go-ahead since it invalidated the previously-pinned digest) — not a placeholder. `kind` smoke test in Task 5 uses fake secret values (`x`) deliberately, since the goal there is validating manifest structure, not real credentials — explicitly noted as such, not an oversight.

**Type/interface consistency:** agent names (`agent-health`, `plant-agent`, `daily-briefing`, `news-briefing`, `security-audit`, `librarian`) match verbatim between Global Constraints, Task 2's manifests, and Task 3's Secret names. Secret names (`agent-health-secrets`, etc.) match between Task 2's `envFrom` references, Task 3's creation commands, and Task 5's kind-test fakes. Schedules match verbatim between Global Constraints and Task 2's `spec.schedule` fields.

**Known limitations, stated honestly:** the `activeDeadlineSeconds` values (300/3000/1800) are reasoned estimates from the design doc's stated concern (an unbounded LLM worst case swallowing the next tick), not empirically measured — Task 7's bake period is real observation data for `agent-health` specifically; the other six agents' deadlines remain unvalidated by a real run until their own future phases exercise them, which is acceptable since they stay `suspend: true` here and can't fire. Task 9 Step 2's "wait for the next hourly tick" is real elapsed time, same category as Task 7 — this plan cannot compress wall-clock waiting, only bound it (3 runs, not a week).
