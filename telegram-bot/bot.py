import base64
import io
import json
import logging
import os
import subprocess
import tempfile
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
    run_travel_agent,
    get_travel_report,
    water_plant,
    save_recipe,
    get_plant,
    get_all_plants,
    save_plant_assessment,
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

VISION_MODELS = [
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "qwen/qwen2.5-vl-7b-instruct:free",
]

PLANT_HEALTH_SYSTEM = (
    "You are a plant health expert. Assess the plant in the photo. "
    "Give specific observations about its health and concise actionable advice. "
    "Keep your response to 3-5 sentences."
)

CONCIERGE_PATH = Path(__file__).parent / "CONCIERGE.md"
SYSTEM_PROMPT = CONCIERGE_PATH.read_text() if CONCIERGE_PATH.exists() else (
    "You are a concierge assistant for Cian's home server. Be concise and direct."
)

# State-reading tools — called in Gemini fallback to build context snapshot
STATE_TOOL_FUNCTIONS = {
    "get_agent_status": get_agent_status,
    "get_plant_status": get_plant_status,
    "get_yopflix_status": get_yopflix_status,
    "get_system_health": get_system_health,
    "get_cron_schedule": get_cron_schedule,
    "get_agent_logs": lambda agent_name="": get_agent_logs(agent_name),
    "get_travel_report": get_travel_report,
}

# All tools available to the LLM (state + action tools)
TOOL_FUNCTIONS = {
    **STATE_TOOL_FUNCTIONS,
    "run_travel_agent": run_travel_agent,
    "water_plant": water_plant,
    "save_recipe": save_recipe,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_agent_status",
            "description": "Get the last run status of all server agents (daily-briefing, news-briefing, security-audit, travel-agent).",
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
    {
        "type": "function",
        "function": {
            "name": "run_travel_agent",
            "description": (
                "Launch the travel agent in the background to research or plan a trip. "
                "Use mode='search' to find flights, hotels, and activities. "
                "Use mode='plan' when the user already has flights and accommodation booked and wants a day-by-day itinerary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "Destination city or country (e.g. 'Barcelona', 'Japan').",
                    },
                    "checkin": {
                        "type": "string",
                        "description": "Check-in / arrival date in YYYY-MM-DD format.",
                    },
                    "checkout": {
                        "type": "string",
                        "description": "Check-out / departure date in YYYY-MM-DD format.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["search", "plan"],
                        "description": "search=find flights+hotels, plan=itinerary from existing bookings. Default: search.",
                    },
                    "origin": {
                        "type": "string",
                        "description": "Departure city for search mode (e.g. 'Dublin', 'Amsterdam').",
                    },
                    "flights": {
                        "type": "string",
                        "description": "Plan mode only: existing flight details as free text (e.g. 'Ryanair FR1234 DUB->BCN 06:30, return 22:00').",
                    },
                    "hotel": {
                        "type": "string",
                        "description": "Plan mode only: existing hotel booking as free text (e.g. 'H10 Marina Barcelona, 7 nights').",
                    },
                },
                "required": ["destination", "checkin", "checkout"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_travel_report",
            "description": "Check whether the latest travel research report is ready and return its filename and size.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "water_plant",
            "description": "Record that a plant was watered today. Use when the user says they watered a plant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant (e.g. 'Monstera').",
                    }
                },
                "required": ["plant_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_recipe",
            "description": "Save a recipe URL to Mealie. Use when the user sends a recipe link or asks to save a recipe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The recipe URL.",
                    }
                },
                "required": ["url"],
            },
        },
    },
]


def _call_gemini_fallback(user_message: str, system_prompt: str) -> str:
    """Execute state-reading tools, inject results, call Gemini CLI as a flat prompt."""
    state_parts = []
    for name, fn in STATE_TOOL_FUNCTIONS.items():
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


def _resolve_plant_name(caption: str, plants: list[dict]) -> dict | None:
    """Use a text LLM to semantically match a user's description to a known plant.

    Handles cases like 'passion flower' -> 'Passiflora' that substring matching can't catch.
    """
    names = ", ".join(p["name"] for p in plants)
    prompt = (
        f"Known plants: {names}\n"
        f"The user referred to their plant as: \"{caption}\"\n"
        "Which plant are they referring to? Reply with the exact name from the list, "
        "or NONE if no match is likely. Reply with only the plant name or NONE."
    )
    for model in FREE_MODELS:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = (response.choices[0].message.content or "").strip()
            if answer.upper() == "NONE":
                return None
            match = next((p for p in plants if p["name"].lower() == answer.lower()), None)
            if match:
                return match
        except Exception:
            continue
    return None


def _analyze_plant_image(image_bytes: bytes, plant: dict) -> str:
    """Analyze a plant image using vision models with Gemini fallback."""
    from datetime import date as _date
    last_watered = plant.get("last_watered", "unknown")
    try:
        days_since = (_date.today() - _date.fromisoformat(last_watered)).days
        days_str = f"{days_since} days ago"
    except Exception:
        days_str = "unknown"

    user_text = (
        f"This is a {plant['name']} ({plant.get('location', 'unknown location')}). "
        f"Last watered {last_watered} ({days_str})."
    )
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "system", "content": PLANT_HEALTH_SYSTEM},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        prompt = f"{PLANT_HEALTH_SYSTEM}\n\n{user_text}"
        res = subprocess.run(
            ["gemini", "-y", "-p", prompt, "-o", "text", tmp_path],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).parent),
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        logger.warning(f"Gemini vision failed (rc={res.returncode}): {res.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Gemini vision exception: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    for model in VISION_MODELS:
        try:
            response = client.chat.completions.create(model=model, messages=messages)
            return response.choices[0].message.content or "No assessment returned."
        except Exception:
            continue

    return "Plant assessment unavailable right now. Try again later."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your server concierge. "
        "Ask me about agent status, plants, yopflix, system health, cron schedules, recent logs, "
        "or say 'search flights to Barcelona 1–7 July from Dublin' to kick off travel research."
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_USER_ID and str(update.effective_user.id) != ALLOWED_USER_ID:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text(
            "Please include the plant name as a caption (e.g. 'monstera')."
        )
        return

    plant = get_plant(caption)
    if not plant:
        all_plants = get_all_plants()
        plant = _resolve_plant_name(caption, all_plants)
    if not plant:
        names = ", ".join(p["name"] for p in all_plants) or "none"
        await update.message.reply_text(
            f"No plant named '{caption}' found. Known plants: {names}"
        )
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    image_bytes = buf.getvalue()

    assessment = _analyze_plant_image(image_bytes, plant)
    save_result = save_plant_assessment(plant["name"], assessment)
    if "saved" not in save_result.lower():
        logger.warning(f"Failed to save plant assessment: {save_result}")
    await update.message.reply_text(assessment)


def main() -> None:
    if not TELEGRAM_TOKEN or not OPENROUTER_KEY:
        logger.error("Missing TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Concierge bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
