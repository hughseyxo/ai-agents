#!/usr/bin/env python3
"""
Gmail REST client — token load/refresh/save, low-level request helper, and
send/draft/profile operations. Extracted from mcp-servers/gmail_server.py so
that both the Gmail MCP server and agents can send email directly from
Python without spending an LLM call on it.
"""

import base64
import json
import os
import tempfile
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser

TOKEN_FILE = os.path.expanduser("~/.google_tokens.json")
# A hung Gmail/OAuth call must not stall an hourly cron run indefinitely.
REQUEST_TIMEOUT = 60
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_tokens(tokens):
    # Atomic write: a crash or a racing MCP server must never leave a
    # truncated / half-written token file (see tests/test_token_save_atomic.py).
    directory = os.path.dirname(TOKEN_FILE) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".google_tokens.", suffix=".tmp")
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
            raise RuntimeError("No refresh_token in token file; re-authentication required.")
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
            raise RuntimeError(f"OAuth token refresh failed (HTTP {e.code}): {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"OAuth token refresh failed (network): {e.reason}")
        if "access_token" not in new_tokens:
            raise RuntimeError(f"OAuth refresh response missing access_token: {new_tokens}")
        tokens["access_token"] = new_tokens["access_token"]
        tokens["expires_in"] = new_tokens.get("expires_in", 3600)
        tokens["obtained_at"] = time.time()
        save_tokens(tokens)
    return tokens["access_token"]


# Cache the authenticated user's address so every send doesn't hit the profile API.
_SENDER_ADDRESS = None


def _get_sender_address():
    """Return the authenticated Gmail address (for the From header). Falls back to
    None on any failure — Gmail still sends as the authenticated user."""
    global _SENDER_ADDRESS
    if _SENDER_ADDRESS is None:
        try:
            _SENDER_ADDRESS = get_profile().get("emailAddress")
        except Exception:
            _SENDER_ADDRESS = None
    return _SENDER_ADDRESS


def gmail_request(method, path, params=None, body=None):
    token = get_access_token()
    url = f"https://gmail.googleapis.com/gmail/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = json.dumps(body).encode() if body else None
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


def get_profile():
    return gmail_request("GET", "/users/me/profile")


def send_email(to, subject, body, mime_type="text/html"):
    msg = MIMEMultipart("alternative")
    sender = _get_sender_address()
    if sender:
        msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, mime_type.split("/")[-1]))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail_request("POST", "/users/me/messages/send", body={"raw": raw})


def create_draft(to, subject, body, mime_type="text/html"):
    msg = MIMEMultipart("alternative")
    sender = _get_sender_address()
    if sender:
        msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, mime_type.split("/")[-1]))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail_request("POST", "/users/me/drafts",
                         body={"message": {"raw": raw}})


# --- read helpers (job-hunt application tracking) ---

class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html):
    parser = _HTMLText()
    try:
        parser.feed(html)
    except Exception:
        pass
    return " ".join(parser.parts)


def search_messages(q, max_results=200):
    """Message stubs ({id, threadId}) matching a Gmail search query, newest first."""
    out, page_token = [], None
    while len(out) < max_results:
        params = {"q": q, "maxResults": min(100, max_results - len(out))}
        if page_token:
            params["pageToken"] = page_token
        resp = gmail_request("GET", "/users/me/messages", params=params)
        out.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out[:max_results]


def get_message(message_id):
    return gmail_request("GET", f"/users/me/messages/{message_id}", params={"format": "full"})


def _decode_part(part):
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_body(payload, mime):
    if payload.get("mimeType") == mime:
        text = _decode_part(payload)
        if text:
            return text
    for part in payload.get("parts") or []:
        text = _find_body(part, mime)
        if text:
            return text
    return ""


def extract_message(msg, body_cap=1500):
    """Flatten a format=full message into {id, thread_id, subject, sender,
    date (ISO, UTC), body}. Body prefers text/plain, else stripped HTML, else
    the snippet; whitespace-collapsed and capped."""
    payload = msg.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    body = _find_body(payload, "text/plain")
    if not body:
        html = _find_body(payload, "text/html")
        body = _html_to_text(html) if html else msg.get("snippet", "")
    date = ""
    if msg.get("internalDate"):
        date = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": headers.get("subject", ""),
        "sender": headers.get("from", ""),
        "date": date,
        "body": " ".join(body.split())[:body_cap],
    }
