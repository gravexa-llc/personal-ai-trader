#!/usr/bin/env python3
"""Personal AI Trader ledger. The CSVs are the source of truth; the workbook is rebuilt from them.

Files, all in the workspace (never in the plugin folder): <workspace>/trading/ledger/
  trades.csv  orders.csv  equity.csv  Trades.xlsx

Usage (`build` needs openpyxl: run it with trading/.venv/bin/python; everything else is standard library):
  ledger.py init                       # create the folder and the CSVs with headers if missing; never overwrites
  ledger.py open   '<json>'            # new row in trades.csv; prints the trade_id (P0001 paper, L0001 live)
  ledger.py close  <trade_id> '<json>' # fills exit fields, computes fees, P/L and days held
  ledger.py update <trade_id> '<json>' # change any fields on a trade (e.g. planned_stop)
  ledger.py order  '<json>'            # new row in orders.csv
  ledger.py equity '<json>'            # new row in equity.csv
  ledger.py show   [open|all]          # print trades as JSON lines
  ledger.py build                      # rebuild Trades.xlsx (start balance from trader.json account.start_balance)

  --workspace PATH may be given anywhere on the line; otherwise the workspace is $CLAUDE_PROJECT_DIR, else
  the first folder at or above the current directory holding trading/trader.json.

Timestamps are "YYYY-MM-DD HH:MM" in the timezone set in trader.json (`timezone`).
Unknown JSON keys are rejected so a typo cannot silently drop data.
"""
import csv
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader_config  # noqa: E402

LEDGER_RELATIVE = os.path.join("trading", "ledger")
WORKBOOK = "Trades.xlsx"

COLUMNS = {
    "trades.csv": [
        "trade_id", "mode", "status", "underlying", "instrument", "strike", "expiry", "quantity",
        "opened_at", "open_order_id", "entry_price", "entry_cost", "underlying_price_at_entry",
        "implied_vol_at_entry", "delta_at_entry", "days_to_expiry_at_entry", "thesis", "catalyst",
        "sources", "planned_stop", "planned_target", "planned_time_exit", "closed_at",
        "close_order_id", "exit_price", "proceeds", "fees", "pnl_dollars", "pnl_percent",
        "days_held", "exit_reason", "followed_plan", "lesson", "option_id",
    ],
    "orders.csv": [
        "timestamp", "mode", "trade_id", "order_id", "action", "symbol", "contract", "quantity",
        "order_type", "limit_price", "fill_price", "state", "note",
    ],
    "equity.csv": [
        "timestamp", "mode", "account_value", "cash", "options_value", "equity_value",
        "open_positions", "day_change_dollars", "drawdown_from_start", "note",
    ],
}
REQUIRED_OPEN = ["mode", "underlying", "instrument", "quantity", "opened_at", "entry_price",
                 "entry_cost", "thesis", "planned_stop", "planned_target", "planned_time_exit"]
REQUIRED_CLOSE = ["closed_at", "exit_price", "proceeds", "exit_reason", "followed_plan"]
REQUIRED_ORDER = ["timestamp", "mode", "action", "symbol", "state"]
REQUIRED_EQUITY = ["timestamp", "mode", "account_value", "cash"]
TS = "%Y-%m-%d %H:%M"  # local time in trader.json's timezone, e.g. 2026-01-05 09:45


def die(msg):
    sys.exit("ledger: " + msg)


class Ledger:
    def __init__(self, workspace):
        self.workspace = workspace
        self.dir = os.path.join(workspace, LEDGER_RELATIVE)

    def path(self, name):
        return os.path.join(self.dir, name)

    def read(self, name):
        # Rows written before a column was added read that column as empty.
        try:
            with open(self.path(name), newline="", encoding="utf-8") as f:
                return [{c: r.get(c) or "" for c in COLUMNS[name]} for r in csv.DictReader(f)]
        except FileNotFoundError:
            die("%s not found; run `ledger.py init` first" % self.path(name))

    def write(self, name, rows):
        tmp = self.path(name + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS[name])
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, self.path(name))

    def append(self, name, row):
        rows = self.read(name)
        rows.append({c: row.get(c, "") for c in COLUMNS[name]})
        self.write(name, rows)


def parse(name, raw, required=()):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        die("bad JSON: %s" % e)
    if not isinstance(data, dict):
        die("the JSON must be an object")
    unknown = [k for k in data if k not in COLUMNS[name]]
    if unknown:
        die("unknown fields for %s: %s" % (name, ", ".join(unknown)))
    missing = [k for k in required if str(data.get(k, "")).strip() == ""]
    if missing:
        die("missing required fields: %s" % ", ".join(missing))
    if "mode" in data and data["mode"] not in ("paper", "live"):
        die("mode must be 'paper' or 'live'")
    return {k: str(v) for k, v in data.items()}


def parse_ts(value, field):
    try:
        return datetime.strptime(value, TS)
    except ValueError:
        die("%s must be YYYY-MM-DD HH:MM (got %r)" % (field, value))


def find(rows, trade_id):
    for r in rows:
        if r["trade_id"] == trade_id:
            return r
    die("no trade with id %s" % trade_id)


def cmd_init(lg):
    os.makedirs(lg.dir, exist_ok=True)
    for name in COLUMNS:
        if not os.path.exists(lg.path(name)):
            lg.write(name, [])
            print("created", lg.path(name))
        else:
            print("exists ", lg.path(name))


def cmd_open(lg, raw):
    row = parse("trades.csv", raw, REQUIRED_OPEN)
    rows = lg.read("trades.csv")
    prefix = "P" if row["mode"] == "paper" else "L"
    n = sum(1 for r in rows if r["trade_id"].startswith(prefix)) + 1
    row["trade_id"] = "%s%04d" % (prefix, n)
    row["status"] = "open"
    parse_ts(row["opened_at"], "opened_at")
    lg.append("trades.csv", row)
    print(row["trade_id"])


def cmd_close(lg, trade_id, raw):
    upd = parse("trades.csv", raw, REQUIRED_CLOSE)
    rows = lg.read("trades.csv")
    r = find(rows, trade_id)
    if r["status"] != "open":
        die("%s is already %s" % (trade_id, r["status"]))
    r.update(upd)
    try:
        fees = float(r["fees"] or 0)
        cost = float(r["entry_cost"])
        proceeds = float(r["proceeds"])
    except ValueError:
        die("fees, entry_cost and proceeds must be numbers")
    pnl = proceeds - cost - fees
    r["fees"] = "%.2f" % fees
    r["pnl_dollars"] = "%.2f" % pnl
    r["pnl_percent"] = "%.1f" % (100 * pnl / cost) if cost else ""
    held = parse_ts(r["closed_at"], "closed_at") - parse_ts(r["opened_at"], "opened_at")
    r["days_held"] = "%.2f" % (held.total_seconds() / 86400)
    r["status"] = "closed"
    lg.write("trades.csv", rows)
    print("%s closed: P/L $%s (%s%%)" % (trade_id, r["pnl_dollars"], r["pnl_percent"]))


def cmd_update(lg, trade_id, raw):
    upd = parse("trades.csv", raw)
    rows = lg.read("trades.csv")
    find(rows, trade_id).update(upd)
    lg.write("trades.csv", rows)
    print("updated", trade_id)


def cmd_order(lg, raw):
    lg.append("orders.csv", parse("orders.csv", raw, REQUIRED_ORDER))


def cmd_equity(lg, raw):
    lg.append("equity.csv", parse("equity.csv", raw, REQUIRED_EQUITY))


def cmd_show(lg, which="open"):
    if which not in ("open", "all"):
        die("show takes 'open' or 'all'")
    for r in lg.read("trades.csv"):
        if which == "all" or r["status"] == "open":
            print(json.dumps({k: v for k, v in r.items() if v != ""}))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def workspace_settings(workspace):
    """(start balance as float or "", tz name or None) from trader.json; a bad config only warns."""
    try:
        cfg = trader_config.load(trader_config.config_path(workspace))
        return float(cfg.account.start_balance), cfg.timezone
    except trader_config.ConfigError as e:
        print("ledger: warning: trader.json not usable (%s); starting balance left blank"
              % "; ".join(e.problems), file=sys.stderr)
        return "", None


def now_local(tz_name):
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now()


def cmd_build(lg):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        die("openpyxl is not installed for %s; run build with trading/.venv/bin/python" % sys.executable)

    start_balance, tz_name = workspace_settings(lg.workspace)
    wb = Workbook()
    wb.remove(wb.active)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F3864")
    long_text = {"thesis", "catalyst", "sources", "lesson", "note", "followed_plan", "exit_reason"}

    trades = lg.read("trades.csv")
    summary = wb.create_sheet("Summary")
    summary.append(["", "paper", "live"])
    stats = {}
    for mode in ("paper", "live"):
        closed = [t for t in trades if t["mode"] == mode and t["status"] == "closed"]
        pnl = [float(t["pnl_dollars"]) for t in closed]
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p <= 0]
        stats[mode] = [
            ("Starting balance", start_balance),
            ("Open trades", sum(1 for t in trades if t["mode"] == mode and t["status"] == "open")),
            ("Closed trades", len(closed)),
            ("Realized P/L ($)", round(sum(pnl), 2)),
            ("Win rate (%)", round(100 * len(wins) / len(pnl), 1) if pnl else ""),
            ("Average win ($)", round(sum(wins) / len(wins), 2) if wins else ""),
            ("Average loss ($)", round(sum(losses) / len(losses), 2) if losses else ""),
            ("Largest win ($)", max(wins) if wins else ""),
            ("Largest loss ($)", min(losses) if losses else ""),
            ("Trades that broke the plan", sum(1 for t in closed if t["followed_plan"].lower().startswith("no"))),
        ]
    for (label, p), (_, l) in zip(stats["paper"], stats["live"]):
        summary.append([label, p, l])
    summary.append([])
    summary.append(["Rebuilt", now_local(tz_name).strftime(TS)])
    summary.column_dimensions["A"].width = 30
    for c in summary[1]:
        c.font = Font(bold=True)

    for title, name in (("Trades", "trades.csv"), ("Orders", "orders.csv"), ("Equity", "equity.csv")):
        ws = wb.create_sheet(title)
        cols = COLUMNS[name]
        ws.append([c.replace("_", " ") for c in cols])
        for r in lg.read(name):
            ws.append([num(r[c]) if c not in long_text and not c.endswith("_id") else r[c] for c in cols])
        for i, c in enumerate(cols, 1):
            cell = ws.cell(row=1, column=i)
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.column_dimensions[get_column_letter(i)].width = 48 if c in long_text else 16
            if c in long_text:
                for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    row[0].alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "E2" if title == "Trades" else "A2"
        ws.auto_filter.ref = ws.dimensions

    out = lg.path(WORKBOOK)
    tmp = lg.path(WORKBOOK + ".tmp")
    wb.save(tmp)
    os.replace(tmp, out)
    print("built", out)


TABLE = {
    "init": (cmd_init, 0), "open": (cmd_open, 1), "close": (cmd_close, 2), "update": (cmd_update, 2),
    "order": (cmd_order, 1), "equity": (cmd_equity, 1), "show": (cmd_show, None), "build": (cmd_build, 0),
}


def split_workspace(argv):
    """Remove `--workspace PATH` (or --workspace=PATH) from anywhere in argv. Returns (path or None, rest)."""
    rest, workspace, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--workspace":
            if i + 1 >= len(argv):
                die("--workspace needs a path")
            workspace, i = argv[i + 1], i + 2
            continue
        if a.startswith("--workspace="):
            workspace = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return workspace, rest


def main(argv=None):
    workspace, a = split_workspace(sys.argv[1:] if argv is None else list(argv))
    if not a:
        die(__doc__)
    cmd, rest = a[0], a[1:]
    if cmd not in TABLE:
        die("unknown command %s" % cmd)
    fn, n = TABLE[cmd]
    if n is not None and len(rest) != n:
        die("%s takes %d argument(s)" % (cmd, n))
    if n is None and len(rest) > 1:
        die("%s takes at most 1 argument" % cmd)
    try:
        root = trader_config.find_workspace(workspace)
    except trader_config.ConfigError as e:
        die("cannot find the workspace (%s); pass --workspace PATH" % "; ".join(e.problems))
    fn(Ledger(root), *rest)


if __name__ == "__main__":
    main()
