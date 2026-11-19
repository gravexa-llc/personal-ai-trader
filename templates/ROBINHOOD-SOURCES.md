# Robinhood information sources — what each tool is good for

What earlier users of this setup learned about each Robinhood tool, as a starting point. Re-test anything that matters to you: Robinhood changes its tools, and your account may behave differently. Keep this file current — add your own evidence when you first use a source or change a verdict, and review it at Sunday reviews.

**Verdict scale:** Keep (used in a routine) · Situational (useful for specific cases) · Drop (tried, adds nothing) · Untested.
A verdict needs evidence: what it was used for and whether it changed a decision. Add yours in the last column.

## Market data
| Source | Tool | Verdict | What we learned | Your notes |
|---|---|---|---|---|
| Stock and ETF quotes | `get_equity_quotes` | Keep | Every check. Up to 20 symbols per call, with the prior close. | |
| Index levels | `get_indexes`, `get_index_quotes` | Keep | S&P 500, Nasdaq-100 and VIX work. Russell 2000 is not available — use IWM. Index ids: VIX `3b912aa2-88f9-4682-8ae3-e39520bdf4db`, S&P 500 `432fbbb8-b82c-454a-852d-eb85382c7066`, Nasdaq-100 `50c298f7-27a8-44a1-b049-ec153cf2892f` (confirm with `search`, `asset_type: market_index`). | |
| Option quotes | `get_option_quotes` | Keep | Bid, ask, delta, implied volatility, open interest, volume, chance of profit: everything the liquidity floors need. | |
| Option chains and contracts | `get_option_chains`, `get_option_instruments` | Keep | Needed for contract ids. Pass the underlying (`chain_symbol`), one expiry and call/put with no strike to get the whole expiry in one call, then `get_option_quotes` on the few strikes of interest. Heavily listed names return many rows. | |
| Stock price history | `get_equity_historicals` | Keep | One call returns daily bars for several symbols; compute 20- and 50-day averages locally. Responses are large: ask only for the range needed. A daily-interval request starting today returns an error ("end must be after start"). **Hour bars start at :00**, so bars started at the 09:30 ET open leave out the first half hour; for same-day ranges use 5-minute bars. | |
| Index price history | `get_index_historicals` | Keep | A month of daily VIX and S&P 500 bars in one call, for the weekly read. Interval is required. Weekend bars come back flagged `interpolated` — ignore them. | |
| Option price history | `get_option_historicals` | Situational | Hourly bars for a held contract since entry (no volume, no bid history). Useful for sizing a stop distance against the contract's normal hourly range, and for reviews; not needed in routine checks. | |
| Bid and ask depth | `get_equity_price_book` | Drop (ETFs) | Market-maker ladders only on liquid ETFs; might matter for a thin single name. | |
| Whether a symbol can be traded | `get_equity_tradability` | Situational | Adds nothing for big ETFs; use once before a first entry on an unfamiliar single name or after a halt. | |

## Research
| Source | Tool | Verdict | What we learned | Your notes |
|---|---|---|---|---|
| Technical indicators | `get_equity_technical_indicators` | Keep | Pass `symbols` (up to 10 per call) with `output: latest`; the older `symbol` parameter is deprecated and has been rejected. One indicator per call. Keep price history as the fallback if the tool misbehaves. | |
| Earnings calendar | `get_earnings_calendar` | Keep | Builds the event table for the week. Noisy (small and foreign names, duplicates, dates flagged unverified); filter with `high_market_cap`. **A 14-day window can be too large to return inline** — use 7-day windows, or query the saved output with `jq`. | |
| Earnings results for one company | `get_earnings_results` | Keep | Actual vs estimate and the next report date in one call. Dates flagged unverified need a second source before a trade depends on them. | |
| Fundamentals | `get_equity_fundamentals` | Situational | Average volume (to spot unusual volume), 52-week high and low with dates, ex-dividend date. Valuation ratios do not bear on swing trades. Use when a name first reaches the shortlist. | |
| Financial statements | `get_financials` | Situational | Several quarters of revenue, margins and net income in one call; backs a single-name thesis with numbers. Nothing for ETFs. | |
| Analyst ratings | `get_equity_analyst_ratings` | Drop | Only current counts and targets, no history, so a change cannot be timed against a move. | |
| SEC filings | `get_sec_filing_index`, `get_sec_filing`, `get_sec_filing_facts`, `get_sec_filing_facts_catalog` | Situational | The index lists 8-Ks, 10-Qs, insider Form 4s and offering prospectuses (424B) on the day they are filed, often before readable news. Prospectus text is not served by `get_sec_filing` (404); read 424Bs elsewhere. | |
| Politician trades | `get_politician_trades` | Drop | Disclosed weeks after the trade; no use for trades held days to weeks. | |
| Symbol search | `search` | Situational | Use once to find an index id (`asset_type: market_index`); tickers need no search. | |

## Discovery
| Source | Tool | Verdict | What we learned | Your notes |
|---|---|---|---|---|
| Scanner preview (nothing saved) | `preview_scan`, `get_scanner_filter_specs`, `get_scanner_datapoints` | Keep | Finds liquid movers outside the watchlist. Filters cover price, % change, gap, relative volume, options volume and open interest, IV, RSI, averages, sector, market cap, earnings date. **Percent values are decimals (0.03 = 3%).** Filters with intervals need `interval` (use `1d`). Expression filters work without looking up names, e.g. relative volume `dayVolume / volumeAvg(candleCount=30, candlePeriod="1d", session="all")`, price `tradeAllDay.price > 20`, options activity `optionsCallDayVolume + optionsPutDayVolume`. Run after the open for relative volume: a pre-market run mostly returns the previous session's volume. `session="regular"` works once the session is open; `closePrev` is not a datapoint (use `changeFromPrevDayRatio`). A price floor also cuts leveraged ETFs and sub-$1 names. | |
| Saved scanners | `get_scans`, `run_scan`, `create_scan` | Not used | Creating one writes to the account; the order guard keeps saved scanners read-only. | |
| Watchlists | `get_watchlists`, `get_watchlist_items`, `get_popular_watchlists`, `get_option_watchlist` | Situational | Curated lists worth a look at prep: "Daily movers", "Trending stocks", "Upcoming earnings". Items of "Daily movers" come back without prices (the scanner does this job better). Sector lists are too large to use. The guard allows writes only to your one configured watchlist. | |

## Account and records
| Source | Tool | Verdict | What we learned | Your notes |
|---|---|---|---|---|
| Accounts and buying power | `get_accounts`, `get_portfolio` | Keep | Go-live gate checks (option level, `agentic_allowed`, cash). | |
| Positions | `get_equity_positions`, `get_option_positions` | Keep | The reconcile source against the ledger. | |
| Order history | `get_equity_orders`, `get_option_orders`, `get_advanced_orders` | Keep | Confirms fills and that resting stops are still `confirmed`. `state` and `created_at_gte` filters work; a stop-limit shows as type `limit` with trigger `stop`. `get_advanced_orders` rejects a date-only `created_at_gte` — pass a full timestamp (`YYYY-MM-DDT00:00:00Z`); its `order_id` filter returns the legs hydrated. | |
| Realized P/L and trade history | `get_realized_pnl`, `get_pnl_trade_history` | Keep (Sunday reconcile) | Matches the ledger except for fees, which Robinhood leaves out (and option P/L is rounded to whole dollars). `get_realized_pnl` takes start/end dates; `get_pnl_trade_history` with `span=week` lists each closing trade. | |
| Tax lots | `get_equity_tax_lots` | Untested | Only relevant when shares are held. | |

## Actions (tracked so you know what is proven)
| Capability | Tool | Status | What we learned | Your notes |
|---|---|---|---|---|
| Trade-approval switch | `get_trade_approval_setting`, `get_trade_approvals`, `decline_trade_approval` | Works (reads) | `human_must_approve_trades` shows the switch; only the owner changes it, in the app. While on, every order — including a protective stop after an entry fills — waits for approval. Read it at the start of every live day. | |
| Order preview | `review_option_order`, `review_equity_order`, `review_advanced_order` | Works | Returns the live quote, broker alerts, collateral and fees (about $0.04 per option contract). Places nothing, so it is also how the order guard is proven. | |
| Order placement and cancel | `place_*`, `cancel_*` | Works | Limit orders at the ask on liquid contracts fill in well under a second. A cancel returns `accepted: true`; confirm with the order history by `order_id` before placing the replacement. | |
| Resting option stops | `place_option_order` with `stop_limit` / `stop_market` | Works | A GTC stop-limit stays `confirmed` overnight. It commits every contract (`pending_sell_quantity`), so **a stop and a target cannot rest together** on one position: cancel the stop, confirm, sell, then rest a new stop for what is left. **Option prices must sit on the tick: 0.05 above $3.00** on the contracts tested — e.g. a 6.88 stop is rejected ("does not satisfy the min tick value"); below $3 the tick is usually 0.01 for penny-class contracts. Check the contract's tick sizes (`min_ticks` on the instrument, if returned) before pricing a stop. **Option stop-market orders are refused in the first 15 minutes of the session** (OPTION_STOP_MARKET_INVALID_TIME_MARKET_OPEN) and are day-only. A stop-market preview on a wide quote can return OPTION_STOP_MARKET_WIDE_BID_ASK_SPREAD — a warning, not a refusal; RULES.md's spread limit decides. On volatile days spreads inside that limit may last only an hour or two. An overnight stop-limit near the market can fill on the opening print, in the widest spread of the day — the reason for the overnight cushion. | |
| OCO exit on shares | `review_advanced_order`, `place_advanced_order`, `cancel_advanced_order` | Works | Target and stop rest together on shares; the stop leg is always stop-market; regular hours; sell side only (the guard). When one leg fills the other is cancelled automatically. | |
| Price alerts | `create_alert`, `get_alerts`, `get_alert_log` | Works, with a limit | Alerts watch the stock price, not an option's premium, and they notify the owner's phone — they do **not** reach the session. Read `get_alert_log` at each check instead. | |
| Options upgrade link | `get_option_level_upgrade_info` | Works | Gives the link to request a higher option level. | |

Never used: crypto tools, option exercise (the guard refuses them).

## Outside Robinhood (the process depends on them)
| Source | Used by | Verdict | What we learned | Your notes |
|---|---|---|---|---|
| Yahoo Finance public chart feed | `tools/watch.py` (5-minute tripwire) | Keep, as a tripwire only | Real time on liquid ETFs, matching Robinhood to the cent in spot checks. Unofficial; the script rejects quotes older than 3 minutes. Re-check on Robinhood before acting. | |
| CBOE delayed option quotes | `tools/price_log.py` (optional) | Keep (logging only) | Public JSON, no login; a full chain in one call. About 15 minutes delayed in the session; carries last-trade times so rows can be lined up with Robinhood quotes. Record only — never traded on. | |
| Web-search summaries | prep and checks | Keep, with care | Headline level only; summaries can conflict or misreport. Read the article before a claim drives a trade. | |
