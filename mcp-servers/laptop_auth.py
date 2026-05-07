#!/usr/bin/env python3
"""
One-time Google OAuth browser flow for Gmail + Calendar.
Run this on your laptop (Windows/Mac) — it opens a browser, completes auth,
and saves tokens to ~/.google_tokens.json

Usage:
    python mcp-servers/laptop_auth.py

Then copy the token file to the server:
    pscp -i "C:\Users\ciano\Documents\yopflixKey.ppk" "%USERPROFILE%\.google_tokens.json" cian@37.187.226.57:/home/cian/.google_tokens.json
"""

import http.server
import json
import os
import time
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars first.")
    print("  Windows cmd: set GOOGLE_CLIENT_ID=your_client_id")
    print("               set GOOGLE_CLIENT_SECRET=your_client_secret")
    exit(1)
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
TOKEN_FILE = os.path.expanduser("~/.google_tokens.json")

auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px">
                <h2>&#10003; Authorised!</h2>
                <p>You can close this tab and return to the terminal.</p>
                </body></html>
            """)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter")

    def log_message(self, format, *args):
        pass  # suppress request logs


def exchange_code(code):
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
        return token


def main():
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token to be returned
    })
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

    print("Opening browser for Google authorisation...")
    print(f"\nIf browser does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    print("Waiting for authorisation callback on localhost:8080 ...")
    while auth_code is None:
        server.handle_request()

    print("\nExchanging code for tokens...")
    token = exchange_code(auth_code)

    if "refresh_token" not in token:
        print("ERROR: No refresh_token in response. Try revoking access at")
        print("https://myaccount.google.com/permissions and re-running this script.")
        return

    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"\nTokens saved to {TOKEN_FILE}")
    print("\nNow copy to your server:")
    print('  pscp -i "C:\\Users\\ciano\\Documents\\yopflixKey.ppk" "%USERPROFILE%\\.google_tokens.json" cian@37.187.226.57:/home/cian/.google_tokens.json')
    print("\nDone!")


if __name__ == "__main__":
    main()
