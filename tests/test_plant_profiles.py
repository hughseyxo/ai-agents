"""Tests for agents.plant_profiles — profile-doc file I/O helpers."""

import pytest

import agents.plant_profiles as pp


# --- safe_profile_path (path-traversal guard) ---

def test_safe_profile_path_normal_name_inside_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    path = pp.safe_profile_path("Monstera Deliciosa")
    assert path == (tmp_path / "monstera-deliciosa.md").resolve()
    assert path.parent == tmp_path.resolve()


@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "../../../root/.ssh/authorized_keys",
    "..",
    "/etc/passwd",
    "a/../../b",
])
def test_safe_profile_path_hostile_names_stay_inside(tmp_path, monkeypatch, hostile):
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    path = pp.safe_profile_path(hostile)
    # Whatever the input, the resolved path must remain directly inside PLANTS_DIR.
    assert path.parent == tmp_path.resolve()


def test_safe_profile_path_rejects_escape(tmp_path, monkeypatch):
    # If a name ever resolves outside PLANTS_DIR, the guard must raise.
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    # Monkeypatch _slug to a passthrough so a real separator survives → escape attempt.
    monkeypatch.setattr(pp, "_slug", lambda n: n)
    with pytest.raises(ValueError):
        pp.safe_profile_path("../../escape")


# --- frontmatter parse/upsert ---

def test_parse_frontmatter_roundtrip():
    text = "---\ntype: plant\nlocation: indoor\n---\n# Monstera\nbody\n"
    meta, body = pp.parse_frontmatter(text)
    assert meta == {"type": "plant", "location": "indoor"}
    assert body == "# Monstera\nbody\n"


def test_parse_frontmatter_absent():
    meta, body = pp.parse_frontmatter("# No fm\n")
    assert meta == {}
    assert body == "# No fm\n"


def test_upsert_frontmatter_preserves_body(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "monstera.md"
    p.write_text("# Monstera\n\n## Current Observations\n- vigorous\n")
    pp.upsert_frontmatter("Monstera", {"type": "plant", "needs_photo": False})
    out = p.read_text()
    assert out.startswith("---\n")
    assert "## Current Observations" in out
    assert "- vigorous" in out


# --- rewrite_section ---

def test_rewrite_existing_section(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "monstera.md"
    p.write_text("# M\n\n## Current Observations\n- old\n\n## History\n- 2026-05 repot\n")
    pp.rewrite_section("Monstera", "Current Observations", "- new fact\n")
    out = p.read_text()
    assert "- new fact" in out and "- old" not in out
    assert "## History\n- 2026-05 repot" in out   # other section intact


def test_rewrite_creates_absent_section(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    p = tmp_path / "m.md"; p.write_text("# M\n")
    pp.rewrite_section("M", "Care Research", "- tolerates 7-14d\n")
    assert "## Care Research\n- tolerates 7-14d" in p.read_text()


# --- read_profile_context ---

def test_read_profile_context(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PROFILES_DIR", tmp_path)
    (tmp_path / "m.md").write_text(
        "---\ntype: plant\nneeds_photo: false\n---\n# M\n"
        "## Current Observations\n- repot overdue\n\n"
        "## Health Assessments\n"
        "### 2026-06-18 — Healthy\n- delta a\n"
        "### 2026-06-06 — Healthy\n- delta b\n"
        "### 2026-05-01 — Healthy\n- old\n"
    )
    ctx = pp.read_profile_context("M", max_assessments=2)
    assert "repot overdue" in ctx
    assert "2026-06-18" in ctx and "2026-06-06" in ctx
    assert "2026-05-01" not in ctx        # bounded


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


# --- append_intelligence_note ---

def test_append_intelligence_note_inserts_after_comment(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "lavender.md"
    p.write_text(
        "# Lavender\n\n"
        "## Intelligence Notes\n"
        "<!-- Appended by each intelligence run -->\n"
        "### 2026-06-01 (pruning)\n- Old note\n"
    )
    result = pp.append_intelligence_note("Lavender", "### 2026-06-17 (completed)\n- deadhead (done via PWA)")
    assert result is True
    txt = p.read_text()
    assert "### 2026-06-17 (completed)" in txt
    assert "deadhead (done via PWA)" in txt
    # New note should appear before the old one (inserted after comment)
    assert txt.index("2026-06-17") < txt.index("2026-06-01")


def test_append_intelligence_note_section_missing_creates_it(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "fern.md"
    p.write_text("# Fern\n\n## Plant Info\n- Location: Indoor\n")
    result = pp.append_intelligence_note("Fern", "### 2026-06-17 (completed)\n- repot (done via PWA)")
    assert result is True
    txt = p.read_text()
    assert "## Intelligence Notes" in txt
    assert "repot (done via PWA)" in txt


def test_append_intelligence_note_missing_profile_returns_false(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    assert pp.append_intelligence_note("Ghost", "### 2026-06-17\n- note") is False
