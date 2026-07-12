"""Batch plant-photo processing: sequential identify+assess job with
pause/resume on detected Claude usage-limit hits, plus shared photo-history
persistence used by both the batch flow and the single-photo upload
endpoint (one consistent mechanism, per the design doc).

Rides the existing FastAPI process (asyncio.create_task) and AgentDB for
persistence — no new service, no new dependencies.
"""

import asyncio
import logging
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import AgentDB
from .plant_model import PlantStore, AssessmentRecord
from . import garden_notes
from .plant_profiles import write_health_assessment, safe_profile_path
from .plant_assessment import load_species_context, format_care_actions_for_profile, parse_assessment_response

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "telegram-bot") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))
from claude_backend import assess_image, identify_and_assess  # noqa: E402

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 5 * 60
MAX_PAUSE_SECONDS = 2 * 60 * 60
PHOTO_RETENTION = 10
TREND_WINDOW = 3

PHOTO_STORE_DIR = REPO_ROOT / "data" / "plant-photos"
BATCH_TEMP_DIR = REPO_ROOT / "data" / "plant-photo-batches"

PLANT_ASSESSMENT_SYSTEM_PATH = REPO_ROOT / "agents" / "prompts" / "plant_photo_assessment.md"
PLANT_HEALTH_SYSTEM_FALLBACK = (
    "You are a plant health expert. Assess the plant in the photo. "
    "Give specific observations about its health and concise actionable advice. "
    "Keep your response to 3-5 sentences."
)

IDENTIFY_AND_ASSESS_SYSTEM = (
    "You are a plant identification and health assessment expert. You will be shown "
    "a photo and a list of known plants. First identify which plant (if any) is "
    "shown, then give a preliminary health assessment based on this one photo.\n\n"
    "Respond with ONLY a JSON object (no markdown fences) with these keys: "
    "matched_plant (the formal name copied exactly from the list, or null if no "
    "confident match), confidence (\"high\", \"medium\", \"low\", or null), "
    "status, summary, observations (list of strings), "
    "watering_recommendation (\"immediate\", \"on_schedule\", or \"delay\"), "
    "care_actions (list of {action, reason, priority}), noteworthy (bool), "
    "note_title, note_body, profile_notes (markdown snippet), "
    "frequency_suggestion (null or {days, reason})."
)


# --- Photo history persistence (shared by batch + single-photo flows) ---


def save_plant_photo(db: AgentDB, plant_name: str, image_bytes: bytes,
                      assessment_summary: str = None, assessment_status: str = None,
                      keep: int = PHOTO_RETENTION) -> str:
    """Persist a photo permanently under data/plant-photos/<slug>/, record it
    in AgentDB, and prune anything beyond the last `keep` photos for this
    plant (deleting the pruned files too). Returns the saved file path."""
    slug = safe_profile_path(plant_name).stem
    plant_dir = PHOTO_STORE_DIR / slug
    plant_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%f")
    dest = plant_dir / f"{timestamp}.jpg"
    dest.write_bytes(image_bytes)

    db.add_plant_photo(plant_name, str(dest), assessment_summary=assessment_summary,
                        assessment_status=assessment_status)

    for pruned_path in db.prune_plant_photos(plant_name, keep=keep):
        Path(pruned_path).unlink(missing_ok=True)

    return str(dest)


def get_trend_photo_paths(db: AgentDB, plant_name: str, limit: int = TREND_WINDOW) -> list[str]:
    """Last `limit` stored photos for this plant, oldest-first — the order
    assess_image's extra_image_paths expects for chronological comparison."""
    rows = db.get_recent_plant_photos(plant_name, limit=limit)  # newest-first
    return [r["file_path"] for r in reversed(rows)]


def persist_assessment(store: PlantStore, db: AgentDB, plant, parsed: dict, image_path: str) -> str:
    """Shared persistence step for a successful assessment: save the photo
    permanently, write profile markdown + optional observation note, and
    update the plant's last_assessment record."""
    image_bytes = Path(image_path).read_bytes()
    status = parsed.get("status", "Assessment")
    summary = parsed.get("summary", "")

    saved_path = save_plant_photo(db, plant.name, image_bytes,
                                   assessment_summary=summary, assessment_status=status)

    profile_notes = parsed.get("profile_notes", "")
    care_block = format_care_actions_for_profile(parsed.get("care_actions") or [])
    if profile_notes or care_block:
        write_health_assessment(plant.name, (profile_notes + care_block).strip())

    try:
        garden_notes.maybe_create_observation_note(plant.name, parsed)
    except Exception as e:
        logger.warning("observation note creation failed for %s: %s", plant.name, e)

    plant.last_assessment = AssessmentRecord(date=date.today(), summary=summary, status=status)
    plant.needs_photo = False
    store.update_plant(plant)
    return saved_path


# --- Prompt building ---


def _build_identification_context(plants: list[dict]) -> str:
    lines = []
    for plant in plants:
        section = load_species_context(plant["name"])
        indicator = ""
        for line in section.splitlines():
            if line.startswith("**Healthy indicators:**"):
                indicator = line.replace("**Healthy indicators:**", "").strip()
        lines.append(f"- {plant['name']}: {indicator}" if indicator else f"- {plant['name']}")
    return "\n".join(lines)


def _identification_user_text(plants: list[dict]) -> str:
    context = _build_identification_context(plants)
    return (
        "Which plant from the following list (if any) is shown in the photo?\n"
        "Visual indicators are provided to help you match what you see.\n\n"
        f"{context}\n\n"
        "If you can't confidently match it to any plant in the list, set matched_plant to null."
    )


def _assessment_system_prompt(plant_name: str) -> str:
    system_prompt = (
        PLANT_ASSESSMENT_SYSTEM_PATH.read_text()
        if PLANT_ASSESSMENT_SYSTEM_PATH.exists() else PLANT_HEALTH_SYSTEM_FALLBACK
    )
    species_context = load_species_context(plant_name)
    if species_context:
        system_prompt += f"\n\n## Species Reference\n{species_context}"
    return system_prompt


def _plant_user_text(plant) -> str:
    last_watered = plant.last_watered.isoformat()
    try:
        days_since = (date.today() - plant.last_watered).days
        days_str = f"{days_since} days ago"
    except Exception:
        days_str = "unknown"
    return (
        f"This is a {plant.name} ({plant.location}). "
        f"Last watered {last_watered} ({days_str}). "
        f"Base watering frequency: every {plant.frequency_days} days."
    )


# --- Batch job creation ---


def create_batch_job(db: AgentDB, image_bytes_list: list[bytes], plant_names: list[str] = None) -> int:
    """Create a new batch job for N uploaded photos, persisting each
    original to a job-scoped temp dir (survives a plant_ui service restart,
    unlike a tempfile.TemporaryDirectory) and creating the AgentDB job row.

    plant_names, if given, is a parallel list (same length/order as
    image_bytes_list) of user-picked plant names — a non-empty entry skips
    the identify Opus call entirely for that photo (_process_item goes
    straight to the trend-assessment call), since the user already knows
    which plant it is. An empty string/None entry falls back to AI
    auto-identification, same as before this parameter existed."""
    plant_names = plant_names or [None] * len(image_bytes_list)
    placeholder_items = [{"status": "pending"} for _ in image_bytes_list]
    job_id = db.create_batch_job(placeholder_items)
    job_dir = BATCH_TEMP_DIR / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for i, image_bytes in enumerate(image_bytes_list):
        path = job_dir / f"{i}.jpg"
        path.write_bytes(image_bytes)
        preassigned = (plant_names[i] or "").strip() or None
        items.append({
            "temp_path": str(path), "status": "pending",
            "matched_plant": preassigned, "confidence": "user-assigned" if preassigned else None,
            "result": None, "error": None,
        })
    db.update_batch_job(job_id, items=items)
    return job_id


def _cleanup_job_dir(job_id: int) -> None:
    job_dir = BATCH_TEMP_DIR / str(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


# --- Per-item processing ---


def _process_item(item: dict, plants: list[dict], store: PlantStore, db: AgentDB) -> tuple[dict, bool]:
    """Process one batch item: call 1 (identify+assess) unless the user
    already picked a plant in the UI for this photo, then call 2
    (trend-refine) if matched. Returns (updated_item, usage_limit_hit).
    On a usage-limit hit, the item is returned unchanged so the caller
    retries the same item after the pause/ping."""
    temp_path = item["temp_path"]
    parsed = None

    if item.get("matched_plant"):
        # Pre-assigned by the user at upload time — skip the identify Opus
        # call entirely, straight to the (single) trend-assessment call.
        plant = store.get_plant(item["matched_plant"])
        if not plant:
            item["error"] = f"Assigned plant '{item['matched_plant']}' not found."
            item["matched_plant"] = None
            item["status"] = "unmatched"
            return item, False
        item["matched_plant"] = plant.name
    else:
        parsed, usage_limit_hit = identify_and_assess(
            temp_path, IDENTIFY_AND_ASSESS_SYSTEM, _identification_user_text(plants)
        )

        if usage_limit_hit:
            return item, True

        if not parsed or not parsed.get("matched_plant"):
            item["status"] = "unmatched"
            item["result"] = parsed
            return item, False

        matched_name = parsed["matched_plant"]
        plant = store.get_plant(matched_name)
        if not plant:
            item["status"] = "unmatched"
            item["result"] = parsed
            item["error"] = f"Identified as '{matched_name}' but no matching plant record found."
            return item, False

        item["matched_plant"] = plant.name
        item["confidence"] = parsed.get("confidence")

    trend_paths = get_trend_photo_paths(db, plant.name)
    raw_response = assess_image(
        temp_path, _assessment_system_prompt(plant.name), _plant_user_text(plant),
        plant_name=plant.name, extra_image_paths=trend_paths,
    )
    if not raw_response:
        item["status"] = "failed"
        item["error"] = "Trend assessment call failed."
        return item, False

    _display, trend_parsed = parse_assessment_response(raw_response, plant.name)
    final_parsed = trend_parsed or parsed
    persist_assessment(store, db, plant, final_parsed, temp_path)

    item["status"] = "done"
    item["result"] = final_parsed
    return item, False


# --- Job engine ---


async def run_batch_job(job_id: int, db: AgentDB = None) -> None:
    """Sequentially process a batch job's items. Pauses (persisting state)
    on a detected Claude usage-limit hit and resumes on a fixed-interval
    ping, up to a capped total pause time — then gives up (status=failed).

    Runs as a fire-and-forget asyncio task (nobody awaits its result), so any
    unhandled exception must be caught here and turned into a terminal
    status=failed row — otherwise the job would sit at status="running"
    forever with no way for the polling UI (or resume_active_jobs on the
    next restart) to know it died."""
    owns_db = db is None
    db = db or AgentDB()
    store = PlantStore(db_path=db.db_path)
    try:
        job = db.get_batch_job(job_id)
        if job is None:
            return
        items = job["items"]
        index = job["current_index"]
        # Persisted so a repeatedly-pausing job can't reset its pause budget
        # by surviving a service restart (resume_active_jobs would otherwise
        # start cumulative_pause_seconds back at 0 every time).
        cumulative_pause_seconds = job.get("paused_seconds", 0)

        while index < len(items):
            plants = store.get_plants_raw()
            # _process_item runs blocking `claude` CLI subprocess calls — keep
            # it off the event loop so a slow/paused batch doesn't freeze the
            # rest of the FastAPI app.
            updated_item, usage_limit_hit = await asyncio.to_thread(
                _process_item, items[index], plants, store, db
            )
            items[index] = updated_item

            if usage_limit_hit:
                if cumulative_pause_seconds + PING_INTERVAL_SECONDS > MAX_PAUSE_SECONDS:
                    db.update_batch_job(
                        job_id, status="failed", items=items, current_index=index,
                        pause_reason="Usage limit persisted past max pause window",
                        paused_seconds=cumulative_pause_seconds,
                    )
                    return
                next_ping = (datetime.utcnow() + timedelta(seconds=PING_INTERVAL_SECONDS)).isoformat()
                db.update_batch_job(
                    job_id, status="paused", items=items, current_index=index,
                    pause_reason="Claude usage limit detected", next_ping_at=next_ping,
                    paused_seconds=cumulative_pause_seconds,
                )
                await asyncio.sleep(PING_INTERVAL_SECONDS)
                cumulative_pause_seconds += PING_INTERVAL_SECONDS
                db.update_batch_job(job_id, status="running", paused_seconds=cumulative_pause_seconds)
                continue  # retry the same item

            index += 1
            db.update_batch_job(job_id, items=items, current_index=index)

        _cleanup_job_dir(job_id)
        db.update_batch_job(job_id, status="done", items=items, current_index=index)
    except Exception as e:
        logger.error("Batch job %s failed with an unhandled error: %s", job_id, e, exc_info=True)
        try:
            db.update_batch_job(job_id, status="failed", pause_reason=f"Unexpected error: {e}")
        except Exception:
            logger.error("Batch job %s: also failed to persist the failure status", job_id, exc_info=True)
    finally:
        store.close()
        if owns_db:
            db.close()


async def assign_item(job_id: int, index: int, plant_name: str, db: AgentDB = None) -> dict:
    """Manually assign an unmatched batch item to a plant and re-run its
    assessment (skipping call-1 identification since the plant is known).
    Runs the blocking vision call off the event loop via asyncio.to_thread."""
    return await asyncio.to_thread(_assign_item_sync, job_id, index, plant_name, db)


def _assign_item_sync(job_id: int, index: int, plant_name: str, db: AgentDB = None) -> dict:
    owns_db = db is None
    db = db or AgentDB()
    store = PlantStore(db_path=db.db_path)
    try:
        job = db.get_batch_job(job_id)
        if job is None:
            raise ValueError(f"Batch job {job_id} not found")
        items = job["items"]
        if not (0 <= index < len(items)):
            raise ValueError(f"Item index {index} out of range")
        # The background loop and this endpoint both read-modify-write the
        # whole items array, so assigning an item that's still "pending"
        # (the loop hasn't reached/finished it yet) would race with the
        # loop's own write and one of the two updates would silently clobber
        # the other. Items in a terminal per-item state (unmatched/failed/
        # done) are safe to touch regardless of the job's overall status.
        if items[index]["status"] == "pending":
            raise ValueError(
                "This photo is still being processed by the batch job — try again shortly."
            )
        plant = store.get_plant(plant_name)
        if not plant:
            raise ValueError(f"No plant named '{plant_name}' found")

        item = items[index]
        trend_paths = get_trend_photo_paths(db, plant.name)
        raw_response = assess_image(
            item["temp_path"], _assessment_system_prompt(plant.name), _plant_user_text(plant),
            plant_name=plant.name, extra_image_paths=trend_paths,
        )
        if not raw_response:
            item["status"] = "failed"
            item["error"] = "Assessment call failed."
        else:
            _display, parsed = parse_assessment_response(raw_response, plant.name)
            if parsed:
                persist_assessment(store, db, plant, parsed, item["temp_path"])
                item["status"] = "done"
                item["matched_plant"] = plant.name
                item["result"] = parsed
            else:
                item["status"] = "failed"
                item["error"] = "Could not parse assessment response."

        items[index] = item
        db.update_batch_job(job_id, items=items)
        return item
    finally:
        store.close()
        if owns_db:
            db.close()


def resume_active_jobs(db: AgentDB = None) -> list[int]:
    """Re-launch processing for any job left running/paused across a
    plant_ui service restart. Called from the FastAPI startup hook.

    The AgentDB connection created here (when the caller doesn't supply one)
    is intentionally never closed — it's handed to every spawned run_batch_job
    task and must outlive this function's return, for the life of the process."""
    db = db or AgentDB()
    job_ids = []
    for job in db.list_active_batch_jobs():
        asyncio.create_task(run_batch_job(job["id"], db=db))
        job_ids.append(job["id"])
    return job_ids
