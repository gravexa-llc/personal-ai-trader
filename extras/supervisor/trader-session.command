#!/bin/zsh
# trader-session.command — TEMPLATE. Opens (or resumes) your Personal AI Trader Claude Code session in this
# Terminal window. Optional, macOS only, never installed by the plugin. See docs/KEEP-RUNNING.md.
#
# Before use, replace every <placeholder> below (keep the quotes), then: chmod +x trader-session.command
# You can double-click it to start the session by hand; the supervisor opens it when the session is gone.
#
# What it does:
#   - refuses to start if your session is already running (looked up by its unique name, see below);
#   - resumes the newest transcript of this workspace with `claude -r <id>`, so the conversation carries on,
#     and passes a start-up prompt that tells the session to follow the resume checklist in STATE.md;
#   - starts a fresh session with the same prompt if there is no transcript yet.

# ---------------------------------------------------------------- fill these in
WORKSPACE="<absolute path to your trading workspace, the folder that holds trading/>"
# A name no other Claude Code session on this Mac uses — not a test session, not another project.
# The supervisor decides "my session is alive" by finding this exact name on a claude command line.
# Letters, digits, spaces and hyphens only (it is used in a pattern).
SESSION_NAME="<unique session name, e.g. Personal AI Trader - your name>"
# Where Claude Code keeps its transcripts. Default ~/.claude; if you run with CLAUDE_CONFIG_DIR, use that.
CONFIG_DIR="<your Claude config folder, e.g. $HOME/.claude>"
# The folder that holds the `claude` command (find it with: dirname "$(command -v claude)").
CLAUDE_BIN_DIR="<folder holding the claude command>"
LOG="<absolute path to a log file, e.g. $HOME/Library/Logs/personal-ai-trader-supervisor.log>"
# Extra flags. Nobody watches a relaunch: a permission prompt would wait until someone answers it.
# Choose deliberately (see docs/KEEP-RUNNING.md); the plugin does not choose for you.
EXTRA_FLAGS=()   # <extra claude flags inside the brackets, or leave it empty>
# ---------------------------------------------------------------- end of settings

export PATH="$CLAUDE_BIN_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$(dirname "$LOG")"
cd "$WORKSPACE" || { echo "cannot cd to $WORKSPACE"; exit 1; }

# Liveness: a claude process of this user whose command line carries --remote-control "<SESSION_NAME>".
# macOS pgrep leaves out its own ancestors unless -a is given; keep the -a.
# The name is matched as a whole word at the end of the command line, so "Trader" does not match
# "Trader Test". --remote-control takes an optional name, so it stays LAST with the name right after it.
ALIVE_PATTERN="(^|/)claude .*--remote-control ${SESSION_NAME}\$"
running=$(pgrep -a -u "$(id -u)" -f "$ALIVE_PATTERN" | head -1)
if [ -n "$running" ]; then
  echo "Your trader session is already running (pid $running). Not starting another."
  echo "$(date -u +%FT%TZ) session.command: refused, already running (pid $running)" >>"$LOG"
  exit 0
fi

STARTUP_PROMPT="Relaunched automatically at $(date -u +%FT%TZ). Read trading/STATE.md and follow its section 'If this session was just cleared, restarted or resumed' in order: reconcile, compare CronList with tools/schedule.py and re-create only the missing runs, restart the price watcher if positions are open in market hours, prove the order guard with a preview, then run the routine for the time of day. The phase in trader.json governs; in DRY_RUN place no orders. Rewrite STATE.md when done and send one push saying the session restarted and whether anything needs me."

# Claude Code stores a workspace's transcripts under <config>/projects/<path with every non-alphanumeric
# character replaced by ->.
TXDIR="$CONFIG_DIR/projects/$(printf '%s' "$WORKSPACE" | sed 's/[^A-Za-z0-9]/-/g')"
newest=$(ls -t "$TXDIR"/*.jsonl 2>/dev/null | head -1)
if [ -n "$newest" ]; then
  sid=$(basename "$newest" .jsonl)
  echo "$(date -u +%FT%TZ) session.command: resuming -r $sid" >>"$LOG"
  exec claude -r "$sid" "$STARTUP_PROMPT" "${EXTRA_FLAGS[@]}" --remote-control "$SESSION_NAME"
else
  echo "$(date -u +%FT%TZ) session.command: no transcript, fresh start" >>"$LOG"
  exec claude "$STARTUP_PROMPT" "${EXTRA_FLAGS[@]}" --remote-control "$SESSION_NAME"
fi
