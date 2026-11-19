# Journal entry format

One file per trading day: `trading/journal/YYYY-MM-DD.md`, starting with `# Journal — <Weekday> YYYY-MM-DD`. Every run appends one entry, even when it did nothing — "no action, because …" is the most common and most useful line. Times are local, from `date`. Prices are Robinhood's; say where any other number came from.

## Check entry (prep, hourly, preclose)

```
## HH:MM <prep|hourly|preclose>
- Account: value, cash, day change (and Robinhood's own total if it differs from the bid-marked value)
- Approvals: switch on/off (first run of the day); pending approvals and what happened to them
- Positions: one line each — mark (bid), P/L, best bid since entry, distance to stop and target, resting exit confirmed (or waiting for approval)
- Market: one or two lines
- Decision: what was done (orders with ids, ledger rows), or "no action" and why
- Shadow: rows added or updated (if any shadow test is running)
- Next: anything the next check must look at
```

A `prep` entry adds:

```
- Schedules: CronList against schedule.py for today — present / re-created
- News: sources read (NEWS-SOURCES.md) and the headlines that matter
- Rates / vol, pre-market, calendar, earnings, scanner
### Plan and shortlist
- <named setup>: <name> — trigger, level, what would cancel it
- Posture: how many entries at most today, and why
```

An entry that opens a trade names the setup, the floors it passed (days to expiry, open interest, spread as % of mid, cost against the cap), cash after the trade, the preview result, the order and fill, the protective stop (order id, or the approval id it is waiting on) and the alerts and watcher levels set.

## Closing mark

```
## HH:MM closing mark
- <trade id> <contract>: bid / ask / mark
```

## Wrap entry

```
## HH:MM wrap
- Day: trades opened and closed with P/L; account value and day change; drawdown from the start balance
- Approvals: sent / approved / declined / expired today; any exit still waiting (first)
- Rules: any breach, any guard block, any limit that stopped an entry
- Worked / did not work
- Schedules: next trading day's runs and the Sunday review — created / already present (ids in STATE.md)
- Tomorrow's prep must look at: earnings, data releases, positions near a level, anything waiting for the owner
```

## Example (illustrative only — invented names and prices)

```
## 09:33 hourly
- Account: 5,112.40 on the bid (cash 4,702.40 + P0002 bid 4.10 = 410); day −10.04 (spread plus fee).
- Approvals: none pending.
- Positions: P0002 XYZ Jan 16 50 put — bid 4.10 / ask 4.30, −2.4%, best bid 4.10; stop 2.10 far.
- Market: index retested the prior day's high and the hourly bar closed back below it.
- Decision: PAPER entry P0002, setup 2 (failed retest). 39 days, OI 2,310, spread 4.8% of mid, cost 420 under the cap; cash after 92%. Preview: no order checks. Paper fill at the ask 4.20. Paper stop 2.10 recorded in STATE.md; alerts set; watcher restarted.
- Next: P0002 bid vs stop; the next hourly close against the level.
```
