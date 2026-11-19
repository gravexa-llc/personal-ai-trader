"""Fixtures: fake workspaces in a tmp dir, and the guard run as a subprocess the way Claude Code runs it."""
import csv
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "tools", "order_guard.py")
TOOLS = os.path.join(ROOT, "tools")
EXAMPLE = os.path.join(ROOT, "templates", "trader.example.json")

ACCOUNT = "ACCT-TEST-0001"
OTHER_ACCOUNT = "ACCT-TEST-9999"
WATCHLIST_ID = "WL-TEST-0001"
OPTION_HELD = "opt-test-held-0001"       # open live trade, entry 5.5, 1 contract (the PLAN.md proof case)
OPTION_AT_4 = "opt-test-held-0002"       # open live trade, entry 4.00, 1 contract
OPTION_PAPER = "opt-test-paper-0003"     # open, but a paper trade
OPTION_CLOSED = "opt-test-closed-0004"   # live, but closed
OPTION_DUP = "opt-test-dup-0005"         # two open live rows: ambiguous

PLUGIN_PREFIX = "mcp__plugin_personal-ai-trader_robinhood-trading__"
PLAIN_PREFIX = "mcp__robinhood-trading__"
PREFIXES = [PLUGIN_PREFIX, PLAIN_PREFIX]

LEDGER_FIELDS = ["trade_id", "mode", "status", "underlying", "instrument", "quantity", "entry_price", "option_id"]
LEDGER_ROWS = [
    {"trade_id": "L0001", "mode": "live", "status": "open", "underlying": "XYZ", "instrument": "put",
     "quantity": "1", "entry_price": "5.5", "option_id": OPTION_HELD},
    {"trade_id": "L0002", "mode": "live", "status": "open", "underlying": "ABC", "instrument": "call",
     "quantity": "1", "entry_price": "4.00", "option_id": OPTION_AT_4},
    {"trade_id": "P0003", "mode": "paper", "status": "open", "underlying": "ABC", "instrument": "call",
     "quantity": "1", "entry_price": "2.00", "option_id": OPTION_PAPER},
    {"trade_id": "L0004", "mode": "live", "status": "closed", "underlying": "ABC", "instrument": "call",
     "quantity": "1", "entry_price": "2.00", "option_id": OPTION_CLOSED},
    {"trade_id": "L0005", "mode": "live", "status": "open", "underlying": "DEF", "instrument": "call",
     "quantity": "1", "entry_price": "2.00", "option_id": OPTION_DUP},
    {"trade_id": "L0006", "mode": "live", "status": "open", "underlying": "DEF", "instrument": "call",
     "quantity": "1", "entry_price": "2.00", "option_id": OPTION_DUP},
    {"trade_id": "L0007", "mode": "live", "status": "open", "underlying": "GHI", "instrument": "shares",
     "quantity": "20", "entry_price": "50.00", "option_id": ""},
]


def base_config(phase="LIVE"):
    with open(EXAMPLE) as f:
        cfg = json.load(f)
    cfg["account"]["number"] = ACCOUNT
    cfg["watchlist"]["id"] = WATCHLIST_ID
    cfg["phase"] = phase
    if phase == "LIVE":
        cfg["go_live"]["met"] = True
        cfg["go_live"]["approved_at"] = "2026-01-05"
    return cfg


def make_workspace(root, config=None, raw_config=None, ledger_rows=LEDGER_ROWS, write_config=True):
    """Create <root>/trading/trader.json and <root>/trading/ledger/trades.csv. Returns root."""
    os.makedirs(os.path.join(root, "trading", "ledger"), exist_ok=True)
    if write_config:
        with open(os.path.join(root, "trading", "trader.json"), "w") as f:
            if raw_config is not None:
                f.write(raw_config)
            else:
                json.dump(config if config is not None else base_config(), f, indent=2)
    if ledger_rows is not None:
        with open(os.path.join(root, "trading", "ledger", "trades.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
            w.writeheader()
            w.writerows(ledger_rows)
    return str(root)


def run_guard(tool_name, tool_input=None, workspace=None, stdin=None, env_extra=None, cwd=None):
    """Run the guard as Claude Code does. Returns (exit code, stderr)."""
    if stdin is None:
        payload = {
            "session_id": "test", "hook_event_name": "PreToolUse", "cwd": workspace or ROOT,
            "tool_name": tool_name, "tool_input": tool_input if tool_input is not None else {},
            "tool_use_id": "toolu_test",
        }
        stdin = json.dumps(payload)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if workspace is not None:
        env["CLAUDE_PROJECT_DIR"] = workspace
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, "-I", GUARD], input=stdin, capture_output=True, text=True,
                       env=env, cwd=cwd or workspace or ROOT, timeout=30)
    return p.returncode, p.stderr


@pytest.fixture
def workspace(tmp_path):
    """A LIVE workspace with the fixture ledger."""
    return make_workspace(tmp_path)


@pytest.fixture
def ws_factory(tmp_path):
    counter = {"n": 0}

    def factory(phase="LIVE", mutate=None, **kw):
        counter["n"] += 1
        root = tmp_path / ("ws%d" % counter["n"])
        cfg = base_config(phase)
        if mutate:
            mutate(cfg)
        return make_workspace(root, config=cfg, **kw)
    return factory

