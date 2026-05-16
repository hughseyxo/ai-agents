#!/usr/bin/env bash
# check-yt-auth.sh — Check YouTube auth status (cookies + PO token provider)
# Run manually or via cron: 0 9 * * 1 /home/cian/git/ai-agents/skills/mealsave/check-yt-auth.sh
#
# Exits non-zero if critical auth is missing/expired.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COOKIES=~/.config/mealsave/youtube-cookies.txt
WARN_DAYS=30
VENV_PYTHON="$BASE_DIR/.venv/bin/python"
POT_PROVIDER_DIR="$BASE_DIR/pot-provider/server"

echo "--- YouTube Auth Check ---"

# 1. Check Python Plugin
if [ -f "$VENV_PYTHON" ]; then
    if ! "$VENV_PYTHON" -c "import yt_dlp_plugins.extractor.getpot_bgutil" 2>/dev/null; then
        echo "WARNING: bgutil-ytdlp-pot-provider plugin not installed in venv."
        echo "  Run: $VENV_PYTHON -m pip install bgutil-ytdlp-pot-provider"
    else
        echo "OK: Python PO token plugin installed."
    fi
else
    echo "WARNING: Virtual environment not found at $VENV_PYTHON"
fi

# 2. Check Node Helper
if [ -d "$POT_PROVIDER_DIR" ]; then
    if [ ! -f "$POT_PROVIDER_DIR/build/generate_once.js" ]; then
        echo "WARNING: PO token Node helper not built."
        echo "  Run: cd $POT_PROVIDER_DIR && npm ci && npx tsc"
    else
        echo "OK: Node helper built."
    fi
else
    echo "WARNING: PO token provider repo not found at $POT_PROVIDER_DIR"
fi

# 3. Check Cookies
if [ ! -f "$COOKIES" ]; then
    echo "INFO: No YouTube cookies file at $COOKIES"
    echo "      This is okay if PO token provider works for most videos."
    exit 0
fi

"$VENV_PYTHON" - "$COOKIES" "$WARN_DAYS" << 'EOF'
import sys, time

cookies_file = sys.argv[1]
warn_days = int(sys.argv[2])

now = time.time()
warn_threshold = warn_days * 86400
soonest = None

try:
    with open(cookies_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            try:
                exp = int(parts[4])
            except ValueError:
                continue
            if exp == 0:
                continue
            if soonest is None or exp < soonest:
                soonest = exp
except FileNotFoundError:
    sys.exit(0)

if soonest is None:
    print("WARNING: No expiring cookies found in file — may be invalid.")
    sys.exit(1)

remaining = soonest - now
days_left = int(remaining / 86400)

if remaining <= 0:
    print(f"ERROR: YouTube cookies EXPIRED. Re-export from browser and scp to {cookies_file}")
    sys.exit(2)
elif remaining < warn_threshold:
    print(f"WARNING: YouTube cookies expire in {days_left} days. Re-export soon.")
    print(f"  1. Go to youtube.com (logged in)")
    print(f"  2. Use 'Get cookies.txt LOCALLY' extension → Export")
    print(f"  3. scp youtube-cookies.txt $(hostname):{cookies_file}")
    sys.exit(1)
else:
    print(f"OK: YouTube cookies valid for {days_left} more days.")
EOF
