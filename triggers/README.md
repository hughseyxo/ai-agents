# RemoteTrigger Definitions

These JSON files define Claude Code RemoteTrigger entries — on-demand tasks that can be fired from any Claude Code session (laptop or server) sharing the same account.

## Triggers

| File | Name | Description |
|------|------|-------------|
| `run-briefing-agent.json` | run-briefing-agent | Manually run the daily briefing agent |
| `check-agent-health.json` | check-agent-health | Check all agents' last run status from agents.db |
| `security-audit.json` | security-audit | Run the 18-check security audit agent on demand |

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
| run-briefing-agent | _not yet created_ |
| check-agent-health | _not yet created_ |
| security-audit | _not yet created_ |

## Invoking from the Laptop

```
RemoteTrigger(action="run", trigger_id="trig_01XXX")
```
