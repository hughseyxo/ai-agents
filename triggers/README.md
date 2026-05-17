# Agent Operations via MCP Bridge

Run any server-side agent from your laptop Claude Code session using the `server-bridge` MCP tools.
The bridge executes commands on the server over Tailscale — no cloud indirection, no auth issues.

## Prerequisites

`server-bridge` must be connected in your Claude Code session. Verify with:
```
list_agents()
```
Expected: array of daily-briefing, news-briefing, security-audit with last run times.

## Running Agents

### Daily Briefing (on demand)

```
exec_shell("bash run-agent.sh daily-briefing", working_dir="/home/cian/git/ai-agents")
```

Sends briefing email, saves report to `output/daily-briefing-YYYY-MM-DD.md`.
Also runs automatically via cron at 07:05 CEST (05:05 UTC) daily.

### News Briefing (on demand)

```
exec_shell("bash run-agent.sh news-briefing", working_dir="/home/cian/git/ai-agents")
```

Runs automatically at 07:00 CEST (05:00 UTC) daily.

### Security Audit (on demand)

```
exec_shell("bash run-agent.sh security-audit", working_dir="/home/cian/git/ai-agents")
```

18 checks: system hardening, seedbox, web exposure. Runs automatically Sundays 08:00 CEST.

## Checking Agent Status

```
list_agents()                              # last run status for all agents
get_agent_status("daily-briefing")        # last 5 runs + step-level detail
get_agent_status("news-briefing")
get_agent_status("security-audit")
```

## Reading Output

```
read_file("/home/cian/git/ai-agents/output/daily-briefing-2026-05-17.md")
exec_shell("ls -lt output/ | head -10", working_dir="/home/cian/git/ai-agents")
exec_shell("tail -50 output/cron.log", working_dir="/home/cian/git/ai-agents")
```

## Why Not RemoteTriggers?

RemoteTriggers run on claude.ai cloud infrastructure — they can't reach the Tailscale network,
the local SQLite database, or the Google OAuth tokens on the server. The MCP bridge runs
commands directly on the server, so all local resources are available.

The scheduled Daily Briefing trigger (`trig_01RFF8uoxNmr3WnobMGZJFza`) still runs at 05:00 UTC
via cron on the server — that path is unaffected.
