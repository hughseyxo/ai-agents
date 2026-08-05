# Phase 6 (plant-ui) k3s Cutover — Design

## Problem

Phase 6 of `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` moves FloraPulse (`plant_ui/`) — the daily-use plant-management PWA — off host systemd and into the k3s cluster. It's deliberately the last of the "prove the patterns" phases (after wedding-ui, CronJobs, and the paused Obsidian work) because it's the highest-daily-use, deepest `claude`-CLI-dependent workload in the whole migration: it runs the CLI directly (not just via MCP) for both multi-turn garden chat and Opus vision diagnostics, and a mistake here is felt immediately, every day.

Phase 5 (Obsidian/CouchDB+livesync-bridge) is separately paused on an unrelated third-party sync-engine bug (see the migration design doc's Phase 5 status note). That doesn't block Phase 6 — Phase 6 reuses the Deployment/Secret/`claude`-CLI-in-pod patterns already proven in Phases 3–4, nothing from Phase 5.

## Current state (as found)

- `plant_ui/server.py` — FastAPI + Alpine PWA, port 8765, bound `0.0.0.0`, reachable today at `http://<TAILSCALE_IP>:8765`, installed as a home-screen PWA icon on at least one device.
- No `Dockerfile` exists for plant-ui yet. `Dockerfile.runner` (Phase 1's cron-agent image) explicitly excludes `plant_ui/` and `telegram-bot/` — that comment predates this phase's reordering decision and needs updating regardless of which image strategy Phase 6 picks.
- Cross-cutting dependency: `plant_ui/server.py` imports `telegram-bot/claude_backend.py` via a `sys.path.insert` hack (`REPO_ROOT / "telegram-bot"`), used for Opus vision diagnostics (`assess_image`, `VISION_MODEL`). It also imports `agents.plant_model`, `agents.plant_profiles`, `agents.plant_assessment`, `agents.photo_batch`.
- `plant_ui/chat_backend.py` shells out to `claude -p --dangerously-skip-permissions --mcp-config plant_ui/garden_chat_mcp.json --strict-mcp-config [--resume <session_id>]`, `cwd=REPO_ROOT`. `garden_chat_mcp.json` spawns `mcp-servers/concierge_server.py` as a stdio child process — not a separate network service, so no additional in-cluster service dependency.
- `data/agents.db` (SQLite, via `PlantStore`) is genuinely shared mutable state: 5 of 6 cron agents are still on the host crontab (not cut over in Phase 4), and `PlantAgent` (hourly weather/intelligence run) also still runs host-side. `data/` also holds `plant-photos/<slug>/` (photo history) and `plant-photo-batches/<job_id>/` (batch job scratch, resumed via a FastAPI `lifespan` hook after any restart — systemd today, must keep working under k8s).
- Docs writes: `agents/plant_profiles.py` → `docs/plants/<slug>.md`; `agents/garden_notes.py` → `docs/plant-observations/<slug>/...` and `docs/garden-knowledge/<topic>.md`. `chat_backend.py` additionally gets read-only `docs/` access via `--add-dir docs/` for the knowledge base. No other `docs/` subdirs are written by plant-ui.
- No `/healthz` endpoint exists in `plant_ui/server.py` today (unlike `wedding_ui/server.py`, which got one in Phase 0).
- `/srv/k3s-claude-home` already exists on the host (`ls` confirms `.claude/`, `.claude.json`, real credentials) from Phase 0's credential-isolation spike — provisioned but never actually mounted into a live workload yet (the one live CronJob, `agent-health`, is LLM-free per the migration modes table).
- Traefik (`~/git/yopflix/seedbox/traefik/`) publishes `80:80`/`443:443` with no host-IP restriction in `services/traefik.yaml` — confirmed it's reachable on the host's public IP, not Tailscale-only by default. No `ipAllowList`/`ipWhiteList` middleware exists anywhere in the current Traefik config.

## Decisions

1. **New dedicated image, `ai-agents-plant-ui`.** Own `Dockerfile` (multi-stage, non-root — following `wedding_ui/Dockerfile`'s already-hardened pattern), own GHCR repo (`ghcr.io/hughseyxo/ai-agents-plant-ui`, private, same policy as the other two images), pinned by digest. Copies `plant_ui/`, `agents/`, `mcp-servers/`, and specifically `telegram-bot/claude_backend.py` (not the rest of `telegram-bot/`). `claude` CLI pinned to the same version as `Dockerfile.runner`. Repo baked in at `/home/cian/git/ai-agents` (migration decision 8 — a different path silently breaks MCP tool resolution). `runAsUser/runAsGroup: 1001` (decision 7).

   *Rejected: extending `ai-agents-runner`.* Would save one Node/claude-CLI-install layer but couples plant-ui's release cadence to the cron agents' and blurs `Dockerfile.runner`'s stated scope. Per-workload images is the pattern wedding-ui already established; staying consistent is worth the duplicated base layer.

2. **New namespace `plant-ui`, PSA `privileged`.** Required by the hostPath mounts in decision 3 (same reasoning as `ai-agents-cron` in Phase 4 — hostPath is forbidden under both `baseline` and `restricted`). Own default-deny-egress `NetworkPolicy` (DNS + 443 allow-listed, matching every other namespace in this migration).

3. **Storage — hostPath throughout, no PVC.** Rejected a PVC-and-copy-in approach (wedding-ui/couchdb's pattern) because `data/agents.db` is still actively written by host-side cron agents and `PlantAgent` — a PVC would fork that state the moment it diverged from the host's copy, silently breaking watering-schedule accuracy. Mounts:
   - `data/` (whole directory: `agents.db`, `plant-photos/`, `plant-photo-batches/`; nothing credential-bearing lives there) — `hostPath`, `type: Directory`, read-write.
   - `docs/` — `hostPath`, `type: Directory`, **read-only**, for the chat backend's `--add-dir docs/` knowledge-base access.
   - `docs/plants`, `docs/plant-observations`, `docs/garden-knowledge` — three additional `hostPath` mounts, `type: Directory`, read-write, at nested paths under the read-only `docs/` mount (the only subdirs plant-ui actually writes). Standard Kubernetes technique — a more specific `mountPath` overlays a broader one — but a **new precedent for this migration**: checked `k8s/base/cronjobs/agent-health.yaml` and confirmed no existing manifest does this yet (it only mounts `data/`, nothing under `docs/`), so this needs its own kubeconform/kind validation in the implementation plan rather than copying a proven pattern.
   - `/srv/k3s-claude-home` — `hostPath`, `type: Directory`, read-write. Reuses the already-provisioned, already-authenticated isolated Claude identity from Phase 0 rather than provisioning a fresh PVC and requiring a brand-new interactive `claude` login. This is also what gives `chat_backend.py`'s `--resume` multi-turn sessions persistence across pod restarts — Phase 0 provisioned this identity but no live workload has mounted it yet (`agent-health`, the one live CronJob, is LLM-free).

   **Explicit judgment call, stated honestly:** the migration design doc's storage section says a writable `docs/` mount should never share a pod with "the separate credential directory." That rule was written with Gmail/Calendar/Drive OAuth token files in mind — credentials whose *capability* (send email, read calendar) makes them dangerous to co-locate with a pod that has any untrusted-content exposure. `/srv/k3s-claude-home`'s `.credentials.json` is the Anthropic session auth needed to run the `claude` CLI at all; it has no such exfiltration-capable scope. Plant-ui structurally cannot avoid mounting both (it's one FastAPI process doing file I/O and spawning `claude` CLI subprocesses), so this phase treats the two credential classes as genuinely different risk categories rather than silently ignoring the stated rule.

4. **Networking — Traefik + Tailscale-only `ipAllowList`, new hostname `plants.yopflix.world`.** A `hostPort`-based approach (reusing the exact `<TAILSCALE_IP>:8765` address, no PWA reinstall) was considered and rejected in favor of centralizing on the same Traefik pattern Phase 3 already proved for wedding-ui, per explicit user preference — accepting that every device's installed PWA icon needs reinstalling to the new address either way. ClusterIP Service in the `plant-ui` namespace; new Traefik dynamic-config route (`traefik/custom/custom-plant-ui-k8s.yaml`, gitignored per the `custom-*.yaml` convention that already keeps literal Tailscale IPs out of tracked files) matching `Host(`plants.yopflix.world`)`; new `ipAllowList` middleware scoped to Tailscale's CGNAT range (`100.64.0.0/10` — confirmed no such middleware exists in the current config, this is new). `plants.yopflix.world` needs to resolve to `<TAILSCALE_IP>` via Tailscale MagicDNS or a per-device hosts-file entry — it is deliberately not public DNS, since the whole point is Tailscale-only reachability.

   **No basic auth.** Unlike wedding-ui, plant-ui has no auth layer today — Tailscale reachability *is* the access control. The `ipAllowList` middleware replicates that exact trust boundary; adding basic auth now would be new daily-use friction with no corresponding regression being fixed.

5. **Add `/healthz` to `plant_ui/server.py`.** Doesn't exist today. Needed for liveness/readiness probes; matches the precedent `wedding_ui/server.py` set in Phase 0.

6. **`imagePullSecret` in the new namespace.** Same GHCR PAT already used for wedding-ui/CronJobs, just a new `kubernetes.io/dockerconfigjson` Secret scoped to the `plant-ui` namespace (Secrets don't cross namespaces).

## Verification

- A real chat exchange (not just `/healthz`) against both the garden-scope and a plant-scope chat thread, confirming `--resume` continuity survives a pod restart (kill the pod, send a follow-up message referencing earlier context, confirm it remembers).
- A real photo upload (single-photo path, not batch, for the dry run — batch is lower-risk to prove second since its job-state resume mechanism is already exercised by every systemd restart today) confirming Opus vision assessment still works and writes an observation note to the real `docs/plant-observations/` path.
- A real watering-record write (e.g. via the PWA's "mark watered" action) confirming `data/agents.db` writes land in the same file the host-side `PlantAgent` and cron agents read — not a forked copy.
- Traefik route reachable at `https://plants.yopflix.world` from a Tailscale-connected device, confirmed unreachable (`ipAllowList` blocks, not just DNS-invisible) from an off-Tailscale connection — same negative-exposure-test rigor as Phase 2's firewall checkpoint and Phase 3's wedding-ui cutover.
- Positive-reachability regression check on the rest of the seedbox stack (Jellyfin/Traefik) immediately after the Traefik config change, same as every other phase that's touched Traefik.
- A rollback drill actually executed (not just documented): revert to the systemd service, confirm it still serves the real untouched data, then re-cut-over — same pattern as every prior phase's rollback drill.

## Related

- `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` — parent migration design; Phase 6 entry, decisions 1/3/7/8/9/11, storage/pod-security architecture sections.
- `docs/superpowers/specs/2026-07-12-batch-plant-photo-upload-design.md` — batch photo job resume mechanism this phase must keep working under k8s pod restarts.
