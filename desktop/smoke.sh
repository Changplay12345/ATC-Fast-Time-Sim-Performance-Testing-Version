#!/usr/bin/env bash
# Smoke test of the macOS build - run after every build, and by CI before a
# release is published. The macOS counterpart of smoke.ps1.
#
#     bash desktop/smoke.sh [path/to/App.app]
#
# Checks, over three launches:
#   1. the app starts and brings its engine up
#   2. the engine listens on 127.0.0.1 only, on a port it picked itself
#   3. /api/health answers, with the same version as VERSION, in local mode
#   4. the API refuses a request without the session token; the docs are off
#   5. the app notices when its engine dies under it
#   6. killing the app takes the engine with it (the parent watch)
# Not checked here (needs someone at the screen): the "engine cannot start"
# error box, and closing the window by hand.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
APP="${1:-$(ls -d "$REPO"/desktop/src-tauri/target/release/bundle/macos/*.app 2>/dev/null | head -1)}"
BIN="$APP/Contents/MacOS/atc-fts-desktop"
LOG="$HOME/Library/Logs/th.co.bearcat.atcfts/ATC Fast-Time Simulation Tool.log"
failed=0

check() {  # name, ok(0/1), detail
  if [ "$2" = 0 ]; then echo "  ok    $1 $3"; else echo "  FAIL  $1 $3"; failed=$((failed + 1)); fi
}
stop_all() {
  pkill -9 -x atc-fts-desktop 2>/dev/null
  pkill -9 -x atc-engine 2>/dev/null
  sleep 1
}
log_count() { [ -f "$LOG" ] && grep -c -F "$1" "$LOG" || echo 0; }
# Starts the app; sets APP_PID, ENGINE_PID and PORT (empty if the engine
# never came up).
start_app() {
  "$BIN" >/dev/null 2>&1 &
  APP_PID=$!
  ENGINE_PID=""; PORT=""
  for _ in $(seq 1 240); do
    sleep 0.25
    ENGINE_PID="$(pgrep -P "$APP_PID" -x atc-engine | head -1)"
    if [ -n "$ENGINE_PID" ]; then
      PORT="$(lsof -nP -a -p "$ENGINE_PID" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {n=split($9,a,":"); print a[n]; exit}')"
      [ -n "$PORT" ] && break
    fi
  done
}

[ -x "$BIN" ] || { echo "app not found: $BIN"; exit 1; }
echo "starting $APP"
stop_all

# --- first launch: the engine and its API ----------------------------------
start_app
check "engine started by the app" "$([ -n "$ENGINE_PID" ] && echo 0 || echo 1)"
if [ -z "$ENGINE_PID" ]; then stop_all; exit 1; fi
listen="$(lsof -nP -a -p "$ENGINE_PID" -iTCP -sTCP:LISTEN | awk 'NR>1 {print $9}')"
check "listens on loopback only" "$(echo "$listen" | grep -v '^127\.0\.0\.1:' | grep -q . && echo 1 || echo 0)" "($listen)"
health=""
for _ in $(seq 1 120); do
  health="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)" && [ -n "$health" ] && break
  sleep 0.5
done
read -r ok ver mode <<< "$(printf '%s' "$health" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d.get("ok"), d.get("version"), d.get("mode"))
except Exception: print("", "", "")')"
check "health answers" "$([ "$ok" = "True" ] && echo 0 || echo 1)"
check "engine version matches VERSION" "$([ "$ver" = "$VERSION" ] && echo 0 || echo 1)" "($ver vs $VERSION)"
check "engine is in local mode" "$([ "$mode" = "local" ] && echo 0 || echo 1)"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT/api/cat62_reference")"
check "API refuses a request without the token" "$([ "$code" = 401 ] && echo 0 || echo 1)" "(HTTP $code)"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT/docs")"
check "interactive docs are off" "$([ "$code" = 404 ] && echo 0 || echo 1)" "(HTTP $code)"

# --- the engine dies under the app: the shell must notice -----------------
before="$(log_count 'the engine stopped unexpectedly')"
kill -9 "$ENGINE_PID"
noticed=1
for _ in $(seq 1 40); do
  sleep 0.25
  [ "$(log_count 'the engine stopped unexpectedly')" -gt "$before" ] && { noticed=0; break; }
done
check "the app notices when its engine dies" "$noticed"
stop_all

# --- second launch: the parent watch ----------------------------------------
start_app
kill -9 "$APP_PID"
gone=1
for _ in $(seq 1 40); do
  sleep 0.25
  pgrep -x atc-engine >/dev/null || { gone=0; break; }
done
check "engine exits when the app is killed" "$gone"
stop_all

if [ "$failed" -gt 0 ]; then echo; echo "$failed check(s) failed"; exit 1; fi
echo; echo "smoke test passed"
