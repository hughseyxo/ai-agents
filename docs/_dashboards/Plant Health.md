---
type: dashboard
title: Plant Health
tags: [dashboard, plants]
---
# Plant Health Dashboard

## All Plants

```dataview
TABLE location, last_watered, effective_frequency_days AS "freq (days)", needs_photo AS "photo?", latest_health AS "health"
FROM "plants"
WHERE type = "plant"
SORT location ASC, file.name ASC
```

## Needs Attention

```dataview
TABLE location, last_watered, latest_health AS "health", needs_photo AS "photo needed"
FROM "plants"
WHERE type = "plant" AND (needs_photo = true OR latest_health != "healthy")
SORT latest_assessment ASC
```
