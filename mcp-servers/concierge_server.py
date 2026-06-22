#!/usr/bin/env python3
"""
Server-concierge MCP server (stdio transport).

Exposes the Telegram concierge bot's tools (plant tracker, agent status,
yopflix/system health, travel agent, recipe saver) to the `claude` CLI so it
can drive them natively during chat. Tool definitions come from the shared
canonical source `telegram-bot/tool_specs.py` — add tools there, not here.

Started by the bot via: claude --mcp-config telegram-bot/concierge_mcp.json
"""
import json
import sys
from pathlib import Path

# tool_specs and tools both live in telegram-bot/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telegram-bot"))

from tool_specs import mcp_tools, func_map

TOOLS = mcp_tools()
FUNCS = func_map()


def send_response(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def send_error(msg_id, code, message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                 "error": {"code": code, "message": message}}) + "\n")
    sys.stdout.flush()


def handle_call_tool(msg_id, name, arguments):
    fn = FUNCS.get(name)
    if fn is None:
        send_error(msg_id, -32601, f"Unknown tool: {name}")
        return
    try:
        result = fn(**(arguments or {}))
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        send_response(msg_id, {"content": [{"type": "text", "text": text}]})
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
            # Per JSON-RPC 2.0, a malformed request gets a Parse error with a null
            # id rather than being silently dropped (which would hang the client).
            send_error(None, -32700, "Parse error")
            continue

        msg_id = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            send_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "concierge", "version": "1.0.0"},
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
