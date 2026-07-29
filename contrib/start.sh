#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
api_pid=""
web_pid=""

stop_children() {
  local pid
  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid"
    fi
  done
  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap 'stop_children; exit 130' INT
trap 'stop_children; exit 143' TERM

"$root/bin/api" &
api_pid=$!
"$root/bin/web" &
web_pid=$!

set +e
wait -n "$api_pid" "$web_pid"
status=$?
set -e

stop_children
exit "$status"
