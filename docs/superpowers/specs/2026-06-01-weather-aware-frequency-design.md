# Weather-Aware, Auto-Adjusting Watering Frequency

**Date:** 2026-06-01
**Status:** Design — approved direction, pending spec review

## Problem
Today, `frequency_days` is a static base cadence per plant. Weather only shifts the
**due date** transiently at task-creation time (`adjust_watering_date`), never the
stored frequency. The plant intelligence run can *recommend* frequency changes (it
files "Review frequency" Todoist tasks) but cannot **apply** them — a human must edit
the tracker. The concierge bot can update plants but frequency changes aren't
weather-aware or logged.

We want:
1. The intelligence run to **apply** frequency changes automatically (bounded, logged,
   reversible), driven by photo/health observations.
2. The concierge bot to change frequency on command.
3. Both to be **weather-aware**, with weather folded into the single visible
   `frequency_days`, differentiating indoor vs outdoor plants and accounting for
   shade tolerance.

## Goals
- One visible, weather-folded `frequency_days` that stays fresh day-to-day.
- Auto-apply within guardrails; every change logged and reversible.
- Reuse existing weather logic (`calculate_adjustment`) and profile **Frequency
  History** tables.

## Non-goals
- No change to how watering completions are synced or how due tasks are created
  (beyond reading the new effective frequency).
- No approval/proposal workflow (user chose auto-within-bounds).

## Design (Approach A — health baseline + hourly weather recompute)

### Data model
Each plant dict in state (`agent='daily-briefing'`, `key='plants'`) gains:
- `baseline_frequency_days` (int) — the intrinsic, health-derived cadence. The only
  field intelligence/bot mutate.

`frequency_days` is retained but **redefined as derived/effective**: the weather-folded
number that every consumer reads. Recomputed each hour; never hand-edited.

**Migration:** when loading a plant without `baseline_frequency_days`, set
`baseline_frequency_days = frequency_days`. Idempotent; runs in the recompute path.

### Weather recompute (deterministic, hourly)
In the existing `weather_update` step of `PlantAgent` (no LLM), after the weather cache
is refreshed, for each plant:

```
delta = calculate_adjustment(plant, weather)          # existing, signed days
frequency_days = clamp(baseline_frequency_days + delta, 1, 30)
```

Persist updated plants to state. This **replaces** the transient
`adjust_watering_date` due-date shift — the weather effect now lives in the stored
`frequency_days`, so `next_water = last_watered + frequency_days` everywhere.

If weather fetch fails, leave `frequency_days = baseline_frequency_days` (no delta),
matching today's "weather failure is non-fatal" behavior.

### Indoor vs outdoor (already differentiated — preserved)
`calculate_adjustment` already branches:
- `_indoor_adjustment` — temp + humidity, gentle (±1–2).
- `_outdoor_adjustment` — rain-aware, heat, larger (±3).

No change to these; they carry over unchanged.

### Shade tolerance (new)
Add `_sunlight_modifier(adj, plant)` applied inside `calculate_adjustment` **after** the
location branch and **before** the ±`MAX_ADJUSTMENT` clamp. It only modulates a
**non-zero** `adj` (so it shapes the weather response without inventing an adjustment
when weather is neutral — the baseline already encodes the plant's typical sun need):

- `adj < 0` (drying — water sooner): full sun → `adj -= 1` (dries faster); shade →
  `adj += 1` (dampen toward 0); partial shade → unchanged.
- `adj > 0` (wetter — defer): shade → `adj += 1` (stays moist longer); full sun →
  `adj -= 1` (dries faster, defer less); partial shade → unchanged.

`sunlight` values: `"full sun"`, `"partial shade"`, `"shade"` (default/unknown →
treated as partial shade → no modifier). Result clamped to ±`MAX_ADJUSTMENT` as today.

### Intelligence applies baseline changes (auto within bounds)
New `[FREQUENCY]` block in `agents/prompts/plant_intelligence.md`, one line per change:
```
[FREQUENCY]
Lantana — 5 — wilting under full sun, soil drying before day 7
[/FREQUENCY]
```
`_apply_intelligence_output` parses it and, per line, updates
`baseline_frequency_days` with guardrails:
- clamp target to **1–30**
- max **±2-day step per run** (request 7→3 moves to 5 this run; converges over runs)
- append a row to the profile's **Frequency History** table:
  `| <date> | <old>→<new> days | intelligence: <reason> |`
- baseline stored separately ⇒ reversible.

No-op if the plant's baseline already equals the (clamped, stepped) target.

### Concierge bot
New tool `set_plant_frequency(plant_name, frequency_days, reason)` in
`telegram-bot/tool_specs.py` + impl in `telegram-bot/tools.py`:
- sets `baseline_frequency_days` (clamp **1–30**, logged to Frequency History as
  `bot: <reason>`); being explicit user intent, it skips the ±2-day step limit.
- triggers an immediate recompute for that plant so `frequency_days` reflects it at
  once (otherwise it refreshes on the next hourly run).

### Consumers to update
Stop applying the transient adjustment; read effective `frequency_days` directly:
- `agents/plant_agent.py` — `create_tasks` (drop `adjust_watering_date` call), status
  table builder, weather_update (add recompute + migration).
- `telegram-bot/tools.py` — `get_plant_status` (already uses `last + frequency_days`;
  verify no separate adjustment).
- `plant.sh list` — already `last_watered + frequency_days`; no change.
- `adjust_watering_date` becomes unused by the main flow; keep `calculate_adjustment`,
  `is_heatwave_incoming` (still used for heatwave early-create), and `_build_reason`
  (reused for history reasons). Remove/deprecate `adjust_watering_date` if no caller
  remains.

## Guardrails (summary)
clamp 1–30 days · intelligence max ±2/run · all changes logged to Frequency History ·
baseline preserved for reversibility · weather failure → no delta.

## Testing (TDD, pure functions where possible)
`tests/test_plant_weather.py`:
- `_sunlight_modifier` / `calculate_adjustment`: full sun amplifies drying, shade
  dampens; modifier only on non-zero adj; clamp respected; indoor vs outdoor preserved.
`tests/test_plant_agent.py`:
- recompute: `frequency_days = clamp(baseline + delta, 1, 30)`; migration sets baseline;
  weather-fail leaves baseline.
- intelligence `[FREQUENCY]` parse + bounds (±2 step, clamp), history row written.
`telegram-bot/test_tools.py`:
- `set_plant_frequency` clamps, logs, triggers recompute.

## File list
- `agents/plant_weather.py` — `_sunlight_modifier`, wire into `calculate_adjustment`.
- `agents/plant_agent.py` — recompute + migration in `weather_update`; `create_tasks`
  + status table read effective frequency; `_apply_intelligence_output` `[FREQUENCY]`.
- `agents/prompts/plant_intelligence.md` — `[FREQUENCY]` marker instructions.
- `telegram-bot/tool_specs.py`, `telegram-bot/tools.py` — `set_plant_frequency`.
- Tests as above. CLAUDE.md updated (plant model: baseline vs effective frequency).

## Risks
- **Double-counting weather** if a consumer still applies `adjust_watering_date` on top
  of the folded frequency — mitigated by removing those calls (see Consumers).
- **LLM drift** in `[FREQUENCY]` values — mitigated by clamp + ±2 step + logging.
- **Baseline migration** must run before first recompute to avoid clobbering a
  hand-set frequency — handled idempotently in the load/recompute path.
