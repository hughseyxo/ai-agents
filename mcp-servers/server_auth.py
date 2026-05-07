#!/usr/bin/env python3
"""
Headless Google OAuth flow - no browser needed on the server.
1. Run this script
2. Open the printed URL on your phone
3. Sign in, Google redirects to localhost:8080/callback?code=XXX (will show error — that's fine)
4. Copy the full URL from your browser address bar and paste it here
"""

import json
import os
import time
import urllib.parse
import urllib.request

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars first.")
    exit(1)

REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
TOKEN_FILE = os.path.expanduser("~/.google_tokens.json")


def main():
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

    print("\n=== Google OAuth — open this URL on your phone ===\n")
    print(auth_url)
    print("\n==================================================")
    print("\nAfter signing in, your browser will show a 'connection refused' error.")
    print("That's expected. Copy the FULL URL from the address bar and paste it below.\n")

    pasted = input("Paste the redirect URL here: ").strip()

    parsed = urllib.parse.urlparse(pasted)
    params = urllib.parse.parse_qs(parsed.query)

    if "code" not in params:
        print("ERROR: No code found in the URL. Make sure you copied the full URL.")
        exit(1)

    code = params["code"][0]
    print("\nExchanging code for tokens...")

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())
        token["obtained_at"] = time.time()

    if "refresh_token" not in token:
        print("ERROR: No refresh_token in response.")
        print("Try revoking access at https://myaccount.google.com/permissions and re-running.")
        exit(1)

    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"\nDone! Tokens saved to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
