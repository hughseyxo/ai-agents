"""Tests for mealsave TikTok metadata fetch.

subprocess.run is mocked because we're testing the parsing/error-handling
logic, not yt-dlp itself.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "mealsave"
sys.path.insert(0, str(SKILL_DIR))

import mealsave  # noqa: E402


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestFetchTiktokMetadata:
    def test_parses_yt_dlp_json(self):
        fake_info = {
            "title": "Spicy Peanut Chicken Noodles",
            "description": "Ingredients:\n- 200g noodles\n- 2 tbsp peanut butter\n\nMethod:\n1. Boil noodles\n2. Mix sauce",
            "uploader": "cookingcreator",
            "duration": 45,
        }
        with patch("mealsave.subprocess.run", return_value=_completed(json.dumps(fake_info))):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/ZNRGbv9wX/")

        assert meta["title"] == "Spicy Peanut Chicken Noodles"
        assert "peanut butter" in meta["description"]
        assert meta["uploader"] == "cookingcreator"

    def test_returns_empty_on_called_process_error(self):
        with patch(
            "mealsave.subprocess.run",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd=["yt-dlp"]),
        ):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/bad/")
        assert meta == {}

    def test_returns_empty_on_timeout(self):
        with patch(
            "mealsave.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=30),
        ):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/slow/")
        assert meta == {}

    def test_returns_empty_on_bad_json(self):
        with patch("mealsave.subprocess.run", return_value=_completed("not-json{")):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/weird/")
        assert meta == {}

    def test_handles_missing_fields(self):
        """yt-dlp sometimes omits fields; result should still parse with empty strings."""
        with patch("mealsave.subprocess.run", return_value=_completed(json.dumps({"title": "X"}))):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/sparse/")
        assert meta["title"] == "X"
        assert meta["description"] == ""
        assert meta["uploader"] == ""

    def test_handles_null_fields(self):
        """yt-dlp may emit explicit nulls; result should coerce to empty strings."""
        with patch(
            "mealsave.subprocess.run",
            return_value=_completed(json.dumps({"title": None, "description": None, "uploader": None})),
        ):
            meta = mealsave.fetch_tiktok_metadata("https://vm.tiktok.com/null/")
        assert meta["title"] == ""
        assert meta["description"] == ""
        assert meta["uploader"] == ""
