---
type: index
title: Agent Learnings
tags: [index, learnings]
---
# Agent Learnings

Atomic status-tagged notes produced by the Librarian agent. See [[Librarian Intelligence]] dashboard.

- `status: active` — currently applied rule
- `status: superseded` — replaced by a newer finding on the same topic

```dataview
TABLE agent, confidence, status, date
FROM "agent-learnings"
SORT date DESC, confidence DESC
```
