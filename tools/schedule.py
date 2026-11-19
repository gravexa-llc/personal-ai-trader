#!/usr/bin/env python3
"""Personal AI Trader schedule helper: prints the one-off scheduled runs for a day as JSON.

The routines (the daily wrap, the Sunday review, prep's check) and /trader-setup pass each entry to
CronCreate as a one-off task (recurring: false, day and month pinned). This tool only prints; it creates
nothing and writes nothing.

Usage:
  schedule.py --day YYYY-MM-DD|today                   that day's runs, or [] on a weekend or holiday
  schedule.py --next-trading-day [--after YYYY-MM-DD]  the runs of the first trading day after --after
                                                       (default: today, local); each run's market_date
                                                       is that day
  schedule.py --sunday-review [--after YYYY-MM-DD]     [the review run] for the first Sunday after --after
  schedule.py --session YYYY-MM-DD|today               {"date", "trading_day", "early_close", "open_local",
                                                       "close_local"} for that market date; the open and
                                                       close are HH:MM in trader.json's timezone, or null
                                                       when the market is shut (the watcher's --until)
  Common options: --workspace PATH (default: $CLAUDE_PROJECT_DIR, else walk up to trading/trader.json),
                  --timezone ZONE (override trader.json's timezone, e.g. for a quick look without a config).

Each run: {"run": "prep|first_check|hourly|preclose|closing_mark|wrap|sunday_review",
           "market_date": "YYYY-MM-DD", "local_date": "YYYY-MM-DD", "local_time": "HH:MM",
           "cron": "M H D Mo *", "prompt": "..."}
`market_date` is the trading day the run belongs to (the Sunday for the review); `local_date` is the date in
the configured timezone, which the cron's D and Mo fields pin. They differ only in timezones far from New York.

Times are defined in US Eastern market time (the regular session is 09:30-16:00 ET) and converted to
trader.json's `timezone` with zoneinfo, so daylight-saving changes on either side are handled:
  prep 09:05, first check 09:36, hourly 10:33 11:33 12:33 13:33 14:33, pre-close 15:12,
  closing mark 15:56, wrap 16:16.
  Early-close days (13:00 ET): prep 09:05, first check 09:36, hourly 10:33 11:33, pre-close 12:12,
  closing mark 12:56, wrap 13:16.
  Sunday review: 15:03 local, or trader.json schedule.sunday_review_local.
  trader.json schedule.offset_minutes (0 to 3) moves every trading-day run that many minutes later; it is
  for a second workspace on the same Robinhood login, so the two do not poll in the same minute.
Minutes stay off :00 and :30: one-off tasks at other minutes fire on the minute (Claude Code adds
jitter at :00 / :30). Holidays and early closes come from tools/market_calendar.json (NYSE); a date
outside its range prints a warning on stderr and is treated as an ordinary weekday.

Exit codes: 0 OK (warnings go to stderr), 2 usage or configuration problem.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    sys.exit("schedule: needs Python 3.9 or later (zoneinfo)")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader_config  # noqa: E402

CALENDAR_PATH = os.path.join(HERE, "market_calendar.json")
MARKET_TZ = ZoneInfo("America/New_York")

# (run, ET time) in firing order.
REGULAR_DAY = [
    ("prep", "09:05"), ("first_check", "09:36"),
    ("hourly", "10:33"), ("hourly", "11:33"), ("hourly", "12:33"), ("hourly", "13:33"), ("hourly", "14:33"),
    ("preclose", "15:12"), ("closing_mark", "15:56"), ("wrap", "16:16"),
]
EARLY_CLOSE_DAY = [
    ("prep", "09:05"), ("first_check", "09:36"),
    ("hourly", "10:33"), ("hourly", "11:33"),
    ("preclose", "12:12"), ("closing_mark", "12:56"), ("wrap", "13:16"),
]

# The exact prompt texts, in one place. The routines and setup use these verbatim.
PROMPTS = {
    "prep": (
        "Personal AI Trader scheduled run: PRE-MARKET PREP.\n"
        "Use the Skill tool to run personal-ai-trader:trader-check with argument prep."
    ),
    "first_check": (
        "Personal AI Trader scheduled run: FIRST CHECK AFTER THE OPEN.\n"
        "Use the Skill tool to run personal-ai-trader:trader-check with argument hourly."
    ),
    "hourly": (
        "Personal AI Trader scheduled run: HOURLY CHECK.\n"
        "Use the Skill tool to run personal-ai-trader:trader-check with argument hourly."
    ),
    "preclose": (
        "Personal AI Trader scheduled run: PRE-CLOSE CHECK.\n"
        "Use the Skill tool to run personal-ai-trader:trader-check with argument preclose."
    ),
    "closing_mark": (
        "Personal AI Trader scheduled run: CLOSING MARK.\n"
        "Read trading/STATE.md for the open option positions, call get_option_quotes for them, and append "
        "a 'Closing mark' entry with each position's bid, ask and mark to today's journal in "
        "trading/journal/. If no option position is open, append a one-line 'Closing mark: no open option "
        "positions' entry. Place no orders and take no other action; the daily wrap uses these bids for "
        "the closing value."
    ),
    "wrap": (
        "Personal AI Trader scheduled run: DAILY WRAP.\n"
        "Use the Skill tool to run personal-ai-trader:trader-wrap."
    ),
    "sunday_review": (
        "Personal AI Trader scheduled run: SUNDAY WEEKLY REVIEW.\n"
        "Use the Skill tool to run personal-ai-trader:trader-weekly-review."
    ),
}

_warned = set()


def warn(message):
    if message not in _warned:
        _warned.add(message)
        print("schedule: warning: " + message, file=sys.stderr)


# ---------------------------------------------------------------- calendar

def load_calendar(path=CALENDAR_PATH):
    with open(path, encoding="utf-8") as f:
        cal = json.load(f)
    cal["_from"] = date.fromisoformat(cal["covers_from"])
    cal["_to"] = date.fromisoformat(cal["covers_to"])
    return cal


def covered(day, cal):
    return cal["_from"] <= day <= cal["_to"]


def day_kind(day, cal):
    """'weekend', 'holiday', 'early_close' or 'regular' for a market date. Warns outside the calendar."""
    if day.weekday() >= 5:
        return "weekend"
    key = day.isoformat()
    if not covered(day, cal):
        warn("tools/market_calendar.json covers %s to %s; %s is outside it, so its holidays and early "
             "closes are unknown and it is treated as a regular trading day. Update the calendar from "
             "https://www.nyse.com/markets/hours-calendars." % (cal["covers_from"], cal["covers_to"], key))
        return "regular"
    if key in cal["holidays"]:
        return "holiday"
    if key in cal["early_closes"]:
        return "early_close"
    return "regular"


def is_trading_day(day, cal):
    return day_kind(day, cal) in ("regular", "early_close")


def market_close_et(day, cal):
    """The session's close as an aware ET datetime, or None if the market is closed that day."""
    kind = day_kind(day, cal)
    if kind not in ("regular", "early_close"):
        return None
    hhmm = cal["early_close"] if kind == "early_close" else cal["regular_close"]
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=MARKET_TZ)


def market_open_et(day, cal):
    if not is_trading_day(day, cal):
        return None
    return datetime.combine(day, parse_hhmm(cal["regular_open"]), tzinfo=MARKET_TZ)


# ---------------------------------------------------------------- runs

def parse_hhmm(text):
    h, m = text.split(":")
    return time(int(h), int(m))


def make_run(run, local_dt, market_date):
    if local_dt.minute in (0, 30):
        warn("%s at %s falls on :%02d; Claude Code may delay a task set on :00 or :30"
             % (run, local_dt.strftime("%H:%M"), local_dt.minute))
    return {
        "run": run,
        "market_date": market_date.isoformat(),
        "local_date": local_dt.strftime("%Y-%m-%d"),
        "local_time": local_dt.strftime("%H:%M"),
        "cron": "%d %d %d %d *" % (local_dt.minute, local_dt.hour, local_dt.day, local_dt.month),
        "prompt": PROMPTS[run],
    }


def runs_for_day(day, tz, cal, offset_minutes=0):
    """The runs for market date `day`, converted to timezone `tz` (a ZoneInfo). [] if the market is closed.
    `offset_minutes` (0 to 3) moves every run that many minutes later."""
    if not 0 <= offset_minutes <= trader_config.MAX_SCHEDULE_OFFSET:
        raise ValueError("offset_minutes must be 0 to %d" % trader_config.MAX_SCHEDULE_OFFSET)
    kind = day_kind(day, cal)
    if kind == "early_close":
        table = EARLY_CLOSE_DAY
    elif kind == "regular":
        table = REGULAR_DAY
    else:
        return []
    out = []
    for run, et in table:
        et_dt = datetime.combine(day, parse_hhmm(et), tzinfo=MARKET_TZ) + timedelta(minutes=offset_minutes)
        out.append(make_run(run, et_dt.astimezone(tz), day))
    return out


def next_trading_day(after, cal, limit=30):
    day = after
    for _ in range(limit):
        day += timedelta(days=1)
        if is_trading_day(day, cal):
            return day
    raise ValueError("no trading day within %d days after %s" % (limit, after))


def next_sunday(after):
    return after + timedelta(days=(6 - after.weekday()) or 7)


def sunday_review_run(sunday, tz, local_hhmm=trader_config.DEFAULT_SUNDAY_REVIEW_LOCAL):
    """The review run on `sunday` at `local_hhmm` in timezone `tz` (the time is local, not ET)."""
    return make_run("sunday_review", datetime.combine(sunday, parse_hhmm(local_hhmm), tzinfo=tz), sunday)


def session(day, tz, cal):
    """The market session on `day` in timezone `tz`."""
    open_et, close_et = market_open_et(day, cal), market_close_et(day, cal)
    return {
        "date": day.isoformat(),
        "trading_day": open_et is not None,
        "early_close": day_kind(day, cal) == "early_close",
        "open_local": open_et.astimezone(tz).strftime("%H:%M") if open_et else None,
        "close_local": close_et.astimezone(tz).strftime("%H:%M") if close_et else None,
    }


# ---------------------------------------------------------------- settings

def settings(workspace=None, timezone=None):
    """(ZoneInfo, sunday review HH:MM, offset minutes) from trader.json; --timezone takes precedence."""
    sunday = trader_config.DEFAULT_SUNDAY_REVIEW_LOCAL
    offset = 0
    tz_name = timezone
    cfg_error = None
    try:
        ws = trader_config.find_workspace(workspace)
        cfg = trader_config.load(trader_config.config_path(ws))
        tz_name = tz_name or cfg.timezone
        sunday = cfg.sunday_review_local
        offset = cfg.schedule_offset_minutes
    except trader_config.ConfigError as e:
        cfg_error = e
    if tz_name is None:
        raise SystemExit("schedule: cannot read the timezone: %s (pass --timezone ZONE or fix trader.json)"
                         % cfg_error)
    if cfg_error is not None:
        warn("trader.json not used (%s); Sunday review at the default %s" % (cfg_error, sunday))
    try:
        return ZoneInfo(tz_name), sunday, offset
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit("schedule: unknown timezone %r" % tz_name)


def parse_date(text, tz):
    if text == "today":
        return datetime.now(tz).date()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SystemExit("schedule: dates are YYYY-MM-DD (got %r)" % text)


def main(argv=None):
    p = argparse.ArgumentParser(description="Print Personal AI Trader's one-off scheduled runs as JSON.")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--day", metavar="YYYY-MM-DD")
    what.add_argument("--next-trading-day", action="store_true")
    what.add_argument("--sunday-review", action="store_true")
    what.add_argument("--session", metavar="YYYY-MM-DD")
    p.add_argument("--after", metavar="YYYY-MM-DD")
    p.add_argument("--workspace")
    p.add_argument("--timezone")
    a = p.parse_args(argv)
    if a.after and (a.day or a.session):
        p.error("--after goes with --next-trading-day or --sunday-review")

    tz, sunday_time, offset = settings(a.workspace, a.timezone)
    cal = load_calendar()
    if a.session:
        result = session(parse_date(a.session, tz), tz, cal)
    elif a.day:
        result = runs_for_day(parse_date(a.day, tz), tz, cal, offset)
    else:
        after = parse_date(a.after, tz) if a.after else datetime.now(tz).date()
        if a.next_trading_day:
            result = runs_for_day(next_trading_day(after, cal), tz, cal, offset)
        else:
            result = [sunday_review_run(next_sunday(after), tz, sunday_time)]
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
