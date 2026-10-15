"""Data model and spoiler-safe composition logic for the WoK companion site.

Chapter/character source data lives in wok_tracker/data/content.py. This
module resolves, for a given chapter, each POV character's *current* bio —
the latest stage whose starting chapter is at or before the requested one in
book order — so nothing from a later chapter can ever leak through.
"""
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel

from wok_tracker.data.content import CHAPTERS, CHARACTERS, REGIONS


class ChapterSummary(BaseModel):
    id: str
    number: Optional[int]
    label: str
    part: str


class CharacterBio(BaseModel):
    key: str
    name: str
    blurb: str


class ChapterDetail(BaseModel):
    id: str
    number: Optional[int]
    label: str
    part: str
    location: Optional[str]
    location_label: Optional[str]
    characters: list[CharacterBio]


class Region(BaseModel):
    key: str
    label: str


@lru_cache(maxsize=1)
def _chapter_order() -> dict[str, int]:
    return {c["id"]: i for i, c in enumerate(CHAPTERS)}


@lru_cache(maxsize=1)
def _region_labels() -> dict[str, str]:
    return {r["key"]: r["label"] for r in REGIONS}


def _bio_as_of(char_key: str, chapter_seq: int) -> Optional[CharacterBio]:
    char = CHARACTERS.get(char_key)
    if char is None:
        return None
    order = _chapter_order()
    current_name, current_blurb = None, None
    for stage_chapter_id, name, blurb in char["stages"]:
        if order.get(stage_chapter_id, -1) <= chapter_seq:
            current_name, current_blurb = name, blurb
        else:
            break
    if current_blurb is None:
        return None
    return CharacterBio(key=char_key, name=current_name, blurb=current_blurb)


def list_chapters() -> list[ChapterSummary]:
    return [
        ChapterSummary(id=c["id"], number=c["number"], label=c["label"], part=c["part"])
        for c in CHAPTERS
    ]


def get_chapter(chapter_id: str) -> Optional[ChapterDetail]:
    order = _chapter_order()
    seq = order.get(chapter_id)
    if seq is None:
        return None
    chapter = CHAPTERS[seq]
    labels = _region_labels()
    characters = [
        bio for key in chapter["pov"] if (bio := _bio_as_of(key, seq)) is not None
    ]
    return ChapterDetail(
        id=chapter["id"],
        number=chapter["number"],
        label=chapter["label"],
        part=chapter["part"],
        location=chapter["location"],
        location_label=labels.get(chapter["location"]) if chapter["location"] else None,
        characters=characters,
    )


def list_regions() -> list[Region]:
    return [Region(key=r["key"], label=r["label"]) for r in REGIONS]
