#!/usr/bin/env python3
"""
Gmail MCP server (stdio transport).
Reads tokens from ~/.google_tokens.json, refreshes automatically.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.gmail_client import (  # noqa: E402
    load_tokens, save_tokens, get_access_token, gmail_request,
    get_profile, send_email, create_draft, _get_sender_address,
)


def gmail_list_messages(query=None, max_results=10):
    params = {"maxResults": max_results}
    if query:
        params["q"] = query
    return gmail_request("GET", "/users/me/messages", params=params)


def gmail_get_message(message_id):
    return gmail_request("GET", f"/users/me/messages/{message_id}")


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
        "description": "Create a draft email in Gmail (does not send).",
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
        "name": "gmail_list_messages",
        "description": "List Gmail messages matching a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g. 'subject:News')"},
                "maxResults": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "gmail_get_message",
        "description": "Get a specific Gmail message by ID.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
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
        elif name == "gmail_list_messages":
            result = gmail_list_messages(
                query=arguments.get("query"),
                max_results=arguments.get("maxResults", 10),
            )
            send_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        elif name == "gmail_get_message":
            result = gmail_get_message(message_id=arguments["id"])
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
