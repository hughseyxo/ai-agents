---
name: mealsave
description: Use when user runs /mealsave <url>, says "save this recipe", "add this to Mealie", "add to mealie", or explicitly asks to import a recipe URL into their Mealie instance. Do NOT trigger on general URL pastes or links unrelated to recipe saving.
allowed-tools: Bash,Read,Write
---

# mealsave

Saves a recipe URL to the user's self-hosted Mealie instance with no manual intervention.
Returns the Mealie recipe URL on success, or fails with a clear error.

## When Invoked

Run the script with the venv Python:

```bash
~/.claude/skills/mealsave/.venv/bin/python ~/.claude/skills/mealsave/mealsave.py <url>
```

## Interpreting Output

- **Success**: script prints a single URL — echo it to the user as the new recipe link
- **"Recipe already in Mealie: ..."**: tell the user and show the existing URL; don't re-run
- **ERROR: ...** on stderr + non-zero exit: surface the error message directly to the user; do not retry silently

## What the Script Does

1. Reads `~/.config/mealsave/.env` for `MEALIE_URL` and `MEALIE_TOKEN`
2. Checks Mealie is reachable
3. Checks for duplicate (same `orgURL`)
4. Tries Mealie's built-in scraper (`/api/recipes/create-url`) — handles most schema.org recipe sites natively
5. Falls back to YouTube transcript → Claude LLM extraction for YouTube URLs
6. Falls back to trafilatura text extraction → Claude LLM extraction for any other URL Mealie couldn't handle
7. Always sets `orgURL` for traceability

## Setup Check

If the user hasn't set up the skill yet (venv missing, config missing), direct them to `~/.claude/skills/mealsave/README.md`.
