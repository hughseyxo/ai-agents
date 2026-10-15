# Villa Grabau (Italy) Wedding Budget PWA — Design

**Date:** 2026-08-29
**Status:** Implemented & live (auth revised same day — see Revision 1)
**Author:** eagna (Claude Code)

## Related
- [[Systems]] · [[Design Docs]]
- Parent app: [[2026-06-27-wedding-budget-pwa-design]] — the Irish original (`wedding_ui/`), still live at `wedding.yopflix.world`.
- Sibling skeleton: FloraPulse Plant PWA (`plant_ui/`).

## Problem

The user wanted a second copy of the Wedding Budget PWA modelled on a **destination wedding at Villa Grabau**
— a 16th-century villa in a nine-hectare botanical park at San Pancrazio, just outside Lucca in Tuscany —
rather than an Irish hotel wedding, seeded with realistic average Italian costs, and hosted publicly for a
guest (`diarmo`) who must **not** get a login to the rest of the yopflix estate.

## Decisions

- **Fork, don't parameterise.** `grabau_ui/` is a copy of `wedding_ui/` rather than a shared package with two
  config profiles. The engine is ~220 LOC and the Irish app is live and in use; a fork keeps the blast radius
  of Italian changes at exactly zero. Cost: the waterfall engine now exists twice — if `compute_budget()` ever
  gains a real change, port it to both.
- **Separate everything:** own DB file (`data/grabau.db`), own `AgentDB` namespace (`grabau-budget`), own
  container, own volume, own Traefik router. Nothing is shared with the Irish app but the source lineage.
- **Scale to the venue.** Villa Grabau hosts roughly 50–140 guests (Lemon House up to ~250), so the default
  config is **120 guests / 12 tables**, not 350.
- **Destination line items.** Costs an Irish hotel wedding has no equivalent for are first-class defaults:
  Italy-based planner, the comune's civil-ceremony fee, *nulla osta* / *Atto Notorio* paperwork and sworn
  translations, guest shuttles from Lucca, a welcome dinner, a wet-weather marquee, on-site villa
  accommodation, and the mandatory **SIAE** music licence.
- **Dedicated basic auth (security).** The seedbox's `httpAuth: true` flag can only attach `common-auth@file`,
  whose users file is shared by *every* protected service on the domain — adding `diarmo` there would have
  handed them a login to Plex, Portainer, Mealie, the Irish wedding app and the rest. So the router is
  hand-written with its own `basicAuth` middleware over its own users file. Verified: `diarmo` gets 200 on
  `italy-wedding.yopflix.world` and **401** on `wedding.yopflix.world`.
- **Tuscan theme.** Cypress-green ground with terracotta/olive/warm-stone accents (the Irish app is plum/gold),
  new villa-and-cypresses PWA icons, villa-sprout brand mark.

## Costs (Tuscany 2026 mid-range averages, all editable in-app)

Default totals at 120 guests: **subtotal €109,690 · grand total €120,659** (10% contingency) ≈ **€1,005/guest**
— inside the €90k–150k band the 2026 guides give for a ~120-guest Tuscan villa wedding, and materially higher
than the Irish app's ~€79k at 350 guests, because Tuscan catering is €150–220/head against Ireland's ~€110.

Headline seeds: villa hire €12,000 · planner €9,000 · catering €185/head · bar €30/head · aperitivo €22/head ·
welcome dinner €55/head · marquee €5,000 · photographer €4,500 · band €4,000 · videographer €3,500 ·
styling & lighting €2,500 · guest shuttles €2,000 · civil ceremony €1,500 · SIAE €450 · paperwork €600.

## Architecture

```
grabau_ui/
  budget_model.py   BudgetConfig(guests=120), CostItem, DEFAULT_ITEMS (Tuscan), BudgetStore, compute_budget()
  server.py         FastAPI: /api/budget, /api/config, /api/items{,/{key}}, /api/reset, /healthz, /metrics
  templates/index.html   Alpine SPA — grabauApp(), Overview / Afford / Breakdown tabs
  static/           app.js, style.css (Tuscan palette), sw.js (grabau-fund-v1), manifest.json, icon-{192,512}.png
  Dockerfile        build context = repo root; image grabau-fund:latest
data/grabau.db      dedicated AgentDB, agent="grabau-budget", keys config + items
```

API and data model are identical to the Irish app; only the seeds, branding and DB namespace differ.

## Hosting

- **URL:** `https://italy-wedding.yopflix.world` (Cloudflare DNS added manually by the user; Let's Encrypt
  cert issued via Traefik httpChallenge).
- **Auth:** Traefik basic auth, users `cian` and `diarmo`, from `traefik/http_auth_italy` (gitignored by
  `**/traefik/http_auth*`). Not the shared `common-auth` file — see Decisions.
- **Container:** `italy-wedding`, host-bound on `<TAILSCALE_IP>:8773`, volume `configitalywedding` →
  `$HOST_CONFIG_PATH/italy-wedding`.
- **Deploy:** `cd ~/git/yopflix/seedbox && ./run-seedbox.sh`.
- **Gotcha (middleware order — broke a real visitor):** `redirect-to-https` MUST precede the auth
  middleware. With auth first, a visitor who types the bare hostname (no `https://`, i.e. every phone user)
  is challenged for credentials over **plaintext HTTP**; browsers suppress or warn on that prompt, and anyone
  who does log in is then 302'd to HTTPS and prompted a second time — it reads as "the link doesn't work".
  Note `run-seedbox.sh` generates `common-auth@file` *before* `redirect-to-https` for every `httpAuth: true`
  service, so `wedding`, `plant`, `mealie`, `portainer` etc. all still have this ordering upstream.
- **Gotcha (cost an iteration):** Traefik's file provider runs each dynamic file through Go templating, so
  `{{ env "TRAEFIK_DOMAIN" }}` must sit in a **single-quoted** YAML scalar. Escaped double quotes
  (`\"TRAEFIK_DOMAIN\"`) fail the whole file with `template: unexpected "\\" in operand`, and Cloudflare
  then returns **525** because no cert is ever requested for the host.

## Testing

`.venv/bin/pytest tests/test_grabau_ui_api.py` — 17 tests, written before the package existed (red → green).
Covers: Italian seeding (120 guests / 12 tables, ≤140 cap), totals math, the researched €90k–150k envelope,
presence of the destination-specific line items, the Tuscan €150–220 per-head catering band, per-guest/
per-table scaling, priority waterfall, whole-unit partial coverage, fully-funded flag, config/item CRUD,
404/400 paths, reset-to-defaults, DB-namespace isolation from `wedding_ui`, `/healthz`, index and `/sw.js`.
`tests/test_wedding_ui_api.py` re-run to confirm no regression in the Irish app (16 passed).

## File list

New in this repo: `grabau_ui/{__init__,budget_model,server}.py`, `grabau_ui/templates/index.html`,
`grabau_ui/static/{app.js,style.css,sw.js,manifest.json,icon-192.png,icon-512.png}`,
`grabau_ui/{requirements.txt,Dockerfile}`, `tests/test_grabau_ui_api.py`, this spec.
New in the yopflix seedbox repo: `services/italy-wedding.yaml`, `samples/custom-traefik/italy-wedding.yaml`,
`traefik/http_auth_italy`, plus a `config.yaml` entry (`traefik.enabled: false` — routing is hand-defined).
Updated: `CLAUDE.md` (Villa Grabau section).

## Sources (Villa Grabau & Tuscany wedding costs 2026)
weddingsitaly.com / exclusiveitalyweddings.com / lalista.com / distinctiveitalyweddings.com — Villa Grabau
capacity (~50–140 guests, Lemon House ~250), setting and on-site accommodation (~55 beds) ·
tuscanyweddinglist.com "Tuscany Wedding Cost Breakdown (2026 Real Prices)" — villa hire €10–25k, catering
€150–220/head, planner €6–15k, photo €5–10k, video €4–8k, band €3–8k, DJ €800–2,000, flowers €4–10k,
hair & makeup €800–2,000, cake €500–1,500, transport €500–2,000, officiant €300–800, lighting/AV €1–5k,
welcome bags €10–30/guest · giorgiafantinborghi.com "Tuscany Wedding Cost 2026: Real Budgets €40k–€250k+" —
bar €25–50/head, civil ceremony for foreign couples €2.5–5.1k (Florence), legal paperwork €300–800, guest
transport €1–3k, marquee €3–15k, SIAE licence €272–583, ceremony quartet €500–1,500 ·
emilianorusso.com / italianvenues.com 2026 guides — ~€1,000/guest all-in for a mid-range Tuscan villa wedding.


## Revision 1 (2026-08-29): basic auth → in-app passcode login

**Why.** The recipient couldn't open the site on an iPhone — "unauthorised right away", with no login prompt.
The body he saw was Traefik's bare `401 Unauthorized`, which means the browser never rendered a credentials
dialog. Two causes, both fatal to basic auth for this use case:

1. **iOS in-app browsers** (WhatsApp/Instagram/Mail webviews) don't render basic-auth prompts — they display
   the 401 body. A link shared in a chat app therefore always looks broken.
2. **Installed Home Screen PWAs** in standalone mode have the same problem — which matters a lot here,
   because this *is* a PWA meant to be installed.

A third, separate bug found on the way: the router had `italy-wedding-auth` **before** `redirect-to-https`,
so anyone typing the bare hostname was challenged for credentials over plaintext HTTP (browsers suppress or
warn on that), then bounced to HTTPS and challenged again. Fixed by reordering; note `run-seedbox.sh`
generates the same wrong order for every `httpAuth: true` service (wedding, plant, mealie, portainer…).

**What replaced it.** `grabau_ui/auth.py` + `/login`, `/logout` in `server.py`:

- One shared passcode (`GRABAU_PASSCODE`), no accounts. HTML form, so it works in every browser, in in-app
  webviews, and in an installed PWA; iOS can save it to the keychain. The input carries
  `autocapitalize="none" autocorrect="off"` — iOS otherwise capitalises the first letter and silently breaks it.
- Session = HMAC-SHA256 signed `issued_at.signature` cookie (`grabau_session`), HttpOnly, SameSite=Lax,
  `Secure` when `X-Forwarded-Proto: https`, 1-year max age. Key from `GRABAU_SECRET`; a random per-process
  key is the fallback (a restart then just logs everyone out — better than a hardcoded default).
- A middleware gates everything except `/login`, `/logout`, `/healthz`, `/sw.js`, `/static/*`, `/metrics`.
  Pages redirect to `/login` (303); `/api/*` returns 401 JSON so the SPA doesn't get HTML back.
- **Fails closed:** unconfigured passcode → 503 everywhere, never an open budget.
- Brute-force throttle: 10 failures per client IP (`CF-Connecting-IP`) per 15 minutes → 429. The passcode is
  short and human-shareable and the form is on the public internet, so this is not optional.
- Traefik's basic-auth middleware and `traefik/http_auth_italy` were deleted; the router now only does the
  HTTPS redirect.

**Incident during rollout (~2 min).** Basic auth was removed from Traefik *before* the container was actually
running the new code, because **`run-seedbox.sh` does not rebuild an existing image** — it recreated the
container from the stale `grabau-fund:latest`, which had no `/login`. For roughly two minutes the budget was
readable and writable unauthenticated. Traefik access logs for the window show exactly two requests, both
from this session's own verification curls; no third party. Contained by stopping the container, then
rebuilding explicitly. **Deploy order for anything in this repo's UI packages: `docker build` first, then
`run-seedbox.sh`.**

A second startup failure followed: `Form(...)` requires `python-multipart`, which is in the repo venv (so all
tests passed) but wasn't in `grabau_ui/requirements.txt`. The container crash-looped and Traefik served 502 —
i.e. it failed closed. Added to requirements.

**Tests.** `tests/test_grabau_ui_api.py` grew to 31: anonymous page redirect, anonymous API 401, blocked
writes, public login page/static/healthz, wrong passcode, successful login, cookie hardening, logout, forged
cookie, wrong-secret token, expired token, fail-closed 503, and throttle 429. The existing 17 now run through
a logged-in client fixture.

**Residual risk.** The passcode is `croi1` — short, and now on a public login form rather than behind
Traefik. The throttle blunts online guessing, but a longer passphrase would be better.
