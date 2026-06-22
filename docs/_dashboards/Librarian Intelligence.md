---
type: dashboard
title: Librarian Intelligence
tags: [dashboard, librarian, learnings]
---
# Librarian Intelligence Dashboard

## Active Learnings (by confidence)

```dataview
TABLE agent, confidence, date, file.name AS "slug"
FROM "agent-learnings"
WHERE status = "active"
SORT confidence DESC, date DESC
```

## Recent Findings (last 14 days)

```dataview
TABLE agent, confidence, status
FROM "agent-learnings"
WHERE date >= date(today) - dur(14 days)
SORT date DESC
```

## Architecture Memory

```dataview
LIST
FROM "librarian-memory"
WHERE status = "active"
SORT date DESC
```
