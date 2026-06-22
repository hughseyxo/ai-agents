---
type: dashboard
title: Memory
tags: [dashboard, memory]
---
# Memory Dashboard

## All Memories (by type)

```dataview
TABLE metadata.type AS "type", file.name AS "slug"
FROM "_memory"
SORT metadata.type ASC, file.mtime DESC
```

## Recent Updates

```dataview
TABLE metadata.type AS "type", file.mtime AS "updated"
FROM "_memory"
SORT file.mtime DESC
LIMIT 10
```
