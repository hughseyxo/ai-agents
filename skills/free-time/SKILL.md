---
name: free-time
description: Use when user says "I have X minutes free", "what should I do?", "I have some free time", or asks what task to tackle in a time window. Suggests the best Todoist tasks to fill available time.
allowed-tools: mcp__todoist__find-tasks,mcp__todoist__find-tasks-by-date
---

# free-time

Recommends the best tasks to fill a block of free time, based on priority, due date, and estimated duration.

## When Invoked

### 1. Parse available time
Extract minutes from the user's message:
- "an hour" → 60 min
- "half an hour" / "30 minutes" → 30 min
- "a couple of hours" → 120 min

### 2. Fetch tasks
Use `find-tasks` to get all incomplete Inbox tasks.

### 3. Estimate duration per task

| Task type | Estimate |
|-----------|----------|
| Reply to email / send message | 10–15 min |
| Review a document | 20–30 min |
| Write short content | 30–45 min |
| Research a topic | 45–60 min |
| Deep work / coding / writing | 60–90 min |
| Admin / form-filling / calls | 15–20 min |
| Vague / unclear task | 30 min (default) |

### 4. Filter and rank
Keep tasks where estimate ≤ available time. Rank by:
1. Overdue first
2. High priority (p1 > p2 > p3 > p4)
3. Soonest due date
4. No due date last

### 5. Present top 2–3 tasks

```
You have [X] minutes. Here's what I'd suggest:

1. **[Task]** (~X min) — [reason: overdue / high priority / quick win]
2. **[Task]** (~X min) — [reason]
3. **[Task]** (~X min) — [reason]
```

If nothing fits, suggest the shortest available task instead.

## Constraints
- **Never** mark tasks as complete — only the user does that
- Estimates are guesses based on task titles — say so
- Don't suggest calendar events or meeting tasks
- Keep it to a few bullet points — no lengthy explanations
