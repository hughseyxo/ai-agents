---
type: index
title: Plants
tags: [index, plants]
---
# Plants

All plant profiles. See [[Plant Health]] dashboard for status overview.

```dataview
TABLE location, baseline_frequency_days AS "base freq", latest_health AS "health", last_watered
FROM "plants"
WHERE type = "plant"
SORT file.name ASC
```
