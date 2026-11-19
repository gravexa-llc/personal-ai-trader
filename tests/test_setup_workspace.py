"""tools/setup_workspace.py: idempotent workspace creation, the default config, guarded config changes,
the go-live gate and the phase changes /trader-setup makes."""
import glob
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, TOOLS

sys.path.insert(0, TOOLS)
import setup_workspace  # noqa: E402
import trader_config  # noqa: E402

SCRIPT = os.path.join(TOOLS, "setup_workspace.py")
ACCT = "ACCT-TEST-0001"


def run(ws, *args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, SCRIPT, "--workspace", str(ws)] + list(args), capture_output=True,
                          text=True, env=env, cwd=str(ws), timeout=30)


def cfg_path(ws):
    return os.path.join(str(ws), "trading", "trader.json")


def load(ws):
    with open(cfg_path(ws)) as f:
        return json.load(f)


def configure(ws, start="5000", number=ACCT, *extra):
    return run(ws, "config", "--account-number", number, "--nickname", "Trading", "--owner", "Sam",
               "--start-balance", start, "--timezone", "America/Chicago", "--today", "2026-11-02", *extra)


# ---------------------------------------------------------------- init

def test_init_creates_the_tree_and_never_overwrites(tmp_path):
    p = run(tmp_path, "init")
    assert p.returncode == 0, p.stderr
    t = tmp_path / "trading"
    for d in ("journal", "reviews", "shadow", "ledger", "logs"):
        assert (t / d).is_dir()
    for f in ("PLAN.md", "ROBINHOOD-SOURCES.md", "NEWS-SOURCES.md", "shadow/README.md", "shadow/stop25.md"):
        assert (t / f).is_file(), f
    assert (t / "PLAN.md").read_text() == open(os.path.join(ROOT, "templates", "PLAN.md")).read()
    notes = (t / "RULES-notes.md").read_text()
    assert notes == setup_workspace.NOTES_HEADER
    # The owner's edits survive a re-run.
    (t / "PLAN.md").write_text("mine\n")
    (t / "RULES-notes.md").write_text("my notes\n")
    p = run(tmp_path, "init")
    assert p.returncode == 0 and "created" not in p.stdout
    assert (t / "PLAN.md").read_text() == "mine\n"
    assert (t / "RULES-notes.md").read_text() == "my notes\n"
    assert not (t / "trader.json").exists() and not (t / "STRATEGY.md").exists()


def test_strategy_sample_is_copied_once(tmp_path):
    assert run(tmp_path, "strategy-sample").returncode == 0
    path = tmp_path / "trading" / "STRATEGY.md"
    assert path.read_text().startswith("> **SAMPLE")
    path.write_text("my strategy\n")
    assert run(tmp_path, "strategy-sample").returncode == 0
    assert path.read_text() == "my strategy\n"


# ---------------------------------------------------------------- config

def test_default_config_is_scaled_and_valid(tmp_path):
    p = configure(tmp_path, "12000")
    assert p.returncode == 0, p.stderr
    cfg = load(tmp_path)
    assert cfg["phase"] == "DRY_RUN" and cfg["owner_name"] == "Sam" and cfg["timezone"] == "America/Chicago"
    assert cfg["account"] == {"number": ACCT, "nickname": "Trading", "start_balance": 12000}
    assert cfg["limits"]["option_cap_dollars"] == 2400
    assert cfg["limits"]["share_cap_dollars"] == 3600
    assert cfg["limits"]["halt_account_value"] == 8400
    assert cfg["limits"]["cash_floor_pct"] == 30 and cfg["limits"]["max_open_positions"] == 5
    assert cfg["rules_set_on"] == "2026-11-02"
    assert cfg["go_live"] == {"paper_days_required": 5, "extensions": 0, "met": False, "approved_at": None}
    assert cfg["watchlist"] == {"name": "Trader", "id": None}
    loaded = trader_config.load(cfg_path(tmp_path))
    assert loaded.warnings == []


def test_example_numbers_come_back_for_the_example_balance(tmp_path):
    assert configure(tmp_path, "5000").returncode == 0
    cfg = load(tmp_path)
    with open(os.path.join(ROOT, "templates", "trader.example.json")) as f:
        example = json.load(f)
    assert cfg["limits"] == example["limits"]
    assert cfg["options"] == example["options"]


def test_odd_balance_rounds_caps_down(tmp_path):
    assert configure(tmp_path, "1234.56").returncode == 0
    cfg = load(tmp_path)
    assert cfg["account"]["start_balance"] == 1234.56
    assert cfg["limits"]["option_cap_dollars"] == 246 and cfg["limits"]["share_cap_dollars"] == 370
    assert cfg["limits"]["halt_account_value"] == 864


def test_config_is_idempotent_and_protects_another_account(tmp_path):
    assert configure(tmp_path).returncode == 0
    before = open(cfg_path(tmp_path)).read()
    p = configure(tmp_path)
    assert p.returncode == 0 and "left unchanged" in p.stdout
    assert open(cfg_path(tmp_path)).read() == before
    p = configure(tmp_path, "5000", "ACCT-TEST-9999")
    assert p.returncode == 1 and "--force" in p.stderr
    assert open(cfg_path(tmp_path)).read() == before
    p = configure(tmp_path, "5000", "ACCT-TEST-9999", "--force")
    assert p.returncode == 0
    assert load(tmp_path)["account"]["number"] == "ACCT-TEST-9999"
    assert len(glob.glob(cfg_path(tmp_path) + ".bak-*")) == 1


@pytest.mark.parametrize("args,needle", [
    (["--start-balance", "abc"], "number"),
    (["--start-balance", "10"], "at least 50"),
    (["--timezone", "Mars/Olympus"], "unknown time zone"),
    (["--account-number", " 12 3"], "without spaces"),
])
def test_config_rejects_bad_input(tmp_path, args, needle):
    base = {"--account-number": ACCT, "--nickname": "Trading", "--owner": "Sam", "--start-balance": "5000",
            "--timezone": "America/Chicago"}
    for k, v in zip(args[::2], args[1::2]):
        base[k] = v
    flat = [x for kv in base.items() for x in kv]
    p = run(tmp_path, "config", *flat)
    assert p.returncode == 2 and needle in p.stderr
    assert not os.path.exists(cfg_path(tmp_path))


def test_detect_timezone():
    assert setup_workspace.detect_timezone({"TZ": "Europe/Berlin"}) == "Europe/Berlin"
    assert setup_workspace.valid_timezone(setup_workspace.detect_timezone({"TZ": "Not/AZone"}))


def test_config_defaults_to_the_system_zone(tmp_path):
    p = run(tmp_path, "config", "--account-number", ACCT, "--nickname", "A", "--owner", "Sam",
            "--start-balance", "5000", env_extra={"TZ": "Asia/Tokyo"})
    assert p.returncode == 0, p.stderr
    assert load(tmp_path)["timezone"] == "Asia/Tokyo"


# ---------------------------------------------------------------- set

def test_set_shows_a_diff_and_needs_yes(tmp_path):
    assert configure(tmp_path).returncode == 0
    before = load(tmp_path)
    p = run(tmp_path, "set", "limits.option_cap_dollars=800", "limits.max_open_positions=4")
    assert p.returncode == 0 and '-    "option_cap_dollars": 1000' in p.stdout and "--yes" in p.stdout
    assert load(tmp_path) == before
    p = run(tmp_path, "set", "limits.option_cap_dollars=800", "limits.max_open_positions=4", "--yes")
    assert p.returncode == 0
    cfg = load(tmp_path)
    assert cfg["limits"]["option_cap_dollars"] == 800 and cfg["limits"]["max_open_positions"] == 4
    p = run(tmp_path, "set", "limits.option_cap_dollars=800", "--yes")
    assert p.returncode == 0 and "nothing to change" in p.stdout


def test_set_values_types(tmp_path):
    assert configure(tmp_path).returncode == 0
    p = run(tmp_path, "set", "watchlist.id=WL-TEST-0001", "allowed.shares=false", "owner_name=Sam Q",
            "schedule.sunday_review_local=16:07", "schedule.offset_minutes=3", "notifications.push=false", "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    cfg = load(tmp_path)
    assert cfg["watchlist"]["id"] == "WL-TEST-0001" and cfg["allowed"]["shares"] is False
    assert cfg["owner_name"] == "Sam Q" and cfg["schedule"]["sunday_review_local"] == "16:07"
    assert cfg["schedule"]["offset_minutes"] == 3
    assert cfg["notifications"]["push"] is False


@pytest.mark.parametrize("pair,code,needle", [
    ("phase=LIVE", 1, "go-live"),
    ("go_live.met=true", 1, "go-live"),
    ("account.number=X", 1, "config --force"),
    ("schema=2", 1, "plugin"),
    ("limits.option_cap=5", 2, "typo"),
    ("allowed.spreads=true", 2, "typo"),
    ("foo=1", 1, "not a key setup changes"),
    ("limits.option_cap_dollars=-5", 1, "invalid"),
    ("limits.cash_floor_pct=100", 1, "invalid"),
    ("options.expiry_days_min=90", 1, "invalid"),
])
def test_set_refuses(tmp_path, pair, code, needle):
    assert configure(tmp_path).returncode == 0
    before = open(cfg_path(tmp_path)).read()
    p = run(tmp_path, "set", pair, "--yes")
    assert p.returncode == code, p.stdout + p.stderr
    assert needle in p.stdout + p.stderr
    assert open(cfg_path(tmp_path)).read() == before


def test_set_prints_sanity_warnings(tmp_path):
    assert configure(tmp_path).returncode == 0
    p = run(tmp_path, "set", "limits.option_cap_dollars=2000", "limits.max_same_theme=9",
            "limits.halt_account_value=5000")
    out = p.stdout
    assert p.returncode == 0
    assert "more than 30% of the start balance" in out
    assert "not below the start balance" in out
    assert "never binds" in out


def test_set_without_config(tmp_path):
    p = run(tmp_path, "set", "limits.option_cap_dollars=800")
    assert p.returncode == 2 and "config" in p.stderr


# ---------------------------------------------------------------- notes

def test_notes_append_once(tmp_path):
    p = run(tmp_path, "notes", "--add", "approval switch left off by the owner's choice", "--today", "2026-11-02")
    assert p.returncode == 0
    path = tmp_path / "trading" / "RULES-notes.md"
    text = path.read_text()
    assert text.startswith(setup_workspace.NOTES_HEADER)
    assert text.endswith("- 2026-11-02: approval switch left off by the owner's choice\n")
    run(tmp_path, "notes", "--add", "approval switch left off by the owner's choice", "--today", "2026-11-02")
    assert path.read_text() == text


# ---------------------------------------------------------------- go-live gate

def paper_week(ws, days=("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"), review="2026-11-08"):
    j = os.path.join(str(ws), "trading", "journal")
    r = os.path.join(str(ws), "trading", "reviews")
    os.makedirs(j, exist_ok=True)
    os.makedirs(r, exist_ok=True)
    for d in days:
        with open(os.path.join(j, d + ".md"), "w") as f:
            f.write("# Journal\n\n## 06:05 prep\n- x\n\n## 13:16 wrap\n- Day: none\n")
    if review:
        with open(os.path.join(r, review + ".md"), "w") as f:
            f.write("# Review\n")


def gate(ws):
    p = run(ws, "gate")
    return p.returncode, json.loads(p.stdout)


def test_gate_fails_on_a_fresh_install(tmp_path):
    assert configure(tmp_path).returncode == 0
    code, g = gate(tmp_path)
    assert code == 1 and not g["pass"] and g["paper_days"] == 0
    assert any("paper trading day" in x for x in g["problems"])
    assert any("Sunday review" in x for x in g["problems"])


def test_gate_counts_wrap_days_and_needs_a_sunday_review(tmp_path):
    assert configure(tmp_path).returncode == 0
    paper_week(tmp_path, review=None)
    # A journal day without a wrap does not count; a mid-week review is not the Sunday review.
    with open(os.path.join(str(tmp_path), "trading", "journal", "2026-11-09.md"), "w") as f:
        f.write("## 06:05 prep\n")
    with open(os.path.join(str(tmp_path), "trading", "reviews", "2026-11-04-midweek.md"), "w") as f:
        f.write("x")
    # A Sunday review before the first paper day does not count either.
    with open(os.path.join(str(tmp_path), "trading", "reviews", "2026-11-01.md"), "w") as f:
        f.write("x")
    code, g = gate(tmp_path)
    assert code == 1 and g["paper_days"] == 5 and g["sunday_reviews"] == []
    with open(os.path.join(str(tmp_path), "trading", "reviews", "2026-11-08.md"), "w") as f:
        f.write("x")
    code, g = gate(tmp_path)
    assert code == 0 and g["pass"] and g["sunday_reviews"] == ["2026-11-08"]
    assert len(g["not_checked_here"]) == 3


def test_gate_needs_rules_set_on_and_enough_days(tmp_path):
    assert configure(tmp_path).returncode == 0
    paper_week(tmp_path, days=("2026-11-02", "2026-11-03"))
    code, g = gate(tmp_path)
    assert code == 1 and "2 paper trading day(s) with a wrap entry, 5 required" in g["problems"]
    cfg = load(tmp_path)
    cfg["rules_set_on"] = None
    cfg["go_live"]["paper_days_required"] = 2
    json.dump(cfg, open(cfg_path(tmp_path), "w"))
    code, g = gate(tmp_path)
    assert code == 1 and g["problems"] == [
        "rules_set_on is not recorded: the owner has not set the limits (run --limits)"]


def test_go_live_needs_the_word_go_and_the_gate(tmp_path):
    assert configure(tmp_path).returncode == 0
    p = run(tmp_path, "go-live", "--confirm", "yes")
    assert p.returncode == 1 and '"go"' in p.stderr
    p = run(tmp_path, "go-live", "--confirm", "go")
    assert p.returncode == 1 and "not met" in p.stdout
    assert load(tmp_path)["phase"] == "DRY_RUN"
    paper_week(tmp_path)
    p = run(tmp_path, "go-live", "--confirm", "go", "--today", "2026-11-08")
    assert p.returncode == 0, p.stdout + p.stderr
    cfg = load(tmp_path)
    assert cfg["phase"] == "LIVE" and cfg["go_live"]["met"] is True and cfg["go_live"]["approved_at"] == "2026-11-08"
    assert trader_config.load(cfg_path(tmp_path)).phase == "LIVE"
    # Not twice.
    p = run(tmp_path, "go-live", "--confirm", "go")
    assert p.returncode == 1


# ---------------------------------------------------------------- leaving HALTED

def halted(ws, met):
    assert configure(ws).returncode == 0
    cfg = load(ws)
    cfg["phase"] = "HALTED"
    cfg["go_live"]["met"] = met
    json.dump(cfg, open(cfg_path(ws), "w"))


def test_resume_from_halt_to_dry_run(tmp_path):
    halted(tmp_path, met=False)
    p = run(tmp_path, "phase", "--from", "HALTED", "--to", "LIVE", "--confirm", "yes")
    assert p.returncode == 1 and "go_live.met is false" in p.stderr
    p = run(tmp_path, "phase", "--from", "HALTED", "--to", "DRY_RUN", "--confirm", "no")
    assert p.returncode == 1
    assert load(tmp_path)["phase"] == "HALTED"
    p = run(tmp_path, "phase", "--from", "HALTED", "--to", "DRY_RUN", "--confirm", "yes")
    assert p.returncode == 0 and load(tmp_path)["phase"] == "DRY_RUN"
    p = run(tmp_path, "phase", "--from", "HALTED", "--to", "DRY_RUN", "--confirm", "yes")
    assert p.returncode == 1 and "not HALTED" in p.stderr


def test_resume_from_halt_to_live(tmp_path):
    halted(tmp_path, met=True)
    p = run(tmp_path, "phase", "--from", "HALTED", "--to", "LIVE", "--confirm", "yes")
    assert p.returncode == 0 and load(tmp_path)["phase"] == "LIVE"


@pytest.mark.parametrize("src,dst", [("DRY_RUN", "LIVE"), ("LIVE", "DRY_RUN"), ("HALTED", "HALTED")])
def test_phase_refuses_other_moves(tmp_path, src, dst):
    assert configure(tmp_path).returncode == 0
    p = run(tmp_path, "phase", "--from", src, "--to", dst, "--confirm", "yes")
    assert p.returncode == 2


# ---------------------------------------------------------------- halt (the check routine's, allowed unattended)

def set_phase(ws, phase, met=None):
    cfg = load(ws)
    cfg["phase"] = phase
    if met is not None:
        cfg["go_live"]["met"] = met
        cfg["go_live"]["approved_at"] = "2026-11-09" if met else None
    json.dump(cfg, open(cfg_path(ws), "w"), indent=2)
    return cfg


def journal(ws, day="2026-11-10"):
    path = os.path.join(str(ws), "trading", "journal", day + ".md")
    return open(path).read() if os.path.exists(path) else ""


@pytest.mark.parametrize("phase,met", [("DRY_RUN", False), ("LIVE", True)])
def test_halt_moves_to_halted_and_changes_nothing_else(tmp_path, phase, met):
    assert configure(tmp_path).returncode == 0
    before = set_phase(tmp_path, phase, met)
    p = run(tmp_path, "halt", "--reason", "account value 3480  at or below\nthe halt level", "--today", "2026-11-10")
    assert p.returncode == 0, p.stdout + p.stderr
    after = load(tmp_path)
    assert after["phase"] == "HALTED"
    assert {k: v for k, v in after.items() if k != "phase"} == {k: v for k, v in before.items() if k != "phase"}
    assert trader_config.load(cfg_path(tmp_path)).phase == "HALTED"
    j = journal(tmp_path)
    assert j.count("HALT: phase %s -> HALTED" % phase) == 1
    assert "Reason: account value 3480 at or below the halt level" in j
    assert len(glob.glob(cfg_path(tmp_path) + ".bak-*")) == 1


def test_halt_is_idempotent(tmp_path):
    assert configure(tmp_path).returncode == 0
    assert run(tmp_path, "halt", "--reason", "x", "--today", "2026-11-10").returncode == 0
    text, j = open(cfg_path(tmp_path)).read(), journal(tmp_path)
    p = run(tmp_path, "halt", "--reason", "x", "--today", "2026-11-10")
    assert p.returncode == 0 and "already HALTED" in p.stdout
    assert open(cfg_path(tmp_path)).read() == text and journal(tmp_path) == j
    assert len(glob.glob(cfg_path(tmp_path) + ".bak-*")) == 1


def test_halt_cannot_unhalt_set_live_or_change_limits(tmp_path):
    assert configure(tmp_path).returncode == 0
    set_phase(tmp_path, "HALTED", met=True)
    text = open(cfg_path(tmp_path)).read()
    # From HALTED it does nothing at all; there is no option to choose a target phase or touch a limit.
    assert run(tmp_path, "halt", "--reason", "x").returncode == 0
    for extra in (["--to", "LIVE"], ["--to", "DRY_RUN"], ["--phase", "LIVE"], ["limits.option_cap_dollars=99999"],
                  ["--yes"], ["--workspace", str(tmp_path)]):
        p = run(tmp_path, "halt", "--reason", "x", *extra)
        assert p.returncode == 2, extra
    assert open(cfg_path(tmp_path)).read() == text
    assert load(tmp_path)["phase"] == "HALTED"


@pytest.mark.parametrize("reason", ["", "   ", "x" * 301])
def test_halt_needs_a_reason(tmp_path, reason):
    assert configure(tmp_path).returncode == 0
    p = run(tmp_path, "halt", "--reason", reason)
    assert p.returncode == 2 and load(tmp_path)["phase"] == "DRY_RUN"


def test_halt_refuses_an_unknown_phase_and_a_missing_config(tmp_path):
    p = run(tmp_path, "halt", "--reason", "x")
    assert p.returncode == 2
    assert configure(tmp_path).returncode == 0
    set_phase(tmp_path, "PAPER")
    p = run(tmp_path, "halt", "--reason", "x")
    assert p.returncode == 1 and load(tmp_path)["phase"] == "PAPER"


def test_set_and_phase_still_cannot_reach_what_halt_refuses(tmp_path):
    assert configure(tmp_path).returncode == 0
    assert run(tmp_path, "set", "phase=HALTED", "--yes").returncode == 1
    assert run(tmp_path, "set", "go_live.extensions=3", "--yes").returncode == 1


# ---------------------------------------------------------------- extend-paper (on the owner's answer)

def test_extend_paper_adds_one_week_and_nothing_else(tmp_path):
    assert configure(tmp_path).returncode == 0
    before = load(tmp_path)
    p = run(tmp_path, "extend-paper", "--expect", "0")
    assert p.returncode == 0, p.stdout + p.stderr
    after = load(tmp_path)
    assert after["go_live"]["extensions"] == 1
    before["go_live"]["extensions"] = 1
    assert after == before
    # A repeat with the old value refuses: one answer, one week.
    p = run(tmp_path, "extend-paper", "--expect", "0")
    assert p.returncode == 1 and "already extended" in p.stderr
    assert load(tmp_path)["go_live"]["extensions"] == 1
    assert run(tmp_path, "extend-paper", "--expect", "1").returncode == 0
    assert load(tmp_path)["go_live"]["extensions"] == 2


@pytest.mark.parametrize("phase,met", [("LIVE", True), ("HALTED", False), ("HALTED", True)])
def test_extend_paper_only_in_the_paper_period(tmp_path, phase, met):
    assert configure(tmp_path).returncode == 0
    set_phase(tmp_path, phase, met)
    text = open(cfg_path(tmp_path)).read()
    p = run(tmp_path, "extend-paper", "--expect", "0")
    assert p.returncode == 1
    assert open(cfg_path(tmp_path)).read() == text


def test_extend_paper_takes_no_other_change(tmp_path):
    assert configure(tmp_path).returncode == 0
    for extra in (["--to", "LIVE"], ["phase=LIVE"], ["--yes"]):
        assert run(tmp_path, "extend-paper", "--expect", "0", *extra).returncode == 2, extra
    assert run(tmp_path, "extend-paper").returncode == 2
    assert load(tmp_path)["go_live"]["extensions"] == 0
