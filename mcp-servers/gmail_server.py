#!/usr/bin/env python3
"""
Gmail MCP server (stdio transport).
Reads tokens from ~/.google_tokens.json, refreshes automatically.
"""

import base64
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TOKEN_FILE = os.path.expanduser("~/.google_tokens.json")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)


def get_access_token():
    tokens = load_tokens()
    obtained_at = tokens.get("obtained_at", 0)
    expires_in = tokens.get("expires_in", 3600)
    if time.time() > obtained_at + expires_in - 300:
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
        with urllib.request.urlopen(req) as resp:
            new_tokens = json.loads(resp.read())
            tokens["access_token"] = new_tokens["access_token"]
            tokens["expires_in"] = new_tokens.get("expires_in", 3600)
            tokens["obtained_at"] = time.time()
            save_tokens(tokens)
    return tokens["access_token"]


def gmail_request(method, path, body=None):
    token = get_access_token()
    url = f"https://gmail.googleapis.com/gmail/v1{path}"
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
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_profile():
    return gmail_request("GET", "/users/me/profile")


def send_email(to, subject, body, mime_type="text/html"):
    msg = MIMEMultipart("alternative")
    msg["From"] = to
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, mime_type.split("/")[-1]))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail_request("POST", "/users/me/messages/send", {"raw": raw})


def create_draft(to, subject, body, mime_type="text/html"):
    msg = MIMEMultipart("alternative")
    msg["From"] = to
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, mime_type.split("/")[-1]))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail_request("POST", "/users/me/drafts",
                         {"message": {"raw": raw}})


# MCP stdio protocol
def send_response(msg_id, result):
    response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def send_error(msg_id, code, message):
    response = {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "gmail_get_profile",
        "description": "Get the authenticated user's Gmail profile including email address.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "gmail_send",
        "description": "Send an email via Gmail.",
        "inputSchema": {
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Email body"},
                "mimeType": {"type": "string", "default": "text/html",
                             "description": "text/html or text/plain"},
            },
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Create a Gmail draft without sending.",
        "inputSchema": {
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "mimeType": {"type": "string", "default": "text/html"},
            },
        },
    },
]


def handle_call_tool(msg_id, name, arguments):
    try:
        if name == "gmail_get_profile":
            result = get_profile()
            send_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        elif name == "gmail_send":
            result = send_email(
                to=arguments["to"],
                subject=arguments["subject"],
                body=arguments["body"],
                mime_type=arguments.get("mimeType", "text/html"),
            )
            send_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        elif name == "gmail_create_draft":
            result = create_draft(
                to=arguments["to"],
                subject=arguments["subject"],
                body=arguments["body"],
                mime_type=arguments.get("mimeType", "text/html"),
            )
            send_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        else:
            send_error(msg_id, -32601, f"Unknown tool: {name}")
    except Exception as e:
        send_error(msg_id, -32000, str(e))


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            send_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gmail", "version": "1.0.0"},
            })
        elif method == "tools/list":
            send_response(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            handle_call_tool(msg_id, params.get("name"), params.get("arguments", {}))
        elif method == "notifications/initialized":
            pass
        else:
            if msg_id is not None:
                send_error(msg_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
