# Workflow: Daily Morning Briefing

## Purpose
Generate a concise daily briefing every morning covering the user's calendar and outstanding tasks. This helps plan the day and avoid missing commitments.

## Steps

### 1. Get today's date
Note the current date (YYYY-MM-DD format) — used for file naming and calendar queries.

### 2. Fetch today's calendar events
Use the Google Calendar MCP tool `gcal_list_events` to retrieve all events for today:
- timeMin: today at 00:00:00 local time
- timeMax: today at 23:59:59 local time
- Sort results by start time

### 3. Fetch tomorrow's calendar events
Use `gcal_list_events` again for tomorrow:
- timeMin: tomorrow at 00:00:00 local time
- timeMax: tomorrow at 23:59:59 local time
- Sort results by start time

### 4. Fetch all pending Todoist tasks
Use the Todoist MCP tool `find-tasks` to retrieve all incomplete tasks across all projects.
- Include task priority, project name, and due date where available
- Order by: overdue first, then by priority (p1 → p4), then by due date

### 5. Format the report
Produce a markdown report with the following sections:

```
# Daily Briefing — [DATE]

## Today's Schedule
- [TIME] — [Event Title] ([Duration if available])
- (If no events: "No events scheduled today")

## Tomorrow's Schedule
- [TIME] — [Event Title] ([Duration if available])
- (If no events: "No events scheduled tomorrow")

## Pending Tasks
### Overdue
- [Task title] (Due: [date]) [Priority badge if p1/p2]

### Due Today
- [Task title] [Priority badge if p1/p2]

### Upcoming
- [Task title] (Due: [date]) — [Project]

### No Due Date
- [Task title] — [Project]

## Quick Wins (estimated < 30 min)
Tasks from the list above that look short based on their title:
- [Task title] (~[X] min)
```

Keep the report tight — bullet points only, no paragraphs.

### 6. Save to file
Save the completed report to:
`output/daily-briefing-YYYY-MM-DD.md`

Where YYYY-MM-DD is today's date.

### 7. Send via Gmail
Authenticate with Gmail if needed using `authenticate`.
Send the report content as an email to the user with:
- Subject: `Daily Briefing — [DATE]`
- Body: the full markdown report

Note: If Gmail send is not available, skip this step and note in the file that email delivery was skipped.

### 8. Confirm completion
Output a brief confirmation: "Briefing saved to output/daily-briefing-YYYY-MM-DD.md"

## Constraints
- Do NOT mark any Todoist tasks as complete — only the user does that
- Do NOT delete or modify any calendar events
- Keep estimates and guesses clearly labelled as estimates
