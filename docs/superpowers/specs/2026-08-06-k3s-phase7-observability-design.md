# Phase 7 (Observability) k3s Cutover — Design

## Problem

Phase 7 of `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` adds observability to the k3s cluster. Its primary driver isn't day-to-day monitoring need — only 3 things are actually live in the cluster today (`wedding-ui`, `plant-ui`, and the `agent-health` CronJob; everything else is `suspend: true` or, in Phase 5's case, dropped entirely) — it's the **prerequisite gate before Phase 8 turns on ArgoCD auto-sync**, the biggest remaining risk in the migration. The parent design doc calls this out explicitly: extending `security-audit` to check cluster-side exposure surfaces (NodePort bind addresses, kubeconfig permissions, RBAC bindings, NetworkPolicy presence) is the actual blocker Phase 8 needs cleared.

Given the resource headroom on this host (31G RAM, 8 cores, 26G available, 1.3T disk free), the full `kube-prometheus-stack` is also in scope alongside the security-audit gate — not because 3 live things strictly need dashboards, but because the cost of running it here is low and it's the natural moment to add it while touching this area.

## Current state (as found)

- `agents/security_audit.py` runs 16 read-only checks weekly (Sunday 06:00 UTC), all pure `subprocess.run()`, no Claude CLI except for the Todoist Critical-finding notification. See `docs/security-audit-agent.md` for the full check list and report format.
- `k8s/base/` has 3 workload namespaces today: `ai-agents-cron` (CronJobs, PSA `privileged`), `wedding-ui` (PSA `restricted`), `plant-ui` (PSA `privileged`). Each has its own default-deny-egress `NetworkPolicy` (DNS + 443 allow-listed).
- NodePort usage so far: `30800` (wedding-ui), `30801` (plant-ui). `kube-proxy`'s `nodeport-addresses` is restricted to `<TAILSCALE_IP>` cluster-wide (migration decision 1, set at k3s install in Phase 2) — every NodePort Service is Tailscale-only by construction, not by per-Service configuration.
- Phase 5 (Obsidian-in-k3s) was dropped 2026-08-06 — `livesync-bridge` stays on Docker Compose in the yopflix seedbox stack indefinitely, entirely outside this cluster. The parent design doc's original Phase 7 line mentioned a `LivesyncBridgeDown` alert; that's no longer applicable since there is nothing in-cluster to alert on.
- `agent-health` (`agents/agent_health.py`) already tracks its own last-successful-run state in `data/agents.db` (SQLite) — this is host-side state, not natively Prometheus-scrapable.
- k3s's built-in `HelmChart` CRD is the existing mechanism this migration uses for Helm-delivered components (no separate Helm CLI install on the host).

## Decisions

1. **New namespace `monitoring`, PSA `privileged`.** Required by the `node-exporter` DaemonSet, which needs `hostPath` mounts on `/proc`/`/sys` and a `hostPort` to scrape node-level metrics — the same category of requirement PSA `privileged` already covers for `ai-agents-cron` and `plant-ui` (hostPath) in this migration, just triggered by a different mechanism (hostPath + hostPort together, not hostPath alone). Own default-deny-egress `NetworkPolicy`, with one deliberate, narrow exception: Prometheus needs to scrape pods across every other namespace, so its egress additionally allows the in-cluster pod CIDR on Kubelet's metrics port (`10250`) and each workload's own metrics port — not a blanket relaxation of the default-deny posture, a specific allow for the one thing this namespace's whole purpose requires.

2. **Delivery via k3s's `HelmChart` CRD**, `kube-prometheus-stack`, all public upstream images. No custom `Dockerfile`, no GHCR image, no CI build-matrix change — the first phase in this migration that needs none of that, since nothing here is this project's own code running as a container.

   Values override:
   - Disable the `etcd` and `kubeProxy` sub-chart scrape targets — k3s's SQLite datastore means neither exists to scrape (parent design doc's own explicit call-out).
   - Prometheus storage: PVC on `local-path` (same storage class as wedding-ui), not hostPath — nothing here shares state with host-side processes.
   - Retention: 15 days, explicit in values (not relying on the chart's own default) — an explicit homelab-appropriate cap, since unbounded retention on a single-node box's shared disk is how it quietly fills.

3. **Grafana — NodePort `30802`, no Telegram/email alert routing.** `Service` type `NodePort`, reachable at `http://<TAILSCALE_IP>:30802` (or the Tailscale MagicDNS name) — same final networking pattern `plant-ui` landed on after its own Traefik/Split-DNS attempt was abandoned: no Traefik route, no custom hostname, Tailscale-only by construction underneath Grafana's own login. Default `kube-prometheus-stack` Grafana dashboards (cluster/node/pod overviews) ship with the chart — no custom dashboard JSON needed for this phase. Alertmanager alerts are visible in Grafana/Alertmanager's UI only; no external notification channel. This project already has Telegram/email pings from other agents (`security-audit`, `agent-health`) — adding a new delivery channel here is more plumbing than this phase's actual driver (the Phase 8 gate) needs.

4. **Four `PrometheusRule` groups, minimal and specific to the 3 live things:**
   - **Pod down/restarting** — `wedding-ui`/`plant-ui` Deployments at 0 ready replicas, or restart count climbing within a short window (crash-loop signal).
   - **agent-health cron staleness** — reads `agent-health`'s own success signal rather than re-deriving cron-schedule logic in PromQL (parent design doc's own explicit preference, to avoid duplicating logic that already exists). Bridged via `node-exporter`'s textfile collector: `agent_health.py`'s success path writes one line (`agent_health_last_success_timestamp <unix_ts>`) to node-exporter's textfile directory on every successful run — avoids standing up a separate exporter process for a single metric.
   - **Node resource pressure** — CPU/memory/disk approaching limits on the one node (native `node-exporter` metrics, no extra plumbing) — relevant because this is the one node running both k3s and the Docker seedbox stack.
   - **NodePort/PVC issues** — wedding-ui's PVC nearing full, or a Service losing all endpoints (both stock `kube-state-metrics` PromQL patterns).

5. **`security-audit` check #17 — cluster-side exposure surfaces.** Extends the existing 16-check pattern in `agents/security_audit.py` (same dict-based finding shape: `{severity, check, detail, fix_commands, context, risk, impact}`, still pure `subprocess.run()`, no new dependencies). This is the actual Phase 8 blocker the parent design doc calls out:
   - **NodePort binding verification** — confirms `kube-proxy`'s `nodeport-addresses` flag is still restricted to `<TAILSCALE_IP>` (migration decision 1); **Critical** if ever reverted to cluster-wide.
   - **kubeconfig permissions** — `/etc/rancher/k3s/k3s.yaml` and `~/.kube/config` should be `0600`, owner-only.
   - **RBAC audit** — flags any `ClusterRoleBinding` granting `cluster-admin` (or equivalent broad access) to anything other than expected system accounts. This is the check that most directly matters before Phase 8, since ArgoCD's own RBAC scope is exactly what the parent design doc's Phase 8 line worries about ("namespace-scoped controller RBAC — default is cluster-admin").
   - **NetworkPolicy presence** — every namespace should have a default-deny-egress policy; flags any namespace missing one. A regression check, not just convention — this migration has consistently added one per namespace, this makes it enforced.

   Runs on the same weekly cron, same report file, same severity/Todoist-notification behavior as the other 16 checks — no new schedule or delivery mechanism.

## Verification

- `kubeconform --ignore-missing-schemas` + a `kind` smoke test before touching the real cluster (same as every phase).
- Positive-reachability regression check on the seedbox stack (`docker ps` count + Jellyfin/Traefik HTTP code) immediately after the `HelmChart` is applied — this is new cluster-wide scrape traffic touching every namespace's `NetworkPolicy`, unlike prior phases' single-namespace changes.
- Real verification, not just "pods are Running": confirm Prometheus has active targets for `wedding-ui`/`plant-ui`/`node-exporter`; confirm the `agent_health_last_success_timestamp` metric appears after `agent-health`'s next real tick; confirm each of the 4 alert rules evaluates without a PromQL error (loads and evaluates cleanly — not that it fires).
- Rollback drill actually executed: `kubectl delete -k k8s/base/monitoring`, confirm the seedbox stack and both live workloads (wedding-ui, plant-ui) are completely unaffected. Lower-stakes than prior cutover drills — this phase is additive/observational, nothing depends on monitoring being up — but still gets an actual drill per this project's discipline.
- `security-audit --dry-run` against the real cluster, confirming check #17's four sub-checks produce context-appropriate findings (not false positives against the migration's actual, already-correct config).

## STOP-task scope

Lighter than prior phases: no existing live infra is being reconfigured (unlike Phase 6/Split DNS's Traefik changes), only a new namespace added and existing things scraped read-only. The one STOP gate: the moment `kube-prometheus-stack` is actually applied to the real cluster (first real resource creation in a new namespace) — same bar every phase has used for "STOP before touching the live cluster."

## File list

- `k8s/base/monitoring/namespace.yaml` — new namespace, PSA `privileged`.
- `k8s/base/monitoring/networkpolicy.yaml` — default-deny-egress + the scoped Prometheus-scrape exception.
- `k8s/base/monitoring/helmchart.yaml` — the `HelmChart` CR + `kube-prometheus-stack` values (etcd/kubeProxy disabled, retention, Grafana NodePort).
- `k8s/base/monitoring/prometheusrules.yaml` — the 4 alert rule groups.
- `k8s/base/monitoring/kustomization.yaml` — wires the above together.
- `k8s/base/kustomization.yaml` — modified to include `monitoring`.
- `agents/security_audit.py` — modified, adds check #17 (4 sub-checks) and its `K8S_CONTEXT` equivalent to the existing `SERVER_CONTEXT` pattern.
- `agents/agent_health.py` — modified, writes the textfile-collector metric on its success path.
- `docs/security-audit-agent.md` — modified, documents check #17.

## Related

- `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` — parent migration design; Phase 7 entry (line 116), decision 1 (Tailscale-only NodePort binding, what check #17's first sub-check verifies), Phase 8 entry (line 117, the ArgoCD RBAC concern check #17's RBAC audit directly serves).
- `docs/security-audit-agent.md` — the existing 16-check agent this phase extends to 17.
- `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md` — the NodePort + Tailscale MagicDNS pattern this phase's Grafana access reuses.
