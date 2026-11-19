"""tools/setup_settings.py: writes and merges the workspace .claude/settings.json, idempotently."""
import glob
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import PLAIN_PREFIX, PLUGIN_PREFIX, ROOT, TOOLS

sys.path.insert(0, TOOLS)
import order_guard  # noqa: E402
import setup_settings  # noqa: E402

SCRIPT = os.path.join(TOOLS, "setup_settings.py")
SKILLS = os.path.join(ROOT, "skills")


def run(ws, *args):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([sys.executable, SCRIPT, "--workspace", str(ws)] + list(args), capture_output=True,
                          text=True, env=env, cwd=str(ws), timeout=30)


def settings_path(ws):
    return os.path.join(str(ws), ".claude", "settings.json")


def read(ws):
    with open(settings_path(ws)) as f:
        return json.load(f)


def write(ws, data):
    os.makedirs(os.path.join(str(ws), ".claude"), exist_ok=True)
    with open(settings_path(ws), "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, indent=2)


def want(ws, **kw):
    return setup_settings.wanted(ROOT, str(ws), **kw)


def test_without_yes_writes_nothing(tmp_path):
    p = run(tmp_path)
    assert p.returncode == 0, p.stderr
    assert "settings.json (new file)" in p.stdout and "--yes" in p.stdout
    assert not os.path.exists(settings_path(tmp_path))


def test_fresh_write_has_every_rule(tmp_path):
    p = run(tmp_path, "--yes")
    assert p.returncode == 0, p.stderr
    perms = read(tmp_path)["permissions"]
    for tool in order_guard.NEVER:
        for prefix in (PLUGIN_PREFIX, PLAIN_PREFIX):
            assert prefix + tool in perms["deny"]
    assert "Edit(/.claude/settings.json)" in perms["deny"]
    assert "Edit(/.claude/settings.local.json)" in perms["deny"]
    assert "Edit(/%s/**)" % ROOT in perms["deny"]
    assert perms["ask"] == ["Edit(/trading/trader.json)"]
    allow = perms["allow"]
    for t in ("CronCreate", "CronList", "CronDelete", "PushNotification", "Edit(/trading/**)",
              "Skill(personal-ai-trader:trader-check *)", "Skill(personal-ai-trader:trader-wrap)",
              'Bash(python3 "%s/tools/schedule.py" *)' % ROOT,
              'Bash(python3 "%s/tools/trader_config.py" --check *)' % ROOT,
              'Bash(python3 "%s/tools/render_rules.py" --check *)' % ROOT,
              'Bash(trading/.venv/bin/python "%s/tools/ledger.py" *)' % ROOT,
              'Bash(%s/trading/.venv/bin/python "%s/tools/ledger.py" *)' % (tmp_path, ROOT),
              'Bash(python3 "%s/tools/watch.py" *)' % ROOT,
              'Bash(python3 "%s/tools/setup_workspace.py" halt --reason *)' % ROOT,
              "Bash(python3 %s/tools/setup_workspace.py halt --reason *)" % ROOT,
              'Bash(python3 "%s/tools/doctor.py" --format *)' % ROOT,
              "Bash(python3 %s/tools/schedule.py *)" % ROOT,
              PLUGIN_PREFIX + "get_portfolio", PLAIN_PREFIX + "review_option_order",
              PLUGIN_PREFIX + "place_option_order"):
        assert t in allow, t
    assert "WebSearch" not in allow
    assert run(tmp_path, "--check").returncode == 0


def test_second_run_changes_nothing(tmp_path):
    assert run(tmp_path, "--yes").returncode == 0
    before = open(settings_path(tmp_path)).read()
    p = run(tmp_path, "--yes")
    assert p.returncode == 0 and "nothing to change" in p.stdout
    assert open(settings_path(tmp_path)).read() == before
    assert not glob.glob(settings_path(tmp_path) + ".bak-*")


def test_merge_keeps_the_users_entries(tmp_path):
    user = {
        "model": "some-model",
        "env": {"FOO": "1"},
        "permissions": {
            "allow": ["Bash(npm test *)", PLUGIN_PREFIX + "get_portfolio"],
            "deny": ["mcp__gmail__*"],
            "defaultMode": "default",
        },
        "hooks": {"Stop": []},
    }
    write(tmp_path, user)
    p = run(tmp_path, "--yes")
    assert p.returncode == 0, p.stderr
    got = read(tmp_path)
    assert got["model"] == "some-model" and got["env"] == {"FOO": "1"} and got["hooks"] == {"Stop": []}
    perms = got["permissions"]
    assert perms["defaultMode"] == "default"
    assert perms["allow"][:2] == ["Bash(npm test *)", PLUGIN_PREFIX + "get_portfolio"]
    assert perms["allow"].count(PLUGIN_PREFIX + "get_portfolio") == 1
    assert perms["deny"][0] == "mcp__gmail__*"
    assert len(glob.glob(settings_path(tmp_path) + ".bak-*")) == 1
    # The diff showed what was added.
    assert "+" in p.stdout and "exercise_option" in p.stdout


def test_stale_plugin_folder_rules_are_replaced(tmp_path):
    old = "/opt/plugins/personal-ai-trader/0.0.9"
    stale = ['Bash(python3 "%s/tools/schedule.py" *)' % old,
             "Bash(trading/.venv/bin/python %s/tools/ledger.py *)" % old]
    mine = ['Bash(python3 "/home/me/scripts/schedule_tool.py" *)', "Bash(python3 other.py *)"]
    write(tmp_path, {"permissions": {"allow": stale + mine}})
    p = run(tmp_path, "--check")
    assert p.returncode == 1  # no deny entries yet
    assert run(tmp_path, "--yes").returncode == 0
    allow = read(tmp_path)["permissions"]["allow"]
    for r in stale:
        assert r not in allow
    for r in mine:
        assert r in allow
    assert 'Bash(python3 "%s/tools/schedule.py" *)' % ROOT in allow


def test_check_exit_codes(tmp_path):
    p = run(tmp_path, "--check")
    assert p.returncode == 1 and "does not exist" in p.stdout
    assert run(tmp_path, "--yes").returncode == 0
    p = run(tmp_path, "--check")
    assert p.returncode == 0 and p.stdout.startswith("OK") and "WARN" not in p.stdout
    # Allow drift is a warning (exit 3 only with --strict).
    data = read(tmp_path)
    data["permissions"]["allow"].remove("CronCreate")
    write(tmp_path, data)
    p = run(tmp_path, "--check")
    assert p.returncode == 0 and "WARN: allow missing: CronCreate" in p.stdout
    assert run(tmp_path, "--check", "--strict").returncode == 3
    # A missing deny entry is a failure.
    data["permissions"]["deny"].remove(PLAIN_PREFIX + "exercise_option")
    write(tmp_path, data)
    p = run(tmp_path, "--check")
    assert p.returncode == 1 and PLAIN_PREFIX + "exercise_option" in p.stdout
    assert read(tmp_path) == data  # --check never writes


def test_check_warns_when_a_denied_tool_is_also_allowed(tmp_path):
    assert run(tmp_path, "--yes").returncode == 0
    data = read(tmp_path)
    data["permissions"]["allow"].append(PLUGIN_PREFIX + "exercise_option")
    write(tmp_path, data)
    p = run(tmp_path, "--check")
    assert p.returncode == 0 and "allowed AND denied" in p.stdout


def test_moved_plugin_folder_deny_is_drift_not_failure(tmp_path):
    assert run(tmp_path, "--yes").returncode == 0
    data = read(tmp_path)
    data["permissions"]["deny"].remove("Edit(/%s/**)" % ROOT)
    write(tmp_path, data)
    p = run(tmp_path, "--check")
    assert p.returncode == 0 and "plugin folder moved" in p.stdout


@pytest.mark.parametrize("content", ["{not json", "[]", '{"permissions": []}', '{"permissions": {"allow": "x"}}',
                                     '{"permissions": {"deny": [1]}}'])
def test_unreadable_settings_are_never_overwritten(tmp_path, content):
    write(tmp_path, content)
    for args in (["--yes"], ["--check"], []):
        p = run(tmp_path, *args)
        assert p.returncode == 2, (args, p.stdout, p.stderr)
        assert "nothing was written" in p.stderr
    assert open(settings_path(tmp_path)).read() == content


def test_workspace_inside_the_plugin_is_refused(tmp_path):
    p = subprocess.run([sys.executable, SCRIPT, "--workspace", os.path.join(ROOT, "templates")],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "inside the plugin" in p.stderr


def test_not_a_plugin_root_is_refused(tmp_path):
    p = run(tmp_path, "--plugin-root", str(tmp_path))
    assert p.returncode == 2 and "not the plugin folder" in p.stderr


def test_print_rules(tmp_path):
    p = run(tmp_path, "--print-rules")
    assert p.returncode == 0
    assert json.loads(p.stdout) == want(tmp_path)
    assert not os.path.exists(settings_path(tmp_path))


def test_web_and_harness_flags(tmp_path):
    w = want(tmp_path, web=True)
    assert "WebSearch" in w["allow"]
    h = want(tmp_path, harness=True)
    for tool in setup_settings.HARNESS_DENY_TOOLS:
        for prefix in (PLUGIN_PREFIX, PLAIN_PREFIX):
            assert prefix + tool in h["deny"]
            assert prefix + tool not in h["allow"]
    assert run(tmp_path, "--harness", "--yes").returncode == 0
    assert PLAIN_PREFIX + "create_alert" in read(tmp_path)["permissions"]["deny"]


# ---------------------------------------------------------------- what is (and is never) allowed

def test_never_allows_anything_that_edits_the_guard_or_settings(tmp_path):
    w = want(tmp_path, web=True)
    for rule in w["allow"]:
        assert rule not in ("Bash", "Bash(*)", "Edit", "Write", "Edit(/**)", "Edit(**)"), rule
        for bad in ("setup_settings", "order_guard", "hooks", ".claude", "price_log", "exec "):
            assert bad not in rule, rule
        if "setup_workspace" in rule:
            # Only the halt, and only as `halt --reason ...` straight after the script path.
            assert re.search(r'/tools/setup_workspace\.py"? halt --reason \*\)$', rule), rule
        if rule.startswith("Edit("):
            assert rule == "Edit(/trading/**)", rule
        if rule.startswith("Bash("):
            assert rule.endswith(")") and " && " not in rule and ";" not in rule and "|" not in rule
    # The two read-only tools are allowed only with --check.
    for rule in w["allow"]:
        if "trader_config.py" in rule or "render_rules.py" in rule:
            assert re.search(r'\.py"? --check \*\)$', rule), rule


def test_allowed_robinhood_tools_are_on_the_guards_list(tmp_path):
    known = (order_guard.OPEN_READS | order_guard.ALERT_WRITES | order_guard.ACCOUNT_TOOLS
             | order_guard.ORDER_TOOLS | order_guard.WATCHLIST_WRITES)
    tools = set(setup_settings.ROUTINE_ROBINHOOD_TOOLS)
    assert not tools & order_guard.NEVER
    assert tools <= known, sorted(tools - known)
    assert not tools & {"create_watchlist", "update_watchlist"}
    allow = want(tmp_path)["allow"]
    for t in tools:
        assert PLUGIN_PREFIX + t in allow and PLAIN_PREFIX + t in allow


def test_every_robinhood_tool_a_routine_names_is_allowed():
    """A routine that calls a tool missing from the allow list would stall an unattended run on a prompt."""
    known = (order_guard.OPEN_READS | order_guard.ALERT_WRITES | order_guard.ACCOUNT_TOOLS
             | order_guard.ORDER_TOOLS | order_guard.WATCHLIST_WRITES)
    named = set()
    for name in ("trader-check", "trader-wrap", "trader-weekly-review"):
        with open(os.path.join(SKILLS, name, "SKILL.md")) as f:
            named |= set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", f.read()))
    used = (named & known) - {"create_watchlist", "update_watchlist"}
    missing = used - set(setup_settings.ROUTINE_ROBINHOOD_TOOLS)
    assert not missing, sorted(missing)


def test_deny_list_matches_the_guard():
    assert set(setup_settings.NEVER) == order_guard.NEVER
    assert {"preview_crypto_order", "place_crypto_order", "cancel_crypto_order",
            "get_crypto_account_onboarding_info", "exercise_option", "cancel_option_exercise"} == order_guard.NEVER


def test_stale_detector():
    root = "/a/b/personal-ai-trader/1.0.0"
    assert setup_settings.stale_allow(['Bash(python3 "/x/tools/ledger.py" *)'], root)
    assert setup_settings.stale_allow(["Bash(exec python3 /x/tools/watch.py *)"], root)
    assert setup_settings.stale_allow(['Bash(pkill -f "%s/tools/watch.py")' % root], root)
    assert setup_settings.stale_allow(["Bash(pgrep -fl %s/tools/watch.py)" % root], root)
    assert not setup_settings.stale_allow(['Bash(python3 "%s/tools/ledger.py" *)' % root], root)
    assert not setup_settings.stale_allow(["Bash(python3 /x/tools/mytool.py *)", "CronList"], root)


def test_deny_only(tmp_path):
    p = run(tmp_path, "--deny-only", "--yes")
    assert p.returncode == 0
    perms = read(tmp_path)["permissions"]
    assert perms["allow"] == [] and perms["ask"] == ["Edit(/trading/trader.json)"]
    p = run(tmp_path, "--check")
    assert p.returncode == 0 and "allow missing" in p.stdout


# ---------------------------------------------------------------- matching the rules the way Claude Code does

def rule_matches(rule, command):
    """Claude Code's Bash rule matching for one simple command (permissions docs, "Wildcard patterns"):
    `*` stands for any text; a trailing ` *` that is the only wildcard also matches the bare command."""
    m = re.match(r"^Bash\((.*)\)$", rule)
    if not m:
        return False
    pattern = m.group(1)
    if pattern.endswith(" *") and pattern.count("*") == 1 and command == pattern[:-2]:
        return True
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, command, re.S) is not None


def allowed(command, rules):
    return any(rule_matches(r, command) for r in rules)


def test_rule_matcher_follows_the_docs():
    assert rule_matches("Bash(ls *)", "ls") and rule_matches("Bash(ls *)", "ls -la")
    assert not rule_matches("Bash(ls *)", "lsof")
    assert rule_matches("Bash(ls*)", "lsof")
    assert rule_matches("Bash(npm run build)", "npm run build")
    assert not rule_matches("Bash(npm run build)", "npm run build --watch")
    assert not rule_matches("Bash(* --help *)", "npm --help")


def _halt_rules(ws):
    return [r for r in want(ws)["allow"] if "setup_workspace" in r]


@pytest.mark.parametrize("quoted", [True, False])
def test_halt_rule_allows_the_halt_and_nothing_else(tmp_path, quoted):
    rules = _halt_rules(tmp_path)
    assert len(rules) == 2
    sw = ('python3 "%s/tools/setup_workspace.py"' if quoted else "python3 %s/tools/setup_workspace.py") % ROOT
    assert allowed(sw + ' halt --reason "account value 3480 at or below halt_account_value 3500"', rules)
    for other in ("phase --from HALTED --to LIVE --confirm yes", "phase --from HALTED --to DRY_RUN --confirm yes",
                  "go-live --confirm go", "set limits.option_cap_dollars=99999 --yes", "set phase=LIVE --yes",
                  "extend-paper --expect 0", "config --account-number 1 --nickname x --owner y --start-balance 9 "
                  "--force", "init", "notes --add x", "--workspace /elsewhere phase --from HALTED --to LIVE "
                  "--confirm yes", "--workspace /elsewhere halt --reason x", "halt", "gate"):
        assert not allowed(sw + " " + other, rules), other
    # Every other allow rule leaves setup_workspace.py alone too.
    everything = want(tmp_path, web=True)["allow"]
    for other in ("phase --from HALTED --to LIVE --confirm yes", "go-live --confirm go", "set phase=LIVE --yes",
                  "extend-paper --expect 0"):
        assert not allowed(sw + " " + other, everything), other


ROUTINE_SKILL_FILES = ("trader-check", "trader-wrap", "trader-weekly-review", "trader-status", "trader-doctor")
# Commands a routine runs that are meant to ask: the owner is present for each.
MEANT_TO_ASK = ('setup_workspace.py" extend-paper', 'doctor.py" --bundle')
COMMAND = re.compile(r"(?:python3|trading/\.venv/bin/python|pgrep|pkill) [^`\n]+")


def skill_commands(name):
    with open(os.path.join(SKILLS, name, "SKILL.md")) as f:
        text = f.read()
    out = []
    fenced = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", text, re.S))
    for span in re.findall(r"`([^`\n]+)`", text) + fenced.splitlines():
        for m in COMMAND.finditer(span):
            cmd = m.group(0).split(" <<")[0].strip()
            if "${CLAUDE_PLUGIN_ROOT}" in cmd or cmd.startswith(("pgrep", "pkill")):
                out.append(cmd.replace("${CLAUDE_PLUGIN_ROOT}", ROOT))
    return out


@pytest.mark.parametrize("name", ROUTINE_SKILL_FILES)
def test_every_command_a_routine_runs_is_allowed(tmp_path, name):
    """The commands written in the routine skills, with the plugin root substituted, each match an allow rule
    (or are one of the few meant to ask). A command that matches none stops an unattended run at a prompt."""
    rules = want(tmp_path)["allow"]
    cmds = skill_commands(name)
    assert cmds, name
    for cmd in cmds:
        if any(m in cmd for m in MEANT_TO_ASK):
            assert not allowed(cmd, rules), cmd
        else:
            assert allowed(cmd, rules), cmd


def test_the_check_halts_with_the_command_and_the_review_extends_with_a_prompt(tmp_path):
    rules = want(tmp_path)["allow"]
    check = skill_commands("trader-check")
    halts = [c for c in check if "setup_workspace.py" in c]
    assert halts and all(" halt --reason " in c and allowed(c, rules) for c in halts)
    review = [c for c in skill_commands("trader-weekly-review") if "setup_workspace.py" in c]
    assert review and all(" extend-paper --expect " in c and not allowed(c, rules) for c in review)


def test_no_process_name_rule_is_written_for_the_watcher(tmp_path):
    """The watcher is checked and stopped through watch.py itself (its own workspace's record), so no
    pgrep / pkill rule exists at all: a process-name pattern can match someone else's watcher."""
    rules = want(tmp_path)["allow"]
    assert not [r for r in rules if "pkill" in r or "pgrep" in r or r.startswith("Bash(kill")]
    for cmd in ('pkill -f "%s/tools/watch.py"' % ROOT, 'pgrep -fl "%s/tools/watch.py"' % ROOT,
                "pkill -f tools/watch.py", "pkill -f watch", "pkill python3",
                "pkill -f /other/trading/tools/watch.py", "pgrep -fl tools/watch.py", "kill 123"):
        assert not allowed(cmd, rules), cmd
    assert allowed('python3 "%s/tools/watch.py" --stop' % ROOT, rules)
    assert allowed('python3 "%s/tools/watch.py" --status' % ROOT, rules)


def test_rooted_watcher_rules_from_earlier_versions_are_removed(tmp_path):
    old = ['Bash(pkill -f "%s/tools/watch.py")' % ROOT, "Bash(pkill -f %s/tools/watch.py)" % ROOT,
           'Bash(pgrep -fl "%s/tools/watch.py")' % ROOT, "Bash(pgrep -fl %s/tools/watch.py)" % ROOT]
    assert run(tmp_path, "--yes").returncode == 0
    data = read(tmp_path)
    data["permissions"]["allow"] += old + ["Bash(npm test *)"]
    write(tmp_path, data)
    assert run(tmp_path, "--check", "--strict").returncode == 3
    assert run(tmp_path, "--yes").returncode == 0
    allow = read(tmp_path)["permissions"]["allow"]
    for r in old:
        assert r not in allow, r
    assert "Bash(npm test *)" in allow
    assert run(tmp_path, "--check", "--strict").returncode == 0


def test_legacy_rules_are_replaced(tmp_path):
    legacy = ["Bash(pgrep -fl tools/watch.py)", "Bash(pkill -f tools/watch.py)",
              'Bash(exec python3 "%s/tools/watch.py" *)' % ROOT, "Bash(exec python3 %s/tools/watch.py *)" % ROOT]
    write(tmp_path, {"permissions": {"allow": legacy + ["Bash(npm test *)"]}})
    assert run(tmp_path, "--yes").returncode == 0
    allow = read(tmp_path)["permissions"]["allow"]
    for r in legacy:
        assert r not in allow, r
    assert "Bash(npm test *)" in allow
    assert run(tmp_path, "--check").returncode == 0


# ---------------------------------------------------------------- news-site fetch rules (--web)

TEMPLATE_HOSTS = ["www.federalreserve.gov", "www.bls.gov", "home.treasury.gov", "www.treasurydirect.gov",
                  "www.eia.gov", "finance.yahoo.com", "www.marketwatch.com", "www.cmegroup.com", "www.nyse.com"]


def write_sources(ws, text):
    os.makedirs(os.path.join(str(ws), "trading"), exist_ok=True)
    with open(os.path.join(str(ws), "trading", "NEWS-SOURCES.md"), "w") as f:
        f.write(text)


def fetch(rules):
    return [r for r in rules if r.startswith("WebFetch")]


def test_web_allows_the_template_sites_when_the_workspace_has_no_sources_file(tmp_path):
    assert fetch(want(tmp_path, web=True)["allow"]) == ["WebFetch(domain:%s)" % h for h in TEMPLATE_HOSTS]


def test_no_fetch_rule_without_web_and_never_a_blanket_one(tmp_path):
    assert fetch(want(tmp_path)["allow"]) == []
    assert fetch(want(tmp_path, web=True, deny_only=True)["allow"]) == []
    for rule in fetch(want(tmp_path, web=True)["allow"]):
        assert setup_settings.FETCH_RULE.match(rule) and "*" not in rule


def test_fetch_rules_follow_the_workspace_sources_file(tmp_path):
    write_sources(tmp_path, "\n".join([
        "# News sources",
        "Prose with a link is not a source: https://prose.example.com/page",
        "| Source | What for | URL | Status |",
        "|---|---|---|---|",
        "| Fed | Rates | https://www.federalreserve.gov/newsevents/calendar.htm | OK |",
        "| Fed again | Rates | https://WWW.FederalReserve.gov/other | |",
        "| Mine | Headlines | http://news.example.org/latest?x=1 | |",
        "| Per company | Filings | Per company; no link | |",
    ]))
    assert fetch(want(tmp_path, web=True)["allow"]) == [
        "WebFetch(domain:www.federalreserve.gov)", "WebFetch(domain:news.example.org)"]


@pytest.mark.parametrize("url", [
    "https://10.0.0.1/admin", "http://localhost/x", "https://localhost:8080/x", "https://user:pw" + "@" + "evil.example.com/x",
    "https://*.example.com/x", "https://example.com:8443/x", "https://*/x", "https://[::1]/x", "https://-bad-.com/x",
])
def test_odd_hosts_never_become_rules(tmp_path, url):
    write_sources(tmp_path, "| Source | What for | URL | Status |\n|---|---|---|---|\n| Odd | x | %s | |\n" % url)
    assert fetch(want(tmp_path, web=True)["allow"]) == []


def test_check_reports_a_source_added_after_setup_and_a_rule_left_behind(tmp_path):
    write_sources(tmp_path, "| Source | What for | URL | Status |\n|---|---|---|---|\n| A | x | https://a.example.com/ | |\n")
    assert run(tmp_path, "--web", "--yes").returncode == 0
    assert "WebFetch(domain:a.example.com)" in read(tmp_path)["permissions"]["allow"]
    p = run(tmp_path, "--check", "--strict")
    assert p.returncode == 0 and "WARN" not in p.stdout, p.stdout
    # the owner swaps the source; nothing is allowed until the script is re-run with their yes
    write_sources(tmp_path, "| Source | What for | URL | Status |\n|---|---|---|---|\n| B | x | https://b.example.com/ | |\n")
    assert "WebFetch(domain:b.example.com)" not in read(tmp_path)["permissions"]["allow"]
    p = run(tmp_path, "--check", "--strict")
    assert p.returncode == 3
    assert "allow missing: WebFetch(domain:b.example.com)" in p.stdout
    assert "not in trading/NEWS-SOURCES.md" in p.stdout and "WebFetch(domain:a.example.com)" in p.stdout
    # re-running keeps the old rule (it may be the owner's own) and adds the new one
    assert run(tmp_path, "--web", "--yes").returncode == 0
    allow = read(tmp_path)["permissions"]["allow"]
    assert "WebFetch(domain:a.example.com)" in allow and "WebFetch(domain:b.example.com)" in allow


def test_check_does_not_ask_for_web_rules_when_the_owner_said_no(tmp_path):
    assert run(tmp_path, "--yes").returncode == 0
    p = run(tmp_path, "--check", "--strict")
    assert p.returncode == 0 and "WebFetch" not in p.stdout and "WebSearch" not in p.stdout
