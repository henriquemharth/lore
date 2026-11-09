#!/usr/bin/env bash
# lore — local operation (127.0.0.1 only, read only over the .md sources).
#
#   scripts/lore.sh build          installs the backend dependencies and builds the web UI
#   scripts/lore.sh start          starts the API + web UI in the background
#   scripts/lore.sh stop           stops the process
#   scripts/lore.sh status         process, address and index summary
#   scripts/lore.sh index          reindexes now (only what changed; --full for everything)
#   scripts/lore.sh logs           follows the log
#   scripts/lore.sh test           runs the backend and frontend test suites
#   scripts/lore.sh install-login  starts lore at every login of this user (macOS launchd)
#   scripts/lore.sh remove-login   turns that off
#
# Configuration: LORE_* variables, or a .env file at the repository root (see docs/CONTRACT.md).
set -euo pipefail

LOGIN_LABEL="dev.lore.agent"
LOGIN_PLIST="$HOME/Library/LaunchAgents/$LOGIN_LABEL.plist"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
DATA="${LORE_DATA_DIR:-$ROOT/.lore}"
HOST="${LORE_HTTP_HOST:-127.0.0.1}"
PORT="${LORE_HTTP_PORT:-8610}"
PIDFILE="$DATA/lore.pid"
LOG="$DATA/lore.log"
LORE="$ROOT/backend/.venv/bin/lore"
URL_HOST="$HOST"
[[ "$HOST" == *:* && "$HOST" != \[* ]] && URL_HOST="[$HOST]"
URL="http://$URL_HOST:$PORT"

running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

# PID of whoever listens on lore's port, only if it really is "lore serve".
port_owner() {
  local pid
  pid="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1)"
  [[ -n "$pid" ]] && ps -o command= -p "$pid" 2>/dev/null | grep -q "lore serve" && echo "$pid"
}

answers() {
  curl -fsS -o /dev/null --max-time 3 "$URL/api/lore/status" 2>/dev/null
}

require_backend() {
  [[ -x "$LORE" ]] || { echo "Backend not installed. Run: scripts/lore.sh build" >&2; exit 1; }
}

cmd_build() {
  (cd "$ROOT/backend" && uv sync)
  if [[ -f "$ROOT/frontend/package.json" ]]; then
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
    echo "Done. Web UI in frontend/dist."
  else
    echo "Done (no frontend/ folder: API only)."
  fi
}

cmd_start() {
  if running; then
    echo "Already running (PID $(cat "$PIDFILE")) at $URL"
    return 0
  fi
  require_backend
  [[ -f "${LORE_FRONTEND_DIR:-$ROOT/frontend/dist}/index.html" ]] || echo "Warning: web UI not built (scripts/lore.sh build). The API starts anyway."
  if port_owner >/dev/null; then
    echo "Port $PORT is already in use (PID $(port_owner)). Run 'scripts/lore.sh stop' or change LORE_HTTP_PORT." >&2
    exit 1
  fi
  mkdir -p "$DATA" && chmod 700 "$DATA"
  rm -f "$PIDFILE"
  # The server writes its own PID to $PIDFILE (on macOS the Python executable may be just a launcher).
  # exec: the subshell becomes the server itself, with no bash waiting on it holding the caller's stdout.
  (cd "$ROOT" && exec nohup "$LORE" serve) >>"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 40); do
    if answers; then
      echo "lore is up: $URL (PID $(cat "$PIDFILE" 2>/dev/null || echo '?')). Log: $LOG"
      return 0
    fi
    sleep 0.5
  done
  echo "lore did not answer. Last lines of the log:" >&2
  tail -n 20 "$LOG" >&2 || true
  exit 1
}

cmd_stop() {
  local pid=""
  if running; then
    pid="$(cat "$PIDFILE")"
  else
    pid="$(port_owner || true)"
  fi
  if [[ -z "$pid" ]]; then
    echo "Not running."
    rm -f "$PIDFILE"
    return 0
  fi
  kill -TERM "$pid"
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid"; fi
  rm -f "$PIDFILE"
  echo "Stopped."
}

cmd_status() {
  if running; then
    echo "Process: running (PID $(cat "$PIDFILE"))"
  elif port_owner >/dev/null; then
    echo "Process: running without a PID file (PID $(port_owner)); 'stop' ends it through the port owner"
  else
    echo "Process: stopped"
  fi
  if answers; then
    echo "Address: $URL  (answering)"
  else
    echo "Address: $URL  (not answering)"
  fi
  if [[ -x "$LORE" ]]; then (cd "$ROOT" && "$LORE" status); fi
}

cmd_index() {
  require_backend
  (cd "$ROOT" && "$LORE" index "$@")
}

cmd_logs() {
  tail -n 50 -f "$LOG"
}

cmd_test() {
  (cd "$ROOT/backend" && uv run python -m unittest discover -s tests)
  if [[ -d "$ROOT/frontend/node_modules" ]]; then
    (cd "$ROOT/frontend" && node_modules/.bin/tsc --noEmit -p . && node --test tests/*.test.mjs)
  else
    echo "Frontend dependencies not installed; skipping the frontend tests (run scripts/lore.sh build)."
  fi
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "$1 is macOS only (it uses launchd). On Linux, use a systemd user service or your own process manager running: $LORE serve" >&2
    exit 1
  fi
}

cmd_install_login() {
  require_macos install-login
  require_backend
  # Unload an old plist first: with KeepAlive loaded, killing the process would make launchd restart it
  # right away, before the file is replaced.
  if [[ -f "$LOGIN_PLIST" ]]; then launchctl unload "$LOGIN_PLIST" >/dev/null 2>&1 || true; fi
  if running || port_owner >/dev/null; then cmd_stop; fi
  mkdir -p "$(dirname "$LOGIN_PLIST")" "$DATA" && chmod 700 "$DATA"
  # launchd runs the server directly (not "start", which detaches into the background: launchd would kill
  # the detached process as soon as the "start" job ended). RunAtLoad starts it at login.
  cat > "$LOGIN_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LOGIN_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$LORE</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
  launchctl load "$LOGIN_PLIST"
  echo "Installed: lore starts by itself at every login of this user (this account only, no sudo)."
  echo "It only starts AGAIN by itself at the next login; if it stops, bring it back with 'scripts/lore.sh start'."
  echo "File: $LOGIN_PLIST"
  echo "To turn it off: scripts/lore.sh remove-login"
}

cmd_remove_login() {
  require_macos remove-login
  if [[ -f "$LOGIN_PLIST" ]]; then
    launchctl unload "$LOGIN_PLIST" >/dev/null 2>&1 || true
    rm -f "$LOGIN_PLIST"
    echo "Removed: lore no longer starts at login. (It does not stop a running process; use 'stop' for that.)"
  else
    echo "It was not installed to start at login."
  fi
}

case "${1:-}" in
  build | start | stop | status | logs | test) "cmd_$1" ;;
  index) shift; cmd_index "$@" ;;
  install-login) cmd_install_login ;;
  remove-login) cmd_remove_login ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
