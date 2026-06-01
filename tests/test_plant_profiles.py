"""Tests for agents.plant_profiles — profile-doc file I/O helpers."""


def test_append_frequency_history_inserts_row(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "lantana.md"
    p.write_text("# Lantana\n\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n\n## Notes\n")
    assert pp.append_frequency_history("Lantana", 7, 5, "intelligence: wilting") is True
    txt = p.read_text()
    assert "7→5 days" in txt
    assert "intelligence: wilting" in txt


def test_append_frequency_history_missing_profile(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    assert pp.append_frequency_history("Ghost", 7, 5, "x") is False
