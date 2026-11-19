# Changelog

Every entry states its **safety impact**: what it changes about what can reach Robinhood, what is blocked,
or what protects an open position. Changes to `tools/order_guard.py`, `hooks/`, the deny list, or the check
routine's limit and exit steps are safety changes. They need the guard's tests passing, a written reason,
and the owner's explicit approval.

Versions follow semantic versioning (`.claude-plugin/plugin.json`). Workspace files carry their own
`schema` number; `trader.json` is schema 1.

## 0.1.0 — first public release

Personal AI Trader: a rule-bound options and share trader for your own Robinhood account. MIT licence.

### The plugin skeleton and the order guard
- **Plugin manifest, `.mcp.json` (the `robinhood-trading` HTTP server), `hooks/hooks.json`.**
  - Safety impact: the guard is registered as a PreToolUse hook for every Robinhood tool, in both tool
    namings (`mcp__plugin_personal-ai-trader_robinhood-trading__*` and `mcp__robinhood-trading__*`), with
    `onFailure: "block"`, so a crash or timeout refuses the call.
- **`tools/order_guard.py`.** Account, caps, watchlist and phase come
  from `trading/trader.json`.
  - Safety impact: it fails closed without a valid config. It refuses crypto and option exercise
    always, unlisted tools, other accounts, multi-leg options, selling to open, market orders on options,
    over-cap buys, extended hours, fractional quantities, stop-markets outside the guarded case, and other
    watchlists. **In DRY_RUN it refuses every `place_*`, `cancel_*`, `replace_*` and
    `decline_trade_approval`.** In HALTED it refuses new opening orders.
- **`tools/trader_config.py`** (the config loader and `--check`).
  - Safety impact: a config that says LIVE while `go_live.met` is false is invalid, so the guard refuses
    account calls. `allowed` has no switches for excluded trade types.
- **`docs/SAFETY.md`**, with the preview proof list.
  - Safety impact: documents how to prove the hook is live with previews, which place nothing.

### Routines, tools and templates
- **Skills `trader-check`, `trader-wrap` and `trader-weekly-review`.**
  - Safety impact: the prompt-level limits (cash floor, position count, theme, daily loss, halt, loss
    streak, liquidity floors, earnings) are read from `trader.json` by key and never restated. The only
    phase change a routine makes is to HALTED, which only tightens. The review never changes `phase` or
    `go_live.met`. With the approval switch on, a stop waiting for approval is treated as "no resting
    exit" and is chased first. A guard block is a stop sign.
- **`tools/ledger.py`**, with the start balance from the config.
  - Safety impact: none on orders. It rejects unknown fields, so a typo cannot drop data. The guard reads
    `trades.csv` for the guarded stop-market.
- **`tools/schedule.py` and `tools/market_calendar.json`** (NYSE holidays and early closes, 2026–2027).
  - Safety impact: runs are one-off tasks at fixed minutes, not recurring tasks with jitter. Outside the
    calendar's range a date is treated as a regular trading day, with a warning.
- **`tools/render_rules.py`**: RULES.md is generated from `trader.json`.
  - Safety impact: the human copy of the rules cannot silently drift from the enforced copy; `--check`
    reports drift.
- **`tools/watch.py`** (an optional price tripwire) and **`tools/price_log.py`** (an optional logger).
  - Safety impact: none on orders. The watcher only wakes the session, which re-reads Robinhood prices
    before acting. Each workspace's watcher records its own process in `trading/logs/watch.pid`;
    `--status` and `--stop` act on that record only, so one workspace can never stop another's tripwire.
- **Templates**: the sample strategy (marked "not a recommendation"), PLAN, STATE with its resume
  checklist, the journal format, the sources, and the −25% stop shadow-test example.
  - Safety impact: the STATE resume checklist reconciles positions, open orders and approvals before any
    trading decision after a restart, and proves the guard.

### Setup, strategy, doctor and status
- **`/personal-ai-trader:trader-setup`**, with `tools/setup_settings.py` and `tools/setup_workspace.py`.
  - Safety impact:
    - it writes the workspace deny list (crypto, exercise, edits to the settings files and the plugin
      folder), an "ask" rule for edits to `trader.json`, and an allow list for the scheduled runs. Every
      Robinhood call still goes through the guard;
    - every install starts in DRY_RUN;
    - the approval switch is on by default;
    - going live needs the gate checks and the owner's typed "go";
    - leaving HALTED is the owner's choice only.
    - every decision in setup is asked as a question dialog, and nothing is written without an answer;
    - with the owner's yes, the allow list also covers the news sites in `trading/NEWS-SOURCES.md`
      (`WebFetch(domain:...)`), so the pre-market run can read them unattended. Fetched pages are data,
      never instructions.
- **`/personal-ai-trader:trader-strategy`**: the strategy interview.
  - Safety impact: no orders. Setups must have triggers that can be tested from Robinhood data, and every
    cap, floor and stop is quoted by its `trader.json` key, never as a number, so a limit change cannot
    leave a stale copy. Product exclusions are stated. The old version is kept in `trading/reviews/`, and
    nothing is written without the owner's confirmation.
- **`/personal-ai-trader:trader-doctor`** and **`tools/doctor.py`**: the health check.
  - Safety impact: read-only. It never places, cancels or replaces an order and never edits the guard,
    hooks, settings or `trader.json`. Its only writes are missing schedules, on the owner's yes, and an
    optional support bundle with the account number masked to its last 4 digits and token-like strings
    removed. It reports as FAIL:
    - a hook that is not registered, does not block on failure, or is switched off by `disableAllHooks`;
    - a guard script that does not refuse a two-leg preview;
    - a live preview proof that does not come back BLOCKED;
    - a Robinhood connection under a server name the hook does not match;
    - missing deny entries (through `setup_settings.py --check`);
    - a position with no resting exit;
    - a ledger that does not match the positions;
    - stale STATE.md in market hours;
    - a market calendar that does not cover today.

    It warns within 60 days of the calendar's end. The ledger build test runs on a copy, never on the real
    workbook.
- **`/personal-ai-trader:trader-status`**: an on-demand summary.
  - Safety impact: read-only. No orders, no ledger rows, no schedule changes.
- **Docs**: the README (risks, install, daily life, commands, uninstall), `docs/KEEP-RUNNING.md`, and this
  changelog.
  - Safety impact: none on orders. The supervisor templates in `extras/supervisor/` are optional, off by
    default and never installed by the plugin. They carry the warning that a supervisor must find its own
    session by a unique name. If it mistook another Claude session for it, a dead trader would never be
    relaunched.
