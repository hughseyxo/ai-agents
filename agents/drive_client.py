#!/usr/bin/env python3
"""Google Drive REST client — token load/refresh/save plus find-or-create +
simple upload, used to push the plant health digest to Drive.

Uses its own token file (TOKEN_FILE) separate from Gmail/Calendar's shared
~/.google_tokens.json, so granting the drive.file scope (via
scripts/setup_drive_oauth.py) can never disturb those already-working tokens.
"""

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

TOKEN_FILE = os.path.expanduser("~/.google_tokens_drive.json")
REQUEST_TIMEOUT = 60
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3/files"


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_tokens(tokens):
    # Atomic write, mirroring agents/gmail_client.py — a crash must never leave
    # a truncated token file.
    directory = os.path.dirname(TOKEN_FILE) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".google_tokens_drive.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_access_token():
    tokens = load_tokens()
    obtained_at = tokens.get("obtained_at", 0)
    expires_in = tokens.get("expires_in", 3600)
    if time.time() > obtained_at + expires_in - 300:
        if "refresh_token" not in tokens:
            raise RuntimeError(
                "No refresh_token in drive token file; re-run scripts/setup_drive_oauth.py."
            )
        data = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                new_tokens = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200] if hasattr(e, "read") else ""
            raise RuntimeError(f"Drive OAuth token refresh failed (HTTP {e.code}): {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Drive OAuth token refresh failed (network): {e.reason}")
        if "access_token" not in new_tokens:
            raise RuntimeError(f"Drive OAuth refresh response missing access_token: {new_tokens}")
        tokens["access_token"] = new_tokens["access_token"]
        tokens["expires_in"] = new_tokens.get("expires_in", 3600)
        tokens["obtained_at"] = time.time()
        save_tokens(tokens)
    return tokens["access_token"]


def _drive_request(method, path, params=None, body=None):
    token = get_access_token()
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_folder(name: str) -> str:
    """Return the id of a Drive folder with this name (visible to this app's
    drive.file scope), creating it if it doesn't exist yet."""
    safe_name = _escape_query_value(name)
    query = f"name = '{safe_name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    result = _drive_request("GET", "/files", params={"q": query, "fields": "files(id,name)"})
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    created = _drive_request("POST", "/files", body={"name": name, "mimeType": FOLDER_MIME})
    return created["id"]


def _find_file_in_folder(name: str, folder_id: str) -> str | None:
    safe_name = _escape_query_value(name)
    query = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
    result = _drive_request("GET", "/files", params={"q": query, "fields": "files(id,name)"})
    files = result.get("files", [])
    return files[0]["id"] if files else None


def upload_or_update_file(folder_id: str, filename: str, content: str,
                          mime_type: str = "text/markdown") -> str:
    """Create the file in folder_id if absent, otherwise overwrite its content
    in place. Returns the file id."""
    file_id = _find_file_in_folder(filename, folder_id)
    data = content.encode("utf-8")
    token = get_access_token()

    if file_id:
        req = urllib.request.Request(
            f"{UPLOAD_BASE}/{file_id}?uploadType=media",
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": mime_type},
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())["id"]

    # Random per-request boundary — content is LLM-authored digest text and
    # must never be able to collide with (and truncate) a fixed boundary.
    boundary = f"plant-digest-{uuid.uuid4().hex}"
    metadata = json.dumps({"name": filename, "parents": [folder_id]}).encode("utf-8")
    multipart = b"".join([
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        metadata,
        f"\r\n--{boundary}\r\n".encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--".encode(),
    ])
    req = urllib.request.Request(
        f"{UPLOAD_BASE}?uploadType=multipart",
        data=multipart,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())["id"]


def upload_digest(content: str, folder_name: str = "Plant Health Digest",
                  filename: str = "Plant Health Digest.md") -> str:
    """Push digest content to Drive: creates the folder/file on first run,
    overwrites content in place on every later call. Returns the file id."""
    folder_id = find_or_create_folder(folder_name)
    return upload_or_update_file(folder_id, filename, content)
