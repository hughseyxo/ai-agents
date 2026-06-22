# Agent Operations (run directly on the server)

> **Note:** The MCP bridge that previously let a laptop Claude Code session drive these agents
> remotely was **deleted 2026-06-22** (code-review remediation, Phase 0). Run agents directly on
> the server over SSH instead. See `docs/mcp-bridge.md` (superseded) for the old design.

## Running Agents

SSH to the server, then from `/home/cian/git/ai-agents`:

```
bash run-agent.sh daily-briefing     # briefing email + output/daily-briefing-YYYY-MM-DD.md (cron: 07:05 CEST)
bash run-agent.sh news-briefing      # news email (cron: 07:00 CEST)
bash run-agent.sh security-audit     # 18 checks: system/seedbox/web (cron: Sun 08:00 CEST)
```

## Checking Agent Status

```
python3 -m agents list                       # all agents + schedules
python3 -m agents history daily-briefing      # recent runs + status
```

## Librarian Proposal Review

The librarian emails prompt-edit / architecture-plan proposals for review. Act on them with the CLI
(replaces the old bridge approve/reject links):

```
python3 -m agents librarian-apply  <id>   # apply a prompt_edit proposal (writes + git-commits the learning)
python3 -m agents librarian-reject <id>   # reject a proposal
python3 -m agents librarian-plan   <id>   # materialise an architecture_plan proposal into docs/superpowers/plans/
```

## Reading Output

```
ls -lt output/ | head -10
tail -50 output/cron.log
```
