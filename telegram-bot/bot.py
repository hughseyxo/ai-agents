import json
import logging
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI, APIError
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from tools import (
    get_agent_status,
    get_plant_status,
    get_yopflix_status,
    get_system_health,
    get_cron_schedule,
    get_agent_logs,
)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
ALLOWED_USER_ID = os.getenv("TELEGRAM_USER_ID", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
    default_headers={
        "HTTP-Referer": "https://github.com/hughseyxo/ai-agents",
        "X-Title": "Server Concierge",
    },
)

FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "minimax/minimax-m2.5:free",
]

CONCIERGE_PATH = Path(__file__).parent / "CONCIERGE.md"
SYSTEM_PROMPT = CONCIERGE_PATH.read_text() if CONCIERGE_PATH.exists() else (
    "You are a concierge assistant for Cian's home server. Be concise and direct."
)

TOOL_FUNCTIONS = {
    "get_agent_status": get_agent_status,
    "get_plant_status": get_plant_status,
    "get_yopflix_status": get_yopflix_status,
    "get_system_health": get_system_health,
    "get_cron_schedule": get_cron_schedule,
    "get_agent_logs": lambda agent_name="": get_agent_logs(agent_name),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_agent_status",
            "description": "Get the last run status of all server agents (daily-briefing, news-briefing, security-audit).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plant_status",
            "description": "Get the watering schedule for all tracked plants including next watering date and overdue flags.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_yopflix_status",
            "description": "Get the yopflix/seedbox status: enabled services, running Docker containers, and disk usage.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_health",
            "description": "Get server system health: CPU usage, RAM usage, and uptime.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cron_schedule",
            "description": "Get the cron schedule for all agents showing when they run (in CEST).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_logs",
            "description": "Get recent log output. Optionally filter by agent name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Agent name to filter by (e.g. 'daily-briefing'). Leave empty for all logs.",
                    }
                },
                "required": [],
            },
        },
    },
]


def _call_gemini_fallback(user_message: str, system_prompt: str) -> str:
    """Execute all tools, inject results, call Gemini CLI as a flat prompt."""
    state_parts = []
    for name, fn in TOOL_FUNCTIONS.items():
        try:
            result = fn()
        except Exception as e:
            result = f"unavailable: {e}"
        state_parts.append(f"### {name}\n{result}")

    state = "\n\n".join(state_parts)
    prompt = (
        f"{system_prompt}\n\n"
        f"## Current Server State\n\n{state}\n\n"
        f"## User Question\n\n{user_message}"
    )

    res = subprocess.run(
        ["gemini", "-y", "-p", prompt, "-o", "text"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).parent),
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    raise RuntimeError(f"Gemini CLI failed (rc={res.returncode}): {res.stderr[:200]}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your server concierge. "
        "Ask me about agent status, plants, yopflix, system health, cron schedules, or recent logs."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_USER_ID and str(update.effective_user.id) != ALLOWED_USER_ID:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": update.message.text},
    ]

    for model in FREE_MODELS:
        try:
            loop_messages = list(messages)
            for _ in range(3):
                response = client.chat.completions.create(
                    model=model,
                    messages=loop_messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
                choice = response.choices[0]

                if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                    loop_messages.append(choice.message)
                    for tc in choice.message.tool_calls:
                        fn = TOOL_FUNCTIONS.get(tc.function.name)
                        if fn:
                            try:
                                args = json.loads(tc.function.arguments or "{}")
                                result = fn(**args)
                            except Exception as e:
                                result = f"Tool error: {e}"
                        else:
                            result = f"Unknown tool: {tc.function.name}"
                        loop_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                else:
                    await update.message.reply_text(choice.message.content or "(no response)")
                    return

            await update.message.reply_text("(reached tool call limit, could not complete response)")
            return

        except APIError as e:
            logger.warning(f"Model {model} failed ({e.status_code}), trying next")
            continue
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await update.message.reply_text("Sorry, unexpected error. Please try again.")
            return

    # All OpenRouter models exhausted — fall back to Gemini CLI
    logger.warning("All OpenRouter models failed, falling back to Gemini CLI")
    try:
        reply = _call_gemini_fallback(update.message.text, SYSTEM_PROMPT)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Gemini fallback failed: {e}")
        await update.message.reply_text("All AI backends unavailable. Please try again later.")


def main() -> None:
    if not TELEGRAM_TOKEN or not OPENROUTER_KEY:
        logger.error("Missing TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Concierge bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
