import os
import sys
import json
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "plant_ui"))

from plant_ui.server import app, get_store, get_db
from agents.plant_model import PlantStore, Plant, AssessmentRecord
from agents.db import AgentDB
from agents import plant_profiles as pp
from agents import photo_batch

@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_agents.db"

@pytest.fixture
def mock_store_db(temp_db_path, monkeypatch, tmp_path):
    # Override profiles directory in plant_profiles helper
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path / "plants")
    # Isolate photo storage — every photo upload path (single or batch) goes
    # through photo_batch.save_plant_photo/create_batch_job, so without this
    # every photo-upload test would write real files into the repo's own
    # data/plant-photos/ and data/plant-photo-batches/ directories.
    monkeypatch.setattr(photo_batch, "PHOTO_STORE_DIR", tmp_path / "plant-photos")
    monkeypatch.setattr(photo_batch, "BATCH_TEMP_DIR", tmp_path / "plant-photo-batches")

    # Create stores with the test db path
    store = PlantStore(temp_db_path)
    db = AgentDB(temp_db_path)

    # Apply dependency overrides for FastAPI app
    app.dependency_overrides[get_store] = lambda: PlantStore(temp_db_path)
    app.dependency_overrides[get_db] = lambda: AgentDB(temp_db_path)

    yield store, db, tmp_path / "plants"

    # Clean overrides
    app.dependency_overrides.clear()
    store.close()
    db.close()

@pytest.fixture
def client(mock_store_db):
    return TestClient(app)

def test_get_plants_empty(client):
    response = client.get("/api/plants")
    assert response.status_code == 200
    assert response.json() == []

def test_add_plant(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    payload = {
        "name": "Ferny",
        "frequency_days": 5,
        "location": "indoor",
        "sunlight": "partial shade",
        "water_sensitivity": "medium"
    }
    
    response = client.post("/api/plants", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    assert res_data["plant"]["name"] == "Ferny"
    assert res_data["plant"]["frequency_days"] == 5
    
    # Check that profile doc was created
    prof_path = plants_dir / "ferny.md"
    assert prof_path.exists()
    assert "Base frequency: 5 days" in prof_path.read_text()

def test_get_plants_with_items(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    # Add a mock plant
    plant_data = Plant(
        name="Spider Plant",
        frequency_days=7,
        baseline_frequency_days=7,
        last_watered=date.today() - timedelta(days=9),  # 2 days overdue
        location="indoor",
        sunlight="partial shade",
        water_sensitivity="medium"
    )
    store.save_plants([plant_data])
    
    response = client.get("/api/plants")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    plant_res = data[0]
    assert plant_res["name"] == "Spider Plant"
    assert plant_res["overdue_days"] == 2
    assert plant_res["next_due_date"] == (date.today() - timedelta(days=2)).isoformat()
    assert plant_res["status_label"] == "Unknown"

def test_get_plant_detail(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plant_data = Plant(
        name="Pothos",
        frequency_days=10,
        baseline_frequency_days=10,
        last_watered=date.today(),
        location="indoor",
        sunlight="shade",
        water_sensitivity="medium"
    )
    store.save_plants([plant_data])
    
    # Create profile markdown
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof_path = plants_dir / "pothos.md"
    prof_path.write_text("# Pothos Profile\nSome notes here.")
    
    response = client.get("/api/plants/Pothos")
    assert response.status_code == 200
    data = response.json()
    assert data["plant"]["name"] == "Pothos"
    assert data["markdown"] == "# Pothos Profile\nSome notes here."

def test_water_plant(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plant_data = Plant(
        name="Jade",
        frequency_days=14,
        baseline_frequency_days=14,
        last_watered=date.today() - timedelta(days=20),
        location="indoor"
    )
    store.save_plants([plant_data])
    
    response = client.post("/api/plants/Jade/water")
    assert response.status_code == 200
    assert response.json()["plant"]["last_watered"] == date.today().isoformat()

    # Verify plant state in DB
    updated_plant = store.get_plant("Jade")
    assert updated_plant.last_watered == date.today()

def test_water_all_plants(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plants = [
        Plant(name="Mint", frequency_days=3, baseline_frequency_days=3, last_watered=date.today() - timedelta(days=5), location="outdoor"),
        Plant(name="Rosemary", frequency_days=7, baseline_frequency_days=7, last_watered=date.today() - timedelta(days=10), location="outdoor"),
        Plant(name="Ficus", frequency_days=8, baseline_frequency_days=8, last_watered=date.today() - timedelta(days=10), location="indoor")
    ]
    store.save_plants(plants)
    
    response = client.post("/api/plants/water-all", json={"location": "outdoor"})
    assert response.status_code == 200
    assert response.json()["watered_count"] == 2

    # Verify Mint and Rosemary last_watered is today
    db_plants = store.get_plants()
    mint = next(p for p in db_plants if p.name == "Mint")
    rosemary = next(p for p in db_plants if p.name == "Rosemary")
    ficus = next(p for p in db_plants if p.name == "Ficus")

    assert mint.last_watered == date.today()
    assert rosemary.last_watered == date.today()
    assert ficus.last_watered != date.today()  # Indoor plant not watered

def test_update_plant(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plant_data = Plant(
        name="Lavender",
        frequency_days=7,
        baseline_frequency_days=7,
        last_watered=date.today(),
        location="outdoor"
    )
    store.save_plants([plant_data])
    
    # Create profile doc
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof_path = plants_dir / "lavender.md"
    prof_path.write_text("# Lavender\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n")
    
    # Update baseline frequency
    payload = {
        "baseline_frequency_days": 10,
        "location": "indoor"
    }
    response = client.patch("/api/plants/Lavender", json=payload)
    assert response.status_code == 200
    assert response.json()["plant"]["baseline_frequency_days"] == 10
    assert response.json()["plant"]["location"] == "indoor"
    
    # Check frequency history was updated in markdown
    assert "7→10 days" in prof_path.read_text()

def test_delete_plant(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plant_data = Plant(
        name="Cactus",
        frequency_days=30,
        baseline_frequency_days=30,
        last_watered=date.today(),
        location="indoor"
    )
    store.save_plants([plant_data])
    
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof_path = plants_dir / "cactus.md"
    prof_path.write_text("# Cactus Profile")
    
    response = client.delete("/api/plants/Cactus")
    assert response.status_code == 200
    assert len(store.get_plants()) == 0
    assert not prof_path.exists()

def test_complete_care_task_removes_task(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    db.set_state("plant-agent", "pending_plant_actions", [
        {"plant": "Lavender", "action": "deadhead", "reason": "spent blooms", "date": "2026-06-17"},
        {"plant": "Monstera", "action": "repot", "reason": "root bound", "date": "2026-06-17"},
    ])
    response = client.post("/api/care-tasks/complete", json={"plant": "Lavender", "action": "deadhead"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["remaining"] == 1
    remaining = db.get_state("plant-agent", "pending_plant_actions")
    assert len(remaining) == 1
    assert remaining[0]["plant"] == "Monstera"


def test_complete_care_task_writes_profile_note(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof = plants_dir / "lavender.md"
    prof.write_text(
        "# Lavender\n\n## Intelligence Notes\n<!-- Appended by each intelligence run -->\n"
    )
    db.set_state("plant-agent", "pending_plant_actions", [
        {"plant": "Lavender", "action": "deadhead", "reason": "spent blooms", "date": "2026-06-17"},
    ])
    client.post("/api/care-tasks/complete", json={"plant": "Lavender", "action": "deadhead"})
    txt = prof.read_text()
    assert "(completed)" in txt
    assert "deadhead" in txt


def test_complete_care_task_idempotent(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    db.set_state("plant-agent", "pending_plant_actions", [])
    response = client.post("/api/care-tasks/complete", json={"plant": "Ghost", "action": "prune"})
    assert response.status_code == 200
    assert response.json()["remaining"] == 0


def test_complete_care_task_marks_seen(client, mock_store_db):
    """Task 11: completing a care task must dedup-mark it so the next intelligence
    run can't resurrect it into pending_plant_actions."""
    store, db, plants_dir = mock_store_db
    db.set_state("plant-agent", "pending_plant_actions", [
        {"plant": "Aloe", "action": "prune", "reason": "", "date": "2026-06-17"},
    ])
    response = client.post("/api/care-tasks/complete", json={"plant": "Aloe", "action": "prune"})
    assert response.status_code == 200
    assert db.check_dedup("plant-agent", "completed_action", "Aloe:prune")


def test_chat_endpoint_happy(client, monkeypatch):
    import plant_ui.server as srv
    monkeypatch.setattr(srv.chat_backend, "chat", lambda **kw: ("Water less in winter.", "sess-9"))
    r = client.post("/api/chat", json={"message": "How often?", "scope": "garden"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Water less in winter." and body["session_id"] == "sess-9"


def test_chat_endpoint_backend_failure(client, monkeypatch):
    import plant_ui.server as srv
    monkeypatch.setattr(srv.chat_backend, "chat", lambda **kw: (None, None))
    r = client.post("/api/chat", json={"message": "hi", "scope": "garden"})
    assert r.status_code == 200
    assert "unavailable" in r.json()["reply"].lower()


_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def test_photo_noteworthy_creates_observation(client, mock_store_db, monkeypatch):
    import plant_ui.server as srv
    store, db, plants_dir = mock_store_db
    plant_data = Plant(
        name="Aloe",
        frequency_days=20,
        baseline_frequency_days=20,
        last_watered=date.today() - timedelta(days=15),
        location="indoor",
    )
    store.save_plants([plant_data])
    plants_dir.mkdir(parents=True, exist_ok=True)
    (plants_dir / "aloe.md").write_text("# Aloe\n## Health Assessments\n")

    captured = {}
    monkeypatch.setattr(
        srv.photo_batch.garden_notes,
        "maybe_create_observation_note",
        lambda slug, parsed: captured.setdefault("called", (slug, parsed)) or "docs/x.md",
    )
    monkeypatch.setattr(
        srv,
        "assess_image",
        lambda *a, **k: json.dumps({
            "status": "Concern",
            "summary": "aphids spotted",
            "profile_notes": "### 2026-06-23 — Concern\n- aphids",
            "noteworthy": True,
            "note_title": "Aphids",
            "note_body": "colony found",
        }),
    )

    from io import BytesIO
    r = client.post(
        "/api/plants/Aloe/photo",
        files={"file": ("p.jpg", BytesIO(_JPEG_BYTES), "image/jpeg")},
    )
    assert r.status_code == 200
    assert captured["called"][0] == "Aloe"


def test_photo_assessment(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    
    plant_data = Plant(
        name="Aloe Vera",
        frequency_days=20,
        baseline_frequency_days=20,
        last_watered=date.today() - timedelta(days=15),
        location="indoor",
        needs_photo=True
    )
    store.save_plants([plant_data])
    
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof_path = plants_dir / "aloe-vera.md"
    prof_path.write_text("# Aloe Vera\n## Health Assessments\n")
    
    # Mock LLM response from assess_image
    mock_llm_response = """
    {
        "status": "Healthy",
        "summary": "The leaves look plump and green with no visual signs of stress or pests.",
        "observations": ["Plump green leaves", "Dry soil"],
        "watering_recommendation": "on_schedule",
        "profile_notes": "### 2026-06-04 — Healthy\\n- Looking very strong."
    }
    """
    
    with patch("plant_ui.server.assess_image", return_value=mock_llm_response) as mock_assess:
        # Create a dummy image upload
        from io import BytesIO
        # Must start with the JPEG magic bytes — the endpoint validates uploads.
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"dummy image bytes"
        file_data = {"file": ("aloe.jpg", BytesIO(jpeg_bytes), "image/jpeg")}
        
        response = client.post("/api/plants/Aloe Vera/photo", files=file_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["parsed"]["status"] == "Healthy"
        
        # Verify plant DB record was updated
        updated_plant = store.get_plant("Aloe Vera")
        assert updated_plant.last_assessment is not None
        assert updated_plant.last_assessment.status == "Healthy"
        assert updated_plant.needs_photo is False  # Flag cleared
        
        # Verify markdown profile was updated
        assert "Looking very strong" in prof_path.read_text()


def test_build_assessment_display_includes_care_actions():
    from agents.plant_assessment import build_assessment_display as _build_assessment_display
    parsed = {
        "status": "Stressed",
        "summary": "Lower leaves yellowing.",
        "observations": ["Yellow lower leaves"],
        "watering_recommendation": "delay",
        "care_actions": [
            {"action": "Remove the 3 yellow lower leaves", "priority": "high", "reason": "they won't recover and divert energy"},
            {"action": "Move 1m closer to the window", "priority": "medium", "reason": "more light"},
        ],
    }
    out = _build_assessment_display(parsed, "Aloe")
    assert "Next steps" in out
    assert "Remove the 3 yellow lower leaves" in out
    assert "Move 1m closer to the window" in out
    assert "more light" in out


def test_photo_care_actions_persisted_to_profile(client, mock_store_db):
    store, db, plants_dir = mock_store_db
    plant_data = Plant(
        name="Aloe",
        frequency_days=20,
        baseline_frequency_days=20,
        last_watered=date.today() - timedelta(days=15),
        location="indoor",
    )
    store.save_plants([plant_data])
    plants_dir.mkdir(parents=True, exist_ok=True)
    prof_path = plants_dir / "aloe.md"
    prof_path.write_text("# Aloe\n## Health Assessments\n")

    mock_response = json.dumps({
        "status": "Stressed",
        "summary": "Lower leaves yellowing.",
        "observations": ["Yellow lower leaves"],
        "watering_recommendation": "delay",
        "care_actions": [
            {"action": "Remove the 3 yellow lower leaves", "priority": "high", "reason": "they won't recover"},
        ],
        "profile_notes": "### 2026-06-29 — Stressed\n- Yellowing observed.",
    })

    with patch("plant_ui.server.assess_image", return_value=mock_response):
        from io import BytesIO
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"dummy image bytes"
        r = client.post(
            "/api/plants/Aloe/photo",
            files={"file": ("p.jpg", BytesIO(jpeg_bytes), "image/jpeg")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "Remove the 3 yellow lower leaves" in data["display_text"]
        assert data["parsed"]["care_actions"][0]["priority"] == "high"

    profile = prof_path.read_text()
    assert "Remove the 3 yellow lower leaves" in profile
    assert "Yellowing observed" in profile


# --- Batch photo upload ---


@pytest.fixture(autouse=True)
def _no_real_batch_jobs(monkeypatch):
    # Prevent any test from accidentally kicking off a real background job
    # (which would try to run the actual `claude` CLI as a subprocess).
    async def _noop(*args, **kwargs):
        return None
    monkeypatch.setattr("plant_ui.server.photo_batch.run_batch_job", _noop)


def test_upload_batch_photos_creates_job(client, mock_store_db):
    from io import BytesIO
    r = client.post(
        "/api/plants/batch-photos",
        files=[
            ("files", ("a.jpg", BytesIO(_JPEG_BYTES), "image/jpeg")),
            ("files", ("b.jpg", BytesIO(_JPEG_BYTES), "image/jpeg")),
        ],
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    status = client.get(f"/api/plants/batch-photos/{job_id}")
    assert status.status_code == 200
    job = status.json()
    assert job["status"] == "running"
    assert len(job["items"]) == 2


def test_upload_batch_photos_with_plant_names_preassigns_items(client, mock_store_db):
    from io import BytesIO
    r = client.post(
        "/api/plants/batch-photos",
        files=[
            ("files", ("a.jpg", BytesIO(_JPEG_BYTES), "image/jpeg")),
            ("files", ("b.jpg", BytesIO(_JPEG_BYTES), "image/jpeg")),
        ],
        data={"plant_names": ["Aloe", ""]},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    status = client.get(f"/api/plants/batch-photos/{job_id}")
    job = status.json()
    assert job["items"][0]["matched_plant"] == "Aloe"
    assert job["items"][0]["confidence"] == "user-assigned"
    assert job["items"][1]["matched_plant"] is None


def test_upload_batch_photos_plant_names_length_mismatch_returns_400(client, mock_store_db):
    from io import BytesIO
    r = client.post(
        "/api/plants/batch-photos",
        files=[("files", ("a.jpg", BytesIO(_JPEG_BYTES), "image/jpeg"))],
        data={"plant_names": ["Aloe", ""]},
    )
    assert r.status_code == 400


def test_upload_batch_photos_rejects_invalid_type(client, mock_store_db):
    from io import BytesIO
    r = client.post(
        "/api/plants/batch-photos",
        files=[("files", ("a.txt", BytesIO(b"not an image"), "text/plain"))],
    )
    assert r.status_code == 400


def test_upload_batch_photos_requires_files(client, mock_store_db):
    r = client.post("/api/plants/batch-photos", files=[])
    assert r.status_code in (400, 422)


def test_get_batch_photo_job_not_found(client, mock_store_db):
    r = client.get("/api/plants/batch-photos/999")
    assert r.status_code == 404


def test_assign_batch_photo_item(client, mock_store_db, monkeypatch):
    import plant_ui.server as srv
    store, db, plants_dir = mock_store_db
    plant_data = Plant(
        name="Aloe", frequency_days=20, baseline_frequency_days=20,
        last_watered=date.today() - timedelta(days=5), location="indoor",
    )
    store.save_plants([plant_data])
    plants_dir.mkdir(parents=True, exist_ok=True)
    (plants_dir / "aloe.md").write_text("# Aloe\n## Health Assessments\n")

    job_id = srv.photo_batch.create_batch_job(db, [_JPEG_BYTES])
    job = db.get_batch_job(job_id)
    items = job["items"]
    items[0]["status"] = "unmatched"
    db.update_batch_job(job_id, items=items)

    monkeypatch.setattr(
        srv.photo_batch, "assess_image",
        lambda *a, **k: json.dumps({
            "status": "Healthy", "summary": "assigned via manual match",
            "observations": [], "watering_recommendation": "on_schedule",
            "care_actions": [], "noteworthy": False,
        }),
    )

    r = client.post(f"/api/plants/batch-photos/{job_id}/items/0/assign", json={"plant_name": "Aloe"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["matched_plant"] == "Aloe"


def test_assign_batch_photo_item_unknown_plant_returns_400(client, mock_store_db):
    import plant_ui.server as srv
    store, db, plants_dir = mock_store_db
    job_id = srv.photo_batch.create_batch_job(db, [_JPEG_BYTES])

    r = client.post(f"/api/plants/batch-photos/{job_id}/items/0/assign", json={"plant_name": "No Such Plant"})
    assert r.status_code == 400


def test_list_and_serve_plant_photos(client, mock_store_db):
    import plant_ui.server as srv
    store, db, plants_dir = mock_store_db
    plant_data = Plant(
        name="Aloe", frequency_days=20, baseline_frequency_days=20,
        last_watered=date.today() - timedelta(days=5), location="indoor",
    )
    store.save_plants([plant_data])

    srv.photo_batch.save_plant_photo(db, "Aloe", b"photo-bytes",
                                      assessment_summary="Looking good", assessment_status="Healthy")

    r = client.get("/api/plants/Aloe/photos")
    assert r.status_code == 200
    photos = r.json()
    assert len(photos) == 1
    assert photos[0]["status"] == "Healthy"

    photo_id = photos[0]["id"]
    img = client.get(f"/api/plants/Aloe/photos/{photo_id}")
    assert img.status_code == 200
    assert img.content == b"photo-bytes"


def test_list_plant_photos_unknown_plant_404(client, mock_store_db):
    r = client.get("/api/plants/No Such Plant/photos")
    assert r.status_code == 404


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthz_reports_db_failure(client, monkeypatch):
    import plant_ui.server as srv

    def broken_get_state(self, agent, key, default=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(srv.AgentDB, "get_state", broken_get_state)
    resp = client.get("/healthz")
    assert resp.status_code == 503
