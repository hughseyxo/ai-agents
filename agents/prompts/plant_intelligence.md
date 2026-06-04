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
5. Pruning needs — based on assessment notes, growth stage, season, and plant type. Flag if a plant is overdue for deadheading, shape pruning, post-bloom cut-back, or size control.

## Steps

**Step 1: Analyse** all plant data. Decide what's notable per plant, which plants need a photo, what email to write, and any action tasks.

**Step 2: Send the email** via mcp__gmail__gmail_send:
- to: cianohughes@gmail.com
- subject: Plant Intelligence Report — {{today}}
- mimeType: text/html
- body: your insight summary wrapped in a minimal HTML shell (dark theme: background #0d1117, text #c9d1d9, font Arial). Cover: key findings, patterns, watering recommendations, and pruning actions needed. Max 400 words.

**Step 3: Create tasks** in Todoist project 6Crf3cH2RF5v86wc via mcp__todoist__add-tasks. Create tasks for clearly actionable items — both care tasks (e.g. "Review Monstera watering frequency") and pruning tasks (e.g. "Deadhead Marguerite daisies", "Prune Lavender after bloom", "Cut back Fuchsia"). For each, check with mcp__todoist__find-tasks first and skip if it already exists. If nothing actionable, skip this step.

**Step 4: Output JSON** — your entire text output after completing the MCP calls above must be ONLY the following JSON block, with no prose, no markdown explanation, no code fences. Use exact plant names from the state JSON.

{
  "plants": [
    {
      "name": "Plant Name As In State JSON",
      "status": "Healthy",
      "notes": ["bullet point about this plant", "another observation"],
      "needs_photo": false,
      "frequency_change": null
    }
  ],
  "pruning": [
    {"name": "Plant Name", "action": "deadhead spent blooms", "reason": "encourage reblooming"}
  ],
  "tasks_created": ["Task title you created", "Another task"],
  "email_sent": true
}

Field rules:
- Include a `plants` entry for every plant where there is something worth noting. Skip plants with nothing to report.
- `status`: one of `Healthy`, `Stressed`, `Overwatered`, `Underwatered`, `Concerning`
- `notes`: 2–5 bullet points about patterns, trends, anything notable from the data
- `needs_photo`: true if the plant needs a photo check (concerning patterns, last assessment >14 days ago, or weather stress)
- `frequency_change`: null if no change needed. If the plant's **baseline** watering cadence should change based on observations, set `{"days": N, "reason": "short reason"}`. Changes are clamped to ±2 days per run; large moves converge over several runs. Do NOT pre-adjust for today's weather — the system folds that in automatically.
- `pruning`: one entry per plant that needs a pruning action. Empty array if none.
- `tasks_created`: titles of all tasks you created in Step 3. Empty array if none.
- `email_sent`: true if you successfully sent the email in Step 2, false if it failed.
