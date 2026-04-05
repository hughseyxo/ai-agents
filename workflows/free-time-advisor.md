# Workflow: Free Time Advisor

## Purpose
When the user has a block of free time, help them pick the best task to fill it — accounting for task priority, estimated duration, and urgency.

## Trigger
The user says something like:
- "I have an hour free, what should I do?"
- "I've got 45 minutes — what task should I tackle?"
- "I have 30 minutes free"

## Steps

### 1. Parse available time
Extract the time available from the user's message. Convert to minutes:
- "an hour" → 60 min
- "half an hour" / "30 minutes" → 30 min
- "15 minutes" → 15 min
- "a couple of hours" → 120 min

### 2. Fetch pending Todoist tasks
Use `find-tasks` to get all incomplete tasks. Note priority, due date, and project for each.

### 3. Estimate task duration
Use the task title and description to make a reasonable duration estimate. Guidelines:

| Task type | Estimate |
|-----------|----------|
| Reply to email / send message | 10–15 min |
| Review a document | 20–30 min |
| Write a short piece of content | 30–45 min |
| Research a topic | 45–60 min |
| Deep work / coding / writing | 60–90 min |
| Admin / form-filling | 15–20 min |
| Meeting prep | 15–30 min |
| Vague / unclear task | 30 min (default) |

Always label estimates clearly: "~X min (estimated)".

### 4. Filter and rank candidates
Keep only tasks where the estimate ≤ available time.

Rank remaining tasks by:
1. Overdue first
2. High priority (p1, then p2)
3. Has a due date (soonest first)
4. No due date last

### 5. Recommend top tasks
Present the top 2–3 candidates clearly:

```
You have [X] minutes. Here's what I'd suggest:

1. **[Task title]** (~[X] min estimated) — [Project]
   Why: [one-line reason: overdue / high priority / quick win]

2. **[Task title]** (~[X] min estimated) — [Project]
   Why: [reason]

3. **[Task title]** (~[X] min estimated) — [Project]
   Why: [reason]
```

If nothing fits the time window, say so and suggest the shortest available task instead.

## Constraints
- Never mark tasks as complete — only the user does that
- Estimates are guesses based on task titles — say so clearly
- Don't suggest calendar events or meeting tasks as "free time" work
- Keep the response to a few bullet points — no lengthy explanations
