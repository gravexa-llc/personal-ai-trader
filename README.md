# Personal AI Trader

A Claude Code plugin that runs a rule-bound options and share trader on **your own** Robinhood account. The
order guard enforces your limits in code. You write the strategy yourself, in a guided interview. It starts
on paper.

Claude checks the market on a schedule through the trading day. It manages open positions and their stops,
and looks for entries that match the setups you wrote. It keeps a ledger, a spreadsheet and a journal, sends
you a daily summary, and reviews the week every Sunday. Every order goes through an **order guard**, a hook
that refuses anything outside your limits whatever the model decides.

Status: version 0.1.0, not yet released. Changes are in
`docs/CHANGELOG.md`.

## Read this first: the risks, in plain words

- **This trades real money once you switch it to LIVE.** Options can lose their whole value, and an account
  can lose most of what is in it. Use a separate Robinhood account funded only with money you can afford to
  lose.
- **Claude makes the trading decisions.** The limits stop *forbidden* trades: wrong account, spreads,
  selling to open, oversized buys, orders in DRY_RUN. Inside those limits Claude can still make bad trades,
  misread the news, or follow a weak strategy.
- **The strategy is yours.** The sample strategy only shows the format. It is not advice, and nobody behind
  this plugin is giving investment advice.
- **It is software.** It can have bugs. Robinhood can change its tools, and Claude Code can change how
  schedules or hooks work. Run `/personal-ai-trader:trader-doctor` when something looks off, and read the daily
  summary.
- **The session has to stay open** for the scheduled checks to run (`docs/KEEP-RUNNING.md`). When it is
  closed, your resting stops at Robinhood still protect open positions. Nothing else watches them.
- **You stay responsible for your account.** You choose the limits and the strategy, and you decide when
  to go live. Robinhood's "approve each order" switch is on by default, so every order waits for you in the
  Robinhood app until you turn the switch off.
- This plugin is not made, endorsed or supported by Robinhood or Anthropic.

What it will **never** do: trade spreads, sell options to open, short, trade crypto, exercise options, place
any order while in DRY_RUN, or trade an account other than the one you configured. The guard blocks these,
and the plugin has no switch to allow them (`docs/SAFETY.md`).

## Install

You need Claude Code (v2.1.295 or later: the guard hook relies on `onFailure: "block"`), Python 3.9+ as
`python3`, a Claude subscription, and a Robinhood account with options level 2 or higher and agent access
turned on.

1. **Make an empty folder** for your trading workspace, for example `~/trading-desk`, and open Claude Code
   in it (`cd ~/trading-desk && claude`). The plugin's code lives in Claude Code's plugin folder. Your
   rules, strategy, ledger and journal live in this workspace, and plugin updates never touch them.
2. **Add the plugin.**
   - From the marketplace (the usual way):
     `/plugin marketplace add gravexa-llc/personal-ai-trader`, then
     `/plugin install personal-ai-trader@personal-ai-trader` (the plugin's name, `@`, the marketplace's name).
     You can also run `/plugin` and pick it from the list. Updates arrive with
     `/plugin marketplace update personal-ai-trader`.
   - From a local copy of this repo (for testing or development): start Claude Code with
     `claude --plugin-dir <path to the personal-ai-trader folder>` from inside your workspace.
3. **Run the setup:** `/personal-ai-trader:trader-setup`. It walks you through one step at a time, and you can
   re-run each step:
   1. prerequisites and the risk warning (you type "I understand"). It creates `trading/` and
      `trading/.venv`;
   2. connecting Robinhood: you run `/mcp` and sign in on Robinhood's own page, so Claude never sees your
      password or token;
   3. picking the account. A default DRY_RUN `trading/trader.json` is written straight away;
   4. Robinhood's "approve each order" switch, on by default;
   5. your limits, written to `trader.json` and shown as `trading/RULES.md`;
   6. your strategy (`/personal-ai-trader:trader-strategy`), or the marked sample for now;
   7. your one Robinhood watchlist;
   8. workspace permissions in `.claude/settings.json`, shown to you as a diff first:
      - a deny list for crypto and option exercise;
      - an "ask" rule for edits to `trader.json`;
      - an allow list so scheduled runs don't stop at prompts. Every Robinhood call still goes through
        the order guard;
   9. the guard proof: a forbidden preview must come back BLOCKED (previews place nothing);
   10. the schedules;
   11. the start of the paper week.

   You start in **DRY_RUN** (paper trading).

## Daily life

- **Trading days run on their own** while the session is open. There is a prep before the open, a first
  check after the open, then hourly checks, a pre-close check, a closing mark and a wrap after the close.
  The times come from `tools/schedule.py`, in your timezone, and skip holidays. Each wrap creates the next
  trading day's runs.
- **You get pushes** for opens and closes, for stops that need your approval, and the daily summary. On
  your phone they need Remote Control (`docs/KEEP-RUNNING.md`).
- **With the approval switch on**, you approve or decline each order in the Robinhood app. A stop sent after
  a fill waits for you too. Until you approve it, that position has no resting exit, and the pushes say so.
- **Sundays** the review scores the week, rewrites the market view and levels in your strategy, and asks
  your questions. After the paper week it asks: **"One more week of paper, or go live?"** Going live is
  always your explicit "go" (`/personal-ai-trader:trader-setup --go-live`).
- **Anything odd?** Run `/personal-ai-trader:trader-doctor` first. If it doesn't fix it, it can write a support
  bundle (account number masked, no tokens) for whoever helps you.
- **Keeping it alive:** see `docs/KEEP-RUNNING.md`. The simple way is a session left open on an always-on
  Mac; an optional macOS supervisor can relaunch the session.

## Commands

| Command | What it does |
|---|---|
| `/personal-ai-trader:trader-setup` | Install flow; `--limits` to change your limits, `--go-live` after the paper week, `--resume-from-halt` to leave HALTED (your choice only). |
| `/personal-ai-trader:trader-strategy` | The strategy interview: writes or rewrites `trading/STRATEGY.md` (old versions kept in `trading/reviews/`). `explain-sample` walks through the sample setups. |
| `/personal-ai-trader:trader-status` | What's open, the marks, P/L, stops and exits, pending approvals, the next run, the record so far. Read-only. |
| `/personal-ai-trader:trader-doctor` | Health check with PASS / WARN / FAIL and a fix for each problem; `quick` for a short version. Read-only, apart from re-creating missing schedules when you say yes. |
| `/personal-ai-trader:trader-check` | The trading check (`prep`, `hourly`, `preclose`). Normally run by the schedule. |
| `/personal-ai-trader:trader-wrap` | The daily wrap. Normally run by the schedule. |
| `/personal-ai-trader:trader-weekly-review` | The Sunday review. Normally run by the schedule. |

## Uninstall

1. **Stop trading first.** If you are LIVE, decide what to do with open positions. They stay at Robinhood
   with whatever orders are resting; uninstalling the plugin does not close them.
2. **Stop the schedules:** `/exit` the session (the in-session tasks die with it), or delete them with
   `CronList` / `CronDelete`.
3. **Remove the plugin:** `/plugin uninstall personal-ai-trader@personal-ai-trader` (or stop passing
   `--plugin-dir`).
4. **Disconnect Robinhood:** remove the `robinhood-trading` connection in `/mcp`, and turn off agent access
   for the account in the Robinhood app.
5. **If you set up the optional supervisor**, unload and delete its LaunchAgent (`docs/KEEP-RUNNING.md`,
   "Turning it off").
6. **Your workspace stays:** `trading/` (ledger, journal, reviews, strategy) and `.claude/settings.json`
   (the deny list) are yours to keep or delete.

## For developers

Plugin layout:

- `.claude-plugin/plugin.json`: the manifest.
- `.claude-plugin/marketplace.json`: the marketplace file, listing this repo's root as the one plugin.
- `.mcp.json`: the Robinhood MCP server (`robinhood-trading`, HTTP).
- `hooks/hooks.json`: registers the order guard as a PreToolUse hook for every Robinhood tool.
- `docs/SAFETY.md`: what the guard blocks and how to prove it is live.
- `docs/KEEP-RUNNING.md`, `docs/CHANGELOG.md`.
- `extras/supervisor/`: optional macOS relaunch templates, never installed by the plugin.

### Tools

Run as `python3 "<plugin root>/tools/<tool>.py" ...`. The ledger build runs under `trading/.venv/bin/python`,
which has openpyxl. Every tool takes `--workspace PATH`. Otherwise the workspace is `$CLAUDE_PROJECT_DIR`,
else the first folder at or above the current directory holding `trading/trader.json`. Tools never write
into the plugin folder; all data goes to the workspace's `trading/`. They need Python 3.9+ and only the
standard library (openpyxl only for `ledger.py build`).

| Tool | What it does |
|---|---|
| `tools/order_guard.py` | The order guard hook. It refuses forbidden orders, wrong accounts, fractional option or share quantities and, in DRY_RUN, every order placement or cancel. See `docs/SAFETY.md`. |
| `tools/trader_config.py` | Loads and validates `trading/trader.json`; `--check` prints OK or the problems. Also finds the workspace for the other tools. |
| `tools/ledger.py` | The ledger: `init`, `open`, `close`, `update`, `order`, `equity`, `show [open\|all]`, `build`. The CSVs in `trading/ledger/` are the source of truth. `build` writes `trading/ledger/Trades.xlsx`, with the start balance from `account.start_balance`. |
| `tools/schedule.py` | Prints the one-off scheduled runs as JSON for CronCreate: `--day DATE`, `--next-trading-day [--after DATE]`, `--sunday-review [--after DATE]`, and `--session DATE` (open and close in local time). Times are defined in New York market time and converted to `timezone`. Holidays and early closes come from `tools/market_calendar.json` (NYSE, 2026–2027; extend it each year). |
| `tools/render_rules.py` | Writes `trading/RULES.md` from `trader.json` and `templates/RULES.md.tmpl`. `--check` exits 1 when the file has drifted from the config. |
| `tools/doctor.py` | The script half of `/trader-doctor`: config, RULES drift, hook registration, the guard script, the deny list, venv and ledger build (on a copy), files, the STRATEGY format, STATE age, the watcher, version and calendar coverage. Prints a table, or JSON with `--format json`. `--bundle` writes a masked support bundle. Read-only otherwise. |
| `tools/setup_settings.py` | Writes or merges the workspace `.claude/settings.json` (deny, ask and allow rules for both tool namings). It prints a diff, writes only with `--yes`, keeps your own entries, and is idempotent. `--check` exits 1 if a deny entry is missing (the doctor uses it). |
| `tools/setup_workspace.py` | Setup's workspace helper: `init` (the `trading/` tree and templates, never overwriting), `config` (the default DRY_RUN trader.json), `set` (validated limit changes, diff first), `notes`, `gate`, `go-live`, `phase`, `halt` (DRY_RUN or LIVE to HALTED only, the check routine's halt; allowed unattended) and `extend-paper` (one more paper week; asks you). |
| `tools/watch.py` | Optional 5-minute price tripwire (`"XYZ above 101.25"` rules; prints `TRIGGER`, `CLOSED` or `DATA FAILURE` and exits). Uses Yahoo Finance's free, unofficial quote feed and logs to `trading/logs/watch.log`. One watcher per workspace: `--status` and `--stop` act only on the watcher that workspace started (`trading/logs/watch.pid`). |
| `tools/price_log.py` | Optional option price logger from CBOE's free delayed feed to `trading/logs/price_log.csv`, for shadow tests and reviews only. No scheduler is installed for it. |

### Templates

- `templates/trader.example.json`: an example config (fake values, phase DRY_RUN), including the optional
  `schedule.sunday_review_local` and `rules_set_on` keys.
- `templates/RULES.md.tmpl`: the rules text that `render_rules.py` fills from the config. If
  `trading/RULES-notes.md` exists, it is copied verbatim to the end under "Owner's notes".
- `templates/STRATEGY.sample.md`: the strategy format, with placeholders. It is not a recommendation.
- `templates/ROBINHOOD-SOURCES.md`, `templates/NEWS-SOURCES.md`: what each information source is good for.
- `templates/shadow/README.md`, `templates/shadow/stop25.md`: how to shadow-test a rule change, with a
  worked example.
- `templates/PLAN.md`, `templates/STATE.md.tmpl`, `templates/journal-entry.md`: the process, state and
  journal formats the skills use.

### Running the tests

```
python3 -m pytest tests
```

The tests need Python 3.9+ and pytest. The workbook tests run only when openpyxl is installed.

## Licence

MIT, see `LICENSE`. The software is provided as is, without warranty. Nothing here is investment advice;
you trade your own account at your own risk.
