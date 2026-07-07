"""Tests for ytsave's parsing, matching, and dedup logic.

subprocess/requests/ytmusicapi are mocked throughout — these test the
pure logic (URL classification, JSON parsing, dedup) not the external
tools themselves.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "ytsave"
sys.path.insert(0, str(SKILL_DIR))

import ytsave  # noqa: E402


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestIsTiktok:
    @pytest.mark.parametrize("url", [
        "https://vm.tiktok.com/ZNRE2STBx/",
        "https://www.tiktok.com/@user/video/123",
        "https://tiktok.com/@user/photo/456",
        "https://vt.tiktok.com/abc/",
    ])
    def test_recognizes_tiktok_urls(self, url):
        assert ytsave.is_tiktok(url) is True

    @pytest.mark.parametrize("url", [
        "https://youtube.com/watch?v=abc",
        "https://example.com/tiktok-lookalike",
        "https://faketiktok.com/@x/video/1",
        "https://tiktok.com.evil.com/x",
        "https://nottiktok.com/",
    ])
    def test_rejects_non_tiktok_urls(self, url):
        assert ytsave.is_tiktok(url) is False


class TestExtractCaption:
    def test_extracts_fields(self):
        info = {"title": "T", "description": "D", "uploader": "U"}
        assert ytsave.extract_caption(info) == {"title": "T", "description": "D", "uploader": "U"}

    def test_coerces_missing_and_null_fields(self):
        info = {"title": None}
        caption = ytsave.extract_caption(info)
        assert caption == {"title": "", "description": "", "uploader": ""}


class TestIsPhotoPost:
    def test_true_when_images_present(self):
        assert ytsave.is_photo_post({"images": [{"url": "x"}]}) is True

    def test_true_when_playlist_type(self):
        assert ytsave.is_photo_post({"_type": "playlist"}) is True

    def test_false_for_regular_video(self):
        assert ytsave.is_photo_post({"duration": 30, "ext": "mp4"}) is False

    def test_false_for_empty_info(self):
        assert ytsave.is_photo_post({}) is False


class TestExtractSlideshowImageUrls:
    def test_picks_highest_res_from_url_list(self):
        info = {"images": [
            {"url_list": ["low.jpg", "high.jpg"]},
            {"url_list": ["only.jpg"]},
        ]}
        assert ytsave.extract_slideshow_image_urls(info) == ["high.jpg", "only.jpg"]

    def test_falls_back_to_url_field(self):
        info = {"images": [{"url": "direct.jpg"}]}
        assert ytsave.extract_slideshow_image_urls(info) == ["direct.jpg"]

    def test_empty_when_no_images(self):
        assert ytsave.extract_slideshow_image_urls({}) == []


class TestParseTikwmImages:
    def test_returns_images(self):
        assert ytsave.parse_tikwm_images({"images": ["a.jpg", "b.jpg"]}) == ["a.jpg", "b.jpg"]

    def test_raises_when_no_images(self):
        with pytest.raises(ValueError):
            ytsave.parse_tikwm_images({"play": "video.mp4"})


class TestParseTitleList:
    def test_parses_clean_json_array(self):
        output = json.dumps([{"title": "Video A", "channel": "Chan A"}])
        assert ytsave.parse_title_list(output) == [{"title": "Video A", "channel": "Chan A"}]

    def test_strips_markdown_fences(self):
        output = "```json\n[{\"title\": \"X\"}]\n```"
        result = ytsave.parse_title_list(output)
        assert result == [{"title": "X", "channel": None}]

    def test_skips_items_without_title(self):
        output = json.dumps([{"channel": "no title here"}, {"title": "Y"}])
        result = ytsave.parse_title_list(output)
        assert result == [{"title": "Y", "channel": None}]

    def test_empty_array_returns_empty_list(self):
        assert ytsave.parse_title_list("[]") == []

    def test_raises_on_non_array(self):
        with pytest.raises(ValueError):
            ytsave.parse_title_list(json.dumps({"title": "not an array"}))

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            ytsave.parse_title_list("not json at all")


class TestParseYtsearchNdjson:
    def test_parses_multiple_lines(self):
        lines = "\n".join([
            json.dumps({"id": "abc123", "title": "Video 1", "channel": "Chan1", "duration": 120}),
            json.dumps({"id": "def456", "title": "Video 2", "uploader": "Chan2", "duration": 60}),
        ])
        result = ytsave.parse_ytsearch_ndjson(lines)
        assert result == [
            {"video_id": "abc123", "title": "Video 1", "channel": "Chan1", "duration": 120},
            {"video_id": "def456", "title": "Video 2", "channel": "Chan2", "duration": 60},
        ]

    def test_skips_bad_lines(self):
        lines = "not json\n" + json.dumps({"id": "abc", "title": "OK"})
        result = ytsave.parse_ytsearch_ndjson(lines)
        assert len(result) == 1
        assert result[0]["video_id"] == "abc"

    def test_skips_entries_without_id(self):
        result = ytsave.parse_ytsearch_ndjson(json.dumps({"title": "No ID"}))
        assert result == []

    def test_empty_input(self):
        assert ytsave.parse_ytsearch_ndjson("") == []


class TestParseMatchResponse:
    def test_maps_indices_to_video_ids(self):
        output = json.dumps([
            {"index": 0, "video_id": "abc"},
            {"index": 1, "video_id": None},
        ])
        result = ytsave.parse_match_response(output, expected_count=2)
        assert result == ["abc", None]

    def test_missing_indices_default_to_none(self):
        output = json.dumps([{"index": 1, "video_id": "xyz"}])
        result = ytsave.parse_match_response(output, expected_count=3)
        assert result == [None, "xyz", None]

    def test_ignores_out_of_range_index(self):
        output = json.dumps([{"index": 5, "video_id": "xyz"}])
        result = ytsave.parse_match_response(output, expected_count=2)
        assert result == [None, None]

    def test_raises_on_non_array(self):
        with pytest.raises(ValueError):
            ytsave.parse_match_response(json.dumps({"not": "array"}), expected_count=1)


class TestClassifyMatches:
    def test_filters_skips(self):
        new_ids, added, skipped = ytsave.classify_matches(
            ["T1", "T2", "T3"], ["a", None, "b"], existing=set()
        )
        assert new_ids == ["a", "b"]
        assert added == ["T1", "T3"]
        assert skipped == ["T2"]

    def test_filters_already_existing(self):
        new_ids, added, skipped = ytsave.classify_matches(
            ["T1", "T2"], ["a", "b"], existing={"a"}
        )
        assert new_ids == ["b"]
        assert added == ["T2"]
        assert skipped == ["T1"]

    def test_dedupes_internal_duplicates_and_reports_second_title_as_skipped(self):
        new_ids, added, skipped = ytsave.classify_matches(
            ["T1", "T2", "T3"], ["a", "a", "b"], existing=set()
        )
        assert new_ids == ["a", "b"]
        assert added == ["T1", "T3"]
        assert skipped == ["T2"]

    def test_preserves_order(self):
        new_ids, added, skipped = ytsave.classify_matches(
            ["T1", "T2", "T3"], ["c", "a", "b"], existing=set()
        )
        assert new_ids == ["c", "a", "b"]
        assert added == ["T1", "T2", "T3"]
        assert skipped == []


class TestExistingVideoIds:
    def test_extracts_ids_from_tracks(self):
        playlist = {"tracks": [{"videoId": "a"}, {"videoId": "b"}, {"title": "no id"}]}
        assert ytsave.existing_video_ids(playlist) == {"a", "b"}

    def test_empty_playlist(self):
        assert ytsave.existing_video_ids({}) == set()
        assert ytsave.existing_video_ids({"tracks": []}) == set()


class TestResolvePlaylistIdFromLibrary:
    def test_matches_case_insensitively(self):
        playlists = [{"title": "TikTok Finds", "playlistId": "PL123"}]
        assert ytsave.resolve_playlist_id_from_library(playlists, "tiktok finds") == "PL123"

    def test_returns_none_when_not_found(self):
        playlists = [{"title": "Other Playlist", "playlistId": "PL999"}]
        assert ytsave.resolve_playlist_id_from_library(playlists, "TikTok Finds") is None


class TestEnsurePlaylist:
    def test_uses_cached_id_if_still_valid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytsave, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ytsave, "CONFIG_DIR", tmp_path)
        ytmusic = MagicMock()
        ytmusic.get_playlist.return_value = {"tracks": []}
        config = {"playlist_id": "PLcached"}
        result = ytsave.ensure_playlist(ytmusic, config, "TikTok Finds")
        assert result == "PLcached"
        ytmusic.create_playlist.assert_not_called()

    def test_creates_playlist_when_none_cached_or_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytsave, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ytsave, "CONFIG_DIR", tmp_path)
        ytmusic = MagicMock()
        ytmusic.get_library_playlists.return_value = []
        ytmusic.create_playlist.return_value = "PLnew"
        config = {"playlist_id": None}
        result = ytsave.ensure_playlist(ytmusic, config, "TikTok Finds")
        assert result == "PLnew"
        ytmusic.create_playlist.assert_called_once()

    def test_reuses_existing_library_playlist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytsave, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ytsave, "CONFIG_DIR", tmp_path)
        ytmusic = MagicMock()
        ytmusic.get_library_playlists.return_value = [{"title": "TikTok Finds", "playlistId": "PLexisting"}]
        config = {"playlist_id": None}
        result = ytsave.ensure_playlist(ytmusic, config, "TikTok Finds")
        assert result == "PLexisting"
        ytmusic.create_playlist.assert_not_called()

    def test_recovers_when_cached_id_deleted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytsave, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ytsave, "CONFIG_DIR", tmp_path)
        ytmusic = MagicMock()
        ytmusic.get_playlist.side_effect = Exception("404")
        ytmusic.get_library_playlists.return_value = []
        ytmusic.create_playlist.return_value = "PLrecreated"
        config = {"playlist_id": "PLdeleted"}
        result = ytsave.ensure_playlist(ytmusic, config, "TikTok Finds")
        assert result == "PLrecreated"


class TestRunYtdlpDumpJson:
    def test_parses_json_output(self):
        with patch("ytsave.subprocess.run", return_value=_completed(json.dumps({"title": "T"}))):
            assert ytsave.run_ytdlp_dump_json("https://vm.tiktok.com/x/") == {"title": "T"}

    def test_returns_empty_on_error(self):
        with patch("ytsave.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["yt-dlp"])):
            assert ytsave.run_ytdlp_dump_json("https://vm.tiktok.com/x/") == {}

    def test_returns_empty_on_timeout(self):
        with patch("ytsave.subprocess.run", side_effect=subprocess.TimeoutExpired(["yt-dlp"], 30)):
            assert ytsave.run_ytdlp_dump_json("https://vm.tiktok.com/x/") == {}


class TestFetchTikwmData:
    def test_returns_data_on_success(self):
        resp = MagicMock()
        resp.json.return_value = {"code": 0, "data": {"images": ["a.jpg"]}}
        resp.raise_for_status = MagicMock()
        with patch("ytsave.requests.get", return_value=resp):
            assert ytsave.fetch_tikwm_data("https://vm.tiktok.com/x/") == {"images": ["a.jpg"]}

    def test_returns_empty_on_api_error_code(self):
        resp = MagicMock()
        resp.json.return_value = {"code": -1, "msg": "not found"}
        resp.raise_for_status = MagicMock()
        with patch("ytsave.requests.get", return_value=resp):
            assert ytsave.fetch_tikwm_data("https://vm.tiktok.com/x/") == {}

    def test_returns_empty_on_request_exception(self):
        import requests as real_requests
        with patch("ytsave.requests.get", side_effect=real_requests.RequestException("boom")):
            assert ytsave.fetch_tikwm_data("https://vm.tiktok.com/x/") == {}
