# Remove Todoist Plant Task Integration

**Date:** 2026-06-15

## Problem

The plant agent was creating Todoist tasks for every due watering and syncing completions back to update `last_watered`. This generated constant noise in Todoist. The FloraPulse PWA and Telegram bot both write `last_watered` directly to the DB, so the Todoist integration was a redundant side-effect with no load-bearing role.

## Decision

Remove both Todoist steps entirely. Watering is managed exclusively through:
- **FloraPulse PWA** — mark as watered via `POST /api/plants/{name}/water` or `POST /api/plants/water-all`
- **Telegram concierge bot** — natural-language watering commands via MCP tools

## Changes

### `agents/plant_agent.py`
- Removed `_SYNC_PROMPT`, `_CREATE_TASK_PROMPT` constants
- Removed `PERSONAL_PROJECT_ID` constant
- Removed `due_water_tasks()` helper function
- Removed `_sync_watering()` step (read completed Todoist tasks → update `last_watered`)
- Removed `_create_tasks()` step (create Todoist tasks for due plants)
- Removed `is_heatwave_incoming` import (only used by `due_water_tasks`)
- Simplified `steps()` to 4 steps: `weather_update`, `photo_requests`, `send_status_email`, `intelligence_run`
- Simplified `report()` accordingly

### `plant_ui/server.py`
- Removed `complete_todoist_task_for_plant()` async function
- Removed `httpx` import (only used by that function)
- Removed `BackgroundTasks` import and dependency from `water_plant`, `water_all_plants`, `delete_plant`
- Removed `PERSONAL_PROJECT_ID` constant
- Removed stale `sync_watering` and `create_tasks` keys from `/api/status` response

### `tests/test_plant_ui_api.py`
- Removed stale `patch("plant_ui.server.complete_todoist_task_for_plant")` mocks from 3 tests
