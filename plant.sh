#!/usr/bin/env bash
set -euo pipefail

PLANTS_FILE="$HOME/plants.json"

usage() {
  echo "Usage:"
  echo "  plant.sh add \"Name\" [frequency_days]   # default 7 days"
  echo "  plant.sh list                           # show all plants + next water date"
  echo "  plant.sh remove \"Name\"                  # remove plant (and delete open Todoist task)"
  exit 1
}

ensure_file() {
  if [[ ! -f "$PLANTS_FILE" ]]; then
    echo "[]" > "$PLANTS_FILE"
  fi
}

cmd_add() {
  local name="${1:?Plant name required}"
  local freq="${2:-7}"
  local today
  today=$(date +%Y-%m-%d)

  ensure_file

  # Check for duplicate
  if jq -e --arg n "$name" '.[] | select(.name == $n)' "$PLANTS_FILE" > /dev/null 2>&1; then
    echo "Plant \"$name\" already exists."
    exit 1
  fi

  jq --arg n "$name" --argjson f "$freq" --arg d "$today" \
    '. + [{"name": $n, "frequency_days": $f, "last_watered": $d}]' \
    "$PLANTS_FILE" > "${PLANTS_FILE}.tmp" && mv "${PLANTS_FILE}.tmp" "$PLANTS_FILE"

  echo "Added \"$name\" (every $freq days, last watered $today)"
}

cmd_list() {
  ensure_file

  if [[ $(jq length "$PLANTS_FILE") -eq 0 ]]; then
    echo "No plants tracked."
    exit 0
  fi

  printf "%-25s %-10s %-14s %-14s\n" "PLANT" "FREQ" "LAST WATERED" "NEXT WATER"
  printf "%-25s %-10s %-14s %-14s\n" "-----" "----" "------------" "----------"

  jq -r '.[] | "\(.name)\t\(.frequency_days)\t\(.last_watered)"' "$PLANTS_FILE" | while IFS=$'\t' read -r name freq last; do
    next=$(date -d "$last + $freq days" +%Y-%m-%d 2>/dev/null || date -j -v+"${freq}d" -f "%Y-%m-%d" "$last" +%Y-%m-%d)
    printf "%-25s %-10s %-14s %-14s\n" "$name" "${freq}d" "$last" "$next"
  done
}

cmd_remove() {
  local name="${1:?Plant name required}"

  ensure_file

  if ! jq -e --arg n "$name" '.[] | select(.name == $n)' "$PLANTS_FILE" > /dev/null 2>&1; then
    echo "Plant \"$name\" not found."
    exit 1
  fi

  jq --arg n "$name" '[.[] | select(.name != $n)]' "$PLANTS_FILE" > "${PLANTS_FILE}.tmp" && mv "${PLANTS_FILE}.tmp" "$PLANTS_FILE"
  echo "Removed \"$name\" from plants.json"

  # Attempt to delete open Todoist task via API if token available
  if [[ -n "${TODOIST_API_TOKEN:-}" ]]; then
    local task_name="Water $name"
    local task_id
    task_id=$(curl -s -H "Authorization: Bearer $TODOIST_API_TOKEN" \
      "https://api.todoist.com/rest/v2/tasks?project_id=6Crf3cH2RF5v86wc" | \
      jq -r --arg t "$task_name" '.[] | select(.content == $t and .is_completed == false) | .id' | head -1)

    if [[ -n "$task_id" ]]; then
      curl -s -X POST "https://api.todoist.com/rest/v2/tasks/$task_id/close" \
        -H "Authorization: Bearer $TODOIST_API_TOKEN" > /dev/null
      echo "Closed Todoist task \"$task_name\""
    fi
  else
    echo "Note: TODOIST_API_TOKEN not set — check Todoist manually for open \"Water $name\" task"
  fi
}

[[ $# -lt 1 ]] && usage

case "$1" in
  add)    shift; cmd_add "$@" ;;
  list)   cmd_list ;;
  remove) shift; cmd_remove "$@" ;;
  *)      usage ;;
esac
