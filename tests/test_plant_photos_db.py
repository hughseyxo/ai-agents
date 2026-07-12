"""Tests for AgentDB plant_photos and photo_batch_jobs support."""

import pytest

from agents.db import AgentDB


@pytest.fixture
def db(tmp_path):
    d = AgentDB(db_path=tmp_path / "agents.db")
    yield d
    d.close()


# --- plant_photos ---


def test_add_plant_photo_returns_id(db):
    photo_id = db.add_plant_photo("Monstera Deliciosa", "/data/plant-photos/monstera-deliciosa/a.jpg")
    assert isinstance(photo_id, int)


def test_get_recent_plant_photos_orders_newest_first(db):
    db.add_plant_photo("Yucca", "/p/1.jpg", taken_at="2026-06-01T00:00:00")
    db.add_plant_photo("Yucca", "/p/2.jpg", taken_at="2026-06-15T00:00:00")
    db.add_plant_photo("Yucca", "/p/3.jpg", taken_at="2026-07-01T00:00:00")

    rows = db.get_recent_plant_photos("Yucca", limit=2)

    assert [r["file_path"] for r in rows] == ["/p/3.jpg", "/p/2.jpg"]


def test_get_recent_plant_photos_scoped_to_plant(db):
    db.add_plant_photo("Yucca", "/p/yucca.jpg")
    db.add_plant_photo("Lavender", "/p/lavender.jpg")

    rows = db.get_recent_plant_photos("Yucca", limit=10)

    assert [r["file_path"] for r in rows] == ["/p/yucca.jpg"]


def test_get_plant_photo_history_includes_assessment_fields(db):
    db.add_plant_photo(
        "Yucca", "/p/1.jpg",
        assessment_summary="Looking healthy", assessment_status="healthy",
    )

    rows = db.get_plant_photo_history("Yucca", limit=10)

    assert rows[0]["assessment_summary"] == "Looking healthy"
    assert rows[0]["assessment_status"] == "healthy"
    assert "id" in rows[0] and "taken_at" in rows[0]


def test_prune_plant_photos_keeps_most_recent_and_returns_pruned_paths(db):
    for i in range(12):
        db.add_plant_photo("Yucca", f"/p/{i}.jpg", taken_at=f"2026-06-{i + 1:02d}T00:00:00")

    pruned = db.prune_plant_photos("Yucca", keep=10)

    assert sorted(pruned) == ["/p/0.jpg", "/p/1.jpg"]
    remaining = db.get_recent_plant_photos("Yucca", limit=100)
    assert len(remaining) == 10
    assert "/p/0.jpg" not in [r["file_path"] for r in remaining]


def test_prune_plant_photos_noop_when_under_limit(db):
    db.add_plant_photo("Yucca", "/p/1.jpg")

    pruned = db.prune_plant_photos("Yucca", keep=10)

    assert pruned == []


# --- photo_batch_jobs ---


def test_create_and_get_batch_job_round_trips_items(db):
    items = [{"temp_path": "/tmp/a.jpg", "status": "pending"}]

    job_id = db.create_batch_job(items)
    job = db.get_batch_job(job_id)

    assert job["status"] == "running"
    assert job["current_index"] == 0
    assert job["items"] == items


def test_get_batch_job_missing_returns_none(db):
    assert db.get_batch_job(999) is None


def test_update_batch_job_persists_fields(db):
    job_id = db.create_batch_job([{"temp_path": "/tmp/a.jpg", "status": "pending"}])

    db.update_batch_job(
        job_id,
        status="paused",
        current_index=1,
        pause_reason="usage limit",
        next_ping_at="2026-07-12T10:00:00",
        items=[{"temp_path": "/tmp/a.jpg", "status": "done"}],
    )
    job = db.get_batch_job(job_id)

    assert job["status"] == "paused"
    assert job["current_index"] == 1
    assert job["pause_reason"] == "usage limit"
    assert job["next_ping_at"] == "2026-07-12T10:00:00"
    assert job["items"] == [{"temp_path": "/tmp/a.jpg", "status": "done"}]


def test_update_batch_job_persists_paused_seconds(db):
    job_id = db.create_batch_job([])
    db.update_batch_job(job_id, paused_seconds=300)
    assert db.get_batch_job(job_id)["paused_seconds"] == 300


def test_update_batch_job_rejects_unknown_field(db):
    job_id = db.create_batch_job([])
    with pytest.raises(ValueError, match="unknown field"):
        db.update_batch_job(job_id, some_arbitrary_column="value")


def test_list_active_batch_jobs_filters_by_status(db):
    running_id = db.create_batch_job([])
    paused_id = db.create_batch_job([])
    db.update_batch_job(paused_id, status="paused")
    done_id = db.create_batch_job([])
    db.update_batch_job(done_id, status="done")

    active_ids = {j["id"] for j in db.list_active_batch_jobs()}

    assert active_ids == {running_id, paused_id}
