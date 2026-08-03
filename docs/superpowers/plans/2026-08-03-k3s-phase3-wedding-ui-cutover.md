# Phase 3 — wedding-ui Full Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan pushes to `origin/main` for the first time in the whole migration, touches live production Traefik routing (outside this repo, in `~/git/yopflix/seedbox`), and does a real ~1 minute cutover window on a workload real users hit.** Tasks 3-5 and 8-10 are marked STOP — do not execute them, or resume past them, without the human partner's explicit go-ahead in this session, given separately for each STOP (not one blanket yes covering all of them). No subagent may be dispatched for any STOP task.

**Goal:** Move `wedding-ui` from its current Docker container onto k3s permanently — real PVC-backed storage, real private-image pull, real Traefik cutover — proven with a write (not just a read) before the live switch, and a rollback path proven to actually work, not just documented.

**Architecture:** wedding-ui gets its own Kubernetes namespace, `wedding-ui`, separate from Phase 2's `ai-agents` namespace. This resolves an ambiguity in the design doc's Pod security section ("PSA `restricted` for `wedding-ui`; `baseline` where hostPath forces it") — Pod Security Admission is enforced per-*namespace*, not per-workload, so a workload that needs the stricter `restricted` policy needs a namespace of its own rather than sharing `ai-agents` (which stays `baseline` because Phase 4's cron-agent pods need hostPath, which `restricted` forbids). wedding-ui needs no hostPath at all (PVC-backed), so it can be fully `restricted`. Traefik (still running in Docker, unchanged) gets a new static route via its existing `file` provider, pointing at a k8s `NodePort` Service bound to the Tailscale IP (already enforced cluster-wide since Phase 2's `--kube-proxy-arg=nodeport-addresses`) — this makes the cutover a one-file change that Traefik hot-reloads, and an equally fast revert.

**Tech Stack:** Kubernetes Deployment/Service/PVC/Namespace, k3s's default `local-path` StorageClass, GitHub Actions (first real run of Phase 1's `ci.yml`), GHCR private image + `imagePullSecret`, Traefik's `file` provider (separate repo: `~/git/yopflix/seedbox`), `gh` CLI (installed this task, official apt method, to check Actions run status without a manual UI round-trip).

## Global Constraints

- **Correction to the design doc:** the design doc's Phase 3 bullet says "chown the seeded DB to 1001" — this is stale. Phase 1's actual `wedding_ui/Dockerfile` creates the app user as `useradd --uid 1000 ...` (uid **1000**, not 1001 — 1001 is `ai-agents-runner`'s uid, a different image). Task 1 fixes this in the design doc; every other task in this plan uses 1000.
- k3s's default StorageClass is `local-path` (`rancher.io/local-path`, `WaitForFirstConsumer`, **`ReclaimPolicy: Delete`** — deleting the PVC deletes the underlying data. Never delete the wedding-ui PVC without a backup of `/app/data/wedding.db` first).
- Images are private on GHCR (Phase 1 decision) — every pod that pulls one needs `imagePullSecrets` referencing a `kubernetes.io/dockerconfigjson` Secret built from a GHCR PAT scoped to `read:packages`. Referenced by `@sha256:` digest, never `:latest` (Architecture section, Phase 1 plan's own Global Constraints).
- No image has ever been pushed to GHCR in this migration — every phase so far is local commits only. Task 3 is the **first-ever push to `origin`** in this entire project's k3s work.
- Traefik config lives in a **separate repo**, `~/git/yopflix/seedbox` (not `ai-agents`) — its `traefik.yaml` has both a `docker` provider (current wedding-ui route, via compose-generated labels) and a `file` provider (`directory: /etc/traefik/custom`, `watch: true`, backed on disk by `~/git/yopflix/seedbox/traefik/custom/`, which already holds `middlewares.yaml`). The existing basic-auth middleware to reuse is `common-auth@file` (defined in that file as `basicAuth.usersFile: /etc/traefik/http_auth`) — the current `wedding.yopflix.world` route uses this via `httpAuth: true` in `config.yaml`, generated into `http.routers.wedding-1.middlewares.0: common-auth@file` by `run-seedbox.sh`.
- Real user-facing risk: `wedding.yopflix.world` is a live route behind Traefik that also fronts 21 other services on the same box. A bad `file`-provider YAML doesn't just break wedding-ui — Traefik parses its whole dynamic config as one unit per provider, so a syntax error in the new file can affect other file-provider-sourced config (the `common-auth` middleware itself lives in a file-provider file).
- `wedding_ui`'s real write-test payload (Global Constraints for Task 7): `PATCH /api/config` with `{"guests": 100}` — verified against `tests/test_wedding_ui_api.py:65-66` (`after["config"]["guests"] == 100`, `after["tables"] == 10`). Must be reset back afterward so a test value doesn't leak into what becomes production data after cutover.
- No tool installed beyond what an official install method provides (Phase 2's own convention) — `gh` CLI is added in Task 3 via GitHub's official apt repository, not a manual binary download.

---

### Task 1: Fix the design doc's stale uid reference

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Correct the Phase 3 bullet**

Find the line starting `- **3 — wedding-ui full cutover.**` and change `chown the seeded DB to 1001` to `chown the seeded DB to 1000` (matches the actual `wedding_ui/Dockerfile` user, uid 1000, not `ai-agents-runner`'s uid 1001 — these are two different images with two different users).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "fix(docs): correct wedding-ui chown target to uid 1000, not 1001

Phase 1's wedding_ui/Dockerfile creates the app user at uid 1000
(useradd --uid 1000 ... app). 1001 is ai-agents-runner's uid, a
different image — the design doc conflated the two."
```

---

### Task 2: wedding-ui Kubernetes manifests

**Files:**
- Create: `k8s/base/wedding-ui/namespace.yaml`
- Create: `k8s/base/wedding-ui/pvc.yaml`
- Create: `k8s/base/wedding-ui/deployment.yaml`
- Create: `k8s/base/wedding-ui/service.yaml`
- Create: `k8s/base/wedding-ui/kustomization.yaml`
- Modify: `k8s/base/kustomization.yaml`

**Interfaces:**
- Consumes: `wedding_ui/Dockerfile` (Phase 1, uid 1000, `/healthz` on port 8000, `VOLUME ["/app/data"]`).
- Produces: a `wedding-ui` Deployment/Service/PVC that later tasks apply to the real cluster once a real image digest exists (Task 3).

- [ ] **Step 1: Namespace, `restricted` PSA**

```yaml
# k8s/base/wedding-ui/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: wedding-ui
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
```

- [ ] **Step 2: PVC**

```yaml
# k8s/base/wedding-ui/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: wedding-data
  namespace: wedding-ui
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
```

- [ ] **Step 3: Deployment**

Image is a placeholder digest here (`sha256:0000...`, 64 zeros) — Task 6 replaces it with the real digest from Task 3 before ever applying this to the real cluster. `restricted` PSA requires `runAsNonRoot: true`, no added capabilities, `allowPrivilegeEscalation: false`, and a `seccompProfile`.

```yaml
# k8s/base/wedding-ui/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wedding-ui
  namespace: wedding-ui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: wedding-ui
  template:
    metadata:
      labels:
        app: wedding-ui
    spec:
      imagePullSecrets:
        - name: ghcr-pull-secret
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: wedding-ui
          image: ghcr.io/hughseyxo/ai-agents-wedding-ui@sha256:0000000000000000000000000000000000000000000000000000000000000
          ports:
            - containerPort: 8000
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: data
              mountPath: /app/data
            - name: tmp
              mountPath: /tmp
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 2
            periodSeconds: 5
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: wedding-data
        - name: tmp
          emptyDir: {}
```

- [ ] **Step 4: Service — ClusterIP for in-cluster use, NodePort for Traefik**

Fixed NodePort `30800` (inside k3s's default 30000-32767 range, unclaimed — this is the first workload). Bound to the Tailscale IP only, cluster-wide, via Phase 2's `--kube-proxy-arg=nodeport-addresses=<TAILSCALE_IP>/32` — no host-specific value needs to appear in this manifest.

```yaml
# k8s/base/wedding-ui/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: wedding-ui
  namespace: wedding-ui
spec:
  type: NodePort
  selector:
    app: wedding-ui
  ports:
    - port: 8000
      targetPort: 8000
      nodePort: 30800
```

- [ ] **Step 5: Sub-kustomization and wiring into `k8s/base`**

```yaml
# k8s/base/wedding-ui/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml
```

Modify `k8s/base/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - wedding-ui
```

- [ ] **Step 6: Validate with kubeconform**

```bash
cd /home/cian/git/ai-agents
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest -ignore-missing-schemas -summary /k8s/base
```
Expected: `Resources: 6 found` (namespace, networkpolicy, + wedding-ui's namespace/pvc/deployment/service), `Errors: 0`.

- [ ] **Step 7: `kind` smoke test — manifest and PSA validity only (no real image pull)**

```bash
export PATH="$HOME/bin:$PATH"
kind create cluster --name k3s-phase3-smoke
kubectl apply -k k8s/base
kubectl get ns wedding-ui -o jsonpath='{.metadata.labels}'
echo
kubectl get pvc -n wedding-ui wedding-data
kubectl describe deployment -n wedding-ui wedding-ui | grep -A3 "Pod Template.*Security"
kind delete cluster --name k3s-phase3-smoke
```
Expected: the namespace labels contain `"pod-security.kubernetes.io/enforce":"restricted"`; the PVC shows `STATUS Pending` (expected — `WaitForFirstConsumer` binding mode means it won't bind until a pod is scheduled, and the placeholder-digest image will never actually pull successfully in `kind`, so the pod itself will sit in `ErrImagePull`/`Pending` — that's fine, this step only proves the manifests are `restricted`-PSA-valid and kustomize-valid, not that the real image runs). No PSA admission rejection errors in the `kubectl apply` output — if there are any, fix the securityContext before proceeding.

- [ ] **Step 8: Commit**

```bash
git add k8s/base/wedding-ui k8s/base/kustomization.yaml
git commit -m "feat(k8s): add wedding-ui manifests (own namespace, restricted PSA)"
```

---

### Task 3: STOP — first-ever push to `origin`, verify the image builds

**Do not run this task without the human partner's explicit go-ahead.** This is the first push to `origin` in the whole k3s migration — 20+ local commits across three phases go public at once, and it triggers real GitHub Actions billing/compute and a real GHCR package creation.

**Files:** none (this pushes what's already committed)

- [ ] **Step 1: Confirm go-ahead, then install `gh` CLI (official method)**

```bash
(type -p wget >/dev/null || (sudo apt update && sudo apt-get install wget -y)) \
  && sudo mkdir -p -m 755 /etc/apt/keyrings \
  && wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
  && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && sudo apt update \
  && sudo apt install gh -y
gh --version
```
Expected: prints a `gh version X.Y.Z` line.

- [ ] **Step 2: Authenticate `gh` (interactive, human types the code)**

```bash
gh auth login --hostname github.com --git-protocol https --web
```
This prints a one-time code and a URL — the human partner opens the URL and enters the code. Wait for `gh auth status` to confirm before continuing.

- [ ] **Step 3: Push**

```bash
cd /home/cian/git/ai-agents
git push origin main
```
Expected: a normal push summary — `main -> main`, no rejection.

- [ ] **Step 4: Watch the Actions run to completion**

```bash
sleep 10
gh run list --repo hughseyxo/ai-agents --branch main --limit 1
gh run watch --repo hughseyxo/ai-agents $(gh run list --repo hughseyxo/ai-agents --branch main --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```
Expected: `gh run watch` polls until the run finishes and exits `0`. If it exits non-zero, **stop** — do not proceed to Task 4 with a broken image. Read the failed job's log (`gh run view --repo hughseyxo/ai-agents --log-failed`) and fix the underlying issue first (this may mean returning to Phase 1's files, not this plan).

- [ ] **Step 5: Extract the real image digest**

```bash
gh run view --repo hughseyxo/ai-agents --log $(gh run list --repo hughseyxo/ai-agents --branch main --limit 1 --json databaseId --jq '.[0].databaseId') | grep -A2 "wedding-ui.*Build (and push on main only)" | grep -i "digest:"
```
Expected: a line containing `sha256:` followed by 64 hex characters — this is the real digest. Record it; Task 6 needs it verbatim.

---

### Task 4: STOP — flip GHCR package visibility to private

**Manual, human-only step — cannot be scripted from `GITHUB_TOKEN` alone** (same category as the Drive OAuth setup already documented in `CLAUDE.md`). Do not proceed until the human partner confirms this is done.

**Files:** none

- [ ] **Step 1: Ask the human partner to do this in the GitHub UI**

Go to `github.com/users/hughseyxo/packages/container/ai-agents-wedding-ui` → Package settings → Danger Zone → Change visibility → Private.

- [ ] **Step 2: Confirm**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://ghcr.io/v2/hughseyxo/ai-agents-wedding-ui/tags/list
```
Expected: `401` (unauthorized) — a private package refuses anonymous access. If it returns `200`, visibility hasn't actually changed yet; wait and re-check before proceeding.

---

### Task 5: STOP — create the `imagePullSecret`

**Requires a human-generated GitHub Personal Access Token — cannot be scripted.**

**Files:** none (cluster state only)

- [ ] **Step 1: Ask the human partner to generate a PAT**

`github.com/settings/tokens/new` (classic) or a fine-grained token — scope: `read:packages` only. Nothing else. This token lives only in the shell command below, never in a tracked file.

- [ ] **Step 2: Create the Secret in the `wedding-ui` namespace**

```bash
read -rsp "GHCR PAT (read:packages): " GHCR_PAT
echo
sudo k3s kubectl create secret docker-registry ghcr-pull-secret \
  --namespace=wedding-ui \
  --docker-server=ghcr.io \
  --docker-username=hughseyxo \
  --docker-password="$GHCR_PAT"
unset GHCR_PAT
```
Expected: `secret/ghcr-pull-secret created`.

- [ ] **Step 3: Verify (without printing the secret)**

```bash
sudo k3s kubectl get secret -n wedding-ui ghcr-pull-secret
```
Expected: shows the secret, `TYPE kubernetes.io/dockerconfigjson`.

---

### Task 6: Apply the real manifests, verify the pod pulls and becomes Ready

**Files:**
- Modify: `k8s/base/wedding-ui/deployment.yaml` (replace the placeholder digest)

- [ ] **Step 1: Replace the placeholder digest with Task 3's real one**

In `k8s/base/wedding-ui/deployment.yaml`, change:
```yaml
image: ghcr.io/hughseyxo/ai-agents-wedding-ui@sha256:0000000000000000000000000000000000000000000000000000000000000
```
to the real digest recorded in Task 3 Step 5, e.g.:
```yaml
image: ghcr.io/hughseyxo/ai-agents-wedding-ui@sha256:<real digest from Task 3>
```

- [ ] **Step 2: Commit**

```bash
cd /home/cian/git/ai-agents
git add k8s/base/wedding-ui/deployment.yaml
git commit -m "feat(k8s): pin wedding-ui to its first real GHCR digest"
```

- [ ] **Step 3: Apply to the real cluster**

```bash
sudo k3s kubectl apply -k k8s/base
sudo k3s kubectl wait --for=condition=Available deployment/wedding-ui -n wedding-ui --timeout=120s
sudo k3s kubectl get pods -n wedding-ui -o wide
```
Expected: `deployment.apps/wedding-ui condition met`; the pod shows `STATUS Running`, `READY 1/1`.

- [ ] **Step 4: Verify `/healthz` through the NodePort**

```bash
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -sf "http://${TS_IP}:30800/healthz"
```
Expected: `{"status":"ok"}`.

---

### Task 7: Real write-test (dry run, not yet the live cutover)

This pod's PVC is empty/freshly-seeded (defaults from `BudgetStore`, per `wedding_ui/budget_model.py`'s `DEFAULT_ITEMS`) — **not** the real production data yet. That copy-in happens in Task 9, as part of the actual cutover window. This task only proves the k8s-hosted pod can genuinely persist a write, isolated from the still-live Docker container.

**Files:** none (live verification only)

- [ ] **Step 1: Confirm the k8s pod's data starts at defaults**

```bash
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -s "http://${TS_IP}:30800/api/budget" | python3 -c "import sys,json; print(json.load(sys.stdin)['config']['guests'])"
```
Expected: `350` (the seeded default per `wedding_ui/budget_model.py`'s `DEFAULT_ITEMS` — confirms this is a fresh PVC, not accidentally pointed at the Docker container's real data).

- [ ] **Step 2: Write, verify it persisted**

```bash
curl -s -X PATCH "http://${TS_IP}:30800/api/config" -H 'Content-Type: application/json' -d '{"guests": 100}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['config']['guests'], d['tables'])"
```
Expected: `100 10` (matches `tests/test_wedding_ui_api.py:65-67`'s assertions exactly).

- [ ] **Step 3: Verify the still-live Docker container is unaffected (proves isolation)**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u "$(grep '^HTTP_USER=' ~/git/yopflix/seedbox/.env | cut -d= -f2):$(grep '^HTTP_PASSWORD=' ~/git/yopflix/seedbox/.env | cut -d= -f2)" https://wedding.yopflix.world/api/budget
```
Expected: `200` — the real site is still being served entirely by the Docker container, untouched. (This confirms the k8s pod and the Docker container really are on separate storage — if they weren't, Task 7 Step 2's write would already be visible on the real site, which would be a serious bug to catch here, not during the live cutover.)

- [ ] **Step 4: Reset the test value**

```bash
curl -s -X POST "http://${TS_IP}:30800/api/reset" > /dev/null
curl -s "http://${TS_IP}:30800/api/budget" | python3 -c "import sys,json; print(json.load(sys.stdin)['config']['guests'])"
```
Expected: `350` again — clean slate before Task 9 overwrites this PVC's data with the real seed anyway.

---

### Task 8: STOP — copy real data into the PVC, chown to 1000

**This is the first half of the ~1 minute cutover window** — the design doc requires stopping the Docker container before copying, so the source data can't change mid-copy. Do not run without explicit go-ahead, given separately from Task 3-5's.

**Files:** none (host + cluster state only)

- [ ] **Step 1: Confirm go-ahead, then stop the Docker container**

```bash
docker stop wedding
```
Expected: `wedding` (container name echoed back).

- [ ] **Step 2: Find the PVC's real on-disk path (local-path-provisioner)**

```bash
PVC_PATH=$(sudo k3s kubectl get pv -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for pv in data['items']:
    claim = pv['spec'].get('claimRef', {})
    if claim.get('name') == 'wedding-data' and claim.get('namespace') == 'wedding-ui':
        print(pv['spec']['hostPath']['path'])
        break
")
echo "$PVC_PATH"
```
Expected: a path like `/var/lib/rancher/k3s/storage/pvc-<uuid>_wedding-ui_wedding-data`.

- [ ] **Step 3: Copy the real seed data in and chown**

```bash
sudo cp /data/config/wedding/wedding.db "${PVC_PATH}/wedding.db"
sudo chown 1000:1000 "${PVC_PATH}/wedding.db"
ls -l "${PVC_PATH}/wedding.db"
```
Expected: `-rw-r--r-- 1 1000 1000 ... wedding.db` (or similar — owner/group both `1000`, matching the container's `app` user, not `root`).

- [ ] **Step 4: Restart the pod so it picks up the copied-in file cleanly**

```bash
sudo k3s kubectl rollout restart deployment/wedding-ui -n wedding-ui
sudo k3s kubectl wait --for=condition=Available deployment/wedding-ui -n wedding-ui --timeout=60s
```

- [ ] **Step 5: Verify the pod now serves the real data**

```bash
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -s "http://${TS_IP}:30800/api/budget" | python3 -c "import sys,json; print(json.load(sys.stdin)['config']['guests'])"
```
Expected: whatever the real guest count currently is on `wedding.yopflix.world` (**not** `350` — if it's still `350`, the copy didn't take and the Docker container must not be restarted until this is fixed).

---

### Task 9: STOP — repoint Traefik (second half of the cutover window)

**Files (in a different repo, `~/git/yopflix/seedbox`, not `ai-agents`):**
- Create: `~/git/yopflix/seedbox/traefik/custom/wedding-k8s.yaml`

- [ ] **Step 1: Confirm go-ahead, then write the file-provider route**

```yaml
# ~/git/yopflix/seedbox/traefik/custom/wedding-k8s.yaml
# Replaces the Docker-provider route for wedding-ui (services/wedding.yaml)
# with a route to the k8s NodePort Service — added 2026-08-03 as part of
# the ai-agents k3s migration, Phase 3. To roll back: delete this file,
# Traefik's file provider hot-reloads within its `watch: true` interval.
http:
  routers:
    wedding-k8s:
      rule: "Host(`wedding.{{ env \"TRAEFIK_DOMAIN\" }}`)"
      entryPoints:
        - secure
      middlewares:
        - common-auth@file
      service: wedding-k8s
  services:
    wedding-k8s:
      loadBalancer:
        servers:
          - url: "http://<TAILSCALE_IP>:30800"
```

Replace `<TAILSCALE_IP>` with the real value from `ai-agents/.env`'s `TAILSCALE_IP` before saving — this file lives in the `yopflix/seedbox` repo, which has its own conventions for host-specific values; check `~/git/yopflix/seedbox/CLAUDE.md` or equivalent for whether it also gitignores a real-value file versus committing literal IPs, and follow whatever that repo already does rather than assuming `ai-agents`' convention applies there too.

- [ ] **Step 2: Verify Traefik picked it up (no restart needed — `watch: true`)**

```bash
sleep 3
docker logs traefik --tail 20 | grep -i "wedding-k8s\|error"
```
Expected: no `error` lines referencing this file; ideally a line showing the new router was loaded (exact log wording depends on Traefik's log level — absence of errors is the primary signal).

- [ ] **Step 3: Verify the live site now serves from k8s**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u "$(grep '^HTTP_USER=' ~/git/yopflix/seedbox/.env | cut -d= -f2):$(grep '^HTTP_PASSWORD=' ~/git/yopflix/seedbox/.env | cut -d= -f2)" https://wedding.yopflix.world/api/budget
```
Expected: `200`.

- [ ] **Step 4: Positive reachability check — same as every prior phase's checkpoint**

```bash
docker ps -q | wc -l
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "jellyfin.yopflix.world:443:${TS_IP}" https://jellyfin.yopflix.world/ --max-time 5
```
Expected: `22` and `302` — a Traefik reload for one route must never affect any other routed service. If either value differs, **stop immediately** and go to Task 10 (rollback).

---

### Task 10: STOP — rollback drill (actually executed)

Design doc Verification: "rollback drills actually executed before each cutover is called done, not just documented." This is the highest-stakes cutover so far (real user-facing traffic), so this drill matters more here than anywhere else in the migration. Run it once, deliberately, right after Task 9 succeeds — proving the fallback path works while everything is still fresh, rather than discovering it doesn't during a real incident later.

**Files:** none (drill only — ends with the k8s route back in place)

- [ ] **Step 1: Confirm go-ahead, then roll back to the Docker container**

```bash
rm ~/git/yopflix/seedbox/traefik/custom/wedding-k8s.yaml
sleep 3
docker start wedding
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' -u "$(grep '^HTTP_USER=' ~/git/yopflix/seedbox/.env | cut -d= -f2):$(grep '^HTTP_PASSWORD=' ~/git/yopflix/seedbox/.env | cut -d= -f2)" https://wedding.yopflix.world/api/budget
```
Expected: `200` — served by the Docker container again (which still has the real, untouched data — Task 8 only *copied* it into the PVC, never deleted the source).

- [ ] **Step 2: Re-cutover to leave the phase in its intended end state**

```bash
docker stop wedding
```
Rewrite `~/git/yopflix/seedbox/traefik/custom/wedding-k8s.yaml` with the exact same content as Task 9 Step 1.
```bash
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' -u "$(grep '^HTTP_USER=' ~/git/yopflix/seedbox/.env | cut -d= -f2):$(grep '^HTTP_PASSWORD=' ~/git/yopflix/seedbox/.env | cut -d= -f2)" https://wedding.yopflix.world/api/budget
docker ps -q | wc -l
```
Expected: `200`, `22` — back to the post-Task-9 state, drill complete.

---

### Task 11: Record Phase 3 completion in the design doc

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Append a Phase 3 status note**

Add to the `## Status` section, after the Phase 2 note:

```markdown
**Phase 3 — wedding-ui full cutover — done (2026-08-03):** `wedding-ui` moved to its own `wedding-ui` namespace (PSA `restricted` — resolves the design doc's earlier ambiguity between "`restricted` for wedding-ui" and "`baseline` where hostPath forces it": those are two different namespaces, not one namespace enforcing two policies). PVC-backed on `local-path`, image pinned by digest, pulled via a PAT-backed `imagePullSecret` (GHCR flipped to private manually after the first-ever push to `origin` in this migration). Dry-run write test proved persistence and isolation from the still-live Docker container before any user-facing change. Traefik cutover via its existing `file` provider (`~/git/yopflix/seedbox/traefik/custom/wedding-k8s.yaml`) — reuses the existing `common-auth@file` basic-auth middleware, hot-reloads with no Traefik restart. Rollback drill actually executed: reverted to the Docker container, confirmed it still served the real (untouched) data, then re-cut-over.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 3 (wedding-ui cutover) completion"
```

---

## Self-Review

**Spec coverage:** design doc Phase 3 bullet — dry run alongside Docker container (Tasks 6-7) ✓; chown seeded DB (Task 8, corrected to 1000 per Task 1) ✓; verify a write not just a read (Task 7) ✓; `emptyDir` at `/tmp` for `readOnlyRootFilesystem` (Task 2 Deployment) ✓; ~1 min window stop→copy→repoint (Tasks 8-9) ✓; positive-reachability check immediately after (Task 9 Step 4) ✓. Architecture section — real PVC not hostPath (Task 2) ✓. Pod security section — `allowPrivilegeEscalation: false`, `capabilities: {drop: [ALL]}`, `seccompProfile: RuntimeDefault` (Task 2 Deployment) ✓; PSA `restricted` for wedding-ui specifically (Task 2, own namespace — the ambiguity this plan's Architecture section calls out and resolves) ✓. Verification section — "rollback drills actually executed... not just documented" (Task 10) ✓.

**Placeholder scan:** the Task 2 Deployment's `sha256:000...0` is a deliberate, explicitly-flagged placeholder (Task 6 replaces it with a real value before the manifest is ever applied to the real cluster) — not a plan-failure placeholder, since Step 1 of that task states exactly what replaces it and why it can't be known until Task 3 runs. Everything else has literal values.

**Type/interface consistency:** the NodePort `30800` is identical across Task 2's Service, Task 6 Step 4's curl, Task 7's curls, and Task 9's Traefik `loadBalancer.servers` URL. The namespace `wedding-ui` is identical across every task's `kubectl ... -n wedding-ui` invocations. The Secret name `ghcr-pull-secret` matches between Task 5's creation and Task 2's Deployment's `imagePullSecrets`.

**Known limitations, stated honestly:** Task 9 Step 1 cannot give the exact literal Traefik file contents in this plan, because it must contain the real `TAILSCALE_IP` value, and this repo's own convention (design decision 5) is to never commit that literal value — the step says to substitute it at write-time instead, and explicitly flags checking the *other* repo's own convention for host-specific values rather than assuming this repo's `.gitignore` pattern applies there. Task 3's `gh run view --log | grep` digest-extraction command is a best-effort text scrape of Actions log output whose exact format this plan cannot fully guarantee without having seen a real run yet (none has happened) — if the grep pattern doesn't match, the fallback is reading the same digest directly from the Actions web UI's job summary, which always shows it regardless of log wording.
