# Phase 2 — k3s Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan touches live host firewall state and installs a second orchestrator (k3s) alongside the running production Docker seedbox stack (22 containers, incl. Traefik fronting every public service).** Tasks 5 and 6 are marked STOP — do not execute them, or resume execution past them, without the human partner's explicit go-ahead in this session. A subagent must not be dispatched for Tasks 5, 6, or 8 unprompted — those steps require a human decision point, not autonomous execution.

**Goal:** Install k3s on the seedbox host, bound to the Tailscale interface only, with scoped firewall rules that let pods reach the API server and cluster DNS without opening anything to the public internet or to the existing Docker containers — then prove both a negative (nothing new is publicly reachable) and a positive (the existing seedbox stack is untouched) before calling the phase done.

**Architecture:** No new application workloads yet (that's Phase 3+) — this phase installs the k3s control plane itself plus the two Phase 1 base manifests (`namespace.yaml`) extended with a default-deny-egress `NetworkPolicy`. Two host-level scripts (`scripts/k3s-install.sh`, `scripts/k3s-firewall-apply.sh` + its rollback counterpart) are written and reviewed before either is ever run. A local `kind` (Kubernetes-in-Docker) smoke test validates the manifests against a throwaway cluster first, so the real install only ever applies manifests already proven to work.

**Tech Stack:** k3s (`stable` channel), `kind` + `kubectl` (official install methods, local smoke-test only), UFW `route` rules (the modern UFW mechanism for FORWARD-chain traffic, not raw `iptables`), `nmap` (negative exposure test, run by the human off-Tailscale — this host cannot validate its own external exposure).

## Global Constraints

- Host is **not behind NAT**: public IP `37.187.226.57` on `eno3`, Tailscale IP in `.env` as `TAILSCALE_IP` (never hardcoded in tracked files — design decision 5). All scripts source `.env` at runtime.
- Design decision 1: bind control plane + NodePorts to Tailscale only — `--bind-address`, `--advertise-address`, `--kube-proxy-arg=nodeport-addresses=<TAILSCALE_IP>/32`. Kubeconfig stays mode 600 (k3s's default — do not pass `--write-kubeconfig-mode 644`).
- Design decision 2: no blanket UFW rules. `Anywhere on tailscale0 ALLOW IN` already exists (verified below) — `kubectl` over Tailscale needs zero new rules. Only two scoped `ufw route allow` rules: pod CIDR → `<TAILSCALE_IP>:6443`, pod CIDR → `10.43.0.10:53`. Plus a default-deny-egress `NetworkPolicy` on the `ai-agents` namespace (DNS + 443 allow-listed).
- k3s defaults: pod CIDR `10.42.0.0/16`, service CIDR `10.43.0.0/16`, cluster DNS `10.43.0.10`. Not configured explicitly in this plan — these are k3s's stock defaults, used as-is.
- The existing seedbox Traefik occupies host ports 80/443 (`docker port traefik` confirms `0.0.0.0:80`, `0.0.0.0:443`) — k3s's bundled Traefik and Klipper `servicelb` MUST be disabled (`--disable=traefik --disable=servicelb`) or install will port-conflict with production traffic.
- `flannel` must be pinned to `tailscale0` via `--flannel-iface=tailscale0` — without this, k3s auto-selects the default-route interface, which on this host is the **public** NIC (`eno3`), putting pod overlay traffic on the public interface. This is the same "host is not behind NAT" fact that reshaped every other design decision (design doc, "The fact that reshaped the design").
- Rollback is `k3s-uninstall.sh` (k3s ships this at `/usr/local/bin/k3s-uninstall.sh`) + the firewall rollback script, never `iptables-restore` — that would strip Docker's own live `DOCKER-USER`/`DOCKER-FORWARD` chains (design doc Phase 2 bullet).
- Verified host state (2026-08-02, this session): `k3s`, `kind`, `kubectl`, `nmap` — none installed. UFW active, default deny incoming/routed, allow outgoing. `FORWARD` chain order: `DOCKER-USER` → `DOCKER-FORWARD` → `ts-forward` → `ufw-before-forward` → ... → policy `DROP`. CouchDB bound `<TAILSCALE_IP>:5984` only (not `0.0.0.0`) — this is the existing-correct baseline Checkpoint A re-confirms hasn't regressed. 22 Docker containers running. Baseline positive-reachability check: `curl --resolve jellyfin.yopflix.world:443:<TAILSCALE_IP> https://jellyfin.yopflix.world/` → **HTTP 302** (Jellyfin's unauthenticated redirect) — this is the exact value Checkpoint B compares against post-install.
- Every task's shell scripts use `set -euo pipefail` and are idempotent where possible (re-running should not error on already-applied state) — this is real ops tooling, not a one-shot plan artifact.

---

### Task 1: `kind` + `kubectl` local smoke test of `k8s/base`

Validates the Phase 1 manifests against a real (if throwaway) Kubernetes API server before they ever touch the production host. Fully local — no host network or firewall changes, no interaction with the live k3s install to come.

**Files:**
- Create: none (tooling installed to `~/bin`, cluster is ephemeral)

**Interfaces:**
- Consumes: `k8s/base/kustomization.yaml`, `k8s/base/namespace.yaml` (Phase 1).
- Produces: confidence that `kubectl apply -k k8s/base` succeeds and the PSA `baseline` label lands correctly — this is what Task 6's real-cluster `apply` step depends on not surprising us.

- [ ] **Step 1: Install `kubectl` (official stable-channel method)**

```bash
mkdir -p ~/bin
curl -L -o ~/bin/kubectl "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x ~/bin/kubectl
export PATH="$HOME/bin:$PATH"
kubectl version --client
```
Expected: prints a `Client Version:` line, no error.

- [ ] **Step 2: Install `kind` (official method)**

```bash
curl -Lo ~/bin/kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x ~/bin/kind
kind version
```
Expected: prints a `kind vX.Y.Z` line.

- [ ] **Step 3: Create an ephemeral cluster and apply `k8s/base`**

```bash
kind create cluster --name k3s-phase2-smoke
kubectl apply -k k8s/base
kubectl get ns ai-agents -o jsonpath='{.metadata.labels}'
echo
```
Expected: `kind create cluster` reports `Set kubectl context to "kind-k3s-phase2-smoke"`; `kubectl apply -k` reports `namespace/ai-agents created`; the `jsonpath` output contains `"pod-security.kubernetes.io/enforce":"baseline"`.

- [ ] **Step 4: Tear down (leaves no lasting host state)**

```bash
kind delete cluster --name k3s-phase2-smoke
```
Expected: `Deleting cluster "k3s-phase2-smoke" ...` with no error.

- [ ] **Step 5: Commit nothing (no tracked files changed) — proceed to Task 2**

This task's deliverable is the confidence, not a diff. No commit.

---

### Task 2: Default-deny-egress `NetworkPolicy` for the `ai-agents` namespace

**Files:**
- Create: `k8s/base/networkpolicy.yaml`
- Modify: `k8s/base/kustomization.yaml`

**Interfaces:**
- Consumes: `ai-agents` namespace (Task 3 of Phase 1).
- Produces: a `NetworkPolicy` resource that Task 6 applies to the real cluster and Task 7's positive checkpoint implicitly relies on (agent pods in Phase 4 need DNS + 443 egress to survive; this policy is the ceiling those pods must fit under).

- [ ] **Step 1: Write the NetworkPolicy**

```yaml
# k8s/base/networkpolicy.yaml
# Default-deny egress for the ai-agents namespace (design decision 2), with
# DNS resolution and outbound HTTPS allow-listed — everything else (the
# Docker bridge networks, CouchDB, Portainer, sshd, etc.) stays unreachable
# from any pod in this namespace even if a pod is compromised.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: ai-agents
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

- [ ] **Step 2: Add it to the base kustomization**

Modify `k8s/base/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - networkpolicy.yaml
```

- [ ] **Step 3: Validate with kubeconform**

```bash
docker run --rm -v "$(pwd)/k8s:/k8s" ghcr.io/yannh/kubeconform:latest -ignore-missing-schemas -summary -recursive /k8s/base
```
Expected: `Resources: 2, Skipped: 0, Errors: 0` (namespace + networkpolicy).

- [ ] **Step 4: Re-run the kind smoke test to confirm it applies cleanly**

```bash
export PATH="$HOME/bin:$PATH"
kind create cluster --name k3s-phase2-smoke
kubectl apply -k k8s/base
kubectl get networkpolicy -n ai-agents default-deny-egress
kind delete cluster --name k3s-phase2-smoke
```
Expected: `kubectl get networkpolicy` shows `default-deny-egress` with `POD-SELECTOR <none>`; cluster deletes cleanly afterward.

- [ ] **Step 5: Commit**

```bash
git add k8s/base/networkpolicy.yaml k8s/base/kustomization.yaml
git commit -m "feat(k8s): add default-deny-egress NetworkPolicy to ai-agents namespace"
```

---

### Task 3: Write `scripts/k3s-install.sh` (review only — do not run)

**Files:**
- Create: `scripts/k3s-install.sh`

**Interfaces:**
- Consumes: `TAILSCALE_IP` from `.env` (repo root, gitignored).
- Produces: the exact install invocation Task 5 runs — writing and reviewing it here, separately from running it, is what makes Task 5 a one-line "run the thing we already agreed on" rather than a live-authored command.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# scripts/k3s-install.sh — installs k3s bound to the Tailscale interface only.
# Reviewed 2026-08-02 as part of Phase 2 of the k3s migration
# (docs/superpowers/specs/2026-08-01-k3s-migration-design.md, design
# decisions 1-2). Do not run without having read this file first — it
# installs a cluster-admin API on this host.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "no .env file found — TAILSCALE_IP is required" >&2
  exit 1
fi
set -a
source .env
set +a
if [ -z "${TAILSCALE_IP:-}" ]; then
  echo "TAILSCALE_IP not set in .env" >&2
  exit 1
fi

echo "Installing k3s, bound to ${TAILSCALE_IP} only."
echo "Traefik and servicelb disabled (host's own Traefik owns 80/443)."

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=stable sh -s - server \
  --node-ip="${TAILSCALE_IP}" \
  --bind-address="${TAILSCALE_IP}" \
  --advertise-address="${TAILSCALE_IP}" \
  --flannel-iface=tailscale0 \
  --kube-proxy-arg=nodeport-addresses="${TAILSCALE_IP}/32" \
  --write-kubeconfig-mode 600 \
  --disable=traefik \
  --disable=servicelb

echo "Waiting for node Ready..."
sudo k3s kubectl wait --for=condition=Ready node --all --timeout=120s
sudo k3s kubectl get nodes -o wide
```

- [ ] **Step 2: Syntax-check only — do not execute**

```bash
bash -n scripts/k3s-install.sh && echo "syntax OK"
chmod +x scripts/k3s-install.sh
```
Expected: `syntax OK`. This step proves the script parses; it does **not** run it — `bash -n` only checks syntax.

- [ ] **Step 3: Commit**

```bash
git add scripts/k3s-install.sh
git commit -m "feat(k8s): add k3s install script, Tailscale-bound (not yet run)"
```

---

### Task 4: Write firewall apply/rollback scripts (review only — do not run)

**Files:**
- Create: `scripts/k3s-firewall-apply.sh`
- Create: `scripts/k3s-firewall-rollback.sh`

**Interfaces:**
- Consumes: `TAILSCALE_IP` from `.env`, k3s's default pod CIDR (`10.42.0.0/16`) and cluster DNS ClusterIP (`10.43.0.10`) — both stock defaults, not configured elsewhere in this plan, so hardcoding them here is documenting a k3s default, not a host-specific secret (design decision 5 concerns host-specific values like the Tailscale IP, not upstream project defaults).
- Produces: the exact commands Task 5 runs, and the exact inverse Task 8's rollback drill runs.

- [ ] **Step 1: Write the apply script**

```bash
#!/usr/bin/env bash
# scripts/k3s-firewall-apply.sh — scoped UFW route rules for k3s (design
# decision 2). Adds exactly two allow rules: pod CIDR -> apiserver, pod
# CIDR -> cluster DNS. Does NOT touch any existing UFW rule. Uses `ufw
# route`, the mechanism for FORWARD-chain (routed) traffic, not a bare
# `ufw allow` (which only governs the INPUT chain).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .env
set +a
if [ -z "${TAILSCALE_IP:-}" ]; then
  echo "TAILSCALE_IP not set in .env" >&2
  exit 1
fi

POD_CIDR="10.42.0.0/16"
CLUSTER_DNS="10.43.0.10"

sudo ufw route allow from "${POD_CIDR}" to "${TAILSCALE_IP}" port 6443 proto tcp comment 'k3s: pods to apiserver'
sudo ufw route allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto udp comment 'k3s: pods to coredns udp'
sudo ufw route allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto tcp comment 'k3s: pods to coredns tcp'

echo "Applied. Current routed rules matching pod CIDR:"
sudo ufw status verbose | grep "${POD_CIDR}" || echo "(none matched — check ufw status manually)"
```

- [ ] **Step 2: Write the rollback script (exact inverse)**

```bash
#!/usr/bin/env bash
# scripts/k3s-firewall-rollback.sh — removes exactly the rules
# scripts/k3s-firewall-apply.sh added. Safe to run even if the rules were
# never applied (ufw route delete on a non-existent rule is a no-op error,
# caught here so the script doesn't abort partway through).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .env
set +a
if [ -z "${TAILSCALE_IP:-}" ]; then
  echo "TAILSCALE_IP not set in .env" >&2
  exit 1
fi

POD_CIDR="10.42.0.0/16"
CLUSTER_DNS="10.43.0.10"

sudo ufw route delete allow from "${POD_CIDR}" to "${TAILSCALE_IP}" port 6443 proto tcp || true
sudo ufw route delete allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto udp || true
sudo ufw route delete allow from "${POD_CIDR}" to "${CLUSTER_DNS}" port 53 proto tcp || true

echo "Rolled back. Confirming no pod-CIDR rules remain:"
sudo ufw status verbose | grep "${POD_CIDR}" && echo "WARNING: rule(s) still present" || echo "clean"
```

- [ ] **Step 3: Syntax-check only — do not execute**

```bash
bash -n scripts/k3s-firewall-apply.sh && echo "apply script syntax OK"
bash -n scripts/k3s-firewall-rollback.sh && echo "rollback script syntax OK"
chmod +x scripts/k3s-firewall-apply.sh scripts/k3s-firewall-rollback.sh
```
Expected: both print their "syntax OK" line.

- [ ] **Step 4: Commit**

```bash
git add scripts/k3s-firewall-apply.sh scripts/k3s-firewall-rollback.sh
git commit -m "feat(k8s): add scoped firewall apply/rollback scripts for k3s (not yet run)"
```

---

### Task 5: STOP — apply firewall rules (requires explicit human go-ahead)

**Do not execute this task's commands without the human partner explicitly authorizing it in this session, after Tasks 1-4 are committed and reviewable in the diff.** This changes live UFW state on a host that also fronts 22 production containers over the same `FORWARD` chain (`DOCKER-USER`/`DOCKER-FORWARD` come before UFW's own chains — verified in Global Constraints — so this should be additive-only, but "should be" is exactly why this is a stop point, not an assumption to act on).

**Files:** none (host state only)

- [ ] **Step 1: Confirm go-ahead received, then run**

```bash
./scripts/k3s-firewall-apply.sh
```
Expected: three `Rule added` (or similar UFW confirmation) lines, then the `grep` showing the three new rules.

- [ ] **Step 2: Immediate regression check — seedbox unaffected by the firewall change alone**

```bash
docker ps -q | wc -l
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "jellyfin.yopflix.world:443:${TAILSCALE_IP}" https://jellyfin.yopflix.world/ --max-time 5
```
Expected: `22` (container count unchanged from the Global Constraints baseline); `302` (matches the pre-change baseline exactly — a UFW rule addition should never change this, since nothing existing was removed or reordered).

If either value differs from baseline: **stop, do not proceed to Task 6**, run `./scripts/k3s-firewall-rollback.sh`, and investigate before continuing.

---

### Task 6: STOP — install k3s and apply base manifests (requires explicit human go-ahead)

**Do not execute this task's commands without the human partner explicitly authorizing it, separately from Task 5's go-ahead — these are two distinct risks (firewall state vs. a new orchestrator process) and should be authorized as two distinct decisions, not one blanket yes.**

**Files:** none (host state only)

- [ ] **Step 1: Confirm go-ahead received, then run**

```bash
./scripts/k3s-install.sh
```
Expected: k3s's own install output ending in a systemd service start; the script's own `kubectl wait` prints `node/<hostname> condition met`; `kubectl get nodes -o wide` shows one `Ready` node with `INTERNAL-IP` equal to `TAILSCALE_IP` (not the public IP `37.187.226.57`, not a `10.x` address).

- [ ] **Step 2: Apply the base manifests to the real cluster**

```bash
sudo k3s kubectl apply -k k8s/base
sudo k3s kubectl get ns ai-agents -o jsonpath='{.metadata.labels}'
echo
sudo k3s kubectl get networkpolicy -n ai-agents
```
Expected: `namespace/ai-agents created`, `networkpolicy.networking.k8s.io/default-deny-egress created`; the labels output contains `pod-security.kubernetes.io/enforce":"baseline`.

- [ ] **Step 3: Confirm kubeconfig permissions**

```bash
ls -l /etc/rancher/k3s/k3s.yaml
```
Expected: mode `600` (design decision 1 — never `644`).

---

### Task 7: Two required checkpoints (negative + positive)

Design doc: "two checkpoints, both required." Neither substitutes for the other.

**Files:** none (verification only)

- [ ] **Step 1: Negative exposure test — MUST be run by the human partner, off-Tailscale**

This cannot be executed from this host or from any Tailscale-connected device — the whole point is proving the API and NodePort range are unreachable from the public internet, and a scan launched from the target machine itself (or from inside the Tailscale mesh, which already has an explicit allow rule) doesn't test that path. Ask the human partner to run this from a phone on mobile data, a cloud VM, or any network that is not this Tailscale tailnet:

```bash
nmap -Pn -p6443,30000-32767 37.187.226.57
```
Expected: every scanned port reports `filtered` (or the whole range reports as filtered in the summary) — **not** `open`, **not** `closed` (closed still confirms a response came back, which filtered/dropped does not). `open` on any of these ports means the go/no-go check has failed: stop immediately, run `./scripts/k3s-firewall-rollback.sh` and `/usr/local/bin/k3s-uninstall.sh`, and do not proceed to any later phase until the exposure is understood.

- [ ] **Step 2: Positive reachability test — can be run from this host**

```bash
docker ps -q | wc -l
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "jellyfin.yopflix.world:443:${TAILSCALE_IP}" https://jellyfin.yopflix.world/ --max-time 5
```
Expected: `22` and `302` — identical to both the pre-change baseline (Global Constraints) and Task 5 Step 2's post-firewall check. Identical across all three measurements is the actual proof; any drift after the k3s install specifically (vs. after the firewall change alone) narrows the regression to k3s's own iptables insertions (flannel/kube-proxy), which is exactly the risk this checkpoint exists to catch.

- [ ] **Step 3: Bind-address checks**

```bash
ss -tlnp | grep 6443
ss -tlnp | grep 5984
```
Expected: the `6443` line shows `${TAILSCALE_IP}:6443`, never `0.0.0.0:6443` or `*:6443`. The `5984` line is unchanged from the Global Constraints baseline (`${TAILSCALE_IP}:5984`) — confirms k3s's install didn't touch CouchDB's own bind.

---

### Task 8: Rollback drill (actually executed, not just documented)

Design doc Verification: "rollback drills actually executed before each cutover is called done, not just documented." This phase's own change gets one, not just the higher-stakes Phase 3+ cutovers — proving the rollback script works now, while the blast radius is still "just a namespace and a NetworkPolicy," is cheaper than discovering it doesn't work during a real incident later.

**Files:** none (host state only)

- [ ] **Step 1: Uninstall k3s**

```bash
sudo /usr/local/bin/k3s-uninstall.sh
```
Expected: k3s's own uninstaller output, ending with the service stopped and removed.

- [ ] **Step 2: Roll back the firewall rules**

```bash
./scripts/k3s-firewall-rollback.sh
```
Expected: `clean` (no pod-CIDR rules remain).

- [ ] **Step 3: Verify the seedbox is fully unaffected and no k3s traces remain**

```bash
docker ps -q | wc -l
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "jellyfin.yopflix.world:443:${TAILSCALE_IP}" https://jellyfin.yopflix.world/ --max-time 5
sudo iptables -L FORWARD -n --line-numbers | head -10
which k3s || echo "k3s binary gone"
```
Expected: `22` and `302` (matches baseline exactly); the `FORWARD` chain's first entries are back to exactly `DOCKER-USER`, `DOCKER-FORWARD`, `ts-forward` (no `KUBE-*` or `cni0`-related chains left behind); `k3s binary gone`.

- [ ] **Step 4: Re-install to leave the cluster ready for Phase 3+**

```bash
./scripts/k3s-firewall-apply.sh
./scripts/k3s-install.sh
sudo k3s kubectl apply -k k8s/base
```
Expected: same output as Tasks 5-6 the first time through. This leaves the phase in its intended end state (cluster installed and verified) rather than rolled back — the drill proves rollback works without leaving Phase 3 without a cluster to build on.

- [ ] **Step 5: Re-run Task 7's checkpoints once more**

Repeat Task 7 Steps 2-3 (the parts runnable from this host — Step 1's off-Tailscale `nmap` does not need re-running if it already passed once against the same firewall rules, since the rollback+reapply cycle recreates identical state).

---

### Task 9: Record Phase 2 completion in the design doc

**Files:**
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md`

- [ ] **Step 1: Append a Phase 2 status note**

Add to the `## Status` section, after the Phase 1 status notes:

```markdown
**Phase 2 — k3s install — done (2026-08-02):** k3s installed (`stable` channel), bound to `<TAILSCALE_IP>` only (`--bind-address`/`--advertise-address`/`--node-ip`), `--flannel-iface=tailscale0` (host's public NIC would otherwise carry pod overlay traffic — same "not behind NAT" fact as every other decision here), Traefik/servicelb disabled (host's own Traefik already owns 80/443). `k8s/base` extended with a default-deny-egress `NetworkPolicy` (DNS + 443 allow-listed). Two scoped `ufw route allow` rules added for pod CIDR → apiserver/coredns — nothing else opened. Negative exposure test (`nmap` from off-Tailscale against `37.187.226.57:6443,30000-32767`) — filtered, not open. Positive reachability (`docker ps` count + Jellyfin-via-Traefik HTTP code) held at `22`/`302` across baseline → post-firewall → post-install → post-rollback-drill-reinstall, all four measurements identical. Rollback drill actually executed: uninstalled, verified the `FORWARD` chain and container count reverted cleanly with no `KUBE-*` chains left behind, then reinstalled to leave the cluster ready for Phase 3.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-k3s-migration-design.md
git commit -m "docs: record Phase 2 (k3s install) completion and checkpoint results"
```

---

## Self-Review

**Spec coverage:** design doc Phase 2 bullet — firewall block pre-written and reviewed before install (Tasks 3-4 write/review, Tasks 5-6 are the separately-authorized run steps) ✓; negative exposure test with the exact `nmap` command and target (Task 7 Step 1) ✓; positive reachability test on the existing seedbox stack (Task 7 Step 2, also re-run after firewall alone in Task 5 Step 2 and after the rollback drill in Task 8 Step 5) ✓; `FORWARD` chain / `DOCKER-USER`/`DOCKER-FORWARD`/`ts-forward` ordering risk called out explicitly (Global Constraints, Task 8 Step 3's chain-order check) ✓; rollback via `k3s-uninstall.sh` + firewall rollback script, never `iptables-restore` (Task 8) ✓. Design decisions 1-2 — Tailscale-only binding (Task 3's install flags) ✓; no blanket UFW rules, scoped `ufw route allow` only (Task 4) ✓; default-deny-egress NetworkPolicy (Task 2) ✓. Design doc Verification section — "kind smoke test before touching the real cluster" (Task 1) ✓; "rollback drills actually executed... not just documented" (Task 8, a real uninstall/reinstall cycle, not a paragraph asserting it would work) ✓.

**Placeholder scan:** no TBDs. Every step has literal commands and literal expected output, including the two host-state-only tasks (5, 6, 7, 8) which have no files to diff but do have exact expected command output.

**Type/interface consistency:** `TAILSCALE_IP`, `POD_CIDR` (`10.42.0.0/16`), and `CLUSTER_DNS` (`10.43.0.10`) are used identically across Task 4's apply/rollback scripts, Task 3's install script's `--kube-proxy-arg`, and Task 7's checkpoint commands. The Jellyfin reachability check (domain `jellyfin.yopflix.world`, expected code `302`) is identical across the Global Constraints baseline, Task 5 Step 2, Task 7 Step 2, and Task 8 Step 3/5 — this repetition across four measurement points is deliberate, not duplication to clean up: it's what makes "did k3s specifically break something, vs. the firewall change, vs. nothing at all" answerable instead of assumed.

**Known limitation, stated honestly:** the `kind` smoke test (Task 1) validates the manifests, not the real host's iptables/UFW interaction — no local tool can simulate that, which is exactly why Tasks 5-8 exist as live, checkpointed, human-authorized steps rather than something this plan could fully verify in advance. The negative exposure test (Task 7 Step 1) cannot be executed by the agent running this plan under any circumstance — it structurally requires a network vantage point this session does not have, not a permissions choice. If the human partner cannot run it promptly after Task 6, the cluster should be considered **unverified-exposed** and Task 8's rollback should be treated as the safer default rather than leaving Task 6's state unverified indefinitely.
