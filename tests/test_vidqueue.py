"""Tests for vidqueue skill."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "vidqueue"
sys.path.insert(0, str(SKILL_DIR))


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Task 2: Core utilities
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_dies_when_config_missing(self, tmp_path, monkeypatch):
        import vidqueue
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(SystemExit):
            vidqueue.load_config()

    def test_parses_env_file(self, tmp_path, monkeypatch):
        import vidqueue
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg_dir = tmp_path / ".config" / "vidqueue"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / ".env").write_text(
            'TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_USER_ID=123\nYOUTUBE_PLAYLIST_NAME=My Queue\n'
        )
        cfg = vidqueue.load_config()
        assert cfg["TELEGRAM_BOT_TOKEN"] == "abc"
        assert cfg["YOUTUBE_PLAYLIST_NAME"] == "My Queue"


class TestIsTiktok:
    def test_standard_url(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://www.tiktok.com/@user/video/123")

    def test_vm_shortlink(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://vm.tiktok.com/ZABCdef/")

    def test_vt_shortlink(self):
        import vidqueue
        assert vidqueue.is_tiktok("https://vt.tiktok.com/ZABCdef/")

    def test_rejects_youtube(self):
        import vidqueue
        assert not vidqueue.is_tiktok("https://youtube.com/watch?v=abc")

    def test_rejects_instagram(self):
        import vidqueue
        assert not vidqueue.is_tiktok("https://instagram.com/reel/abc")


# ---------------------------------------------------------------------------
# Task 3: TikTok extraction
# ---------------------------------------------------------------------------

class TestFetchTiktokMetadata:
    def test_parses_yt_dlp_json(self):
        import vidqueue
        fake = {"title": "Watch these essays", "description": "Must watch", "uploader": "creator"}
        with patch("vidqueue.subprocess.run", return_value=_completed(json.dumps(fake))):
            meta = vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/ZABCdef/")
        assert meta["title"] == "Watch these essays"
        assert meta["uploader"] == "creator"

    def test_returns_empty_on_error(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["yt-dlp"])):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/bad/") == {}

    def test_returns_empty_on_timeout(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.TimeoutExpired(["yt-dlp"], 30)):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/slow/") == {}

    def test_returns_empty_on_bad_json(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", return_value=_completed("not-json{")):
            assert vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/bad/") == {}

    def test_coerces_null_fields(self):
        import vidqueue
        with patch("vidqueue.subprocess.run", return_value=_completed(
            json.dumps({"title": None, "description": None, "uploader": None})
        )):
            meta = vidqueue.fetch_tiktok_metadata("https://vm.tiktok.com/null/")
        assert meta == {"title": "", "description": "", "uploader": ""}


class TestFetchTiktokVideoNonFatal:
    def test_returns_none_on_download_failure(self, tmp_path):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["yt-dlp"])):
            assert vidqueue.fetch_tiktok_video("https://vm.tiktok.com/bad/", str(tmp_path)) is None

    def test_returns_none_on_timeout(self, tmp_path):
        import vidqueue
        with patch("vidqueue.subprocess.run", side_effect=subprocess.TimeoutExpired(["yt-dlp"], 120)):
            assert vidqueue.fetch_tiktok_video("https://vm.tiktok.com/slow/", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Task 4: YouTube URL utilities
# ---------------------------------------------------------------------------

class TestExtractYoutubeUrls:
    def test_extracts_youtu_be(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("https://youtu.be/dQw4w9WgXcQ") == ["https://youtu.be/dQw4w9WgXcQ"]

    def test_extracts_full_url(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("https://www.youtube.com/watch?v=9bZkp7q19f0") == ["https://youtu.be/9bZkp7q19f0"]

    def test_extracts_multiple(self):
        import vidqueue
        text = "https://youtu.be/dQw4w9WgXcQ and https://www.youtube.com/watch?v=9bZkp7q19f0"
        assert len(vidqueue.extract_youtube_urls(text)) == 2

    def test_empty_for_no_urls(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("no links here") == []

    def test_ignores_non_youtube(self):
        import vidqueue
        assert vidqueue.extract_youtube_urls("https://tiktok.com/abc") == []


class TestGetVideoIdFromUrl:
    def test_youtu_be(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_long_form(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://www.youtube.com/watch?v=9bZkp7q19f0") == "9bZkp7q19f0"

    def test_none_for_non_youtube(self):
        import vidqueue
        assert vidqueue.get_video_id_from_url("https://tiktok.com/abc") is None


# ---------------------------------------------------------------------------
# Task 5: LLM video extraction
# ---------------------------------------------------------------------------

class TestParseVideoList:
    def test_parses_valid_array(self):
        import vidqueue
        raw = '[{"title": "Why Kubrick Matters", "channel": "Some Channel", "youtube_url": "https://youtu.be/abc123"}]'
        result = vidqueue._parse_video_list(raw)
        assert result[0]["title"] == "Why Kubrick Matters"
        assert result[0]["channel"] == "Some Channel"
        assert result[0]["youtube_url"] == "https://youtu.be/abc123"

    def test_strips_markdown_fences(self):
        import vidqueue
        raw = '```json\n[{"title": "Test", "channel": null, "youtube_url": null}]\n```'
        assert vidqueue._parse_video_list(raw)[0]["title"] == "Test"

    def test_empty_on_bad_json(self):
        import vidqueue
        assert vidqueue._parse_video_list("not json") == []

    def test_filters_empty_titles(self):
        import vidqueue
        raw = '[{"title": "", "channel": "X"}, {"title": "Real Video", "channel": null, "youtube_url": null}]'
        result = vidqueue._parse_video_list(raw)
        assert len(result) == 1
        assert result[0]["title"] == "Real Video"

    def test_null_channel_is_none(self):
        import vidqueue
        raw = '[{"title": "Essay", "channel": null, "youtube_url": null}]'
        assert vidqueue._parse_video_list(raw)[0]["channel"] is None


class TestLlmExtractVideos:
    def test_returns_parsed_list_on_success(self):
        import vidqueue
        raw = '[{"title": "Philosophy of Inception", "channel": "Nerdwriter1", "youtube_url": null}]'
        with patch("vidqueue.subprocess.run", return_value=_completed(raw)):
            result = vidqueue.llm_extract_videos("some text")
        assert result[0]["title"] == "Philosophy of Inception"

    def test_falls_back_to_gemini(self):
        import vidqueue
        raw = '[{"title": "Fallback Video", "channel": null, "youtube_url": null}]'
        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            return _completed(raw) if call_count > 1 else _completed("", returncode=1)

        with patch("vidqueue.subprocess.run", side_effect=fake_run):
            result = vidqueue.llm_extract_videos("text")
        assert result[0]["title"] == "Fallback Video"


# ---------------------------------------------------------------------------
# Task 6: YouTube OAuth client
# ---------------------------------------------------------------------------

class TestYoutubeAuth:
    def test_dies_when_no_token_and_no_credentials(self, tmp_path, monkeypatch):
        import vidqueue
        monkeypatch.setattr(vidqueue, "_token_path", lambda: tmp_path / "token.json")
        monkeypatch.setattr(vidqueue, "_credentials_path", lambda: tmp_path / "missing.json")
        with pytest.raises(SystemExit):
            vidqueue.get_youtube_client()

    def test_token_path_in_config_dir(self):
        import vidqueue
        assert ".config/vidqueue/youtube_token.json" in str(vidqueue._token_path())

    def test_credentials_path_in_repo_root(self):
        import vidqueue
        path = vidqueue._credentials_path()
        assert path.name == "credentials.json"
        assert "ai-agents" in str(path)


# ---------------------------------------------------------------------------
# Task 7: Playlist management
# ---------------------------------------------------------------------------

class TestGetOrCreatePlaylist:
    def test_returns_existing(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLabc123", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        assert vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations") == "PLabc123"
        mock_yt.playlists().insert.assert_not_called()

    def test_creates_when_missing(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {"items": []}
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLnew456"}
        assert vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations") == "PLnew456"

    def test_no_match_on_different_name(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLother", "snippet": {"title": "Other Playlist"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLcreated"}
        assert vidqueue.get_or_create_playlist(mock_yt, "TikTok Recommendations") == "PLcreated"


class TestGetPlaylistVideoIds:
    def test_returns_set(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().list().execute.return_value = {
            "items": [{"contentDetails": {"videoId": "abc"}}, {"contentDetails": {"videoId": "def"}}]
        }
        mock_yt.playlistItems().list_next.return_value = None
        assert vidqueue.get_playlist_video_ids(mock_yt, "PL1") == {"abc", "def"}

    def test_empty_playlist(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        assert vidqueue.get_playlist_video_ids(mock_yt, "PL1") == set()


# ---------------------------------------------------------------------------
# Task 8: YouTube search and insertion
# ---------------------------------------------------------------------------

class TestYoutubeSearch:
    def test_returns_video_id(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {"items": [{"id": {"videoId": "xyz789"}}]}
        assert vidqueue.youtube_search(mock_yt, "Why Kubrick Matters", "Channel") == "xyz789"

    def test_returns_none_on_empty(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {"items": []}
        assert vidqueue.youtube_search(mock_yt, "Obscure Video", None) is None

    def test_returns_none_on_exception(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.side_effect = Exception("quota")
        assert vidqueue.youtube_search(mock_yt, "Video", None) is None

    def test_includes_channel_in_query(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {"items": []}
        vidqueue.youtube_search(mock_yt, "Essay Title", "Nerdwriter1")
        assert "Nerdwriter1" in mock_yt.search().list.call_args[1]["q"]


class TestInsertVideo:
    def test_calls_api(self):
        import vidqueue
        mock_yt = MagicMock()
        vidqueue.insert_video(mock_yt, "PLabc", "vid999")
        body = mock_yt.playlistItems().insert.call_args[1]["body"]
        assert body["snippet"]["playlistId"] == "PLabc"
        assert body["snippet"]["resourceId"]["videoId"] == "vid999"
        assert body["snippet"]["resourceId"]["kind"] == "youtube#video"

    def test_dies_on_exception(self):
        import vidqueue
        mock_yt = MagicMock()
        mock_yt.playlistItems().insert().execute.side_effect = Exception("API error")
        with pytest.raises(SystemExit):
            vidqueue.insert_video(mock_yt, "PLabc", "vid999")


# ---------------------------------------------------------------------------
# Task 9: Main pipeline
# ---------------------------------------------------------------------------

class TestMainPipeline:
    def test_adds_video_from_caption_url(self, capsys, monkeypatch):
        import vidqueue
        monkeypatch.setattr(vidqueue, "load_config", lambda: {"YOUTUBE_PLAYLIST_NAME": "TikTok Recommendations"})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {
            "title": "Watch these!", "uploader": "creator",
            "description": "Great essay https://youtu.be/dQw4w9WgXcQ",
        })
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [])
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLtest", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)
        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/123"]
        vidqueue.main()
        out = capsys.readouterr().out
        assert "ADDED:dQw4w9WgXcQ:" in out
        assert "PLAYLIST:PLtest:" in out

    def test_skips_already_queued_video(self, capsys, monkeypatch):
        import vidqueue
        monkeypatch.setattr(vidqueue, "load_config", lambda: {})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {
            "title": "T", "uploader": "u",
            "description": "https://youtu.be/existingABC",
        })
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [])
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {
            "items": [{"id": "PLtest", "snippet": {"title": "TikTok Recommendations"}}]
        }
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlistItems().list().execute.return_value = {
            "items": [{"contentDetails": {"videoId": "existingABC"}}]
        }
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)
        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/456"]
        vidqueue.main()
        out = capsys.readouterr().out
        assert "SKIPPED:existingABC:" in out
        mock_yt.playlistItems().insert.assert_not_called()

    def test_unresolvable_title_marked_unresolved(self, capsys, monkeypatch):
        import vidqueue
        monkeypatch.setattr(vidqueue, "load_config", lambda: {})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_metadata", lambda url: {"title": "T", "uploader": "u", "description": ""})
        monkeypatch.setattr(vidqueue, "fetch_tiktok_video", lambda url, tmpdir: None)
        monkeypatch.setattr(vidqueue, "llm_extract_videos", lambda text, source_hint="": [
            {"title": "Obscure Essay", "channel": None, "youtube_url": None}
        ])
        monkeypatch.setattr(vidqueue, "youtube_search", lambda yt, title, channel: None)
        mock_yt = MagicMock()
        mock_yt.playlists().list().execute.return_value = {"items": []}
        mock_yt.playlists().list_next.return_value = None
        mock_yt.playlists().insert().execute.return_value = {"id": "PLnew"}
        mock_yt.playlistItems().list().execute.return_value = {"items": []}
        mock_yt.playlistItems().list_next.return_value = None
        monkeypatch.setattr(vidqueue, "get_youtube_client", lambda: mock_yt)
        sys.argv = ["vidqueue.py", "https://www.tiktok.com/@creator/video/789"]
        vidqueue.main()
        assert "UNRESOLVED:Obscure Essay" in capsys.readouterr().out
