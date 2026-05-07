# Workflow: Daily Morning Briefing

## Purpose
Generate a concise daily briefing every morning covering the user's calendar and outstanding tasks. This helps plan the day and avoid missing commitments.

## Steps

### 1. Import MCP connectors
Use ToolSearch to load all required MCP tools before proceeding:
- Search `"todoist find-tasks"` to import the Todoist connector
- Search `"google_calendar gcal_list_events"` to import the local Google Calendar MCP server
- Search `"gmail gmail_send"` to import the local Gmail connector

**Todoist retry:** If the Todoist tools are not found on the first ToolSearch attempt, retry up to 3 times with a 10-second pause (use `sleep 10` via Bash) between attempts. Todoist MCP connections can be slow to initialize in remote sessions.

**Todoist REST API fallback:** If MCP is still unavailable after 3 retries, fall back to the Todoist REST API directly. Use WebFetch (or Bash with curl) to call:
- `GET https://api.todoist.com/rest/v2/tasks?project_id=inbox` with header `Authorization: Bearer $TODOIST_API_TOKEN`
- Read the TODOIST_API_TOKEN from the environment variable (it's exported by the shell script)
- The response is a JSON array of task objects with fields: `content`, `priority` (4=p1, 3=p2, 2=p3, 1=p4), `due.date`, `due.string`
- Use this data for both the Inbox Tasks section and the Coming Up tasks

If both MCP and REST API fail, continue without Todoist data and note the failure in the report.

### 2. Get today's date
Note today's date and the date 28 days from now in YYYY-MM-DD format.

### 3. Fetch today's calendar events
Call `mcp__google_calendar__gcal_list_events` with:
- startTime: today at 00:00:00 (ISO 8601, e.g. `2026-04-29T00:00:00`)
- endTime: today at 23:59:59 (ISO 8601, e.g. `2026-04-29T23:59:59`)
- timeZone: `Europe/Berlin`

### 4. Fetch upcoming events (next 30 days)
Make three parallel calls:

**Call A — Primary calendar:**
Call `mcp__google_calendar__gcal_list_events` with:
- calendarId: `cianohughes@gmail.com`
- startTime: tomorrow 00:00:00 (ISO 8601)
- endTime: 30 days from now 23:59:59 (ISO 8601)
- timeZone: `Europe/Berlin`
- pageSize: 50

**Call B — On-call calendar:**
Call `mcp__google_calendar__gcal_list_events` with:
- calendarId: `8ubfqbcooeks9np5aufgu7g3mm0gj1rh@import.calendar.google.com`
- startTime: tomorrow 00:00:00 (ISO 8601)
- endTime: 30 days from now 23:59:59 (ISO 8601)
- timeZone: `Europe/Berlin`
- pageSize: 50

From **Call A**, filter to notable events only — exclude all-day date markers, recurring daily events, events under 15 min. Keep meetings, appointments, one-off events.

From **Call B**, include ALL on-call shifts — label each one clearly as `[ON CALL]`.

Merge both lists, sort chronologically, cap at 15 events total.

**Call C — Todoist upcoming tasks:**
Use `mcp__todoist__find-tasks-by-date` with `startDate: "today"`, `daysCount: 30`, and `limit: 50` — tasks with a due date within the next 30 days. Include these in the Coming Up section labelled as `[TASK]`.

### 5. Fetch Inbox tasks from Todoist
Call `mcp__todoist__find-tasks` with `projectId: "inbox"`, `limit: 50` to retrieve only incomplete Inbox tasks.
- Include task priority and due date where available
- Order by: overdue first, then by priority (p1 → p4), then by due date
- Do NOT include tasks from any named projects

### 6. Plant watering check
Read `~/plants.json` (array of `{name, frequency_days, last_watered}`).

For each plant:
1. Calculate `next_water_date = last_watered + frequency_days`
2. If `next_water_date` is within 7 days from today:
   a. Search Todoist for an incomplete task named exactly `Water [Plant Name]` in project `6Crf3cH2RF5v86wc` (Personal). Use `mcp__todoist__find-tasks` with a filter or the REST API: `GET https://api.todoist.com/rest/v2/tasks?project_id=6Crf3cH2RF5v86wc` and check for matching `content`.
   b. If no matching task exists:
      - Create a Todoist task via `mcp__todoist__add-tasks` with: `content: "Water [Plant Name]"`, `projectId: "6Crf3cH2RF5v86wc"`, `dueString: "[next_water_date]"`, `priority: "p4"`
      - Update `last_watered` to `next_water_date` in `~/plants.json` using the Write tool
3. Collect a list of plants needing water in the next 7 days (with their next_water_date) for the report.

If `~/plants.json` doesn't exist or is empty, skip this step silently.

### 7. Format the markdown report
Save to `output/daily-briefing-YYYY-MM-DD.md`:

```
# Daily Briefing — [DATE]

## Today's Schedule
- [TIME] — [Event Title]
- (If no events: "No events scheduled today")

## Inbox Tasks
### Overdue
- [Task title] (Due: [date]) 🔴

### Due Today
- [Task title] 🟡

### Upcoming
- [Task title] (Due: [date])

### No Due Date
- [Task title]

## Plant Watering
- [DATE] — Water [Plant Name]
- (If no plants need water soon: "All plants are happy!")

## Quick Wins (estimated < 30 min)
- [Task title] (~[X] min)

## Coming Up (next 30 days)
- [DATE] — [Event Title]              (calendar events)
- [DATE] — [ON CALL] On-Call Shift    (on-call shifts)
- [DATE] — [TASK] [Task title]        (Todoist tasks)
```

### 8. Build the HTML email
Construct the email body as HTML with inline CSS only.

**Include a Plant Watering section** in the HTML between Inbox Tasks and Quick Wins:

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

        <!-- Plant Watering -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">🌱 Plant Watering</h2>
          [For each plant needing water: <p style="margin:0 0 6px;padding:8px 12px;background:#f0fff4;border-left:3px solid #27ae60;border-radius:4px;font-size:14px;color:#333;"><strong>[DATE]</strong> — Water [Plant Name]</p>]
          [If none: <p style="margin:0;color:#999;font-size:14px;font-style:italic;">All plants are happy!</p>]
        </td></tr>

        <!-- Quick Wins -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">⚡ Quick Wins</h2>
          [For each: <p style="margin:0 0 6px;padding:8px 12px;background:#fafafa;border-radius:4px;font-size:14px;color:#333;">[Title] <span style="color:#888;font-size:12px;">~[X] min</span></p>]
          [If none: omit this section]
        </td></tr>

        <!-- Coming Up -->
        <tr><td style="padding:20px 32px 0;">
          <h2 style="margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#1a1a2e;">📆 Coming Up</h2>
          [For each calendar event: <p style="margin:0 0 6px;padding:8px 12px;background:#f0f4ff;border-left:3px solid #4a6fa5;border-radius:4px;font-size:14px;color:#333;"><strong>[DATE]</strong> — [Title]</p>]
          [For each on-call shift: <p style="margin:0 0 6px;padding:8px 12px;background:#fff8f0;border-left:3px solid #e67e22;border-radius:4px;font-size:14px;color:#333;"><strong>[DATE]</strong> — <span style="color:#e67e22;font-weight:700;">[ON CALL]</span> On-Call Shift</p>]
          [For each Todoist task: <p style="margin:0 0 6px;padding:8px 12px;background:#f0fff4;border-left:3px solid #2ecc71;border-radius:4px;font-size:14px;color:#333;"><strong>[DATE]</strong> — <span style="color:#27ae60;font-weight:700;">[TASK]</span> [Title]</p>]
          [All sorted chronologically, max 15 entries total]
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

### 9. Send via Gmail MCP
Call `mcp__gmail__gmail_send` with:
- to: `cianohughes@gmail.com`
- subject: `Daily Briefing — [WEEKDAY, DATE]`
- body: the full HTML from step 7
- mimeType: `text/html`

If Gmail fails, note it in the saved file but do not stop.

### 10. Confirm completion
Output: `Briefing saved to output/daily-briefing-YYYY-MM-DD.md and email sent.`

## Constraints
- Do NOT mark any Todoist tasks as complete or modify them — read-only
- Do NOT include tasks from named Todoist projects — Inbox only
- Do NOT delete or modify any calendar events
- Do NOT run any git commands
- Keep estimates clearly labelled as estimates
