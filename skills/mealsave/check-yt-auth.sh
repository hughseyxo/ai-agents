#!/usr/bin/env bash
# check-yt-auth.sh — Check YouTube cookie expiry for mealsave
# Run manually or via cron: 0 9 * * 1 ~/.claude/skills/mealsave/check-yt-auth.sh
#
# Outputs nothing if cookies are fine (>30 days).
# Prints a warning if expiring soon (<30 days).
# Exits non-zero if expired.

set -euo pipefail

COOKIES=~/.config/mealsave/youtube-cookies.txt
WARN_DAYS=30

if [ ! -f "$COOKIES" ]; then
    echo "INFO: No YouTube cookies file at $COOKIES — YouTube URLs won't work until set up."
    exit 0
fi

python3 - "$COOKIES" "$WARN_DAYS" << 'EOF'
import sys, time

cookies_file = sys.argv[1]
warn_days = int(sys.argv[2])

now = time.time()
warn_threshold = warn_days * 86400
soonest = None

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
