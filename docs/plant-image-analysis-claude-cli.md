# Plant Image Analysis via the claude CLI (Opus 4.8)

## Problem

Plant photo assessment in the concierge Telegram bot used free OpenRouter vision
models (`nvidia/nemotron-nano-12b-v2-vl:free`, `meta-llama/llama-3.2-11b-vision-instruct`).
They were wildly inconsistent for plant-health diagnosis — wrong status calls,
contradictory structured fields, frequent JSON failures. A second-pass
`_validate_plant_assessment` LLM cross-check was bolted on to catch the contradictions.

The bot's **text** chat had already been moved onto the `claude` CLI (Pro
subscription, no API billing) via `claude_backend.ask_claude`. The vision path had not.

## Decision

Route plant image analysis through the `claude` CLI on the Pro subscription, using
**Opus 4.8** for maximum accuracy. The CLI reads the image with its `Read` tool.
Free vision models are removed entirely (no fallback). The redundant validator
cross-check is removed — Opus does not contradict itself the way the free models did.

Brainstormed decisions:
- **Server-side claude CLI** (not the Claude.ai mobile app Project — that has no
  API/export to hook into).
- **Opus 4.8** (`VISION_MODEL`), accuracy over speed (~10-20s/photo).
- **Keep** the existing `agents/prompts/plant_photo_assessment.md` prompt (strict JSON
  output contract the bot parses).
- **No fallback**; **drop** `_validate_plant_assessment`.

## Architecture / Data flow

```
Phone (Telegram) ──photo──> handle_photo (bot.py)
   │
   ├─ caption names a plant ──> get_plant / _resolve_plant_name
   └─ no/generic caption ────> _identify_plant_from_image
                                   └─ _write_temp_image → assess_image(Read tool) → name match
   │
   └─> _analyze_plant_image
          ├─ build context (last-watered, profile doc, species reference)
          ├─ _write_temp_image(bytes) → /tmp/xxxx.jpg
          ├─ assess_image(path, system_prompt, user_text)  # claude -p, Opus, Read
          │     └─ claude_backend._run_claude → JSON {"result": "..."}
          ├─ parse JSON (fences/prose-tolerant) → _build_assessment_display
          └─ os.unlink(temp)
   │
   └─> save: note_plant_observation (docs/plants/<slug>.md) +
            save_plant_assessment (DB state) + optional frequency inline-keyboard
```

`assess_image` builds: `claude -p --dangerously-skip-permissions --output-format json
--model claude-opus-4-8 --append-system-prompt <prompt> --add-dir <temp dir>
--allowedTools Read`, stdin = `"Read the image at <path> and respond.\n\n<user_text>"`.
It is stateless — no MCP config, no `--resume`. `--add-dir` grants the `Read` tool
access to the temp file outside the repo cwd.

## Error handling

- `assess_image` returns `None` on rc≠0 / timeout (120s) / not-runnable / unparseable
  JSON (shared `_run_claude` helper, same contract as `ask_claude`).
- `_analyze_plant_image`: `None` → "Plant assessment unavailable right now. Try again
  later." `_identify_plant_from_image`: `None` → bot asks the user to name the plant.
- Temp image is always unlinked via `try/finally`.

## Files

- `telegram-bot/claude_backend.py` — `VISION_MODEL`, `_run_claude` (refactor),
  `assess_image`.
- `telegram-bot/bot.py` — `_write_temp_image`; `_identify_plant_from_image` and
  `_analyze_plant_image` rewritten onto `assess_image`; `VISION_MODELS`,
  `_validate_plant_assessment`, `_VALIDATOR_PROMPT_PATH`, `base64` import removed.
- `telegram-bot/test_claude_backend.py`, `telegram-bot/test_bot.py` — coverage for
  `assess_image` and the repointed vision path.

## Notes

- `client` / `FREE_MODELS` remain — still used by `_resolve_plant_name` (text) and the
  `handle_message` text fallback chain.
- `agents/prompts/plant_assessment_validator.md` is now orphaned (no code references)
  but left in place; safe to delete later.
- The user can later paste their Claude.ai "plant health" Project instructions into
  `agents/prompts/plant_photo_assessment.md` to mirror that Project's behaviour.
