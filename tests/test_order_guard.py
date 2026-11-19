"""Table-driven tests for tools/order_guard.py, run as a subprocess exactly as Claude Code runs the hook.

Every case runs under both tool namings (the plugin's own server and a server the user connected).
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import (ACCOUNT, GUARD, OPTION_AT_4, OPTION_CLOSED, OPTION_DUP, OPTION_HELD, OPTION_PAPER,
                      OTHER_ACCOUNT, PLAIN_PREFIX, PLUGIN_PREFIX, PREFIXES, ROOT, WATCHLIST_ID,
                      make_workspace, run_guard)

BLOCK = "order_guard: BLOCKED — "
OPT_A = "opt-test-a-277p"   # two ids "from the same underlying" for the spread proof
OPT_B = "opt-test-b-275p"


# ---------------------------------------------------------------- argument builders

def leg(option_id=OPT_A, side="buy", effect="open", ratio=1):
    return {"option_id": option_id, "side": side, "position_effect": effect, "ratio_quantity": ratio}


def opt(legs=None, **kw):
    args = {"account_number": ACCOUNT, "quantity": 1, "direction": "debit", "price": "0.90", "type": "limit",
            "time_in_force": "gfd", "market_hours": "regular_hours", "legs": legs if legs is not None else [leg()]}
    args.update(kw)
    return {k: v for k, v in args.items() if v is not None}


def sell_to_close(option_id=OPTION_HELD, **kw):
    base = dict(direction="credit", price="3.00", legs=[leg(option_id, "sell", "close")])
    base.update(kw)
    return opt(**base)


def stop_market(option_id=OPTION_HELD, stop="6.88", **kw):
    base = dict(type="stop_market", stop_price=stop, price=None)
    base.update(kw)
    return sell_to_close(option_id, **base)


def eq(**kw):
    args = {"account_number": ACCOUNT, "symbol": "GHI", "side": "buy", "type": "limit", "quantity": 20,
            "limit_price": "50.00", "time_in_force": "gfd", "market_hours": "regular_hours"}
    args.update(kw)
    return {k: v for k, v in args.items() if v is not None}


def oco(**kw):
    args = {"account_number": ACCOUNT, "symbol": "GHI", "side": "sell", "quantity": 20,
            "take_profit_price": "54.00", "stop_price": "48.00", "time_in_force": "gtc",
            "market_hours": "regular_hours"}
    args.update(kw)
    return args


def acct(n=ACCOUNT, **kw):
    d = {"account_number": n}
    d.update(kw)
    return d


def check(result, expect, needle=None):
    code, err = result
    if expect == "allow":
        assert code == 0, err
        assert err == ""
    else:
        assert code == 2, "expected a block, got exit %s (%s)" % (code, err)
        assert err.startswith(BLOCK), err
        if needle:
            assert needle in err, err


# ---------------------------------------------------------------- PLAN.md "How to run the proof" and
# the preview proof cases, against a LIVE workspace.

PROOF_CASES = [
    ("proof: two-leg spread preview", "review_option_order",
     opt(legs=[leg(OPT_A, "buy", "open"), leg(OPT_B, "sell", "open")]), "block",
     "option orders must have exactly one leg"),
    ("proof: sell-to-open preview", "review_option_order", opt(legs=[leg(side="sell", effect="open")]),
     "block", "only buy-to-open and sell-to-close are allowed"),
    ("proof: single-leg buy-to-open preview", "review_option_order", opt(), "allow", None),
    ("proof: another account's portfolio", "get_portfolio", acct(OTHER_ACCOUNT), "block",
     "account is not the configured account"),
    ("proof: crypto preview", "preview_crypto_order", {"account_number": ACCOUNT, "side": "buy"}, "block",
     "never allowed"),
    ("proof: option buy over the cap", "review_option_order", opt(price="10.10"), "block",
     "over the $1000 per-trade cap"),
    ("proof: market share buy", "review_equity_order", eq(type="market", limit_price=None), "block",
     "share buys must be limit orders"),
    ("proof: change to another watchlist", "add_to_watchlist", {"list_id": "WL-OTHER-PRIMARY", "symbols": ["SPY"]},
     "block", "only the 'Trader' watchlist may be changed"),
    ("proof: 20-share limit buy preview", "review_equity_order", eq(), "allow", None),
    ("proof: approval-setting read", "get_trade_approval_setting", acct(), "allow", None),
    ("proof: buy-side OCO", "review_advanced_order", oco(side="buy"), "block",
     "advanced (OCO) orders are allowed only as sell-side exits"),
    ("proof: sell-side OCO 20 shares gtc", "review_advanced_order", oco(), "allow", None),
    ("proof: stop-market at 6.88 (entry 5.5)", "review_option_order", stop_market(stop="6.88"), "allow", None),
    ("proof: stop-market at 6.00", "review_option_order", stop_market(stop="6.00"), "block",
     "is below entry 5.5 x 1.25"),
    ("proof: stop-market at 5.00, exact message", "review_option_order", stop_market(stop="5.00"), "block",
     "option stop_market stop 5.00 is below entry 5.5 x 1.25 = 6.875"),
    ("proof: stop-market gtc", "review_option_order", stop_market(time_in_force="gtc"), "block",
     "must be a day order (gfd)"),
    ("proof: stop-market quantity 2", "review_option_order", stop_market(quantity=2), "block",
     "quantity 2 is more than the 1 held"),
    ("proof: stop-market unknown option id", "review_option_order", stop_market("opt-test-unknown"), "block",
     "needs exactly one open live trade"),
    ("proof: option market sell", "review_option_order", sell_to_close(type="market"), "block",
     "option sell type 'market' is not allowed"),
    ("proof: buy-side stop-market", "review_option_order",
     opt(type="stop_market", stop_price="6.88", legs=[leg(OPTION_HELD)]), "block",
     "option buys must be limit orders"),
    ("proof: stop-limit sell still passes", "review_option_order",
     sell_to_close(type="stop_limit", stop_price="2.75", price="2.40"), "allow", None),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("label,tool,args,expect,needle", PROOF_CASES, ids=[c[0] for c in PROOF_CASES])
def test_proof_cases(workspace, prefix, label, tool, args, expect, needle):
    check(run_guard(prefix + tool, args, workspace), expect, needle)


def test_proof_lower_cap_case(ws_factory):
    """A lower cap set in the config: a $760 option buy against a $750 cap."""
    ws = ws_factory(mutate=lambda c: c["limits"].update(option_cap_dollars=750))
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(price="7.60"), ws), "block",
          "option buy costs $760.00, over the $750 per-trade cap")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(price="7.50"), ws), "allow")


# ---------------------------------------------------------------- every check, allowed and blocked side

CHECK_CASES = [
    # options: legs, effects, hours, types
    ("option: no legs", "review_option_order", opt(legs=[]), "block", "exactly one leg"),
    ("option: legs not a list", "review_option_order", opt(legs={"0": leg()}), "block", "exactly one leg"),
    ("option: leg not an object", "review_option_order", opt(legs=["x"]), "block", "only buy-to-open"),
    ("option: buy-to-close", "review_option_order", opt(legs=[leg(side="buy", effect="close")]), "block",
     "only buy-to-open and sell-to-close"),
    ("option: ratio 2", "review_option_order", opt(legs=[leg(ratio=2)]), "block", "ratio quantity must be 1"),
    ("option: extended hours", "review_option_order", opt(market_hours="extended_hours"), "block",
     "must use regular_hours"),
    ("option: buy stop_limit", "review_option_order", opt(type="stop_limit", stop_price="1.00"), "block",
     "option buys must be limit orders"),
    ("option: buy market", "review_option_order", opt(type="market"), "block", "option buys must be limit"),
    ("option: buy exactly at cap 10.00 x 1", "review_option_order", opt(price="10.00"), "allow", None),
    ("option: buy at cap 5.00 x 2", "review_option_order", opt(price="5.00", quantity=2), "allow", None),
    ("option: buy 5.01 x 2 over cap", "review_option_order", opt(price="5.01", quantity=2), "block",
     "over the $1000"),
    ("option: buy price missing", "review_option_order", opt(price=None), "block", "limit price is missing"),
    ("option: buy price not a number", "review_option_order", opt(price="abc"), "block", "not a number"),
    ("option: buy quantity negative", "review_option_order", opt(quantity=-1), "block", "positive"),
    ("option: buy quantity zero", "review_option_order", opt(quantity=0), "block", "positive"),
    ("option: buy price true", "review_option_order", opt(price=True), "block", "not a number"),
    ("option: place buy within cap", "place_option_order", opt(), "allow", None),
    ("option: sell-to-close limit", "place_option_order", sell_to_close(), "allow", None),
    ("option: sell-to-close stop_limit", "place_option_order",
     sell_to_close(type="stop_limit", stop_price="2.75"), "allow", None),
    ("option: sell-to-close trailing", "review_option_order", sell_to_close(type="trailing_stop"), "block",
     "not allowed"),
    ("option: sell extended hours", "place_option_order", sell_to_close(market_hours="all_day_hours"), "block",
     "regular_hours"),
    # shares
    ("shares: dollar amount", "review_equity_order", eq(dollar_amount="500", quantity=None), "block",
     "dollar-amount share orders"),
    ("shares: fractional", "review_equity_order", eq(quantity="1.5"), "block", "fractional"),
    ("shares: extended hours", "review_equity_order", eq(market_hours="extended_hours"), "block",
     "share orders must use regular_hours"),
    ("shares: buy exactly at cap 30 x 50", "place_equity_order", eq(quantity=30, limit_price="50"), "allow",
     None),
    ("shares: buy over cap 31 x 50.00", "place_equity_order", eq(quantity=31), "block",
     "over the $1500 per-position cap"),
    ("shares: buy stop_limit", "review_equity_order", eq(type="stop_limit", stop_price="50"), "block",
     "share buys must be limit"),
    ("shares: buy no limit price", "review_equity_order", eq(limit_price=None), "block", "limit price"),
    ("shares: sell limit", "place_equity_order", eq(side="sell", limit_price="55"), "allow", None),
    ("shares: sell stop_limit", "place_equity_order", eq(side="sell", type="stop_limit", stop_price="47"),
     "allow", None),
    ("shares: sell stop_market", "place_equity_order", eq(side="sell", type="stop_market", stop_price="47",
                                                           limit_price=None), "allow", None),
    ("shares: sell market", "place_equity_order", eq(side="sell", type="market", limit_price=None), "block",
     "share sell type 'market' is not allowed"),
    ("shares: short side", "review_equity_order", eq(side="sell_short"), "block", "side must be buy or sell"),
    ("shares: no quantity", "review_equity_order", eq(quantity=None), "block", "quantity is missing"),
    # advanced
    ("oco: sell extended hours", "place_advanced_order", oco(market_hours="extended_hours"), "block",
     "advanced (OCO) orders must use regular_hours"),
    ("oco: place sell side", "place_advanced_order", oco(), "allow", None),
    ("oco: no side", "review_advanced_order", oco(side=None), "block", "sell-side exits"),
    # accounts
    ("account: missing on order", "review_option_order", opt(account_number=None), "block",
     "no account number given"),
    ("account: other on order", "place_equity_order", eq(account_number=OTHER_ACCOUNT), "block",
     "account is not the configured account"),
    ("account: rhs mismatch", "get_portfolio", acct(rhs_account_number=OTHER_ACCOUNT), "block",
     "account is not the configured account"),
    ("account: rhs only, matching", "get_equity_positions", {"rhs_account_number": ACCOUNT}, "allow", None),
    ("account: numeric-looking but different", "get_option_positions", acct(ACCOUNT + "0"), "block",
     "not the configured account"),
    ("account: empty string", "get_portfolio", acct(""), "block", "no account number given"),
    ("account: cancel other account", "cancel_option_order", acct(OTHER_ACCOUNT, order_id="o1"), "block",
     "not the configured account"),
    ("account: cancel own account", "cancel_option_order", acct(order_id="o1"), "allow", None),
    ("account: decline own account", "decline_trade_approval", acct(approval_id="a1"), "allow", None),
    # watchlists
    ("watchlist: create the configured name", "create_watchlist", {"display_name": "Trader"}, "allow", None),
    ("watchlist: create another name", "create_watchlist", {"display_name": "Other"}, "block",
     "only a watchlist named 'Trader' may be created"),
    ("watchlist: add to configured id", "add_to_watchlist", {"list_id": WATCHLIST_ID, "symbols": ["SPY"]},
     "allow", None),
    ("watchlist: remove from configured id", "remove_from_watchlist", {"list_id": WATCHLIST_ID, "symbols": ["SPY"]},
     "allow", None),
    ("watchlist: no list id", "add_to_watchlist", {"symbols": ["SPY"]}, "block", "only the 'Trader' watchlist"),
    ("watchlist: rename", "update_watchlist", {"list_id": WATCHLIST_ID, "display_name": "Renamed"}, "block",
     "may not be renamed"),
    ("watchlist: update keeping the name", "update_watchlist", {"list_id": WATCHLIST_ID, "display_name": "Trader"},
     "allow", None),
    ("watchlist: crypto", "add_to_watchlist", {"list_id": WATCHLIST_ID, "currency_pair_ids": ["btc"]}, "block",
     "crypto may not be added"),
    # alerts (writes with no account; need a valid config)
    ("alerts: create", "create_alert", {"symbol": "SPY", "price": 500}, "allow", None),
    ("alerts: mark read", "mark_alerts_read", {}, "allow", None),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("label,tool,args,expect,needle", CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_checks(workspace, prefix, label, tool, args, expect, needle):
    check(run_guard(prefix + tool, args, workspace), expect, needle)


# ---------------------------------------------------------------- allow-list

# The old guard's lists, copied by hand so an accidental change to the sets shows up here.
OPEN_READS = [
    "get_accounts", "get_equity_quotes", "get_equity_historicals", "get_equity_fundamentals",
    "get_equity_technical_indicators", "get_equity_price_book", "get_equity_analyst_ratings",
    "get_financials", "get_earnings_calendar", "get_earnings_results", "get_indexes", "get_index_quotes",
    "get_index_historicals", "get_option_chains", "get_option_instruments", "get_option_quotes",
    "get_option_historicals", "get_sec_filing_index", "get_sec_filing", "get_sec_filing_facts",
    "get_sec_filing_facts_catalog", "get_politician_trades", "search", "get_scanner_filter_specs",
    "get_scanner_datapoints", "preview_scan", "get_scans", "run_scan", "get_popular_watchlists",
    "get_watchlists", "get_watchlist_items", "get_option_watchlist", "get_currency_pairs", "get_crypto_quotes",
    "get_alerts", "get_alert_log",
]
ACCOUNT_READS = [
    "get_portfolio", "get_equity_positions", "get_option_positions", "get_crypto_positions",
    "get_equity_orders", "get_option_orders", "get_crypto_orders", "get_advanced_orders",
    "get_equity_tax_lots", "get_equity_tradability", "get_realized_pnl", "get_pnl_trade_history",
    "get_trade_approvals", "get_trade_approval_setting", "get_option_level_upgrade_info",
    "get_limited_margin_upgrade_info",
]
NEVER = ["preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
         "get_crypto_account_onboarding_info", "exercise_option", "cancel_option_exercise"]
NOT_LISTED = ["create_scan", "update_scan_config", "update_scan_filters", "follow_watchlist",
              "unfollow_watchlist", "add_option_to_watchlist", "remove_option_from_watchlist",
              "replace_option_order", "transfer_funds", "get_bank_accounts", ""]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool", OPEN_READS)
def test_open_reads_pass(workspace, prefix, tool):
    check(run_guard(prefix + tool, {"symbols": ["SPY"]}, workspace), "allow")


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool", ACCOUNT_READS)
def test_account_reads(workspace, prefix, tool):
    check(run_guard(prefix + tool, acct(), workspace), "allow")
    check(run_guard(prefix + tool, acct(OTHER_ACCOUNT), workspace), "block", "not the configured account")
    check(run_guard(prefix + tool, {}, workspace), "block", "no account number given")


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool", NEVER)
def test_never_reachable(workspace, prefix, tool):
    check(run_guard(prefix + tool, acct(), workspace), "block", "never allowed")


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool", NOT_LISTED)
def test_not_on_allow_list(workspace, prefix, tool):
    if tool == "":
        # "mcp__robinhood-trading__" with nothing after it is not a Robinhood tool name at all
        check(run_guard(prefix, {}, workspace), "allow")
        return
    check(run_guard(prefix + tool, acct(list_id=WATCHLIST_ID), workspace), "block", "not on the allow-list")


@pytest.mark.parametrize("tool_name", [
    "Bash", "Read", "mcp__claude_ai_Notion__search", "mcp__robinhood-trading-evil__place_option_order",
    "mcp__robinhood__place_option_order",
])
def test_other_tools_pass_untouched(workspace, tool_name):
    check(run_guard(tool_name, {"command": "ls"}, workspace), "allow")


@pytest.mark.parametrize("tool_name", [
    "mcp__plugin_personal-ai-trader_robinhood-trading__place_option_order",
    "mcp__plugin_some-fork_robinhood-trading__place_option_order",
    "mcp__plugin_my_plugin_robinhood-trading__place_option_order",
    "mcp__robinhood-trading__place_option_order",
])
def test_any_plugin_naming_is_guarded(ws_factory, tool_name):
    check(run_guard(tool_name, opt(), ws_factory("DRY_RUN")), "block", "phase is DRY_RUN")


# ---------------------------------------------------------------- phases

DRY_RUN_REFUSED = [
    ("place_option_order", opt()),
    ("place_option_order", sell_to_close()),
    ("place_option_order", stop_market()),
    ("place_equity_order", eq()),
    ("place_equity_order", eq(side="sell", limit_price="55")),
    ("place_advanced_order", oco()),
    ("cancel_option_order", acct(order_id="o1")),
    ("cancel_equity_order", acct(order_id="o1")),
    ("cancel_advanced_order", acct(order_id="o1")),
    ("decline_trade_approval", acct(approval_id="a1")),
]
DRY_RUN_ALLOWED = [
    ("review_option_order", opt()),
    ("review_option_order", sell_to_close()),
    ("review_equity_order", eq()),
    ("review_advanced_order", oco()),
    ("get_portfolio", acct()),
    ("get_option_orders", acct()),
    ("get_accounts", {}),
    ("create_watchlist", {"display_name": "Trader"}),
    ("create_alert", {"symbol": "SPY"}),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool,args", DRY_RUN_REFUSED, ids=["%s-%d" % (t, i) for i, (t, _) in enumerate(DRY_RUN_REFUSED)])
def test_dry_run_refuses_place_cancel_decline(ws_factory, prefix, tool, args):
    check(run_guard(prefix + tool, args, ws_factory("DRY_RUN")), "block", "phase is DRY_RUN: %s is refused" % tool)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool,args", DRY_RUN_ALLOWED, ids=["%s-%d" % (t, i) for i, (t, _) in enumerate(DRY_RUN_ALLOWED)])
def test_dry_run_allows_previews_and_reads(ws_factory, prefix, tool, args):
    check(run_guard(prefix + tool, args, ws_factory("DRY_RUN")), "allow")


def test_dry_run_previews_still_checked(ws_factory):
    ws = ws_factory("DRY_RUN")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(price="20"), ws), "block", "over the $1000")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(account_number=OTHER_ACCOUNT), ws), "block",
          "not the configured account")
    check(run_guard(PLUGIN_PREFIX + "review_option_order",
                    opt(legs=[leg(OPT_A, "buy", "open"), leg(OPT_B, "sell", "open")]), ws),
          "block", "exactly one leg")


def test_dry_run_still_checks_account_after_phase(ws_factory):
    # DRY_RUN refusal comes first, whatever else is wrong with the call
    ws = ws_factory("DRY_RUN")
    check(run_guard(PLAIN_PREFIX + "cancel_option_order", acct(OTHER_ACCOUNT), ws), "block", "phase is DRY_RUN")


HALTED_REFUSED = [
    ("review_option_order", opt(), "option buy-to-open"),
    ("place_option_order", opt(), "option buy-to-open"),
    ("review_equity_order", eq(), "share buy"),
    ("place_equity_order", eq(), "share buy"),
]
HALTED_ALLOWED = [
    ("place_option_order", sell_to_close()),
    ("place_option_order", sell_to_close(type="stop_limit", stop_price="2.75")),
    ("place_option_order", stop_market()),
    ("review_option_order", sell_to_close()),
    ("place_equity_order", eq(side="sell", limit_price="55")),
    ("place_advanced_order", oco()),
    ("cancel_option_order", acct(order_id="o1")),
    ("cancel_equity_order", acct(order_id="o1")),
    ("cancel_advanced_order", acct(order_id="o1")),
    ("decline_trade_approval", acct(approval_id="a1")),
    ("get_portfolio", acct()),
    ("get_option_quotes", {}),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool,args,what", HALTED_REFUSED, ids=[t for t, _, _ in HALTED_REFUSED])
def test_halted_refuses_opening_orders(ws_factory, prefix, tool, args, what):
    check(run_guard(prefix + tool, args, ws_factory("HALTED")), "block",
          "phase is HALTED: no new opening orders (%s)" % what)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool,args", HALTED_ALLOWED, ids=["%s-%d" % (t, i) for i, (t, _) in enumerate(HALTED_ALLOWED)])
def test_halted_allows_closes_cancels_reads(ws_factory, prefix, tool, args):
    check(run_guard(prefix + tool, args, ws_factory("HALTED")), "allow")


def test_halted_still_checks_account_and_shape(ws_factory):
    ws = ws_factory("HALTED")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", sell_to_close(account_number=OTHER_ACCOUNT), ws),
          "block", "not the configured account")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", sell_to_close(type="market"), ws), "block",
          "not allowed")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", opt(legs=[leg(side="sell", effect="open")]), ws),
          "block", "only buy-to-open and sell-to-close")


# ---------------------------------------------------------------- allowed.* switches

def test_allowed_switches(ws_factory):
    ws = ws_factory(mutate=lambda c: c["allowed"].update(long_options=False, shares=False))
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(), ws), "block", "allowed.long_options")
    check(run_guard(PLUGIN_PREFIX + "review_equity_order", eq(), ws), "block", "allowed.shares")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", sell_to_close(), ws), "allow")
    check(run_guard(PLUGIN_PREFIX + "place_equity_order", eq(side="sell", limit_price="55"), ws), "allow")


# ---------------------------------------------------------------- option stop-market exception boundaries

STOP_MARKET_CASES = [
    ("exactly at entry x 1.25 (4.00 -> 5.00)", stop_market(OPTION_AT_4, "5.00"), "allow", None),
    ("just below (4.99)", stop_market(OPTION_AT_4, "4.99"), "block", "stop 4.99 is below entry 4.00 x 1.25 = 5.0000"),
    ("well above (8.00)", stop_market(OPTION_AT_4, "8.00"), "allow", None),
    ("quantity above the ledger", stop_market(OPTION_AT_4, "5.00", quantity=2), "block", "more than the 1 held"),
    ("quantity equal to the ledger", stop_market(OPTION_AT_4, "5.00", quantity=1), "allow", None),
    ("non-gfd (gtc)", stop_market(OPTION_AT_4, "5.00", time_in_force="gtc"), "block", "day order (gfd)"),
    ("non-gfd (ioc)", stop_market(OPTION_AT_4, "5.00", time_in_force="ioc"), "block", "day order (gfd)"),
    ("time_in_force omitted (defaults to gfd)", stop_market(OPTION_AT_4, "5.00", time_in_force=None), "allow",
     None),
    ("no stop price", stop_market(OPTION_AT_4, None), "block", "stop price is missing"),
    ("paper trade only", stop_market(OPTION_PAPER, "9.00"), "block", "needs exactly one open live trade"),
    ("closed live trade", stop_market(OPTION_CLOSED, "9.00"), "block", "needs exactly one open live trade"),
    ("two open live rows", stop_market(OPTION_DUP, "9.00"), "block", "needs exactly one open live trade"),
    ("leg without option id", stop_market(None, "9.00"), "block", "needs exactly one open live trade"),
    ("extended hours", stop_market(OPTION_AT_4, "5.00", market_hours="extended_hours"), "block",
     "regular_hours"),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("label,args,expect,needle", STOP_MARKET_CASES, ids=[c[0] for c in STOP_MARKET_CASES])
def test_stop_market_boundaries(workspace, prefix, label, args, expect, needle):
    check(run_guard(prefix + "place_option_order", args, workspace), expect, needle)


def test_stop_market_lock_gain_from_config(ws_factory):
    ws = ws_factory(mutate=lambda c: c["options"]["ratchet"].update(lock_gain_pct=50, trigger_gain_pct=80))
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "5.99"), ws), "block",
          "x 1.5 = 6.000")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "6.00"), ws), "allow")


def test_stop_market_disabled_in_config(ws_factory):
    ws = ws_factory(mutate=lambda c: c["options"]["stop_market_after_gain"].update(enabled=False))
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "9.00"), ws), "block",
          "stop_market is switched off")


def test_stop_market_without_ledger(tmp_path):
    ws = make_workspace(tmp_path, ledger_rows=None)
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "9.00"), ws), "block",
          "needs exactly one open live trade")


def test_stop_market_bad_ledger_entry_price(tmp_path):
    rows = [{"trade_id": "L1", "mode": "live", "status": "open", "quantity": "1", "entry_price": "n/a",
             "option_id": OPTION_AT_4}]
    ws = make_workspace(tmp_path, ledger_rows=rows)
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "9.00"), ws), "block",
          "ledger entry price")


def test_ledger_is_read_from_the_workspace(tmp_path):
    """Two workspaces, two ledgers: the guard reads the one under CLAUDE_PROJECT_DIR."""
    a = make_workspace(tmp_path / "a")
    b = make_workspace(tmp_path / "b", ledger_rows=[])
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "5.00"), a), "allow")
    check(run_guard(PLUGIN_PREFIX + "place_option_order", stop_market(OPTION_AT_4, "5.00"), b), "block",
          "needs exactly one open live trade")


# ---------------------------------------------------------------- watchlist config

def test_watchlist_without_id_refuses_changes_but_allows_create(ws_factory):
    ws = ws_factory(mutate=lambda c: c["watchlist"].update(id=None))
    check(run_guard(PLUGIN_PREFIX + "add_to_watchlist", {"list_id": WATCHLIST_ID}, ws), "block",
          "only the 'Trader' watchlist may be changed")
    check(run_guard(PLUGIN_PREFIX + "create_watchlist", {"display_name": "Trader"}, ws), "allow")


def test_watchlist_name_from_config(ws_factory):
    ws = ws_factory(mutate=lambda c: c["watchlist"].update(name="Sam picks", id="WL-SAM"))
    check(run_guard(PLAIN_PREFIX + "create_watchlist", {"display_name": "Sam picks"}, ws), "allow")
    check(run_guard(PLAIN_PREFIX + "create_watchlist", {"display_name": "Trader"}, ws), "block")
    check(run_guard(PLAIN_PREFIX + "add_to_watchlist", {"list_id": "WL-SAM"}, ws), "allow")
    check(run_guard(PLAIN_PREFIX + "add_to_watchlist", {"list_id": WATCHLIST_ID}, ws), "block")


def test_account_and_caps_from_config(ws_factory):
    def m(c):
        c["account"]["number"] = "ACCT-TEST-0002"
        c["limits"].update(option_cap_dollars=400, share_cap_dollars=600)
    ws = ws_factory(mutate=m)
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(ACCOUNT), ws), "block", "not the configured account")
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct("ACCT-TEST-0002"), ws), "allow")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(account_number="ACCT-TEST-0002", price="4.00"), ws),
          "allow")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(account_number="ACCT-TEST-0002", price="4.01"), ws),
          "block", "over the $400")
    check(run_guard(PLUGIN_PREFIX + "review_equity_order",
                    eq(account_number="ACCT-TEST-0002", quantity=12, limit_price="50"), ws), "allow")
    check(run_guard(PLUGIN_PREFIX + "review_equity_order",
                    eq(account_number="ACCT-TEST-0002", quantity=13, limit_price="50"), ws), "block", "over the $600")


# ---------------------------------------------------------------- fail closed

STATE_CHANGING = [
    ("place_option_order", opt()),
    ("review_option_order", opt()),
    ("cancel_option_order", acct(order_id="o1")),
    ("get_portfolio", acct()),
    ("create_watchlist", {"display_name": "Trader"}),
    ("add_to_watchlist", {"list_id": WATCHLIST_ID}),
    ("create_alert", {"symbol": "SPY"}),
    ("decline_trade_approval", acct()),
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool,args", STATE_CHANGING, ids=[t for t, _ in STATE_CHANGING])
def test_missing_config_refuses_everything_but_reads(tmp_path, prefix, tool, args):
    ws = make_workspace(tmp_path, write_config=False)
    check(run_guard(prefix + tool, args, ws), "block", "trading/trader.json is missing or invalid")
    check(run_guard(prefix + "get_accounts", {}, ws), "allow")
    check(run_guard(prefix + "get_option_quotes", {}, ws), "allow")


@pytest.mark.parametrize("raw", ["{not json", "", "[]", '{"schema": 1}', "null"])
def test_malformed_config_fails_closed(tmp_path, raw):
    ws = make_workspace(tmp_path, raw_config=raw)
    check(run_guard(PLUGIN_PREFIX + "place_equity_order", eq(), ws), "block", "missing or invalid")
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(), ws), "block", "missing or invalid")
    check(run_guard(PLUGIN_PREFIX + "get_accounts", {}, ws), "allow")


@pytest.mark.parametrize("mutate,needle", [
    (lambda c: c.update(phase="PAPER"), "phase must be one of"),
    (lambda c: c.update(phase="LIVE") or c["go_live"].update(met=False), "go_live.met is false"),
    (lambda c: c["limits"].update(option_cap_dollars=0), "option_cap_dollars must be positive"),
    (lambda c: c["limits"].update(share_cap_dollars="1500"), "share_cap_dollars must be a number"),
    (lambda c: c["account"].update(number=123456), "account.number must be a non-empty string"),
    (lambda c: c["allowed"].update(spreads=True), "allowed.spreads is not a switch"),
    (lambda c: c.pop("watchlist"), "watchlist must be an object"),
])
def test_invalid_config_fails_closed(ws_factory, mutate, needle):
    ws = ws_factory(mutate=mutate)
    check(run_guard(PLUGIN_PREFIX + "place_option_order", sell_to_close(), ws), "block", needle)
    check(run_guard(PLUGIN_PREFIX + "get_index_quotes", {}, ws), "allow")


def test_config_warning_does_not_stop_the_guard(ws_factory):
    ws = ws_factory(mutate=lambda c: c["limits"].update(option_cap_dollars=2000))  # 40% of start: warning
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(price="20.00"), ws), "allow")
    check(run_guard(PLUGIN_PREFIX + "review_option_order", opt(price="20.01"), ws), "block", "over the $2000")


def test_project_dir_without_config_does_not_fall_back(tmp_path):
    """CLAUDE_PROJECT_DIR set but empty: even with a valid config in the cwd, refuse."""
    good = make_workspace(tmp_path / "good")
    empty = tmp_path / "empty"
    empty.mkdir()
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(), str(empty), cwd=good), "block", "missing or invalid")


def test_walk_up_from_cwd_when_no_project_dir(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    sub = os.path.join(ws, "trading", "journal")
    os.makedirs(sub)
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(), workspace=None, cwd=sub), "allow")
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(OTHER_ACCOUNT), workspace=None, cwd=sub), "block")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    check(run_guard(PLUGIN_PREFIX + "get_portfolio", acct(), workspace=None, cwd=str(nowhere)), "block",
          "no trading/trader.json found")


@pytest.mark.parametrize("stdin,needle", [
    ("not json at all", "could not read the tool call"),
    ("", "could not read the tool call"),
    ("[]", "could not read the tool call"),
    ('{"tool_input": {}}', "could not read the tool call"),
    ('{"tool_name": 7}', "could not read the tool call"),
])
def test_malformed_stdin_fails_closed(workspace, stdin, needle):
    check(run_guard(None, stdin=stdin, workspace=workspace), "block", needle)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tool_input", ["a string", [1, 2], 5])
def test_unreadable_tool_input_fails_closed(workspace, prefix, tool_input):
    stdin = json.dumps({"tool_name": prefix + "get_accounts", "tool_input": tool_input})
    check(run_guard(None, stdin=stdin, workspace=workspace), "block", "no readable details")


def test_null_tool_input_on_a_read_passes(workspace):
    stdin = json.dumps({"tool_name": PLUGIN_PREFIX + "get_accounts", "tool_input": None})
    check(run_guard(None, stdin=stdin, workspace=workspace), "allow")


def test_missing_config_loader_fails_closed(tmp_path, workspace):
    """A broken plugin install (trader_config.py missing): reads pass, everything else is refused."""
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(GUARD, str(lone / "order_guard.py"))
    env = {k: v for k, v in os.environ.items()}
    env["CLAUDE_PROJECT_DIR"] = workspace

    def run(tool, args):
        p = subprocess.run([sys.executable, "-I", str(lone / "order_guard.py")],
                           input=json.dumps({"tool_name": PLUGIN_PREFIX + tool, "tool_input": args}),
                           capture_output=True, text=True, env=env, timeout=30)
        return p.returncode, p.stderr
    check(run("get_portfolio", acct()), "block", "cannot load tools/trader_config.py")
    check(run("get_accounts", {}), "allow")


# ---------------------------------------------------------------- plugin wiring

def _json(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return json.load(f)


def test_hooks_json_registers_the_guard_for_both_namings():
    import re
    hooks = _json("hooks/hooks.json")["hooks"]["PreToolUse"]
    assert len(hooks) == 1
    matcher = re.compile(hooks[0]["matcher"])
    (handler,) = hooks[0]["hooks"]
    assert handler["type"] == "command"
    assert handler["onFailure"] == "block"
    assert "${CLAUDE_PLUGIN_ROOT}/tools/order_guard.py" in handler["args"]
    plugin = _json(".claude-plugin/plugin.json")["name"]
    (server,) = _json(".mcp.json")["mcpServers"].keys()
    own = "mcp__plugin_%s_%s__place_option_order" % (plugin, server)
    for name in (own, "mcp__robinhood-trading__place_option_order", "mcp__robinhood-trading__get_accounts",
                 "mcp__plugin_other_robinhood-trading__exercise_option"):
        assert matcher.search(name), name
    for name in ("Bash", "mcp__claude_ai_Notion__search", "mcp__robinhood-trading-evil__x"):
        assert not matcher.search(name), name


def test_guard_parses_what_the_matcher_matches():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import order_guard
    assert order_guard.robinhood_tool("mcp__plugin_personal-ai-trader_robinhood-trading__get_accounts") == "get_accounts"
    assert order_guard.robinhood_tool("mcp__robinhood-trading__place_option_order") == "place_option_order"
    assert order_guard.robinhood_tool("mcp__plugin_a_b_robinhood-trading__search") == "search"
    assert order_guard.robinhood_tool("Bash") is None


def test_mcp_json_declares_the_robinhood_server():
    servers = _json(".mcp.json")["mcpServers"]
    assert servers == {"robinhood-trading": {"type": "http", "url": "https://agent.robinhood.com/mcp/trading"}}


def test_plugin_manifest():
    m = _json(".claude-plugin/plugin.json")
    assert m["name"] == "personal-ai-trader"
    assert m["version"] == "0.1.0"
    assert "email" not in m["author"]


# ---------------------------------------------------------------- whole contracts (phase 2 follow-up)

@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("label,args,expect", [
    ("buy 1.5 contracts", opt(quantity="1.5"), "block"),
    ("buy 0.5 contracts (cost under the cap)", opt(quantity=0.5), "block"),
    ("sell-to-close 1.5 contracts", sell_to_close(quantity="1.5"), "block"),
    ("stop-market 0.5 contracts", stop_market(quantity="0.5"), "block"),
    ("non-numeric quantity on a sell", sell_to_close(quantity="one"), "block"),
    ("buy 2 contracts", opt(quantity=2), "allow"),
    ("buy '1.0' contracts", opt(quantity="1.0"), "allow"),
    ("sell-to-close 1 contract", sell_to_close(quantity=1), "allow"),
])
def test_option_quantity_must_be_whole_contracts(workspace, prefix, label, args, expect):
    for tool in ("review_option_order", "place_option_order"):
        check(run_guard(prefix + tool, args, workspace), expect,
              "option quantity must be a whole number of contracts" if expect == "block" else None)
