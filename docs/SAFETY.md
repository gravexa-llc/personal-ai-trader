# Safety: the order guard

Personal AI Trader trades real money, so its hard limits are code, not instructions to the model. The core is
`tools/order_guard.py`, a Claude Code **PreToolUse hook** that runs before every Robinhood tool call. It
reads your limits from `trading/trader.json` and refuses anything outside them, whatever the model decides.
A refusal looks like this, and the call never reaches Robinhood:

```
order_guard: BLOCKED — option orders must have exactly one leg; multi-leg trades are not allowed
```

The plugin offers no way to turn the guard off.

## The layers

| Layer | What it stops | Who enforces it |
|---|---|---|
| Deny list in the workspace `.claude/settings.json` (written by setup) | Crypto orders, option exercise and its cancel | Claude Code: the tools are removed from the session |
| Order guard hook (this file) | Everything in the next section | Claude Code runs the hook; exit code 2 blocks the call |
| Check routine | Cash floor, position count, theme limit, daily loss, halt level, loss streak, liquidity, earnings | The skill, backed by the ledger (prompt-level) |
| Robinhood's "approve each order" switch | Every order waits for you in the Robinhood app | Robinhood |
| Go-live gate | Real orders before the paper week | The guard, through `phase` |

The hook is registered with `"onFailure": "block"`, so if the guard crashes, is missing, or times out, the
call is blocked rather than let through (needs Claude Code v2.1.295 or later).

## What the guard blocks

Applied in this order:

1. **Unreadable input**: a call it cannot parse is refused.
2. **Never reachable**: crypto preview, place and cancel, the crypto sign-up tool, option exercise and its cancel.
3. **Unlisted tools**: any Robinhood tool not on the guard's allow-list (for example saved-scanner writes,
   following watchlists, the options watchlist, or a tool Robinhood adds later).
4. **No valid config**: if `trading/trader.json` is missing, unreadable or invalid, only read-only tools that
   name no account (quotes, chains, search, `get_accounts`, ...) pass; everything else is refused.
5. **Phase**:
   - `DRY_RUN` refuses every `place_*`, `cancel_*`, `replace_*` and `decline_trade_approval`. Previews (`review_*`) and reads pass, and are still checked against the rules below.
   - `HALTED` refuses new opening orders, both previews and placements (option buy-to-open, share buys). Closing orders, all cancels, declining an approval, and reads pass.
   - `LIVE` applies the rules below. The config is rejected as invalid if it says `LIVE` while `go_live.met` is false.
6. **Wrong account**: every tool that takes an account number is refused for any account but `account.number`.
7. **Orders**:
   - Options: exactly one leg; only buy-to-open (limit order) or sell-to-close (limit or stop-limit); regular hours; whole contracts only; a buy costs at most `limits.option_cap_dollars` (price × 100 × contracts). No market orders, no spreads, no selling to open.
   - Option stop-market sells, only to protect a gain and only if `options.stop_market_after_gain.enabled`: day order (`gfd`), sell-to-close, the contract matches exactly one open live trade in `trading/ledger/trades.csv` by `option_id`, no more contracts than that trade holds, and a stop of at least entry × (1 + `options.ratchet.lock_gain_pct` / 100), so entry × 1.25 with the default settings.
   - Shares: buys are limit orders costing at most `limits.share_cap_dollars`; sells are limit, stop-limit or stop-market; regular hours; whole shares; no dollar-amount orders.
   - Advanced (OCO) orders: sell side only (a buy-side OCO covers a short), regular hours.
   - `allowed.long_options` / `allowed.shares` set to false refuse option buys / share buys.
8. **Watchlists**: only a list named `watchlist.name` may be created, and only the list with id
   `watchlist.id` may be changed. It may not be renamed and crypto may not be added to it.

What it **cannot** check, because it needs account state: the cash floor, position count, whether shares being
sold are held, and the spread and time rules for the stop-market swap. The check routine owns those.

The guard covers both tool namings: `mcp__plugin_personal-ai-trader_robinhood-trading__*` (the server this plugin
declares) and `mcp__robinhood-trading__*` (if you connected the Robinhood server yourself).

## Running the tests

From the plugin folder, with any Python 3.9+ that has pytest:

```
python3 -m pytest tests
python3 tools/trader_config.py --check          # in your workspace: is trader.json valid?
```

The tests run the guard as a subprocess exactly as Claude Code does, against fake workspaces, for every case
below plus every rule above.

## Proving the hook is live

Unit tests prove the script; this proves Claude Code is actually running it. Previews place nothing, so the
proof is safe in any phase. Ask Claude (or let `/trader-doctor` do it) to make these calls on your configured
account with real option ids from `get_option_instruments`:

| # | Call | Expected |
|---|---|---|
| 1 | `review_option_order`, quantity 1, debit, limit 0.90, two legs: buy/open on one put and sell/open on another of the same underlying | `order_guard: BLOCKED — option orders must have exactly one leg` |
| 2 | `review_option_order`, one leg sell/open | `BLOCKED — only buy-to-open and sell-to-close are allowed` |
| 3 | `review_option_order`, one leg buy/open, limit price within your cap (not in HALTED) | Reaches Robinhood and returns a preview |
| 4 | `get_portfolio` on an account number that is not yours | `BLOCKED — account is not the configured account` |
| 5 | Crypto preview | `BLOCKED — preview_crypto_order is never allowed` (or the tool is missing: the deny list removed it) |
| 6 | `review_option_order` buy costing more than the option cap | `BLOCKED — option buy costs $…, over the $… per-trade cap` |
| 7 | `review_equity_order` market buy | `BLOCKED — share buys must be limit orders` |
| 8 | `add_to_watchlist` on any list other than yours | `BLOCKED — only the '<name>' watchlist may be changed` |
| 9 | `review_equity_order` limit buy within the share cap | Reaches Robinhood |
| 10 | `get_trade_approval_setting` on your account | Reaches Robinhood |
| 11 | `review_advanced_order` side buy | `BLOCKED — advanced (OCO) orders are allowed only as sell-side exits` |
| 12 | `review_advanced_order` side sell on shares you hold | Reaches Robinhood |
| 13 | With an open live option trade in the ledger: `review_option_order` stop_market sell-to-close below entry × 1.25 | `BLOCKED — option stop_market stop … is below entry … x 1.25 = …` |
| 14 | In DRY_RUN: `place_option_order` for anything | `BLOCKED — phase is DRY_RUN: place_option_order is refused` |

**If case 1 returns a preview instead of `BLOCKED`, the hook is not running.** Stop trading (treat the session as
DRY_RUN) until it is fixed. If every Robinhood call returns `BLOCKED — could not read the tool call` or
`trading/trader.json is missing or invalid`, fix that first: the guard is failing closed.
