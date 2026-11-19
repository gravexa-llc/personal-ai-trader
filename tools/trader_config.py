#!/usr/bin/env python3
"""Load and validate a workspace's `trading/trader.json`.

Used by the order guard at trade time and later by the other tools and the doctor, so it is
standard library only and runs on Python 3.9.

Finding the file:
  - if CLAUDE_PROJECT_DIR is set (Claude Code sets it for hooks), the config is
    $CLAUDE_PROJECT_DIR/trading/trader.json and nothing else; if it is not there, loading fails.
  - otherwise walk up from the current directory and take the first trading/trader.json found.

Errors and warnings:
  - anything missing, of the wrong type, out of range or contradictory that the guard or the routines
    depend on is an error: load() raises ConfigError listing every problem found.
  - sanity rules about the size of the limits (a cap above 30% of the start balance, a halt level at or
    above the start balance, ...) are warnings: they are returned on the config object and printed by
    --check, but they do not stop the guard from working.

Optional keys (absent in older configs; defaults apply):
  - schedule.sunday_review_local  "HH:MM" local time of the Sunday review run (default 15:03).
  - schedule.offset_minutes       0 to 3 (default 0): trading-day runs fire this many minutes later, for a
                                  second workspace on the same Robinhood login.
  - rules_set_on                  "YYYY-MM-DD" the day the owner last set the limits; RULES.md quotes it.

The other tools find their workspace with find_workspace(): an explicit --workspace PATH, else
$CLAUDE_PROJECT_DIR, else the walk up described above.

CLI:  python3 tools/trader_config.py --check [--path FILE]
      prints "OK: <path>" (plus any warnings) and exits 0, or "PROBLEMS in <path>:" and one line per
      problem and exits 1.
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

SCHEMA_VERSION = 1
PHASES = ("DRY_RUN", "LIVE", "HALTED")
CONFIG_RELATIVE = os.path.join("trading", "trader.json")
TRADES_RELATIVE = os.path.join("trading", "ledger", "trades.csv")
MAX_CAP_SHARE_OF_START = Decimal("0.30")
# The product has no switches for spreads, selling to open, shorting or crypto,
# so `allowed` accepts exactly these keys and nothing else.
ALLOWED_KEYS = ("long_options", "shares")
KNOWN_TOP_LEVEL = {
    "schema", "owner_name", "phase", "account", "timezone", "limits", "options", "allowed",
    "watchlist", "notifications", "go_live", "schedule", "rules_set_on",
}
DEFAULT_SUNDAY_REVIEW_LOCAL = "15:03"
# Largest schedule.offset_minutes: keeps the closing mark (15:56 ET) before the close and every run off :00/:30.
MAX_SCHEDULE_OFFSET = 3
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ConfigError(Exception):
    """The config is missing, unreadable or invalid. `problems` lists every problem found."""

    def __init__(self, path, problems):
        self.path = path
        self.problems = list(problems)
        super().__init__("%s: %s" % (path, "; ".join(self.problems)))


@dataclass(frozen=True)
class Account:
    number: str
    nickname: str
    start_balance: Decimal


@dataclass(frozen=True)
class Limits:
    option_cap_dollars: Decimal
    share_cap_dollars: Decimal
    cash_floor_pct: Decimal
    max_open_positions: int
    max_same_theme: int
    daily_loss_stop_pct: Decimal
    halt_account_value: Decimal
    loss_streak_pause: int


@dataclass(frozen=True)
class Ratchet:
    trigger_gain_pct: Decimal
    lock_gain_pct: Decimal
    trail_pct: Decimal


@dataclass(frozen=True)
class StopMarketAfterGain:
    enabled: bool
    max_spread_pct: Decimal
    not_before: str
    raise_max_spread_pct: Decimal = None  # optional; equals max_spread_pct when unset


@dataclass(frozen=True)
class Options:
    expiry_days_min: int
    expiry_days_max: int
    min_open_interest: int
    max_spread_pct_of_mid: Decimal
    default_stop_pct: Decimal
    ratchet: Ratchet
    overnight_cushion: Decimal
    stop_market_after_gain: StopMarketAfterGain


@dataclass(frozen=True)
class Allowed:
    long_options: bool
    shares: bool


@dataclass(frozen=True)
class Watchlist:
    name: str
    id: Optional[str]


@dataclass(frozen=True)
class GoLive:
    paper_days_required: int
    extensions: int
    met: bool
    approved_at: Optional[str]


@dataclass(frozen=True)
class TraderConfig:
    path: str
    schema: int
    owner_name: str
    phase: str
    account: Account
    timezone: str
    limits: Limits
    options: Options
    allowed: Allowed
    watchlist: Watchlist
    push_notifications: bool
    go_live: GoLive
    warnings: List[str] = field(default_factory=list)
    sunday_review_local: str = DEFAULT_SUNDAY_REVIEW_LOCAL
    rules_set_on: Optional[str] = None
    schedule_offset_minutes: int = 0

    @property
    def workspace(self):
        """The folder that holds `trading/` (two levels above trader.json)."""
        return os.path.dirname(os.path.dirname(os.path.abspath(self.path)))

    @property
    def trades_path(self):
        return os.path.join(self.workspace, TRADES_RELATIVE)

    @property
    def stop_market_min_multiple(self):
        """A guarded option stop-market must sit at or above entry x this (1 + lock gain)."""
        return (Decimal("1") + self.options.ratchet.lock_gain_pct / Decimal("100")).normalize()


# ---------------------------------------------------------------- finding the file

def find_config(environ=None, cwd=None):
    """Return the path where the config should be. Does not check that it parses."""
    environ = os.environ if environ is None else environ
    project = environ.get("CLAUDE_PROJECT_DIR")
    if project:
        return os.path.join(project, CONFIG_RELATIVE)
    here = os.path.abspath(cwd or os.getcwd())
    while True:
        candidate = os.path.join(here, CONFIG_RELATIVE)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            raise ConfigError(os.path.join(os.path.abspath(cwd or os.getcwd()), CONFIG_RELATIVE),
                              ["no trading/trader.json found in this folder or any folder above it"])
        here = parent


def find_workspace(explicit=None, environ=None, cwd=None):
    """The workspace folder (the one holding trading/): `explicit` if given, else $CLAUDE_PROJECT_DIR,
    else the first folder at or above the current directory with trading/trader.json. Raises ConfigError
    when the walk up finds nothing."""
    if explicit:
        return os.path.abspath(explicit)
    return os.path.dirname(os.path.dirname(os.path.abspath(find_config(environ=environ, cwd=cwd))))


def config_path(workspace):
    return os.path.join(workspace, CONFIG_RELATIVE)


# ---------------------------------------------------------------- validation helpers

class _Checker:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def section(self, data, key):
        value = data.get(key) if isinstance(data, dict) else None
        if not isinstance(value, dict):
            self.errors.append("%s must be an object" % key)
            return {}
        return value

    def string(self, data, key, where, allow_none=False, allow_empty=False):
        value = data.get(key)
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            self.errors.append("%s.%s must be a non-empty string%s"
                               % (where, key, " or null" if allow_none else ""))
            return ""
        return value

    def boolean(self, data, key, where):
        value = data.get(key)
        if not isinstance(value, bool):
            self.errors.append("%s.%s must be true or false" % (where, key))
            return False
        return value

    def integer(self, data, key, where, minimum=None):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            self.errors.append("%s.%s must be a whole number" % (where, key))
            return 0
        if minimum is not None and value < minimum:
            self.errors.append("%s.%s must be at least %s (got %s)" % (where, key, minimum, value))
        return value

    def decimal(self, data, key, where, positive=False, minimum=None, maximum=None, below=None):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.errors.append("%s.%s must be a number" % (where, key))
            return Decimal("0")
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            self.errors.append("%s.%s must be a number" % (where, key))
            return Decimal("0")
        if not number.is_finite():
            self.errors.append("%s.%s must be a finite number" % (where, key))
            return Decimal("0")
        if positive and number <= 0:
            self.errors.append("%s.%s must be positive (got %s)" % (where, key, value))
        if minimum is not None and number < minimum:
            self.errors.append("%s.%s must be at least %s (got %s)" % (where, key, minimum, value))
        if maximum is not None and number > maximum:
            self.errors.append("%s.%s must be at most %s (got %s)" % (where, key, maximum, value))
        if below is not None and number >= below:
            self.errors.append("%s.%s must be below %s (got %s)" % (where, key, below, value))
        return number


def validate(data, path="trader.json"):
    """Validate parsed JSON and return a TraderConfig, or raise ConfigError."""
    c = _Checker()
    if not isinstance(data, dict):
        raise ConfigError(path, ["the file must hold one JSON object"])

    schema = data.get("schema")
    if schema != SCHEMA_VERSION or isinstance(schema, bool):
        c.errors.append("schema must be %d (got %r)" % (SCHEMA_VERSION, schema))
    owner = c.string(data, "owner_name", "config")
    phase = data.get("phase")
    if phase not in PHASES:
        c.errors.append("phase must be one of %s (got %r)" % (", ".join(PHASES), phase))
        phase = ""
    timezone = c.string(data, "timezone", "config")
    for key in sorted(set(data) - KNOWN_TOP_LEVEL):
        c.warnings.append("unknown top-level key %r is ignored" % key)

    a = c.section(data, "account")
    number = a.get("number")
    if not isinstance(number, str) or not number.strip() or number != number.strip():
        c.errors.append("account.number must be a non-empty string without spaces (quote it in the JSON)")
        number = ""
    account = Account(number=number, nickname=c.string(a, "nickname", "account"),
                      start_balance=c.decimal(a, "start_balance", "account", positive=True))

    lim = c.section(data, "limits")
    limits = Limits(
        option_cap_dollars=c.decimal(lim, "option_cap_dollars", "limits", positive=True),
        share_cap_dollars=c.decimal(lim, "share_cap_dollars", "limits", positive=True),
        cash_floor_pct=c.decimal(lim, "cash_floor_pct", "limits", minimum=0, below=100),
        max_open_positions=c.integer(lim, "max_open_positions", "limits", minimum=1),
        max_same_theme=c.integer(lim, "max_same_theme", "limits", minimum=1),
        daily_loss_stop_pct=c.decimal(lim, "daily_loss_stop_pct", "limits", positive=True, below=100),
        halt_account_value=c.decimal(lim, "halt_account_value", "limits", minimum=0),
        loss_streak_pause=c.integer(lim, "loss_streak_pause", "limits", minimum=1),
    )

    o = c.section(data, "options")
    r = c.section(o, "ratchet") if o else {}
    s = c.section(o, "stop_market_after_gain") if o else {}
    not_before = c.string(s, "not_before", "options.stop_market_after_gain")
    if not_before and not HHMM.match(not_before):
        c.errors.append("options.stop_market_after_gain.not_before must be HH:MM (got %r)" % not_before)
    sm_max_spread = c.decimal(s, "max_spread_pct", "options.stop_market_after_gain", positive=True)
    # Optional: a working stop-market may be raised with a slightly wider spread than the first swap needs.
    if isinstance(s, dict) and s.get("raise_max_spread_pct") is not None:
        sm_raise_spread = c.decimal(s, "raise_max_spread_pct", "options.stop_market_after_gain", positive=True)
        if sm_raise_spread and sm_max_spread and sm_raise_spread < sm_max_spread:
            c.errors.append("options.stop_market_after_gain.raise_max_spread_pct must not be below max_spread_pct")
    else:
        sm_raise_spread = sm_max_spread
    options = Options(
        expiry_days_min=c.integer(o, "expiry_days_min", "options", minimum=1),
        expiry_days_max=c.integer(o, "expiry_days_max", "options", minimum=1),
        min_open_interest=c.integer(o, "min_open_interest", "options", minimum=0),
        max_spread_pct_of_mid=c.decimal(o, "max_spread_pct_of_mid", "options", positive=True),
        default_stop_pct=c.decimal(o, "default_stop_pct", "options", positive=True, below=100),
        ratchet=Ratchet(
            trigger_gain_pct=c.decimal(r, "trigger_gain_pct", "options.ratchet", positive=True),
            lock_gain_pct=c.decimal(r, "lock_gain_pct", "options.ratchet", positive=True),
            trail_pct=c.decimal(r, "trail_pct", "options.ratchet", positive=True, below=100),
        ),
        overnight_cushion=c.decimal(o, "overnight_cushion", "options", positive=True, maximum=1),
        stop_market_after_gain=StopMarketAfterGain(
            enabled=c.boolean(s, "enabled", "options.stop_market_after_gain"),
            max_spread_pct=sm_max_spread,
            not_before=not_before,
            raise_max_spread_pct=sm_raise_spread,
        ),
    )
    if options.expiry_days_min > options.expiry_days_max:
        c.errors.append("options.expiry_days_min must not be above options.expiry_days_max")
    if r and options.ratchet.lock_gain_pct >= options.ratchet.trigger_gain_pct > 0:
        c.errors.append("options.ratchet.lock_gain_pct must be below trigger_gain_pct")

    al = c.section(data, "allowed")
    for key in sorted(set(al) - set(ALLOWED_KEYS)):
        c.errors.append("allowed.%s is not a switch this product has (only %s)" % (key, ", ".join(ALLOWED_KEYS)))
    allowed = Allowed(long_options=c.boolean(al, "long_options", "allowed"),
                      shares=c.boolean(al, "shares", "allowed"))

    w = c.section(data, "watchlist")
    watchlist = Watchlist(name=c.string(w, "name", "watchlist"),
                          id=c.string(w, "id", "watchlist", allow_none=True))

    n = c.section(data, "notifications")
    push = c.boolean(n, "push", "notifications")

    g = c.section(data, "go_live")
    go_live = GoLive(paper_days_required=c.integer(g, "paper_days_required", "go_live", minimum=0),
                     extensions=c.integer(g, "extensions", "go_live", minimum=0),
                     met=c.boolean(g, "met", "go_live"),
                     approved_at=c.string(g, "approved_at", "go_live", allow_none=True))
    if phase == "LIVE" and not go_live.met:
        c.errors.append("phase is LIVE but go_live.met is false; the go-live gate must be met first")

    sunday_review_local = DEFAULT_SUNDAY_REVIEW_LOCAL
    schedule_offset = 0
    if "schedule" in data:
        sch = c.section(data, "schedule")
        for key in sorted(set(sch) - {"sunday_review_local", "offset_minutes"}):
            c.warnings.append("unknown key schedule.%s is ignored" % key)
        if "sunday_review_local" in sch:
            value = sch.get("sunday_review_local")
            if not isinstance(value, str) or not HHMM.match(value):
                c.errors.append("schedule.sunday_review_local must be HH:MM (got %r)" % (value,))
            else:
                sunday_review_local = value
        if "offset_minutes" in sch:
            value = sch.get("offset_minutes")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SCHEDULE_OFFSET:
                c.errors.append("schedule.offset_minutes must be a whole number from 0 to %d (got %r)"
                                % (MAX_SCHEDULE_OFFSET, value))
            else:
                schedule_offset = value
    rules_set_on = data.get("rules_set_on")
    if rules_set_on is not None and (not isinstance(rules_set_on, str) or not DATE.match(rules_set_on)):
        c.errors.append("rules_set_on must be YYYY-MM-DD or null (got %r)" % (rules_set_on,))
        rules_set_on = None

    # Sanity rules: warnings, not errors.
    start = account.start_balance
    if start > 0:
        for key in ("option_cap_dollars", "share_cap_dollars"):
            cap = getattr(limits, key)
            if cap > start * MAX_CAP_SHARE_OF_START:
                c.warnings.append("limits.%s %s is more than 30%% of the start balance %s" % (key, cap, start))
        if limits.halt_account_value >= start:
            c.warnings.append("limits.halt_account_value %s is not below the start balance %s"
                              % (limits.halt_account_value, start))

    if c.errors:
        raise ConfigError(path, c.errors)
    return TraderConfig(path=path, schema=schema, owner_name=owner, phase=phase, account=account,
                        timezone=timezone, limits=limits, options=options, allowed=allowed,
                        watchlist=watchlist, push_notifications=push, go_live=go_live,
                        warnings=c.warnings, sunday_review_local=sunday_review_local,
                        rules_set_on=rules_set_on, schedule_offset_minutes=schedule_offset)


def load(path=None, environ=None, cwd=None):
    """Find (unless `path` is given), read and validate the config. Raises ConfigError."""
    path = path or find_config(environ=environ, cwd=cwd)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ConfigError(path, ["file not found"])
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(path, ["cannot read the file (%s)" % e])
    except ValueError as e:
        raise ConfigError(path, ["not valid JSON (%s)" % e])
    return validate(data, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "--check" or len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != "--path"):
        print("usage: trader_config.py --check [--path FILE]", file=sys.stderr)
        return 2
    try:
        cfg = load(path=argv[2] if len(argv) == 3 else None)
    except ConfigError as e:
        print("PROBLEMS in %s:" % e.path)
        for p in e.problems:
            print("  - " + p)
        return 1
    print("OK: %s (phase %s)" % (cfg.path, cfg.phase))
    for w in cfg.warnings:
        print("  warning: " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
