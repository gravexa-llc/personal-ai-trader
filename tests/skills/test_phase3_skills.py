"""Lint the phase 3 skills (trader-strategy, trader-doctor, trader-status), the user docs and the optional
supervisor templates. The generic checks (frontmatter, personal data in skills, clock times, contracted
paths) are in test_skill_files.py; these are the promises specific to each file."""
import os
import plistlib
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_skill_files import ROOT, personal_hits, read, split_frontmatter  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import doctor  # noqa: E402

SKILLS = os.path.join(ROOT, "skills")
EXTRAS = os.path.join(ROOT, "extras", "supervisor")
SAMPLE = os.path.join(ROOT, "templates", "STRATEGY.sample.md")


def body(name):
    return split_frontmatter(read(os.path.join(SKILLS, name, "SKILL.md")))[1]


def lines_with(text, pattern):
    return [l for l in text.splitlines() if re.search(pattern, l)]


NEGATION = re.compile(r"(?i)\b(never|no|not|nothing)\b")


# ---------------------------------------------------------------- shared: read-only skills

@pytest.mark.parametrize("name", ["trader-doctor", "trader-status"])
def test_read_only_skills_only_mention_writes_to_forbid_them(name):
    """Every line naming an order-changing tool must say it is not to be used."""
    for line in lines_with(body(name), r"place_|cancel_|replace_|decline_trade_approval"):
        assert NEGATION.search(line), "%s mentions an order tool without forbidding it: %r" % (name, line)


@pytest.mark.parametrize("name", ["trader-doctor", "trader-status", "trader-strategy"])
def test_skills_mask_or_avoid_the_account_number(name):
    text = body(name)
    if name == "trader-strategy":
        assert "places no orders" in text.lower()
    else:
        assert "last 4" in text


# ---------------------------------------------------------------- trader-doctor

def test_doctor_runs_the_script_with_the_plugin_root():
    text = body("trader-doctor")
    assert '"${CLAUDE_PLUGIN_ROOT}/tools/doctor.py" --format json' in text
    assert "--tool-names -" in text and "--bundle --skill-results -" in text


def test_doctor_covers_every_design_area():
    text = body("trader-doctor")
    for area in ("Robinhood connection", "Account", "Approval switch", "Config", "Order guard", "Deny list",
                 "Python", "Schedules", "State", "Reconcile", "Watcher", "Files", "Version", "Calendar"):
        assert area in text, area


def test_doctor_names_every_script_check():
    text = body("trader-doctor")
    for cid in doctor.CHECK_IDS:
        assert "`%s`" % cid in text, cid


def test_doctor_guard_proof_is_by_preview_and_both_directions():
    text = body("trader-doctor")
    assert "review_option_order" in text
    assert "two legs" in text and "must be BLOCKED" in text
    assert "must pass the guard" in text
    assert "exactly one leg" in text
    assert "Never send the `place_*` version" in text


def test_doctor_checks_the_robinhood_server_name():
    text = body("trader-doctor")
    assert "mcp__plugin_personal-ai-trader_robinhood-trading__" in text and "mcp__robinhood-trading__" in text
    assert "recognised server name" in text


def test_doctor_only_writes_schedules_after_a_yes():
    text = body("trader-doctor")
    creates = lines_with(text, r"CronCreate")
    assert creates, "the doctor should describe re-creating missing schedules"
    for line in creates:
        assert "yes" in line.lower() or "only" in line.lower(), line
    assert "recurring: false" in text
    assert "verbatim" in text


def test_doctor_reconciles_and_requires_resting_exits():
    text = body("trader-doctor")
    assert "get_option_positions" in text and "get_equity_positions" in text
    assert "resting exit" in text and "waiting for approval" in text
    assert "top of the report" in text


def test_doctor_names_setup_step_8_for_settings_drift():
    assert "step 8 of `/personal-ai-trader:trader-setup`" in body("trader-doctor")


def test_doctor_quick_argument_documented():
    fields, text = split_frontmatter(read(os.path.join(SKILLS, "trader-doctor", "SKILL.md")))
    assert "quick" in fields.get("argument-hint", "")
    assert "$ARGUMENTS" in text


# ---------------------------------------------------------------- trader-strategy

def test_strategy_follows_the_sample_format():
    text = body("trader-strategy")
    assert "templates/STRATEGY.sample.md" in text
    for section in doctor.STRATEGY_SECTIONS:
        assert section.lstrip("# ") in text, section


def test_strategy_setups_have_every_testable_part():
    text = body("trader-strategy")
    for part in ("**Trigger**", "**Thesis**", "**Entry**", "**Exit**", "**Contract**", "**Stay out**"):
        assert part in text, part
    for tool in ("get_equity_technical_indicators", "get_equity_historicals", "get_earnings_calendar"):
        assert tool in text


def test_strategy_reads_floors_from_config_and_never_restates_numbers():
    text = body("trader-strategy")
    for key in ("options.expiry_days_min", "options.expiry_days_max", "options.min_open_interest",
                "options.max_spread_pct_of_mid", "limits.option_cap_dollars", "limits.share_cap_dollars",
                "limits.cash_floor_pct", "options.default_stop_pct"):
        assert key in text, key
    assert not re.search(r"\d\s*%", text), "a percentage is written as a number in trader-strategy"
    assert not re.search(r"\\?\$\d", text), "a dollar amount is written as a number in trader-strategy"


def test_strategy_keeps_old_versions_and_never_overwrites():
    text = body("trader-strategy")
    assert "trading/reviews/STRATEGY-v<N>-<YYYY-MM-DD>.md" in text
    assert "never overwrite" in text


def test_strategy_confirms_before_writing():
    text = body("trader-strategy")
    assert "Write nothing until they say yes" in text
    assert "whole draft" in text


def test_strategy_sample_is_not_advice():
    text = body("trader-strategy")
    assert "not a recommendation" in text
    assert "explain-sample" in text
    assert "does not give advice" in text


def test_strategy_states_product_exclusions():
    text = body("trader-strategy")
    for word in ("Spreads", "selling options to open", "short selling", "crypto", "exercise"):
        assert word in text, word


def test_strategy_validates_its_output_with_the_doctor():
    assert '"${CLAUDE_PLUGIN_ROOT}/tools/doctor.py" --format table --only strategy' in body("trader-strategy")


def test_sample_numbered_setup_shape_matches_what_the_strategy_skill_writes():
    """The doctor counts setups by '<n>. **Name.**'; the skill writes that shape; the sample has it."""
    assert "`<n>. **<Name> (<calls|puts|shares>).** <one-line summary>`" in body("trader-strategy")
    with open(SAMPLE, encoding="utf-8") as f:
        assert doctor.strategy_problems(f.read()) == []
    example = "## Setups in play\n1. **Gap fade (puts).** Example.\n   - **Trigger** x\n"
    assert re.match(r"^\d+\.\s+\*\*", example.splitlines()[1])


# ---------------------------------------------------------------- trader-status

def test_status_shows_everything_it_promises():
    text = body("trader-status")
    for item in ("Phase", "Account", "Open positions", "Pending approvals", "Today", "Next scheduled run",
                 "Record so far", "resting exit"):
        assert item in text, item
    assert "at the **bid**" in text


def test_status_writes_nothing():
    text = body("trader-status")
    assert "Read-only" in text
    for line in lines_with(text, r"CronCreate|CronDelete|ledger\.py\"? (?:equity|open|close|update|order|build)"):
        assert NEGATION.search(line), line
    assert 'trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" show all' in text


# ---------------------------------------------------------------- docs

DOCS = ["README.md", "docs/KEEP-RUNNING.md", "docs/CHANGELOG.md", "docs/SAFETY.md"]


@pytest.mark.parametrize("rel", DOCS + ["extras/supervisor/trader-session.command",
                                        "extras/supervisor/trader-supervisor.sh",
                                        "extras/supervisor/trader-supervisor.plist.template"])
def test_no_personal_data_in_docs_and_extras(rel):
    found = personal_hits(read(os.path.join(ROOT, rel)))
    assert not found, "%s contains personal data: %s" % (rel, found)


def test_readme_sections():
    text = read(os.path.join(ROOT, "README.md"))
    for heading in ("## Read this first: the risks, in plain words", "## Install", "## Daily life", "## Commands",
                    "## Uninstall"):
        assert heading in text, heading
    assert "/personal-ai-trader:trader-setup" in text
    assert "/plugin marketplace add" in text and "--plugin-dir" in text
    for skill in sorted(os.listdir(SKILLS)):
        if os.path.isfile(os.path.join(SKILLS, skill, "SKILL.md")):
            assert "/personal-ai-trader:%s" % skill in text, skill


def test_keep_running_has_both_options_and_the_warning():
    text = read(os.path.join(ROOT, "docs", "KEEP-RUNNING.md"))
    assert "Option 1 (simple)" in text and "Option 2 (advanced, macOS only)" in text
    assert "never installs it" in text
    assert "must find *its own* session by a unique name" in text
    assert "claude -r" in text and "resume checklist" in text
    for f in os.listdir(EXTRAS):
        assert f in text, f


def test_changelog_lists_every_part_with_safety_impact():
    text = read(os.path.join(ROOT, "docs", "CHANGELOG.md"))
    assert "## 0.1.0" in text
    for part in ("### The plugin skeleton and the order guard", "### Routines, tools and templates",
                 "### Setup, strategy, doctor and status"):
        assert part in text
    assert "Unreleased" not in text
    sections = re.split(r"(?m)^### ", text)[1:]
    for section in sections:
        items = re.findall(r"(?m)^- \*\*", section)
        impacts = section.count("Safety impact")
        assert items and impacts == len(items), "every item needs a safety-impact note: %s" % section[:40]


# ---------------------------------------------------------------- supervisor templates

def filled(text):
    """Replace every <placeholder> (it starts with a letter and stays on one line) with a fake value."""
    return re.sub(r"<(?!(?:plist|dict|key|string|array|integer|true|false)\b)[A-Za-z][^<>\n]*>", "/fake/value", text)


@pytest.mark.parametrize("name", ["trader-session.command", "trader-supervisor.sh"])
def test_supervisor_scripts_parse_once_filled_in(name, tmp_path):
    text = read(os.path.join(EXTRAS, name))
    assert "<" in text and ">" in text, "templates carry <placeholders>"
    path = tmp_path / name
    path.write_text(filled(text))
    zsh = "/bin/zsh"
    if not os.path.exists(zsh):
        pytest.skip("zsh not available")
    p = subprocess.run([zsh, "-n", str(path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("name", ["trader-session.command", "trader-supervisor.sh"])
def test_supervisor_scripts_look_for_their_own_unique_session(name):
    text = read(os.path.join(EXTRAS, name))
    assert 'pgrep -a -u "$(id -u)" -f "$ALIVE_PATTERN"' in text
    assert "--remote-control ${SESSION_NAME}\\$" in text, "the name must be anchored at the end"
    assert "unique" in text


def test_session_launcher_resumes_with_a_startup_prompt():
    text = read(os.path.join(EXTRAS, "trader-session.command"))
    assert 'exec claude -r "$sid" "$STARTUP_PROMPT"' in text
    assert "trading/STATE.md" in text and "restarted or resumed" in text
    assert text.rstrip().splitlines()[-2].rstrip().endswith('--remote-control "$SESSION_NAME"')


def test_supervisor_warning_about_other_sessions():
    text = read(os.path.join(EXTRAS, "trader-supervisor.sh"))
    assert "never mistake another Claude session" in text


def test_plist_template_is_valid_once_filled_in():
    text = read(os.path.join(EXTRAS, "trader-supervisor.plist.template"))
    data = plistlib.loads(filled(text).encode())
    assert data["StartInterval"] == 180 and data["RunAtLoad"] is True
    assert data["ProgramArguments"][0] == "/bin/zsh"


def test_plugin_never_installs_the_supervisor():
    """No skill, tool or hook refers to the supervisor templates or to launchctl."""
    for base in ("skills", "tools", "hooks"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, base)):
            for f in files:
                if f.endswith((".md", ".py", ".json")):
                    text = read(os.path.join(dirpath, f))
                    assert "extras/supervisor" not in text and "launchctl" not in text, os.path.join(dirpath, f)


# ---- trader-strategy asks through dialogs, multi-select where several answers apply (owner requirement)

def test_strategy_questions_are_dialogs_with_multi_select():
    text = body("trader-strategy")
    head = text.split("## 1. ")[0]
    assert "AskUserQuestion" in head and "multiSelect: true" in head
    assert "could be mistaken for an answer" in head
    assert text.count("**[Dialog]**") >= 10
    questions = text.split("## 3. The questions")[1].split("## 4. ")[0]
    for n in range(1, 8):
        item = re.search(r"^%d\. .*?(?=^\d\. |\Z)" % n, questions, re.S | re.M).group(0)
        assert "**[Dialog]**" in item, "question %d has no dialog" % n
    assert questions.count("multi-select") >= 5
    for section_start in ("## 4. ", "## 6. "):
        part = text.split(section_start)[1].split("\n## ")[0]
        assert "**[Dialog]**" in part, section_start


def test_strategy_single_stocks_go_straight_to_a_dialog():
    text = body("trader-strategy")
    assert '"How should single stocks get onto the screen?"' in text
    for option in ("**No fixed list**", "**I'll name some**", "**Drop single stocks**"):
        assert option in text
    assert "do **not** ask for symbols as free text first" in text
