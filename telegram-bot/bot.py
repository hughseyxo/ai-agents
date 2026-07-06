import asyncio
import io
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.plant_profiles import write_health_assessment, write_profile_atomic, safe_profile_path
from agents import garden_notes
from agents.plant_assessment import (
    load_species_context as _load_species_context,
    format_care_actions_for_profile as _format_care_actions_for_profile,
    parse_assessment_response,
)
from tools import (
    update_plant,
    get_plant,
    get_all_plants,
    save_plant_assessment,
)
from claude_backend import ask_claude, assess_image
from antigravity_backend import ask_antigravity, query_agy

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_USER_ID", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

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
    answer = query_agy(prompt)
    if answer:
        answer = answer.strip()
        if answer.upper() != "NONE":
            al = answer.lower()
            match = (
                next((p for p in plants if p["name"].lower() == al), None)
                or next((p for p in plants if p["name"].lower() in al or al in p["name"].lower()), None)
            )
            if match:
                return match
    # Last-resort substring match against the raw caption. (handle_photo already
    # tries get_plant() — itself substring-based — before calling this, so in
    # practice this only fires for non-photo callers.)
    cl = caption.lower()
    return next((p for p in plants if p["name"].lower() in cl or cl in p["name"].lower()), None)


_GENERIC_ASSESSMENT_CAPTIONS = {"assess", "check", "identify"}


@contextmanager
def _temp_image(image_bytes: bytes):
    """Write image bytes into a private temp dir, yield the file path, clean up after.

    Isolated in its own directory so `assess_image`'s `--add-dir` exposes only this
    one image to the Read-enabled subprocess, not the whole system temp dir.
    """
    with tempfile.TemporaryDirectory(prefix="plant-") as d:
        path = os.path.join(d, "image.jpg")
        Path(path).write_bytes(image_bytes)
        yield path


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


_IDENTIFICATION_SYSTEM = (
    "You are a plant identification expert. You will be shown a photo and a list of "
    "known plants. Reply with ONLY the formal plant name from the list (copied exactly "
    "as written, before any parentheses), or NONE if you cannot identify it confidently."
)


def _identify_plant_from_image(image_bytes: bytes, plants: list) -> dict | None:
    """Ask the claude CLI which known plant is in the image. Returns plant dict or None."""
    plant_context = _build_identification_context(plants)
    common_name_lookup = _build_common_name_lookup(plants)
    user_text = (
        "Which plant from the following list is shown in the photo?\n"
        "Visual indicators and common names are provided to help you match what you see.\n\n"
        f"{plant_context}\n\n"
        "Reply with ONLY the formal plant name from the list, or NONE."
    )
    with _temp_image(image_bytes) as path:
        answer = assess_image(path, _IDENTIFICATION_SYSTEM, user_text)
    if not answer:
        return None
    answer = answer.strip().strip(".,!?\"'")
    if answer.upper() == "NONE":
        return None
    al = answer.lower()
    return (
        next((p for p in plants if p["name"].lower() == al), None)
        or next((p for p in plants if p["name"].lower() in al or al in p["name"].lower()), None)
        or common_name_lookup.get(al)
    )


def _analyze_plant_image(image_bytes: bytes, plant: dict) -> tuple[str, dict | None]:
    """Analyze a plant image. Returns (display_text, parsed_json_or_None)."""
    from datetime import date as _date
    last_watered = plant.get("last_watered", "unknown")
    try:
        days_since = (_date.today() - _date.fromisoformat(last_watered)).days
        days_str = f"{days_since} days ago"
    except Exception:
        days_str = "unknown"

    # Load species reference for this plant
    species_context = _load_species_context(plant["name"])
    system_prompt = PLANT_ASSESSMENT_SYSTEM
    if species_context:
        system_prompt += f"\n\n## Species Reference\n{species_context}"

    user_text = (
        f"This is a {plant['name']} ({plant.get('location', 'unknown location')}). "
        f"Last watered {last_watered} ({days_str}). "
        f"Base watering frequency: every {plant.get('frequency_days', '?')} days."
    )
    with _temp_image(image_bytes) as path:
        # Profile context injected inside assess_image via read_profile_context
        raw_response = assess_image(path, system_prompt, user_text, plant_name=plant["name"])

    if not raw_response:
        return "Plant assessment unavailable right now. Try again later.", None

    return parse_assessment_response(raw_response, plant["name"])


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

    loop = asyncio.get_running_loop()

    async def _chunk_reply(text: str) -> None:
        for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
            await update.message.reply_text(chunk)

    # Primary backend: Antigravity CLI. Runs off the event loop since the
    # subprocess blocks. Returns None on any failure → fall through to Claude.
    try:
        reply = await loop.run_in_executor(
            None, ask_antigravity, update.effective_chat.id, update.message.text
        )
    except Exception as e:
        logger.warning(f"antigravity backend errored, falling back: {e}")
        reply = None
    if reply:
        await _chunk_reply(reply)
        return

    logger.info("antigravity backend unavailable, falling back to Claude")

    # Secondary backend: claude CLI.
    try:
        reply = await loop.run_in_executor(
            None, ask_claude, update.effective_chat.id, update.message.text
        )
    except Exception as e:
        logger.warning(f"claude backend errored: {e}")
        reply = None
    if reply:
        await _chunk_reply(reply)
        return

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
        # C12: assess_image runs a blocking subprocess — keep it off the event loop.
        plant = await asyncio.to_thread(_identify_plant_from_image, image_bytes, all_plants)
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

    # C12: assess_image runs a blocking subprocess — keep it off the event loop.
    display_text, parsed = await asyncio.to_thread(_analyze_plant_image, image_bytes, plant)

    if parsed:
        # Save structured notes to plant profile doc, folding in prioritised care actions.
        profile_notes = parsed.get("profile_notes", "")
        care_block = _format_care_actions_for_profile(parsed.get("care_actions"))
        if profile_notes or care_block:
            write_health_assessment(plant["name"], (profile_notes + care_block).strip())

        # Auto-create a standalone observation note when the assessment is noteworthy.
        try:
            garden_notes.maybe_create_observation_note(plant["name"], parsed)
        except Exception as e:
            logger.warning("observation note creation failed: %s", e)

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
        # Fallback: plain text behaviour — but don't persist error strings as valid assessments
        if not display_text.startswith("Plant assessment unavailable"):
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
    # C10: callbacks were ungated — enforce the same allowlist as message/photo
    # so a stranger's button press can't mutate plant state.
    if ALLOWED_USER_ID and str(update.effective_user.id) != ALLOWED_USER_ID:
        return
    await query.answer()

    data = query.data or ""

    if data.startswith("plant_freq_dismiss:"):
        plant_name = data.split(":", 1)[1]
        await query.edit_message_text(f"Dismissed frequency change for {plant_name}.")
        return

    if data.startswith("plant_freq:"):
        # Format is plant_freq:<name>:<days>. The name may itself contain a colon,
        # so split off the trailing days field with rpartition rather than a fixed
        # 3-way split (which would reject or mangle such names).
        payload = data[len("plant_freq:"):]
        plant_name, sep, new_days_str = payload.rpartition(":")
        if not sep or not plant_name:
            await query.edit_message_text("Invalid callback data.")
            return
        try:
            new_days = int(new_days_str)
        except ValueError:
            await query.edit_message_text("Invalid frequency value.")
            return

        # C13: update_plant's 2nd positional arg is `location`; a dict here silently
        # no-ops the frequency change. Pass frequency_days as a keyword.
        update_plant(plant_name, frequency_days=new_days)

        # Append to plant profile doc
        from datetime import date as _date
        today = _date.today().isoformat()
        freq_note = f"\n| {today} | ?→{new_days} days | Applied via Telegram photo assessment |"
        # C11: bound the untrusted plant name to a safe path inside the plants
        # directory; safe_profile_path raises if the slug escapes.
        try:
            profile_path = safe_profile_path(plant_name)
        except ValueError:
            profile_path = None
        if profile_path and profile_path.exists():
            existing = profile_path.read_text()
            if "## Frequency History" in existing:
                # Phase 1.3: atomic write so a crash can't truncate the profile.
                write_profile_atomic(profile_path, existing.rstrip() + freq_note + "\n")

        await query.edit_message_text(
            f"✓ Updated {plant_name} watering frequency to every {new_days} days."
        )


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("Missing TELEGRAM_BOT_TOKEN.")
        return

    if not ALLOWED_USER_ID:
        logger.error("Missing TELEGRAM_USER_ID — refusing to start unauthenticated (fail closed).")
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
