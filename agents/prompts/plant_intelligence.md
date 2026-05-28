You are analyzing all plant data to produce insights, update plant profiles, and flag any plants needing attention.

## Input Data

**Current date:** {{today}}

**Plant state:**
{{plant_state_json}}

**Weather-adjusted watering cache:**
{{weather_cache_json}}

**Plant profiles:**
{{plant_profiles}}

**Recent agent notes:**
{{agent_notes}}

## Your Task

Analyze all data to identify:
1. Patterns in watering adherence vs schedule
2. Plants showing signs of stress, overwatering, or frequency mismatch based on data trends
3. Plants that would benefit from a photo health check (long gap since last assessment, concerning data patterns, weather stress)
4. Actionable changes to watering frequency or care

## Output

Produce the following delimited sections FIRST, before any MCP calls. All sections are required even if empty.

**Per-plant profile notes** (one block per plant where there is something worth noting):

[PROFILE:Plant Name As In State JSON]
2-5 bullet points about patterns, trends, anything notable from the data.
Use the plant's actual name exactly as it appears in the plant state JSON.
[/PROFILE]

**Photo check flags:**

[NEEDS_PHOTO]
Comma-separated list of plant names that should get a photo check soon. Criteria: concerning data patterns, last assessment >14 days ago, or weather stress warranting visual verification. If none: leave empty.
[/NEEDS_PHOTO]

**Insight email body:**

[EMAIL]
Plain text or simple markdown. What changed or was noticed across all plants. Actionable recommendations. Max 300 words. Address the user directly by name if known, otherwise directly ("Your plants...").
[/EMAIL]

**Todoist action tasks:**

[TASKS]
One per line, format: Content: "task text", due: YYYY-MM-DD, priority: p3
Only for clearly actionable items (e.g. "Review Monstera watering frequency", "Check outdoor plants after heatwave").
If none needed, leave empty.
[/TASKS]

## After producing the above sections:

**Send the email** via mcp__gmail__gmail_send:
- to: cianohughes@gmail.com
- subject: Plant Intelligence Report — {{today}}
- mimeType: text/html
- body: wrap the [EMAIL] content in a minimal HTML shell (dark theme: background #0d1117, text #c9d1d9)

**Create the tasks** from [TASKS] in Todoist project 6Crf3cH2RF5v86wc via mcp__todoist__add-tasks.
For each task, first check with mcp__todoist__find-tasks whether it already exists (by content), and skip if it does.

Output the delimited sections FIRST, then perform the MCP calls.
