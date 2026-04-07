#!/usr/bin/env python3
"""
One-time Google OAuth device flow authentication.
Run this once on the server to generate tokens for Calendar and Gmail.
Tokens are saved to ~/.google_tokens.json
"""

import json
import os
import time
import urllib.request
import urllib.parse

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOKEN_FILE = os.path.expanduser("~/.google_tokens.json")


def request_device_code():
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/device/code",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        raise RuntimeError(f"Device code request failed {e.code}: {body}")


def poll_for_token(device_code, interval):
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode()
    while True:
        time.sleep(interval)
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                token = json.loads(resp.read())
                token["obtained_at"] = time.time()
                return token
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
            error = body.get("error")
            if error == "authorization_pending":
                print(".", end="", flush=True)
                continue
            elif error == "slow_down":
                interval += 5
                continue
            else:
                raise RuntimeError(f"Auth failed: {body}")


def main():
    print("Requesting device code from Google...")
    device = request_device_code()

    print(f"\n{'='*60}")
    print(f"Visit this URL on any device:")
    print(f"  {device['verification_url']}")
    print(f"\nEnter this code: {device['user_code']}")
    print(f"{'='*60}\n")
    print("Waiting for authorization", end="", flush=True)

    token = poll_for_token(device["device_code"], device["interval"])

    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"\n\nTokens saved to {TOKEN_FILE}")
    print("You're authenticated! Run the MCP servers now.")


if __name__ == "__main__":
    main()
