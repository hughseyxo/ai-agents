"""Tests for scripts.gen_codebase_map — the server-only codebase-map generator.

The generator regenerates a script-managed file-list block in each docs/_map/<area>.md
note while preserving hand-written prose outside the markers and existing per-file
descriptions inside them.
"""

from scripts import gen_codebase_map as gcm


# --- discover_files ---

def test_discover_files_lists_matching_files_relative(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "base.py").write_text("x")
    (tmp_path / "agents" / "db.py").write_text("x")
    (tmp_path / "agents" / "notes.txt").write_text("x")  # not matched by *.py

    found = gcm.discover_files(["agents/*.py"], tmp_path)

    assert found == ["agents/base.py", "agents/db.py"]


def test_discover_files_excludes_git_ignored_paths(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("skills/vendor/\n")
    (tmp_path / "skills" / "vendor").mkdir(parents=True)
    (tmp_path / "skills" / "vendor" / "dep.py").write_text("x")  # gitignored
    (tmp_path / "skills" / "real.py").write_text("x")            # tracked surface

    found = gcm.discover_files(["skills/**/*"], tmp_path)

    assert found == ["skills/real.py"]


def test_discover_files_excludes_dirs_dotfiles_and_pycache(tmp_path):
    (tmp_path / "skills" / "mealsave").mkdir(parents=True)
    (tmp_path / "skills" / "mealsave" / "mealsave.py").write_text("x")
    (tmp_path / "skills" / ".hidden.py").write_text("x")
    (tmp_path / "skills" / "__pycache__").mkdir()
    (tmp_path / "skills" / "__pycache__" / "x.pyc").write_text("x")

    found = gcm.discover_files(["skills/**/*"], tmp_path)

    assert found == ["skills/mealsave/mealsave.py"]


# --- parse_block ---

def test_parse_block_extracts_path_and_description():
    text = (
        "intro\n"
        f"{gcm.START}\n"
        "- `agents/base.py` — BaseAgent class — lifecycle, `providers` attr\n"
        "- `agents/db.py` — SQLite wrapper\n"
        f"{gcm.END}\n"
        "outro\n"
    )
    parsed = gcm.parse_block(text)
    assert parsed == {
        "agents/base.py": "BaseAgent class — lifecycle, `providers` attr",
        "agents/db.py": "SQLite wrapper",
    }


def test_parse_block_no_markers_returns_empty():
    assert gcm.parse_block("just prose, no block") == {}


# --- splice ---

def test_splice_preserves_prose_outside_markers():
    text = (
        "# Title\n\nSome prose before.\n\n"
        f"{gcm.START}\n- `old.py` — old\n{gcm.END}\n\n"
        "Some prose after.\n"
    )
    out = gcm.splice(text, ["- `new.py` — new"])
    assert "Some prose before." in out
    assert "Some prose after." in out
    assert "- `new.py` — new" in out
    assert "- `old.py` — old" not in out


def test_splice_appends_block_when_no_markers_preserving_prose():
    text = "# Title\n\nHand-written prose.\n"
    out = gcm.splice(text, ["- `a.py` — a"], title="agents")
    assert "Hand-written prose." in out
    assert gcm.START in out and gcm.END in out
    assert "- `a.py` — a" in out


# --- update_area_note ---

def _read(p):
    return p.read_text()


def test_update_area_note_new_file_gets_todo_placeholder(tmp_path):
    note = tmp_path / "agents.md"
    added, removed = gcm.update_area_note(note, "agents", ["agents/base.py"])
    assert (added, removed) == (1, 0)
    assert f"- `agents/base.py` — {gcm.TODO}" in _read(note)


def test_update_area_note_preserves_existing_description(tmp_path):
    note = tmp_path / "agents.md"
    note.write_text(
        f"# agents\n\n{gcm.START}\n"
        "- `agents/base.py` — BaseAgent class — the real description\n"
        f"{gcm.END}\n"
    )
    added, removed = gcm.update_area_note(note, "agents", ["agents/base.py"])
    assert (added, removed) == (0, 0)
    assert "BaseAgent class — the real description" in _read(note)
    assert gcm.TODO not in _read(note)


def test_update_area_note_removes_vanished_file(tmp_path):
    note = tmp_path / "agents.md"
    note.write_text(
        f"# agents\n\n{gcm.START}\n"
        "- `agents/base.py` — kept\n"
        "- `agents/gone.py` — removed soon\n"
        f"{gcm.END}\n"
    )
    added, removed = gcm.update_area_note(note, "agents", ["agents/base.py"])
    assert (added, removed) == (0, 1)
    out = _read(note)
    assert "agents/base.py" in out
    assert "agents/gone.py" not in out


# --- generate (integration) ---

def _build_repo(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "base.py").write_text("x")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("x")
    areas = {"agents": ["agents/*.py"], "tests": ["tests/*.py"]}
    return areas


def test_generate_creates_map_dir_and_notes_when_absent(tmp_path):
    areas = _build_repo(tmp_path)
    map_dir = tmp_path / "docs" / "_map"

    gcm.generate(tmp_path, map_dir, areas=areas)

    assert (map_dir / "index.md").exists()
    assert (map_dir / "agents.md").exists()
    assert (map_dir / "tests.md").exists()
    assert "[[agents]]" in (map_dir / "index.md").read_text()
    assert "agents/base.py" in (map_dir / "agents.md").read_text()


def test_generate_is_idempotent(tmp_path):
    areas = _build_repo(tmp_path)
    map_dir = tmp_path / "docs" / "_map"

    gcm.generate(tmp_path, map_dir, areas=areas)
    snapshot = {p.name: p.read_text() for p in map_dir.glob("*.md")}
    gcm.generate(tmp_path, map_dir, areas=areas)
    after = {p.name: p.read_text() for p in map_dir.glob("*.md")}

    assert snapshot == after


def test_generate_preserves_prose_and_descriptions_across_runs(tmp_path):
    areas = _build_repo(tmp_path)
    map_dir = tmp_path / "docs" / "_map"
    gcm.generate(tmp_path, map_dir, areas=areas)

    # Human edits: adds prose + fills in a description.
    note = map_dir / "agents.md"
    text = note.read_text().replace(
        f"- `agents/base.py` — {gcm.TODO}",
        "- `agents/base.py` — BaseAgent lifecycle",
    )
    text += "\n## My notes\nKeep this prose.\n"
    note.write_text(text)

    # New file appears, regenerate.
    (tmp_path / "agents" / "db.py").write_text("x")
    gcm.generate(tmp_path, map_dir, areas=areas)

    out = note.read_text()
    assert "BaseAgent lifecycle" in out          # description preserved
    assert "Keep this prose." in out             # prose preserved
    assert f"- `agents/db.py` — {gcm.TODO}" in out  # new file added
