# Phase 5 (Obsidian Cutover) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **No subagent may be dispatched for any task marked STOP** — those require a human partner's own, separately-given go-ahead, the same rule Phase 3/4's plans used.

**Goal:** Move CouchDB + `livesync-bridge` from Docker Compose (in the separate `yopflix/seedbox` repo) into a single k3s Pod, replacing the Docker containers entirely (not a dual-run).

**Architecture:** One `Deployment`, one Pod, two containers (`couchdb`, `livesync-bridge`) sharing the pod's network namespace — `livesync-bridge` reaches CouchDB via `localhost:5984`, no `ClusterIP` `Service` needed. A new `obsidian-vault` namespace, PSA `privileged` (required by CouchDB's `hostPort: 5984` binding regardless of storage choice — see Global Constraints). CouchDB's data is PVC-backed with a one-time copy-in from the Docker named volume; `livesync-bridge`'s 3 peer paths stay hostPath (actively edited by host-side Claude Code sessions, not a one-time snapshot).

**Tech Stack:** k3s (`Deployment`, `PVC`, `Secret`, `ConfigMap`), `couchdb:3` (public image), `livesync-bridge` (locally-built Deno image, imported into k3s's containerd — no registry), `kubeconform`, `kustomize`.

## Global Constraints

- Namespace `obsidian-vault`, PSA `privileged` — required because CouchDB's `hostPort: 5984` binding (needed to keep serving Obsidian clients at the exact `<TAILSCALE_IP>:5984` address they already use) is restricted starting at PSA `baseline`, same tier as `hostPath` volumes. Confirmed already-decided in the parent design doc (decision 11): `service-node-port-range` deliberately stays default, so `hostPort`, not `NodePort`, is the mechanism.
- Both containers live in **one Pod** (not separate Deployments) — `livesync-bridge` connects to CouchDB via `http://localhost:5984`, not a Service DNS name.
- CouchDB image: `couchdb:3` (public, no `imagePullSecret` needed).
- `livesync-bridge` image: no registry. Built locally from `~/git/yopflix/seedbox/services/livesync-bridge` (`docker build`), then imported directly into k3s's containerd via `k3s ctr images import` — avoids standing up a cross-repo CI build for a single-node cluster.
- hostPath sources for `livesync-bridge` (identical to the current Docker Compose config, verified against `~/git/yopflix/seedbox/services/livesync-bridge.yaml`): `/home/cian/git/ai-agents/docs` → `/data/docs`; `/home/cian/.claude/projects/-home-cian-git-ai-agents/memory` → `/data/memory`; `/home/cian/git/ai-agents/CLAUDE.md` → `/data/project/CLAUDE.md` (single file — confirmed safe against the Phase 4 EBUSY hazard, since `livesync-bridge`'s write path in `PeerStorage.ts`'s `put()` does `Deno.open`+`write`+`truncate` directly on the target, not tempfile-then-rename).
- `livesync-bridge`'s `config.json` peer definitions (`baseDir`): `docs` group → `""` (root); `memory` group → `"_memory/"`; `project` group → `"_project/"`. Each group has a matching `couchdb-<group>` peer (database `obsidian-vault`) and `fs-<group>` peer (matching `baseDir` above under `/data/...`).
- `config.json`'s `${VAR}` substitutions read from the container's environment: `COUCHDB_USER`, `COUCHDB_PASSWORD`, `PASSPHRASE` (exact names — verified from the real `config.json`, not the `.env.custom` source file's `LIVESYNC-BRIDGE_`-prefixed key names, which are only the *source* naming convention stripped by Compose's env_file generation).
- The k8s version of `config.json` changes exactly one thing from the current file: every `"url": "http://couchdb:5984"` becomes `"url": "http://localhost:5984"` (no separate `couchdb` hostname exists without a Service object, and both containers share one network namespace).
- CouchDB's own admin credentials are supplied via its own env vars on the `couchdb` container (`COUCHDB_USER`/`COUCHDB_PASSWORD`, CouchDB's own convention) — not shared with `livesync-bridge`'s Secret, even though the values happen to be identical, since neither container needs the other's full credential set as a matter of least-privilege scoping.
- CouchDB config directory (`~/git/yopflix/seedbox/services/couchdb/`, contains `docker.ini`/`local.ini` — gitignored, holds the persisted admin credential hash) gets copied into the PVC alongside the data directory at cutover time, so CouchDB doesn't need to re-derive the admin hash from scratch.
- Baseline positive-reachability count: `docker ps -q | wc -l` = `22` as of plan-writing time.

---

### Task 1: Build and import the `livesync-bridge` image

**Files:** none (local Docker + k3s containerd state only)

- [ ] **Step 1: Build the image locally**

```bash
docker build -t livesync-bridge:phase5 ~/git/yopflix/seedbox/services/livesync-bridge
```
Expected: build completes, `docker images livesync-bridge:phase5` shows the tag.

- [ ] **Step 2: Import into k3s's containerd**

```bash
docker save livesync-bridge:phase5 | sudo k3s ctr images import -
sudo k3s ctr images list | grep livesync-bridge
```
Expected: the image appears in k3s's own image list (`docker.io/library/livesync-bridge:phase5` or similar), confirming it's usable by a Pod spec without any registry.

---

### Task 2: Write the k8s manifests

**Files:**
- Create: `k8s/base/obsidian-vault/namespace.yaml`
- Create: `k8s/base/obsidian-vault/pvc.yaml`
- Create: `k8s/base/obsidian-vault/configmap.yaml`
- Create: `k8s/base/obsidian-vault/deployment.yaml`
- Create: `k8s/base/obsidian-vault/kustomization.yaml`
- Modify: `k8s/base/kustomization.yaml`

**Interfaces:**
- Consumes: the `obsidian-vault-secrets` Secret (created in Task 4) by name; the `livesync-bridge:phase5` image (Task 1); the hostPath sources from Global Constraints.
- Produces: one `Deployment` in the `obsidian-vault` namespace, validated by `kubeconform` in Task 3.

- [ ] **Step 1: Namespace**

```yaml
# k8s/base/obsidian-vault/namespace.yaml
# PSA privileged is required here, not a default left too loose: CouchDB's
# hostPort: 5984 binding (needed to keep serving Obsidian clients at the
# same <TAILSCALE_IP>:5984 address they already use) is forbidden under
# both `baseline` and `restricted` — only `privileged` allows it. See the
# Phase 5 cutover design doc for the full reasoning (this mirrors the
# ai-agents-cron namespace's hostPath rationale from Phase 4, but the
# trigger here is hostPort, not hostPath).
apiVersion: v1
kind: Namespace
metadata:
  name: obsidian-vault
  labels:
    pod-security.kubernetes.io/enforce: privileged
    pod-security.kubernetes.io/warn: privileged
```

- [ ] **Step 2: PVC for CouchDB's data**

```yaml
# k8s/base/obsidian-vault/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: couchdb-data
  namespace: obsidian-vault
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 2Gi
```

- [ ] **Step 3: ConfigMap for `livesync-bridge`'s `config.json`**

```yaml
# k8s/base/obsidian-vault/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: livesync-bridge-config
  namespace: obsidian-vault
data:
  config.json: |
    {
      "peers": [
        {
          "type": "couchdb",
          "name": "couchdb-docs",
          "group": "docs",
          "database": "obsidian-vault",
          "username": "${COUCHDB_USER}",
          "password": "${COUCHDB_PASSWORD}",
          "url": "http://localhost:5984",
          "passphrase": "${PASSPHRASE}",
          "obfuscatePassphrase": "",
          "baseDir": "",
          "useRemoteTweaks": true
        },
        {
          "type": "storage",
          "name": "fs-docs",
          "group": "docs",
          "baseDir": "/data/docs",
          "scanOfflineChanges": true,
          "useChokidar": true
        },
        {
          "type": "couchdb",
          "name": "couchdb-memory",
          "group": "memory",
          "database": "obsidian-vault",
          "username": "${COUCHDB_USER}",
          "password": "${COUCHDB_PASSWORD}",
          "url": "http://localhost:5984",
          "passphrase": "${PASSPHRASE}",
          "obfuscatePassphrase": "",
          "baseDir": "_memory/",
          "useRemoteTweaks": true
        },
        {
          "type": "storage",
          "name": "fs-memory",
          "group": "memory",
          "baseDir": "/data/memory",
          "scanOfflineChanges": true,
          "useChokidar": true
        },
        {
          "type": "couchdb",
          "name": "couchdb-project",
          "group": "project",
          "database": "obsidian-vault",
          "username": "${COUCHDB_USER}",
          "password": "${COUCHDB_PASSWORD}",
          "url": "http://localhost:5984",
          "passphrase": "${PASSPHRASE}",
          "obfuscatePassphrase": "",
          "baseDir": "_project/",
          "useRemoteTweaks": true
        },
        {
          "type": "storage",
          "name": "fs-project",
          "group": "project",
          "baseDir": "/data/project",
          "scanOfflineChanges": true,
          "useChokidar": true
        }
      ]
    }
```
This is identical to the real `~/git/yopflix/seedbox/services/livesync-bridge/config.json` except every `"url": "http://couchdb:5984"` is `"http://localhost:5984"` (Global Constraints).

- [ ] **Step 4: Deployment — both containers, one Pod**

```yaml
# k8s/base/obsidian-vault/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: obsidian-vault
  namespace: obsidian-vault
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: obsidian-vault
  template:
    metadata:
      labels:
        app: obsidian-vault
    spec:
      containers:
        - name: couchdb
          image: couchdb:3
          ports:
            - containerPort: 5984
              hostPort: 5984
              hostIP: "<TAILSCALE_IP>"
          envFrom:
            - secretRef:
                name: obsidian-vault-secrets
          volumeMounts:
            - name: couchdb-data
              mountPath: /opt/couchdb/data
            - name: couchdb-config
              mountPath: /opt/couchdb/etc/local.d
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 1000m
              memory: 1Gi
        - name: livesync-bridge
          image: livesync-bridge:phase5
          imagePullPolicy: Never
          env:
            - name: LSB_CONFIG
              value: /config/config.json
          envFrom:
            - secretRef:
                name: obsidian-vault-secrets
          volumeMounts:
            - name: livesync-bridge-config
              mountPath: /config
            - name: docs
              mountPath: /data/docs
            - name: memory
              mountPath: /data/memory
            - name: project
              mountPath: /data/project
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: couchdb-data
          persistentVolumeClaim:
            claimName: couchdb-data
        - name: couchdb-config
          hostPath:
            path: /home/cian/git/yopflix/seedbox/services/couchdb
            type: Directory
        - name: livesync-bridge-config
          configMap:
            name: livesync-bridge-config
        - name: docs
          hostPath:
            path: /home/cian/git/ai-agents/docs
            type: Directory
        - name: memory
          hostPath:
            path: /home/cian/.claude/projects/-home-cian-git-ai-agents/memory
            type: Directory
        - name: project
          hostPath:
            path: /home/cian/git/ai-agents
            type: Directory
```
Replace `<TAILSCALE_IP>` with the real value from `ai-agents/.env`'s `TAILSCALE_IP` at write-time (repo convention — never commit the literal value).

**Note on the `project` mount:** `CLAUDE.md` itself is a single file, but `livesync-bridge`'s `fs-project` peer only needs read/write on that one file — however Kubernetes' `hostPath: type: File` mount targets a single path inside the container matching a single path on the host 1:1, and the container needs `/data/project/CLAUDE.md` specifically (per the ConfigMap's `baseDir: "/data/project"` + the file itself). Mount the **parent directory** (`/home/cian/git/ai-agents`, `type: Directory`) at `/data/project` rather than a single-file mount at `/data/project/CLAUDE.md` — this exposes more of the host's `ai-agents` repo into the container than strictly necessary (unlike the Docker Compose version's precise single-file bind), but `livesync-bridge`'s `fs-project` peer only ever touches the one file it's configured to sync (`baseDir` scopes it), and this sidesteps needing a differently-named single-file target inside a directory that doesn't otherwise exist as its own mount point in the container's view. If tighter scoping matters later, revisit with a purpose-built host directory containing only `CLAUDE.md`.

- [ ] **Step 5: `kustomization.yaml`**

```yaml
# k8s/base/obsidian-vault/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - pvc.yaml
  - configmap.yaml
  - deployment.yaml
```

- [ ] **Step 6: Wire into `k8s/base/kustomization.yaml`**

```yaml
# before:
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - wedding-ui
  - cronjobs
# after:
resources:
  - namespace.yaml
  - networkpolicy.yaml
  - wedding-ui
  - cronjobs
  - obsidian-vault
```

- [ ] **Step 7: Commit**

```bash
git add k8s/base/obsidian-vault/ k8s/base/kustomization.yaml
git commit -m "feat(k8s): add Obsidian vault manifests (CouchDB + livesync-bridge, one Pod)"
```

---

### Task 3: Validate manifests (kubeconform + kind)

**Files:** none (validation only)

- [ ] **Step 1: `kubeconform`**

```bash
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest \
  -ignore-missing-schemas -summary /k8s/base
```
Expected: 0 errors; resource count includes the new namespace, PVC, ConfigMap, Deployment.

- [ ] **Step 2: `kind` smoke test**

```bash
kind create cluster --name phase5-smoke 2>&1 | tail -5
kubectl --context kind-phase5-smoke apply -f k8s/base/obsidian-vault/namespace.yaml
kubectl --context kind-phase5-smoke create secret generic obsidian-vault-secrets -n obsidian-vault \
  --from-literal=COUCHDB_USER=x --from-literal=COUCHDB_PASSWORD=x --from-literal=PASSPHRASE=x
kubectl --context kind-phase5-smoke apply -k k8s/base/obsidian-vault
kubectl --context kind-phase5-smoke get deployment,pvc -n obsidian-vault
```
Expected: no PSA violation warnings (the namespace's own `privileged` label is respected); the Deployment and PVC are created without error. The Pod itself won't reach `Running` (`kind`'s node has no `/home/cian/...` hostPath sources and no `livesync-bridge:phase5` image in its own containerd) — that's expected, matching every prior phase's `kind` smoke test scope (manifest acceptance, not full runtime).

- [ ] **Step 3: Tear down**

```bash
kind delete cluster --name phase5-smoke
```

---

### Task 4: STOP — create the real Secret

**Requires real credential values from `~/git/yopflix/seedbox/.env.custom` — cannot be scripted without the human partner's own input.**

**Files:** none (cluster state only)

- [ ] **Step 1: Confirm go-ahead, then create the Secret**

Ask the human partner to run this themselves (via the `!` prefix), so the real values never enter the assistant's context:

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl create namespace obsidian-vault --dry-run=client -o yaml | kubectl apply -f -
source ~/git/yopflix/seedbox/.env.custom
kubectl create secret generic obsidian-vault-secrets \
  --namespace=obsidian-vault \
  --from-literal=COUCHDB_USER="${LIVESYNC_BRIDGE_COUCHDB_USER}" \
  --from-literal=COUCHDB_PASSWORD="${LIVESYNC_BRIDGE_COUCHDB_PASSWORD}" \
  --from-literal=PASSPHRASE="${LIVESYNC_BRIDGE_PASSPHRASE}"
```
Note: `.env.custom` uses hyphenated key names (`LIVESYNC-BRIDGE_COUCHDB_USER`); bash/zsh can't reference hyphenated variable names directly, so check the exact sourced variable names with `env | grep -i couchdb` after `source`-ing and adjust the `--from-literal` lines to match if they differ from the underscored guess above.

Expected: `secret/obsidian-vault-secrets created`.

- [ ] **Step 2: Verify (without printing contents)**

```bash
kubectl get secret -n obsidian-vault obsidian-vault-secrets
```
Expected: `TYPE Opaque`, `DATA 3`.

---

### Task 5: STOP — dry run against a data copy

**This validates the pod works before touching the live Docker containers — no downtime risk yet.**

**Files:** none (host + cluster state only)

- [ ] **Step 1: Confirm go-ahead, then copy the data to a scratch location**

```bash
HOST_CONFIG_PATH=$(grep '^HOST_CONFIG_PATH=' ~/git/yopflix/seedbox/.env | cut -d= -f2)
sudo mkdir -p /tmp/couchdb-dryrun-data
sudo cp -a "${HOST_CONFIG_PATH}/couchdb/." /tmp/couchdb-dryrun-data/
```

- [ ] **Step 2: Temporarily point the PVC's underlying `local-path` directory at the copy, apply, and verify the pod starts**

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl apply -k k8s/base/obsidian-vault
kubectl wait --for=condition=Available deployment/obsidian-vault -n obsidian-vault --timeout=60s
kubectl get pods -n obsidian-vault
```
Expected: `1/1 Running`. (The PVC provisions fresh under `local-path` — this dry run validates the Pod spec, image, config, and CouchDB/`livesync-bridge` startup itself, not the exact data copy step, which Task 6 exercises for real.)

- [ ] **Step 3: Verify CouchDB responds and `livesync-bridge` connects**

```bash
kubectl exec -n obsidian-vault deployment/obsidian-vault -c couchdb -- curl -s http://localhost:5984/ 
kubectl logs -n obsidian-vault deployment/obsidian-vault -c livesync-bridge --tail=20
```
Expected: CouchDB returns its version JSON; `livesync-bridge` logs show it connecting to peers without auth errors.

- [ ] **Step 4: Tear down the dry-run deployment (Task 6 re-applies for real)**

```bash
kubectl delete -k k8s/base/obsidian-vault
sudo rm -rf /tmp/couchdb-dryrun-data
```

---

### Task 6: STOP — real cutover

**This is the downtime window: only one process can bind `<TAILSCALE_IP>:5984` at a time.**

**Files:** none (host + cluster state only)

- [ ] **Step 1: Confirm go-ahead, then stop the Docker containers**

```bash
cd ~/git/yopflix/seedbox
sudo docker stop livesync-bridge couchdb
```
Expected: both container names echoed back.

- [ ] **Step 2: Apply the real manifests (creates a fresh PVC)**

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl apply -k k8s/base/obsidian-vault
kubectl wait --for=condition=Available deployment/obsidian-vault -n obsidian-vault --timeout=60s
```

- [ ] **Step 3: Copy the real data into the PVC**

```bash
PVC_PATH=$(kubectl get pv -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for pv in data['items']:
    claim = pv['spec'].get('claimRef', {})
    if claim.get('name') == 'couchdb-data' and claim.get('namespace') == 'obsidian-vault':
        print(pv['spec']['local']['path'])
        break
")
echo "$PVC_PATH"
HOST_CONFIG_PATH=$(grep '^HOST_CONFIG_PATH=' ~/git/yopflix/seedbox/.env | cut -d= -f2)
sudo cp -a "${HOST_CONFIG_PATH}/couchdb/." "${PVC_PATH}/"
sudo chown -R 5984:5984 "${PVC_PATH}"
```
`5984:5984` matches the official `couchdb:3` image's own `couchdb` user/group (verify with `docker exec couchdb id couchdb` on the still-stopped-but-not-yet-removed container, or `docker run --rm couchdb:3 id couchdb` if already removed, before trusting this value blindly).

- [ ] **Step 4: Restart the pod so it picks up the copied-in data cleanly**

```bash
kubectl rollout restart deployment/obsidian-vault -n obsidian-vault
kubectl wait --for=condition=Available deployment/obsidian-vault -n obsidian-vault --timeout=60s
kubectl get pods -n obsidian-vault
```
Expected: `1/1 Running`.

- [ ] **Step 5: Verify CouchDB serves the real database**

```bash
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -s "http://${TS_IP}:5984/obsidian-vault" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('doc_count'))"
```
Expected: a real document count (not `0` and not a "database does not exist" error) — proves the copied-in data is genuinely being served, not a fresh empty database.

- [ ] **Step 6: Verify a live Obsidian client round-trip**

Ask the human partner to confirm: open Obsidian on a phone or laptop, make a small edit (or check that recent notes still sync), and confirm the change reaches this host's disk within the usual ~30s window (or, in the other direction, that a change made on this host's disk via `docs/` appears on the device). This is the design doc's own bar — a real device round-trip, not just an API query.

- [ ] **Step 7: Positive reachability — other services unaffected**

```bash
docker ps -q | wc -l
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "jellyfin.yopflix.world:443:${TS_IP}" https://jellyfin.yopflix.world/ --max-time 5
```
Expected: `20` (22 minus the 2 stopped `couchdb`/`livesync-bridge` containers) and `302`. If either differs, **stop immediately** and go to Task 7 (rollback).

---

### Task 7: STOP — rollback drill (actually executed)

**Files:** none (drill only — ends with the k8s pod back in place)

- [ ] **Step 1: Confirm go-ahead, then roll back to Docker**

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl scale deployment/obsidian-vault -n obsidian-vault --replicas=0
cd ~/git/yopflix/seedbox
sudo docker start couchdb livesync-bridge
sleep 5
TS_IP=$(grep '^TAILSCALE_IP=' /home/cian/git/ai-agents/.env | cut -d= -f2)
curl -s "http://${TS_IP}:5984/obsidian-vault" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('doc_count'))"
```
Expected: the same real document count as Task 6 Step 5 — served by the Docker container again (which still has the real, untouched data; Task 6 only *copied* it into the PVC, never deleted the source).

- [ ] **Step 2: Re-cutover to leave the phase in its intended end state**

```bash
sudo docker stop couchdb livesync-bridge
kubectl scale deployment/obsidian-vault -n obsidian-vault --replicas=1
kubectl wait --for=condition=Available deployment/obsidian-vault -n obsidian-vault --timeout=60s
curl -s "http://${TS_IP}:5984/obsidian-vault" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('doc_count'))"
docker ps -q | wc -l
```
Expected: same document count, `20` — back to the post-Task-6 state, drill complete.

---

### Task 8: Record Phase 5 completion in the design doc

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Append a Phase 5 status note**

Add to the `## Status` section, after the Phase 4 note, summarizing: the single-Pod architecture, the `privileged`-PSA-via-`hostPort` finding (not `hostPath` this time), the locally-built/`ctr`-imported `livesync-bridge` image (no registry), the config.json `localhost` change, and the rollback drill result — following the same level of honest, specific detail as every prior phase's note (real facts discovered during implementation, not just the plan's original assumptions).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 5 (Obsidian cutover) completion"
```

---

## Self-Review

**Spec coverage:** single Pod, two containers, `localhost:5984` internal ✓ Task 2 Step 4; PSA `privileged` via `hostPort` (not `hostPath`) ✓ Global Constraints + Task 2 Step 1; CouchDB PVC + one-time copy-in ✓ Task 6 Steps 2-3; `livesync-bridge` hostPath (3 peers, safe against EBUSY) ✓ Global Constraints + Task 2 Step 4; scoped Secret for `livesync-bridge` only ✓ Task 4; dry-run-before-real-cutover ✓ Tasks 5-6; rollback drill actually executed ✓ Task 7; positive-reachability check ✓ Task 6 Step 7.

**Placeholder scan:** `<TAILSCALE_IP>` in Task 2 Step 4 is the same category as Phase 3's — a real value substituted at write-time per repo convention (never committed literally), not an unresolved gap. The exact `.env.custom` variable name for `COUCHDB_USER` in Task 4 is flagged as needing a quick `env | grep` check rather than asserted with false confidence, since hyphen-to-underscore shell variable naming from that file wasn't independently verified letter-for-letter — an honest known-limitation, not a placeholder.

**Type/interface consistency:** Secret name `obsidian-vault-secrets` matches between Task 2's Deployment `envFrom`, Task 3's kind-test fake, and Task 4's real creation. PVC name `couchdb-data` matches between Task 2's `pvc.yaml`/Deployment volume reference and Task 6's PV-path lookup script (same jq/python pattern Phase 3 used, adjusted for this claim name). ConfigMap name `livesync-bridge-config` matches between Task 2 Step 3's creation and Step 4's Deployment volume reference.

**Known limitations, stated honestly:** the `project` hostPath mount (Task 2's note) is broader than the current Docker Compose single-file bind — a deliberate, flagged tradeoff, not an oversight, since a k8s `hostPath: type: File` mount can't easily be retargeted to a different filename inside the container the way a Compose bind mount can. The admin `chown` uid/gid (Task 6 Step 3) is asserted as `5984:5984` based on the official image's documented convention but instructs verifying against the real (soon-to-be-stopped) container before trusting it, rather than asserting it blindly — same honesty pattern as Phase 3's uid discovery.
