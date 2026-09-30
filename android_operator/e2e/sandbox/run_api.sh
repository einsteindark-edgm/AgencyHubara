#!/usr/bin/env bash
# Sandbox API: the REAL src.main:app (chats + orders plugins) on 127.0.0.1:8010
# over synthetic data, with Medusa / Temporal / WhatsApp faked. See VERIFY.md.
#
#   ./run_api.sh start [--reset]   seed if missing (or re-seed with --reset), start in background
#   ./run_api.sh stop              stop it
#   ./run_api.sh restart [--reset]
#   ./run_api.sh status            pid + health
#   ./run_api.sh fg [--reset]      run in the foreground (Ctrl-C to stop)
#   ./run_api.sh check             boot + wire everything, print the route check, exit
#
# Android emulator base URL: http://10.0.2.2:8010  (build the app with -Phubara.apiUrl=http://10.0.2.2:8010)
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${SANDBOX_REPO:-$(cd "$SANDBOX_DIR/../../.." && pwd)}"   # android_operator/e2e/sandbox → raíz del repo
DATA_DIR="${SANDBOX_DATA_DIR:-$SANDBOX_DIR/data}"
PORT=8010
LOG="$SANDBOX_DIR/api.log"
PID_FILE="$SANDBOX_DIR/api.pid"            # written by launcher.py (the python/uvicorn process)
WRAPPER_PID_FILE="$SANDBOX_DIR/api.wrapper.pid"
UV_BIN="$(command -v uv || true)"
[ -n "$UV_BIN" ] || { echo "uv not found in PATH" >&2; exit 1; }
PY_SEED="$(command -v python3)"

cmd="${1:-start}"
shift || true
reset=0
for arg in "$@"; do
  case "$arg" in
    --reset) reset=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

port_busy() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

ensure_seed() {
  if [ "$reset" = 1 ] || [ ! -f "$DATA_DIR/medusa/store.json" ]; then
    SANDBOX_DATA_DIR="$DATA_DIR" "$PY_SEED" "$SANDBOX_DIR/seed.py"
    return
  fi
  # The seed is relative to "now": warn when it is stale (fires/hot/windows drift).
  local age_min
  age_min="$("$PY_SEED" -c 'import json,sys,time; print(int((time.time()*1000-json.load(open(sys.argv[1]))["seeded_at_ms"])/60000))' "$DATA_DIR/seed_info.json" 2>/dev/null || echo "?")"
  if [ "$age_min" != "?" ] && [ "$age_min" -gt 20 ]; then
    echo "note: the seed is ${age_min} min old — timestamps are relative; use --reset (or python3 inject.py reset) for fresh fires/hot/windows" >&2
  fi
}

# Clean environment: NOTHING from the caller's shell (no tokens, no AWS profile,
# no .env) — only what the sandbox needs. launcher.py re-checks and aborts if a
# credential-like variable is present. `uv run` needs the `cd hubara_agency`
# (repo convention); --no-sync = never touches the venv.
ENV_ARGS=(
  PATH="$(dirname "$UV_BIN"):/usr/bin:/bin:/usr/sbin:/sbin"
  HOME="$HOME" LANG="en_US.UTF-8" LC_ALL="en_US.UTF-8" PYTHONUNBUFFERED=1
  SANDBOX_REPO="$REPO" SANDBOX_DATA_DIR="$DATA_DIR"
  SANDBOX_SEND_DELAY_S="${SANDBOX_SEND_DELAY_S:-0}"   # optional simulated WhatsApp latency (s)
  PYTHONPYCACHEPREFIX="$SANDBOX_DIR/.pycache"          # never write bytecode into the repo
)
launch() {
  (cd "$REPO/hubara_agency" && env -i "${ENV_ARGS[@]}" "$UV_BIN" run --no-sync python "$SANDBOX_DIR/launcher.py" "$@")
}

health() {
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/" 2>/dev/null
}

start_bg() {
  if is_running; then echo "already running (pid $(cat "$PID_FILE"))"; return 0; fi
  if port_busy; then echo "port $PORT is busy — not starting" >&2; lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true; exit 1; fi
  ensure_seed
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting sandbox API ===" >>"$LOG"
  (
    cd "$REPO/hubara_agency"
    nohup env -i "${ENV_ARGS[@]}" "$UV_BIN" run --no-sync python "$SANDBOX_DIR/launcher.py" >>"$LOG" 2>&1 </dev/null &
    echo $! >"$WRAPPER_PID_FILE"
  )
  for _ in $(seq 1 90); do
    if out="$(health)"; then
      echo "sandbox API up on http://127.0.0.1:$PORT (emulator: http://10.0.2.2:$PORT) pid=$(cat "$PID_FILE" 2>/dev/null || echo '?')"
      echo "$out"
      return 0
    fi
    sleep 1
  done
  echo "did not become healthy in 90 s — see $LOG" >&2
  tail -40 "$LOG" >&2 || true
  exit 1
}

stop() {
  local stopped=0
  if [ -f "$PID_FILE" ]; then
    local pid; pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
      stopped=1
    fi
    rm -f "$PID_FILE"
  fi
  if [ -f "$WRAPPER_PID_FILE" ]; then
    local wpid; wpid="$(cat "$WRAPPER_PID_FILE")"
    kill -0 "$wpid" 2>/dev/null && kill -TERM "$wpid" 2>/dev/null || true
    rm -f "$WRAPPER_PID_FILE"
  fi
  if [ "$stopped" = 1 ]; then echo "sandbox API stopped"; else echo "sandbox API was not running"; fi
}

case "$cmd" in
  start) start_bg ;;
  stop) stop ;;
  restart) stop; start_bg ;;
  status)
    if is_running; then echo "running pid=$(cat "$PID_FILE")"; health && echo; else echo "not running"; fi ;;
  fg)
    if port_busy; then echo "port $PORT is busy" >&2; exit 1; fi
    ensure_seed
    launch ;;
  check)
    ensure_seed
    launch --check ;;
  *) echo "usage: $0 {start|stop|restart|status|fg|check} [--reset]" >&2; exit 2 ;;
esac
