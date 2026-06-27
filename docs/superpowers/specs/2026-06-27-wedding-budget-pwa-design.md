# Wedding Budget PWA — Design

**Date:** 2026-06-27
**Status:** Implemented
**Author:** eagna (Claude Code)

## Related
- [[Systems]] · [[Design Docs]]
- Sibling app: FloraPulse Plant PWA (`plant_ui/`) — same skeleton (FastAPI + Alpine PWA + SQLite).

## Problem

The user is saving toward a large wedding (~350 guests) and wants a shared, good-looking webapp that turns
"how much have we saved" into **tangible affordability** — e.g. *"we can now afford the caterer for 197 of 350
guests"* and *"flowers for all 35 tables"*. It must be reachable by their partner (a public login), and seeded
with realistic **Irish 2025/2026 wedding costs** so the numbers mean something out of the box.

## Decisions

- **Auth:** Traefik HTTP basic auth at `wedding.yopflix.world` (a 2nd htpasswd user for the partner). No in-app login.
- **Scope:** Calculator only — no LLM/chat backend, so the app is self-contained and Dockerisable.
- **Savings:** a single editable figure (no contributions log).
- **Affordability:** **priority waterfall** — pour savings into items in priority order (venue → food → ceremony/
  photo → entertainment → flowers → attire → extras → contingency), giving a clear "funded line".

## Architecture

```
wedding_ui/
  budget_model.py   BudgetConfig, CostItem, DEFAULT_ITEMS, BudgetStore, compute_budget() [waterfall]
  server.py         FastAPI: GET /api/budget, PATCH /api/config, PATCH/POST/DELETE /api/items, POST /api/reset, SPA
  templates/index.html   Alpine SPA shell, 3 tabs
  static/app.js          weddingApp() — fetches budget, posts edits, formats coverage
  static/style.css       wedding palette glassmorphism (plum/gold/blush/sage)
  static/sw.js           service worker (cache-first shell, network-first /api)
  static/manifest.json   PWA manifest + icon-192/512.png
  Dockerfile, requirements.txt
tests/test_wedding_ui_api.py   13 tests (TestClient + temp wedding.db)
```

Mirrors `plant_ui/` patterns: `Depends(get_store)` injection, `app.dependency_overrides` in tests,
`AgentDB.get_state/set_state` key-value storage.

## Data model

Stored in a dedicated `data/wedding.db` (own `AgentDB`, independent of `agents.db`) under `agent="wedding-budget"`:
- `config` → `{guests:int, seats_per_table:int, savings:float, contingency_pct:float}`
- `items`  → list of `{key, label, category, unit_cost:float, scaling:"fixed|per_guest|per_table", priority:int, note}`

`tables = ceil(guests/seats_per_table)`. Per-item total = `unit_cost × (guests | tables | 1)`.
Grand total = `subtotal × (1 + contingency_pct/100)`. Defaults seed on first read; `POST /api/reset` restores them.

### Seeded costs (Irish 2025/2026 averages, all editable)
21 line items totalling ≈ **€71.6k** before contingency, ≈ **€78.8k** with 10% at 350 guests — catering €110/head,
arrival drinks €12/head, evening food €8/head, centrepieces €70/table, photographer €2,400, videographer €2,000,
band €2,500, DJ €700, dress €2,000, rings €1,700, etc. (full table in `budget_model.DEFAULT_ITEMS`).

## Affordability waterfall (`compute_budget`)

Items sorted by `(priority, key)`. A running `pool = savings` funds each in turn:
- `pool ≥ line_total` → **funded** (pool decremented).
- `pool ≤ 0` → **unfunded**.
- otherwise → **partial**: per-unit items report `floor(pool/unit_cost)` whole units covered; fixed items can't be
  partially bought so `units_covered=0` but `pct_funded` shows progress; pool then drains to 0.

Contingency is shown as a final buffer card and folded into the goal. Top-line: `pct_funded`, `gap`, `fully_funded`.

## Frontend (3 tabs)

1. **Overview** — SVG savings-goal ring (% funded), Saved / Goal / Still-to-save stats, inline savings editor with a
   playful headline ("the caterer is covered for all 350 guests"), per-category spend bars.
2. **Afford** — the coverage cards driven by the waterfall (funded ✓ / "197 of 350 guests" / "% there").
3. **Breakdown** — editable item table grouped by category (inline unit-cost edits persist), guest/seats/contingency
   controls, add/remove custom items, reset-to-defaults, subtotal/contingency/grand-total rows.

Palette: deep plum `#3d2b3d`, champagne gold `#c9a24b`, blush `#e8b4b8`, sage `#9caf88`, ivory text; Cormorant
Garamond display + Jost body; glassmorphism panels; mobile-first.

## Deployment

Calculator-only ⇒ no `claude` CLI ⇒ Dockerised and added to the seedbox like other public services:
- `wedding_ui/Dockerfile` (build context = repo root) — copies `wedding_ui/` + `agents/{__init__,db}.py`, persists
  `wedding.db` on a named volume mounted at `/app/data`, serves uvicorn on `:8000`.
- `~/git/yopflix/seedbox/services/wedding.yaml` + a `config.yaml` entry with `host: wedding.${TRAEFIK_DOMAIN}`,
  `internalPort: 8000`, `httpAuth: true`. Deploy via `./run-seedbox.sh` (TLS via Let's Encrypt).
- Partner login: append a 2nd user to `seedbox/traefik/http_auth`.

## Testing

`.venv/bin/pytest tests/test_wedding_ui_api.py` — 13 tests: seeding, totals math, per-guest/per-table scaling,
config recompute, savings→affordability, item edit/add/delete, reset, waterfall funded/partial/unfunded semantics,
per-guest partial unit count, and validation (negative cost/guests → 422, unknown key → 404).

## File list
New: `wedding_ui/{__init__,budget_model,server}.py`, `wedding_ui/templates/index.html`,
`wedding_ui/static/{app.js,style.css,sw.js,manifest.json,icon-192.png,icon-512.png}`,
`wedding_ui/{requirements.txt,Dockerfile}`, `tests/test_wedding_ui_api.py`, this spec.
In yopflix repo: `services/wedding.yaml`, `config.yaml` edit, htpasswd user.
Updated: `CLAUDE.md` (Wedding Budget PWA section).

## Sources (Irish wedding costs 2025/2026)
weddingsonline.ie 2025 survey (avg €36,641; €107/head; ~141 guests) · steviedeeweddingdj.ie 2026 guide
(catering €90–130/head; band €2.5–2.7k; DJ €700) · surefiretrio.com 2026 (avg ~€38.5k) ·
julesbridaljewellery.com / diamondsfactory.ie item breakdown (rings €1,668; cake €618; stationery €750;
hair&makeup €456; cars €545; celebrant €504; flowers €1,025; photo €2,094; video €1,700) ·
andrewcollinsphotography.com Irish Wedding Budget Calculator 2025/2026.
