# Tailscale Split DNS for `*.internal.yopflix.world` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan touches the live Traefik routing config and the live seedbox Docker stack (both in `~/git/yopflix/seedbox`, outside this repo), and requires one manual step in the Tailscale admin console (an external SaaS account, not scriptable from here).** Tasks 3, 4, 5 are marked STOP — do not execute them, or resume past them, without the human partner's explicit go-ahead in this session, given separately for each STOP. No subagent may be dispatched for any STOP task.

**Goal:** Make `plants.internal.yopflix.world` (renamed from `plants.yopflix.world`) resolve correctly on every tailnet device — phones included — via a new `dnsmasq` container and a Tailscale Split DNS route, without ever touching resolution for `wedding.yopflix.world` or any other existing public `yopflix.world` hostname.

**Architecture:** A `dnsmasq` container joins the existing yopflix seedbox Docker stack, bound to the Tailscale interface only, answering a single wildcard record (`*.internal.yopflix.world` → this host's Tailscale IP) and nothing else — no upstream/forwarding configured, since Tailscale's own client-side Split DNS proxy only ever forwards matching-suffix queries to it in the first place. The existing Traefik route gets renamed to match the new suffix. A Split DNS route added in the Tailscale admin console (manual, external) tells every tailnet device to send `*.internal.yopflix.world` queries to this new resolver.

**Tech Stack:** `dnsmasq` (Docker container, `4km3/dnsmasq` image), the existing yopflix seedbox Docker Compose stack (`~/git/yopflix/seedbox`, `services/*.yaml` + `config.yaml` + `run-seedbox.sh`), Traefik `file` provider (already in use), Tailscale Split DNS (admin console, no CLI/API path from this node).

## Global Constraints

- **Suffix is `internal.yopflix.world`, not `yopflix.world`.** Delegating the whole domain would make the new resolver authoritative for `wedding.yopflix.world` too, on every tailnet device — confirmed via `host wedding.yopflix.world` resolving today to real public Cloudflare IPs (`188.114.96.2`/`188.114.97.2`). Every task in this plan must preserve that separation.
- **`dnsmasq` gets no forwarding/upstream config, ever.** It should only ever be asked about `*.internal.yopflix.world` (Tailscale's client-side proxy filters before forwarding) — if a task adds forwarding "just in case," that defeats the design's whole safety property. Task 2's verification explicitly checks this stays true.
- **Bind to the Tailscale interface only, both UDP and TCP port 53.** Never `0.0.0.0` — same rule as every other service in this stack (confirmed convention: `services/couchdb.yaml` hardcodes the real Tailscale IP directly in its `ports:` mapping, e.g. `"100.96.86.73:5984:5984"` — this repo's established convention is to commit the literal Tailscale IP in `services/*.yaml`, unlike `ai-agents`' own `<TAILSCALE_IP>`-placeholder convention. Follow the seedbox repo's own convention here, not `ai-agents`'.)
- **No new UFW rule needed.** `Anywhere on tailscale0 ALLOW IN` already exists on this host and covers any port bound to the Tailscale interface.
- **The Traefik route rename happens in a gitignored file** (`~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml`, matches the `custom-*.yaml` gitignore pattern) — edited in place, not re-created from scratch.
- **The Tailscale admin console step cannot be scripted from this node.** No `tailscale` CLI subcommand exposes Split DNS route configuration; it's admin-console-only (`login.tailscale.com/admin/dns`).
- Every STOP task requires the human partner's own explicit go-ahead, given separately per task.

---

### Task 1: `dnsmasq` service in the seedbox stack

**Files:**
- Create: `~/git/yopflix/seedbox/services/dnsmasq.yaml`
- Create: `~/git/yopflix/seedbox/services/dnsmasq/dnsmasq.conf`
- Modify: `~/git/yopflix/seedbox/config.yaml`

**Interfaces:**
- Consumes: this host's real Tailscale IP (from `tailscale ip -4` — write it directly into `services/dnsmasq.yaml`'s `ports:`, matching `services/couchdb.yaml`'s existing convention of hardcoding it there).
- Produces: a running `dnsmasq` container answering DNS on `<TAILSCALE_IP>:53` (UDP+TCP) for `*.internal.yopflix.world` only. Consumed by Task 2 (verification) and Task 5 (the Split DNS route points at this IP:53).

- [ ] **Step 1: Get the real Tailscale IP**

```bash
tailscale ip -4
```
Note the output — you'll use it literally in Step 2 and Step 3 (this repo's own convention, per Global Constraints, unlike `ai-agents`' placeholder convention).

- [ ] **Step 2: Write the dnsmasq config**

Create `~/git/yopflix/seedbox/services/dnsmasq/dnsmasq.conf`:

```
# Answers *.internal.yopflix.world only. Deliberately no upstream/forwarding
# servers configured — Tailscale's own client-side Split DNS proxy only
# ever forwards queries matching the delegated suffix here in the first
# place, so this container never needs to (and must never) answer for
# anything else. See docs/superpowers/specs/2026-08-06-tailscale-split-dns-internal-yopflix-design.md
# in the ai-agents repo for the full reasoning.
no-resolv
no-hosts
address=/internal.yopflix.world/<TAILSCALE_IP_FROM_STEP_1>
```

Replace `<TAILSCALE_IP_FROM_STEP_1>` with the real value from Step 1.

- [ ] **Step 3: Write the service definition**

Create `~/git/yopflix/seedbox/services/dnsmasq.yaml` (same shape as `services/couchdb.yaml` — `image`, `container_name`, `restart`, `ports` bound to the real Tailscale IP, `environment`, `volumes`):

```yaml
services:
  dnsmasq:
    image: 4km3/dnsmasq:2.90-r3
    container_name: dnsmasq
    restart: always
    cap_add:
      - NET_ADMIN
    ports:
      # Tailscale IP only — never 0.0.0.0
      - "<TAILSCALE_IP_FROM_STEP_1>:53:53/udp"
      - "<TAILSCALE_IP_FROM_STEP_1>:53:53/tcp"
    volumes:
      - ./services/dnsmasq/dnsmasq.conf:/etc/dnsmasq.conf
```

Replace `<TAILSCALE_IP_FROM_STEP_1>` with the real value from Step 1 in both `ports:` lines.

- [ ] **Step 4: Enable it in config.yaml**

In `~/git/yopflix/seedbox/config.yaml`, add a new entry matching `couchdb`'s shape (find the `- name: couchdb` block and add this near it):

```yaml
  - name: dnsmasq
    enabled: true
    vpn: false
    traefik:
      enabled: false
```

- [ ] **Step 5: Commit (this repo tracks `services/*.yaml` and `config.yaml`, not the gitignored Traefik custom files)**

```bash
cd ~/git/yopflix/seedbox
git add services/dnsmasq.yaml services/dnsmasq/dnsmasq.conf config.yaml
git commit -m "feat(dnsmasq): add Tailscale-only resolver for *.internal.yopflix.world"
```

---

### Task 2: STOP — deploy and verify `dnsmasq` in isolation

**Files:** none (cluster/stack state only)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead in this session.

- [ ] **Step 2: Deploy**

```bash
cd ~/git/yopflix/seedbox
sudo ./run-seedbox.sh
docker ps --filter "name=dnsmasq"
```
Expected: `dnsmasq` container `Up`, alongside all other existing containers (this script is idempotent — only `dnsmasq` should be newly created; every other container should show its prior uptime, not a fresh restart).

- [ ] **Step 3: Positive check — it answers for the delegated suffix**

```bash
TS_IP=$(tailscale ip -4)
dig @"$TS_IP" plants.internal.yopflix.world +short
```
Expected: prints the same Tailscale IP (the wildcard record answering).

- [ ] **Step 4: Negative check — it does NOT answer for anything else**

```bash
TS_IP=$(tailscale ip -4)
dig @"$TS_IP" wedding.yopflix.world +short
echo "exit=$?"
```
Expected: empty output (no answer) — confirms decision 6 of the design doc (no forwarding/upstream configured) is actually true in the deployed config, not just design intent. If this returns an IP, STOP — that means `dnsmasq` is answering for a domain it must never touch, and Task 1's config has a bug that needs fixing before continuing.

- [ ] **Step 5: Confirm no regression on the rest of the stack**

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w "%{http_code}\n" "http://$(tailscale ip -4)/"
```
Expected: same container count and HTTP code as the Phase 6 plan's own baseline (22 containers, `404`) — `run-seedbox.sh` is idempotent and this change is additive only.

---

### Task 3: STOP — rename the Traefik route, remove the test hosts-entry

**Files (outside this repo):**
- Modify: `~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml` (gitignored, edited in place)
- Modify: `/etc/hosts` (remove one line)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 2's.

- [ ] **Step 2: Rename the Host rule**

In `~/git/yopflix/seedbox/traefik/custom/custom-plant-ui-k8s.yaml`, change:
```yaml
      rule: 'Host(`plants.yopflix.world`)'
```
to:
```yaml
      rule: 'Host(`plants.internal.yopflix.world`)'
```
(Leave the router name `plant-ui-k8s`, `entryPoints: [insecure]`, `middlewares:`, and the `loadBalancer` backend URL exactly as they are — only the `Host()` rule's domain changes.)

- [ ] **Step 3: Remove the Phase-6-testing hosts-file entry**

```bash
sudo sed -i '/plants.yopflix.world/d' /etc/hosts
grep plants /etc/hosts
```
Expected: no output from the `grep` (line removed). This entry was only ever a server-local shortcut for Phase 6 testing — leaving it in place would mask a real `dnsmasq`/Split DNS misconfiguration in Task 4/5's verification, since the server itself would keep resolving the old name locally regardless of what any other tailnet device sees.

- [ ] **Step 4: Verify the renamed route works**

```bash
TS_IP=$(tailscale ip -4)
curl -sf -H "Host: plants.internal.yopflix.world" "http://${TS_IP}/healthz"
echo
echo "--- old hostname should no longer route (no hosts entry, no DNS for it) ---"
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: plants.yopflix.world" "http://${TS_IP}/"
```
Expected: first curl prints `{"status":"ok"}` (the rename works, tested via a direct Host header — doesn't depend on DNS at all, so it isolates the Traefik-side change from the DNS-side change still pending in Task 5). Second curl's `404` is expected and fine (Traefik's default no-match response) — it just confirms the old rule is really gone, not a leftover duplicate route.

- [ ] **Step 5: Confirm no regression**

```bash
docker ps -q | wc -l
curl -s -o /dev/null -w "%{http_code}\n" "http://$(tailscale ip -4)/"
```
Expected: matches the baseline (22 containers, `404`).

---

### Task 4: Update Phase 6 docs for the rename

**Files:**
- Modify: `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md`
- Modify: `docs/superpowers/plans/2026-08-05-k3s-phase6-plant-ui-cutover.md`

**Interfaces:**
- Consumes: the rename from Task 3.

- [ ] **Step 1: Update the Phase 6 design doc**

Find every occurrence of `plants.yopflix.world` in `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md` and replace with `plants.internal.yopflix.world`. Add a one-line note where the hostname is first introduced (decision 4): "Renamed from `plants.yopflix.world` to `plants.internal.yopflix.world` — see `docs/superpowers/specs/2026-08-06-tailscale-split-dns-internal-yopflix-design.md`."

- [ ] **Step 2: Update the Phase 6 plan doc**

Find every occurrence of `plants.yopflix.world` in `docs/superpowers/plans/2026-08-05-k3s-phase6-plant-ui-cutover.md` (Task 8 and Task 9's remaining unexecuted references, plus Task 10's rollback-drill steps that haven't run yet) and replace with `plants.internal.yopflix.world`, so Phase 6's still-pending Task 10 uses the current hostname rather than a now-stale one.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md docs/superpowers/plans/2026-08-05-k3s-phase6-plant-ui-cutover.md
git commit -m "docs: update Phase 6 docs for plants.internal.yopflix.world rename"
git push origin main
```

---

### Task 5: STOP — Tailscale admin console Split DNS route (manual)

**Files:** none (external SaaS config)

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 3's.

- [ ] **Step 2: Ask the human partner to add the nameserver and split route**

Give them these exact steps (this is entirely manual — no `tailscale` CLI or API path from this node exposes Split DNS route configuration):

1. Go to `login.tailscale.com/admin/dns`.
2. Under "Nameservers", click "Add nameserver" → "Custom".
3. Enter this host's Tailscale IP (from Task 1 Step 1) as the nameserver address.
4. Check "Restrict to domain" and enter `internal.yopflix.world`.
5. Save.

- [ ] **Step 3: Check what's verifiable from this node**

```bash
tailscale dns status
```
This node's own `tailscale dns status` reflects DNS config *this device* receives from the coordination server — it may or may not show a Split DNS route that's scoped to route other devices' queries through this one, since this device is the nameserver target, not necessarily a consumer of the route. Report whatever it shows; do not assume it proves the route is live for other devices either way — Task 6 is the real verification for that.

---

### Task 6: STOP — real verification from a different tailnet device

**Files:** none

- [ ] **Step 1: Confirm go-ahead**

Do not proceed without the human partner's explicit go-ahead, given separately from Task 5's. (In practice this task *is* the human partner acting — there's no shell command to run from this server that can validate another device's DNS resolution or browser behavior.)

- [ ] **Step 2: Ask the human partner to check, from their phone or laptop (a genuinely different device, tailnet-connected, not this server)**

1. Open a browser and go to `http://plants.internal.yopflix.world`. Expected: the FloraPulse plant-ui PWA loads.
2. Immediately after, in the same browser on the same device, go to `https://wedding.yopflix.world`. Expected: loads exactly as it always has (the existing basic-auth prompt, then the wedding budget app) — this is the one check that actually proves the narrow-suffix design decision worked: a new internal-only DNS delegation must never affect this hostname's resolution.
3. Report both results back.

- [ ] **Step 3: If either check fails**

Do not attempt further changes without discussing with the human partner first — a failure on the `wedding.yopflix.world` check specifically means the Split DNS scoping didn't work as designed and needs the Tailscale admin console route re-examined (not a code fix in this repo), which is exactly the blast-radius scenario this plan's whole design exists to prevent.

---

### Task 7: Record completion

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-tailscale-split-dns-internal-yopflix-design.md`

- [ ] **Step 1: Add a completion status block**

Summarize: `dnsmasq` deployed and verified isolated (Task 2's positive/negative checks), Traefik route renamed (Task 3), Phase 6 docs updated (Task 4), Tailscale Split DNS route added (Task 5), and the real cross-device verification result from Task 6 (both the new hostname working and `wedding.yopflix.world` unaffected).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-06-tailscale-split-dns-internal-yopflix-design.md
git commit -m "docs: record Tailscale Split DNS (internal.yopflix.world) completion"
git push origin main
```

---

## Self-Review

**Spec coverage:** design decision 1 (narrow suffix) → Global Constraints + Task 3/5's exact scoping. Decision 2 (dnsmasq) → Task 1. Decision 3 (wildcard record) → Task 1 Step 2. Decision 4 (lives in seedbox repo) → Task 1's file locations. Decision 5 (Tailscale-interface-only, no new UFW) → Task 1 Step 3 + Global Constraints. Decision 6 (no forwarding) → Task 1 Step 2's config + Task 2 Step 4's explicit negative check. Decision 7 (manual admin console step) → Task 5. Verification section's four checkpoints → Task 2 (isolated dnsmasq check), Task 6 Step 2.2 (wedding.yopflix.world unaffected), Task 2 Step 4 (dig-based no-forwarding proof), rollback path (not separately tasked — see note below).

**Gap found in self-review:** the design doc's Verification section calls for confirming the rollback path is "actually instant/near-instant," not just assumed. No task currently does this. Adding it to Task 6 as an optional Step 4 would extend an already-manual, already-multi-step task; instead, note it here explicitly as **deliberately deferred** — rolling back (removing the Split DNS route, or `docker stop dnsmasq`) is low-risk to test independently at any later time, and forcing a real DNS outage test into this plan's critical path adds real disruption risk (a mistake while testing rollback could leave `internal.yopflix.world` unresolvable for longer than intended) for a check whose value is mostly reassurance rather than blocking correctness. Flagged here for the human partner rather than silently dropped.

**Placeholder scan:** `<TAILSCALE_IP_FROM_STEP_1>` in Task 1 is an intentional, explicit fill-in-the-real-value marker tied to a concrete preceding step (not a vague TBD) — consistent with how prior phases in this migration handle the same real IP substitution.

**Type/interface consistency:** the Tailscale IP obtained in Task 1 Step 1 is reused literally in Task 1 Steps 2-3, Task 2's `dig`/`curl` commands, and Task 5 Step 2's admin-console nameserver field — same value throughout. The router name `plant-ui-k8s` and backend service name in Task 3 are left untouched from Phase 6's Task 8, matching that plan's Global Constraints exactly.

## Related

- `docs/superpowers/specs/2026-08-06-tailscale-split-dns-internal-yopflix-design.md` — design this plan implements.
- `docs/superpowers/plans/2026-08-05-k3s-phase6-plant-ui-cutover.md` — the Phase 6 plan whose Task 8 created the route being renamed here, and whose still-pending Task 10 (rollback drill) should use the renamed hostname once Task 4 of this plan lands.
