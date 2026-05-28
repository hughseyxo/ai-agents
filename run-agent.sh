#!/usr/bin/env zsh
source /home/cian/git/ai-agents/.env
export TODOIST_API_TOKEN GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET MCP_BRIDGE_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_USER_ID CONCIERGE_BOT_TOKEN
cd /home/cian/git/ai-agents

# Pre-flight: refresh Google OAuth token
source scripts/check-google-token.sh || { echo "Skipping agent due to token failure"; exit 1; }

python3 -m agents "$@"
