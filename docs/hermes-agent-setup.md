# Hermes Agent — fix plan to reach desired goal state

## Current state

- Hermes v0.13.0 installed at `~/.local/bin/hermes`, configured at `~/.hermes/config.yaml`.
- Telegram gateway wired up in `~/.hermes/.env` and `config.yaml` (lines 386, 491, 532).
- `model.default: gemini-flash-latest` via `gemini` (direct API).
- Auth in place for `google-gemini-cli` (OAuth) and presumably Anthropic via Claude Code creds.
- **`providers: {}`, `fallback_providers: []`, `credential_pool_strategies: {}` — all empty.**

## Desired goal state

Hermes runs day-to-day (CLI + Telegram) without 429/400 errors, with transparent failover between Gemini and Claude. Calls draw on existing Pro subscriptions (Gemini OAuth daily quota, Claude Pro via `claude_code` cred) — no third-party billing surprises.

## Gap → fixes

### Fix 1: Stop the direct Gemini API path

Default is `gemini-flash-latest` via the `gemini` provider — that hits the AI Studio project key, whose **monthly spend cap is exceeded** (HTTP 429 RESOURCE_EXHAUSTED in `errors.log` 12:36–12:37). Switch the default provider to `google-gemini-cli` (OAuth) which has daily-quota headroom (`/gquota` confirms).

`~/.hermes/config.yaml`:
```yaml
model:
  default: gemini-flash-latest
  provider: google-gemini-cli
  base_url: cloudcode-pa://google
```

### Fix 2: Stop calling `gemini-3.1-pro-preview`

That model has tight per-minute RPM limits and was the source of every Gemini 429 in the log between 12:11–12:13. Find what's selecting it (a skill, a session override, or `/model` history) and pin to `gemini-flash-latest` or `gemini-2.5-flash`.

- Check `~/.hermes/skills/*/skill.yaml` for `model:` overrides.
- Check `~/.hermes/sessions/` for sticky session model overrides.
- Check `~/.hermes/.hermes_history` for prior `/model` switches that may have persisted.

### Fix 3: Stop calling `claude-opus-4-6`

Pro doesn't cover Opus, so the call goes via the third-party billing path and 400s with the "extra usage" message (`errors.log` 12:16). Pin Anthropic calls to Sonnet, which Pro does cover.

### Fix 4: Populate `fallback_providers`

Currently empty — when the primary 429s, nothing catches. Add:

```yaml
fallback_providers:
  - provider: google-gemini-cli
    model: gemini-flash-latest          # same as primary, but explicit for the chain
  - provider: anthropic
    model: claude-sonnet-4-6            # Pro quota via claude_code cred
    source: claude_code
  - provider: anthropic
    model: claude-haiku-4-5             # lighter Pro fallback
    source: claude_code
```

Verify with `hermes fallback` or by inducing a primary failure.

### Fix 5: Declare the Anthropic provider with `claude_code` source

`agent/anthropic_adapter.py` already supports reading `~/.claude/.credentials.json` and impersonating the CLI (User-Agent `claude-cli/<version>`). Make sure the provider entry exists:

```yaml
providers:
  anthropic:
    source: claude_code
```

Run `hermes auth anthropic` if `~/.hermes/auth.json` doesn't show an Anthropic entry sourced from `claude_code`.

### Fix 6: Silence the noise providers

`openrouter` (no credit) and `nous` (no auth) generate WARN lines every few minutes (`errors.log` 11:13–12:11) as Hermes probes them for auxiliary tasks. Either:
- Run `hermes auth nous` to fix Nous, top up OpenRouter; OR
- Disable them in `auxiliary:` config (lines 161–231 in `config.yaml`) by setting `provider: ''` or using a working provider.

Cosmetic but worth doing — keeps the log readable.

## Verification

1. `hermes` interactive: prompt → response from `gemini-flash-latest` via `google-gemini-cli`. Confirm in `~/.hermes/logs/agent.log`.
2. Tail `~/.hermes/logs/errors.log` for 24h → no HTTP 400/429.
3. Force a primary failure (rename auth file briefly, or set bogus model) → confirm Hermes falls back to Anthropic Sonnet, history preserved.
4. From Telegram: send a message to the home channel → response arrives.

## Out of scope (for now)

- Rewiring `agents/base.py:synthesize()` to call Hermes. This is deferred until Hermes is stable and the prerequisites below are met.
- Topping up direct Gemini API key, OpenRouter, Anthropic extra-usage credit.
- Migrating `free_time_bot.py` / `mealsave-bot.service` into Hermes skills.

## Prerequisites for Future `base.py` Integration

When Hermes is eventually integrated as the backend for `BaseAgent.synthesize()`, the following architectural gaps must be addressed:

1. **Timeout Idempotency:** Hermes must be configured to **never fail over on a timeout**. As documented in `docs/llm-failover.md`, a timed-out LLM process may have already executed side effects (e.g., creating a calendar event or sending an email). Failing over on a timeout risks duplicating these actions. Hermes must pass timeouts up to the Python layer as terminal errors.
2. **Prompt Adaptation:** `base.py` currently handles runtime prompt translation (e.g., stripping Claude's `ToolSearch` instructions and remapping `mcp__` namespaces for Gemini). Hermes does not currently perform this translation natively. This logic must either be ported into Hermes or retained in Python before the prompt is passed to the Hermes binary.
