"""Lint skills/trader-setup/SKILL.md against its contract.

The generic checks (frontmatter, personal data, clock times, $<digit>, contracted paths, plugin-root paths)
run on every skill in test_skill_files.py; these are the setup-specific promises.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILL = os.path.join(ROOT, "skills", "trader-setup", "SKILL.md")


def text():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def frontmatter():
    m = re.match(r"\A---\n(.*?)\n---\n", text(), re.S)
    assert m
    return m.group(1)


def section(heading_start):
    """The text from a '## <heading_start>' heading to the next '## ' heading."""
    body = text()
    m = re.search(r"^## %s.*?(?=^## |\Z)" % re.escape(heading_start), body, re.S | re.M)
    assert m, "no section starting with ## %s" % heading_start
    return m.group(0)


def test_only_the_owner_starts_it():
    fm = frontmatter()
    assert re.search(r"^disable-model-invocation: true$", fm, re.M)
    assert "argument-hint:" in fm
    for mode in ("--limits", "--go-live", "--resume-from-halt", "--dry-run"):
        assert mode in fm and mode in text()


@pytest.mark.parametrize("n,title", [
    (1, "Prerequisites"), (2, "Connect Robinhood"), (3, "Pick the account"), (4, "Approval switch"),
    (5, "Limits interview"), (6, "Strategy"), (7, "Watchlist"), (8, "Permissions"), (9, "Guard proof"),
    (10, "Schedules"), (11, "Start DRY_RUN"),
])
def test_steps_in_order(n, title):
    assert re.search(r"^## %d\. %s" % (n, re.escape(title)), text(), re.M), "step %d %s" % (n, title)
    positions = [text().index("## %d. " % i) for i in range(1, 12)]
    assert positions == sorted(positions)


def test_prerequisites_and_warning():
    s = section("1. ")
    assert "2.1.295" in s and "onFailure: block" in s and "python3 --version" in s
    assert "**I understand — continue**" in s and "**Stop, write nothing**" in s
    for promise in ("never do", "Real money", "not advice", "owner owns it"):
        assert promise.lower() in s.lower(), promise
    assert "trading/.venv" in s and "openpyxl" in s


def test_connect_never_sees_credentials():
    s = section("2. ")
    assert "/mcp" in s and "get_accounts" in s
    assert "never sees the password or the token" in s


def test_account_choice_writes_the_default_config_at_once():
    s = section("3. ")
    for word in ("agentic_allowed", "option level", "level 2", "afford to lose"):
        assert word in s, word
    assert "setup_workspace.py" in text() and "config --account-number" in s
    assert "templates/trader.example.json" in s and "rules_set_on" in s
    assert "20%" in s and "30%" in s
    assert "trader_config.py\" --check" in s


def test_approval_switch_on_by_default():
    s = section("4. ")
    assert "get_trade_approval_setting" in s
    assert "turn it on" in s and "read it again" in s
    assert "explicitly" in s and "notes --add" in s
    assert "protective stop" in s and "no resting exit" in s


def test_limits_interview():
    s = section("5. ")
    for key in ("start_balance", "option_cap_dollars", "share_cap_dollars", "cash_floor_pct",
                "daily_loss_stop_pct", "halt_account_value", "max_open_positions", "max_same_theme",
                "long_options", "get_portfolio", "render_rules.py", "--yes", "rules_set_on"):
        assert key in s, key


def test_watchlist_dry_run_prints_instead_of_creating():
    s = section("7. ")
    assert "create_watchlist" in s and "get_watchlists" in s and "watchlist.id" in s
    assert re.search(r"--dry-run.*do not call `create_watchlist`", s, re.S)


def test_permissions_need_the_owners_yes():
    s = section("8. ")
    assert "setup_settings.py" in s and "--yes" in s and "--check" in s
    assert "explicit yes" in s
    for word in ("crypto", "exercise", "both tool namings", "order guard", "trader.json"):
        assert word in s, word


def test_guard_proof_uses_previews_only():
    s = section("9. ")
    assert "review_option_order" in s and "exactly one leg" in s and "place nothing" in s
    assert "place_" not in s
    assert "hook is not firing" in s and "Stop setup" in s


def test_schedules_come_from_schedule_py_without_duplicates():
    s = section("10. ")
    assert "CronList" in s and "CronCreate" in s and "recurring: false" in s and "verbatim" in s
    for flag in ("--day today", "--next-trading-day", "--sunday-review"):
        assert flag in s
    assert "docs/KEEP-RUNNING.md" in s
    assert "duplicate" in s


def test_start_dry_run():
    s = section("11. ")
    for word in ("ledger.py\" init", "STATE.md.tmpl", "DRY_RUN", "go-live gate", "journal", "what happens tomorrow"):
        assert word.lower() in s.lower(), word


def test_go_live_needs_the_gate_and_the_word():
    s = section("Go live")
    for word in ("gate", "get_portfolio", "get_trade_approval_setting", "step 9", "setup_settings.py",
                 "**go**", "go-live --confirm go", "push"):
        assert word in s, word
    assert "exactly \"go\"" in s


def test_resume_from_halt_is_the_owners_choice():
    s = section("Leaving HALTED")
    assert "phase --from HALTED" in s and "explicit choice" in s and "go_live.met" in s
    assert "halts again" in s


def test_setup_never_places_orders():
    body = text()
    assert "No order is ever placed by setup" in body
    for tool in ("place_option_order", "place_equity_order", "place_advanced_order", "cancel_option_order"):
        assert tool not in body, tool


# ---- every decision is a question dialog (a product requirement)

DIALOG = "**[Dialog]**"


def test_dialog_rule_is_stated():
    head = text().split("## 1. ")[0]
    assert "AskUserQuestion" in head
    assert "one decision per question" in head
    assert "never a line at the end of a message" in head


@pytest.mark.parametrize("heading,minimum", [
    ("1. ", 2),   # folder choice, the risk acknowledgement
    ("3. ", 3),   # account, name/balance/time zone, replace an existing config
    ("4. ", 1),   # approval switch
    ("5. ", 4),   # balance mismatch, keep or change limits, write, "are these your rules"
    ("6. ", 1),   # strategy
    ("7. ", 2),   # existing list, name
    ("8. ", 2),   # web search, write the rules
    ("11. ", 1),  # open items at the end
    ("Leaving HALTED", 1),
])
def test_each_decision_step_uses_a_dialog(heading, minimum):
    assert section(heading).count(DIALOG) >= minimum, heading


def test_no_decision_is_asked_as_plain_prose():
    """A step that says 'ask the owner / ask whether / ask which' must carry the dialog marker in the same
    numbered item; the two typed answers (the /mcp sign-in and the go-live word) are the only exceptions."""
    for line in text().splitlines():
        if not re.search(r"\b[Aa]sk(s)? (the owner|them|whether|which|about|what|one question)", line):
            continue
        if DIALOG in line:
            continue
        allowed = ("`/mcp`", "type **go**", "ask the owner to update Claude Code", "repeat the `/mcp` step",
                   "asks the owner first, even in an unattended run", "will ask them to re-run",
                   "Never ask for", "which asks the owner", "keeps asking", "keep asking")
        assert any(a in line for a in allowed), line[:160]


def test_go_live_word_stays_typed():
    s = section("Go live")
    assert "type **go**" in s and "not a dialog" in s
    assert DIALOG not in s


def test_strategy_and_rules_questions_are_separate_dialogs():
    assert "This dialog holds this one question only" in section("5. ")
    assert "its own dialog" in section("6. ")
    assert "never combine it with a question from another step" in section("4. ")
