# Shadow tests — trying a rule change without trading it

A shadow test runs a proposed rule beside the live rules **on paper only**, so you can see what it would have done before you decide to adopt it. Live trading is not changed while a test runs.

## When to use one
- You want to change a limit or an exit (a tighter stop, a different ratchet), or add a setup, and you are not sure it is better.
- A review raised a "what if we had…" question that your own trades can answer over a few weeks.

## How to run one
1. **Write the question** at the top of a new file in `trading/shadow/` (one file per test, e.g. `stop25.md`): the rule as it is, the rule proposed, who asked and when, and the review date when you will judge it (usually two to four Sunday reviews later).
2. **Say how it is judged.** Use the same yardstick as the live rule (for option stops: the **bid**, as RULES.md does). Say which observations count (scheduled checks, watcher wakes, the closing mark) and what is missed between them.
3. **Say what gets a row.** Usually: every live trade, plus every "would enter" signal of a setup being shadowed. Decide up front, so the sample is not picked after the fact.
4. **Keep one table**, one row per trade, filled in by the routines as the trade runs: entry, the levels of both versions, the observations that matter, and the P/L under each version. Back-fill past trades only from recorded data (journal bids, `get_option_historicals`) and say so in the row.
5. **Judge it at the review date.** Total P/L under each version, how many trades differed, and the largest single difference. A test with very few differing trades is not an answer: extend it or drop it.
6. **Decide.** Only the owner changes a rule. If adopted, change `trader.json` (or STRATEGY.md for a setup), regenerate RULES.md, and note the decision at the top of the shadow file. Keep the file as the record.

## Rules for every shadow test
- It never places, changes or cancels an order. It only writes rows.
- It never changes the live rule while it runs.
- Unfilled cells stay empty; a guess is not data.
- Every number in a row says where it came from (check time, journal entry, historicals).

`stop25.md` is a worked example: a −25% premium stop tested against the −50% default.
