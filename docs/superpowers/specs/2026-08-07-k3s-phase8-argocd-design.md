# Phase 8: ArgoCD (GitOps Continuous Delivery) — Design

## Problem

Every prior phase of the k3s migration ends the same way: a human runs `kubectl apply -k k8s/base` (or a subpath) by hand after reviewing a diff. That's fine at the current scale but doesn't scale with intent — the parent migration doc calls out ArgoCD as the mechanism to close that loop: the cluster should reconcile itself against what's committed to `k8s/base`, with drift detection, sync history, and a UI, instead of relying on someone remembering to re-apply.

Phase 7 (Observability) explicitly built its `security-audit` check #17 (NodePort exposure, kubeconfig permissions, RBAC cluster-admin audit, NetworkPolicy presence) as "the actual prerequisite gate Phase 8 needs before turning on ArgoCD auto-sync" — this phase is that gate's first real consumer.

## Decisions

**1. Delivery: HelmChart CRD.** ArgoCD installs via k3s's built-in `HelmChart` CR (`apiVersion: helm.cattle.io/v1`), pulling the upstream `argo-cd` chart — same mechanism Phase 7 used for `kube-prometheus-stack`. No manual Helm CLI, no custom image, no CI build-matrix change, consistent with every prior phase's delivery pattern for third-party components.

**2. Namespace: `argocd`, PSA `baseline`.** No hostPath/hostPort workloads here (unlike `monitoring`'s `node-exporter`), so `baseline` is sufficient — no `privileged` label needed.

**3. Scope: everything in `k8s/base`, one Application, no app-of-apps.** A single ArgoCD `Application` points at `k8s/base` as a whole (kustomize build), covering the top-level `namespace.yaml`/`networkpolicy.yaml` plus `wedding-ui`, `cronjobs`, `plant-ui`, `monitoring`. This matches how the repo is applied today (`kubectl apply -k k8s/base` as one operation) and keeps the object count low for a homelab of this size. Rejected: app-of-apps (one parent Application generating 5 children) — more moving parts than this scale justifies; revisit only if independent per-component sync approval becomes a real need.

**4. `syncPolicy: manual` for month one.** Per the parent migration doc: "Without Phase −1's branch protection, auto-sync from a public repo means landing a commit on `main` is code execution on this host." ArgoCD detects and displays drift (`OutOfSync`) but never applies it automatically. Syncing is a deliberate action (`argocd app sync` or the UI's Sync button), same review discipline as today's manual `kubectl apply`, but now with a diff view and history instead of trusting memory.

**5. ArgoCD never self-manages.** `k8s/base/argocd/` (ArgoCD's own manifests: namespace, NetworkPolicy, HelmChart, AppProject, RBAC, the Application object itself) is a **sibling directory to `k8s/base`, not a member of it** — never listed in `k8s/base/kustomization.yaml`'s `resources:`, never referenced by the Application's own `source.path`. It is applied the same way it always has been: `kubectl apply -k k8s/base/argocd`, by hand, at bootstrap and on any future change to ArgoCD's own config. This is a direct, literal reading of the parent doc's "never self-manage `k8s/platform/argocd`" constraint (the actual path differs slightly — `k8s/base/argocd` vs the doc's placeholder `k8s/platform/argocd` — but the constraint itself is unchanged: ArgoCD's own manifests are never inside the tree ArgoCD watches).

**6. Access: NodePort 30803 + Tailscale, TLS on.** `argocd-server` exposed as `NodePort`, bound to the Tailscale interface only — same trust boundary as Grafana (30802), plant-ui, wedding-ui. Port `30803` (next free port in this project's NodePort range). Unlike those, ArgoCD keeps TLS on (`server.insecure: false`, the chart default) — a self-signed cert, accepted once by browser/`argocd` CLI, same trust model already accepted for `kubectl`'s connection to the apiserver in this project. Chosen over plain HTTP because ArgoCD's server holds write credentials to the cluster and to the source repo; the other NodePort services here are read-mostly UIs behind Tailscale's own encryption, a materially lower-stakes default to relax.

**7. AppProject: explicit scope, blacklisted cluster resources.** One `AppProject` (not the built-in `default`) with:
- `sourceRepos`: this repo's GitHub URL only
- `destinations`: the 5 target namespaces (`ai-agents`, `ai-agents-cron`, `plant-ui`, `wedding-ui`, `monitoring`) — `argocd` itself is deliberately NOT a destination, per Decision 5
- `clusterResourceBlacklist`: `[{group: rbac.authorization.k8s.io, kind: ClusterRole}, {group: rbac.authorization.k8s.io, kind: ClusterRoleBinding}]` — no Application under this project can ever create or modify a `ClusterRole`/`ClusterRoleBinding`, closing off the most direct path to privilege escalation via a malicious/compromised commit

**8. RBAC: explicit per-namespace Roles, no cluster-admin.** ArgoCD's default install grants its controller cluster-admin; this project replaces that with:
- A `Role` + `RoleBinding` in each of the 5 target namespaces, granting only what ArgoCD needs there: full CRUD on `deployments`, `cronjobs`, `services`, `configmaps`, `secrets`, `networkpolicies`, `poddisruptionbudgets`; read-only (`get/list/watch`) on `pods`, `replicasets`, `jobs` (needed for ArgoCD's health/status display, not for reconciliation)
- One `ClusterRole` + `ClusterRoleBinding` limited to `get/list/watch/create/patch` on `Namespace` objects only — required because `Namespace` is itself cluster-scoped (the top-level `namespace.yaml` and each component's own `namespace.yaml` need this), but scoped to exactly that one resource type, nothing else
- Bound to the `argocd-application-controller` service account (the one that actually applies manifests); `argocd-server`'s own service account gets no additional RBAC beyond what the chart grants it for UI/API operation

**9. Bootstrap sequence (manual, one-time):** install the HelmChart → read the auto-generated `argocd-initial-admin-secret` for first login → change the admin password (or configure alternate auth — out of scope for this phase, default admin account is acceptable for a single-operator homelab) → **delete `argocd-initial-admin-secret`**. This deletion is a required step of the bootstrap task, not an optional hardening step — check #18 (below) verifies it stayed deleted.

**10. `security-audit` check #18: ArgoCD posture.** Three sub-checks, following check #17's established pattern (`self._finding(...)`/`self._pass(...)`):
- `argocd-initial-admin-secret` does not exist in the `argocd` namespace (Secret was deleted post-bootstrap, hasn't silently regenerated)
- The `k8s-manifests` Application's `spec.syncPolicy.automated` field is absent/null (still manual — nobody flipped it to auto-sync)
- No `ClusterRoleBinding` grants `argocd-application-controller` or `argocd-server` service accounts `cluster-admin` (RBAC hasn't drifted back to the chart's cluster-admin default — reuses the same detection logic as check #17's RBAC sub-check rather than duplicating it)

**11. `exec.enabled: false`.** ArgoCD's optional web-terminal-into-pods feature stays disabled (chart default is already `false`; this is an explicit `valuesContent` entry to make the choice visible in the manifest rather than relying on an upstream default that could change).

## File List

**New:**
- `k8s/base/argocd/namespace.yaml` — `argocd` namespace, PSA `baseline`
- `k8s/base/argocd/networkpolicy.yaml` — default-deny-egress, destination-scoped (pod CIDR `10.42.0.0/24` + Tailscale CGNAT `100.64.0.0/10` + DNS on 53) — `argocd-repo-server` needs outbound HTTPS to `github.com` to pull this repo, which is why the CGNAT-only rule from `monitoring`'s NetworkPolicy isn't sufficient here; the DNS rule plus an unrestricted-port-443 egress rule (same shape as `monitoring`'s, since GitHub's IPs aren't in the pod CIDR) covers it
- `k8s/base/argocd/helmchart.yaml` — `HelmChart` CR: `server.service.type: NodePort` / `nodePort: 30803`, `server.insecure: false`, `configs.params."server.insecure": "false"`, `configs.cm."exec.enabled": "false"`, controller RBAC creation disabled where the chart allows it (using this project's own `rbac.yaml` instead)
- `k8s/base/argocd/appproject.yaml` — the `AppProject` from Decision 7
- `k8s/base/argocd/rbac.yaml` — the per-namespace Roles/RoleBindings + the one Namespace-scoped ClusterRole/ClusterRoleBinding from Decision 8
- `k8s/base/argocd/application.yaml` — the single `Application` object (`spec.source.path: k8s/base`, `spec.syncPolicy: {}`  — no `automated` key)
- `k8s/base/argocd/kustomization.yaml` — lists the above 5 files (+ namespace.yaml), deliberately never referenced by `k8s/base/kustomization.yaml`

**Modified:**
- `agents/security_audit.py` — check #18 (`_check_argocd_posture` + `K8S_CONTEXT` additions if needed for the `argocd` namespace/`Application` kubectl calls)
- `docs/security-audit-agent.md` — row 18 (gitignored, local-only)
- `tests/test_security_audit.py` — `TestArgoCDPosture` test class, 3+ tests
- `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` — Phase 8 completion paragraph, once done

## Verification

- `kubeconform --ignore-missing-schemas` in CI (existing pattern)
- `kind` smoke test — `Application`/`AppProject` CRDs won't exist in plain `kind` (same documented limitation as Phase 7's `HelmChart`/`PrometheusRule`: manifest acceptance, not runtime behavior)
- **STOP:** first real bootstrap onto the live cluster — install the HelmChart, wait for ArgoCD pods healthy, delete the initial-admin secret, confirm the `k8s-manifests` Application shows `OutOfSync` (or `Synced`, if it happens to match) against the *current* live state without ever auto-applying anything
- **STOP:** first manual sync — trigger it explicitly, confirm the resulting cluster state matches `k8s/base` exactly, confirm a second sync immediately after is a no-op (`Synced`, no diff)
- Rollback drill: `kubectl delete -k k8s/base/argocd` — confirm the 5 managed workloads (wedding-ui, cronjobs, plant-ui, monitoring, and the base namespace/networkpolicy) are completely unaffected and keep running; only the ArgoCD controller itself disappears; re-apply cleanly
- `security-audit` check #18 passes against the live cluster (all 3 sub-checks `pass`)

## Related

- Parent: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` (Phase 8 entry, line 119)
- Prerequisite: `docs/superpowers/specs/2026-08-06-k3s-phase7-observability-design.md` (check #17, the exposure-surface gate this phase builds on)
