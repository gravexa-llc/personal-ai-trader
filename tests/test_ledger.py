"""tools/ledger.py in a tmp workspace, run as a subprocess the way the routines run it."""
import csv
import json
import os
import subprocess
import sys

import pytest

from conftest import TOOLS, base_config

LEDGER = os.path.join(TOOLS, "ledger.py")


def make_ws(tmp_path, start_balance=5000):
    os.makedirs(tmp_path / "trading")
    cfg = base_config("DRY_RUN")
    cfg["account"]["start_balance"] = start_balance
    (tmp_path / "trading" / "trader.json").write_text(json.dumps(cfg))
    return str(tmp_path)


def run(ws, *args, use_flag=True, env_ws=False):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if env_ws:
        env["CLAUDE_PROJECT_DIR"] = ws
    cmd = [sys.executable, LEDGER] + list(args) + (["--workspace", ws] if use_flag else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=TOOLS, timeout=60)


def rows(ws, name):
    with open(os.path.join(ws, "trading", "ledger", name), newline="") as f:
        return list(csv.DictReader(f))


OPEN = {"mode": "paper", "underlying": "XYZ", "instrument": "call", "strike": "50", "expiry": "2026-12-18",
        "quantity": "2", "opened_at": "2026-11-02 06:36", "entry_price": "1.50", "entry_cost": "300",
        "thesis": "trend continuation", "planned_stop": "0.75", "planned_target": "ratchet",
        "planned_time_exit": "2026-12-08", "option_id": "opt-test-1"}


def test_init_creates_headers_and_never_overwrites(tmp_path):
    ws = make_ws(tmp_path)
    p = run(ws, "init")
    assert p.returncode == 0, p.stderr
    d = os.path.join(ws, "trading", "ledger")
    assert sorted(os.listdir(d)) == ["equity.csv", "orders.csv", "trades.csv"]
    with open(os.path.join(d, "trades.csv")) as f:
        assert f.readline().startswith("trade_id,mode,status,underlying")
    assert run(ws, "open", json.dumps(OPEN)).returncode == 0
    assert run(ws, "init").returncode == 0
    assert len(rows(ws, "trades.csv")) == 1


def test_files_go_to_the_workspace_not_the_plugin(tmp_path):
    ws = make_ws(tmp_path)
    run(ws, "init")
    assert not os.path.exists(os.path.join(os.path.dirname(TOOLS), "ledger"))
    assert not os.path.exists(os.path.join(os.path.dirname(TOOLS), "trading"))


def test_open_close_and_pnl(tmp_path):
    ws = make_ws(tmp_path)
    run(ws, "init")
    p = run(ws, "open", json.dumps(OPEN))
    assert p.stdout.strip() == "P0001", p.stderr
    live = dict(OPEN, mode="live")
    assert run(ws, "open", json.dumps(live)).stdout.strip() == "L0001"
    assert run(ws, "open", json.dumps(OPEN)).stdout.strip() == "P0002"
    p = run(ws, "close", "P0001", json.dumps({
        "closed_at": "2026-11-04 12:36", "exit_price": "2.25", "proceeds": "450", "fees": "0.08",
        "exit_reason": "ratchet stop", "followed_plan": "yes"}))
    assert p.returncode == 0, p.stderr
    assert "P/L $149.92 (50.0%)" in p.stdout
    t = {r["trade_id"]: r for r in rows(ws, "trades.csv")}["P0001"]
    assert t["status"] == "closed"
    assert t["pnl_dollars"] == "149.92" and t["pnl_percent"] == "50.0" and t["fees"] == "0.08"
    assert t["days_held"] == "2.25"
    again = run(ws, "close", "P0001", json.dumps({
        "closed_at": "2026-11-04 12:36", "exit_price": "2.25", "proceeds": "450",
        "exit_reason": "x", "followed_plan": "yes"}))
    assert again.returncode != 0 and "already closed" in again.stderr


def test_loss_without_fees(tmp_path):
    ws = make_ws(tmp_path)
    run(ws, "init")
    run(ws, "open", json.dumps(OPEN))
    run(ws, "close", "P0001", json.dumps({"closed_at": "2026-11-02 07:36", "exit_price": "0.75", "proceeds": "150",
                                          "exit_reason": "stop", "followed_plan": "yes"}))
    t = rows(ws, "trades.csv")[0]
    assert t["pnl_dollars"] == "-150.00" and t["pnl_percent"] == "-50.0" and t["fees"] == "0.00"


@pytest.mark.parametrize("data,needle", [
    (dict(OPEN, colour="red"), "unknown fields for trades.csv: colour"),
    ({k: v for k, v in OPEN.items() if k != "thesis"}, "missing required fields: thesis"),
    (dict(OPEN, mode="real"), "mode must be 'paper' or 'live'"),
    (dict(OPEN, opened_at="2026-11-02T06:36"), "opened_at must be YYYY-MM-DD HH:MM"),
])
def test_open_validation(tmp_path, data, needle):
    ws = make_ws(tmp_path)
    run(ws, "init")
    p = run(ws, "open", json.dumps(data))
    assert p.returncode != 0 and needle in p.stderr
    assert rows(ws, "trades.csv") == []


def test_update_order_equity_show(tmp_path):
    ws = make_ws(tmp_path)
    run(ws, "init")
    run(ws, "open", json.dumps(OPEN))
    assert run(ws, "update", "P0001", json.dumps({"planned_stop": "1.13"})).returncode == 0
    assert rows(ws, "trades.csv")[0]["planned_stop"] == "1.13"
    assert run(ws, "order", json.dumps({"timestamp": "2026-11-02 06:36", "mode": "paper", "action": "buy",
                                         "symbol": "XYZ", "state": "filled"})).returncode == 0
    bad = run(ws, "order", json.dumps({"timestamp": "2026-11-02 06:36", "mode": "paper"}))
    assert bad.returncode != 0 and "missing required fields" in bad.stderr
    assert run(ws, "equity", json.dumps({"timestamp": "2026-11-02 13:16", "mode": "paper",
                                          "account_value": "5000", "cash": "4700"})).returncode == 0
    assert len(rows(ws, "orders.csv")) == 1 and len(rows(ws, "equity.csv")) == 1
    shown = run(ws, "show").stdout.strip().splitlines()
    assert json.loads(shown[0])["trade_id"] == "P0001"
    assert run(ws, "show", "all").returncode == 0
    assert run(ws, "show", "bogus").returncode != 0


def test_workspace_from_project_dir(tmp_path):
    ws = make_ws(tmp_path)
    p = run(ws, "init", use_flag=False, env_ws=True)
    assert p.returncode == 0, p.stderr
    assert os.path.exists(os.path.join(ws, "trading", "ledger", "trades.csv"))


def test_workspace_flag_before_command(tmp_path):
    ws = make_ws(tmp_path)
    p = subprocess.run([sys.executable, LEDGER, "--workspace", ws, "init"], capture_output=True, text=True,
                       cwd=TOOLS, env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    assert p.returncode == 0, p.stderr


def test_missing_ledger_says_run_init(tmp_path):
    ws = make_ws(tmp_path)
    p = run(ws, "show")
    assert p.returncode != 0 and "run `ledger.py init` first" in p.stderr


def test_no_workspace_found(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    p = subprocess.run([sys.executable, LEDGER, "init"], capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert p.returncode != 0 and "cannot find the workspace" in p.stderr


def test_build_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    ws = make_ws(tmp_path, start_balance=7500)
    run(ws, "init")
    run(ws, "open", json.dumps(OPEN))
    run(ws, "close", "P0001", json.dumps({"closed_at": "2026-11-04 12:36", "exit_price": "2.25", "proceeds": "450",
                                          "exit_reason": "ratchet", "followed_plan": "yes"}))
    p = run(ws, "build")
    assert p.returncode == 0, p.stderr
    path = os.path.join(ws, "trading", "ledger", "Trades.xlsx")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Trades", "Orders", "Equity"]
    summary = {r[0]: r[1:] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
    assert summary["Starting balance"] == (7500, 7500)
    assert summary["Realized P/L ($)"][0] == 150
    assert wb["Trades"].cell(row=2, column=1).value == "P0001"


def test_update_can_cancel_a_never_filled_entry(tmp_path):
    # trader-check marks an entry that was never approved / filled this way.
    ws = make_ws(tmp_path)
    run(ws, "init")
    run(ws, "open", json.dumps(dict(OPEN, mode="live")))
    p = run(ws, "update", "L0001", json.dumps({"status": "cancelled", "exit_reason": "entry never approved"}))
    assert p.returncode == 0, p.stderr
    t = rows(ws, "trades.csv")[0]
    assert t["status"] == "cancelled" and t["exit_reason"] == "entry never approved"
    assert run(ws, "show").stdout.strip() == ""
    p = run(ws, "close", "L0001", json.dumps({"closed_at": "2026-11-04 12:36", "exit_price": "1", "proceeds": "1",
                                              "exit_reason": "x", "followed_plan": "yes"}))
    assert p.returncode != 0 and "already cancelled" in p.stderr
