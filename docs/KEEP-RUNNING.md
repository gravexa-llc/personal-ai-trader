# Keeping the trader running

Personal AI Trader's checks, wrap and Sunday review are **scheduled tasks inside your Claude Code session**
(one-off tasks created with `CronCreate`, one trading day ahead). Claude Code fires them only while that
session is open and idle; they stop when it exits. A run that is due while Claude is busy with another turn
waits until that turn ends. (Claude Code docs: https://code.claude.com/docs/en/scheduled-tasks.)

What happens when the session is gone:

- **Your open positions keep their resting exits at Robinhood.** Stops placed by the checks rest at the
  broker (GTC overnight) and do not depend on the session. If an exit is still waiting for your approval in
  the Robinhood app, it is not resting yet. Approve it.
- **What stops:** the hourly checks, the ratchet that raises stops as a trade gains, targets, the price
  watcher, new entries, the wrap and the journal, and the creation of the next day's runs. If the wrap is
  missed, the next day has no runs until a session starts again.
- **Nothing is lost.** Every run rewrites `trading/STATE.md`. Its first section, "If this session was just
  cleared, restarted or resumed", is a checklist that any new session follows: reconcile with Robinhood,
  rebuild the missing scheduled runs from `tools/schedule.py`, restart the watcher, prove the guard, then run
  the routine for the time of day.

There are two ways to keep it running. Start with the simple one.

## Option 1 (simple): leave a session open on an always-on Mac

1. Use a Mac that stays awake during market hours. In System Settings, stop it sleeping when the display
   turns off (Battery or Energy, "Prevent automatic sleeping…"), or run `caffeinate -i` in a spare Terminal
   window. A laptop with its lid closed sleeps.
2. Open Terminal, `cd` to your trading folder, start `claude`, and leave that window open. Do not `/exit` it
   at the end of the day: the wrap has just created tomorrow's runs in it.
3. After a restart, an update or a crash, open the folder again and start Claude with `claude -r` (pick the
   most recent conversation) or plain `claude`. Then say: **"Read trading/STATE.md and follow its resume
   checklist."** A resumed session usually keeps its scheduled tasks. A fresh one starts with none, and the
   checklist rebuilds them.
4. Not sure it's all there? Run `/personal-ai-trader:trader-doctor`. It compares the session's scheduled tasks
   with what `schedule.py` expects for today, the next trading day and Sunday, and offers to re-create the
   missing ones. Nothing is created until you say yes.

### Notifications on your phone
The routines send one-line pushes with Claude Code's `PushNotification` tool: an open, a close, a stop
waiting for your approval, the daily wrap. On their own these are desktop notifications. To get them on your
phone, connect the session with **Remote Control**: start it with `claude --remote-control "<a name>"` or
type `/remote-control` inside the session. Then install the Claude app on the phone, signed in to the same
account, and allow notifications. In `/config`, turn on push notifications ("Push when Claude decides").
Remote Control needs a Claude subscription plan (not an API key). It also lets you talk to the session from
the phone. (Docs: https://code.claude.com/docs/en/remote-control.) With `notifications.push` set to false
in `trader.json`, the routines put the line at the top of their reply instead.

## Option 2 (advanced, macOS only): a supervisor that relaunches the session

This is for someone comfortable with Terminal and `launchctl`. **It is optional and off by default. The
plugin never installs it.** The templates are in `extras/supervisor/`:

| File | What it is |
|---|---|
| `trader-session.command` | Starts your session in a Terminal window. It resumes the newest transcript of your workspace with `claude -r <id>` and passes a start-up prompt that tells the session to follow the STATE.md resume checklist. It refuses to start a second copy. |
| `trader-supervisor.sh` | A plain shell script (no AI) that a LaunchAgent runs every 3 minutes. If your session is gone, it opens `trader-session.command` in Terminal. Optionally, during market hours, it shows a desktop notification when `trading/STATE.md` has not been rewritten for 90 minutes, which means the session may be alive but stuck. |
| `trader-supervisor.plist.template` | The LaunchAgent that runs the supervisor every 3 minutes and at login. |

### The one rule: the supervisor must find *its own* session by a unique name

The supervisor decides that your trader is alive by looking for a `claude` process whose command line ends
with `--remote-control "<your session name>"`. **If that pattern also matched some other Claude session, the
supervisor would believe your trader is alive while it is not.** The other session could be a second
project, a test session, or anything started with a similar name. Nothing would relaunch your trader, and no
checks would run. So:

- pick a name that no other session on this Mac uses, and never start any other session with it. That
  includes test sessions: give those a different name or none;
- use letters, digits, spaces and hyphens only;
- keep `--remote-control "<name>"` last on the command line (the template does), so the pattern can anchor
  on the end and a longer name like "<name> Test" does not match;
- keep the `-a` on `pgrep`: without it, macOS `pgrep` leaves out its own parent processes.

### Setting it up
1. Copy the three files out of the plugin to a folder of your own, for example `~/personal-ai-trader-supervisor/`.
   Plugin updates replace the plugin folder, and your filled-in copies should not be in it.
2. In both scripts, replace every `<placeholder>`: your workspace path, the unique session name (the same in
   both), the path of the launcher, a log file, a lock file, and the folder that holds the `claude` command
   (`dirname "$(command -v claude)"`). Use full paths throughout: a LaunchAgent does not load your shell
   profile.
3. **Permission prompts.** Nobody watches a relaunch. If the session stops at a permission prompt, it waits
   until someone answers it, from the phone through Remote Control or at the Mac. You decide what goes in
   `EXTRA_FLAGS` (for example a permission mode) with that in mind. The plugin does not choose for you, and
   the order guard and the deny list apply whatever you choose.
4. `chmod +x trader-session.command trader-supervisor.sh`. Double-click `trader-session.command` once to
   check that it starts (or resumes) your session, then quit that session with `/exit`.
5. Fill in the plist, save it as `~/Library/LaunchAgents/<your label>.plist`, and check it with
   `plutil -lint ~/Library/LaunchAgents/<your label>.plist`. Load it:
   `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<your label>.plist`
6. Within about 3 minutes a Terminal window opens with your session. The supervisor's log says what it did.
7. The first time it runs, macOS may ask whether the supervisor may control Terminal. Allow it, or the
   relaunch fails.

To restart the session yourself, `/exit` it and wait about 3 minutes. Don't also start it by hand, or you get
two sessions (the launcher refuses if one is already running).

### Turning it off
`launchctl bootout gui/$(id -u)/<your label>`, then delete the plist from `~/Library/LaunchAgents/`.

### What it does not cover
- **The Mac asleep, off or logged out.** The LaunchAgent runs only while you are logged in, and Terminal
  needs your desktop session to open a window.
- **Holidays in the stale check** come from the plugin's `tools/schedule.py` (set `PLUGIN_ROOT`). The plugin
  folder can move when the plugin updates, so check the path after an update; with `PLUGIN_ROOT` empty the
  stale check is off.
- **Sign-ins.** If the Robinhood sign-in expires, a relaunched session cannot fix that. The first check
  fails and pushes you. Run `/mcp` to sign in again.

## A quick health check, any time
`/personal-ai-trader:trader-doctor` checks the connection, the guard, the schedules, STATE.md's age, the watcher
and the files, and says what to fix. Run it first whenever something looks wrong.
