# Librarian Recommendations — 2026-06-19

## Problem
The librarian audit (re-enabled this session, now scheduled Sun audit / Mon–Sat watch)
produced 10 pending proposals. Several were already implemented; the rest are small
hardening/refactor fixes. This doc records what was applied, held, and why.

## Applied

| ID | Target | Change |
|----|--------|--------|
| `5cd82277` | `agents/news_briefing.py` | `_parse_rss` now logs the exception to stderr before returning `[]`, instead of silently swallowing malformed-feed errors. |
| `feab14e7` | `telegram-bot/tools.py` | `note_plant_observation` now reads + rewrites the profile doc via `write_profile_atomic` (temp-file + `os.replace`) instead of a non-atomic `open(path, "a")`, so it can't interleave with the plant-agent's atomic rewrites. |
| `7fb3d866` | `agents/plant_agent.py` | `_apply_intelligence_output` now calls the shared `append_intelligence_note` helper for both intelligence notes and pruning entries, removing two hand-rolled markdown-insert blocks. Behaviour-preserving (identical insertion format). |
| `47e99c39` | `skills/mealsave/mealsave.py` | `llm_extract` passes the prompt to `claude`/`agy` via **stdin** (`input=prompt`) instead of as a `-p <prompt>` argv, avoiding `Argument list too long` on large recipes and prompt leakage into process logs. Matches the concierge backend pattern. |

### Test impact
- `tests/test_plant_agent.py` (3 `TestApplyIntelligenceOutput` tests) updated to also patch
  `agents.plant_profiles.PLANTS_DIR`, since the helper resolves paths via that module
  rather than `agents.plant_agent.REPO_ROOT`.
- Verified: `test_news_briefing` + `test_synthesize` (66), `telegram-bot/test_tools` (81),
  `test_mealsave_tiktok` (6) all pass. plant_agent refactor validated by import + logic
  review (see "Known issue" — its test module can't currently collect).

## Already implemented (no-op)
- `2f4fb802`, `a3b795d7` (daily-briefing): prompt already uses RFC3339 timestamps and an
  HTML-only output constraint.
- `0c60e39e` (telegram-bot): `bot.py` already guards `if not display_text.startswith("Plant assessment unavailable")` before saving.
- `68295cb5` (plant-agent atomic writes): plant_profiles already atomic; the remaining
  tools.py piece is covered by `feab14e7`.

## Held — needs decision
- `afb5a752` (librarian env-filtering + `allow_failover_on_timeout`): **not applied.**
  The primary finding (`Argument list too long` spawning `agy`) did not reproduce — the
  librarian audit ran successfully via Antigravity this session. The proposed env
  whitelist (PATH/HOME/USER/LANG/LC_ALL + GOOGLE_/TELEGRAM_/TODOIST_/MCP_) omits vars the
  CLIs may need for auth/config (XDG_*, ANTHROPIC_*/GEMINI_*, NODE_*), risking breakage of
  **all** LLM synthesis. High blast radius for an unconfirmed bug — recommend skipping or
  validating the diagnosis first.
- `88286d64` (plant-agent, 2026-06-03): broad `try/except` in `_send_status_email` /
  `_intelligence_run` masks errors as `success`. Legitimate but out of scope for "latest"
  (this run's) proposals.

## Known issue (pre-existing, unrelated)
`tests/test_plant_agent.py` fails at collection: `from agents.plant_agent import due_water_tasks`
references a function removed in commit `6c124b6` ("remove Todoist task creation and watering
sync"). The orphaned `TestDueWaterTasks` import/class blocks the whole module. Needs cleanup
separately.
