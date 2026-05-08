# Free Time Telegram Bot

## Purpose
Telegram bot that suggests Todoist tasks to fill a block of free time. Accessible from phone — the mobile counterpart to the `skills/free-time/` Claude Code skill.

## How it works
1. User sends minutes to bot (e.g. "30", "I have an hour")
2. Bot calls Claude CLI with Todoist MCP to fetch inbox tasks as JSON
3. Python estimates task durations via keyword matching, filters by available time
4. Ranks: overdue first → high priority → soonest due date
5. Replies with top 3 task suggestions

## Files
- `free_time_bot.py` — entire bot (single file)
- `free-time-bot.service` — systemd unit
- `.env` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`

## Security
- Locked to single Telegram user ID (env var `TELEGRAM_USER_ID`)
- No inbound ports — uses outbound HTTPS long-polling
- Bot token in `.env` (gitignored)

## Deployment
```bash
pip install python-telegram-bot
sudo cp free-time-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now free-time-bot
```

## Duration estimation keywords
Defined in `DURATION_KEYWORDS` dict in `free_time_bot.py`. Default estimate: 15 min.

## Dependencies
- `python-telegram-bot` (pip)
- Claude CLI (for MCP access to Todoist)
