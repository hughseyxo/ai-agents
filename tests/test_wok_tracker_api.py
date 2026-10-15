import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from wok_tracker.server import app
from wok_tracker.data.content import CHAPTERS, CHARACTERS, REGIONS
from wok_tracker.wok_model import get_chapter


@pytest.fixture
def client():
    return TestClient(app)


# --- chapter list ---

def test_list_chapters_returns_all_in_book_order(client):
    data = client.get("/api/chapters").json()
    assert len(data) == len(CHAPTERS)
    assert [c["id"] for c in data] == [c["id"] for c in CHAPTERS]
    assert data[0]["id"] == "prelude"
    assert data[-1]["id"] == "epilogue"


# --- single chapter detail ---

def test_get_chapter_returns_pov_and_location(client):
    resp = client.get("/api/chapters/ch12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["location"] == "shattered_plains"
    assert {c["key"] for c in data["characters"]} == set(CHAPTERS[
        next(i for i, c in enumerate(CHAPTERS) if c["id"] == "ch12")
    ]["pov"])


def test_unknown_chapter_404s(client):
    resp = client.get("/api/chapters/ch9999")
    assert resp.status_code == 404


def test_regions_endpoint_matches_source(client):
    data = client.get("/api/regions").json()
    assert {r["key"] for r in data} == {r["key"] for r in REGIONS}


def test_index_and_healthz(client):
    assert client.get("/").status_code == 200
    assert client.get("/healthz").status_code == 200


# --- spoiler-safety: bios never jump ahead of the requested chapter ---

def test_bio_progression_is_monotonic_and_bounded():
    """A character's bio at chapter N must be exactly the stage active at N —
    never a stage that only starts later in book order."""
    order = {c["id"]: i for i, c in enumerate(CHAPTERS)}
    for key, char in CHARACTERS.items():
        stage_seqs = [order[cid] for cid, _, _ in char["stages"]]
        assert stage_seqs == sorted(stage_seqs), f"{key} stages out of book order"

        for stage_idx, (stage_chapter_id, name, blurb) in enumerate(char["stages"]):
            seq = order[stage_chapter_id]
            chapter = get_chapter(CHAPTERS[seq]["id"])
            bio = next((c for c in chapter.characters if c.key == key), None)
            if bio is not None:
                assert bio.blurb == blurb

            # One chapter before this stage starts, the bio must not yet
            # reflect it (guards against off-by-one leaks into earlier chapters).
            if seq > 0:
                prev_chapter_id = CHAPTERS[seq - 1]["id"]
                prev_detail = get_chapter(prev_chapter_id)
                prev_bio = next((c for c in prev_detail.characters if c.key == key), None)
                if prev_bio is not None:
                    assert prev_bio.blurb != blurb


def test_kaladin_bio_differs_between_early_and_late_chapters():
    early = next(c for c in get_chapter("ch2").characters if c.key == "kaladin")
    late = next(c for c in get_chapter("ch73").characters if c.key == "kaladin")
    assert early.blurb != late.blurb
    assert "prisoner" in early.blurb.lower()


def test_syl_is_not_named_before_chapter_6():
    """Regression: her name (Syl) isn't given in the book until chapter 6."""
    before = next(c for c in get_chapter("ch2").characters if c.key == "syl")
    after = next(c for c in get_chapter("ch6").characters if c.key == "syl")
    assert before.name != "Syl"
    assert after.name == "Syl"


def test_every_pov_character_key_is_defined():
    for chapter in CHAPTERS:
        for key in chapter["pov"]:
            assert key in CHARACTERS, f"{chapter['id']} references undefined character '{key}'"


def test_every_location_key_is_a_known_region():
    region_keys = {r["key"] for r in REGIONS}
    for chapter in CHAPTERS:
        if chapter["location"] is not None:
            assert chapter["location"] in region_keys, chapter["id"]
