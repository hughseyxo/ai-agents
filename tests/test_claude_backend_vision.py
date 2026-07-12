import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

import claude_backend as cb


# --- detect_usage_limit ---


def test_detect_usage_limit_matches_known_phrases():
    assert cb.detect_usage_limit("", "Error: usage limit reached, resets in 2h") is True
    assert cb.detect_usage_limit("some output mentioning rate limit exceeded", "") is True
    assert cb.detect_usage_limit("", "HTTP 429 Too Many Requests") is True


def test_detect_usage_limit_false_on_unrelated_failure():
    assert cb.detect_usage_limit("", "Error: could not read file, no such file or directory") is False
    assert cb.detect_usage_limit("", "") is False


# --- assess_image with extra_image_paths ---


def _fake_completed_process(stdout: str, returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def test_assess_image_without_extra_paths_uses_single_add_dir():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process(json.dumps({"result": "ok"}))
        result = cb.assess_image("/tmp/plant-abc/image.jpg", "system", "user text")

    assert result == "ok"
    cmd = mock_run.call_args.args[0]
    assert cmd.count("--add-dir") == 1
    assert "/tmp/plant-abc" in cmd


def test_assess_image_with_extra_paths_adds_each_dir_and_lists_them_in_prompt():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process(json.dumps({"result": "trend ok"}))
        result = cb.assess_image(
            "/tmp/new/image.jpg", "system", "user text",
            extra_image_paths=["/tmp/old1/a.jpg", "/tmp/old2/b.jpg"],
        )

    assert result == "trend ok"
    cmd = mock_run.call_args.args[0]
    assert cmd.count("--add-dir") == 3
    assert "/tmp/new" in cmd and "/tmp/old1" in cmd and "/tmp/old2" in cmd
    prompt = mock_run.call_args.kwargs["input"]
    assert "/tmp/old1/a.jpg" in prompt
    assert "/tmp/old2/b.jpg" in prompt


def test_assess_image_dedupes_add_dir_for_paths_sharing_a_directory():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process(json.dumps({"result": "ok"}))
        cb.assess_image(
            "/tmp/shared/new.jpg", "system", "user text",
            extra_image_paths=["/tmp/shared/old.jpg"],
        )

    cmd = mock_run.call_args.args[0]
    assert cmd.count("--add-dir") == 1


# --- identify_and_assess ---


def test_identify_and_assess_returns_parsed_dict_on_success():
    payload = {
        "matched_plant": "Monstera Deliciosa", "confidence": "high",
        "status": "Healthy", "summary": "Looking good",
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process(json.dumps({"result": json.dumps(payload)}))
        parsed, usage_limit_hit = cb.identify_and_assess("/tmp/x/photo.jpg", "system prompt", "user text")

    assert parsed["matched_plant"] == "Monstera Deliciosa"
    assert parsed["confidence"] == "high"
    assert parsed["status"] == "Healthy"
    assert usage_limit_hit is False


def test_identify_and_assess_returns_none_on_cli_failure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process("", returncode=1)
        parsed, usage_limit_hit = cb.identify_and_assess("/tmp/x/photo.jpg", "system prompt", "user text")

    assert parsed is None
    assert usage_limit_hit is False


def test_identify_and_assess_returns_none_on_unparseable_result():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process(json.dumps({"result": "not json at all, no braces"}))
        parsed, usage_limit_hit = cb.identify_and_assess("/tmp/x/photo.jpg", "system prompt", "user text")

    assert parsed is None
    assert usage_limit_hit is False


def test_identify_and_assess_flags_usage_limit_on_failure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed_process("", returncode=1)
        mock_run.return_value.stderr = "Error: usage limit reached, resets in 45m"
        parsed, usage_limit_hit = cb.identify_and_assess("/tmp/x/photo.jpg", "system prompt", "user text")

    assert parsed is None
    assert usage_limit_hit is True
