#!/usr/bin/env python3
"""Workspace helper for /trader-setup: the trading/ tree, the templates, and every write to trader.json the
setup makes. Standard library only. Each command checks before it writes and is safe to re-run; nothing the
owner wrote is ever overwritten.

Usage (every command takes --workspace PATH; default $CLAUDE_PROJECT_DIR, else the current directory):
  init                       create trading/ and its folders (journal, reviews, shadow, ledger, logs), copy
                             PLAN.md, ROBINHOOD-SOURCES.md, NEWS-SOURCES.md and shadow/ from templates/ if
                             missing, and create trading/RULES-notes.md with its header if missing
  strategy-sample            copy templates/STRATEGY.sample.md to trading/STRATEGY.md if it is missing
  config --account-number N --nickname NAME --owner NAME --start-balance AMOUNT
         [--timezone ZONE] [--today YYYY-MM-DD] [--force]
                             write the default DRY_RUN trader.json from templates/trader.example.json:
                             option cap 20% of the start, share cap 30%, halt at 70%, the other defaults as
                             in the example, rules_set_on today. An existing config for the same account is
                             left alone; one for another account needs --force (the old file is backed up).
  set KEY=VALUE ... [--yes]  change settable keys (limits.*, options.*, allowed.*, account.start_balance,
                             account.nickname, owner_name, timezone, watchlist.name, watchlist.id,
                             notifications.push, rules_set_on, schedule.sunday_review_local,
                             schedule.offset_minutes). VALUE is JSON
                             (a number, true/false, null, "text") or plain text. Prints a diff and the sanity
                             warnings; writes only with --yes and only a config that validates.
  notes --add TEXT           append a dated line to trading/RULES-notes.md (created with its header if missing)
  gate                       the go-live checks that live in files, as JSON: paper trading days (journal days
                             with a wrap entry) against go_live.paper_days_required, a Sunday review after the
                             first paper day, rules_set_on, phase DRY_RUN. Exit 0 when they all pass, else 1.
                             The checks that need Robinhood or this session (funding, approval setting, guard
                             proof) are the skill's.
  go-live --confirm go [--today YYYY-MM-DD]
                             DRY_RUN -> LIVE: sets go_live.met true, go_live.approved_at and phase LIVE. Refuses
                             unless `gate` passes and --confirm is exactly "go" (the owner's word).
  phase --from HALTED --to DRY_RUN|LIVE --confirm yes
                             leave HALTED on the owner's explicit choice. LIVE only if go_live.met is true.
  halt --reason TEXT [--today YYYY-MM-DD]
                             DRY_RUN or LIVE -> HALTED, nothing else (the check routine's halt at the halt
                             level). Changes only `phase`, appends one line to today's journal, and is
                             idempotent: already HALTED prints OK and writes nothing. It can never leave
                             HALTED, set LIVE or change a limit, so the workspace settings allow it unattended.
  extend-paper --expect N    one more paper week on the owner's answer: go_live.extensions N -> N+1, nothing
                             else. DRY_RUN only, go_live.met false. --expect is the value the caller read, so
                             a repeated call refuses instead of adding a second week. Not allowed unattended:
                             the owner is present when they answer, so the call prompts.
  detect-timezone            print the system time zone (what config uses when --timezone is not given)

Exit codes: 0 done (or nothing to do), 1 refused or a check failed (the reason is printed), 2 usage or an
invalid config.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    sys.exit("setup_workspace: needs Python 3.9 or later (zoneinfo)")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader_config  # noqa: E402

PLUGIN = os.path.dirname(HERE)
TEMPLATES = os.path.join(PLUGIN, "templates")
EXAMPLE = os.path.join(TEMPLATES, "trader.example.json")
FOLDERS = ("journal", "reviews", "shadow", "ledger", "logs")
COPIES = (("PLAN.md", "PLAN.md"), ("ROBINHOOD-SOURCES.md", "ROBINHOOD-SOURCES.md"),
          ("NEWS-SOURCES.md", "NEWS-SOURCES.md"))
NOTES = "RULES-notes.md"
NOTES_HEADER = (
    "<!-- Owner's notes on the rules. tools/render_rules.py copies this file verbatim into RULES.md under\n"
    "     \"Owner's notes\". Notes record decisions and reasons; they never change a limit. The limits are in\n"
    "     trader.json, which is what the order guard and the routines read. Add a line per decision, dated. -->\n"
)
OPTION_CAP_SHARE = Decimal("0.20")
SHARE_CAP_SHARE = Decimal("0.30")
HALT_SHARE = Decimal("0.70")

SETTABLE_PREFIXES = ("limits.", "options.", "allowed.")
SETTABLE_KEYS = {"account.start_balance", "account.nickname", "owner_name", "timezone", "watchlist.name",
                 "watchlist.id", "notifications.push", "rules_set_on", "schedule.sunday_review_local",
                 "schedule.offset_minutes"}
NEW_KEYS_OK = {"rules_set_on", "schedule.sunday_review_local", "schedule.offset_minutes"}
NOT_SETTABLE = {
    "phase": "the phase changes only through `go-live`, `phase` or `halt`",
    "go_live": "the go-live gate changes only through `go-live` (and `extend-paper` for go_live.extensions)",
    "account.number": "a different account needs `config --force` (a new default config)",
    "schema": "the schema number is the plugin's",
}
WRAP_ENTRY = re.compile(r"^## \d{1,2}:\d{2} wrap\b", re.M)
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def out(*parts):
    print(*parts)


def fail(msg, code=1):
    print("setup_workspace: " + msg, file=sys.stderr)
    return code


def workspace_of(a):
    return os.path.abspath(a.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def trading(ws, *parts):
    return os.path.join(ws, "trading", *parts)


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path, text, backup=False):
    if backup and os.path.exists(path):
        b = "%s.bak-%s" % (path, datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(path, b)
        out("backup:", b)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def copy_if_missing(src, dst):
    if os.path.exists(dst):
        out("exists ", dst)
        return False
    shutil.copy2(src, dst)
    out("created", dst)
    return True


# ---------------------------------------------------------------- init / notes / strategy

def ensure_notes(ws):
    path = trading(ws, NOTES)
    if os.path.exists(path):
        out("exists ", path)
        return path
    os.makedirs(trading(ws), exist_ok=True)
    write_atomic(path, NOTES_HEADER)
    out("created", path)
    return path


def cmd_init(ws):
    for d in ("",) + FOLDERS:
        path = trading(ws, d) if d else trading(ws)
        if os.path.isdir(path):
            out("exists ", path + "/")
        else:
            os.makedirs(path)
            out("created", path + "/")
    for src, dst in COPIES:
        copy_if_missing(os.path.join(TEMPLATES, src), trading(ws, dst))
    shadow_src = os.path.join(TEMPLATES, "shadow")
    for name in sorted(os.listdir(shadow_src)):
        if name.endswith(".md"):
            copy_if_missing(os.path.join(shadow_src, name), trading(ws, "shadow", name))
    ensure_notes(ws)
    return 0


def cmd_strategy_sample(ws):
    os.makedirs(trading(ws), exist_ok=True)
    copy_if_missing(os.path.join(TEMPLATES, "STRATEGY.sample.md"), trading(ws, "STRATEGY.md"))
    return 0


def cmd_notes(ws, text, today):
    path = ensure_notes(ws)
    line = "- %s: %s\n" % (today, " ".join(text.split()))
    with open(path, encoding="utf-8") as f:
        current = f.read()
    if line in current:
        out("exists ", "note already recorded")
        return 0
    with open(path, "a", encoding="utf-8") as f:
        if current and not current.endswith("\n"):
            f.write("\n")
        f.write(line)
    out("added  ", line.strip())
    return 0


# ---------------------------------------------------------------- timezone and dates

def detect_timezone(environ=None):
    environ = os.environ if environ is None else environ
    tz = environ.get("TZ", "").lstrip(":")
    candidates = [tz] if tz else []
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        with open("/etc/timezone", encoding="utf-8") as f:
            candidates.append(f.read().strip())
    except OSError:
        pass
    for c in candidates:
        if valid_timezone(c):
            return c
    return "America/New_York"


def valid_timezone(name):
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def today_in(tz):
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def parse_day(text):
    try:
        return date.fromisoformat(text).isoformat()
    except (TypeError, ValueError):
        raise SystemExit(fail("not a date (YYYY-MM-DD): %r" % (text,), 2))


# ---------------------------------------------------------------- config

def whole(d):
    return int(d.to_integral_value(rounding=ROUND_FLOOR))


def default_config(number, nickname, owner, start, tz, today):
    with open(EXAMPLE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["owner_name"] = owner
    cfg["phase"] = "DRY_RUN"
    cfg["account"] = {"number": number, "nickname": nickname,
                      "start_balance": int(start) if start == start.to_integral_value() else float(start)}
    cfg["timezone"] = tz
    cfg["limits"]["option_cap_dollars"] = whole(start * OPTION_CAP_SHARE)
    cfg["limits"]["share_cap_dollars"] = whole(start * SHARE_CAP_SHARE)
    cfg["limits"]["halt_account_value"] = int((start * HALT_SHARE).to_integral_value(rounding=ROUND_HALF_UP))
    cfg["watchlist"]["id"] = None
    cfg["go_live"].update(met=False, approved_at=None, extensions=0)
    cfg["rules_set_on"] = today
    return cfg


def load_raw(ws):
    path = trader_config.config_path(ws)
    with open(path, encoding="utf-8") as f:
        return path, json.load(f)


def validated(cfg, path):
    """trader_config's verdict, or (None, problems)."""
    try:
        return trader_config.validate(cfg, path), []
    except trader_config.ConfigError as e:
        return None, e.problems


def cmd_config(ws, a):
    tz = a.timezone or detect_timezone()
    if not valid_timezone(tz):
        return fail("unknown time zone %r (use an IANA name such as America/Chicago)" % tz, 2)
    try:
        start = Decimal(str(a.start_balance))
    except InvalidOperation:
        return fail("--start-balance must be a number", 2)
    if not start.is_finite() or start < 50:
        return fail("--start-balance must be at least 50 (got %s)" % a.start_balance, 2)
    number = a.account_number.strip()
    if not number or number != a.account_number or " " in number:
        return fail("--account-number must be the account number without spaces", 2)
    today = parse_day(a.today) if a.today else today_in(tz)
    path = trader_config.config_path(ws)
    if os.path.exists(path):
        try:
            _, current = load_raw(ws)
        except (OSError, ValueError) as e:
            current = None
            if not a.force:
                return fail("%s exists but is unreadable (%s); fix it or pass --force" % (path, e))
        same = isinstance(current, dict) and isinstance(current.get("account"), dict) \
            and current["account"].get("number") == number
        if same and not a.force:
            out("exists ", "%s already configures account ending %s; left unchanged" % (path, number[-4:]))
            return 0
        if not a.force:
            return fail("%s configures a different account; pass --force to replace it (it is backed up)" % path)
    cfg = default_config(number, a.nickname, a.owner, start, tz, today)
    checked, problems = validated(cfg, path)
    if checked is None:
        return fail("the default config does not validate: " + "; ".join(problems), 2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, dump(cfg), backup=True)
    out("wrote  ", path)
    out("  phase DRY_RUN, account ending %s, start %s, option cap %s, share cap %s, halt at %s, timezone %s"
        % (number[-4:], cfg["account"]["start_balance"], cfg["limits"]["option_cap_dollars"],
           cfg["limits"]["share_cap_dollars"], cfg["limits"]["halt_account_value"], tz))
    for w in checked.warnings:
        out("  warning: " + w)
    return 0


def parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def settable(key):
    for bad, why in NOT_SETTABLE.items():
        if key == bad or key.startswith(bad + "."):
            return why
    if key in SETTABLE_KEYS or key.startswith(SETTABLE_PREFIXES):
        return None
    return "not a key setup changes (settable: %s, %s)" % (", ".join(p + "*" for p in SETTABLE_PREFIXES),
                                                           ", ".join(sorted(SETTABLE_KEYS)))


def sanity(cfg):
    """Warnings beyond trader_config's own: answers that are legal but probably not meant."""
    w = []
    lim = cfg.get("limits", {})
    start = cfg.get("account", {}).get("start_balance")
    try:
        if lim["max_same_theme"] > lim["max_open_positions"]:
            w.append("limits.max_same_theme is above limits.max_open_positions, so it never binds")
        if lim["cash_floor_pct"] < 10:
            w.append("limits.cash_floor_pct below 10% leaves almost no cash buffer")
        if lim["daily_loss_stop_pct"] > 20:
            w.append("limits.daily_loss_stop_pct above 20% would rarely stop anything")
        if lim["max_open_positions"] > 10:
            w.append("more than 10 open positions is a lot to manage with hourly checks")
        if isinstance(start, (int, float)) and start > 0:
            if lim["halt_account_value"] < start * 0.5:
                w.append("limits.halt_account_value is below half the start balance")
            if lim["option_cap_dollars"] < 50:
                w.append("an option cap under 50 dollars leaves few contracts to buy")
    except (KeyError, TypeError):
        pass
    if cfg.get("allowed", {}).get("long_options") is False and cfg.get("allowed", {}).get("shares") is False:
        w.append("allowed.long_options and allowed.shares are both false: nothing can be bought")
    return w


def set_path(cfg, key, value):
    parts = key.split(".")
    node = cfg
    for i, part in enumerate(parts[:-1]):
        if part not in node:
            if ".".join(parts[:i + 1]) == "schedule":
                node[part] = {}
            else:
                raise KeyError(key)
        node = node[part]
        if not isinstance(node, dict):
            raise KeyError(key)
    if parts[-1] not in node and key not in NEW_KEYS_OK:
        raise KeyError(key)
    node[parts[-1]] = value


def show_diff(old, new, label="trader.json"):
    sys.stdout.writelines(difflib.unified_diff(dump(old).splitlines(True), dump(new).splitlines(True),
                                               label + " (current)", label + " (new)"))


def cmd_set(ws, pairs, yes):
    try:
        path, cfg = load_raw(ws)
    except FileNotFoundError:
        return fail("no trading/trader.json yet; run `config` first", 2)
    except (OSError, ValueError) as e:
        return fail("trading/trader.json is unreadable (%s)" % e, 2)
    new = json.loads(json.dumps(cfg))
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            return fail("expected KEY=VALUE, got %r" % pair, 2)
        why = settable(key)
        if why:
            return fail("%s: %s" % (key, why))
        try:
            set_path(new, key, parse_value(raw))
        except KeyError:
            return fail("%s is not a key in trader.json (typo?)" % key, 2)
    checked, problems = validated(new, path)
    if checked is None:
        out("REFUSED: the change would make trader.json invalid:")
        for p in problems:
            out("  - " + p)
        return 1
    if new == cfg:
        out("OK: nothing to change")
        return 0
    show_diff(cfg, new)
    for w in checked.warnings + sanity(new):
        out("  warning: " + w)
    if not yes:
        out("\nNothing written. Run again with --yes to write these changes.")
        return 0
    write_atomic(path, dump(new), backup=True)
    out("wrote  ", path)
    return 0


# ---------------------------------------------------------------- go-live gate and phase

def gate(ws):
    try:
        cfg = trader_config.load(trader_config.config_path(ws))
    except trader_config.ConfigError as e:
        return {"pass": False, "problems": ["trader.json is missing or invalid: " + "; ".join(e.problems)]}
    jdir, rdir = trading(ws, "journal"), trading(ws, "reviews")
    paper_days = []
    if os.path.isdir(jdir):
        for name in sorted(os.listdir(jdir)):
            m = DAY_FILE.match(name)
            if not m:
                continue
            with open(os.path.join(jdir, name), encoding="utf-8") as f:
                if WRAP_ENTRY.search(f.read()):
                    paper_days.append(m.group(1))
    reviews = []
    if os.path.isdir(rdir):
        for name in sorted(os.listdir(rdir)):
            m = DAY_FILE.match(name)
            if m and date.fromisoformat(m.group(1)).weekday() == 6:
                if paper_days and m.group(1) > paper_days[0]:
                    reviews.append(m.group(1))
    required = cfg.go_live.paper_days_required
    problems = []
    if cfg.phase != "DRY_RUN":
        problems.append("phase is %s, not DRY_RUN" % cfg.phase)
    if cfg.go_live.met:
        problems.append("go_live.met is already true")
    if len(paper_days) < required:
        problems.append("%d paper trading day(s) with a wrap entry, %d required" % (len(paper_days), required))
    if not reviews:
        problems.append("no Sunday review (trading/reviews/YYYY-MM-DD.md on a Sunday) after the first paper day")
    if not cfg.rules_set_on:
        problems.append("rules_set_on is not recorded: the owner has not set the limits (run --limits)")
    return {"pass": not problems, "paper_days": len(paper_days), "paper_days_required": required,
            "first_paper_day": paper_days[0] if paper_days else None,
            "last_paper_day": paper_days[-1] if paper_days else None,
            "sunday_reviews": reviews, "rules_set_on": cfg.rules_set_on, "phase": cfg.phase,
            "extensions": cfg.go_live.extensions, "start_balance": str(cfg.account.start_balance),
            "problems": problems,
            "not_checked_here": ["account funded as stated (get_portfolio)", "approval setting read "
                                 "(get_trade_approval_setting)", "guard proof passed this session"]}


def cmd_gate(ws):
    result = gate(ws)
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


def cmd_go_live(ws, confirm, today):
    if confirm != "go":
        return fail('refused: --confirm must be exactly "go", the owner\'s own word')
    result = gate(ws)
    if not result["pass"]:
        out("REFUSED: the go-live gate is not met:")
        for p in result["problems"]:
            out("  - " + p)
        return 1
    path, cfg = load_raw(ws)
    new = json.loads(json.dumps(cfg))
    day = parse_day(today) if today else today_in(cfg["timezone"])
    new["go_live"].update(met=True, approved_at=day)
    new["phase"] = "LIVE"
    checked, problems = validated(new, path)
    if checked is None:
        return fail("the LIVE config does not validate: " + "; ".join(problems), 2)
    show_diff(cfg, new)
    write_atomic(path, dump(new), backup=True)
    out("wrote  ", path, "(phase LIVE, go_live.met true, approved %s)" % day)
    return 0


def cmd_phase(ws, src, dst, confirm):
    if confirm != "yes":
        return fail("refused: leaving %s needs --confirm yes after the owner chose %s" % (src, dst))
    if src != "HALTED" or dst not in ("DRY_RUN", "LIVE"):
        return fail("only HALTED -> DRY_RUN or HALTED -> LIVE is done here", 2)
    try:
        path, cfg = load_raw(ws)
    except (OSError, ValueError) as e:
        return fail("trading/trader.json is missing or unreadable (%s)" % e, 2)
    if cfg.get("phase") != src:
        return fail("refused: phase is %r, not %s" % (cfg.get("phase"), src))
    if dst == "LIVE" and not (cfg.get("go_live") or {}).get("met"):
        return fail("refused: LIVE needs the go-live gate (go_live.met is false); resume to DRY_RUN")
    new = json.loads(json.dumps(cfg))
    new["phase"] = dst
    checked, problems = validated(new, path)
    if checked is None:
        return fail("the new config does not validate: " + "; ".join(problems), 2)
    show_diff(cfg, new)
    write_atomic(path, dump(new), backup=True)
    out("wrote  ", path, "(phase %s)" % dst)
    return 0


HALT_FROM = ("DRY_RUN", "LIVE")
REASON_MAX = 300


def journal_append(ws, day, line):
    path = trading(ws, "journal", "%s.md" % day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    current = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            current = f.read()
    with open(path, "a", encoding="utf-8") as f:
        if current and not current.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return path


def cmd_halt(ws, reason, today):
    """DRY_RUN or LIVE -> HALTED. The only change is `phase`; it only tightens (the guard then refuses every
    new opening order), so it is safe to run without the owner. Idempotent."""
    reason = " ".join((reason or "").split())
    day = parse_day(today) if today else None
    if not reason:
        return fail("refused: --reason must say why (for the journal and the owner)", 2)
    if len(reason) > REASON_MAX:
        return fail("refused: --reason is longer than %d characters" % REASON_MAX, 2)
    try:
        path, cfg = load_raw(ws)
    except (OSError, ValueError) as e:
        return fail("trading/trader.json is missing or unreadable (%s); the guard is refusing every account "
                    "call already" % e, 2)
    if not isinstance(cfg, dict):
        return fail("trading/trader.json must hold one JSON object", 2)
    phase = cfg.get("phase")
    if phase == "HALTED":
        out("OK: phase is already HALTED; nothing to change")
        return 0
    if phase not in HALT_FROM:
        return fail("refused: halt moves only DRY_RUN or LIVE to HALTED (phase is %r)" % (phase,))
    new = json.loads(json.dumps(cfg))
    new["phase"] = "HALTED"
    checked, problems = validated(new, path)
    if checked is None:
        return fail("refused: the halted config does not validate: " + "; ".join(problems), 2)
    tz = cfg.get("timezone") if valid_timezone(cfg.get("timezone")) else "America/New_York"
    day = day or today_in(tz)
    show_diff(cfg, new)
    write_atomic(path, dump(new), backup=True)
    out("wrote  ", path, "(phase %s -> HALTED)" % phase)
    clock = datetime.now(ZoneInfo(tz)).strftime("%H:%M")
    jpath = journal_append(ws, day, "- %s HALT: phase %s -> HALTED (setup_workspace.py halt). Reason: %s"
                           % (clock, phase, reason))
    out("journal", jpath)
    return 0


def cmd_extend_paper(ws, expect):
    """go_live.extensions N -> N+1 on the owner's "one more week". Nothing else changes."""
    try:
        path, cfg = load_raw(ws)
    except (OSError, ValueError) as e:
        return fail("trading/trader.json is missing or unreadable (%s)" % e, 2)
    if not isinstance(cfg, dict) or not isinstance(cfg.get("go_live"), dict):
        return fail("trading/trader.json has no go_live block", 2)
    if cfg.get("phase") != "DRY_RUN":
        return fail("refused: extend-paper is for the paper period (phase is %r, not DRY_RUN)" % (cfg.get("phase"),))
    g = cfg["go_live"]
    if g.get("met"):
        return fail("refused: go_live.met is already true")
    current = g.get("extensions")
    if isinstance(current, bool) or not isinstance(current, int):
        return fail("go_live.extensions is not a whole number (%r)" % (current,), 2)
    if expect != current:
        return fail("refused: go_live.extensions is %d, not the expected %d (already extended?)" % (current, expect))
    new = json.loads(json.dumps(cfg))
    new["go_live"]["extensions"] = current + 1
    checked, problems = validated(new, path)
    if checked is None:
        return fail("the new config does not validate: " + "; ".join(problems), 2)
    show_diff(cfg, new)
    write_atomic(path, dump(new), backup=True)
    out("wrote  ", path, "(go_live.extensions %d -> %d)" % (current, current + 1))
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description="Workspace helper for /trader-setup.")
    p.add_argument("--workspace")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "strategy-sample", "gate", "detect-timezone"):
        s = sub.add_parser(name)
        s.add_argument("--workspace", dest="ws2")
    c = sub.add_parser("config")
    c.add_argument("--workspace", dest="ws2")
    c.add_argument("--account-number", required=True)
    c.add_argument("--nickname", required=True)
    c.add_argument("--owner", required=True)
    c.add_argument("--start-balance", required=True)
    c.add_argument("--timezone")
    c.add_argument("--today")
    c.add_argument("--force", action="store_true")
    s = sub.add_parser("set")
    s.add_argument("--workspace", dest="ws2")
    s.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    s.add_argument("--yes", action="store_true")
    n = sub.add_parser("notes")
    n.add_argument("--workspace", dest="ws2")
    n.add_argument("--add", required=True)
    n.add_argument("--today")
    g = sub.add_parser("go-live")
    g.add_argument("--workspace", dest="ws2")
    g.add_argument("--confirm", required=True)
    g.add_argument("--today")
    ph = sub.add_parser("phase")
    ph.add_argument("--workspace", dest="ws2")
    ph.add_argument("--from", dest="src", required=True)
    ph.add_argument("--to", dest="dst", required=True)
    ph.add_argument("--confirm", required=True)
    # halt and extend-paper take no --workspace of their own (use the global one, before the command), so the
    # narrow allow rule for `halt --reason *` covers exactly this command in this workspace.
    h = sub.add_parser("halt", allow_abbrev=False)
    h.add_argument("--reason", required=True)
    h.add_argument("--today")
    e = sub.add_parser("extend-paper", allow_abbrev=False)
    e.add_argument("--expect", type=int, required=True)
    a = p.parse_args(argv)
    if getattr(a, "ws2", None):
        a.workspace = a.ws2
    ws = workspace_of(a)

    if a.cmd == "init":
        return cmd_init(ws)
    if a.cmd == "strategy-sample":
        return cmd_strategy_sample(ws)
    if a.cmd == "detect-timezone":
        out(detect_timezone())
        return 0
    if a.cmd == "config":
        return cmd_config(ws, a)
    if a.cmd == "set":
        return cmd_set(ws, a.pairs, a.yes)
    if a.cmd == "notes":
        if a.today:
            day = parse_day(a.today)
        else:
            try:
                day = today_in(load_raw(ws)[1]["timezone"])
            except Exception:
                day = date.today().isoformat()
        return cmd_notes(ws, a.add, day)
    if a.cmd == "gate":
        return cmd_gate(ws)
    if a.cmd == "go-live":
        return cmd_go_live(ws, a.confirm, a.today)
    if a.cmd == "phase":
        return cmd_phase(ws, a.src, a.dst, a.confirm)
    if a.cmd == "halt":
        return cmd_halt(ws, a.reason, a.today)
    if a.cmd == "extend-paper":
        return cmd_extend_paper(ws, a.expect)
    return 2


if __name__ == "__main__":
    sys.exit(main())
