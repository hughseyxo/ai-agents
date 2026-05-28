import base64
import io
import json
import logging
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI, APIError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes

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
    add_plant,
    update_plant,
    remove_plant,
    research_plant_watering,
    research_plant_sunlight,
    save_recipe,
    get_plant,
    get_all_plants,
    save_plant_assessment,
    note_plant_observation,
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
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "meta-llama/llama-3.2-11b-vision-instruct",
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

PLANT_ASSESSMENT_DIR = Path(__file__).parent.parent / "docs" / "plants"
_ASSESSMENT_PROMPT_PATH = Path(__file__).parent.parent / "agents" / "prompts" / "plant_photo_assessment.md"
PLANT_ASSESSMENT_SYSTEM = _ASSESSMENT_PROMPT_PATH.read_text() if _ASSESSMENT_PROMPT_PATH.exists() else PLANT_HEALTH_SYSTEM

# State-reading tools — called in Antigravity fallback to build context snapshot
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
    "get_plant": get_plant,
    "get_all_plants": get_all_plants,
    "run_travel_agent": run_travel_agent,
    "water_plant": water_plant,
    "add_plant": add_plant,
    "update_plant": update_plant,
    "remove_plant": remove_plant,
    "research_plant_watering": research_plant_watering,
    "research_plant_sunlight": research_plant_sunlight,
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
            "name": "add_plant",
            "description": "Add a new plant to the watering tracker. Use when the user wants to track a new plant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the plant (e.g. 'Monstera Deliciosa').",
                    },
                    "frequency_days": {
                        "type": "integer",
                        "description": "How often to water the plant in days (e.g. 7 for weekly).",
                    },
                    "location": {
                        "type": "string",
                        "enum": ["indoor", "outdoor"],
                        "description": "Whether the plant is indoors or outdoors. Default: indoor.",
                    },
                    "sunlight": {
                        "type": "string",
                        "enum": ["full sun", "partial shade", "shade"],
                        "description": "Sunlight requirements. Call research_plant_sunlight first if unsure.",
                    },
                },
                "required": ["name", "frequency_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plant",
            "description": "Update a plant's location, watering frequency, or sunlight requirements. Use when the user says a plant is indoor/outdoor, wants to change watering frequency, or specify sunlight needs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant to update (e.g. 'Gazania').",
                    },
                    "location": {
                        "type": "string",
                        "enum": ["indoor", "outdoor"],
                        "description": "New location for the plant.",
                    },
                    "frequency_days": {
                        "type": "integer",
                        "description": "New watering frequency in days.",
                    },
                    "sunlight": {
                        "type": "string",
                        "enum": ["full sun", "partial shade", "shade"],
                        "description": "New sunlight requirements for the plant.",
                    },
                },
                "required": ["plant_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_plant_sunlight",
            "description": "Look up sunlight requirements for a plant. Returns 'full sun', 'partial shade', or 'shade'. Call before add_plant or update_plant when sunlight is unknown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant to research (e.g. 'Monstera Deliciosa').",
                    }
                },
                "required": ["plant_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_plant_watering",
            "description": "Look up the recommended watering frequency for a plant using web search. Call this before add_plant when the user hasn't specified how often to water.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant to research (e.g. 'Monstera Deliciosa').",
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
    {
        "type": "function",
        "function": {
            "name": "get_plant",
            "description": "Look up a single plant by name (exact or substring match). Returns plant details or null if not found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant to look up (e.g. 'Monstera').",
                    }
                },
                "required": ["plant_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_plants",
            "description": "Get the full list of all tracked plants with their details (name, location, sunlight, watering frequency, last watered, last assessment).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_plant",
            "description": "Remove a plant from the watering tracker. Use when the user says a plant has died or they no longer want to track it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "Name of the plant to remove (e.g. 'Monstera').",
                    }
                },
                "required": ["plant_name"],
            },
        },
    },
]


def _call_antigravity_fallback(user_message: str, system_prompt: str) -> str:
    """Execute state-reading tools, inject results, call Antigravity CLI as a flat prompt."""
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
        ["agy", "-y", "-o", "text"],
        input=prompt,
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).parent),
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    raise RuntimeError(f"Antigravity CLI failed (rc={res.returncode}): {res.stderr[:200]}")


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


_SPECIES_REFERENCE_PATH = Path(__file__).parent.parent / "docs" / "plants" / "species_reference.md"


def _load_species_context(plant_name: str) -> str:
    """Extract the section for this plant from species_reference.md. Returns '' on any failure."""
    try:
        text = _SPECIES_REFERENCE_PATH.read_text()
        heading = f"## {plant_name}"
        start = text.find(heading)
        if start == -1:
            return ""
        # Find next ## heading after the start
        next_heading = text.find("\n## ", start + len(heading))
        section = text[start:next_heading].strip() if next_heading != -1 else text[start:].strip()
        return section
    except Exception:
        return ""


_GENERIC_ASSESSMENT_CAPTIONS = {"assess", "check", "identify"}


def _identify_plant_from_image(image_bytes: bytes, plants: list) -> dict | None:
    """Ask vision model which known plant is in the image. Returns plant dict or None."""
    names = "\n".join(f"- {p['name']}" for p in plants)
    prompt = (
        f"Which plant from this list is shown in the photo?\n{names}\n\n"
        "Reply with ONLY the exact plant name from the list, or NONE if you cannot identify it."
    )
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]}]
    for model in VISION_MODELS:
        try:
            response = client.chat.completions.create(model=model, messages=messages)
            answer = (response.choices[0].message.content or "").strip()
            if answer.upper() == "NONE":
                return None
            match = next((p for p in plants if p["name"].lower() == answer.lower()), None)
            if match:
                return match
        except Exception:
            continue
    return None


def _analyze_plant_image(image_bytes: bytes, plant: dict) -> tuple[str, dict | None]:
    """Analyze a plant image. Returns (display_text, parsed_json_or_None)."""
    from datetime import date as _date
    last_watered = plant.get("last_watered", "unknown")
    try:
        days_since = (_date.today() - _date.fromisoformat(last_watered)).days
        days_str = f"{days_since} days ago"
    except Exception:
        days_str = "unknown"

    # Load plant profile doc for context
    slug = plant["name"].lower().replace(" ", "-")
    profile_path = PLANT_ASSESSMENT_DIR / f"{slug}.md"
    profile_context = ""
    if profile_path.exists():
        profile_context = f"\n\nPlant profile history:\n{profile_path.read_text()}"

    # Load species reference for this plant
    species_context = _load_species_context(plant["name"])
    system_prompt = PLANT_ASSESSMENT_SYSTEM
    if species_context:
        system_prompt += f"\n\n## Species Reference\n{species_context}"

    user_text = (
        f"This is a {plant['name']} ({plant.get('location', 'unknown location')}). "
        f"Last watered {last_watered} ({days_str})."
        f"Base watering frequency: every {plant.get('frequency_days', '?')} days."
        f"{profile_context}"
    )
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]

    raw_response = None
    for model in VISION_MODELS:
        try:
            response = client.chat.completions.create(model=model, messages=messages)
            raw_response = response.choices[0].message.content or ""
            break
        except Exception as e:
            logger.warning(f"Vision model {model} failed: {e}")
            continue

    if not raw_response:
        return "Plant assessment unavailable right now. Try again later.", None

    # Try to parse as JSON
    try:
        import re as _re
        text = raw_response.strip()
        if text.startswith("```"):
            text = _re.sub(r"^```[a-z]*\n?", "", text)
            text = _re.sub(r"\n?```$", "", text.strip())
        parsed = json.loads(text)
        # Build display text from structured data
        display = f"**{parsed.get('status', 'Assessment')}**\n\n{parsed.get('summary', '')}"
        obs = parsed.get("observations", [])
        if obs:
            display += "\n\n**Observations:**\n" + "\n".join(f"• {o}" for o in obs)
        rec = parsed.get("watering_recommendation")
        if rec:
            display += f"\n\n**Watering:** {rec}"
        return display, parsed
    except (json.JSONDecodeError, ValueError, KeyError):
        # JSON parse failed — return raw text, no structured data
        return raw_response, None


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
            for _ in range(5):
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
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
                            "content": str(result),
                        })
                else:
                    await update.message.reply_text(choice.message.content or "(no response)")
                    return

            await update.message.reply_text("Sorry, this request needs too many steps. Try asking something more specific.")
            return

        except APIError as e:
            logger.warning(f"Model {model} failed ({e.status_code}), trying next")
            continue
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await update.message.reply_text("Sorry, unexpected error. Please try again.")
            return

    # All OpenRouter models exhausted — fall back to Antigravity CLI
    logger.warning("All OpenRouter models failed, falling back to Antigravity CLI")
    try:
        reply = _call_antigravity_fallback(update.message.text, SYSTEM_PROMPT)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Antigravity fallback failed: {e}")
        await update.message.reply_text("All AI backends unavailable. Please try again later.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_USER_ID and str(update.effective_user.id) != ALLOWED_USER_ID:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    caption = (update.message.caption or "").strip()

    if not caption or caption.lower() in _GENERIC_ASSESSMENT_CAPTIONS:
        # Visual identification path — download image then ask vision model which plant it is
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        all_plants = get_all_plants()
        plant = _identify_plant_from_image(image_bytes, all_plants)
        if not plant:
            await update.message.reply_text(
                "Couldn't identify the plant from the photo. Send it again with the plant name as caption."
            )
            return
    else:
        # Text-based lookup — no download needed if plant not found
        plant = get_plant(caption)
        if not plant:
            all_plants = get_all_plants()
            plant = _resolve_plant_name(caption, all_plants)
        if not plant:
            names = ", ".join(p["name"] for p in get_all_plants()) or "none"
            await update.message.reply_text(
                f"No plant named '{caption}' found. Known plants: {names}"
            )
            return

        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

    display_text, parsed = _analyze_plant_image(image_bytes, plant)

    if parsed:
        # Save structured notes to plant profile doc
        profile_notes = parsed.get("profile_notes", "")
        if profile_notes:
            note_plant_observation(plant["name"], profile_notes)

        # Save assessment summary to DB state (existing behaviour)
        save_plant_assessment(plant["name"], parsed.get("summary", display_text))

        # Send assessment text
        for chunk in [display_text[i:i+4000] for i in range(0, len(display_text), 4000)]:
            await update.message.reply_text(chunk)

        # If frequency change suggested, send inline keyboard
        freq_suggestion = parsed.get("frequency_suggestion")
        if freq_suggestion and isinstance(freq_suggestion, dict):
            new_days = freq_suggestion.get("days")
            reason = freq_suggestion.get("reason", "")
            current_days = plant.get("frequency_days", "?")
            if new_days and new_days != current_days:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✓ Apply",
                        callback_data=f"plant_freq:{plant['name']}:{new_days}"
                    ),
                    InlineKeyboardButton(
                        "✗ Dismiss",
                        callback_data=f"plant_freq_dismiss:{plant['name']}"
                    ),
                ]])
                await update.message.reply_text(
                    f"💡 Suggest changing *{plant['name']}* watering: "
                    f"{current_days}→{new_days} days\n_{reason}_",
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
    else:
        # Fallback: plain text behaviour
        save_result = save_plant_assessment(plant["name"], display_text)
        if "saved" not in save_result.lower():
            logger.warning(f"Failed to save plant assessment: {save_result}")
        for chunk in [display_text[i:i+4000] for i in range(0, len(display_text), 4000)]:
            await update.message.reply_text(chunk)


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks for plant frequency change proposals."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data.startswith("plant_freq_dismiss:"):
        plant_name = data.split(":", 1)[1]
        await query.edit_message_text(f"Dismissed frequency change for {plant_name}.")
        return

    if data.startswith("plant_freq:"):
        parts = data.split(":")
        if len(parts) != 3:
            await query.edit_message_text("Invalid callback data.")
            return
        _, plant_name, new_days_str = parts
        try:
            new_days = int(new_days_str)
        except ValueError:
            await query.edit_message_text("Invalid frequency value.")
            return

        update_plant(plant_name, {"frequency_days": new_days})

        # Append to plant profile doc
        from datetime import date as _date
        today = _date.today().isoformat()
        freq_note = f"\n| {today} | ?→{new_days} days | Applied via Telegram photo assessment |"
        slug = plant_name.lower().replace(" ", "-")
        profile_path = PLANT_ASSESSMENT_DIR / f"{slug}.md"
        if profile_path.exists():
            existing = profile_path.read_text()
            if "## Frequency History" in existing:
                existing = existing.rstrip() + freq_note + "\n"
                profile_path.write_text(existing)

        await query.edit_message_text(
            f"✓ Updated {plant_name} watering frequency to every {new_days} days."
        )


def main() -> None:
    if not TELEGRAM_TOKEN or not OPENROUTER_KEY:
        logger.error("Missing TELEGRAM_BOT_TOKEN or OPENROUTER_API_KEY.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(_handle_callback))
    logger.info("Concierge bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
