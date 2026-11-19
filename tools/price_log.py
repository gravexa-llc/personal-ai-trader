#!/usr/bin/env python3
"""Personal AI Trader option price logger (OPTIONAL; no AI, no Robinhood login). Standard library only.

For every open option trade in <workspace>/trading/ledger/trades.csv (read-only) it fetches the contract's
bid / ask from CBOE's free public delayed-quote feed and appends one row to
<workspace>/trading/logs/price_log.csv (errors go to price_log.err beside it). It does nothing outside the
regular US session (09:30 to the close ET, plus 20 minutes for the feed delay, on trading days from
tools/market_calendar.json) unless --force is given.

The feed is delayed (about 15 minutes during the session) and unofficial, so the log is a record for shadow
tests and reviews -- NEVER a price to trade on or to move a stop with; live decisions use Robinhood quotes.
Each row carries the time the data represents (`data_as_of_local`, from the underlying's last trade time in
the feed) as well as when it was fetched, both in trader.json's timezone, so rows can be lined up with
Robinhood quotes later. Rows can repeat when the feed has not refreshed: de-duplicate on data_as_of_local.

Scheduling: the plugin installs NO scheduler for this script (no launchd job, no cron) and nothing runs it
by default. To use it, run it every 5 minutes with a scheduler of your own choosing on an always-on machine
(see the keep-running notes); it exits at once outside market hours, so it is safe to run all day.

Usage: price_log.py [--workspace PATH]          normal run (honours market hours)
       price_log.py [--workspace PATH] --force  run now regardless of the clock (testing)
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schedule  # noqa: E402  (calendar helpers)
import trader_config  # noqa: E402

URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/%s.json"
ET = schedule.MARKET_TZ
FEED_DELAY_MARGIN = timedelta(minutes=20)  # data for the close arrives ~15 min later

FIELDS = ["fetched_at_local", "data_as_of_local", "feed_timestamp_utc", "trade_id", "contract",
          "occ_symbol", "underlying_price", "bid", "ask", "mid", "last", "last_trade_local",
          "volume", "iv", "delta"]


def in_session(now, cal):
    """True from the open to the close (+ the feed-delay margin) on a trading day."""
    now_et = now.astimezone(ET)
    day = now_et.date()
    open_et, close_et = schedule.market_open_et(day, cal), schedule.market_close_et(day, cal)
    if open_et is None:
        return False
    return open_et <= now_et <= close_et + FEED_DELAY_MARGIN


def occ(underlying, expiry, kind, strike):
    d = datetime.strptime(expiry, "%Y-%m-%d")
    return "%s%s%s%08d" % (underlying.upper(), d.strftime("%y%m%d"),
                           "P" if kind.lower().startswith("p") else "C",
                           round(float(strike) * 1000))


def et_to_local(s, tz):
    """The feed's naive ET timestamps -> local 'YYYY-MM-DD HH:MM:SS'."""
    if not s:
        return ""
    return datetime.fromisoformat(s).replace(tzinfo=ET).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def open_options(trades_path):
    with open(trades_path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)
                if r.get("status") == "open" and r.get("instrument", "").lower() in ("put", "call")]


def fetch(symbol):
    req = urllib.request.Request(URL % symbol.upper(), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    force = "--force" in argv
    workspace = None
    if "--workspace" in argv:
        i = argv.index("--workspace")
        if i + 1 >= len(argv):
            sys.exit("price_log: --workspace needs a path")
        workspace = argv[i + 1]
    try:
        ws = trader_config.find_workspace(workspace)
        tz = ZoneInfo(trader_config.load(trader_config.config_path(ws)).timezone)
    except trader_config.ConfigError as e:
        sys.exit("price_log: trader.json is missing or invalid (%s)" % "; ".join(e.problems))

    now = datetime.now(tz)
    if not force and not in_session(now, schedule.load_calendar()):
        return 0
    trades_path = os.path.join(ws, "trading", "ledger", "trades.csv")
    if not os.path.exists(trades_path):
        return 0
    trades = open_options(trades_path)
    if not trades:
        return 0
    log_dir = os.path.join(ws, "trading", "logs")
    out, err = os.path.join(log_dir, "price_log.csv"), os.path.join(log_dir, "price_log.err")
    os.makedirs(log_dir, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    feeds, rows = {}, []
    for t in trades:
        sym = t["underlying"].upper()
        try:
            code = occ(sym, t["expiry"], t["instrument"], t["strike"])
            if sym not in feeds:
                feeds[sym] = fetch(sym)
            d = feeds[sym]
            q = next((o for o in d["data"]["options"] if o["option"] == code), None)
            if q is None:
                raise ValueError("contract %s not in feed" % code)
        except Exception as e:  # log and carry on; a missing row is visible in the CSV
            with open(err, "a", encoding="utf-8") as f:
                f.write("%s %s: %s\n" % (stamp, t.get("trade_id"), e))
            continue
        bid, ask = q.get("bid"), q.get("ask")
        rows.append({
            "fetched_at_local": stamp,
            "data_as_of_local": et_to_local(d["data"].get("last_trade_time"), tz),
            "feed_timestamp_utc": d.get("timestamp", ""),
            "trade_id": t["trade_id"],
            "contract": "%s %s %s %s" % (sym, t["expiry"], t["strike"], t["instrument"]),
            "occ_symbol": code,
            "underlying_price": d["data"].get("current_price"),
            "bid": bid, "ask": ask,
            "mid": round((bid + ask) / 2, 3) if bid is not None and ask is not None else "",
            "last": q.get("last_trade_price"),
            "last_trade_local": et_to_local(q.get("last_trade_time"), tz),
            "volume": q.get("volume"), "iv": q.get("iv"), "delta": q.get("delta"),
        })
    if rows:
        new = not os.path.exists(out)
        with open(out, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
