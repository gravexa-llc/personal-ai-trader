#!/bin/zsh
# trader-supervisor.sh — TEMPLATE. Run every few minutes by a LaunchAgent (trader-supervisor.plist.template).
# Optional, macOS only, off by default, never installed by the plugin. See docs/KEEP-RUNNING.md.
#
# Plain script, no AI: if your trader session is gone, it opens trader-session.command in Terminal, which
# resumes the session with `claude -r` and a start-up prompt. Optionally it also shows a desktop notification
# when trading/STATE.md has not been rewritten for 90 minutes during market hours (the session may be alive
# but stuck, or its schedules gone).
#
# Needs you to be logged in to the Mac (Terminal must be able to open a window). It cannot help when the Mac
# is asleep, off, or logged out.
#
# Replace every <placeholder> (keep the quotes), then: chmod +x trader-supervisor.sh

# ---------------------------------------------------------------- fill these in
WORKSPACE="<absolute path to your trading workspace, the folder that holds trading/>"
# Exactly the same unique name as in trader-session.command (letters, digits, spaces and hyphens only).
# See the warning below.
SESSION_NAME="<unique session name, e.g. Personal AI Trader - your name>"
LAUNCHER="<absolute path to your filled-in trader-session.command>"
LOG="<absolute path to a log file, e.g. $HOME/Library/Logs/personal-ai-trader-supervisor.log>"
LOCK="<absolute path to a lock file, e.g. $HOME/Library/Caches/personal-ai-trader-supervisor.lock>"
# Optional stale check: the plugin folder (holds tools/schedule.py), used to learn today's market hours.
# Leave it empty to switch the stale check off. After a plugin update the folder can change: re-check it.
PLUGIN_ROOT="<plugin folder, or leave empty>"
STALE_MINUTES=90
# ---------------------------------------------------------------- end of settings

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")"

# WARNING — look for YOUR session by a unique name, and never mistake another Claude session for it.
# If the pattern also matched another claude process (a second project, a test session, a session started
# with a similar name), the supervisor would think your trader is alive while it is not, and nothing would
# relaunch it: no checks, no stops watched. Use a name nobody else uses, keep --remote-control "<name>" last
# on the command line, and keep the trailing $ so a longer name does not match.
# macOS pgrep leaves out its own ancestors unless -a is given; keep the -a.
ALIVE_PATTERN="(^|/)claude .*--remote-control ${SESSION_NAME}\$"

# Single flight: skip this run if a relaunch happened less than two minutes ago.
if [ -f "$LOCK" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK") ))
  [ $age -lt 120 ] && exit 0
fi

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Personal AI Trader\"" >/dev/null 2>&1 || true
}

stale_check() {
  [ -n "$PLUGIN_ROOT" ] || return 0
  local state="$WORKSPACE/trading/STATE.md" session open close now age stamp last
  [ -f "$state" ] || return 0
  # schedule.py knows weekends, holidays and early closes; it prints open/close in your timezone.
  session=$(python3 "$PLUGIN_ROOT/tools/schedule.py" --session today --workspace "$WORKSPACE" 2>/dev/null) || return 0
  open=$(printf '%s' "$session" | python3 -c 'import json,sys; print(json.load(sys.stdin)["open_local"] or "")')
  close=$(printf '%s' "$session" | python3 -c 'import json,sys; print(json.load(sys.stdin)["close_local"] or "")')
  [ -n "$open" ] && [ -n "$close" ] || return 0            # market shut today
  now=$(date +%H:%M)
  [[ "$now" > "$open" ]] && [[ "$now" < "$close" ]] || return 0
  age=$(( ($(date +%s) - $(stat -f %m "$state")) / 60 ))
  [ $age -lt $STALE_MINUTES ] && return 0
  stamp="$LOCK.stale"
  if [ -f "$stamp" ]; then
    last=$(( $(date +%s) - $(stat -f %m "$stamp") ))
    [ $last -lt 3600 ] && return 0                           # at most one notification an hour
  fi
  touch "$stamp"
  echo "$(date -u +%FT%TZ) STATE.md stale for $age min (session alive: $1)" >>"$LOG"
  notify "No trading check for $age minutes. Resting stops still protect open positions; please look at the session."
}

if pgrep -a -u "$(id -u)" -f "$ALIVE_PATTERN" >/dev/null; then
  stale_check yes
  exit 0
fi

echo $$ >"$LOCK"
echo "$(date -u +%FT%TZ) no live trader session, opening the launcher in Terminal" >>"$LOG"
out=$(open -a Terminal "$LAUNCHER" 2>&1); rc=$?
echo "$(date -u +%FT%TZ) open -a Terminal rc=$rc ${out:+($out)}" >>"$LOG"
if [ $rc -ne 0 ]; then
  notify "The trader session is gone and the relaunch failed (rc=$rc). Open it by hand."
fi
exit 0
