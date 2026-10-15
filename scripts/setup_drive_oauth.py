#!/usr/bin/env python3
"""One-time interactive OAuth consent for Google Drive (drive.file scope).

Writes a token file separate from Gmail/Calendar's ~/.google_tokens.json (see
agents/drive_client.py) so this consent can never disturb those working tokens.
Reuses the existing OAuth client (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in .env,
redirect_uri http://localhost, same as Gmail/Calendar).

Usage: python3 scripts/setup_drive_oauth.py
"""

import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.drive_client import CLIENT_ID, CLIENT_SECRET, SCOPE, TOKEN_FILE, save_tokens

REDIRECT_URI = "http://localhost"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _print_qr(data: str) -> None:
    """Render a scannable QR code to the terminal. Falls back to a note if the
    `qrcode` package isn't installed — the printed URL still works standalone."""
    try:
        import qrcode
    except ImportError:
        print("   (install `qrcode` for a scannable code: pip install qrcode)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set (check .env).", file=sys.stderr)
        sys.exit(1)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("1. Scan this QR code with your phone camera (avoids copy/paste-corrupting")
    print("   a 200+ char URL over a terminal), or open the URL below manually:\n")
    _print_qr(auth_url)
    print(f"\n   {auth_url}\n")
    print("2. Sign in and approve access. Your browser will redirect to a localhost")
    print("   URL that fails to load — that's expected. Copy the 'code' query")
    print("   parameter from that URL.\n")
    code = input("Paste the code here: ").strip()
    if not code:
        print("ERROR: no code entered.", file=sys.stderr)
        sys.exit(1)

    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"ERROR: token exchange failed (HTTP {e.code}): {detail}", file=sys.stderr)
        sys.exit(1)

    if "refresh_token" not in tokens:
        print("ERROR: response had no refresh_token — retry with prompt=consent "
              "(already set) after revoking prior access at "
              "https://myaccount.google.com/permissions", file=sys.stderr)
        sys.exit(1)

    tokens["obtained_at"] = time.time()
    save_tokens(tokens)
    print(f"\nSaved Drive token to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
