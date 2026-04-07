#!/usr/bin/env python3
"""
Google Calendar MCP server (stdio transport).
Reads tokens from ~/.google_tokens.json, refreshes automatically.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

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
    # Refresh if within 5 minutes of expiry
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


def gcal_request(path, params=None):
    token = get_access_token()
    url = f"https://www.googleapis.com/calendar/v3{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def list_events(calendar_id="primary", time_min=None, time_max=None,
                max_results=50, time_zone="Europe/Dublin"):
    params = {
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    if time_zone:
        params["timeZone"] = time_zone
    return gcal_request(f"/calendars/{urllib.parse.quote(calendar_id)}/events", params)


def list_calendars():
    return gcal_request("/users/me/calendarList")


# MCP stdio protocol
def send_response(msg_id, result):
    response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    line = json.dumps(response) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def send_error(msg_id, code, message):
    response = {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}
    line = json.dumps(response) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


TOOLS = [
    {
        "name": "gcal_list_events",
        "description": "List Google Calendar events within a time range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendarId": {"type": "string", "default": "primary"},
                "timeMin": {"type": "string", "description": "RFC3339 start time e.g. 2026-04-07T00:00:00"},
                "timeMax": {"type": "string", "description": "RFC3339 end time e.g. 2026-04-07T23:59:59"},
                "maxResults": {"type": "integer", "default": 50},
                "timeZone": {"type": "string", "default": "Europe/Dublin"},
            },
        },
    },
    {
        "name": "gcal_list_calendars",
        "description": "List all calendars the user has access to.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle_call_tool(msg_id, name, arguments):
    try:
        if name == "gcal_list_events":
            result = list_events(
                calendar_id=arguments.get("calendarId", "primary"),
                time_min=arguments.get("timeMin"),
                time_max=arguments.get("timeMax"),
                max_results=arguments.get("maxResults", 50),
                time_zone=arguments.get("timeZone", "Europe/Dublin"),
            )
            send_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        elif name == "gcal_list_calendars":
            result = list_calendars()
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
                "serverInfo": {"name": "google-calendar", "version": "1.0.0"},
            })
        elif method == "tools/list":
            send_response(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            handle_call_tool(msg_id, params.get("name"), params.get("arguments", {}))
        elif method == "notifications/initialized":
            pass  # no response needed
        else:
            if msg_id is not None:
                send_error(msg_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
