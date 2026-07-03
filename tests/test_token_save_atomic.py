"""Atomicity tests for the Google OAuth token writers used by Gmail/Calendar.

Regression: agents.gmail_client (used by gmail_server) and calendar_server
both refresh the shared ~/.google_tokens.json. When a daily/news briefing
spawns both MCP servers at once, their concurrent (and previously
non-atomic) writes interleaved and corrupted the file, leaving "No
refresh_token found" and silently killing the briefing emails. save_tokens()
must write atomically so a crash or a racing writer can never leave a
truncated / half-written file.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from agents import gmail_client

MCP_DIR = Path(__file__).resolve().parent.parent / "mcp-servers"
VALID_TOKENS = {
    "access_token": "ya29.access",
    "refresh_token": "1//refresh",
    "expires_in": 3600,
    "obtained_at": 1000.0,
    "scope": "scope",
    "token_type": "Bearer",
}


def _load_module(name):
    spec = importlib.util.spec_from_file_location(name, MCP_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["gmail_client", "calendar_server"])
def server(request, tmp_path, monkeypatch):
    # gmail's token load/save now lives in agents.gmail_client (extracted from
    # gmail_server.py); calendar_server still owns its copy directly.
    module = gmail_client if request.param == "gmail_client" else _load_module(request.param)
    token_file = tmp_path / ".google_tokens.json"
    token_file.write_text(json.dumps(VALID_TOKENS))
    monkeypatch.setattr(module, "TOKEN_FILE", str(token_file))
    return module, token_file


def test_save_tokens_roundtrips(server):
    module, token_file = server
    new = dict(VALID_TOKENS, access_token="ya29.new", obtained_at=2000.0)
    module.save_tokens(new)
    assert json.loads(token_file.read_text()) == new


def test_save_tokens_sets_owner_only_perms(server):
    module, token_file = server
    module.save_tokens(VALID_TOKENS)
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_crash_mid_write_preserves_existing_file(server, monkeypatch):
    """A failure while serialising must leave the prior valid token intact."""
    module, token_file = server

    def boom(*_args, **_kwargs):
        raise RuntimeError("serialisation blew up")

    monkeypatch.setattr(module.json, "dump", boom)
    with pytest.raises(RuntimeError):
        module.save_tokens(dict(VALID_TOKENS, access_token="ya29.doomed"))

    # File must still be the original, complete, refresh_token-bearing token.
    recovered = json.loads(token_file.read_text())
    assert recovered == VALID_TOKENS


def test_no_temp_files_left_behind(server, monkeypatch):
    module, token_file = server

    def boom(*_args, **_kwargs):
        raise RuntimeError("serialisation blew up")

    monkeypatch.setattr(module.json, "dump", boom)
    with pytest.raises(RuntimeError):
        module.save_tokens(VALID_TOKENS)

    leftovers = [p.name for p in token_file.parent.iterdir() if p.name != token_file.name]
    assert leftovers == []
