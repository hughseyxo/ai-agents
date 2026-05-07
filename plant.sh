#!/usr/bin/env bash
set -euo pipefail

DB_PATH="/home/cian/git/ai-agents/data/agents.db"

usage() {
  echo "Usage:"
  echo "  plant.sh add \"Name\" [frequency_days]   # default 7 days"
  echo "  plant.sh list                           # show all plants + next water date"
  echo "  plant.sh remove \"Name\"                  # remove plant (and close open Todoist task)"
  exit 1
}

# All DB access via Python — sqlite3 CLI not available on this system
_plant_cmd() {
  python3 - "$@" <<'PYEOF'
import json, sqlite3, sys
from datetime import datetime, timedelta

DB = "/home/cian/git/ai-agents/data/agents.db"
AGENT = "daily-briefing"
KEY = "plants"

def get_db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS state (
        agent TEXT NOT NULL, key TEXT NOT NULL, value TEXT,
        updated TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (agent, key))""")
    return conn

def load_plants(conn):
    row = conn.execute("SELECT value FROM state WHERE agent=? AND key=?", (AGENT, KEY)).fetchone()
    return json.loads(row[0]) if row else []

def save_plants(conn, plants):
    conn.execute(
        "INSERT INTO state (agent, key, value, updated) VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(agent, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (AGENT, KEY, json.dumps(plants)))
    conn.commit()

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "list":
    conn = get_db()
    plants = load_plants(conn)
    if not plants:
        print("No plants tracked.")
        sys.exit(0)
    print(f"{'PLANT':<25} {'FREQ':<10} {'LAST WATERED':<14} {'NEXT WATER':<14}")
    print(f"{'-----':<25} {'----':<10} {'------------':<14} {'----------':<14}")
    for p in plants:
        last = datetime.strptime(p["last_watered"], "%Y-%m-%d")
        nxt = last + timedelta(days=p["frequency_days"])
        print(f"{p['name']:<25} {p['frequency_days']}d{'':<8} {p['last_watered']:<14} {nxt.strftime('%Y-%m-%d'):<14}")

elif cmd == "add":
    name = sys.argv[2] if len(sys.argv) > 2 else None
    freq = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    if not name:
        print("Plant name required.", file=sys.stderr)
        sys.exit(1)
    conn = get_db()
    plants = load_plants(conn)
    if any(p["name"] == name for p in plants):
        print(f'Plant "{name}" already exists.')
        sys.exit(1)
    today = datetime.now().strftime("%Y-%m-%d")
    plants.append({"name": name, "frequency_days": freq, "last_watered": today})
    save_plants(conn, plants)
    print(f'Added "{name}" (every {freq} days, last watered {today})')

elif cmd == "remove":
    name = sys.argv[2] if len(sys.argv) > 2 else None
    if not name:
        print("Plant name required.", file=sys.stderr)
        sys.exit(1)
    conn = get_db()
    plants = load_plants(conn)
    if not any(p["name"] == name for p in plants):
        print(f'Plant "{name}" not found.')
        sys.exit(1)
    plants = [p for p in plants if p["name"] != name]
    save_plants(conn, plants)
    print(f'Removed "{name}"')
else:
    sys.exit(1)
PYEOF
}

cmd_remove_todoist() {
  local name="$1"
  if [[ -n "${TODOIST_API_TOKEN:-}" ]]; then
    local task_name="Water $name"
    local task_id
    task_id=$(curl -s -H "Authorization: Bearer $TODOIST_API_TOKEN" \
      "https://api.todoist.com/api/v1/tasks?project_id=6Crf3cH2RF5v86wc" | \
      python3 -c "import json,sys; data=json.load(sys.stdin); tasks=[t for t in data.get('results',[]) if t['content']=='$task_name' and not t['checked']]; print(tasks[0]['id'] if tasks else '')" 2>/dev/null)

    if [[ -n "$task_id" ]]; then
      curl -s -X POST "https://api.todoist.com/api/v1/tasks/$task_id/close" \
        -H "Authorization: Bearer $TODOIST_API_TOKEN" > /dev/null
      echo "Closed Todoist task \"$task_name\""
    fi
  else
    echo "Note: TODOIST_API_TOKEN not set — check Todoist manually for open \"Water $name\" task"
  fi
}

[[ $# -lt 1 ]] && usage

case "$1" in
  add)    _plant_cmd "$@" ;;
  list)   _plant_cmd "$@" ;;
  remove)
    _plant_cmd "$@"
    cmd_remove_todoist "${2:?Plant name required}"
    ;;
  *)      usage ;;
esac
