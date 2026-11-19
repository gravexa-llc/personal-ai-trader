#!/usr/bin/env python3
"""Write the workspace's trading/RULES.md from trading/trader.json and templates/RULES.md.tmpl.

RULES.md is the human-readable copy of the limits the order guard and the routines enforce; generating it
from the config keeps the two from drifting apart. Standard library only.

Usage:
  render_rules.py [--workspace PATH]            write <workspace>/trading/RULES.md
  render_rules.py --check [--workspace PATH]    exit 0 if RULES.md matches what the config generates,
                                                exit 1 (with a diff) if it differs or is missing
  render_rules.py --print [--workspace PATH]    print the generated text, write nothing
Exit 2: the config is missing or invalid (the problems are printed).

The workspace is --workspace, else $CLAUDE_PROJECT_DIR, else the first folder at or above the current
directory holding trading/trader.json. If trading/RULES-notes.md exists it is copied verbatim to the end, under
"Owner's notes" (so editing the notes is drift too, until RULES.md is regenerated). Placeholders in the template are {{name}}; an unknown or unused
placeholder is a bug and stops the render.
"""
import argparse
import difflib
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader_config  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(HERE), "templates", "RULES.md.tmpl")
RULES_RELATIVE = os.path.join("trading", "RULES.md")
NOTES_RELATIVE = os.path.join("trading", "RULES-notes.md")
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")


def money(d):
    d = Decimal(d)
    if d == d.to_integral_value():
        return "${:,}".format(int(d))
    return "${:,.2f}".format(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(d):
    d = Decimal(d).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP).normalize()
    text = format(d, "f")
    return text + "%"


def plain(d):
    return format(Decimal(d).normalize(), "f")


def owner_notes(cfg):
    """The 'Owner's notes' section: trading/RULES-notes.md verbatim, or nothing if the file does not exist."""
    path = os.path.join(cfg.workspace, NOTES_RELATIVE)
    try:
        with open(path, encoding="utf-8") as f:
            notes = f.read()
    except FileNotFoundError:
        return ""
    if notes and not notes.endswith("\n"):
        notes += "\n"
    return ("\n## Owner's notes\nCopied verbatim from `trading/RULES-notes.md`. Notes record decisions and their "
            "reasons; the limits above are what the order guard and the routines enforce.\n\n" + notes)


def values(cfg):
    lim, opt = cfg.limits, cfg.options
    start = cfg.account.start_balance
    owner = cfg.owner_name
    set_by = ("set by %s on %s" % (owner, cfg.rules_set_on)) if cfg.rules_set_on \
        else "set by %s (date not recorded)" % owner

    instruments = []
    if cfg.allowed.long_options:
        instruments.append("- Buying calls and buying puts (long options), single leg.")
    else:
        instruments.append("- Buying options is **switched off** (`allowed.long_options` is false); the guard refuses option buys.")
    if cfg.allowed.shares:
        instruments.append("- Buying shares and ETFs outright, in whole shares.")
    else:
        instruments.append("- Buying shares is **switched off** (`allowed.shares` is false); the guard refuses share buys.")
    instruments.append("- Universe: any stock or ETF whose options pass the liquidity floors in section 7.")

    sm = opt.stop_market_after_gain
    multiple = cfg.stop_market_min_multiple
    if sm.enabled:
        stop_market_rule = (
            "- Exception — a stop-market sell-to-close is allowed only to protect a gain: a day order (gfd), "
            "regular hours, on a position whose bid has reached **+%s** of entry, with a bid-ask spread of "
            "**%s** of the mid or less, and the stop at least **+%s** above entry (entry × %s). Robinhood refuses "
            "option stop-markets in the first 15 minutes of the session, so not before **%s** local time. "
            "Because it lasts one day and cannot rest beside the stop-limit, it is swapped in at the first check "
            "(cancel the stop-limit, confirm, place the stop-market) and swapped back at the pre-close check "
            "(cancel, confirm, rest the stop-limit GTC for the night). The order guard enforces the day order, "
            "sell-to-close, the held quantity from the ledger and the entry × %s floor."
            % (pct(opt.ratchet.trigger_gain_pct), pct(sm.max_spread_pct), pct(opt.ratchet.lock_gain_pct),
               plain(multiple), sm.not_before, plain(multiple)))
        if sm.raise_max_spread_pct > sm.max_spread_pct:
            stop_market_rule += (
                "\n- Raising a day stop-market that is already working (cancel, confirm, place at the new "
                "ratchet level) is allowed with a spread up to **%s** of the mid; the first swap into a "
                "stop-market still needs **%s** or less. Above %s at a raise, rest a stop-limit at the new "
                "level instead." % (pct(sm.raise_max_spread_pct), pct(sm.max_spread_pct),
                                    pct(sm.raise_max_spread_pct)))
    else:
        stop_market_rule = ("- Option stop-market orders are **switched off** "
                            "(`options.stop_market_after_gain.enabled` is false); the guard refuses them.")

    cushion = opt.overnight_cushion
    if cushion < 1:
        below = (Decimal("1") - cushion) * 100
        cushion_rule = (
            "- At the pre-close check, an option position under the ratchet rests its overnight stop-limit "
            "(GTC) **%s below the ratchet stop** (ratchet stop × %s, rounded up to the price tick; limit "
            "10–15%% below that), so a wide opening quote is less likely to trigger it. There is no floor: "
            "for that one night the resting stop may sit below the locked gain.\n"
            "- At the first check after the open, once spreads settle, the stop is raised back to the ratchet "
            "level, which restores the full lock. If the bid is already at or below the ratchet stop, exit by hand with a limit order near the "
            "bid, reassessing for at most 30 minutes after the open.\n"
            "- The position always keeps a resting exit. Before the ratchet starts, the planned stop is "
            "unchanged overnight." % (pct(below), plain(cushion)))
    else:
        cushion_rule = ("- No overnight cushion (`options.overnight_cushion` is 1): the overnight stop-limit "
                        "rests at the ratchet stop itself.")

    if cfg.push_notifications:
        reporting_push = ("- A push notification (Claude Code's `PushNotification`) to %s for every open, close "
                          "or limit hit." % owner)
    else:
        reporting_push = ("- Push notifications are off (`notifications.push` is false): opens, closes and limit "
                          "hits are reported in the journal and the daily wrap.")

    if cfg.go_live.met:
        gate_status = "**met**" + (", approved %s" % cfg.go_live.approved_at if cfg.go_live.approved_at else "")
    else:
        gate_status = "**not met yet**"

    halt_drop = (Decimal("1") - lim.halt_account_value / start) * 100 if start else Decimal("0")
    return {
        "owner": owner,
        "set_by": set_by,
        "date_hint": "" if cfg.rules_set_on else
        " Record the date the limits were set as `rules_set_on` (YYYY-MM-DD) in trader.json.",
        "phase": cfg.phase,
        "nickname": cfg.account.nickname,
        "account_last4": cfg.account.number[-4:],
        "start_balance": money(start),
        "paper_days": str(cfg.go_live.paper_days_required),
        "extensions": str(cfg.go_live.extensions),
        "gate_status": gate_status,
        "watchlist_name": cfg.watchlist.name,
        "instruments": "\n".join(instruments),
        "expiry_min": str(opt.expiry_days_min),
        "expiry_max": str(opt.expiry_days_max),
        "option_cap": money(lim.option_cap_dollars),
        "option_cap_pct": pct(lim.option_cap_dollars / start * 100) if start else "?",
        "default_stop": pct(opt.default_stop_pct),
        "planned_option_loss": money(lim.option_cap_dollars * opt.default_stop_pct / 100),
        "share_cap": money(lim.share_cap_dollars),
        "cash_floor": pct(lim.cash_floor_pct),
        "max_positions": str(lim.max_open_positions),
        "max_same_theme": str(lim.max_same_theme),
        "halt_value": money(lim.halt_account_value),
        "halt_drop": "−" + pct(halt_drop),
        "daily_loss": pct(lim.daily_loss_stop_pct),
        "loss_streak": str(lim.loss_streak_pause),
        "min_oi": "{:,}".format(opt.min_open_interest),
        "max_spread": pct(opt.max_spread_pct_of_mid),
        "ratchet_trigger": pct(opt.ratchet.trigger_gain_pct),
        "ratchet_lock": pct(opt.ratchet.lock_gain_pct),
        "ratchet_trail": pct(opt.ratchet.trail_pct),
        "stop_market_rule": stop_market_rule,
        "cushion_rule": cushion_rule,
        "reporting_push": reporting_push,
        "owner_notes": owner_notes(cfg),
    }


def render(cfg, template_path=TEMPLATE):
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    vals = values(cfg)
    used = set(PLACEHOLDER.findall(template))
    unknown = used - set(vals)
    unused = set(vals) - used
    if unknown or unused:
        raise ValueError("template and renderer disagree: unknown %s, unused %s"
                         % (sorted(unknown), sorted(unused)))
    return PLACEHOLDER.sub(lambda m: vals[m.group(1)], template)


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate trading/RULES.md from trading/trader.json.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if RULES.md differs from the config")
    mode.add_argument("--print", action="store_true", help="print the generated text, write nothing")
    p.add_argument("--workspace")
    a = p.parse_args(argv)
    try:
        ws = trader_config.find_workspace(a.workspace)
        cfg = trader_config.load(trader_config.config_path(ws))
    except trader_config.ConfigError as e:
        print("render_rules: trader.json is missing or invalid (%s):" % e.path)
        for problem in e.problems:
            print("  - " + problem)
        return 2
    text = render(cfg)
    out = os.path.join(ws, RULES_RELATIVE)
    if a.print:
        sys.stdout.write(text)
        return 0
    if a.check:
        try:
            with open(out, encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            print("DRIFT: %s is missing; run render_rules.py to write it" % out)
            return 1
        if current == text:
            print("OK: %s matches trader.json" % out)
            return 0
        print("DRIFT: %s differs from what trader.json generates (run render_rules.py to rewrite it):" % out)
        sys.stdout.writelines(difflib.unified_diff(current.splitlines(True), text.splitlines(True),
                                                   "RULES.md (current)", "RULES.md (from trader.json)"))
        return 1
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
