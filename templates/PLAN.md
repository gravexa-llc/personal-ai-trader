# Operating plan — how the trading loop runs

Copied into your workspace as `trading/PLAN.md` by `/trader-setup`. Limits live in `trader.json` (and its human copy `RULES.md`); this file is the process. Plugin updates ship a new version of this template; your copy changes only if you change it.

## Market hours
- Regular session: NYSE 09:30–16:00 New York time, converted to `timezone` in `trader.json` (in US Pacific time 6:30 AM–1:00 PM). Orders outside the regular session are refused by the order guard.
- Holidays and early closes (13:00 New York time) come only from `tools/schedule.py`, which knows the exchange calendar. A day for which `schedule.py --day <date>` lists no trading-day runs is a closed day. On an early-close day its runs end earlier; "the close" in the routines means that day's close. The plugin's calendar needs a yearly update; `/trader-doctor` warns when it is running out.

## Process
- **Prep** before the open reads overnight news, the calendar and earnings, screens the watchlist and writes the day's plan and shortlist. It also confirms the rest of today's runs exist.
- **Checks** shortly after the open and then hourly: reconcile with Robinhood, enforce the limits, manage every open position (stops, ratchet, resting exits, approvals), look for entries that match a named setup in `STRATEGY.md`, journal the decision.
- **Pre-close check** before the close: no entries; close what must not be held; every position rests an exit overnight.
- **Closing mark** just before the close: records each open option's bid; the wrap values the day on it.
- **Daily wrap** after the close: reconcile, snapshot, rebuild the workbook, journal, create the next trading day's runs, update STATE.md, send the daily summary.
- **Sunday review**: score, diagnose, re-plan `STRATEGY.md`, renew the schedules, ask the owner the week's questions — and after the paper week, "One more week of paper, or go live?".
- An extra **mid-week review** runs after the daily loss stop or the loss-streak pause trips, or a volatility spike.

Routines are the plugin skills `personal-ai-trader:trader-check` (argument `prep`, `hourly` or `preclose`), `personal-ai-trader:trader-wrap` and `personal-ai-trader:trader-weekly-review`. The session makes every trading decision and places every order; background agents may do research only.

## Phases
- **DRY_RUN** (every install starts here): paper trades only. The order guard refuses every order placement and cancel. Paper fills: buys at the ask, sells at the bid. Previews are still made — they place nothing and prove the order would pass.
- **LIVE**: real orders within the rules. Only `/trader-setup --go-live` sets it, after the paper week, the gate checks and the owner's explicit "go".
- **HALTED**: set by the check routine when the account falls to the halt level. No new entries; open positions are managed and closed. Only the owner leaves HALTED, through `/trader-setup`.

## Robinhood's approval switch
On by default: every order the session sends waits for the owner to approve or decline it in the Robinhood app. The routines read the setting (`get_trade_approval_setting`) at the first run of each day and pending approvals (`get_trade_approvals`) at every run while any is pending. They never resubmit an order whose approval is pending, and they log approvals that are declined or expire. **After an entry fills, its protective stop also waits for approval: until the owner approves it, the position has no resting exit.** The checks push the owner ("approve the stop for X in the Robinhood app"), re-check at every run and watch the stop level by hand meanwhile. The owner turns the switch off in the app when ready; the routines note the change in the journal.

## Files (everything is on disk so a session reset loses nothing)
| File | Purpose | Written when |
|---|---|---|
| `trading/trader.json` | The config the order guard and the routines read: account, phase, limits, option rules | By `/trader-setup` or the owner; the routines never edit it by hand: the check sets `phase: HALTED` at the halt level with `setup_workspace.py halt`, and the Sunday review adds to `go_live.extensions` on the owner's "one more week" with `setup_workspace.py extend-paper` |
| `trading/RULES.md` | Human-readable rules, generated from trader.json by `tools/render_rules.py` | When trader.json changes |
| `trading/PLAN.md` | This process | When the process changes |
| `trading/STRATEGY.md` | The owner's setups, market view, watchlist, this week's calendar | `/trader-strategy`; every Sunday review (old versions kept in `reviews/`) |
| `trading/STATE.md` | Where we are right now: phase, approvals, open positions, schedules, waiting on the owner, the resume checklist | End of every run |
| `trading/ROBINHOOD-SOURCES.md` | Every Robinhood information source: tested or not, verdict, evidence | When a source is first tried or a verdict changes; scored every Sunday |
| `trading/NEWS-SOURCES.md` | News sources and which run reads them | When a source is added or proves useless |
| `trading/journal/YYYY-MM-DD.md` | What each check saw and decided, including "did nothing, because" | Every run |
| `trading/reviews/YYYY-MM-DD.md` | Sunday review and the plan for the week; mid-week reviews; old STRATEGY versions | Every Sunday, and mid-week when a guard trips |
| `trading/shadow/*.md` | Shadow tests of a rule change, judged on real prices without changing live orders | Each check, as each file's own instructions say |
| `trading/ledger/trades.csv` | One row per position, open to close, with thesis and P/L | On every open, update and close |
| `trading/ledger/orders.csv` | One row per order event (preview, approval, placed, filled, cancelled) | On every order event |
| `trading/ledger/equity.csv` | Account value snapshot | Every check and at the close |
| `trading/ledger/Trades.xlsx` | The spreadsheet, rebuilt from the CSVs (never edited by hand) | After every ledger change and at the wrap |
| `trading/logs/` | Price watcher heartbeat (`watch.log`) and other tool logs | By the tools |
| `trading/.venv/` | Python environment for the ledger (openpyxl) | By `/trader-setup` |

The CSVs are the source of truth; the workbook is generated from them.

## Schedule
The runs are **in-session one-off tasks** created with `CronCreate` (`recurring: false`), one trading day ahead.
- **Where the times come from:** only `tools/schedule.py`. It prints the day's runs as JSON `[{run, local_time, cron, prompt}]` in the workspace's timezone, skipping weekends and holidays and shortening early-close days. Each entry is created with its `cron` and `prompt` verbatim.
  - `schedule.py --day YYYY-MM-DD` — the runs of one day;
  - `schedule.py --next-trading-day [--after DATE]` — the next trading day's runs;
  - `schedule.py --sunday-review [--after DATE]` — the coming Sunday's review.
- **Why one-off:** Claude Code delays recurring tasks on purpose ("jitter"), by up to 30 minutes for hourly or daily schedules, and recurring tasks expire after 7 days. One-off tasks pinned to a day and month, at a minute other than :00 or :30, fire on the minute and do not expire. Source: https://code.claude.com/docs/en/scheduled-tasks.
- **Who creates what:** each daily wrap creates the next trading day's runs and, if none exists, the coming Sunday's review. The Sunday review confirms the next trading day's runs and creates the following Sunday's. Prep confirms the rest of its own day. On any start, the resume checklist in STATE.md re-creates whatever is missing. Every creator runs `CronList` first and creates only what is missing, so nothing is duplicated.
- **The session must stay open.** The tasks live in the Claude Code session: they fire only while it is open and idle (a run or a conversation in progress delays them until that turn ends), and they die when it exits. If a wrap is missed, the next day has no runs until the session is started again and its checklist runs. `docs/KEEP-RUNNING.md` describes how to keep a session alive.
- **Extra checks:** a one-off check (for example 15 minutes after an event, or while an order or an approval is working) uses `recurring: false` with day and month pinned and the prompt `Personal AI Trader one-off check: <reason>. Run the personal-ai-trader:trader-check skill with argument hourly.`

### Checking more often than hourly
The check tightens to every 15 minutes (one-off tasks, ended when the trigger clears) around a scheduled event in the session that bears on an open position (Fed, CPI, jobs, a held company's earnings) and while an order — or an approval — is working. Price levels are left to the price watcher and the hourly checks. Robinhood price alerts at each position's stop and target levels notify the owner's phone; they do not reach the session, so every check reads `get_alert_log`.

### The 5-minute price watcher (optional)
`tools/watch.py` polls stock prices every 5 minutes from a free public feed and exits the moment a stop or target level on an underlying is crossed. It runs as a background command of the session, so its exit wakes the session, which re-checks on Robinhood and acts. It dies with the session (each check confirms it is alive), watches stock prices rather than option premiums, and the feed is unofficial — a tripwire, never a price to trade on.

## Surviving a session reset
- On every start the session reads `trading/STATE.md` and follows its numbered resume checklist: read the files, reconcile, rebuild missing schedules, restart the watcher, prove the guard, then run the routine for the time of day.
- STATE.md is rewritten at the end of every run, so the newest file is the truth, not the conversation.
- If a reset happens during market hours, the reconcile of positions, open orders and pending approvals against the ledger comes before any trading decision.

## Operating notes (things learned that are not obvious from the code)
- **Ledger:** `ledger.py` is the only way ledger rows are written; it rejects unknown fields so a typo cannot drop data. Call it in full, one Bash call per command, from the workspace root: `trading/.venv/bin/python "<plugin root>/tools/ledger.py" open '{...}'`. No shell function, alias, variable or chained `cd` in front: the workspace allow rules match each command's own text, so any wrapper makes an unattended run stop at a permission prompt.
  - `open` writes the trade **before** the order is sent, with thesis, catalyst, sources, planned stop, target and time exit, and the `option_id` for an option (the guard matches a later stop-market on it). Paper trades get ids `P0001…`, live ones `L0001…`.
  - After a live fill, `update` the trade with the real fill price, cost, fees and order id. An entry that never fills (cancelled, approval declined or expired) is set to `status: cancelled` with the reason in `exit_reason`.
  - `order` rows: one per event, with `mode`, `action`, `symbol`, `state` (`previewed`, `awaiting-approval`, `approved`, `approval-declined`, `approval-expired`, `placed`, `filled`, `cancelled`, `paper-filled`) and the Robinhood order or approval id.
  - `build` after every change.
- **Paper fill rule:** paper buys fill at the ask, paper sells at the bid, timestamp now; what is held is marked at the bid. Paper cash = start balance + realized paper P/L − cost of open paper positions.
- **Option lookups:** `get_option_instruments` with `chain_symbol`, one `expiration_dates` value and `type` returns the whole expiry; then `get_option_quotes` on the few strikes of interest.
- **Tick sizes:** option prices of 3.00 and above tick in 0.05 on most underlyings (below 3.00, 0.01 or 0.05 depending on the class). Round a protective stop **up** to the tick, never down below a locked-in level. A resting stop commits the contracts, so a target order cannot rest beside it: to take a target, cancel the stop, confirm it is cancelled, sell, then rest a new stop for anything left.
- **Robinhood stop-markets on options** are refused in the first 15 minutes after the open and exist only as day orders; the check swaps them in after `options.stop_market_after_gain.not_before` and back to a GTC stop-limit at the pre-close check.
- **A resting option stop does not fire when only the bid dips below it**; the bid rule in the check closes the position by hand.
- **Robinhood index ids** for `get_index_quotes`: VIX `3b912aa2-88f9-4682-8ae3-e39520bdf4db`, S&P 500 `432fbbb8-b82c-454a-852d-eb85382c7066`, Nasdaq-100 `50c298f7-27a8-44a1-b049-ec153cf2892f`. Russell 2000 is not available; use IWM.
- **Scanner:** `preview_scan` saves nothing. Percent filters take decimals (0.03 = 3%); filters with intervals need `interval` (use `1d`).
- **`get_advanced_orders`** needs a full RFC3339 timestamp in `created_at_gte` (or an `order_id`).
- **Option fees when live:** a few cents per contract; record them in the ledger.
- **Time:** always take the time from `date`. A one-off task more than 5 minutes late with an idle session is worth a journal line.
- `.claude/scheduled_tasks.lock` is Claude Code's own bookkeeping for in-session schedules; leave it alone.
- **The order guard** (`docs/SAFETY.md`): a block (`order_guard: BLOCKED — …`) is a stop sign — fix the order to fit the rules or drop it, never work around it. If a preview of a two-leg option order comes back as a preview instead of BLOCKED, the hook is not running: treat the session as DRY_RUN until it is fixed.
