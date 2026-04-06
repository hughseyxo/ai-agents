# Workflow: Daily Morning Briefing

## Purpose
Generate a concise daily briefing every morning covering the user's calendar and outstanding tasks. This helps plan the day and avoid missing commitments.

## Steps

### 1. Import MCP connector
Use ToolSearch to load the Todoist MCP tool before proceeding:
- Search `"todoist find-tasks"` to import the Todoist connector

### 2. Get today's date
Note today's date and tomorrow's date in YYYY-MM-DD format.

### 3. Fetch calendar events via ICS
Run the following bash command to fetch the calendar:
```bash
curl -s "$GCAL_ICS_URL"
```
Parse the ICS output to extract VEVENT blocks. For each event extract:
- SUMMARY (title)
- DTSTART (start time)
- DTEND (end time)
- DESCRIPTION (optional)

Filter events to only those occurring **today** or **tomorrow**.
Sort by start time within each day.

### 4. Fetch all pending Todoist tasks
Call `mcp__todoist__find-tasks` to retrieve all incomplete tasks across all projects.
- Include task priority, project name, and due date where available
- Order by: overdue first, then by priority (p1 → p4), then by due date

### 5. Format the report
Produce a markdown report with the following sections:

```
# Daily Briefing — [DATE]

## Today's Schedule
- [TIME] — [Event Title]
- (If no events: "No events scheduled today")

## Tomorrow's Schedule
- [TIME] — [Event Title]
- (If no events: "No events scheduled tomorrow")

## Pending Tasks
### Overdue
- [Task title] (Due: [date]) [p1/p2 if high priority]

### Due Today
- [Task title] [p1/p2 if high priority]

### Upcoming
- [Task title] (Due: [date]) — [Project]

### No Due Date
- [Task title] — [Project]

## Quick Wins (estimated < 30 min)
- [Task title] (~[X] min)
```

Keep the report tight — bullet points only, no paragraphs.

### 6. Save to file
Save the completed report to:
`output/daily-briefing-YYYY-MM-DD.md`

### 7. Send via Gmail (SMTP)
Write a plain-text email file to `/tmp/briefing_email.txt` with this format:
```
From: [GMAIL_ADDRESS env var]
To: [GMAIL_ADDRESS env var]
Subject: Daily Briefing — [DATE]
Content-Type: text/plain; charset=utf-8

[full report content]
```

Then send it with:
```bash
curl --ssl-reqd \
  --url 'smtps://smtp.gmail.com:465' \
  --user "${GMAIL_ADDRESS}:${GMAIL_APP_PASSWORD}" \
  --mail-from "${GMAIL_ADDRESS}" \
  --mail-rcpt "${GMAIL_ADDRESS}" \
  --upload-file /tmp/briefing_email.txt
```

If the send fails, note it in the saved file but do not stop.

### 8. Confirm completion
Output: `Briefing saved to output/daily-briefing-YYYY-MM-DD.md`

## Constraints
- Do NOT mark any Todoist tasks as complete — only the user does that
- Do NOT delete or modify any calendar events
- Do NOT run any git commands
- Keep estimates and guesses clearly labelled as estimates
