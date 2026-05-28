# Telegram Bots

Overview of all Telegram bots in this project.

## Active Bots

### mealsave-bot
- **Purpose:** Forward a recipe URL → saves it to Mealie
- **Code:** `skills/mealsave/mealsave_bot.py`
- **Service:** `~/.config/systemd/user/mealsave-bot.service`
- **Token:** `~/.config/mealsave/.env` (`TELEGRAM_BOT_TOKEN`)
- **Auth:** Locked to single `TELEGRAM_USER_ID`
- **Status:** Running (PID managed by systemd)

### free-time-bot
- **Purpose:** Given a free time window, suggests ranked Todoist tasks
- **Code:** `free_time_bot.py`
- **Service:** `~/.config/systemd/user/free-time-bot.service` (WantedBy=multi-user.target — requires linger)
- **Token:** `/home/cian/git/ai-agents/.env` (`TELEGRAM_BOT_TOKEN`)
- **Auth:** Locked to single `TELEGRAM_USER_ID`
- **Backend:** Claude CLI with Todoist MCP
- **Status:** Running

### concierge-bot (openrouter-bot)
- **Purpose:** Natural-language server concierge — agent status, plant watering, yopflix/seedbox, system health, cron schedules, logs
- **Code:** `telegram-bot/bot.py` + `telegram-bot/tools.py`
- **Service:** `~/.config/systemd/user/concierge-bot.service`
- **Token:** `telegram-bot/.env` (`TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, `TELEGRAM_USER_ID`)
- **Backend:** OpenRouter API → `meta-llama/llama-3.3-70b-instruct:free` with tool use
- **Auth:** Locked to `TELEGRAM_USER_ID`
- **Status:** Running (systemd-managed)
- **Tools:** `get_agent_status`, `get_plant_status`, `get_yopflix_status`, `get_system_health`, `get_cron_schedule`, `get_agent_logs`
- **Design doc:** `docs/superpowers/specs/2026-05-17-server-concierge-bot-design.md`

## Failed / Abandoned

### hermes-gateway (abandoned 2026-05-17)
- **Intent:** Persistent Telegram orchestrator routing work to Claude/Antigravity CLI via local Hermes-3 8B (Ollama)
- **Why it failed:** `hermes-cli` was never installable — cloud model quota (Antigravity HTTP 429, Anthropic HTTP 400) killed every turn. Local Ollama model path was under-configured. The `hermes-agent` venv was never set up.
- **Cleanup done:** Service disabled and removed from `~/.config/systemd/user/`
- **Design doc:** `docs/hermes-orchestrator.md` (kept as record)

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `telegram.error.Conflict` | Two instances polling same token | Kill orphaned process (`ps aux \| grep bot_name`), leave systemd instance running |
| `203/EXEC` on systemd unit | ExecStart path doesn't exist | Check venv is installed, or remove the service |
| Bot online but silent | `TELEGRAM_USER_ID` mismatch | Check `.env` value matches your actual Telegram user ID |
| Service dies on SSH logout | linger not enabled | `loginctl enable-linger $USER` |
