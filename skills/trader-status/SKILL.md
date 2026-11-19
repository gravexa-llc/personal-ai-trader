---
name: trader-status
description: Personal AI Trader status on demand — "what's open, how are we doing". Shows the phase, account value, open positions with their marks, P/L, stops and resting exits, pending approvals, today's journal headline, the next scheduled run and the record so far. Read-only — places, cancels and writes nothing.
---

# Status

**Where things are.** The workspace is `${CLAUDE_PROJECT_DIR}`; paths below are relative to it and commands run from it. The plugin is at `${CLAUDE_PLUGIN_ROOT}`. The owner is `owner_name` in `trading/trader.json`. Time from `date`.

**Read-only.** No `place_*`, `cancel_*`, `replace_*`, `decline_trade_approval`, alert or watchlist write; no ledger row (not even an equity snapshot); no edit to STATE.md, the journal or any config; no `CronCreate` or `CronDelete`; no push. If something needs action, say so and name the routine that does it. Never print the full account number (use `****` and the last 4 digits).

## 1. Read
1. `trading/STATE.md`, `trading/trader.json` (`phase`, `account`, `limits`, `go_live`) and today's `trading/journal/YYYY-MM-DD.md` if it exists.
2. The ledger, as one Bash call from the workspace root, exactly so (no chained `cd`, no shell function):
   ```
   trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" show all
   ```
3. Is the market open now? `python3 "${CLAUDE_PLUGIN_ROOT}/tools/schedule.py" --session today` gives today's open and close in local time (null when closed). Outside the session, quotes are stale: say "as of the last close" beside every mark.

## 2. Live data
- **LIVE / HALTED:** on `account.number` only — `get_portfolio`, `get_option_positions` (nonzero), `get_equity_positions`, open orders (`get_option_orders`, `get_equity_orders`, `get_advanced_orders`), and `get_trade_approvals`. Marks: `get_option_quotes` for each option (mark at the **bid**), `get_equity_quotes` for shares (last trade).
- **DRY_RUN:** positions are the open `mode: paper` rows; mark them with `get_option_quotes` / `get_equity_quotes` the same way. Paper cash = `account.start_balance` + realized paper P/L − cost of open paper positions. Read `get_trade_approval_setting` only if STATE.md has no reading from today.
- If a Robinhood call fails or is blocked, say so in one line and fall back to STATE.md's last values, labelled with their time.

## 3. Show
Short, in this order, with the phase first:

1. **Phase** — DRY_RUN (paper day `n` of `go_live.paper_days_required`, from STATE.md), LIVE or HALTED (and why, from STATE.md).
2. **Account** — value with options marked at the bid (and Robinhood's own total beside it if different), cash, change since the last close in STATE.md "Account snapshot", drawdown from `account.start_balance`.
3. **Open positions** — a table: trade id, contract or shares, quantity, entry, mark (bid), P/L in dollars and percent, the stop (planned stop, or the ratchet stop and best bid from STATE.md), the **resting exit** (order id and type, or **waiting for approval**, or **none** — which is the first thing to say), the thesis stop and time exit.
   - LIVE: compare with the ledger; a position Robinhood shows that the ledger does not (or the other way round) is named at the top with "run `/personal-ai-trader:trader-doctor`".
4. **Pending approvals** — each with what it is for and since when; a protective stop waiting for approval reads "approve the stop for <SYM> in the Robinhood app — no resting exit until you do".
5. **Today** — the journal's latest entry heading and its decision line (one or two lines), or "no run yet today".
6. **Next scheduled run** — `CronList` (reading the list changes nothing): the earliest task that runs one of this plugin's routines, with its local time. If none is listed on a trading day, or none at all before the next trading day, say so and suggest `/personal-ai-trader:trader-doctor` (it compares the schedule and offers to re-create missing runs).
7. **Record so far** — from the ledger, separately for paper and live: closed trades, wins and losses, win rate, realized P/L, average win and average loss, largest loss, the current loss streak against `limits.loss_streak_pause`.
8. **Waiting on you** — STATE.md "Waiting on the owner", one line each.

Keep it to a screen. Numbers are Robinhood's (or the ledger's); say where any other number came from.
