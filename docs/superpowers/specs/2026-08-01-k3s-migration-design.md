# Containerise `ai-agents` and run it on k3s — SRE proof of concept

## Problem

`github.com/hughseyxo/ai-agents` is public but runs entirely on host cron + systemd — there is no Kubernetes-shaped artefact in the portfolio, and an SRE CV gap needs a real one, not a toy demo. The workloads already exist and are genuinely always-on (wedding-ui, plant-ui, six cron agents, CouchDB/livesync-bridge for the Obsidian vault), so the migration can be a real production cutover rather than a synthetic sample app.

Two independent review passes (architectural, security) against the first draft found live issues that had to be fixed before any cluster work: an unrestricted LLM tool surface on untrusted input, sensitive files not gitignored, and a leaked OAuth secret. Those are **Phase −1** and are now done (see Status).

## Status

**Phase −1 — complete**, merged to `main` (commit `7058cc8`), tag `pre-k8s-migration`:
- `.gitignore` covers `data/plant-photos/`, `data/plant-photo-batches/`, `docs/daily/`, `docs/agent-learnings/`; 12 previously-tracked files untracked (history still holds them — a separate, explicit decision if that needs purging).
- `agents/base.py` confines the `claude` CLI's tool surface per agent (`mcp_config` / `allowed_tools` against a `DENIED_TOOLS` default-deny list). Empirically verified against the live CLI, not just mocked: `--allowedTools` does not restrict (Gmail read tools stayed reachable until moved into the denylist explicitly); MCP tools are deferred behind `ToolSearch`, so MCP-using agents must re-enable it explicitly or silently stop sending mail.
- Outstanding, user's own action: rotate the leaked Google OAuth client secret.

**Phases 0–8 (the k3s migration itself) are parked** — not started, except one Phase 0 prerequisite fixed opportunistically (see below). This document is the persisted form of that plan so it survives context resets; `superpowers:writing-plans` turns each phase into a bite-sized execution plan when we reach it (not all at once — Phase 0's spikes can change decisions baked into later phases).

**Phase 0, task 1 — done (2026-08-01):** `livesync-bridge` was crash-looping (765 restarts) with `"Remote database is encrypted but no passphrase provided"`. Root cause: the remote CouchDB database has E2EE enabled but `services/livesync-bridge/config.json` (in the separate `~/git/yopflix/seedbox` repo) had empty `passphrase` fields on all three CouchDB peers. Fixed by adding `LIVESYNC-BRIDGE_PASSPHRASE` to `.env.custom` (gitignored, matches the existing `LIVESYNC-BRIDGE_COUCHDB_*` pattern) and referencing it as `${PASSPHRASE}` in `config.json` — the bridge's `main.ts` substitutes `${VAR}` from its own process env at startup, the same mechanism already used for the CouchDB username/password. Applied via `sudo ./run-seedbox.sh` (idempotent full-stack redeploy; only `livesync-bridge` was recreated, the other 21 containers were untouched). Confirmed stable post-fix — no restarts, and logs show it actively draining the sync backlog (files from `docs/superpowers/specs/`, `docs/daily/`, `docs/agent-learnings/` all observed syncing). Still to prove for the Phase 0 checkpoint proper: a write originating in CouchDB (i.e. from another Obsidian device) landing back on this host's disk, not just disk→CouchDB.

## The fact that reshaped the design

This host is **not behind NAT**:
```
eno3         UP   37.187.226.57/24    ← public, routable (OVH)
tailscale0        <TAILSCALE_IP>/32
```
`cian` (uid 1001) has `NOPASSWD: ALL` and is in the `docker` group. Every firewall and hostPath decision below is read in that light — the first draft assumed a private LAN and got the firewall rules backwards.

## Migration modes

| Workload | Mode | Why |
|---|---|---|
| `wedding-ui`, `couchdb`, `livesync-bridge` | Full cutover out of the yopflix seedbox | They only live there because it was the easiest place to run a container in an existing Docker stack. |
| `agent-health` | Full cutover, crontab line deleted | LLM-free, no OAuth, idempotent — one real cutover beats six suspended ones. |
| Other 5 cron agents | Manifests `suspend: true`, host crontab untouched | They send email; racing a live crontab line double-sends. |
| `plant-ui` | Optional Phase 8, cut if risk/time runs out | Deepest `claude`-CLI dependency, used daily — highest blast radius if wrong. |

Excluded: `security-audit` stays host-side (would need hostPID + hostNetwork + the Docker socket) but must be **extended** to audit the cluster, since it's the one control that would catch the exposures below and the migration moves things out of its current view.

## Design decisions (verified constraints that drove them)

1. **Bind the control plane and NodePorts to Tailscale only**, not the default 0.0.0.0. Public IP + k3s's default API bind + kube-proxy's default NodePort-on-all-interfaces would otherwise publish a cluster-admin API and put ArgoCD/Grafana on the public internet.
   `--bind-address=<TAILSCALE_IP> --advertise-address=<TAILSCALE_IP> --kube-proxy-arg=nodeport-addresses=<TAILSCALE_IP>/32`. Keep kubeconfig at mode 600, not `--write-kubeconfig-mode 644`.
2. **No blanket UFW rules.** `Anywhere on tailscale0 ALLOW IN` already exists — `kubectl` over Tailscale needs zero new rules. `ufw allow 6443/tcp` would expose the API on `37.187.226.57`; `ufw allow from 10.42.0.0/16 to any` would hand every pod a path to Portainer→docker.sock→root, Home Assistant, CouchDB, sshd. Scoped rules only: pod CIDR → `<TAILSCALE_IP>:6443`, pod CIDR → `10.43.0.10:53`. Plus a default-deny-egress `NetworkPolicy` on the namespace (k3s's kube-router netpol controller enforces it) allow-listing DNS and 443 out.
3. **Never mount `~/.claude`, or `~/.claude/projects/<repo>/` generally.** k3s has no userns by default, so a pod writing hostPath as 1001 writes as the real `cian`. `~/.claude` holds `settings.json` hooks and 4265 plugin scripts — write access there is host code execution on the next interactive `claude` run, then root via the NOPASSWD sudoers entry. `~/.claude/projects/-home-cian-git-ai-agents/` (this repo's own transcript history, measured at ~1.2 GB) additionally holds a live leaked secret, so even read-only is an exfiltration surface.
   Instead: provision `/srv/k3s-claude-home` (owned 1001), authenticated as a **separate** Anthropic session, containing only `.credentials.json` and a minimal `settings.json` (`"disableAllHooks": true`, no plugins, no `projects/`). Accept the re-auth divergence — that's the isolation boundary working as intended.
   **One explicit, narrow exception:** `~/.claude/projects/-home-cian-git-ai-agents/memory/` — this is curated content meant to be synced (Claude's memory folder, the third of the Obsidian vault's three sync peers per `CLAUDE.md`), not a transcript, hook, or credential. `livesync-bridge`'s pod (Phase 5) mounts this one leaf path read-write and nothing else under `~/.claude`.
4. **No `.git` mount.** `_git_commit` is reachable only from the manual `librarian-apply` subcommand, but mounting host `.git` against the image's copied tree would let a mismatched working tree stage mass deletions into real history.
5. **Manifests are parameterised, not hardcoded.** The Tailscale IP and other host-specific values never appear literally in tracked files — `CLAUDE.md` and `docs/obsidian-vault-setup.md` reference `<TAILSCALE_IP>` with a pointer to `.env` (gitignored), the same pattern this migration's manifests follow. `k8s/base` carries placeholders; `k8s/overlays/prod` is gitignored, generated from a committed `prod.example`.
6. **Fix `livesync-bridge` in Docker first, prove the Obsidian round-trip, before it exists in k3s at all.** It's currently crash-looping (765 restarts, missing E2EE passphrase) with 79 unsynced files — moving a broken thing into a new environment just relocates the outage.
7. **`runAsUser/runAsGroup: 1001`, not root.** `data/agents.db` is `0644 cian:cian` and `fsGroup` doesn't apply to hostPath; the `claude` CLI also refuses `--dangerously-skip-permissions` as root. Image needs a `cian` user at uid 1001.
8. **Repo path inside the image must be `/home/cian/git/ai-agents`, not `/app`.** `~/.claude.json` keys trust and `enabledMcpjsonServers` to the project path — a different path silently runs with zero MCP tools.
9. **Mount the token directory, not the token file.** `agents/gmail_client.py:save_tokens()` uses `mkstemp` + `os.replace`; `os.replace()` onto a bind-mounted *file* returns `EBUSY`.
10. **Omit `timeZone:` on CronJobs.** Host is `Etc/UTC` and schedules already encode that; setting `Europe/Amsterdam` fires everything 2h early.
11. **Don't widen `service-node-port-range`.** ~20 live host ports already sit in 5000–32767; default 30000–32767 is clear. CouchDB instead gets `hostPort: 5984` + `hostIP` (not a NodePort).

## Architecture

Two images to GHCR, referenced by `@sha256:` digest, never a mutable tag:
1. `ai-agents-wedding-ui` — multi-stage, non-root, PVC-backed. The one fully cluster-native workload (calculator-only, no LLM CLI).
2. `ai-agents-runner` — repo baked in at `/home/cian/git/ai-agents`, user `cian` uid 1001, runs the cron agents.

**Storage:** `wedding-ui` and `couchdb` get real PVCs. Agent pods use hostPath onto `data/`, `output/`, and the separate credential directory (decision 3), with `type:` set explicitly — an unset `type` makes the kubelet create a missing path as root. `docs/` is read-only except the specific subdirectories an agent must write (`docs/plants`, `docs/plant-observations`), and never mounted in the same pod as a credential mount.

**Pod security, every workload:** `allowPrivilegeEscalation: false`, `capabilities: {drop: [ALL]}`, `seccompProfile: RuntimeDefault`, PSA namespace labels (`restricted` for `wedding-ui`; `baseline` where hostPath forces it), `LimitRange` + `ResourceQuota` per namespace. `free -g` shows 26 GB available — size limits generously; over-tight limits cause OOMKills, not safety.

## Where this makes things worse than today — stated honestly

1. Today agent execution has no inbound network path; afterwards agents are pods on a shared network reachable from ArgoCD, Grafana, and the web pods. Mitigated by the default-deny NetworkPolicy.
2. Today all 22 containers bind Tailscale-only; k3s NodePort defaults to all interfaces on a box with a public IP. Mitigated by `nodeport-addresses`.
3. Today `~/.claude` is reachable only by processes you start. Mitigated by not mounting it at all.
4. Today there's no cluster-admin API on the box; k3s adds one. Mitigated by `bind-address` + 600 kubeconfig.
5. Today running code here needs SSH or the Cloudflare-fronted 80/443. Afterwards there's a third path: land a commit on a public repo's `main`. Mitigated by branch protection + ArgoCD manual sync for the first month.

Genuine wins, for balance: `agent-health` in a pod is better isolated than a cron line; `readOnlyRootFilesystem` + resource limits on `wedding-ui` beat the current container; the `LivesyncBridgeDown` alert (Phase 6) fixes a live silent outage. All of these land on the credential-free workloads.

## Phases

- **0 — Prereqs & go/no-go spikes.** Fix `livesync-bridge` in Docker, prove the Obsidian round-trip. Spike the `claude` CLI against `/srv/k3s-claude-home` specifically (not the permissive config — a spike that only tests the easy path will pass and ship the vulnerable design). Capacity table. `.dockerignore`, `agents/requirements.txt`, `/healthz` + `/metrics` on `wedding_ui`.
- **1 — Images + CI.** `kubeconform --ignore-missing-schemas` (`-strict` errors on CRs). Explicit `permissions:` blocks; `on: pull_request` never `pull_request_target`; image push only on `main`. State GHCR visibility; provision an `imagePullSecret` if private. SBOM + provenance + a documented `cosign verify-attestation` step.
- **2 — k3s install.** Firewall block from decisions 1–2, pre-written and reviewed, then install, then two checkpoints, both required: a **negative** exposure test (`nmap -Pn -p6443,30000-32767 37.187.226.57` from off-Tailscale must show filtered; `ss -tlnp | grep 5984` must show `<TAILSCALE_IP>:5984`, never `0.0.0.0`) and a **positive** reachability test on the *existing* seedbox stack (Jellyfin, or Traefik generally, still reachable over Tailscale immediately after install). UFW's `FORWARD` chain is default-`DROP`, with Docker's own `DOCKER-USER`/`DOCKER-FORWARD` chains and Tailscale's `ts-forward` chain punching through it — k3s's `flannel`/`kube-proxy` insert their own chains into the same tables at install time, and a rule-ordering shift could break Docker's inter-container traffic without ever touching a Docker container directly. Rollback is `k3s-uninstall.sh` + `systemctl restart docker`, not `iptables-restore` (would strip Docker's live chains).
- **3 — wedding-ui full cutover.** Dry run alongside the Docker container; `chown` the seeded DB to 1001 (source is `root:root`); verify a **write**, not just a read; `emptyDir` at `/tmp` for `readOnlyRootFilesystem`. Then a ~1 min window: stop container → re-copy data → repoint Traefik — followed immediately by the same positive-reachability check as Phase 2 (Jellyfin/Traefik still serving everything else), since a Traefik reconfiguration mistake has blast radius across every routed service, not just wedding-ui.
- **4 — CronJobs.** `agent-health` cut over for real; the other five `suspend: true`. No `timeZone:`; `concurrencyPolicy: Forbid`; `backoffLimit: 0`; `activeDeadlineSeconds` well under the schedule interval (`synthesize()`'s worst case ≈ 60 min would otherwise swallow the next hourly run). `check-google-token.sh` as an initContainer. Per-agent `--strict-mcp-config` so `news-briefing` gets no MCP servers at all (this is the Phase −1 `agents/base.py` fix, now enforced identically in-cluster).
- **5 — Obsidian cutover.** CouchDB StatefulSet, `hostPort: 5984` + `hostIP`. Bridge config as a Secret — it interpolates credentials and the E2EE passphrase. `livesync-bridge`'s pod mounts `~/.claude/projects/-home-cian-git-ai-agents/memory/` read-write for the memory sync peer (decision 3's narrow exception) — no other `~/.claude` path.
- **6 — Observability.** kube-prometheus-stack via k3s's HelmChart CRD; disable the `etcd` and `kubeProxy` sub-charts (SQLite datastore — those scrape targets don't exist). Retention cap. `LivesyncBridgeDown` alert. Point the agent-staleness rule at `agent-health`'s own success signal rather than duplicating its logic. **Extend `security-audit` to check cluster-side exposure surfaces** (NodePort bind addresses, kubeconfig permissions, RBAC bindings, NetworkPolicy presence) — a prerequisite gate before Phase 7 turns on ArgoCD auto-sync, the biggest remaining risk in the migration.
- **7 — ArgoCD + docs.** `syncPolicy: manual` for month one; never self-manage `k8s/platform/argocd`; `AppProject` with explicit `sourceRepos`/`destinations` and a `clusterResourceBlacklist` on `ClusterRole`/`ClusterRoleBinding`; namespace-scoped controller RBAC (default is cluster-admin); delete `argocd-initial-admin-secret` after bootstrap; `exec.enabled: false`; deliberate TLS choice. Without Phase −1's branch protection, auto-sync from a public repo means landing a commit on `main` is code execution on this host.
- **8 — plant-ui (optional).** Cut only if the earlier phases land clean and there's still appetite — daily-use workload, highest cost of getting it wrong.

## Verification

Unit tests green each phase · `kubeconform --ignore-missing-schemas` in CI · `kind` smoke test before touching the real cluster · negative exposure test from off-Tailscale (Phase 2) · Obsidian round-trip proven twice, Docker then in-cluster (Phase 0, Phase 5) · a real write to wedding-ui at dry run, not just a read (Phase 3) · a week of `agent-health` cluster-only runs with its host crontab line gone (Phase 4) · exactly one `success` row and one email per manual Job run · seedbox regression check after every phase that touches it · rollback drills actually executed before each cutover is called done, not just documented.

## Capacity (measured 2026-08-02)

| Resource | Total | Used | Available | Note |
|---|---|---|---|---|
| Disk (`/`) | 11T | 9.0T (88%) | 1.4T | Plenty of headroom despite the high %; don't let the percentage alone drive sizing decisions. |
| Memory | 31Gi | 3.8Gi + 23Gi cache | 26Gi available | Matches the design doc's earlier "26 GB" figure. Size k8s resource limits generously — over-tight limits cause OOMKills, not safety. |
| CPU | 8 cores | — | — | |
| `data/` | 229M | — | — | hostPath mount size for agent pods. |
| `docs/` | 1.5M | — | — | hostPath mount size for the Obsidian-synced subdirectories. |
| Docker images | — | 17.04GB / 21 images | — | Existing footprint before any k3s/GHCR images are added. |

## Related

- `docs/superpowers/specs/2026-06-19-obsidian-vault-backend-design.md` — the CouchDB/livesync-bridge stack this migrates out of yopflix.
- `docs/superpowers/specs/2026-06-27-wedding-budget-pwa-design.md` — wedding-ui's current Docker/Traefik deployment, cut over in Phase 3.
- `docs/obsidian-vault-setup.md` — device/credential setup for the vault this touches in Phase 5.
