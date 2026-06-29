# Plant assessment — prioritised care actions

## Problem
After a photo health assessment, the only actionable output was the watering
recommendation (`immediate`/`on_schedule`/`delay`) plus an optional frequency
change. Everything else a plant might need — pruning, light/placement, pest
treatment, fertiliser, repotting, humidity — was buried in the free-text summary
with no structure, priority, or persistence. "Next steps" were weak.

## Design decisions
- **New structured field `care_actions`** in the assessment JSON: a list of
  `{action, priority, reason}` where `priority ∈ {high, medium, low}`. The model
  is instructed to give 1–4 concrete, doable steps ordered most-important-first,
  to go beyond watering, and to *not* restate the watering recommendation. Empty
  list only when the plant genuinely needs nothing.
- **Render** the actions in the assessment display as a `*Next steps:*` block,
  highest priority first, with a priority dot (🔴/🟡/🟢).
- **Persist** the actions to the plant profile doc deterministically (server-side
  formatting, not relying on the LLM to duplicate them into `profile_notes`).
  They are appended under `## Health Assessments` as a
  `**Recommended next steps:**` block, so they survive over time and are visible
  in chat context (the profile doc is folded into the garden/plant chat prompt).
- **Both photo paths** (FloraPulse PWA and Telegram concierge bot) share the
  prompt file and got mirrored display + persistence logic.
- **"Discuss next steps" button** (PWA only): the assessment panel has a button
  that opens the existing plant-scoped chat seeded with the recommended actions,
  so the owner can talk the steps through with the assistant. It auto-sends an
  opening message built from `care_actions`; the chat's profile context already
  carries the persisted next steps.

## Architecture / data flow
1. `agents/prompts/plant_photo_assessment.md` — prompt now emits `care_actions`.
2. Vision call (Opus 4.8 via `claude_backend.assess_image`) returns the JSON.
3. `_build_assessment_display()` renders the `*Next steps:*` block (sorted by
   priority).
4. `_format_care_actions_for_profile()` builds the markdown block; it is
   concatenated onto `profile_notes` and written via
   `write_health_assessment()`.
5. The PWA returns `display_text` + `parsed` (incl. `care_actions`) to the SPA,
   which renders `display_text` as markdown. The bot sends `display_text` to
   Telegram.

## File list
- `agents/prompts/plant_photo_assessment.md` — added `care_actions` field + guidance.
- `plant_ui/server.py` — `_format_care_action_lines`, `_format_care_actions_for_profile`,
  display + persistence wiring, fallback parser key.
- `telegram-bot/bot.py` — mirrored helpers, display + persistence wiring, fallback key.
- `tests/test_plant_ui_api.py` — `test_build_assessment_display_includes_care_actions`,
  `test_photo_care_actions_persisted_to_profile`.

## Related
- [[FloraPulse Plant PWA]] · concierge bot photo/vision path.
