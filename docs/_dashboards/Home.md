---
type: dashboard
title: Eagna Home
tags: [dashboard, home, moc]
---
# Eagna — Home

> Eagna is the Claude Code instance for this ai-agents project. Blunt, dry, disagrees when warranted. No sycophancy. See [[feedback_personality]].

## Project Source of Truth
- [[CLAUDE]] — project conventions, structure, agent inventory, MCP integrations
- [[feedback_personality]] — Eagna personality (blunt, dry, no sycophancy)

## Dashboards
- [[Plant Health]] — all plants, watering schedule, attention queue
- [[Librarian Intelligence]] — active learnings, recent findings, architecture memory
- [[Memory]] — durable facts grouped by type

## Recent Daily Notes

```dataview
TABLE topics, sessions
FROM "daily"
WHERE type = "daily"
SORT date DESC
LIMIT 7
```

## Open Threads

```dataview
FLATTEN open_threads
FROM "daily"
WHERE type = "daily" AND length(open_threads) > 0
SORT date DESC
LIMIT 3
```

## Project Memory

```dataview
TABLE metadata.type AS "type", description
FROM "_memory"
WHERE metadata.type = "project"
SORT file.mtime DESC
```
