# Design Doc: OpenRouter Telegram Bot

## Problem
The previous Telegram orchestrator (Hermes) relied on local Ollama inference and CLI-proxying. On the current CPU-only hardware, local inference was unacceptably slow (timeouts), and vendor restrictions (Anthropic/Google) blocked programmatic use of CLI-bound Pro subscriptions via API.

## Goal
Provide a fast, reliable, and cost-effective Telegram chat interface to Gemini, Claude, and open-source models without requiring expensive monthly API commitments or hitting hardware bottlenecks.

## Design Decisions
- **OpenRouter as Backend:** Aggregates multiple LLMs into a single OpenAI-compatible API.
- **Free-Tier Focus:** Leverage OpenRouter's free-to-use models (e.g., Llama 3, Gemma) to maintain zero-cost operation.
- **Standard SDK:** Use the official `openai` Python SDK for stability and ease of maintenance.
- **Lightweight Frontend:** Use `python-telegram-bot` for the interface, running as a systemd service.
- **Stateless (Initial):** Basic request-response model, with plans for per-chat context memory in the future.

## Architecture
1. **Telegram User** sends message to Bot.
2. **Python Bot** (running on Seedbox) receives message via Polling.
3. **Python Bot** forwards prompt to **OpenRouter API** via HTTPS.
4. **OpenRouter** routes to provider (Meta, Google, etc.).
5. **Response** flows back to Python Bot and is delivered to User.

## Data Model
- **Secrets:** Stored in `.env` (TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY).
- **Configuration:** Model selection and system prompts hardcoded in `bot.py` or `.env`.

## File List
- `~/git/ai-agents/telegram-bot/bot.py`: Main application logic.
- `~/git/ai-agents/telegram-bot/.env`: Private API keys (gitignored).
- `~/git/ai-agents/telegram-bot/.env.example`: Template for setup.
- `~/git/ai-agents/telegram-bot/requirements.txt` (or `.venv` managed by `uv`): Dependencies.

## Future Enhancements
- **Conversation Memory:** Store last N turns in a dictionary keyed by `chat_id`.
- **Model Switching:** Command-based switching between Gemini, Claude, and Llama.
- **Systemd Integration:** Create a user-level service for persistence.
