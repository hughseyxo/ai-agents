import sys
import os
import logging
import tempfile
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field

# Ensure project directories are in path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

from agents.plant_model import PlantStore, Plant, AssessmentRecord
from agents.db import AgentDB
from agents import garden_notes
from agents.plant_profiles import (
    write_health_assessment,
    append_frequency_history,
    append_intelligence_note,
    profile_path,
    safe_profile_path,
    write_profile_atomic
)
from agents.plant_assessment import (
    load_species_context as _load_species_context,
    format_care_actions_for_profile as _format_care_actions_for_profile,
    parse_assessment_response,
)
from claude_backend import assess_image
from plant_ui import chat_backend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plant_ui")

app = FastAPI(title="Plant AI PWA")

# Constants
PLANT_ASSESSMENT_SYSTEM_PATH = REPO_ROOT / "agents" / "prompts" / "plant_photo_assessment.md"
PLANT_HEALTH_SYSTEM = (
    "You are a plant health expert. Assess the plant in the photo. "
    "Give specific observations about its health and concise actionable advice. "
    "Keep your response to 3-5 sentences."
)

# Upload limits / prompt bounds
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
# Magic-byte signatures for the allowed image types (defence beyond content-type).
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")
MAX_PROFILE_CHARS = 4000  # cap profile markdown folded into the vision prompt


def _safe_profile_path_or_400(name: str) -> Path:
    """Resolve a plant profile path, raising HTTP 400 on a traversal attempt."""
    try:
        return safe_profile_path(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plant name")


# Pydantic models for API
class PlantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    frequency_days: int = Field(ge=1, le=30)
    location: Literal["indoor", "outdoor"] = "indoor"
    sunlight: str = Field(default="", max_length=50)
    water_sensitivity: Literal["high", "medium", "low"] = "medium"

class PlantUpdate(BaseModel):
    name: Optional[str] = None
    frequency_days: Optional[int] = None
    baseline_frequency_days: Optional[int] = None
    last_watered: Optional[date] = None
    location: Optional[Literal["indoor", "outdoor"]] = None
    sunlight: Optional[str] = None
    water_sensitivity: Optional[Literal["high", "medium", "low"]] = None
    needs_photo: Optional[bool] = None

class WaterAllRequest(BaseModel):
    location: Literal["indoor", "outdoor"]

class CompleteTaskRequest(BaseModel):
    plant: str
    action: str

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    scope: Literal["garden", "plant"] = "garden"
    plant_name: Optional[str] = None
    session_id: Optional[str] = None

# Helper functions
def get_store() -> PlantStore:
    store = PlantStore()
    try:
        yield store
    finally:
        store.close()

def get_db() -> AgentDB:
    db = AgentDB()
    try:
        yield db
    finally:
        db.close()

def create_profile_doc(name: str, location: str, sunlight: str, sensitivity: str, frequency: int):
    path = _safe_profile_path_or_400(name)
    if path.exists():
        return
    content = f"""# {name}

## Plant Info
- Location: {location.capitalize()} | Sunlight: {sunlight}
- Water sensitivity: {sensitivity} | Base frequency: {frequency} days

## Observed Behaviour
<!-- Free text updated by intelligence run — notable patterns, visual cues -->

## Health Assessments
<!-- Photo assessments appended here -->

## Frequency History
| Date | Change | Reason |
|---|---|---|

## Intelligence Notes
<!-- Appended by each intelligence run -->
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_profile_atomic(path, content)

# API Endpoints
@app.get("/api/plants")
def get_plants(store: PlantStore = Depends(get_store)):
    plants = store.get_plants()
    today = date.today()
    result = []
    for p in plants:
        next_due = p.last_watered + timedelta(days=p.frequency_days)
        overdue = max(0, (today - next_due).days)
        status_label = p.last_assessment.status if p.last_assessment else "Unknown"
        
        p_dict = p.model_dump()
        p_dict.update({
            "next_due_date": next_due.isoformat(),
            "overdue_days": overdue,
            "status_label": status_label
        })
        result.append(p_dict)
    
    # Sort: overdue -> due today -> upcoming
    result.sort(key=lambda x: (x["overdue_days"] == 0, x["next_due_date"], x["name"]))
    return result

@app.get("/api/plants/attention")
def get_attention(store: PlantStore = Depends(get_store), db: AgentDB = Depends(get_db)):
    plants = store.get_plants()
    today = date.today()

    watering_needed = []
    photos_needed = []
    for p in plants:
        overdue = (today - p.last_watered).days - p.frequency_days
        if overdue > 0:
            watering_needed.append({"name": p.name, "overdue_days": overdue})
        if p.needs_photo:
            reason = "flagged by intelligence"
            if p.last_assessment:
                days_since = (today - p.last_assessment.date).days
                reason = f"last assessed {days_since}d ago"
            photos_needed.append({"name": p.name, "reason": reason})

    care_tasks = db.get_state("plant-agent", "pending_plant_actions") or []
    return {
        "watering_needed": watering_needed,
        "photos_needed": photos_needed,
        "care_tasks": care_tasks,
    }


@app.get("/api/plants/{name}")
def get_plant_detail(name: str, store: PlantStore = Depends(get_store)):
    plant = store.get_plant(name)
    if not plant:
        raise HTTPException(status_code=404, detail=f"Plant '{name}' not found")
    
    path = _safe_profile_path_or_400(plant.name)
    markdown_content = ""
    if path.exists():
        markdown_content = path.read_text()
        
    return {
        "plant": plant,
        "markdown": markdown_content
    }

@app.post("/api/plants")
def add_plant(data: PlantCreate, store: PlantStore = Depends(get_store)):
    plants = store.get_plants()
    if any(p.name.lower() == data.name.lower().strip() for p in plants):
        raise HTTPException(status_code=400, detail=f"Plant '{data.name}' already exists")
    
    new_plant = Plant(
        name=data.name.strip(),
        frequency_days=data.frequency_days,
        baseline_frequency_days=data.frequency_days,
        last_watered=date.today(),
        location=data.location,
        sunlight=data.sunlight,
        water_sensitivity=data.water_sensitivity,
        needs_photo=False
    )
    
    plants.append(new_plant)
    store.save_plants(plants)
    
    # Create profile markdown
    create_profile_doc(
        name=new_plant.name,
        location=new_plant.location,
        sunlight=new_plant.sunlight,
        sensitivity=new_plant.water_sensitivity,
        frequency=new_plant.frequency_days
    )
    
    return {"status": "success", "plant": new_plant}

@app.patch("/api/plants/{name}")
def update_plant_fields(name: str, data: PlantUpdate, store: PlantStore = Depends(get_store)):
    plants = store.get_plants()
    match_idx = -1
    for i, p in enumerate(plants):
        if p.name.lower() == name.lower().strip():
            match_idx = i
            break
            
    if match_idx == -1:
        raise HTTPException(status_code=404, detail=f"Plant '{name}' not found")
    
    old_plant = plants[match_idx]
    old_name = old_plant.name
    
    # Check if name is being changed and is already taken
    if data.name and data.name.strip().lower() != old_name.lower():
        new_name_clean = data.name.strip()
        if any(p.name.lower() == new_name_clean.lower() for p in plants if p.name.lower() != old_name.lower()):
            raise HTTPException(status_code=400, detail=f"Plant '{new_name_clean}' already exists")
            
        # Rename markdown profile (both paths bounded inside the plants dir)
        old_profile = _safe_profile_path_or_400(old_name)
        new_profile = _safe_profile_path_or_400(new_name_clean)
        if old_profile.exists():
            old_profile.rename(new_profile)
    
    # Log frequency changes in history
    freq_changed = False
    old_freq = old_plant.baseline_frequency_days
    new_freq = data.baseline_frequency_days if data.baseline_frequency_days is not None else (data.frequency_days if data.frequency_days is not None else None)
    
    # Update plant fields
    updated_dict = old_plant.model_dump()
    for field, val in data.model_dump(exclude_unset=True).items():
        if field == "name":
            updated_dict[field] = val.strip()
        else:
            updated_dict[field] = val

    # If baseline frequency is updated, clamp and sync with effective frequency if needed
    if data.baseline_frequency_days is not None:
        updated_dict["baseline_frequency_days"] = max(1, min(30, data.baseline_frequency_days))
        # Keep frequency_days in sync if no active weather adjustment
        if data.frequency_days is None:
            updated_dict["frequency_days"] = updated_dict["baseline_frequency_days"]
            
    updated_plant = Plant(**updated_dict)
    plants[match_idx] = updated_plant
    store.save_plants(plants)
    
    # Record frequency change in history if baseline changed
    if new_freq is not None and new_freq != old_freq:
        append_frequency_history(updated_plant.name, old_freq, new_freq, "Updated in UI")
        
    return {"status": "success", "plant": updated_plant}

@app.delete("/api/plants/{name}")
def delete_plant(name: str, store: PlantStore = Depends(get_store)):
    plants = store.get_plants()
    match_idx = -1
    for i, p in enumerate(plants):
        if p.name.lower() == name.lower().strip():
            match_idx = i
            break

    if match_idx == -1:
        raise HTTPException(status_code=404, detail=f"Plant '{name}' not found")

    plant = plants.pop(match_idx)
    store.save_plants(plants)

    # Delete profile document if exists
    p_path = _safe_profile_path_or_400(plant.name)
    if p_path.exists():
        try:
            p_path.unlink()
        except Exception as e:
            logger.error(f"Failed to delete profile doc for {plant.name}: {e}")

    return {"status": "success", "message": f"Plant '{plant.name}' removed"}

@app.post("/api/plants/{name}/water")
def water_plant(name: str, store: PlantStore = Depends(get_store)):
    plant = store.get_plant(name)
    if not plant:
        raise HTTPException(status_code=404, detail=f"Plant '{name}' not found")

    plant.last_watered = date.today()
    store.update_plant(plant)
    return {"status": "success", "plant": plant}

@app.post("/api/plants/water-all")
def water_all_plants(data: WaterAllRequest, store: PlantStore = Depends(get_store)):
    plants = store.get_plants()
    updated_count = 0
    today = date.today()

    for p in plants:
        if p.location == data.location:
            p.last_watered = today
            updated_count += 1

    if updated_count > 0:
        store.save_plants(plants)

    return {"status": "success", "waterED_count": updated_count}

@app.get("/api/weather")
def get_weather(db: AgentDB = Depends(get_db)):
    return db.get_plant_weather_cache()

@app.get("/api/status")
def get_agent_status(db: AgentDB = Depends(get_db)):
    return {
        "send_status_email": db.get_state("plant-agent", "last_send_status_email"),
        "intelligence_run": db.get_state("plant-agent", "last_intelligence_run"),
    }

@app.post("/api/care-tasks/complete")
def complete_care_task(data: CompleteTaskRequest, db: AgentDB = Depends(get_db)):
    tasks = db.get_state("plant-agent", "pending_plant_actions") or []
    updated = [t for t in tasks if not (t.get("plant") == data.plant and t.get("action") == data.action)]
    db.set_state("plant-agent", "pending_plant_actions", updated)
    db.mark_seen("plant-agent", "completed_action", f"{data.plant}:{data.action}")
    today = date.today().isoformat()
    append_intelligence_note(data.plant, f"### {today} (completed)\n- {data.action} (marked done via PWA)")
    return {"status": "success", "remaining": len(updated)}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    reply, session_id = await asyncio.to_thread(
        chat_backend.chat,
        message=req.message, scope=req.scope,
        plant_name=req.plant_name, session_id=req.session_id,
    )
    if reply is None:
        reply = "Sorry — the gardening assistant is unavailable right now. Try again shortly."
    return {"reply": reply, "session_id": session_id}


@app.post("/api/plants/{name}/photo")
async def upload_photo(name: str, file: UploadFile = File(...), store: PlantStore = Depends(get_store)):
    plant = store.get_plant(name)
    if not plant:
        raise HTTPException(status_code=404, detail=f"Plant '{name}' not found")
        
    # Validate the upload: declared content-type, size cap, and magic bytes.
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG or WebP images are accepted.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 8 MB).")
    if not contents.startswith(_IMAGE_MAGIC):
        raise HTTPException(status_code=400, detail="Upload is not a valid image file.")

    # Replicate _analyze_plant_image logic
    last_watered = plant.last_watered.isoformat()
    try:
        days_since = (date.today() - plant.last_watered).days
        days_str = f"{days_since} days ago"
    except Exception:
        days_str = "unknown"

    p_path = _safe_profile_path_or_400(plant.name)
    profile_context = ""
    if p_path.exists():
        # Read off the event loop; truncate so a huge profile can't blow up the prompt.
        profile_text = await asyncio.to_thread(p_path.read_text)
        if len(profile_text) > MAX_PROFILE_CHARS:
            profile_text = profile_text[:MAX_PROFILE_CHARS] + "\n[profile truncated]"
        profile_context = f"\n\nPlant profile history:\n{profile_text}"
        
    species_context = await asyncio.to_thread(_load_species_context, plant.name)

    # Read/fallback system prompt (off the event loop)
    system_prompt = PLANT_HEALTH_SYSTEM
    if PLANT_ASSESSMENT_SYSTEM_PATH.exists():
        system_prompt = await asyncio.to_thread(PLANT_ASSESSMENT_SYSTEM_PATH.read_text)
        
    if species_context:
        system_prompt += f"\n\n## Species Reference\n{species_context}"
        
    user_text = (
        f"This is a {plant.name} ({plant.location}). "
        f"Last watered {last_watered} ({days_str})."
        f"Base watering frequency: every {plant.frequency_days} days."
        f"{profile_context}"
    )
    
    # Save upload to temp file and call assess_image
    with tempfile.TemporaryDirectory(prefix="plant_ui-") as tmpdir:
        tmp_img = Path(tmpdir) / "image.jpg"
        tmp_img.write_bytes(contents)
        
        # Run assess_image from claude_backend in executor since it's blocking
        loop = asyncio.get_running_loop()
        try:
            raw_response = await loop.run_in_executor(
                None, assess_image, str(tmp_img), system_prompt, user_text
            )
        except Exception as e:
            logger.error(f"Image assessment failed: {e}")
            raw_response = None
            
    if not raw_response:
        raise HTTPException(status_code=500, detail="Plant assessment is unavailable right now. Try again later.")

    display_text, parsed = parse_assessment_response(raw_response, plant.name)

    if parsed:
        # Save structured notes to plant profile doc (## Health Assessments section),
        # folding in the prioritised care actions so next steps persist over time.
        profile_notes = parsed.get("profile_notes", "")
        care_block = _format_care_actions_for_profile(parsed.get("care_actions") or [])
        if profile_notes or care_block:
            write_health_assessment(plant.name, (profile_notes + care_block).strip())

        # Auto-create a standalone observation note when the assessment is noteworthy.
        try:
            garden_notes.maybe_create_observation_note(plant.name, parsed)
        except Exception as e:
            logger.warning("observation note creation failed: %s", e)

        # Save assessment summary to DB
        summary_to_save = parsed.get("summary", display_text)
        status_to_save = parsed.get("status", "Assessment")
        plant.last_assessment = AssessmentRecord(
            date=date.today(),
            summary=summary_to_save,
            status=status_to_save
        )
        # Clear needs_photo flag if assessed
        plant.needs_photo = False
        store.update_plant(plant)
        
        # Suggest frequency change if any
        freq_suggestion = parsed.get("frequency_suggestion")
        return {
            "status": "success",
            "display_text": display_text,
            "parsed": parsed,
            "frequency_suggestion": freq_suggestion
        }
    else:
        # Text only fallback
        if not display_text.startswith("Plant assessment unavailable"):
            plant.last_assessment = AssessmentRecord(
                date=date.today(),
                summary=display_text,
                status="Assessment"
            )
            plant.needs_photo = False
            store.update_plant(plant)
            
        return {
            "status": "success",
            "display_text": display_text,
            "parsed": None,
            "frequency_suggestion": None
        }

# Serve Frontend SPA
@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = REPO_ROOT / "plant_ui" / "templates" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html template not found")
    return index_path.read_text()

@app.get("/sw.js", response_class=FileResponse)
def serve_sw():
    sw_path = REPO_ROOT / "plant_ui" / "static" / "sw.js"
    if not sw_path.exists():
        raise HTTPException(status_code=404, detail="Service worker not found")
    return FileResponse(sw_path, media_type="application/javascript")

# Mount static folder
app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "plant_ui" / "static")), name="static")
