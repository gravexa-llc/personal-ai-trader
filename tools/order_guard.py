#!/usr/bin/env python3
"""Personal AI Trader order guard: a PreToolUse hook in front of every Robinhood tool (docs/SAFETY.md).

Registered by the plugin's hooks/hooks.json for both tool namings:
  mcp__plugin_<plugin>_robinhood-trading__<tool>   the server the plugin's own .mcp.json declares
  mcp__robinhood-trading__<tool>                   a server the user connected themselves
Claude Code passes the pending call as JSON on stdin ({"tool_name": ..., "tool_input": {...}}). Exit 2 blocks
the call and shows stderr ("order_guard: BLOCKED — <reason>") to the model; exit 0 lets the normal permission
flow continue. The hook is registered with onFailure "block", so a crash or timeout also blocks. Previews
(review_*) are guarded like placements, so a forbidden trade is refused before it is even simulated, and the
guard can be proven live with a preview, which places nothing.

Everything user-specific comes from the workspace's trading/trader.json (tools/trader_config.py): account
number, caps, watchlist name and id, phase, the stop-market rule. The ledger is <workspace>/trading/ledger/trades.csv.

Layers, in the order they are applied:
  1. Input. Unreadable stdin or tool_input is refused (fails closed). Tools that are not Robinhood pass.
  2. Never reachable: crypto preview / place / cancel, crypto sign-up info, option exercise and its cancel.
  3. Allow-list. A Robinhood tool not listed below is refused until the owner decides on it.
  4. Pure reads that name no account pass without a config, so the setup and the doctor can look around.
  5. Config. Anything else needs a valid trader.json; if it is missing, unreadable or invalid, the call is
     refused. Reads pass, anything that could change account state or names an account is refused.
  6. Phase.
       DRY_RUN  refuses every place_*, cancel_*, replace_* and decline_trade_approval. Previews and reads pass.
       HALTED   refuses new opening orders, both preview and placement (option buy-to-open, share buys).
                Closing orders, every cancel (the guard cannot tell a protective order from an entry, and a
                cancel never opens risk), declining an approval, and reads pass.
       LIVE     the order checks below apply. (The config loader refuses LIVE unless go_live.met is true.)
  7. Account. Every tool that takes an account number is refused for any account but the configured one.
  8. Orders.
       options: exactly one leg, ratio 1; only buy-to-open (limit) or sell-to-close (limit or stop-limit);
         regular hours; whole contracts (a fractional quantity is refused); a buy costs at most limits.option_cap_dollars (price x 100 x contracts); buys need
         allowed.long_options.
       option stop-market sells: only to protect a gain, when options.stop_market_after_gain.enabled — day
         order (gfd), sell-to-close, the contract matches exactly one open live trade in the ledger by
         option_id, no more contracts than that trade holds, and the stop at least
         entry x (1 + options.ratchet.lock_gain_pct / 100). Plain market orders on options are refused.
       shares: buys are limit orders costing at most limits.share_cap_dollars and need allowed.shares; sells
         are limit, stop-limit or stop-market; regular hours; whole shares; no dollar-amount orders.
       advanced (OCO) orders on shares: sell side only, regular hours.
  9. Watchlists: only the single list named watchlist.name may be created, and only the list with id
     watchlist.id may be changed (not renamed, no crypto). Saved scanners, the options watchlist and
     followed lists are read-only.

It cannot check anything that needs account state (cash floor, position count, whether shares being sold are
held, the spread and time rules for the stop-market swap); those stay with the check routine and the ledger.
"""
import csv
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

SERVER = "robinhood-trading"
# Both namings; mirrors the matcher in hooks/hooks.json. Greedy, so the tool is what follows the last
# "robinhood-trading__".
TOOL_NAME = re.compile(r"^mcp__(?:plugin_.+_)?" + re.escape(SERVER) + r"__(?P<tool>.+)$")

NEVER = {
    "preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
    "get_crypto_account_onboarding_info", "exercise_option", "cancel_option_exercise",
}
# Read-only tools that take no account number. They pass even without a config.
OPEN_READS = {
    "get_accounts", "get_equity_quotes", "get_equity_historicals", "get_equity_fundamentals",
    "get_equity_technical_indicators", "get_equity_price_book", "get_equity_analyst_ratings",
    "get_financials", "get_earnings_calendar", "get_earnings_results", "get_indexes", "get_index_quotes",
    "get_index_historicals", "get_option_chains", "get_option_instruments", "get_option_quotes",
    "get_option_historicals", "get_sec_filing_index", "get_sec_filing", "get_sec_filing_facts",
    "get_sec_filing_facts_catalog", "get_politician_trades", "search", "get_scanner_filter_specs",
    "get_scanner_datapoints", "preview_scan", "get_scans", "run_scan", "get_popular_watchlists",
    "get_watchlists", "get_watchlist_items", "get_option_watchlist", "get_currency_pairs", "get_crypto_quotes",
    "get_alerts", "get_alert_log",
}
# Alert writes take no account number but change what the account pushes; they need a valid config.
ALERT_WRITES = {"mark_alerts_read", "create_alert", "update_alert", "delete_alert"}
# Tools that name an account: allowed for the configured account only.
ACCOUNT_TOOLS = {
    "get_portfolio", "get_equity_positions", "get_option_positions", "get_crypto_positions",
    "get_equity_orders", "get_option_orders", "get_crypto_orders", "get_advanced_orders",
    "get_equity_tax_lots", "get_equity_tradability", "get_realized_pnl", "get_pnl_trade_history",
    "get_trade_approvals", "get_trade_approval_setting", "decline_trade_approval",
    "get_option_level_upgrade_info", "get_limited_margin_upgrade_info",
    "cancel_option_order", "cancel_equity_order", "cancel_advanced_order",
}
ORDER_TOOLS = {
    "review_option_order", "place_option_order", "review_equity_order", "place_equity_order",
    "review_advanced_order", "place_advanced_order",
}
WATCHLIST_WRITES = {"create_watchlist", "update_watchlist", "add_to_watchlist", "remove_from_watchlist"}
ALLOWED_LEGS = {("buy", "open"), ("sell", "close")}
DRY_RUN_REFUSED_PREFIXES = ("place_", "cancel_", "replace_")
DRY_RUN_REFUSED = {"decline_trade_approval"}


def block(reason):
    print("order_guard: BLOCKED — " + reason, file=sys.stderr)
    sys.exit(2)


def number(value, what):
    if isinstance(value, bool):
        block("%s is not a number (%r)" % (what, value))
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        block("%s is missing or not a number (%r)" % (what, value))
    if not n.is_finite() or n <= 0:
        block("%s must be a positive number (%r)" % (what, value))
    return n


def robinhood_tool(tool_name):
    """The bare Robinhood tool name, or None if this is not a Robinhood tool."""
    m = TOOL_NAME.match(tool_name)
    return m.group("tool") if m else None


def load_config():
    """The validated config, or a block naming the problem (fails closed)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import trader_config
    except Exception as e:  # the plugin is broken: refuse
        block("cannot load tools/trader_config.py (%s); only read-only tools are allowed" % e)
    try:
        return trader_config.load()
    except trader_config.ConfigError as e:
        block("trading/trader.json is missing or invalid (%s: %s); only read-only tools are allowed "
              "until it is fixed (check it with tools/trader_config.py --check)" % (e.path, "; ".join(e.problems)))


def check_phase(cfg, name):
    if cfg.phase == "DRY_RUN" and (name.startswith(DRY_RUN_REFUSED_PREFIXES) or name in DRY_RUN_REFUSED):
        block("phase is DRY_RUN: %s is refused (previews and reads are allowed; record paper trades in the "
              "ledger instead)" % name)
    if cfg.phase not in ("DRY_RUN", "LIVE", "HALTED"):
        block("unknown phase %r" % cfg.phase)


def refuse_opening_if_halted(cfg, what):
    if cfg.phase == "HALTED":
        block("phase is HALTED: no new opening orders (%s); closing orders, cancels and reads are allowed" % what)


def check_account(cfg, args):
    given = [str(args[k]) for k in ("account_number", "rhs_account_number") if args.get(k) not in (None, "")]
    if not given:
        block("no account number given; only the configured account (%s) is allowed" % cfg.account.nickname)
    if any(g != cfg.account.number for g in given):
        block("account is not the configured account (%s)" % cfg.account.nickname)


def check_whole_contracts(value):
    """Option quantities are whole contracts. Only a non-integer quantity is refused here; a missing or
    non-positive one is left to the checks that already handle it (buys and guarded stop-markets)."""
    if value is None:
        return
    try:
        n = Decimal(str(value)) if not isinstance(value, bool) else None
    except (InvalidOperation, ValueError):
        n = None
    if n is None or not n.is_finite() or n != n.to_integral_value():
        block("option quantity must be a whole number of contracts; got %r" % (value,))


def check_option_order(cfg, args):
    legs = args.get("legs")
    if not isinstance(legs, list) or len(legs) != 1:
        block("option orders must have exactly one leg; multi-leg trades are not allowed")
    leg = legs[0] if isinstance(legs[0], dict) else {}
    effect = (leg.get("side"), leg.get("position_effect"))
    if effect not in ALLOWED_LEGS:
        block("only buy-to-open and sell-to-close are allowed; got %s / %s" % effect)
    if leg.get("ratio_quantity", 1) != 1:
        block("ratio quantity must be 1")
    if args.get("market_hours", "regular_hours") != "regular_hours":
        block("option orders must use regular_hours")
    check_whole_contracts(args.get("quantity"))
    order_type = args.get("type", "limit")
    if effect == ("buy", "open"):
        refuse_opening_if_halted(cfg, "option buy-to-open")
        if not cfg.allowed.long_options:
            block("buying options is switched off in trader.json (allowed.long_options)")
        if order_type != "limit":
            block("option buys must be limit orders; got %r" % order_type)
        cap = cfg.limits.option_cap_dollars
        cost = number(args.get("price"), "limit price") * 100 * number(args.get("quantity"), "quantity")
        if cost > cap:
            block("option buy costs $%s, over the $%s per-trade cap" % (cost, cap))
    elif order_type == "stop_market":
        check_option_stop_market(cfg, args, leg)
    elif order_type not in ("limit", "stop_limit"):
        block("option sell type %r is not allowed; use limit, stop_limit, or a guarded stop_market" % order_type)


def check_option_stop_market(cfg, args, leg):
    rule = cfg.options.stop_market_after_gain
    if not rule.enabled:
        block("option stop_market is switched off in trader.json (options.stop_market_after_gain.enabled)")
    if args.get("time_in_force", "gfd") != "gfd":
        block("option stop_market must be a day order (gfd)")
    stop = number(args.get("stop_price"), "stop price")
    quantity = number(args.get("quantity"), "quantity")
    option_id = leg.get("option_id")
    try:
        with open(cfg.trades_path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("mode") == "live" and r.get("status") == "open"
                    and option_id and r.get("option_id") == option_id]
    except (OSError, UnicodeDecodeError, csv.Error):
        rows = []
    if len(rows) != 1:
        block("option stop_market needs exactly one open live trade with this option_id in ledger/trades.csv")
    entry = number(rows[0].get("entry_price"), "ledger entry price")
    if quantity > number(rows[0].get("quantity"), "ledger quantity"):
        block("option stop_market quantity %s is more than the %s held" % (quantity, rows[0].get("quantity")))
    multiple = cfg.stop_market_min_multiple
    if stop < entry * multiple:
        block("option stop_market stop %s is below entry %s x %s = %s; only a gain may be protected this way"
              % (stop, entry, multiple, entry * multiple))


def check_equity_order(cfg, args):
    if args.get("dollar_amount") not in (None, ""):
        block("dollar-amount share orders are not allowed; give a whole-share quantity")
    quantity = number(args.get("quantity"), "quantity")
    if quantity != quantity.to_integral_value():
        block("fractional share orders are not allowed")
    if args.get("market_hours", "regular_hours") != "regular_hours":
        block("share orders must use regular_hours")
    side, order_type = args.get("side"), args.get("type")
    if side == "buy":
        refuse_opening_if_halted(cfg, "share buy")
        if not cfg.allowed.shares:
            block("buying shares is switched off in trader.json (allowed.shares)")
        if order_type != "limit":
            block("share buys must be limit orders; got %r" % order_type)
        cap = cfg.limits.share_cap_dollars
        cost = number(args.get("limit_price"), "limit price") * quantity
        if cost > cap:
            block("share buy costs $%s, over the $%s per-position cap" % (cost, cap))
    elif side == "sell":
        if order_type not in ("limit", "stop_limit", "stop_market"):
            block("share sell type %r is not allowed; use limit, stop_limit or stop_market" % order_type)
    else:
        block("share order side must be buy or sell; got %r" % side)


def check_advanced_order(cfg, args):
    # OCO exit on shares: a take-profit limit and a stop-loss resting together. Sell side only —
    # a buy-side OCO exists to cover a short, and short selling is not allowed.
    if args.get("side") != "sell":
        block("advanced (OCO) orders are allowed only as sell-side exits; got side %r" % args.get("side"))
    if args.get("market_hours", "regular_hours") != "regular_hours":
        block("advanced (OCO) orders must use regular_hours")


def check_watchlist_write(cfg, name, args):
    wanted = cfg.watchlist.name
    if name == "create_watchlist":
        if args.get("display_name") != wanted:
            block("only a watchlist named %r may be created" % wanted)
        return
    list_id = cfg.watchlist.id or ""
    if not list_id or args.get("list_id") != list_id:
        block("only the %r watchlist may be changed (its id is watchlist.id in trader.json)" % wanted)
    if name == "update_watchlist" and args.get("display_name") not in (None, "", wanted):
        block("the %r watchlist may not be renamed" % wanted)
    if args.get("currency_pair_ids"):
        block("crypto may not be added to or removed from the watchlist")


def guard(call):
    if not isinstance(call, dict) or not isinstance(call.get("tool_name"), str):
        block("could not read the tool call (no tool_name)")
    name = robinhood_tool(call["tool_name"])
    if name is None:
        sys.exit(0)
    args = call.get("tool_input")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        block("tool call has no readable details")

    if name in NEVER:
        block("%s is never allowed (crypto and option exercise are not part of this product)" % name)
    known = OPEN_READS | ALERT_WRITES | ACCOUNT_TOOLS | ORDER_TOOLS | WATCHLIST_WRITES
    if name not in known:
        block("%s is not on the allow-list of Robinhood tools; ask the owner before using it" % name)
    if name in OPEN_READS:
        sys.exit(0)

    cfg = load_config()
    check_phase(cfg, name)
    if name in ALERT_WRITES:
        sys.exit(0)
    if name in WATCHLIST_WRITES:
        check_watchlist_write(cfg, name, args)
        sys.exit(0)
    check_account(cfg, args)
    if name in ORDER_TOOLS:
        kind = name.partition("_")[2]
        if kind == "option_order":
            check_option_order(cfg, args)
        elif kind == "equity_order":
            check_equity_order(cfg, args)
        else:
            check_advanced_order(cfg, args)
    sys.exit(0)


def main():
    try:
        call = json.load(sys.stdin)
    except Exception as e:
        block("could not read the tool call (%s)" % e)
    try:
        guard(call)
    except SystemExit:
        raise
    except BaseException as e:  # any bug in the guard fails closed
        block("internal error in the guard (%s: %s)" % (type(e).__name__, e))
    sys.exit(0)


if __name__ == "__main__":
    main()
