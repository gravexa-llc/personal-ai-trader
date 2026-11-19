---
name: trader-wrap
description: Personal AI Trader's end-of-day wrap, run by schedule after the close on trading days — reconcile the ledger with Robinhood, snapshot the account at the closing bids, rebuild the spreadsheet, journal the day, create the next trading day's scheduled runs (and the Sunday review if missing), rewrite STATE.md and send the owner the daily summary. Places no new entries.
---

# Daily wrap

**Where things are.** The workspace is `${CLAUDE_PROJECT_DIR}`, the folder this session runs in; paths below are relative to it. The plugin is at `${CLAUDE_PLUGIN_ROOT}`. **How commands are run** (so the workspace allow rules match them and a scheduled run never stops at a prompt): one tool per Bash call, typed exactly as written here, from the workspace root — no `cd` chained in front, no shell function or alias, no `exec`, nothing chained before or after it. If a command fails because `trading/` is not there, the shell has left the workspace root: run `cd` to the workspace folder as a Bash call of its own, then repeat the command. The ledger tool is always called in full, for example:

```
trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" show open
```

Numbers come from `trading/trader.json` and `trading/RULES.md`; the schedule, holidays and early closes come only from `tools/schedule.py`. Time from `date`. The owner is `owner_name` in trader.json. Pushes go through `PushNotification` if `notifications.push` is true, otherwise to the top of the session reply.

1. **Read** `trading/STATE.md`, `trading/trader.json` and today's journal. If `python3 "${CLAUDE_PLUGIN_ROOT}/tools/schedule.py" --day <today>` shows today was not a trading day, stop without writing or notifying — but still do step 6 if the next trading day's runs are missing.
2. **Reconcile.**
   - LIVE / HALTED: `get_equity_orders`, `get_option_orders`, `get_advanced_orders` for today, `get_trade_approvals`, and current positions on `account.number`. Every fill has an `orders.csv` row; every position a matching open trade; **every open position has its resting exit order** — or, with the approval switch on, a stop **waiting for approval**, which is named first in the summary and the push. Every approval sent today has a final row (`approved`, `approval-declined`, `approval-expired`) or is still listed as pending in STATE.md. An entry whose approval was declined or expired has its trade row set to `cancelled`. Fix the ledger and name any mismatch in the summary.
   - DRY_RUN: every paper open and close in the journal has its ledger rows (`mode: paper`, `paper-filled`).
3. **Snapshot.** Mark open options at the last regular-session bid before the close (from the closing-mark entry in today's journal, or the pre-close check; note the post-close quote and Robinhood's own total beside it). Shares at the last trade. Append the closing `trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" equity` row with `note: close`.
4. `trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" build` to refresh `trading/ledger/Trades.xlsx`.
5. **Journal.** Append `## HH:MM wrap` (format: `${CLAUDE_PLUGIN_ROOT}/templates/journal-entry.md`, wrap section): trades opened and closed today with P/L, account value and day change, drawdown from `account.start_balance`, approvals sent / approved / declined / expired, rule checks (any breach, any guard block), what worked, what did not, and what tomorrow's prep must look at (earnings, data releases, positions near a level, anything waiting for the owner).
6. **Schedules — one-off tasks for the next trading day** (why one-off: `trading/PLAN.md` "Schedule"). Do this before step 7 is finalised so STATE.md lists the ids.
   1. `CronList`.
   2. `python3 "${CLAUDE_PLUGIN_ROOT}/tools/schedule.py" --next-trading-day --after <today>` and `python3 "${CLAUDE_PLUGIN_ROOT}/tools/schedule.py" --sunday-review --after <today>`. Each prints JSON `[{run, local_time, cron, prompt}]`.
   3. For each entry with no existing task of the same `cron` and `prompt`, `CronCreate` with `recurring: false`, that `cron` and that `prompt`, verbatim. Never create a task that already exists; never type times or holidays yourself.
   4. Delete any leftover **recurring** task that runs one of this plugin's skills (they are delayed by jitter and expire).
   5. Write the ids, times and runs into STATE.md "Active schedules" (step 7).
7. **State.** Rewrite `trading/STATE.md` (structure of `${CLAUDE_PLUGIN_ROOT}/templates/STATE.md.tmpl`): phase, approval setting and pending approvals, account snapshot (closing value — the next day's change is measured from it), open positions with stops, targets, best bids and resting exit ids, active schedules, price watcher levels for tomorrow, waiting on the owner, next, last run time from `date`.
8. **DRY_RUN, last trading day of the week** (the next trading day from step 6 falls after the coming Sunday — normally a Friday): add to STATE.md "Next" and "Waiting on the owner" that Sunday's review closes the paper week and will ask the owner **"One more week of paper, or go live?"** — once at least `go_live.paper_days_required` paper trading days are complete. Count the paper days with a wrap entry in the journal and write the count into STATE.md.
9. **Tell the owner.** One push, under 200 characters, PAPER or LIVE first: day P/L, account value, trades opened / closed, open positions, and anything that needs them (a stop waiting for approval comes first). Example: `PAPER wrap: +84, value 5,084. Opened XYZ calls, closed none. 2 open. Nothing needed.` Put the fuller summary in the session reply.
