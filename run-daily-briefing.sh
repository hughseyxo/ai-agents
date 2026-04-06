#!/usr/bin/env zsh
source /home/cian/.zshrc
cd /home/cian/git/ai-agents
claude --dangerously-skip-permissions -p "$(cat workflows/daily-briefing.md)" --output-format text
