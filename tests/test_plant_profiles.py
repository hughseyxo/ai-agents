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


# --- write_health_assessment ---

def test_write_health_assessment_appends_to_comment(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "monstera-deliciosa.md"
    p.write_text(
        "# Monstera Deliciosa\n\n"
        "## Health Assessments\n"
        "<!-- Photo assessments appended here -->\n\n"
        "## Other\n"
    )
    result = pp.write_health_assessment("Monstera Deliciosa", "### 2026-06-04 — Healthy\n- Looking good")
    assert result is True
    txt = p.read_text()
    assert "### 2026-06-04 — Healthy" in txt
    assert "Looking good" in txt


def test_write_health_assessment_creates_section_if_missing(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "fern.md"
    p.write_text("# Fern\n\n## Plant Info\n- Location: Indoor\n")
    result = pp.write_health_assessment("Fern", "### 2026-06-04 — Stressed\n- Drooping")
    assert result is True
    txt = p.read_text()
    assert "## Health Assessments" in txt
    assert "Drooping" in txt


def test_write_health_assessment_missing_profile_returns_false(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    assert pp.write_health_assessment("Ghost Plant", "notes") is False


def test_write_health_assessment_slug_with_spaces(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "snake-plant.md"
    p.write_text("# Snake Plant\n\n## Health Assessments\n<!-- Photo assessments appended here -->\n")
    result = pp.write_health_assessment("Snake Plant", "### 2026-06-04 — Healthy\n- Fine")
    assert result is True
    assert "Fine" in p.read_text()
