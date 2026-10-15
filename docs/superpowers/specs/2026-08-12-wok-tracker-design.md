# Way of Kings Spoiler-Free Companion Site — Design

## Problem

A friend reading *The Way of Kings* (Stormlight Archive #1) is finding the book dense — five interleaved POV threads, dozens of characters, and a large invented geography (Roshar) that's hard to place chapters in. He wants a lightweight tool: pick the chapter he just finished, and see who's in it (with a short reminder of who they are) and where on the map it happens — with a hard guarantee that nothing from a later chapter ever leaks through.

## Design Decisions

- **Progressive character bios over per-chapter blurbs.** Rather than authoring one static bio per character, each character has a small ordered list of `(chapter_id, blurb)` stages. Rendering chapter N picks — per POV character — the *latest* stage whose chapter is at or before N in book order. This makes spoiler-safety structural rather than something to manually double-check per chapter: a stage written to reflect chapter 8's reveals is physically unreachable from chapter 5's render path, because the lookup only ever walks forward through stages up to the current chapter's position.
- **Static content, no database.** All chapter/character/region data is read-only and hand-authored (`wok_tracker/data/content.py`). There's no user data on the server at all — "mark as read" progress is a client-only feature (localStorage), since the site is public with no login and progress is inherently per-visitor. This is a deliberate simplification versus the original plan (which sketched a `chapters.json` + could-reuse-AgentDB shape): a plain Python module is easier to author correctly than hand-written JSON, and there's no runtime mutation to justify a DB-backed store.
- **Simplified, non-canonical map.** The SVG map (`static/map.svg`) is a schematic grid of labeled regions (Shattered Plains, Kharbranth, Hearthstone, Jah Keved, etc.), not a reproduction of the book's real map — avoids any copyright concern and keeps the highlight logic to "toggle a CSS class on the region matching the current chapter's `location` key."
- **Hosting mirrors `wedding_ui/`.** Same FastAPI + Alpine.js single-page skeleton, same two-stage Dockerfile pattern, deployed via the yopflix seedbox stack (Docker + Traefik). Unlike `wedding_ui`, there's no `httpAuth` — this is just book content, no sensitive data, and no AgentDB dependency is baked into the image.

## Data Model

`wok_tracker/data/content.py`:
- `REGIONS`: list of `{key, label}` for the map.
- `CHARACTERS`: `{character_key: {name, stages: [(chapter_id, blurb), ...]}}` — stages must be in book order.
- `CHAPTERS`: ordered list (book order — this list *is* the canonical ordering) of `{id, number, label, part, pov: [character_key], location: region_key | None}`.

`wok_tracker/wok_model.py` resolves a requested chapter into a `ChapterDetail` (location + composed character bios) by looking up each POV character's active stage as of that chapter's position in the `CHAPTERS` list. `location=None` chapters (currently just the Prelude, set outside the book's real timeline) render with no map highlight.

## Content Coverage

All 87 entries of the book: Prelude, Prologue, Chapters 1–75, Interludes 1–9, Epilogue — with POV assignments and settings compiled from the published chapter list (cross-checked via chapter-summary sources), and hand-written progressive bios for the ~25 named POV/recurring characters. The five principal POV characters (Kaladin, Shallan, Dalinar, Adolin, Szeth) get multi-stage bios tracking their major reveals; secondary characters get a single bio locked to their first appearance, since most don't have an unfolding arc within this book.

## Files

- `wok_tracker/data/content.py` — authored content (regions, characters, chapters)
- `wok_tracker/wok_model.py` — Pydantic models + spoiler-safe composition (`list_chapters`, `get_chapter`, `list_regions`)
- `wok_tracker/server.py` — FastAPI app: `GET /api/chapters`, `GET /api/chapters/{id}`, `GET /api/regions`, `/healthz`, serves the SPA + `/static`
- `wok_tracker/templates/index.html`, `wok_tracker/static/{app.js,style.css,map.svg,manifest.json,sw.js}` — chapter picker, character cards, map highlight, localStorage read-tracking
- `wok_tracker/requirements.txt`, `wok_tracker/Dockerfile` — mirrors `wedding_ui`'s build pattern, no AgentDB dependency
- `tests/test_wok_tracker_api.py` — API tests + a spoiler-monotonicity test asserting no character's bio at chapter N-1 ever matches the bio that becomes active at chapter N
- `~/git/yopflix/seedbox/services/wok-tracker.yaml` + `config.yaml` entry — Traefik-routed Docker service, `httpAuth: false`

## Verification

- `.venv/bin/pytest tests/test_wok_tracker_api.py` — 9 tests: chapter ordering, single-chapter detail shape, 404 on unknown chapter, region consistency, index/healthz, and the spoiler-monotonicity checks (every character key referenced by a chapter is defined; every location key is a known region; stage order matches book order; bios strictly change at their stage boundary and never earlier).
- Manual: `uvicorn wok_tracker.server:app --reload`, click through chapters in a browser, confirm the map highlight and character cards update and stay consistent with the "no spoilers past this chapter" rule.
- Post-deploy: `cd ~/git/yopflix/seedbox && ./run-seedbox.sh`, then confirm `https://wok-tracker.<domain>` loads.

## Related

- Hosting precedent: [[wedding_ui]] design at `docs/superpowers/specs/2026-06-27-wedding-budget-pwa-design.md`
