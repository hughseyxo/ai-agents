import pytest
from pathlib import Path
from agents import garden_notes


@pytest.fixture
def notes_dirs(tmp_path, monkeypatch):
    obs = tmp_path / "docs" / "plant-observations"
    know = tmp_path / "docs" / "garden-knowledge"
    plants = tmp_path / "docs" / "plants"
    for d in (obs, know, plants):
        d.mkdir(parents=True)
    monkeypatch.setattr(garden_notes, "OBSERVATIONS_DIR", obs)
    monkeypatch.setattr(garden_notes, "KNOWLEDGE_DIR", know)
    monkeypatch.setattr(garden_notes, "REPO_ROOT", tmp_path)
    # Route plant-profile writes into tmp too
    from agents import plant_profiles
    monkeypatch.setattr(plant_profiles, "PLANTS_DIR", plants)
    monkeypatch.setattr(plant_profiles, "PROFILES_DIR", plants)
    return obs, know, plants


def test_slugify():
    assert garden_notes.slugify("Leaf Spot / Fungus!") == "leaf-spot-fungus"
    assert garden_notes.slugify("   ") == "note"


def test_create_observation_note_writes_frontmatter(notes_dirs):
    obs, _, _ = notes_dirs
    rel = garden_notes.create_observation_note(
        "lavender", "2026-06-23", "Leaf Spot", "Concern", "Brown spots on lower leaves."
    )
    path = obs / "lavender" / "2026-06-23-leaf-spot.md"
    assert path.exists()
    text = path.read_text()
    assert "type: observation" in text
    assert "plant: lavender" in text
    assert "Brown spots on lower leaves." in text
    assert rel.endswith("2026-06-23-leaf-spot.md")


def test_create_knowledge_note(notes_dirs):
    _, know, _ = notes_dirs
    rel = garden_notes.create_knowledge_note(
        "Mediterranean Watering", "Let soil dry between waterings.", ("lavender",)
    )
    path = know / "mediterranean-watering.md"
    assert path.exists()
    assert "type: knowledge" in path.read_text()


def test_read_garden_note_rejects_traversal(notes_dirs):
    with pytest.raises(ValueError):
        garden_notes.read_garden_note("../../CLAUDE.md")


def test_append_linked_note(notes_dirs):
    _, _, plants = notes_dirs
    (plants / "lavender.md").write_text("---\ntype: plant\n---\n# Lavender\n")
    ok = garden_notes.append_linked_note(
        "lavender", "docs/plant-observations/lavender/2026-06-23-leaf-spot.md", "Leaf Spot"
    )
    assert ok
    text = (plants / "lavender.md").read_text()
    assert "## Linked Notes" in text
    assert "2026-06-23-leaf-spot" in text


def test_maybe_create_observation_note_gating(notes_dirs):
    assert garden_notes.maybe_create_observation_note("lavender", {"noteworthy": False}) is None
    (notes_dirs[2] / "lavender.md").write_text("---\ntype: plant\n---\n# Lavender\n")
    rel = garden_notes.maybe_create_observation_note(
        "lavender",
        {"noteworthy": True, "note_title": "Aphids", "note_body": "Colony on new growth.", "status": "Concern"},
    )
    assert rel and "aphids" in rel
