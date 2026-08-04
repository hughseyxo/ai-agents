# Phase 5 — Obsidian Cutover Design

## Related

Sub-project of [[2026-08-01-k3s-migration-design]] (Phase 5 bullet). Follows [[2026-08-03-k3s-phase3-wedding-ui-cutover]] (data-migration/rollback-drill pattern) and [[2026-08-03-k3s-phase4-cronjobs-design]] (PSA-vs-hostPath/hostPort lessons, both directly relevant here). Touches the Obsidian vault system described in [[2026-06-19-obsidian-vault-backend-design]] and `docs/obsidian-vault-setup.md`.

## Problem

CouchDB + `livesync-bridge` currently run as Docker containers in the `yopflix/seedbox` Compose stack (a separate repo, `~/git/yopflix/seedbox`), not in `ai-agents` itself — the first Phase of this migration whose primary workload lives outside this repo. CouchDB is the sync backend Obsidian mobile/laptop clients connect to directly (via the self-hosted LiveSync plugin, not through `livesync-bridge`); `livesync-bridge` is a one-way-ish process mirroring `docs/`, the Claude memory folder, and `CLAUDE.md` between CouchDB and this host's disk.

## Scope

Move both workloads into the k3s cluster, replacing their Docker Compose equivalents entirely (not a dual-run — unlike Phase 4's cron agents, there's no way to run two CouchDB instances against the same client connections in parallel).

## Architecture

One `Deployment`, one Pod, two containers (`couchdb`, `livesync-bridge`), in a single new namespace (naming: `obsidian-vault`), PSA `privileged`. Both containers share the pod's network namespace — `livesync-bridge` reaches CouchDB via `localhost:5984`, no `ClusterIP` `Service` needed for internal traffic. `privileged` PSA is required regardless of storage choice: CouchDB's `hostPort: 5984` binding (not a `NodePort` — the design doc's own decision 11 deliberately keeps `service-node-port-range` at default, since ~20 host ports already live in `5000–32767`) is restricted starting at PSA `baseline`, same tier as `hostPath` volumes. Given both containers need `privileged` either way, they share one namespace rather than being split like `wedding-ui`/`ai-agents-cron` were — no PSA-scoping benefit to splitting here.

## Storage & data flow

- **CouchDB:** PVC (`local-path`), populated via a one-time data copy-in from the Docker named volume's real host path (`$HOST_CONFIG_PATH/couchdb`, resolved from `yopflix/seedbox`'s `.env`) — same pattern as Phase 3's `wedding-ui` `wedding.db` migration. CouchDB is exclusive-access (only one process touches its data files at a time), so this is a clean one-time cutover, not an ongoing-shared-state situation like Phase 4's `agents.db`.
- **`livesync-bridge`:** 3 hostPath mounts, identical host paths to the current Docker Compose config (verified against `~/git/yopflix/seedbox/services/livesync-bridge.yaml`):
  - `/home/cian/git/ai-agents/docs` (directory)
  - `/home/cian/.claude/projects/-home-cian-git-ai-agents/memory` (directory)
  - `/home/cian/git/ai-agents/CLAUDE.md` (single file)

  These stay hostPath, not migrated — they're actively edited by host-side Claude Code sessions on an ongoing basis, not a one-time snapshot. Verified safe against Phase 4's discovered EBUSY hazard: `livesync-bridge`'s actual write path (`PeerStorage.ts`'s `put()`) does `Deno.open(path, {create: true})` + `write` + `truncate` directly on the target path, not a tempfile-then-rename pattern — so the single-file `CLAUDE.md` mount doesn't hit the failure mode that broke `check-google-token.sh`'s OAuth refresh in Phase 4.

## Secrets

`livesync-bridge`'s `config.json` interpolates `${VAR}` placeholders for CouchDB credentials and the E2EE passphrase — currently supplied via Docker Compose's `env_file` mechanism, sourced from `yopflix/seedbox`'s gitignored `.env.custom` (`LIVESYNC-BRIDGE_*`-prefixed keys). In k8s this becomes a scoped `Secret`, `envFrom`'d into the `livesync-bridge` container only. CouchDB's own admin credentials are supplied the same way Docker does today, via its own env vars on the `couchdb` container — not shared with `livesync-bridge`'s Secret, since neither container needs the other's full credential set.

## Cutover procedure

Unlike Phase 3's Traefik-mediated cutover (reversible via a config file with no real resource contention), this one has genuine port contention: only one process can bind `<TAILSCALE_IP>:5984` at a time, so there's an unavoidable downtime window, not an instantly-reversible file swap.

1. **Dry run against a data copy**, without touching the live Docker containers: verify the new pod starts, CouchDB auth works, `livesync-bridge` connects to it — all against a *copy* of the real data, so there's no live risk during validation.
2. **Real cutover:** stop the Docker `couchdb` + `livesync-bridge` containers, copy the real data into the PVC, start the k8s pod bound to the same port, verify a live Obsidian client round-trip (a note created on a phone/laptop lands on this host's disk via the in-cluster bridge) — matching the parent design doc's own Verification bar: *"Obsidian round-trip proven twice, Docker then in-cluster (Phase 0, Phase 5)."*
3. **Rollback drill, actually executed** (matching every prior phase's bar): stop the k8s pod, restart the Docker containers, verify a round-trip works again on Docker, then re-cut-over to leave the phase in its intended end state.

## Verification

- `kubeconform --ignore-missing-schemas` in CI, same gate as every other `k8s/` addition.
- A real Obsidian client round-trip (not just a DB query) proves the cutover works end-to-end, in both directions if practical (disk→CouchDB and CouchDB→disk).
- Rollback drill executed both directions, not just documented.
- Positive-reachability check on the other 21 Docker containers on this host, same pattern as every prior phase (`docker ps` count, Jellyfin-via-Traefik HTTP code) — a Phase 5 mistake must not take down anything else in the seedbox stack.

## File List

- Create: `k8s/base/obsidian-vault/namespace.yaml`, `pvc.yaml`, `deployment.yaml`, `secret.yaml` (template only — real values applied at cutover time, never committed), `kustomization.yaml`
- Modify: `k8s/base/kustomization.yaml`
- Modify: `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` (Phase 5 completion status note, at the end)
- No changes to `~/git/yopflix/seedbox` beyond eventually removing the now-redundant `couchdb`/`livesync-bridge` Compose service definitions (a cleanup step, likely deferred to the implementation plan rather than this design)
