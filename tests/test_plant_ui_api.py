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

@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_agents.db"

@pytest.fixture
def mock_store_db(temp_db_path, monkeypatch, tmp_path):
    # Override profiles directory in plant_profiles helper
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path / "plants")
    
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
    
    # Mock Todoist task completion to avoid network calls
    with patch("plant_ui.server.complete_todoist_task_for_plant", return_value=True) as mock_todoist:
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
    
    with patch("plant_ui.server.complete_todoist_task_for_plant", return_value=True):
        response = client.post("/api/plants/water-all", json={"location": "outdoor"})
        assert response.status_code == 200
        assert response.json()["waterED_count"] == 2
        
        # Verify Jade and Rosemary last_watered is today
        db_plants = store.get_plants()
        mint = next(p for p in db_plants if p.name == "Mint")
        rosemary = next(p for p in db_plants if p.name == "Rosemary")
        ficus = next(p for p in db_plants if p.name == "Ficus")
        
        assert mint.last_watered == date.today()
        assert rosemary.last_watered == date.today()
        assert ficus.last_watered != date.today() # Indoor plant not watered

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
    
    with patch("plant_ui.server.complete_todoist_task_for_plant", return_value=True):
        response = client.delete("/api/plants/Cactus")
        assert response.status_code == 200
        assert len(store.get_plants()) == 0
        assert not prof_path.exists()

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
        file_data = {"file": ("aloe.jpg", BytesIO(b"dummy image bytes"), "image/jpeg")}
        
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
