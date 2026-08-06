# Tailscale Split DNS for `*.internal.yopflix.world` — Design

## Problem

Phase 6 of the k3s migration (plant-ui cutover) added a new Traefik route, `plants.yopflix.world`, restricted to Tailscale-only reachability via an `ipAllowList` middleware. That route works correctly for anyone who can resolve the hostname to this host's Tailscale IP — but nothing currently provides that resolution except a manual `/etc/hosts` line added to the server itself for testing. Phones especially can't practically get a hosts-file entry without root/jailbreak, so the route is effectively unusable from the devices that matter most for a daily-use PWA.

## Current state (as found)

- Tailscale MagicDNS is enabled tailnet-wide (suffix `tailed77a8.ts.net`), but MagicDNS only auto-assigns names within that suffix — it cannot register an arbitrary custom domain like `plants.yopflix.world`.
- Tailscale's own "Split DNS Routes" (admin console, `login.tailscale.com/admin/dns`) is currently empty — nothing delegates any domain to a custom resolver today.
- `yopflix.world` is **not** an internal-only domain. `host wedding.yopflix.world` and `host yopflix.world` both resolve today via real public DNS (Cloudflare proxy IPs, `188.114.96.2`/`188.114.97.2`) — this domain is actively used for legitimately public services.
- The yopflix seedbox stack (`~/git/yopflix/seedbox`, a separate git repo) is Docker-Compose-driven via a `run-seedbox.sh` + `config.yaml` generation pattern; Traefik's own dynamic config is generated the same way. New services in that stack follow a `services/<name>.yaml` convention.

## Decisions

1. **Delegate a narrow subdomain, `internal.yopflix.world`, not all of `yopflix.world`.** Tailscale Split DNS delegates an entire suffix to a custom resolver, for every tailnet device, at once. Delegating all of `yopflix.world` would make the new resolver authoritative — for every tailnet device — for `wedding.yopflix.world` too, silently overriding its real public Cloudflare resolution. A bug or outage in the new resolver would then break an already-working, publicly-used hostname for every tailnet device, not just the new one. Scoping to `internal.yopflix.world` means a broken resolver can only ever affect new internal-only hosts under that suffix — every existing hostname keeps resolving via normal public DNS, completely unaffected, always. Consequence: `plants.yopflix.world` (set up in Phase 6's Task 8) is renamed to `plants.internal.yopflix.world`.

2. **`dnsmasq`, not CoreDNS.** The need is a single static wildcard answer, not a plugin pipeline. `dnsmasq`'s entire config for this is one line. Matches this migration's general bias toward the smallest tool that does the job (e.g. hostPath over a PVC where a PVC wasn't structurally required).

3. **Wildcard record, not one record per hostname.** `address=/internal.yopflix.world/<TAILSCALE_IP>` resolves *any* `*.internal.yopflix.world` name, not just `plants`. Any future Tailscale-only route under this suffix works with zero config changes to `dnsmasq` — avoids needing to touch this again for something that's free to make generic now.

4. **Lives in the yopflix seedbox stack, not the `ai-agents` repo.** Follows the existing `services/<name>.yaml` + `config.yaml` + `run-seedbox.sh` convention that Traefik itself already uses in that repo — this is infrastructure serving the whole seedbox's Tailscale-only routing, not something specific to `ai-agents`.

5. **Bound to the Tailscale interface only, port 53.** Same reasoning as every Tailscale-bound service in this migration (decision 1 of the parent migration design: never `0.0.0.0` on a box with a public IP). No new UFW rule needed — the existing `Anywhere on tailscale0 ALLOW IN` rule already covers any port on that interface.

6. **`dnsmasq` needs no forwarding/recursion logic.** Tailscale's Split DNS operates client-side: each tailnet device's own Tailscale DNS proxy inspects the query name and only forwards queries matching the delegated suffix (`internal.yopflix.world`) to the custom nameserver — everything else still goes to the device's normal system DNS, untouched. `dnsmasq` here will only ever receive queries it's actually meant to answer; it never becomes a path for, or a single point of failure on, any other domain.

7. **Manual, one-time step — cannot be scripted from here.** Adding the nameserver and the Split DNS route (`internal.yopflix.world` → this host's Tailscale IP) requires the Tailscale admin console (`login.tailscale.com/admin/dns`) — no `tailscale` CLI subcommand or node-level mechanism exposes this. Same category as the GHCR PAT generation and Google OAuth setup steps already documented elsewhere in this project.

## Verification

- `plants.internal.yopflix.world` resolves to the Tailscale IP and the plant-ui `/healthz` endpoint responds, from a genuinely different tailnet device (not the server itself) — proves the whole chain end-to-end, not just the server-local `/etc/hosts` shortcut used during Phase 6 testing.
- **`wedding.yopflix.world` (and `yopflix.world` generally) still resolve via real public DNS, completely unaffected**, checked from the same tailnet device right after the Split DNS route is added — this is the one check that actually validates decision 1's whole reason for existing; skipping it would leave the main risk this design exists to avoid unverified.
- `dig`/`host` against the new `dnsmasq` container directly (bypassing Tailscale's client-side routing) to confirm it only answers for `*.internal.yopflix.world` and correctly refuses/doesn't-serve anything else — confirms decision 6's "no forwarding logic" claim is actually true in the deployed config, not just the design intent.
- A rollback path: removing the Split DNS route in the Tailscale admin console (or stopping the `dnsmasq` container) immediately reverts every tailnet device to normal DNS resolution for `internal.yopflix.world` names — worth confirming this is actually instant/near-instant (Tailscale DNS config typically propagates to clients quickly, but this hasn't been verified against this specific tailnet) rather than assumed.

## Related

- `docs/superpowers/specs/2026-08-05-k3s-phase6-plant-ui-cutover-design.md` — the Traefik route (`plants.yopflix.world`, being renamed here) this design provides resolution for.
- `docs/superpowers/specs/2026-08-01-k3s-migration-design.md` — parent migration design; decision 1 (Tailscale-only binding) and decision 2 (no blanket UFW rules) both apply directly here.
