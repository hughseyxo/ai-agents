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

## Steps

**Step 1: Analyse** all plant data. Decide what's notable per plant, which plants need a photo, what email to write, and any action tasks.

**Step 2: Send the email** via mcp__gmail__gmail_send:
- to: cianohughes@gmail.com
- subject: Plant Intelligence Report — {{today}}
- mimeType: text/html
- body: your insight summary wrapped in a minimal HTML shell (dark theme: background #0d1117, text #c9d1d9, font Arial). Cover: key findings, patterns, recommendations. Max 300 words.

**Step 3: Create tasks** in Todoist project 6Crf3cH2RF5v86wc via mcp__todoist__add-tasks. Only clearly actionable items (e.g. "Review Monstera watering frequency"). For each, check with mcp__todoist__find-tasks first and skip if it exists. If nothing actionable, skip this step.

**Step 4: Output the following structured sections** — this is your final text output, produced AFTER the MCP calls above.

All sections required. Use exact plant names from the state JSON.

[PROFILE:Plant Name As In State JSON]
2-5 bullet points about patterns, trends, anything notable from the data for this plant.
[/PROFILE]

(one [PROFILE] block per plant where there is something worth noting)

[NEEDS_PHOTO]
Comma-separated list of plant names that should get a photo check. Criteria: concerning patterns, last assessment >14 days ago, or weather stress. Empty if none.
[/NEEDS_PHOTO]

[EMAIL]
The insight summary text you sent in Step 2 (plain text version, for the record).
[/EMAIL]

[TASKS]
Tasks you created in Step 3, one per line. Empty if none.
[/TASKS]
