# RemoteTrigger Definitions

These JSON files define Claude Code RemoteTrigger entries — on-demand tasks that can be fired from any Claude Code session (laptop or server) sharing the same account.

## Triggers

| File | Name | Description | Cloud-runnable? |
|------|------|-------------|----------------|
| `run-briefing-agent.json` | run-briefing-agent | Manually run the daily briefing on demand | ✅ Yes (Calendar + Gmail + Todoist) |
| `check-agent-health.json` | check-agent-health | Check all agents' last run status from agents.db | ❌ No — needs server SQLite. Use MCP bridge: `list_agents()` |
| `security-audit.json` | security-audit | Run the 18-check security audit agent on demand | ❌ No — needs server shell. Use MCP bridge: `exec_shell("bash run-agent.sh security-audit")` |

> **Note:** RemoteTriggers run on claude.ai cloud infrastructure — they can access the git repo and cloud MCP connections (Gmail, Calendar, Todoist) but cannot reach the server's SQLite database, Tailscale network, or local services. Tasks requiring server access should be run via the MCP bridge from laptop Claude Code instead.

## Creating / Updating Triggers

Triggers are managed via the `RemoteTrigger` tool in a Claude Code session. To create from a definition file:

```
ToolSearch("select:RemoteTrigger")
RemoteTrigger(action="create", body={ ...contents of the JSON file... })
```

To list existing triggers and their IDs:
```
RemoteTrigger(action="list")
```

## Trigger IDs

Once created, trigger IDs are assigned by the platform (format: `trig_01XXX`).
Update this section after creation:

| Name | Trigger ID |
|------|-----------|
| Daily Briefing (scheduled 05:00 UTC) | `trig_01RFF8uoxNmr3WnobMGZJFza` |
| run-briefing-agent (on demand) | `trig_01A2SKJz7xJDccejzoBYQK2p` |
| check-agent-health | N/A — use MCP bridge |
| security-audit | N/A — use MCP bridge |

## Invoking from the Laptop

```
RemoteTrigger(action="run", trigger_id="trig_01XXX")
```
