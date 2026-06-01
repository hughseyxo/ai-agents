import asyncio
import base64
import io
import json
import logging
import os
import re
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
    get_travel_report,
    update_plant,
    get_plant,
    get_all_plants,
    save_plant_assessment,
    note_plant_observation,
)
from tool_specs import openai_tools, func_map
from claude_backend import ask_claude

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

REPO_ROOT = Path(__file__).parent.parent
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
TOOL_FUNCTIONS = func_map()

TOOLS = openai_tools()


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

_WATERING_REC_MAP = {
    "immediate": "immediate", "now": "immediate", "water now": "immediate",
    "on schedule": "on_schedule", "on_schedule": "on_schedule", "schedule": "on_schedule",
    "delay": "delay", "delay watering": "delay",
}


def _extract_assessment_from_text(raw: str) -> dict | None:
    """Try to salvage structured fields from a non-JSON markdown assessment response."""
    status_m = re.search(r'\*{0,2}[Ss]tatus\*{0,2}\s*[:\-]\s*([A-Za-z]+)', raw)
    summary_m = re.search(r'\*{0,2}[Ss]ummary\*{0,2}\s*[:\-]\s*(.+?)(?=\n\*{0,2}[A-Z]|\Z)', raw, re.DOTALL)
    watering_m = re.search(r'\*{0,2}[Ww]atering[^:\n]*\*{0,2}\s*[:\-]\s*([A-Za-z _]+)', raw)
    obs_m = re.search(r'\*{0,2}[Oo]bservations?\*{0,2}\s*[:\-]\s*(.+?)(?=\n\*{0,2}[A-Z]|\Z)', raw, re.DOTALL)

    status = status_m.group(1).strip() if status_m else "Assessment"
    summary = summary_m.group(1).strip().rstrip("*").strip() if summary_m else raw[:300].strip()
    rec_raw = watering_m.group(1).strip().lower() if watering_m else ""
    rec = next((v for k, v in _WATERING_REC_MAP.items() if k in rec_raw), None)
    obs_text = obs_m.group(1).strip() if obs_m else ""
    obs = [l.lstrip("•*- ").strip() for l in obs_text.splitlines() if l.strip()] if obs_text else []

    if not (status_m or summary_m):
        return None
    return {"status": status, "summary": summary, "observations": obs,
            "watering_recommendation": rec, "frequency_suggestion": None, "profile_notes": ""}


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


_VALIDATOR_PROMPT_PATH = REPO_ROOT / "agents" / "prompts" / "plant_assessment_validator.md"


def _validate_plant_assessment(parsed: dict, plant: dict) -> tuple[dict, bool]:
    """Validate vision model JSON via agy/claude subprocess (text-only, no tool access).
    Returns (result, was_corrected)."""
    try:
        template = _VALIDATOR_PROMPT_PATH.read_text()
    except Exception:
        return parsed, False

    species_context = _load_species_context(plant["name"])
    prompt = (template
              .replace("{{plant_name}}", plant["name"])
              .replace("{{species_reference}}", species_context or "No reference available.")
              .replace("{{assessment_json}}", json.dumps(parsed, indent=2)))

    # Safe invocation: prompt via stdin, no --dangerously-skip-permissions.
    # Without that flag any tool-use attempt needs a terminal to prompt — there is none
    # in a subprocess, so agy/claude produce text-only output.
    for cmd in [
        ["agy", "-y", "-o", "text"],
        ["claude", "--print", "--output-format", "text"],
    ]:
        try:
            result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                    cwd=str(REPO_ROOT), timeout=60)
            if result.returncode != 0 or not result.stdout.strip():
                continue
            response = result.stdout.strip()
            if response.upper() == "VALID":
                return parsed, False
            text = re.sub(r"^```[a-z]*\n?", "", response)
            text = re.sub(r"\n?```$", "", text.strip())
            corrected = json.loads(text)
            return corrected, True
        except Exception:
            continue
    return parsed, False


_GENERIC_ASSESSMENT_CAPTIONS = {"assess", "check", "identify"}


def _build_identification_context(plants: list) -> str:
    """One-liner per plant: formal name (common names) + healthy indicator description."""
    lines = []
    for plant in plants:
        section = _load_species_context(plant["name"])
        indicator, also_known = "", ""
        for line in section.splitlines():
            if line.startswith("**Healthy indicators:**"):
                indicator = line.replace("**Healthy indicators:**", "").strip()
            elif line.startswith("**Also known as:**"):
                also_known = line.replace("**Also known as:**", "").strip()
        label = plant["name"]
        if also_known:
            label += f" (also: {also_known})"
        lines.append(f"- {label}: {indicator}" if indicator else f"- {label}")
    return "\n".join(lines)


def _build_common_name_lookup(plants: list) -> dict:
    """Map lowercase common/alias names → plant dict."""
    lookup = {}
    for plant in plants:
        section = _load_species_context(plant["name"])
        for line in section.splitlines():
            if line.startswith("**Also known as:**"):
                aliases = line.replace("**Also known as:**", "").strip()
                for alias in aliases.split(","):
                    lookup[alias.strip().lower()] = plant
    return lookup


def _identify_plant_from_image(image_bytes: bytes, plants: list) -> dict | None:
    """Ask vision model which known plant is in the image. Returns plant dict or None."""
    plant_context = _build_identification_context(plants)
    common_name_lookup = _build_common_name_lookup(plants)
    prompt = (
        "Which plant from the following list is shown in the photo?\n"
        "Visual indicators and common names are provided to help you match what you see.\n\n"
        f"{plant_context}\n\n"
        "Reply with ONLY the formal plant name from the list (copy it exactly as written "
        "before any parentheses), or NONE if you cannot identify it with confidence."
    )
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]}]
    for model in VISION_MODELS:
        try:
            response = client.chat.completions.create(model=model, messages=messages)
            answer = (response.choices[0].message.content or "").strip().strip(".,!?\"'")
            if answer.upper() == "NONE":
                return None
            al = answer.lower()
            match = (
                next((p for p in plants if p["name"].lower() == al), None)
                or next((p for p in plants if p["name"].lower() in al or al in p["name"].lower()), None)
                or common_name_lookup.get(al)
            )
            if match:
                return match
        except Exception:
            continue
    return None


_WATERING_LABELS = {"immediate": "💧 Water now", "on_schedule": "✅ On schedule", "delay": "⏳ Delay watering"}
_STATUS_EMOJI = {"Healthy": "🟢", "Stressed": "🟡", "Concerning": "🟠", "Underwatered": "🔵", "Overwatered": "🔴"}


def _build_assessment_display(parsed: dict, plant: dict) -> str:
    status = parsed.get("status", "Assessment")
    summary = parsed.get("summary", "")
    obs = parsed.get("observations", [])
    rec = parsed.get("watering_recommendation", "")
    freq = parsed.get("frequency_suggestion")
    emoji = _STATUS_EMOJI.get(status, "⚪")
    lines = [f"{emoji} *{plant['name']}* — {status}", "", summary]
    if obs:
        lines += ["", "*Observations:*"] + [f"• {o}" for o in obs]
    if rec:
        lines += ["", _WATERING_LABELS.get(rec, f"Watering: {rec}")]
    if freq and isinstance(freq, dict):
        lines += [f"📅 Suggested frequency: every {freq.get('days')} days"]
    return "\n".join(lines)


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

    # Try to parse as JSON — handle code fences, leading/trailing prose, and partial wrapping
    try:
        text = raw_response.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text.strip())
        # If there's prose around the JSON, extract just the outermost {...} object
        if not text.startswith("{"):
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                text = m.group(0)
        parsed = json.loads(text)
        # Validate consistency with a text LLM (catches vision model contradictions)
        parsed, was_corrected = _validate_plant_assessment(parsed, plant)
        if was_corrected:
            existing_notes = parsed.get("profile_notes", "")
            parsed["profile_notes"] = existing_notes + "\n[Validator: structured fields corrected for consistency with observations]"
            logger.info(f"[{plant['name']}] Assessment corrected by validation LLM")
        display = _build_assessment_display(parsed, plant)
        return display, parsed
    except (json.JSONDecodeError, ValueError, KeyError):
        # JSON parse failed — try to salvage structured fields from markdown prose
        salvaged = _extract_assessment_from_text(raw_response)
        if salvaged:
            return _build_assessment_display(salvaged, plant), salvaged
        return f"*{plant['name']}*\n\n{raw_response}", None


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

    # Primary backend: claude CLI (Claude-quality chat + native MCP tool use,
    # multi-turn memory). Runs off the event loop since the subprocess blocks.
    # Returns None on any failure → fall through to OpenRouter / Antigravity.
    try:
        reply = await asyncio.get_running_loop().run_in_executor(
            None, ask_claude, update.effective_chat.id, update.message.text
        )
    except Exception as e:
        logger.warning(f"claude backend errored, falling back: {e}")
        reply = None
    if reply:
        for chunk in [reply[i:i + 4000] for i in range(0, len(reply), 4000)]:
            await update.message.reply_text(chunk)
        return

    logger.info("claude backend unavailable, falling back to OpenRouter")

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
            await update.message.reply_text(chunk, parse_mode="Markdown")

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
        header = f"**{plant['name']}**\n\n"
        full_text = header + display_text
        for chunk in [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]:
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
