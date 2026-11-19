"""tools/doctor.py against tmp workspaces: every script check, the report, the bundle, and that it writes
nothing else. All values are fake."""
import csv
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from conftest import ROOT, TOOLS, base_config

sys.path.insert(0, TOOLS)
import doctor  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

SCRIPT = os.path.join(TOOLS, "doctor.py")
LEDGER = os.path.join(TOOLS, "ledger.py")
RENDER = os.path.join(TOOLS, "render_rules.py")
SAMPLE = os.path.join(ROOT, "templates", "STRATEGY.sample.md")
LA = ZoneInfo("America/Los_Angeles")
ACCOUNT = "ACCT-TEST-0001"          # conftest's fake account; must never appear in full in any output
PLUGIN = "mcp__plugin_personal-ai-trader_robinhood-trading__"
PLAIN = "mcp__robinhood-trading__"

# A Wednesday that is a regular trading day (market 06:30-13:00 in Los Angeles), and other days.
WED_MARKET = datetime(2026, 11, 4, 9, 0, tzinfo=LA)
WED_PREMARKET = datetime(2026, 11, 4, 5, 0, tzinfo=LA)
WED_AFTER_CLOSE = datetime(2026, 11, 4, 13, 10, tzinfo=LA)
WED_EVENING = datetime(2026, 11, 4, 19, 0, tzinfo=LA)
SATURDAY = datetime(2026, 11, 7, 10, 0, tzinfo=LA)
SUNDAY_MORNING = datetime(2026, 11, 8, 10, 0, tzinfo=LA)
THANKSGIVING = datetime(2026, 11, 26, 9, 0, tzinfo=LA)

HAVE_OPENPYXL = True
try:
    import openpyxl  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_OPENPYXL = False
needs_openpyxl = pytest.mark.skipif(not HAVE_OPENPYXL, reason="openpyxl not installed in the test interpreter")


def env(user_conf):
    e = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    e["CLAUDE_CONFIG_DIR"] = str(user_conf)
    return e


SETUP_SETTINGS = os.path.join(TOOLS, "setup_settings.py")
DENIED_TOOLS = ("preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
                "get_crypto_account_onboarding_info", "exercise_option", "cancel_option_exercise")


def settings_path(ws):
    return os.path.join(ws, ".claude", "settings.json")


def read_settings(ws):
    with open(settings_path(ws)) as f:
        return json.load(f)


def write_settings(ws, data):
    with open(settings_path(ws), "w") as f:
        json.dump(data, f)


def own_strategy():
    with open(SAMPLE, encoding="utf-8") as f:
        text = f.read()
    return "\n".join(l for l in text.splitlines() if not l.startswith(">")) + "\n"


def write_venv(ws, mode="good"):
    """A stand-in for trading/.venv: bin/python runs the test interpreter (with or without site-packages)."""
    bindir = os.path.join(ws, "trading", ".venv", "bin")
    os.makedirs(bindir, exist_ok=True)
    py = os.path.join(bindir, "python")
    flag = " -S" if mode == "no-openpyxl" else ""
    with open(py, "w") as f:
        f.write('#!/bin/sh\nexec "%s"%s "$@"\n' % (sys.executable, flag))
    os.chmod(py, 0o755)
    return py


def make_ws(tmp_path, name="ws", phase="DRY_RUN", mutate=None, trades=None):
    """A complete, healthy workspace. Returns (workspace path, user config dir)."""
    ws = tmp_path / name
    trading = ws / "trading"
    for d in ("journal", "ledger", "reviews", "logs", "shadow"):
        os.makedirs(trading / d, exist_ok=True)
    cfg = base_config(phase)
    if mutate:
        mutate(cfg)
    (trading / "trader.json").write_text(json.dumps(cfg, indent=2))
    user_conf = tmp_path / (name + "-userconf")
    os.makedirs(user_conf, exist_ok=True)
    e = env(user_conf)
    subprocess.run([sys.executable, RENDER, "--workspace", str(ws)], capture_output=True, env=e)  # may be invalid
    subprocess.run([sys.executable, LEDGER, "init", "--workspace", str(ws)], check=True, capture_output=True, env=e)
    for t in trades or []:
        subprocess.run([sys.executable, LEDGER, "open", json.dumps(t), "--workspace", str(ws)], check=True,
                       capture_output=True, env=e)
    subprocess.run([sys.executable, SETUP_SETTINGS, "--workspace", str(ws), "--yes"], check=True,
                   capture_output=True, env=e)
    (trading / "STATE.md").write_text("# State\n")
    (trading / "PLAN.md").write_text("# Plan\n")
    (trading / "STRATEGY.md").write_text(own_strategy())
    write_venv(str(ws))
    return str(ws), str(user_conf)


def set_mtime(path, when):
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def ctx_for(ws, user_conf, now=WED_EVENING, plugin_root=None):
    kw = {"plugin_root": plugin_root} if plugin_root else {}
    return doctor.Context(ws, now=now, user_config_dir=user_conf, **kw)


def one(results):
    assert len(results) == 1
    return results[0]


def cli(ws, user_conf, *args, stdin=None):
    p = subprocess.run([sys.executable, SCRIPT, "--workspace", ws] + list(args), capture_output=True, text=True,
                       env=env(user_conf), cwd=ws, input=stdin, timeout=180)
    return p


def by_id(rep):
    return {r["id"]: r for r in rep["checks"]}


OPEN_TRADE = {"mode": "paper", "underlying": "XYZ", "instrument": "call", "strike": "50", "expiry": "2026-12-18",
              "quantity": "1", "opened_at": "2026-11-03 07:40", "entry_price": "2.10", "entry_cost": "210",
              "thesis": "fake thesis", "planned_stop": "1.05", "planned_target": "3.15",
              "planned_time_exit": "2026-12-01", "option_id": "opt-test-0001"}


# ---------------------------------------------------------------- the whole run

@needs_openpyxl
def test_healthy_workspace_passes_everything(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--format", "json", "--now", "2026-11-04T19:00")
    assert p.returncode == 0, p.stdout + p.stderr
    rep = json.loads(p.stdout)
    statuses = {r["id"]: r["status"] for r in rep["checks"]}
    assert set(statuses) == set(doctor.CHECK_IDS)
    assert all(s == "PASS" for s in statuses.values()), statuses
    assert rep["summary"]["FAIL"] == 0
    assert rep["phase"] == "DRY_RUN"
    assert rep["account_masked"] == "****0001"
    assert ACCOUNT not in p.stdout


def test_table_lists_failures_first_with_fixes(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(settings_path(ws))
    p = cli(ws, uc, "--now", "2026-11-04T19:00")
    assert p.returncode == 1
    lines = p.stdout.splitlines()
    assert lines[0].startswith("Status")
    assert lines[2].startswith("FAIL") and "Deny list" in lines[2]
    assert "Fixes:" in p.stdout and "trader-setup" in p.stdout
    assert ACCOUNT not in p.stdout


def test_both_format_prints_json_then_table(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--format", "both", "--only", "config,version")
    head, _, tail = p.stdout.partition("\n\nStatus")
    assert json.loads(head)["summary"]["PASS"] == 2
    assert "config" in tail


def test_only_and_unknown_ids(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--format", "json", "--only", "calendar")
    assert [r["id"] for r in json.loads(p.stdout)["checks"]] == ["calendar"]
    p = cli(ws, uc, "--only", "nonsense")
    assert p.returncode == 2


def test_no_workspace_found(tmp_path):
    empty = tmp_path / "empty"
    os.makedirs(empty)
    p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, cwd=str(empty),
                       env=env(tmp_path), timeout=60)
    assert p.returncode == 2 and "cannot find the workspace" in p.stdout


@needs_openpyxl
def test_doctor_writes_nothing_in_the_workspace(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    (open(os.path.join(ws, "trading", "ledger", "Trades.xlsx"), "w")).write("SENTINEL")

    def snapshot():
        out = {}
        for base, dirs, files in os.walk(ws):
            for f in files:
                p = os.path.join(base, f)
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns)
        return out

    before = snapshot()
    p = cli(ws, uc, "--format", "json", "--now", "2026-11-04T09:00")
    assert p.returncode in (0, 1)
    assert snapshot() == before
    with open(os.path.join(ws, "trading", "ledger", "Trades.xlsx")) as f:
        assert f.read() == "SENTINEL"


def test_a_crashing_check_becomes_a_fail_row(tmp_path, monkeypatch):
    ws, uc = make_ws(tmp_path)

    def boom(ctx):
        raise RuntimeError("kaput")
    monkeypatch.setitem(doctor.CHECKS, "calendar", boom)
    r = one(doctor.run_checks(ctx_for(ws, uc), only={"calendar"}))
    assert r["status"] == "FAIL" and "kaput" in r["detail"]


# ---------------------------------------------------------------- config and rules

def test_config_missing(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(os.path.join(ws, "trading", "trader.json"))
    ctx = ctx_for(ws, uc)
    assert one(doctor.check_config(ctx))["status"] == "FAIL"
    assert one(doctor.check_rules(ctx))["status"] == "SKIP"
    g = one(doctor.check_guard_script(ctx))
    assert g["status"] == "WARN" and "fails closed" in g["detail"]


def test_config_invalid_json(tmp_path):
    ws, uc = make_ws(tmp_path)
    with open(os.path.join(ws, "trading", "trader.json"), "w") as f:
        f.write("{not json")
    r = one(doctor.check_config(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "not valid JSON" in r["detail"] and r["fix"]


def test_config_contradiction_is_a_fail(tmp_path):
    def live_without_gate(cfg):
        cfg["phase"] = "LIVE"
        cfg["go_live"]["met"] = False
    ws, uc = make_ws(tmp_path, mutate=live_without_gate)
    r = one(doctor.check_config(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "go_live.met" in r["detail"]


def test_config_sanity_warning(tmp_path):
    def big_cap(cfg):
        cfg["limits"]["option_cap_dollars"] = 4000
    ws, uc = make_ws(tmp_path, mutate=big_cap)
    r = one(doctor.check_config(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "30%" in r["detail"]


def test_rules_match_drift_and_missing(tmp_path):
    ws, uc = make_ws(tmp_path)
    ctx = ctx_for(ws, uc)
    assert one(doctor.check_rules(ctx))["status"] == "PASS"
    rules = os.path.join(ws, "trading", "RULES.md")
    with open(rules, "a") as f:
        f.write("\nhand edit\n")
    r = one(doctor.check_rules(ctx))
    assert r["status"] == "WARN" and "differs" in r["detail"] and "render_rules.py" in r["fix"]
    os.remove(rules)
    r = one(doctor.check_rules(ctx))
    assert r["status"] == "WARN" and "missing" in r["detail"]


# ---------------------------------------------------------------- hook and guard

def fake_plugin(tmp_path, hooks=None, guard_body=None):
    root = tmp_path / "plugin"
    shutil.copytree(os.path.join(ROOT, "tools"), root / "tools")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), root / ".claude-plugin")
    os.makedirs(root / "hooks")
    if hooks is None:
        shutil.copy(os.path.join(ROOT, "hooks", "hooks.json"), root / "hooks" / "hooks.json")
    elif hooks != "absent":
        (root / "hooks" / "hooks.json").write_text(json.dumps(hooks))
    if guard_body is not None:
        (root / "tools" / "order_guard.py").write_text(guard_body)
    return str(root)


def test_hook_registration_passes_for_the_shipped_plugin(tmp_path):
    ws, uc = make_ws(tmp_path)
    assert one(doctor.check_hook(ctx_for(ws, uc)))["status"] == "PASS"


def test_hook_file_missing(tmp_path):
    ws, uc = make_ws(tmp_path)
    r = one(doctor.check_hook(ctx_for(ws, uc, plugin_root=fake_plugin(tmp_path, hooks="absent"))))
    assert r["status"] == "FAIL" and "do not trade" in r["fix"].lower()


def test_hook_without_on_failure_block(tmp_path):
    ws, uc = make_ws(tmp_path)
    with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
        hooks = json.load(f)
    del hooks["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"]
    r = one(doctor.check_hook(ctx_for(ws, uc, plugin_root=fake_plugin(tmp_path, hooks=hooks))))
    assert r["status"] == "FAIL" and "onFailure" in r["detail"]


def test_hook_matcher_missing_a_naming(tmp_path):
    ws, uc = make_ws(tmp_path)
    with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
        hooks = json.load(f)
    hooks["hooks"]["PreToolUse"][0]["matcher"] = "^mcp__robinhood-trading__.*$"
    r = one(doctor.check_hook(ctx_for(ws, uc, plugin_root=fake_plugin(tmp_path, hooks=hooks))))
    assert r["status"] == "FAIL" and "mcp__plugin_personal-ai-trader_robinhood-trading__" in r["detail"]


@pytest.mark.parametrize("where", ["settings.json", "settings.local.json", "user"])
def test_disable_all_hooks_is_a_fail(tmp_path, where):
    ws, uc = make_ws(tmp_path)
    if where == "user":
        path = os.path.join(uc, "settings.json")
        data = {}
    else:
        path = os.path.join(ws, ".claude", where)
        data = read_settings(ws) if where == "settings.json" else {}
    data["disableAllHooks"] = True
    with open(path, "w") as f:
        json.dump(data, f)
    r = one(doctor.check_hook(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "disableAllHooks" in r["detail"]


@pytest.mark.parametrize("phase", ["DRY_RUN", "LIVE", "HALTED"])
def test_guard_script_proof_in_every_phase(tmp_path, phase):
    ws, uc = make_ws(tmp_path, phase=phase)
    r = one(doctor.check_guard_script(ctx_for(ws, uc)))
    assert r["status"] == "PASS", r


def test_guard_script_that_lets_everything_through_fails(tmp_path):
    ws, uc = make_ws(tmp_path)
    root = fake_plugin(tmp_path, guard_body="import sys\nsys.exit(0)\n")
    r = one(doctor.check_guard_script(ctx_for(ws, uc, plugin_root=root)))
    assert r["status"] == "FAIL" and "did not refuse" in r["detail"]


def test_guard_script_that_blocks_everything_fails(tmp_path):
    ws, uc = make_ws(tmp_path)
    body = ("import sys\nprint('order_guard: BLOCKED — option orders must have exactly one leg', file=sys.stderr)\n"
            "sys.exit(2)\n")
    r = one(doctor.check_guard_script(ctx_for(ws, uc, plugin_root=fake_plugin(tmp_path, guard_body=body))))
    assert r["status"] == "FAIL" and "allowed single-leg" in r["detail"]


def test_guard_script_with_option_buys_switched_off(tmp_path):
    def no_options(cfg):
        cfg["allowed"]["long_options"] = False
    ws, uc = make_ws(tmp_path, mutate=no_options)
    r = one(doctor.check_guard_script(ctx_for(ws, uc)))
    assert r["status"] == "PASS" and "skipped" in r["detail"]


# ---------------------------------------------------------------- deny list

def test_deny_list_complete(tmp_path):
    ws, uc = make_ws(tmp_path)
    assert one(doctor.check_deny(ctx_for(ws, uc)))["status"] == "PASS"


def test_deny_list_missing_file(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(settings_path(ws))
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "missing" in r["detail"] and "step 8" in r["fix"]


def test_deny_list_unreadable(tmp_path):
    ws, uc = make_ws(tmp_path)
    with open(settings_path(ws), "w") as f:
        f.write("{oops")
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "unreadable" in r["detail"]


@pytest.mark.parametrize("drop", [PLUGIN + "exercise_option", PLAIN + "cancel_option_exercise",
                                  PLAIN + "get_crypto_account_onboarding_info", PLUGIN + "place_crypto_order"])
def test_deny_list_one_entry_missing(tmp_path, drop):
    ws, uc = make_ws(tmp_path)
    data = read_settings(ws)
    data["permissions"]["deny"] = [d for d in data["permissions"]["deny"] if d != drop]
    write_settings(ws, data)
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and drop in r["detail"] and "1 deny entry" in r["detail"]
    assert "step 8" in r["fix"]


def test_deny_list_only_one_naming(tmp_path):
    ws, uc = make_ws(tmp_path)
    data = read_settings(ws)
    data["permissions"]["deny"] = [d for d in data["permissions"]["deny"] if not d.startswith(PLUGIN)]
    write_settings(ws, data)
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "6 deny entries" in r["detail"]


def test_deny_list_hand_written_minimal_list_fails(tmp_path):
    """Only the twelve tool entries, without setup's settings-file and plugin-folder rules."""
    ws, uc = make_ws(tmp_path)
    write_settings(ws, {"permissions": {"deny": [p + t for p in (PLAIN, PLUGIN) for t in DENIED_TOOLS]}})
    assert one(doctor.check_deny(ctx_for(ws, uc)))["status"] == "FAIL"


def test_stale_allow_rule_after_a_plugin_move_warns(tmp_path):
    ws, uc = make_ws(tmp_path)
    data = read_settings(ws)
    data["permissions"]["allow"].append('Bash(python3 "/old/plugin/folder/tools/schedule.py" *)')
    write_settings(ws, data)
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "stale" in r["detail"]
    assert "step 8 of /personal-ai-trader:trader-setup" in r["fix"]


def test_missing_ask_rule_warns(tmp_path):
    ws, uc = make_ws(tmp_path)
    data = read_settings(ws)
    data["permissions"]["ask"] = []
    write_settings(ws, data)
    r = one(doctor.check_deny(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "ask missing" in r["detail"]


def test_deny_check_fails_when_setup_settings_is_missing(tmp_path):
    ws, uc = make_ws(tmp_path)
    root = fake_plugin(tmp_path)
    os.remove(os.path.join(root, "tools", "setup_settings.py"))
    r = one(doctor.check_deny(ctx_for(ws, uc, plugin_root=root)))
    assert r["status"] == "FAIL"


# ---------------------------------------------------------------- python and the ledger build

def test_python_no_venv(tmp_path):
    ws, uc = make_ws(tmp_path)
    shutil.rmtree(os.path.join(ws, "trading", ".venv"))
    r = one(doctor.check_python(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "venv" in r["detail"]


def test_python_without_openpyxl(tmp_path):
    ws, uc = make_ws(tmp_path)
    write_venv(ws, "no-openpyxl")
    r = one(doctor.check_python(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "openpyxl" in r["detail"]


@needs_openpyxl
def test_python_ledger_not_initialised(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(os.path.join(ws, "trading", "ledger", "orders.csv"))
    r = one(doctor.check_python(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "ledger.py" in r["fix"]


@needs_openpyxl
def test_python_build_runs_on_a_copy_only(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    ledger_dir = os.path.join(ws, "trading", "ledger")
    with open(os.path.join(ledger_dir, "Trades.xlsx"), "w") as f:
        f.write("SENTINEL")
    before = sorted(os.listdir(ledger_dir))
    r = one(doctor.check_python(ctx_for(ws, uc)))
    assert r["status"] == "PASS" and "copy" in r["detail"]
    assert sorted(os.listdir(ledger_dir)) == before
    with open(os.path.join(ledger_dir, "Trades.xlsx")) as f:
        assert f.read() == "SENTINEL"


@needs_openpyxl
def test_python_build_failure_is_reported(tmp_path):
    ws, uc = make_ws(tmp_path)
    path = os.path.join(ws, "trading", "ledger", "trades.csv")
    with open(path) as f:
        header = f.readline().strip().split(",")
    row = {c: "" for c in header}
    row.update({"trade_id": "P0001", "mode": "paper", "status": "closed", "pnl_dollars": "not-a-number"})
    with open(path, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=header).writerow(row)
    r = one(doctor.check_python(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and "build" in r["detail"]


# ---------------------------------------------------------------- files and strategy

def test_files_present(tmp_path):
    ws, uc = make_ws(tmp_path)
    assert one(doctor.check_files(ctx_for(ws, uc)))["status"] == "PASS"


@pytest.mark.parametrize("folder", ["journal", "ledger", "reviews", "logs"])
def test_files_missing_folder(tmp_path, folder):
    ws, uc = make_ws(tmp_path)
    shutil.rmtree(os.path.join(ws, "trading", folder))
    r = one(doctor.check_files(ctx_for(ws, uc)))
    assert r["status"] == "FAIL" and ("trading/%s/ missing" % folder) in r["detail"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write anywhere")
def test_files_read_only_folder(tmp_path):
    ws, uc = make_ws(tmp_path)
    journal = os.path.join(ws, "trading", "journal")
    os.chmod(journal, stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = one(doctor.check_files(ctx_for(ws, uc)))
    finally:
        os.chmod(journal, 0o755)
    assert r["status"] == "FAIL" and "not writable" in r["detail"]


def test_files_missing_state_is_a_warning_here(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(os.path.join(ws, "trading", "PLAN.md"))
    r = one(doctor.check_files(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "PLAN.md" in r["detail"]


def test_files_no_trading_folder(tmp_path):
    ws = tmp_path / "bare"
    os.makedirs(ws)
    r = one(doctor.check_files(doctor.Context(str(ws), now=WED_EVENING, user_config_dir=str(tmp_path))))
    assert r["status"] == "FAIL"


def test_strategy_own(tmp_path):
    ws, uc = make_ws(tmp_path)
    r = one(doctor.check_strategy(ctx_for(ws, uc)))
    assert r["status"] == "PASS" and "4 numbered setups" in r["detail"]


def test_strategy_still_the_sample(tmp_path):
    ws, uc = make_ws(tmp_path)
    shutil.copy(SAMPLE, os.path.join(ws, "trading", "STRATEGY.md"))
    r = one(doctor.check_strategy(ctx_for(ws, uc)))
    assert r["status"] == "WARN" and "sample" in r["detail"] and "trader-strategy" in r["fix"]


def test_strategy_missing(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(os.path.join(ws, "trading", "STRATEGY.md"))
    assert one(doctor.check_strategy(ctx_for(ws, uc)))["status"] == "WARN"


def test_strategy_missing_section_and_setups(tmp_path):
    text = own_strategy().replace("## Watchlist to screen at prep", "## Things I like")
    assert doctor.strategy_problems(text) == ["no '## Watchlist to screen at prep' section"]
    no_setups = "\n".join(l for l in own_strategy().splitlines() if not l[:2] in ("1.", "2.", "3.", "4."))
    assert any("numbered setups" in p for p in doctor.strategy_problems(no_setups))


def test_strategy_not_in_play_at_any_level_and_open_position_singular():
    text = own_strategy().replace("### Not in play", "## Not in play").replace("## Open positions",
                                                                                "## Open position")
    assert doctor.strategy_problems(text) == []


def test_sample_template_has_every_section():
    with open(SAMPLE, encoding="utf-8") as f:
        assert doctor.strategy_problems(f.read()) == []


# ---------------------------------------------------------------- state age

@pytest.mark.parametrize("now,age_min,expected", [
    (WED_MARKET, 30, "PASS"),            # fresh
    (WED_MARKET, 120, "FAIL"),           # stale in market hours
    (WED_AFTER_CLOSE, 120, "WARN"),      # stale after the close, before the wrap window ends
    (WED_PREMARKET, 900, "PASS"),        # before today's window opens
    (WED_EVENING, 400, "PASS"),          # after the wrap window
    (SATURDAY, 3000, "PASS"),            # weekend
    (THANKSGIVING, 3000, "PASS"),        # holiday
])
def test_state_age(tmp_path, now, age_min, expected):
    ws, uc = make_ws(tmp_path)
    set_mtime(os.path.join(ws, "trading", "STATE.md"), now - timedelta(minutes=age_min))
    r = one(doctor.check_state(ctx_for(ws, uc, now=now)))
    assert r["status"] == expected, r
    if expected != "PASS":
        assert "KEEP-RUNNING" in r["fix"]


def test_state_missing_is_a_fail(tmp_path):
    ws, uc = make_ws(tmp_path)
    os.remove(os.path.join(ws, "trading", "STATE.md"))
    assert one(doctor.check_state(ctx_for(ws, uc, now=WED_MARKET)))["status"] == "FAIL"


def test_state_via_cli_now(tmp_path):
    ws, uc = make_ws(tmp_path)
    set_mtime(os.path.join(ws, "trading", "STATE.md"), datetime(2026, 11, 4, 6, 40, tzinfo=LA))
    p = cli(ws, uc, "--format", "json", "--only", "state", "--now", "2026-11-04T10:00")
    assert by_id(json.loads(p.stdout))["state"]["status"] == "FAIL"
    p = cli(ws, uc, "--format", "json", "--only", "state", "--now", "2026-11-04T07:30")
    assert by_id(json.loads(p.stdout))["state"]["status"] == "PASS"


# ---------------------------------------------------------------- watcher

def write_watch_log(ws, *lines):
    with open(os.path.join(ws, "trading", "logs", "watch.log"), "w") as f:
        for when, text in lines:
            f.write(when.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def test_watcher_not_needed_without_positions(tmp_path):
    ws, uc = make_ws(tmp_path)
    assert one(doctor.check_watcher(ctx_for(ws, uc, now=WED_MARKET)))["status"] == "PASS"


def test_watcher_outside_market_hours(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    r = one(doctor.check_watcher(ctx_for(ws, uc, now=WED_EVENING)))
    assert r["status"] == "PASS" and "outside market hours" in r["detail"]


def test_watcher_missing_log(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    r = one(doctor.check_watcher(ctx_for(ws, uc, now=WED_MARKET)))
    assert r["status"] == "WARN" and "no watch.log" in r["detail"]


def test_watcher_fresh(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    write_watch_log(ws, (datetime(2026, 11, 4, 8, 50), "START XYZ below 48 every 300s"),
                    (datetime(2026, 11, 4, 8, 56), "OK XYZ=50.10"))
    assert one(doctor.check_watcher(ctx_for(ws, uc, now=WED_MARKET)))["status"] == "PASS"


def test_watcher_stale(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    write_watch_log(ws, (datetime(2026, 11, 4, 8, 20), "OK XYZ=50.10"))
    r = one(doctor.check_watcher(ctx_for(ws, uc, now=WED_MARKET)))
    assert r["status"] == "WARN" and "40 min" in r["detail"]


def test_watcher_exited(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    write_watch_log(ws, (datetime(2026, 11, 4, 8, 58), "EXIT TRIGGER XYZ below 48 price=47.90 at 08:58:00"))
    r = one(doctor.check_watcher(ctx_for(ws, uc, now=WED_MARKET)))
    assert r["status"] == "WARN" and "exited" in r["detail"]


# ---------------------------------------------------------------- version and calendar

@pytest.mark.parametrize("schema,expected,word", [(1, "PASS", ""), (0, "FAIL", "migration"), (2, "FAIL", "newer")])
def test_version(tmp_path, schema, expected, word):
    ws, uc = make_ws(tmp_path)
    path = os.path.join(ws, "trading", "trader.json")
    with open(path) as f:
        cfg = json.load(f)
    cfg["schema"] = schema
    with open(path, "w") as f:
        json.dump(cfg, f)
    r = one(doctor.check_version(ctx_for(ws, uc)))
    assert r["status"] == expected and word in r["detail"]
    assert "0.1.0" in r["detail"] or r["detail"].startswith("plugin ")


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 11, 4, 9, 0, tzinfo=LA), "PASS"),
    (datetime(2027, 10, 31, 9, 0, tzinfo=LA), "PASS"),     # 61 days before the end
    (datetime(2027, 11, 1, 9, 0, tzinfo=LA), "WARN"),      # 60 days before the end
    (datetime(2027, 12, 31, 9, 0, tzinfo=LA), "WARN"),
    (datetime(2028, 1, 3, 9, 0, tzinfo=LA), "FAIL"),
    (datetime(2025, 12, 31, 9, 0, tzinfo=LA), "FAIL"),
])
def test_calendar_coverage(tmp_path, now, expected):
    ws, uc = make_ws(tmp_path)
    r = one(doctor.check_calendar(ctx_for(ws, uc, now=now)))
    assert r["status"] == expected, r
    if expected != "PASS":
        assert "market_calendar.json" in r["fix"]


def test_calendar_warns_within_60_days_boundary(tmp_path):
    ws, uc = make_ws(tmp_path)
    end = doctor.schedule.load_calendar()["_to"]
    for days, expected in ((61, "PASS"), (60, "WARN"), (0, "WARN")):
        day = end - timedelta(days=days)
        now = datetime(day.year, day.month, day.day, 9, 0, tzinfo=LA)
        assert one(doctor.check_calendar(ctx_for(ws, uc, now=now)))["status"] == expected, days


# ---------------------------------------------------------------- expected schedules and open trades

def test_expected_schedules_mid_session(tmp_path):
    ws, uc = make_ws(tmp_path)
    exp = doctor.expected_schedules(ctx_for(ws, uc, now=WED_MARKET))
    assert [r["local_time"] for r in exp["today_remaining"]["runs"]] == \
        ["09:33", "10:33", "11:33", "12:12", "12:56", "13:16"]
    assert exp["next_trading_day"]["date"] == "2026-11-05"
    assert exp["next_trading_day"]["required"] is False
    assert exp["sunday_review"]["runs"][0]["local_date"] == "2026-11-08"


def test_expected_schedules_after_the_wrap(tmp_path):
    ws, uc = make_ws(tmp_path)
    exp = doctor.expected_schedules(ctx_for(ws, uc, now=WED_EVENING))
    assert exp["today_remaining"]["runs"] == []
    assert exp["next_trading_day"]["required"] is True
    assert len(exp["next_trading_day"]["runs"]) == 10


def test_expected_schedules_weekend_and_sunday(tmp_path):
    ws, uc = make_ws(tmp_path)
    sat = doctor.expected_schedules(ctx_for(ws, uc, now=SATURDAY))
    assert sat["next_trading_day"]["date"] == "2026-11-09" and sat["next_trading_day"]["required"] is True
    assert sat["sunday_review"]["runs"][0]["local_date"] == "2026-11-08"
    sun = doctor.expected_schedules(ctx_for(ws, uc, now=SUNDAY_MORNING))
    assert sun["sunday_review"]["runs"][0]["local_date"] == "2026-11-08"   # today's, still ahead
    sun_late = doctor.expected_schedules(ctx_for(ws, uc, now=SUNDAY_MORNING.replace(hour=18)))
    assert sun_late["sunday_review"]["runs"][0]["local_date"] == "2026-11-15"


def test_expected_schedules_around_thanksgiving(tmp_path):
    ws, uc = make_ws(tmp_path)
    exp = doctor.expected_schedules(ctx_for(ws, uc, now=datetime(2026, 11, 25, 19, 0, tzinfo=LA)))
    assert exp["next_trading_day"]["date"] == "2026-11-27"
    assert len(exp["next_trading_day"]["runs"]) == 7   # early close


def test_open_trades_in_report(tmp_path):
    ws, uc = make_ws(tmp_path, trades=[OPEN_TRADE])
    p = cli(ws, uc, "--format", "json", "--only", "version")
    rep = json.loads(p.stdout)
    assert rep["open_trades"][0]["trade_id"] == "P0001"
    assert rep["open_trades"][0]["option_id"] == "opt-test-0001"


# ---------------------------------------------------------------- Robinhood tool naming

def test_classify_tool_names():
    names = [PLUGIN + "get_accounts", PLAIN + "get_accounts", "mcp__claude_ai_Robinhood__place_option_order",
             "Bash", "mcp__other__thing"]
    out = {c["tool"]: c for c in doctor.classify_tool_names(names)}
    assert set(out) == {PLUGIN + "get_accounts", PLAIN + "get_accounts", "mcp__claude_ai_Robinhood__place_option_order"}
    assert out[PLUGIN + "get_accounts"]["covered_by_guard"] is True
    assert out[PLAIN + "get_accounts"]["covered_by_guard"] is True
    assert out["mcp__claude_ai_Robinhood__place_option_order"]["covered_by_guard"] is False
    assert out["mcp__claude_ai_Robinhood__place_option_order"]["naming"] == "unrecognised"


@pytest.mark.parametrize("names,expected", [
    ([PLUGIN + "get_accounts", PLUGIN + "place_option_order"], "PASS"),
    ([PLAIN + "get_accounts"], "PASS"),
    ([PLUGIN + "get_accounts", PLAIN + "get_accounts"], "WARN"),
    ([PLUGIN + "get_accounts", "mcp__robinhood__place_option_order"], "FAIL"),
    ([], "FAIL"),
])
def test_connector_check(names, expected):
    assert one(doctor.check_connector(doctor.classify_tool_names(names)))["status"] == expected


def test_tool_names_via_cli_stdin(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--format", "json", "--only", "version", "--tool-names", "-",
            stdin="\n".join([PLUGIN + "get_accounts", "mcp__robinhood__get_accounts"]))
    rep = json.loads(p.stdout)
    assert p.returncode == 1
    assert by_id(rep)["connector"]["status"] == "FAIL"
    assert len(rep["robinhood_tools"]) == 2
    p = cli(ws, uc, "--format", "json", "--only", "version", "--tool-names", "-",
            stdin=json.dumps([PLAIN + "get_accounts"]))
    assert by_id(json.loads(p.stdout))["connector"]["status"] == "PASS"


# ---------------------------------------------------------------- masking and the support bundle

def test_mask_account():
    assert doctor.mask_account("123456789") == "****6789"
    assert doctor.mask_account("12") == "****"
    assert doctor.mask_account(None) == "****"


def test_scrub_masks_numbers_and_tokens():
    text = ('account 123456789 here; Authorization: Bearer abcdefghijklmnop123; "access_token": "s3cr3tvalue"; '
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl ok; option 6b1e2c3d-0000-4000-8000-123456789abc")
    out = doctor.scrub(text, ["123456789"])
    assert "account ****6789 here" in out
    assert "abcdefghijklmnop123" not in out and "s3cr3tvalue" not in out and "eyJhbGci" not in out
    assert "6b1e2c3d-0000-4000-8000-123456789abc" in out   # an option id that contains the digits is kept


def test_scrub_home_only_when_asked():
    home = os.path.expanduser("~")
    assert doctor.scrub(home + "/x") == home + "/x"
    assert doctor.scrub(home + "/x", home=True) == "~/x"


def test_bundle(tmp_path):
    def real_looking(cfg):
        cfg["account"]["number"] = "987654321"
    ws, uc = make_ws(tmp_path, mutate=real_looking)
    with open(os.path.join(ws, "trading", "journal", "2026-11-03.md"), "w") as f:
        f.write("# Journal\n\n## 07:33 hourly\n- old entry\n\n## 12:12 preclose\n- account 987654321 "
                "token=abcd1234efgh\n- last entry line\n")
    skill = [{"area": "Robinhood connection", "check": "get_accounts", "status": "PASS", "detail": "ok"},
             {"area": "Reconcile", "check": "exits", "status": "FAIL",
              "detail": "XYZ has no resting exit on account 987654321", "fix": "approve the stop in the app"}]
    p = cli(ws, uc, "--bundle", "--skill-results", "-", "--now", "2026-11-04T19:00", stdin=json.dumps(skill))
    assert p.returncode == 1   # the skill's FAIL counts
    path = os.path.join(ws, "trading", "logs", "doctor-2026-11-04.md")
    assert os.path.isfile(path)
    with open(path) as f:
        text = f.read()
    assert "987654321" not in text and "****4321" in text
    assert "abcd1234efgh" not in text
    assert "last entry line" in text and "old entry" not in text
    assert "skill:exits" in text and "no resting exit" in text
    assert text.index("skill:exits") < text.index("| PASS |")       # failures first
    assert '"number": "****4321"' in text
    assert "0.1.0" in text
    assert "987654321" not in p.stdout
    assert "Support bundle" in p.stdout


def test_bundle_rejects_bad_skill_results(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--bundle", "--skill-results", "-", stdin=json.dumps([{"status": "MAYBE"}]))
    assert p.returncode == 2
    assert not os.path.exists(os.path.join(ws, "trading", "logs", "doctor-%s.md" % datetime.now(LA).date()))


def test_stdin_cannot_feed_two_inputs(tmp_path):
    ws, uc = make_ws(tmp_path)
    p = cli(ws, uc, "--skill-results", "-", "--tool-names", "-")
    assert p.returncode == 2
