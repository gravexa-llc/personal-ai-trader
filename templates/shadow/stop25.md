# Stop-loss shadow test — −25% vs −50% of premium (worked example)

**Question:** would a stop at −25% of the premium do better than the −50% default (RULES.md section 8, `options.default_stop_pct`)? Live trading continues unchanged (−50% default, ratchet past the trigger) until the review.
**Asked by:** `<owner>` on `<YYYY-MM-DD>`. **Review:** Sunday review of `<YYYY-MM-DD>` (two to four weeks out).

## Method
- **Which trades:** every live option trade from the start of the test, and every "would enter" row of any setup being shadowed. Shares are excluded (their stops are price-based).
- **Judged on the bid**, like the live rule. The −25% stop is hit the first time a check, a watcher wake or the closing mark sees the bid at or below **entry × 0.75**. Between observations a lower intraday bid can be missed — the same blind spot the live stop has; note it when option historicals show one.
- **P/L with −25% stop:** the result if sold at entry × 0.75 on the day it was hit (contracts × 100 × (0.75 × entry − entry), minus fees); if never hit, the same as the actual result.
- **P/L actual:** what the trade did under the live rules (−50% stop, ratchet).
- **Once the bid reaches the ratchet trigger (+50% by default) the ratchet takes over in both versions,** so the two paths can only differ before that point. Record the lowest bid seen before the trigger.
- **Back-fills** of trades opened before the test use only recorded data: the lowest bid in the journal, or `get_option_historicals` (which has no bid history — say which was used).
- Junk quotes (outside regular hours, a one-off wide print) do not count; say so in the note.

## Reading the result at the review
- Total P/L under each version, and the difference.
- How many trades the −25% stop would have closed that went on to recover (stopped out of a winner), and how many it would have saved from a deeper loss.
- With fewer than about five trades that differ, extend the test or drop it; it is not an answer yet.

## Table
| Trade | Setup | Entry | −25% level | −50% level | Lowest bid before trigger (date, source) | −25% hit? (when) | P/L with −25% stop | P/L actual (−50% / ratchet) | Note |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

How to fill a row (illustration only, not a real trade): entry 2.00 → −25% level 1.50, −50% level 1.00; lowest bid before +50% was 1.60 at a check, so the −25% stop was not hit and both columns show the actual result.
