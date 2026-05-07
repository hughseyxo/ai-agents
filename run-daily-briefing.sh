#!/usr/bin/env zsh
source /home/cian/git/ai-agents/.env
export TODOIST_API_TOKEN GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
cd /home/cian/git/ai-agents

# Pre-flight: refresh Google OAuth token
source scripts/check-google-token.sh || { echo "Skipping briefing due to token failure"; exit 1; }

claude --dangerously-skip-permissions -p "$(cat workflows/daily-briefing.md)" --output-format text 2>&1
