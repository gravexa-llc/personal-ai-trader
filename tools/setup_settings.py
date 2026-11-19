#!/usr/bin/env python3
"""Write or merge the workspace's .claude/settings.json for Personal AI Trader.

A plugin cannot ship permission rules (plugin settings honour only `agent` and `subagentStatusLine`), so
/trader-setup writes them into the workspace with this script. Standard library only.

What it writes, under "permissions":
  deny   the Robinhood tools this product never uses (crypto preview / place / cancel, the crypto sign-up
         tool, option exercise and its cancel) in both tool namings; edits to the workspace's Claude
         settings files; edits to the plugin folder (where the order guard lives). With --harness also the
         alert writes and every watchlist write (a test workspace).
  ask    edits to trading/trader.json: the config the order guard reads is never changed without the
         owner seeing it (this beats the trading/** edit rule below).
  allow  what the scheduled runs need to work unattended:
           - the plugin's routine tools, scoped to this plugin folder: trader_config.py --check,
             schedule.py, render_rules.py --check, ledger.py (under trading/.venv/bin/python, relative
             and absolute), watch.py, doctor.py --format (its read-only report), and
             setup_workspace.py halt --reason (the one config change a routine makes: DRY_RUN or LIVE ->
             HALTED, nothing else). The watcher is stopped with `watch.py --stop` (its own workspace's
             watcher only), so no pgrep / pkill rule is written
             only, and `date`;
           - the Robinhood tools the routines call, in both namings (every one of them is still checked by
             the order guard hook, which refuses anything outside the limits);
           - CronCreate, CronList, CronDelete, PushNotification, and the plugin's routine skills;
           - edits under trading/ (journal, ledger, STATE.md, ...);
           - with --web, WebSearch and one WebFetch(domain:<host>) rule per site listed in the
             workspace's trading/NEWS-SOURCES.md (the shipped template's sites if that file does not
             exist yet), so the prep run can read its news pages unattended. Only hosts found in a URL
             in that file's table are allowed; there is never a blanket WebFetch rule.
         Never allowed: setup_settings.py itself, any other setup_workspace.py command (set, phase,
         go-live, config, extend-paper, ...), order_guard.py, or any edit of the guard, the hook or the
         settings.

Every Bash rule is written twice, with the script path quoted and unquoted, and the skills call each tool as
one simple command from the workspace root (no `cd ... &&`, no shell functions, no `exec`), because a Bash
allow rule matches each simple command of a compound line on its own text (Claude Code permissions docs:
compound commands, wrappers).

Merging: the user's own entries are kept, in their order; missing entries are appended. A WebFetch rule
for a site that has since left NEWS-SOURCES.md is kept too (it may be the user's own) and reported by
--check as a WARN line to remove by hand. Allow rules for
this plugin's tools that point at another plugin folder (left over from an earlier plugin version, whose
folder moves on update) are removed. Running it twice changes nothing.

Usage:
  setup_settings.py [--workspace PATH] [--plugin-root PATH] [--web] [--harness] [--deny-only]
        print the rules and a diff against the current file; writes nothing
  setup_settings.py ... --yes      write the merged file (the old one is kept as settings.json.bak-<time>)
  setup_settings.py ... --check    exit 1 if any deny entry is missing (the doctor uses this); allow and
                                   ask drift is printed as WARN lines (exit 3 for drift with --strict).
                                   The web rules are checked whenever the file already allows WebSearch
                                   or a news site, so a source added to NEWS-SOURCES.md later shows up
                                   as "allow missing" until the owner re-runs this script.
  setup_settings.py ... --print-rules   print the rules this plugin wants, as JSON
The workspace is --workspace, else $CLAUDE_PROJECT_DIR, else the current directory. The plugin root is
--plugin-root, else the folder this script lives in (its parent). Exit 2: unreadable settings file or bad
arguments; the file is never overwritten then.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from order_guard import NEVER  # noqa: E402  (the guard's own list: one source of truth)

PLUGIN_NAME = "personal-ai-trader"
SERVER = "robinhood-trading"
PREFIXES = ("mcp__plugin_%s_%s__" % (PLUGIN_NAME, SERVER), "mcp__%s__" % SERVER)
SETTINGS_RELATIVE = os.path.join(".claude", "settings.json")
NEWS_SOURCES_RELATIVE = os.path.join("trading", "NEWS-SOURCES.md")
NEWS_SOURCES_TEMPLATE = os.path.join("templates", "NEWS-SOURCES.md")
URL_HOST = re.compile(r"https?://([^/\s|`<>()\"']+)")
# A plain DNS name with an alphabetic top-level label: no IP address, port, user@, wildcard or localhost.
VALID_HOST = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
FETCH_RULE = re.compile(r"^WebFetch\(domain:(?P<host>[^)]*)\)$")

# Robinhood tools the routines (trader-check, trader-wrap, trader-weekly-review, trader-status,
# trader-doctor) call. Every one is on the order guard's allow-list; the guard still checks each call.
ROUTINE_ROBINHOOD_TOOLS = (
    # account-scoped reads (the guard refuses any account but trader.json's)
    "get_portfolio", "get_equity_positions", "get_option_positions", "get_equity_orders",
    "get_option_orders", "get_advanced_orders", "get_equity_tax_lots", "get_equity_tradability",
    "get_realized_pnl", "get_pnl_trade_history", "get_trade_approvals", "get_trade_approval_setting",
    "get_option_level_upgrade_info",
    # market data and research (no account)
    "get_accounts", "get_equity_quotes", "get_equity_historicals", "get_equity_fundamentals",
    "get_equity_technical_indicators", "get_equity_price_book", "get_equity_analyst_ratings",
    "get_financials", "get_earnings_calendar", "get_earnings_results", "get_indexes", "get_index_quotes",
    "get_index_historicals", "get_option_chains", "get_option_instruments", "get_option_quotes",
    "get_option_historicals", "get_sec_filing_index", "get_sec_filing", "get_sec_filing_facts",
    "get_sec_filing_facts_catalog", "get_politician_trades", "search", "get_scanner_filter_specs",
    "get_scanner_datapoints", "preview_scan", "get_scans", "run_scan", "get_popular_watchlists",
    "get_watchlists", "get_watchlist_items", "get_option_watchlist", "get_alerts", "get_alert_log",
    # alerts on the underlyings of open positions
    "create_alert", "update_alert", "delete_alert", "mark_alerts_read",
    # the one watchlist (the guard allows only the list with watchlist.id)
    "add_to_watchlist", "remove_from_watchlist",
    # previews, orders, cancels and approvals (DRY_RUN: the guard refuses every place / cancel / decline)
    "review_option_order", "place_option_order", "review_equity_order", "place_equity_order",
    "review_advanced_order", "place_advanced_order", "cancel_option_order", "cancel_equity_order",
    "cancel_advanced_order", "decline_trade_approval",
)
# Never allowed unattended: create_watchlist and update_watchlist (setup only, with the owner present).
HARNESS_DENY_TOOLS = ("create_alert", "update_alert", "delete_alert", "mark_alerts_read",
                      "create_watchlist", "update_watchlist", "add_to_watchlist", "remove_from_watchlist")

ROUTINE_SKILLS = ("trader-check", "trader-wrap", "trader-weekly-review", "trader-status", "trader-doctor")
SESSION_TOOLS = ("CronCreate", "CronList", "CronDelete", "PushNotification")

# Plugin tools a routine runs, with the argument prefix that keeps the rule narrow where it matters.
# (tool, argument prefix or "" for any arguments, interpreters)
PLUGIN_TOOL_RULES = (
    ("trader_config.py", "--check", ("python3",)),
    ("schedule.py", "", ("python3",)),
    ("render_rules.py", "--check", ("python3",)),
    ("ledger.py", "", ("python3", "trading/.venv/bin/python", "{ws}/trading/.venv/bin/python")),
    ("watch.py", "", ("python3",)),
    ("doctor.py", "--format", ("python3",)),
    # Only `halt --reason ...`: never set, phase, go-live, config or extend-paper (they prompt).
    ("setup_workspace.py", "halt --reason", ("python3",)),
)
# The watcher is checked and stopped with `watch.py --status` / `--stop`, which act on the one watcher
# its own workspace recorded. No pgrep / pkill rule is written: any process-name pattern can match a
# watcher that belongs to another workspace or another trader on the same machine.
EXTRA_BASH = ("date", "date +*")
# Rules earlier versions wrote that are now replaced (removed on merge, reported by --check).
LEGACY_BASH = ("Bash(pgrep -fl tools/watch.py)", "Bash(pkill -f tools/watch.py)")

MANAGED_TOOL = re.compile(
    r"^Bash\((?:exec )?\S*python3?\s+\"?(?P<root>.+?)/tools/"
    r"(?:trader_config|schedule|render_rules|ledger|watch|doctor|setup_workspace)\.py\"?(?: .*)?\)$")
MANAGED_WATCHER = re.compile(r"^Bash\((?:pgrep -fl|pkill -f) \"?(?P<root>.+?)/tools/watch\.py\"?\)$")


# ---------------------------------------------------------------- the rules

def _bash_tool_rules(root, ws):
    rules = []
    for tool, args, interpreters in PLUGIN_TOOL_RULES:
        path = "%s/tools/%s" % (root, tool)
        tail = (" " + args + " *") if args else " *"
        for interp in interpreters:
            interp = interp.format(ws=ws)
            rules.append('Bash(%s "%s"%s)' % (interp, path, tail))
            rules.append("Bash(%s %s%s)" % (interp, path, tail))
    return rules


def news_hosts(plugin_root, workspace):
    """Hosts of the URLs in the table rows of the workspace's trading/NEWS-SOURCES.md, in file order,
    without repeats; from the plugin's template when the workspace has no such file yet. Anything that is
    not a plain DNS name (an IP address, a port, credentials, a wildcard) is left out, so an odd row can
    never widen the allow list beyond one named site."""
    text = ""
    for path in (os.path.join(workspace, NEWS_SOURCES_RELATIVE), os.path.join(plugin_root, NEWS_SOURCES_TEMPLATE)):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            break
        except (OSError, UnicodeDecodeError):
            continue
    hosts = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for m in URL_HOST.finditer(line):
            host = m.group(1).lower().rstrip(".")
            if VALID_HOST.match(host) and host not in hosts:
                hosts.append(host)
    return hosts


def fetch_rules(plugin_root, workspace):
    return ["WebFetch(domain:%s)" % h for h in news_hosts(plugin_root, workspace)]


def wanted(plugin_root, workspace, web=False, harness=False, deny_only=False):
    """The rules this plugin wants, as {"deny": [...], "ask": [...], "allow": [...]}. With deny_only, no
    allow rules (the owner chose to approve every scheduled call by hand)."""
    root = os.path.abspath(plugin_root).rstrip("/")
    ws = os.path.abspath(workspace).rstrip("/")
    deny = ["%s%s" % (p, t) for t in sorted(NEVER) for p in PREFIXES]
    deny += ["Edit(/.claude/settings.json)", "Edit(/.claude/settings.local.json)", "Edit(/%s/**)" % root]
    if harness:
        deny += ["%s%s" % (p, t) for t in HARNESS_DENY_TOOLS for p in PREFIXES]
    ask = ["Edit(/trading/trader.json)"]
    allow = _bash_tool_rules(root, ws)
    allow += ["Bash(%s)" % c for c in EXTRA_BASH]
    tools = [t for t in ROUTINE_ROBINHOOD_TOOLS if not (harness and t in HARNESS_DENY_TOOLS)]
    allow += ["%s%s" % (p, t) for t in tools for p in PREFIXES]
    allow += list(SESSION_TOOLS)
    for s in ROUTINE_SKILLS:
        allow += ["Skill(%s:%s)" % (PLUGIN_NAME, s), "Skill(%s:%s *)" % (PLUGIN_NAME, s)]
    allow.append("Edit(/trading/**)")
    if web:
        allow.append("WebSearch")
        allow += fetch_rules(root, ws)
    if deny_only:
        allow = []
    return {"deny": deny, "ask": ask, "allow": allow}


def required_deny(want):
    """The deny entries whose absence is a failure (--check exit 1): everything but the plugin-folder rule,
    which moves with every plugin update and is reported as drift instead."""
    return [r for r in want["deny"] if not r.startswith("Edit(//")]


# ---------------------------------------------------------------- reading and merging

class SettingsError(Exception):
    pass


def read_settings(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SettingsError("%s is not readable JSON (%s); fix or move it first, nothing was written" % (path, e))
    if not isinstance(data, dict):
        raise SettingsError("%s must hold one JSON object; nothing was written" % path)
    perms = data.get("permissions", {})
    if not isinstance(perms, dict):
        raise SettingsError("%s: \"permissions\" must be an object; nothing was written" % path)
    for key in ("allow", "deny", "ask"):
        if key in perms and not (isinstance(perms[key], list) and all(isinstance(x, str) for x in perms[key])):
            raise SettingsError("%s: permissions.%s must be a list of strings; nothing was written" % (path, key))
    return data


def stale_allow(rules, plugin_root):
    """Allow rules for this plugin's tools that point at a different plugin folder, or that an earlier
    version wrote in a form the skills no longer use (`exec python3 ...`, any pgrep / pkill of a watcher)."""
    root = os.path.abspath(plugin_root).rstrip("/")
    out = []
    for r in rules:
        if r in LEGACY_BASH:
            out.append(r)
            continue
        if MANAGED_WATCHER.match(r):
            out.append(r)
            continue
        m = MANAGED_TOOL.match(r)
        if m and (m.group("root") != root or r.startswith("Bash(exec ")):
            out.append(r)
    return out


def merge(current, want, plugin_root):
    """Return the merged settings dict (a new object; `current` is not changed)."""
    data = json.loads(json.dumps(current)) if current else {}
    perms = data.setdefault("permissions", {})
    for key in ("deny", "ask", "allow"):
        existing = list(perms.get(key, []))
        if key == "allow":
            stale = set(stale_allow(existing, plugin_root))
            existing = [r for r in existing if r not in stale]
        for rule in want[key]:
            if rule not in existing:
                existing.append(rule)
        perms[key] = existing
    return data


def web_was_allowed(current):
    """True when the settings file already allows WebSearch or a news site: the owner said yes to the web
    question at setup, so --check also looks for the news-site rules."""
    allow = ((current or {}).get("permissions") or {}).get("allow") or []
    return any(r == "WebSearch" or FETCH_RULE.match(r) for r in allow)


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- CLI

def check(current, want, plugin_root, path, strict):
    perms = (current or {}).get("permissions", {})
    deny, ask, allow = (set(perms.get(k, [])) for k in ("deny", "ask", "allow"))
    missing_deny = [r for r in required_deny(want) if r not in deny]
    if current is None:
        print("FAIL: %s does not exist; run /trader-setup (permissions step)" % path)
        return 1
    if missing_deny:
        print("FAIL: %s is missing %d deny entr%s:" % (path, len(missing_deny), "y" if len(missing_deny) == 1 else "ies"))
        for r in missing_deny:
            print("  - " + r)
        return 1
    drift = []
    drift += ["deny missing (plugin folder moved?): " + r for r in want["deny"] if r not in deny and r not in missing_deny]
    drift += ["ask missing: " + r for r in want["ask"] if r not in ask]
    drift += ["allow missing: " + r for r in want["allow"] if r not in allow]
    drift += ["allow stale (old plugin folder or old form): " + r for r in stale_allow(perms.get("allow", []), plugin_root)]
    wanted_fetch = set(r for r in want["allow"] if FETCH_RULE.match(r))
    if wanted_fetch:
        drift += ["allow for a site that is not in trading/NEWS-SOURCES.md (remove it by hand if it is not "
                  "yours): " + r for r in perms.get("allow", []) if FETCH_RULE.match(r) and r not in wanted_fetch]
    overlap = sorted(set(want["deny"]) & allow)
    drift += ["allowed AND denied (deny wins): " + r for r in overlap]
    print("OK: %s has every deny entry (%d)" % (path, len(required_deny(want))))
    for d in drift:
        print("  WARN: " + d)
    if drift:
        print("  Re-run setup_settings.py (shows a diff, --yes to write) to bring the allow list up to date.")
    return 3 if (drift and strict) else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Write or merge the workspace .claude/settings.json.")
    p.add_argument("--workspace")
    p.add_argument("--plugin-root", default=os.path.dirname(HERE))
    p.add_argument("--web", action="store_true",
                   help="also allow WebSearch and the sites in trading/NEWS-SOURCES.md for the prep run's news")
    p.add_argument("--harness", action="store_true", help="test workspace: also deny alert and watchlist writes")
    p.add_argument("--deny-only", action="store_true", help="write the deny and ask rules but no allow rules")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--yes", action="store_true", help="write the merged file")
    mode.add_argument("--check", action="store_true", help="exit 1 if a deny entry is missing")
    mode.add_argument("--print-rules", action="store_true")
    p.add_argument("--strict", action="store_true", help="with --check: exit 3 on allow/ask drift")
    a = p.parse_args(argv)

    ws = os.path.abspath(a.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = os.path.abspath(a.plugin_root)
    if not os.path.isfile(os.path.join(root, "tools", "order_guard.py")):
        print("setup_settings: %s is not the plugin folder (no tools/order_guard.py)" % root, file=sys.stderr)
        return 2
    if os.path.commonpath([ws, root]) == root:
        print("setup_settings: the workspace must not be inside the plugin folder", file=sys.stderr)
        return 2
    want = wanted(root, ws, web=a.web, harness=a.harness, deny_only=a.deny_only)
    if a.print_rules:
        sys.stdout.write(dump(want))
        return 0

    path = os.path.join(ws, SETTINGS_RELATIVE)
    try:
        current = read_settings(path)
    except SettingsError as e:
        print("setup_settings: " + str(e), file=sys.stderr)
        return 2
    if a.check:
        if not a.web and not a.deny_only and web_was_allowed(current):
            want = wanted(root, ws, web=True, harness=a.harness)
        return check(current, want, root, path, a.strict)

    merged = merge(current, want, root)
    old_text = dump(current) if current is not None else ""
    new_text = dump(merged)
    if current is not None and merged == current:
        print("OK: %s already has every rule; nothing to change" % path)
        return 0
    print("Changes to %s:" % path)
    sys.stdout.writelines(difflib.unified_diff(
        old_text.splitlines(True), new_text.splitlines(True),
        "settings.json (current)" if current is not None else "settings.json (new file)",
        "settings.json (with Personal AI Trader rules)"))
    if not a.yes:
        print("\nNothing written. Run again with --yes to write these changes.")
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if current is not None:
        backup = "%s.bak-%s" % (path, datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(path, backup)
        print("backup:", backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, path)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
