#!/usr/bin/env bash
# Pre-flight check for Google OAuth tokens.
# Sources .env for client credentials, attempts a token refresh,
# and exits non-zero if the refresh fails.
#
# Usage: source this script from run-*-briefing.sh before launching claude.
#   source scripts/check-google-token.sh || exit 1

set -euo pipefail

TOKEN_FILE="$HOME/.google_tokens.json"

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Token file not found at $TOKEN_FILE"
    echo "  Run: python3 mcp-servers/server_auth.py to create it"
    exit 1
fi

# Read current tokens
REFRESH_TOKEN=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE'))['refresh_token'])" 2>/dev/null)

if [[ -z "$REFRESH_TOKEN" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: No refresh_token found in $TOKEN_FILE"
    exit 1
fi

# Attempt token refresh
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST https://oauth2.googleapis.com/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=${GOOGLE_CLIENT_ID}" \
    -d "client_secret=${GOOGLE_CLIENT_SECRET}" \
    -d "refresh_token=${REFRESH_TOKEN}" \
    -d "grant_type=refresh_token" 2>/dev/null)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" != "200" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Google OAuth token refresh failed (HTTP $HTTP_CODE)"
    echo "  Response: $BODY"
    echo "  To fix: python3 mcp-servers/server_auth.py"
    exit 1
fi

# Update token file with fresh access token (atomic write — see mcp-servers/gmail_server.py)
echo "$BODY" | python3 -c "
import json, sys, time, os, tempfile
token_file = '$TOKEN_FILE'
with open(token_file) as f:
    tokens = json.load(f)
resp = json.load(sys.stdin)
tokens['access_token'] = resp['access_token']
tokens['expires_in'] = resp.get('expires_in', 3600)
tokens['obtained_at'] = time.time()
directory = os.path.dirname(os.path.abspath(token_file))
fd, tmp = tempfile.mkstemp(dir=directory, prefix='.google_tokens.', suffix='.tmp')
try:
    with os.fdopen(fd, 'w') as f:
        json.dump(tokens, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, token_file)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Google OAuth token refreshed successfully"
