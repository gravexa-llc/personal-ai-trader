#!/usr/bin/env python3
"""Personal AI Trader price watcher (optional): polls stock prices and EXITS when a level is crossed, which
wakes the Claude Code session that started it.

Run it as a background command from the session (Bash with run_in_background). The session is re-invoked
when this process exits, so every exit path prints one line saying why.

  watch.py [--workspace PATH] [--interval 300] [--from HH:MM] [--until HH:MM] "XYZ above 101.25" "XYZ below 95.5" ...
  watch.py --status [--workspace PATH]     is this workspace's watcher running?
  watch.py --stop   [--workspace PATH]     stop this workspace's watcher (and only that one)

One watcher per workspace. A watcher records its process in <workspace>/trading/logs/watch.pid; --stop and
--status read that file, and starting a watcher first stops the one this workspace already has. Nothing here
searches for watcher processes by name, so a watcher that belongs to another workspace, another plugin
folder or another program on the same machine is never touched.

Rules are "SYMBOL above LEVEL" or "SYMBOL below LEVEL" (one per stop or target level on an underlying).
--from and --until are local times in trader.json's `timezone`. Defaults: --from one minute after today's
open (09:31 ET) and --until today's close (16:00 ET, or 13:00 ET on an early-close day), both converted to
the configured timezone using tools/market_calendar.json. Before --from it only waits: pre-market quotes on
this feed carry the previous close's timestamp and would otherwise count as stale failures.

Exit lines (stdout):
  TRIGGER <symbol> <above|below> <level> price=<p> at <HH:MM:SS>   a level was crossed
  CLOSED no trigger ...                                              reached --until (or the market is shut today)
  DATA FAILURE ...                                                   price feed failed 3 polls running
  STOPPED ...                                                        stopped by --stop or replaced by a new start

Price feed: Yahoo Finance's free public chart endpoint (no account, unofficial, may change or block without
notice). It is a tripwire only: the session re-checks every price on Robinhood before acting, and a quote
older than 3 minutes counts as a failed poll. A heartbeat line is appended to
<workspace>/trading/logs/watch.log on every poll so a check can confirm the watcher is alive.
Standard library only.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from datetime import time as dtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader_config  # noqa: E402

URL = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1m&range=1d"
MAX_AGE = 180        # seconds; an older quote counts as a failed poll
MAX_FAILURES = 3
LOG_RELATIVE = os.path.join("trading", "logs", "watch.log")
PID_RELATIVE = os.path.join("trading", "logs", "watch.pid")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
USAGE = ('usage: watch.py [--workspace PATH] [--interval SECONDS] [--from HH:MM] [--until HH:MM] '
         '"SYMBOL above|below LEVEL" ...\n       watch.py --status|--stop [--workspace PATH]')


class UsageError(ValueError):
    pass


def parse_rule(text):
    """'XYZ above 101.25' -> ('XYZ', 'above', 101.25). Raises UsageError."""
    parts = text.split()
    if len(parts) != 3:
        raise UsageError("rule must be 'SYMBOL above|below LEVEL', got %r" % text)
    sym, side, level = parts[0].upper(), parts[1].lower(), parts[2]
    if not SYMBOL.match(sym):
        raise UsageError("bad symbol in rule %r" % text)
    if side not in ("above", "below"):
        raise UsageError("rule must be 'SYMBOL above|below LEVEL', got %r" % text)
    try:
        value = float(level)
    except ValueError:
        raise UsageError("level must be a number in rule %r" % text)
    if not value > 0 or value == float("inf"):
        raise UsageError("level must be a positive number in rule %r" % text)
    return sym, side, value


def parse_args(argv):
    """Returns a dict: workspace, interval, start, until (HH:MM or None), rules [(sym, side, level)]."""
    args = list(argv)
    opts = {"workspace": None, "interval": 300, "start": None, "until": None, "rules": [], "action": "run"}
    while args:
        a = args.pop(0)
        if a in ("--stop", "--status"):
            if opts["action"] != "run":
                raise UsageError("give --stop or --status, not both")
            opts["action"] = a[2:]
        elif a in ("--interval", "--from", "--until", "--workspace"):
            if not args:
                raise UsageError("%s needs a value" % a)
            value = args.pop(0)
            if a == "--interval":
                try:
                    opts["interval"] = int(value)
                except ValueError:
                    raise UsageError("--interval takes whole seconds, got %r" % value)
                if opts["interval"] < 10:
                    raise UsageError("--interval must be at least 10 seconds")
            elif a == "--workspace":
                opts["workspace"] = value
            else:
                if not HHMM.match(value):
                    raise UsageError("%s takes HH:MM, got %r" % (a, value))
                opts["start" if a == "--from" else "until"] = value
        elif a.startswith("--"):
            raise UsageError("unknown option %s" % a)
        else:
            opts["rules"].append(parse_rule(a))
    if opts["action"] != "run":
        if opts["rules"] or opts["start"] or opts["until"]:
            raise UsageError("--%s takes no rules or times" % opts["action"])
    elif not opts["rules"]:
        raise UsageError("give at least one rule")
    return opts


# ---------------------------------------------------------------- one watcher per workspace

def process_start(pid):
    """A stable marker of when pid started, if that process is a watch.py, else None. A recorded pid is
    trusted only while this still matches, so a number the system has reused is never signalled.

    On Linux the marker is the start tick from /proc: the start time `ps` prints there is recomputed on
    every call and can differ by a second for the same process. Elsewhere it is the `ps` start time."""
    proc = "/proc/%d" % pid
    if os.path.isdir("/proc/self"):
        try:
            with open(proc + "/cmdline", "rb") as f:
                command = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            with open(proc + "/stat", encoding="utf-8", errors="replace") as f:
                fields = f.read().rpartition(")")[2].split()   # after "pid (name)": state is field 3
        except OSError:
            return None
        if "watch.py" not in command or len(fields) < 20:
            return None
        return "tick %s" % fields[19]                           # field 22, starttime
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out or "watch.py" not in out:
        return None
    return " ".join(out.split()[:5])


def read_record(ws):
    """(pid, start) of the watcher this workspace recorded and that is still that process, else None."""
    try:
        with open(os.path.join(ws, PID_RELATIVE), encoding="utf-8") as f:
            first, _, rest = f.read().strip().partition(" ")
        pid = int(first)
    except (OSError, ValueError):
        return None
    if pid <= 1 or pid == os.getpid():
        return None
    start = process_start(pid)
    if start is None or start != rest.strip():
        return None
    return pid, start


def write_record(ws):
    path = os.path.join(ws, PID_RELATIVE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("%d %s\n" % (os.getpid(), process_start(os.getpid()) or ""))


def clear_record(ws, only_pid=None):
    """Remove the record; with only_pid, only if it still names that process."""
    path = os.path.join(ws, PID_RELATIVE)
    try:
        if only_pid is not None:
            with open(path, encoding="utf-8") as f:
                if f.read().split(" ")[0].strip() != str(only_pid):
                    return
        os.remove(path)
    except OSError:
        pass


def stop_recorded(ws, wait=5.0):
    """Stop the watcher this workspace recorded. Returns its pid, or None if there was none."""
    rec = read_record(ws)
    if rec is None:
        clear_record(ws)
        return None
    pid = rec[0]
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        clear_record(ws)
        return None
    deadline = time.time() + wait
    while time.time() < deadline and process_start(pid) == rec[1]:
        time.sleep(0.1)
    if process_start(pid) == rec[1]:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    clear_record(ws, only_pid=pid)
    return pid


def last_log_line(ws):
    try:
        with open(os.path.join(ws, LOG_RELATIVE), encoding="utf-8") as f:
            lines = f.read().splitlines()
        return lines[-1] if lines else ""
    except OSError:
        return ""


def crossed(rule, price):
    _, side, level = rule
    return (side == "above" and price >= level) or (side == "below" and price <= level)


def describe(rules):
    return "; ".join("%s %s %s" % r for r in rules)


def price(symbol):
    req = urllib.request.Request(URL % symbol, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        meta = json.load(r)["chart"]["result"][0]["meta"]
    age = time.time() - meta["regularMarketTime"]
    if age > MAX_AGE:
        raise ValueError("%s quote is %d seconds old" % (symbol, age))
    return float(meta["regularMarketPrice"])


def session_window(tz, now_local):
    """Today's default (from, until) as aware datetimes (one minute after the open, the close), or
    (None, None) if the market is shut today."""
    import schedule
    cal = schedule.load_calendar()
    today_et = now_local.astimezone(schedule.MARKET_TZ).date()
    open_et, close_et = schedule.market_open_et(today_et, cal), schedule.market_close_et(today_et, cal)
    if open_et is None:
        return None, None
    return (open_et + timedelta(minutes=1)).astimezone(tz), close_et.astimezone(tz)


def local_at(hhmm, tz, now_local, after=None):
    """Today's HH:MM in tz; if `after` is given and that is not later, the next day's (window over midnight)."""
    h, m = map(int, hhmm.split(":"))
    dt = datetime.combine(now_local.date(), dtime(h, m), tzinfo=tz)
    if after is not None and dt <= after:
        dt = datetime.combine(now_local.date() + timedelta(days=1), dtime(h, m), tzinfo=tz)
    return dt


def main(argv=None):
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        sys.exit("watch: %s\n%s" % (e, USAGE))
    try:
        from zoneinfo import ZoneInfo
        ws = trader_config.find_workspace(opts["workspace"])
        tz = ZoneInfo(trader_config.load(trader_config.config_path(ws)).timezone)
    except trader_config.ConfigError as e:
        sys.exit("watch: trader.json is missing or invalid (%s); pass --workspace PATH" % "; ".join(e.problems))
    except Exception as e:
        sys.exit("watch: cannot use the configured timezone (%s)" % e)

    if opts["action"] == "status":
        rec = read_record(ws)
        print(("RUNNING pid %d (this workspace). Last log line: %s" % (rec[0], last_log_line(ws) or "none"))
              if rec else "NOT RUNNING (this workspace has no watcher)")
        return
    if opts["action"] == "stop":
        pid = stop_recorded(ws)
        print(("STOPPED pid %d (this workspace's watcher)" % pid) if pid
              else "NOT RUNNING (this workspace has no watcher; nothing stopped)")
        return

    log_path = os.path.join(ws, LOG_RELATIVE)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def now():
        return datetime.now(tz)

    def log(line):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(now().strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")

    def finish(line, code=0):
        log("EXIT " + line)
        clear_record(ws, only_pid=os.getpid())
        print(line, flush=True)
        sys.exit(code)

    rules = opts["rules"]
    replaced = stop_recorded(ws)
    if replaced:
        log("REPLACED this workspace's earlier watcher (pid %d)" % replaced)
    write_record(ws)
    signal.signal(signal.SIGTERM, lambda *_: finish(
        "STOPPED by this workspace (a --stop, or a new watcher replaced it): %s" % describe(rules), 143))
    t0 = now()
    default_start, default_until = (None, None)
    if opts["start"] is None or opts["until"] is None:
        default_start, default_until = session_window(tz, t0)
        if default_start is None:
            log("START " + describe(rules))
            finish("CLOSED no trigger: the market is closed today (%s)" % describe(rules))
    start = local_at(opts["start"], tz, t0) if opts["start"] else default_start
    until = local_at(opts["until"], tz, t0, after=start) if opts["until"] else default_until
    until_text = until.strftime("%H:%M")

    log("START %s every %ds from %s until %s (%s)" % (describe(rules), opts["interval"], start.strftime("%H:%M"),
                                                      until_text, tz.key))
    failures = 0
    if now() < start:
        log("WAIT until %s" % start.strftime("%H:%M"))
        while now() < start:
            time.sleep(20)
    while True:
        if now() >= until:
            finish("CLOSED no trigger by %s (%s)" % (until_text, describe(rules)))
        try:
            prices = {s: price(s) for s in {r[0] for r in rules}}
            failures = 0
        except Exception as e:  # network, format or stale quote: all count the same
            failures += 1
            log("FAIL %d/%d %s" % (failures, MAX_FAILURES, e))
            if failures >= MAX_FAILURES:
                finish("DATA FAILURE price feed failed %d polls running: %s" % (failures, e))
            time.sleep(opts["interval"])
            continue
        log("OK " + " ".join("%s=%.2f" % kv for kv in sorted(prices.items())))
        for rule in rules:
            p = prices[rule[0]]
            if crossed(rule, p):
                finish("TRIGGER %s %s %s price=%.2f at %s" % (rule[0], rule[1], rule[2], p, now().strftime("%H:%M:%S")))
        time.sleep(opts["interval"])


if __name__ == "__main__":
    main()
