"""Unit tests for tools/trader_config.py."""
import json
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from conftest import EXAMPLE, ROOT, TOOLS, base_config, make_workspace

sys.path.insert(0, TOOLS)
import trader_config as tc  # noqa: E402


def example():
    with open(EXAMPLE) as f:
        return json.load(f)


def problems(data):
    with pytest.raises(tc.ConfigError) as e:
        tc.validate(data, "trader.json")
    return e.value.problems


def test_example_template_is_valid_and_typed():
    cfg = tc.validate(example(), "trader.json")
    assert cfg.phase == "DRY_RUN"
    assert cfg.owner_name == "Sam"
    assert cfg.account.number == "ACCT-TEST-0001"
    assert cfg.account.start_balance == Decimal("5000")
    assert cfg.limits.option_cap_dollars == Decimal("1000")
    assert cfg.limits.share_cap_dollars == Decimal("1500")
    assert cfg.limits.max_open_positions == 5
    assert cfg.options.overnight_cushion == Decimal("0.9")
    assert cfg.options.stop_market_after_gain.enabled is True
    assert cfg.options.stop_market_after_gain.not_before == "06:45"
    assert cfg.options.stop_market_after_gain.max_spread_pct == Decimal("5")
    assert cfg.options.stop_market_after_gain.raise_max_spread_pct == Decimal("6")
    assert cfg.watchlist.name == "Trader" and cfg.watchlist.id is None
    assert cfg.go_live.met is False
    assert cfg.stop_market_min_multiple == Decimal("1.25")
    assert cfg.warnings == []


def test_raise_spread_defaults_to_the_first_swap_spread():
    d = example()
    d["options"]["stop_market_after_gain"].pop("raise_max_spread_pct")
    cfg = tc.validate(d)
    assert cfg.options.stop_market_after_gain.raise_max_spread_pct == Decimal("5")
    assert cfg.warnings == []
    d["options"]["stop_market_after_gain"]["raise_max_spread_pct"] = None
    assert tc.validate(d).options.stop_market_after_gain.raise_max_spread_pct == Decimal("5")


def test_lock_gain_written_as_float_still_gives_1_25():
    d = example()
    d["options"]["ratchet"]["lock_gain_pct"] = 25.0
    assert str(tc.validate(d).stop_market_min_multiple) == "1.25"


def test_paths_follow_the_config_file(tmp_path):
    ws = make_workspace(tmp_path)
    cfg = tc.load(os.path.join(ws, "trading", "trader.json"))
    assert cfg.workspace == os.path.abspath(ws)
    assert cfg.trades_path == os.path.join(os.path.abspath(ws), "trading", "ledger", "trades.csv")


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.pop("schema"), "schema must be 1"),
    (lambda d: d.update(schema=2), "schema must be 1"),
    (lambda d: d.update(schema=True), "schema must be 1"),
    (lambda d: d.update(owner_name=""), "owner_name must be a non-empty string"),
    (lambda d: d.update(phase="dry_run"), "phase must be one of DRY_RUN, LIVE, HALTED"),
    (lambda d: d.pop("phase"), "phase must be one of"),
    (lambda d: d.pop("timezone"), "timezone must be a non-empty string"),
    (lambda d: d.pop("account"), "account must be an object"),
    (lambda d: d["account"].update(number=123456789), "account.number must be a non-empty string"),
    (lambda d: d["account"].update(number=" ACCT "), "account.number must be a non-empty string"),
    (lambda d: d["account"].update(start_balance=0), "account.start_balance must be positive"),
    (lambda d: d["account"].update(start_balance="5000"), "account.start_balance must be a number"),
    (lambda d: d.pop("limits"), "limits must be an object"),
    (lambda d: d["limits"].update(option_cap_dollars=-1), "limits.option_cap_dollars must be positive"),
    (lambda d: d["limits"].update(option_cap_dollars=True), "limits.option_cap_dollars must be a number"),
    (lambda d: d["limits"].pop("share_cap_dollars"), "limits.share_cap_dollars must be a number"),
    (lambda d: d["limits"].update(cash_floor_pct=100), "limits.cash_floor_pct must be below 100"),
    (lambda d: d["limits"].update(max_open_positions=0), "limits.max_open_positions must be at least 1"),
    (lambda d: d["limits"].update(max_open_positions=2.5), "limits.max_open_positions must be a whole number"),
    (lambda d: d["limits"].update(daily_loss_stop_pct=0), "limits.daily_loss_stop_pct must be positive"),
    (lambda d: d["limits"].update(halt_account_value=-5), "limits.halt_account_value must be at least 0"),
    (lambda d: d["options"].update(expiry_days_min=70), "expiry_days_min must not be above"),
    (lambda d: d["options"].update(overnight_cushion=1.2), "options.overnight_cushion must be at most 1"),
    (lambda d: d["options"]["ratchet"].update(lock_gain_pct=60), "lock_gain_pct must be below trigger_gain_pct"),
    (lambda d: d["options"]["ratchet"].pop("lock_gain_pct"), "options.ratchet.lock_gain_pct must be a number"),
    (lambda d: d["options"].pop("ratchet"), "ratchet must be an object"),
    (lambda d: d["options"]["stop_market_after_gain"].update(enabled="yes"), "enabled must be true or false"),
    (lambda d: d["options"]["stop_market_after_gain"].update(not_before="6:45"), "not_before must be HH:MM"),
    (lambda d: d["options"]["stop_market_after_gain"].update(raise_max_spread_pct=4),
     "raise_max_spread_pct must not be below max_spread_pct"),
    (lambda d: d["options"]["stop_market_after_gain"].update(raise_max_spread_pct="6"),
     "raise_max_spread_pct must be a number"),
    (lambda d: d["options"]["stop_market_after_gain"].update(raise_max_spread_pct=0),
     "raise_max_spread_pct must be positive"),
    (lambda d: d["options"]["stop_market_after_gain"].update(not_before="25:00"), "not_before must be HH:MM"),
    (lambda d: d["schedule"].update(offset_minutes=4), "schedule.offset_minutes must be a whole number from 0 to 3"),
    (lambda d: d["schedule"].update(offset_minutes=-1), "schedule.offset_minutes must be a whole number from 0 to 3"),
    (lambda d: d["schedule"].update(offset_minutes=1.5), "schedule.offset_minutes must be a whole number from 0 to 3"),
    (lambda d: d["allowed"].update(spreads=True), "allowed.spreads is not a switch this product has"),
    (lambda d: d["allowed"].update(crypto=False), "allowed.crypto is not a switch"),
    (lambda d: d["allowed"].pop("shares"), "allowed.shares must be true or false"),
    (lambda d: d["watchlist"].update(name=""), "watchlist.name must be a non-empty string"),
    (lambda d: d["watchlist"].update(id=42), "watchlist.id must be a non-empty string or null"),
    (lambda d: d["notifications"].update(push=1), "notifications.push must be true or false"),
    (lambda d: d["go_live"].update(extensions=-1), "go_live.extensions must be at least 0"),
    (lambda d: d.update(phase="LIVE"), "phase is LIVE but go_live.met is false"),
])
def test_validation_errors(mutate, needle):
    d = example()
    mutate(d)
    found = problems(d)
    assert any(needle in p for p in found), found


def test_all_problems_are_reported_at_once():
    d = example()
    d["phase"] = "PAPER"
    d["limits"]["option_cap_dollars"] = 0
    d["allowed"]["shorting"] = True
    assert len(problems(d)) >= 3


@pytest.mark.parametrize("data", [[], None, "x", 5])
def test_non_object_is_an_error(data):
    assert problems(data) == ["the file must hold one JSON object"]


def test_live_with_gate_met_is_valid():
    cfg = tc.validate(base_config("LIVE"))
    assert cfg.phase == "LIVE"


def test_halted_is_valid_without_gate():
    d = example()
    d["phase"] = "HALTED"
    assert tc.validate(d).phase == "HALTED"


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["limits"].update(option_cap_dollars=1600), "option_cap_dollars 1600 is more than 30%"),
    (lambda d: d["limits"].update(share_cap_dollars=1501), "share_cap_dollars 1501 is more than 30%"),
    (lambda d: d["limits"].update(halt_account_value=5000), "halt_account_value 5000 is not below"),
    (lambda d: d.update(extra="x"), "unknown top-level key 'extra'"),
])
def test_sanity_rules_are_warnings(mutate, needle):
    d = example()
    mutate(d)
    cfg = tc.validate(d)
    assert any(needle in w for w in cfg.warnings), cfg.warnings


def test_cap_exactly_30_percent_is_no_warning():
    d = example()
    d["limits"]["option_cap_dollars"] = 1500
    assert tc.validate(d).warnings == []


# ---------------------------------------------------------------- finding and loading

def test_find_uses_project_dir_only(tmp_path):
    good = make_workspace(tmp_path / "good")
    assert tc.find_config(environ={"CLAUDE_PROJECT_DIR": "/nonexistent-ws"}, cwd=good) == \
        os.path.join("/nonexistent-ws", "trading", "trader.json")


def test_find_walks_up_without_project_dir(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    deep = os.path.join(ws, "trading", "journal", "2026")
    os.makedirs(deep)
    assert tc.find_config(environ={}, cwd=deep) == os.path.join(os.path.abspath(ws), "trading", "trader.json")


def test_find_raises_when_nothing_above(tmp_path):
    with pytest.raises(tc.ConfigError) as e:
        tc.find_config(environ={}, cwd=str(tmp_path))
    assert "no trading/trader.json found" in e.value.problems[0]


def test_load_missing_file(tmp_path):
    with pytest.raises(tc.ConfigError) as e:
        tc.load(str(tmp_path / "trader.json"))
    assert e.value.problems == ["file not found"]


def test_load_malformed_json(tmp_path):
    p = tmp_path / "trader.json"
    p.write_text("{ nope")
    with pytest.raises(tc.ConfigError) as e:
        tc.load(str(p))
    assert e.value.problems[0].startswith("not valid JSON")


def test_load_via_environment(tmp_path):
    ws = make_workspace(tmp_path)
    cfg = tc.load(environ={"CLAUDE_PROJECT_DIR": ws})
    assert cfg.account.number == "ACCT-TEST-0001"


# ---------------------------------------------------------------- CLI

def cli(*args, cwd=None, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "trader_config.py")] + list(args),
                       capture_output=True, text=True, cwd=cwd or ROOT, env=env, timeout=30)
    return p.returncode, p.stdout, p.stderr


def test_cli_check_ok():
    code, out, _ = cli("--check", "--path", EXAMPLE)
    assert code == 0
    assert out.startswith("OK: ") and "phase DRY_RUN" in out


def test_cli_check_prints_warnings(tmp_path):
    cfg = base_config("DRY_RUN")
    cfg["limits"]["option_cap_dollars"] = 4000
    ws = make_workspace(tmp_path, config=cfg)
    code, out, _ = cli("--check", env_extra={"CLAUDE_PROJECT_DIR": ws})
    assert code == 0
    assert "warning: limits.option_cap_dollars" in out


def test_cli_check_problems(tmp_path):
    ws = make_workspace(tmp_path, raw_config='{"schema": 1, "phase": "SOON"}')
    code, out, _ = cli("--check", env_extra={"CLAUDE_PROJECT_DIR": ws})
    assert code == 1
    assert out.startswith("PROBLEMS in ")
    assert "  - phase must be one of" in out


def test_cli_check_missing(tmp_path):
    code, out, _ = cli("--check", env_extra={"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert code == 1 and "file not found" in out


@pytest.mark.parametrize("args", [[], ["--fix"], ["--check", "--path"], ["--check", "x", "y"]])
def test_cli_usage(args):
    code, _, err = cli(*args)
    assert code == 2 and "usage:" in err
