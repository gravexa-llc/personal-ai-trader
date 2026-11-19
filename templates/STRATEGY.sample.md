> **SAMPLE — an example of the format, not a recommendation. Replace it with /trader-strategy.**
> Every level, date and name below is a placeholder (`<like this>`) or an illustration. Nothing here is advice.

# Strategy — current view and what is in play

Version `<n>`, written `<YYYY-MM-DD>` (Sunday review) for the week of `<Mon date>`–`<Fri date>`. Rewritten every Sunday review; amended mid-week in the "Amendments" section at the bottom, with a date.
The previous version is kept at `reviews/STRATEGY-v<n-1>-<YYYY-MM-DD>.md`; the week is scored in `reviews/<YYYY-MM-DD>.md`.

## Market read (as of the `<date>` close)
- One or two lines on the tape: trend, rotation, what the market is worried about. `<e.g. tech correcting, money rotating to defensives and energy>`
- **Below both** the 20- and 50-day: `<SYMBOL> <last> (<20-day> / <50-day>)`, ...
- **Above both:** `<SYMBOL> <last> (<20-day> / <50-day>)`, ...
- **Between:** `<SYMBOL> <last> (<20-day> / <50-day>)`.
- Rates and macro: policy rate `<range>`, next decision `<date>`, 10-year `<yield>`, VIX `<level>`, oil `<level>`.
- Sources: Robinhood quotes and indicators; `<news sources used, see NEWS-SOURCES.md>`.

## Calendar that matters (local time)
| Date | Event |
|---|---|
| `<Mon date>` | `<data release and time>`; `<companies reporting before / after>` |
| `<Tue date>` | ... |
| `<Wed date>` | ... |
| `<Thu date>` | ... |
| `<Fri date>` | ... |
| Next | `<big events in the following weeks>` |

## Sizing
- Caps (RULES.md): option `<option cap>` a trade, shares `<share cap>`; cash ≥ `<cash floor>`; at most `<max positions>` open, at most `<max same theme>` per theme.
- Default size (judgement, inside the caps): full-strength setup `<$ range>`; ordinary `<$ range>`. At most `<n>` new entries a day.
- Deployment guide, not a rule: `<e.g. 30–60%>` invested while trends are clear; less when the read is mixed or the week is full of events.
- Expiry and strike choice for the current volatility: `<e.g. VIX near 20 — buy 35–50 days out; where near-the-money contracts cost more than the cap, use a cheaper related ETF or a strike that still passes the liquidity floors>`.

## Setups in play
Every entry must name one of these setups; the check routine refuses an entry that does not.

1. **Trend continuation (calls).** The underlying is above its 20- and 50-day averages and pulls back to the 20-day or a prior breakout level and holds it. Delta `<0.40–0.55>`. Candidates: `<SYMBOL>` near `<level>` (20-day `<level>`), `<SYMBOL>` `<level range>` (not before `<event>`).
2. **Breakdown (puts).**
   - **2b:** below both averages, a rally back to a level that fails on the hourly close. Levels: `<SYMBOL>` `<prior high>` / `<prior close>` / `<50-day>`; `<SYMBOL>` `<level>` / `<level>`.
   - Further breakdown variants are run as shadow tests before they trade (see `shadow/README.md`).
3. **Post-earnings follow-through (stricter test).** Day one (the first session after the report) closes in the top third of its range for a long (bottom third for a short); on day two the stock holds beyond the day-one close through the first 90 minutes of the session. Ordinary size while the setup is unproven. This week: `<SYMBOL>` (day two `<date>`, holds above `<day-one close>`), `<SYMBOL>` ... — at most one per theme.
4. **Shares instead of options** when the view is sound but no contract passes the liquidity floors or fits the cap. Exit with one OCO order (target and stop together).

### Not in play
- Any single-name option held through its own earnings (unless the trade is an explicit earnings thesis).
- Anything expiring inside `<expiry_days_min>` days.
- Chasing a name up more than `<5>`% on the day.
- A level that failed the day before, until a full session closes back beyond it.
- A rate-sensitive short-horizon trade against that morning's 10-year move.
- New rate-dependent positions before a scheduled release that morning (`<e.g. jobs report, CPI, rate decision>`).

## Exits
- RULES.md sections 8–10: stop at `<−default stop>` on the bid until the bid reaches `<+ratchet trigger>`; then the stop locks `<+lock gain>` and trails `<trail>` below the best bid since entry, raised at every check, never lowered. Past the trigger, with a tight spread, the stop rests as a day-only stop-market from the first check to the pre-close check (if switched on).
- Overnight cushion: at pre-close the GTC stop-limit rests at ratchet stop × `<cushion>`; raised back at the first check after the open.
- The resting option stop-limit triggers on trades, not the bid: every check compares the bid with the stop and closes by hand if the bid is through it (cancel the resting stop first).
- Thesis stops per position (e.g. "exit on an hourly close above `<level>`") are written in the ledger at entry.

## Posture this week
- `<e.g. all cash; mixed tape — aim for balance: one long from the groups above their averages, at most one short in the weak group>`.
- Event risk: `<the week's big events and how they change sizing or timing>`.

## Watchlist to screen at prep
Broad: `<SPY, QQQ, IWM>`. Sectors: `<sector ETFs>`. Rates and metals: `<TLT, GLD>`. Commodities: `<USO>`.
Single names: `<the names you follow>`; this week's reporters `<SYMBOLS>`.
Add scanner finds each morning and Robinhood's "Daily movers" and "Upcoming earnings" lists.
Indicators: `get_equity_technical_indicators` with `symbols` (up to 10 per call) and `output: latest`.

## Open positions
- `<trade id>: <quantity> <SYMBOL> <expiry> <strike> <call/put> from <entry> (<date>). Resting stop <stop> / <limit>. Ratchet starts at <entry × (1 + trigger)>. Thesis stop: <condition>. Out by <date>.>` — or "None."

## Amendments
- `<YYYY-MM-DD>: <what changed mid-week and why>`
