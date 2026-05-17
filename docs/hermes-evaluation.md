# Hermes Evaluation and Viability Report

## Objective
Evaluate the efforts made to get the Hermes gateway working using CLI-based proxy backends (`google-gemini-cli` and `claude_code`). The primary goal is to determine if Hermes can reliably operate using existing Gemini Pro and Claude Pro subscriptions, avoiding pay-as-you-go API costs.

## Summary of Efforts & Errors Encountered

Based on the logs (`.hermes/logs/errors.log` and `.hermes/logs/agent.log`) and configuration (`.hermes/config.yaml`), the following approaches have been attempted:

### 1. Gemini Integration (`google-gemini-cli`)
- **Setup:** Hermes was configured to route requests through `google-gemini-cli` using a custom base URL (`cloudcode-pa://google`).
- **Issues Encountered:**
  - `Provider 'gemini' is set in config.yaml but no API key was found.` initially indicated standard API paths were being attempted.
  - `OAuth provider google-gemini-cli not directly supported`: The internal HTTP client in Hermes failed to resolve the custom OAuth flow required by the CLI.
  - `Code Assist 404: Requested entity was not found`: When Hermes attempted to call `gemini-1.5-flash` and `gemini-3-flash-preview` via the Code Assist endpoint, Google's backend returned 404s. The Code Assist endpoint uses specific internal model names and does not map to standard Gemini model identifiers.
  - `HTTP 429: Gemini quota exhausted`: Even when a request partially succeeded or was retried, it quickly hit a hidden quota limit (`Your quota will reset after 56s.`), proving the endpoint is highly restricted.

### 2. Claude Integration (`claude_code`)
- **Setup:** Hermes was configured to use Anthropic as a provider with the source set to `claude_code`, attempting to proxy requests to avoid API fees.
- **Issues Encountered:**
  - `HTTP 404: model: claude-3-5-haiku-20241022`: Initial attempts to use standard model names failed because the proxy setup didn't support them.
  - `HTTP 400: Third-party apps now draw from your extra usage, not your plan limits.`: When Hermes successfully routed requests using the proxy models (`claude-haiku-4-5` and `claude-sonnet-4-6`), Anthropic's backend explicitly rejected them. 

### 3. Local Model Integration (`Ollama`)
- **Setup:** Hermes was temporarily configured to use a local Ollama instance (`http://127.0.0.1:11434/v1`) running `hermes3:3b`.
- **Issues Encountered:**
  - The API request (`say pong`) was successfully dispatched but completely hung. The system waited for over 5 minutes without receiving any response or error before the gateway was eventually terminated/restarted.
  - Because this is a CPU-only OVH seedbox without dedicated GPU hardware, running even small models like `hermes3:3b` locally is impractically slow and results in severe timeouts.

## Viability Assessment

**The plan to run Hermes purely on Pro subscriptions via CLI proxying or Local Models is currently NOT VIABLE.**

1. **Claude is Blocked:** Anthropic has implemented strict billing enforcement. Their backend now detects third-party usage (like the CLI being driven programmatically or proxied) and explicitly refuses to draw from the Claude Pro subscription plan. It strictly requires a prepaid API balance ("extra usage"). There is no known workaround for this policy.
2. **Gemini is Brittle and Rate-Limited:** The `google-gemini-cli` relies on an undocumented, internal Google Cloud Code endpoint (`cloudcode-pa://google`). This endpoint is heavily rate-limited (as seen in the 429 errors), does not support the latest standard models, and requires complex OAuth handling that Hermes currently fails to implement reliably.
3. **Local Models (Ollama) Timeout:** The server's hardware (CPU-only seedbox) is insufficient for inference. Requests simply hang indefinitely.

## Conclusion

If avoiding pay-as-you-go API costs is a strict requirement, Hermes cannot function reliably on this hardware under current vendor restrictions. To make Hermes viable, the only stable path forward is to provision actual pay-as-you-go API keys (e.g., Google AI Studio, Anthropic Console, or an aggregator like OpenRouter).