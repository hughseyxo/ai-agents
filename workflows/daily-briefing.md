# Workflow: Daily Morning Briefing

## Purpose
Generate a concise daily briefing every morning covering the user's calendar and outstanding tasks. This helps plan the day and avoid missing commitments.

## Steps

### 1. Import MCP connectors
Use ToolSearch to load all required MCP tools before proceeding:
- Search `"todoist find-tasks"` to import the Todoist connector
- Search `"gcal_list_events"` to import the Google Calendar connector
- Search `"gmail_send"` to import the Gmail connector

### 2. Get today's date
Note today's date and the date 28 days from now in YYYY-MM-DD format.

### 3. Fetch today's calendar events
Call `mcp__google-calendar__gcal_list_events` with:
- calendarId: `primary`
- timeMin: today at 00:00:00
- timeMax: today at 23:59:59
- timeZone: `Europe/Dublin`

### 4. Fetch upcoming notable events (next 4 weeks)
Call `mcp__google-calendar__gcal_list_events` with:
- calendarId: `primary`
- timeMin: tomorrow at 00:00:00
- timeMax: 28 days from now at 23:59:59
- timeZone: `Europe/Dublin`
- maxResults: 50

Filter to only **notable** events — exclude:
- All-day events that are just date markers (birthdays, public holidays)
- Recurring daily events (daily standups, lunch blocks)
- Events under 15 minutes

Keep: meetings with attendees, appointments, deadlines, one-off events. Top 10 chronologically.

### 5. Fetch Inbox tasks from Todoist
Call `mcp__todoist__find-tasks` with `projectId: "inbox"` to retrieve only incomplete Inbox tasks.
- Include task priority and due date where available
- Order by: overdue first, then by priority (p1 → p4), then by due date
- Do NOT include tasks from any named projects

### 6. Format the markdown report
Save to `output/daily-briefing-YYYY-MM-DD.md`:

```
# Daily Briefing — [DATE]

## Today's Schedule
- [TIME] — [Event Title]
- (If no events: "No events scheduled today")

## Coming Up (next 4 weeks)
- [DAY DATE, TIME] — [Event Title]
- (If none: "Nothing notable in the next 4 weeks")

## Inbox Tasks
### Overdue
- [Task title] (Due: [date]) 🔴

### Due Today
- [Task title] 🟡

### Upcoming
- [Task title] (Due: [date])

### No Due Date
- [Task title]

## Quick Wins (estimated < 30 min)
- [Task title] (~[X] min)
```

### 7. Build the HTML email
Construct the email body as HTML with inline CSS only:

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
          [For each notable upcoming event: <p style="margin:0 0 8px;padding:10px 14px;background:#f0f4ff;border-left:3px solid #4a6fa5;border-radius:4px;font-size:14px;color:#333;"><strong>[Mon 7 Apr, 14:00]</strong> — [Title]</p>]
          [If none: <p style="margin:0;color:#999;font-size:14px;font-style:italic;">Nothing notable in the next 4 weeks</p>]
        </td></tr>

        <!-- Inbox Tasks -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">✅ Inbox Tasks</h2>

          [If overdue:]
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#c0392b;letter-spacing:0.5px;">Overdue</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fff5f5;border-left:3px solid #e74c3c;border-radius:4px;font-size:14px;color:#333;">[Title] <span style="color:#e74c3c;font-size:12px;">[due date]</span></p>]

          [If due today:]
          <p style="margin:12px 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#e67e22;letter-spacing:0.5px;">Due Today</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fffaf0;border-left:3px solid #f39c12;border-radius:4px;font-size:14px;color:#333;">[Title]</p>]

          [If upcoming:]
          <p style="margin:12px 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;color:#27ae60;letter-spacing:0.5px;">Upcoming</p>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#f0fff4;border-left:3px solid #2ecc71;border-radius:4px;font-size:14px;color:#333;">[Title] <span style="color:#888;font-size:12px;">[due date]</span></p>]

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
        <tr><td style="padding:24px 32px;border-top:1px solid #eee;">
          <p style="margin:0;color:#aaa;font-size:12px;text-align:center;">Generated by your Daily Briefing Agent</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
```

### 8. Send via Gmail MCP
First call `mcp__gmail__gmail_get_profile` to get the user's email address.
Then call `mcp__gmail__gmail_send` with:
- to: the email address from above
- subject: `Daily Briefing — [WEEKDAY, DATE]`
- body: the full HTML from step 7
- mimeType: `text/html`

If Gmail fails, note it in the saved file but do not stop.

### 9. Confirm completion
Output: `Briefing saved to output/daily-briefing-YYYY-MM-DD.md`

## Constraints
- Do NOT mark any Todoist tasks as complete — only the user does that
- Do NOT include tasks from named Todoist projects — Inbox only
- Do NOT delete or modify any calendar events
- Do NOT run any git commands
- Keep estimates clearly labelled as estimates
