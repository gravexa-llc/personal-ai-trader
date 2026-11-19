"""tools/render_rules.py: every number comes from trader.json, and --check detects drift."""
import json
import os
import subprocess
import sys

import pytest

from conftest import TOOLS, base_config

sys.path.insert(0, TOOLS)
import render_rules  # noqa: E402
import trader_config  # noqa: E402

SCRIPT = os.path.join(TOOLS, "render_rules.py")


def make_ws(tmp_path, mutate=None):
    cfg = base_config("DRY_RUN")
    cfg["rules_set_on"] = "2026-11-02"
    if mutate:
        mutate(cfg)
    os.makedirs(tmp_path / "trading", exist_ok=True)
    (tmp_path / "trading" / "trader.json").write_text(json.dumps(cfg))
    return str(tmp_path)


def run(ws, *args):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([sys.executable, SCRIPT, "--workspace", ws] + list(args), capture_output=True,
                          text=True, env=env, cwd=TOOLS, timeout=30)


def rules_text(ws):
    with open(os.path.join(ws, "trading", "RULES.md")) as f:
        return f.read()


def odd_numbers(cfg):
    cfg["owner_name"] = "Robin"
    cfg["account"].update(number="ACCT-TEST-4321", nickname="Swing", start_balance=12000)
    cfg["limits"].update(option_cap_dollars=2400, share_cap_dollars=3100, cash_floor_pct=35,
                         max_open_positions=4, max_same_theme=3, daily_loss_stop_pct=6,
                         halt_account_value=9000, loss_streak_pause=4)
    cfg["options"].update(expiry_days_min=28, expiry_days_max=55, min_open_interest=750,
                          max_spread_pct_of_mid=8, default_stop_pct=40, overnight_cushion=0.85)
    cfg["options"]["ratchet"].update(trigger_gain_pct=60, lock_gain_pct=30, trail_pct=20)
    cfg["options"]["stop_market_after_gain"].update(max_spread_pct=4, raise_max_spread_pct=7, not_before="09:46")
    cfg["watchlist"]["name"] = "Swing list"
    cfg["go_live"]["paper_days_required"] = 7


def test_every_number_comes_from_the_config(tmp_path):
    ws = make_ws(tmp_path, odd_numbers)
    p = run(ws)
    assert p.returncode == 0, p.stdout + p.stderr
    text = rules_text(ws)
    for needle in [
        "Only Robin changes them", 'nicknamed "Swing" (number ending 4321)', "**$12,000**",
        "**$2,400** (20% of the $12,000 start)", "default 40% stop the planned loss is up to $960",
        "**$3,100**", "**35%** of account value", "At most **4** positions", "at most **3** that depend",
        "**$9,000** or less (−25% from the start)", "down **6%**", "after **4** losing trades",
        "**28 to 55 days**", "open interest of **750**", "no wider than **8%**", "**−40%** of the premium",
        "reaches **+60%**", "lock in **+30%**", "trails **20%** below", "spread of **4%**",
        "(entry × 1.3)", "not before **09:46**", "spread up to **7%** of the mid", "still needs **4%** or less",
        "There is no floor", "restores the full lock", "**15% below the ratchet stop** (ratchet stop × 0.85",
        '"Swing list"', "7 trading days of paper", "set by Robin on 2026-11-02", "**DRY_RUN**",
    ]:
        assert needle in text, needle
    # Nothing from the default config leaked in.
    for stale in ("$5,000", "$1,000", "$1,500", "$3,500", "Sam"):
        assert stale not in text, stale
    assert "{{" not in text


def test_switches_change_the_text(tmp_path):
    def off(cfg):
        cfg["allowed"]["shares"] = False
        cfg["options"]["stop_market_after_gain"]["enabled"] = False
        cfg["options"]["overnight_cushion"] = 1
        cfg["notifications"]["push"] = False
        cfg["rules_set_on"] = None
    ws = make_ws(tmp_path, off)
    assert run(ws).returncode == 0
    text = rules_text(ws)
    assert "Buying shares is **switched off**" in text
    assert "Option stop-market orders are **switched off**" in text
    assert "No overnight cushion" in text
    assert "Push notifications are off" in text
    assert "set by Sam (date not recorded)" in text


def test_check_detects_drift(tmp_path):
    ws = make_ws(tmp_path)
    p = run(ws, "--check")
    assert p.returncode == 1 and "missing" in p.stdout
    assert run(ws).returncode == 0
    p = run(ws, "--check")
    assert p.returncode == 0 and p.stdout.startswith("OK")
    # A hand edit is drift.
    path = os.path.join(ws, "trading", "RULES.md")
    with open(path, "a") as f:
        f.write("- extra rule\n")
    p = run(ws, "--check")
    assert p.returncode == 1 and "+- extra rule" not in p.stdout and "-- extra rule" in p.stdout
    # A config change is drift too.
    assert run(ws).returncode == 0
    cfg_path = os.path.join(ws, "trading", "trader.json")
    cfg = json.load(open(cfg_path))
    cfg["limits"]["option_cap_dollars"] = 900
    json.dump(cfg, open(cfg_path, "w"))
    p = run(ws, "--check")
    assert p.returncode == 1 and "$900" in p.stdout
    assert run(ws).returncode == 0 and run(ws, "--check").returncode == 0


def test_print_writes_nothing(tmp_path):
    ws = make_ws(tmp_path)
    p = run(ws, "--print")
    assert p.returncode == 0 and p.stdout.startswith("# Trading rules")
    assert not os.path.exists(os.path.join(ws, "trading", "RULES.md"))


def test_invalid_config_exits_2(tmp_path):
    ws = make_ws(tmp_path, lambda c: c["limits"].pop("option_cap_dollars"))
    p = run(ws, "--check")
    assert p.returncode == 2 and "option_cap_dollars" in p.stdout


def test_template_and_renderer_agree():
    cfg = trader_config.validate(base_config("DRY_RUN"))
    text = render_rules.render(cfg)
    assert "{{" not in text and "}}" not in text


def test_unknown_placeholder_is_an_error(tmp_path):
    tmpl = tmp_path / "t.tmpl"
    with open(render_rules.TEMPLATE) as f:
        tmpl.write_text(f.read() + "{{nope}}\n")
    with pytest.raises(ValueError):
        render_rules.render(trader_config.validate(base_config("DRY_RUN")), str(tmpl))


@pytest.mark.parametrize("value", ["2026/11/02", "yesterday", 20261102])
def test_rules_set_on_is_validated(value):
    cfg = base_config("DRY_RUN")
    cfg["rules_set_on"] = value
    with pytest.raises(trader_config.ConfigError):
        trader_config.validate(cfg)


# ---------------------------------------------------------------- owner's notes (trading/RULES-notes.md)

def test_no_notes_file_no_section(tmp_path):
    ws = make_ws(tmp_path)
    assert run(ws).returncode == 0
    text = rules_text(ws)
    assert "Owner's notes" not in text
    assert text.endswith("trips.\n")


def test_notes_are_included_verbatim(tmp_path):
    ws = make_ws(tmp_path)
    notes = ("<!-- header -->\n- 2026-11-02: approval switch left off by choice.\n"
             "- 2026-11-03: **cash floor** kept at 30% {{not a placeholder}} $5 \\n\n")
    (tmp_path / "trading" / "RULES-notes.md").write_text(notes)
    assert run(ws).returncode == 0
    text = rules_text(ws)
    head, sep, tail = text.partition("\n## Owner's notes\n")
    assert sep and head.endswith("trips.\n")
    assert tail.endswith(notes)
    assert "Copied verbatim from `trading/RULES-notes.md`" in tail


def test_notes_without_final_newline(tmp_path):
    ws = make_ws(tmp_path)
    (tmp_path / "trading" / "RULES-notes.md").write_text("- one line")
    assert run(ws).returncode == 0
    assert rules_text(ws).endswith("- one line\n")


def test_editing_notes_is_drift(tmp_path):
    ws = make_ws(tmp_path)
    notes = tmp_path / "trading" / "RULES-notes.md"
    notes.write_text("- first\n")
    assert run(ws).returncode == 0 and run(ws, "--check").returncode == 0
    notes.write_text("- first\n- second\n")
    p = run(ws, "--check")
    assert p.returncode == 1 and "+- second" in p.stdout
    notes.unlink()
    p = run(ws, "--check")
    assert p.returncode == 1 and "Owner's notes" in p.stdout


def test_raise_rule_is_left_out_when_it_equals_the_first_swap(tmp_path):
    def same(cfg):
        cfg["options"]["stop_market_after_gain"].pop("raise_max_spread_pct")
    ws = make_ws(tmp_path, same)
    assert run(ws).returncode == 0
    text = rules_text(ws)
    assert "Raising a day stop-market" not in text
    assert "spread of **5%**" in text
