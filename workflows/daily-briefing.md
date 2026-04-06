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
Run both commands and combine the results:
```bash
curl -s "$GCAL_ICS_URL"
curl -s "$GCAL_ICS_URL_2"
```
Parse the ICS output from both calendars to extract VEVENT blocks. For each event extract:
- SUMMARY (title)
- DTSTART (start time)
- DTEND (end time)
- DESCRIPTION (optional)

Merge events from both calendars and deduplicate by title+time. Split into two groups:

**Group A — Today:** all events occurring today, sorted by start time.

**Group B — Upcoming (next 4 weeks):** events from tomorrow through 28 days from now. Filter to only **notable** events — exclude:
- All-day events that are just date markers or reminders (e.g. "Birthday", "Holiday", single-word all-day blocks)
- Recurring daily events (e.g. daily standups, lunch blocks)
- Events under 15 minutes

Keep: meetings with other people, appointments, deadlines, one-off events, multi-day events. List chronologically, max 10 events. Show the date and time for each.

### 4. Fetch Inbox tasks from Todoist
Call `mcp__todoist__find-tasks` with `projectId: "inbox"` to retrieve only incomplete Inbox tasks.
- Include task priority and due date where available
- Order by: overdue first, then by priority (p1 → p4), then by due date
- Do NOT include tasks from any named projects

### 5. Format the markdown report
Save a markdown version to `output/daily-briefing-YYYY-MM-DD.md` with these sections:

```
# Daily Briefing — [DATE]

## Today's Schedule
- [TIME] — [Event Title]
- (If no events: "No events scheduled today")

## Coming Up (next 4 weeks)
- [DAY DATE, TIME] — [Event Title]
- (If no notable events: "Nothing notable in the next 4 weeks")

## Inbox Tasks
### Overdue
- [Task title] (Due: [date]) 🔴

### Due Today
- [Task title] 🟡

### No Due Date
- [Task title]

## Quick Wins (estimated < 30 min)
- [Task title] (~[X] min)
```

### 6. Build the HTML email
Construct the email body as HTML. Use inline CSS only (no external stylesheets). Follow this structure and style:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr><td style="background:#1a1a2e;padding:28px 32px;">
          <p style="margin:0;color:#a0a8c0;font-size:13px;letter-spacing:1px;text-transform:uppercase;">Daily Briefing</p>
          <h1 style="margin:4px 0 0;color:#ffffff;font-size:24px;">[WEEKDAY, DATE]</h1>
        </td></tr>

        <!-- Today's Schedule -->
        <tr><td style="padding:24px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">📅 Today</h2>
          [For each event: <p style="margin:0 0 8px;padding:10px 14px;background:#f0f4ff;border-left:3px solid #4a6fa5;border-radius:4px;font-size:14px;color:#333;"><strong>[TIME]</strong> — [Title]</p>]
          [If no events: <p style="margin:0;color:#999;font-size:14px;font-style:italic;">No events today</p>]
        </td></tr>

        <!-- Coming Up -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">🗓 Coming Up</h2>
          [For each notable upcoming event in next 4 weeks: <p style="margin:0 0 8px;padding:10px 14px;background:#f0f4ff;border-left:3px solid #4a6fa5;border-radius:4px;font-size:14px;color:#333;"><strong>[Mon 7 Apr, 14:00]</strong> — [Title]</p>]
          [If none: <p style="margin:0;color:#999;font-size:14px;font-style:italic;">Nothing notable in the next 4 weeks</p>]
        </td></tr>

        <!-- Inbox Tasks -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">✅ Inbox Tasks</h2>

          [If overdue tasks exist:]
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#c0392b;letter-spacing:0.5px;">Overdue</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fff5f5;border-left:3px solid #e74c3c;border-radius:4px;font-size:14px;color:#333;">[Title] <span style="color:#e74c3c;font-size:12px;">[due date]</span></p>]

          [If due today:]
          <p style="margin:12px 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#e67e22;letter-spacing:0.5px;">Due Today</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fffaf0;border-left:3px solid #f39c12;border-radius:4px;font-size:14px;color:#333;">[Title]</p>]

          [If no due date:]
          <p style="margin:12px 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#888;letter-spacing:0.5px;">No Due Date</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fafafa;border-left:3px solid #ccc;border-radius:4px;font-size:14px;color:#333;">[Title]</p>]
        </td></tr>

        <!-- Quick Wins -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">⚡ Quick Wins</h2>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fafafa;border-radius:4px;font-size:14px;color:#333;">[Title] <span style="color:#888;font-size:12px;">~[X] min</span></p>]
          [If none: omit this section]
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:24px 32px;margin-top:8px;border-top:1px solid #eee;">
          <p style="margin:0;color:#aaa;font-size:12px;text-align:center;">Generated by your Daily Briefing Agent</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
```

### 7. Send via Gmail (SMTP)
Write the email to `/tmp/briefing_email.txt` using MIME format:

```
From: ${GMAIL_ADDRESS}
To: ${GMAIL_ADDRESS}
Subject: Daily Briefing — [WEEKDAY, DATE]
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

[HTML email content from step 6]
```

Then send:
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
- Do NOT include tasks from named Todoist projects — Inbox only
- Do NOT delete or modify any calendar events
- Do NOT run any git commands
- Keep estimates clearly labelled as estimates
