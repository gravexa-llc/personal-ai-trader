"""Lint the routine skills and the routine templates.

- every skills/<name>/SKILL.md has frontmatter with `name` (matching its folder) and `description`;
- no personal data (owner names, the live account number, home-folder paths) in skills or templates;
- no hard-coded clock times in the skills (the schedule comes only from tools/schedule.py), except
  inside a clearly marked example;
- no `$<digit>` in a skill body: Claude Code would substitute it with a skill argument;
- every plugin tool or template a skill names is one of the contracted files.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(ROOT, "skills")
TEMPLATES_DIR = os.path.join(ROOT, "templates")

ROUTINE_SKILLS = ("trader-check", "trader-wrap", "trader-weekly-review")

# The tools the skills may call (the tool contract).
CONTRACT_TOOLS = {
    "tools/ledger.py", "tools/schedule.py", "tools/render_rules.py", "tools/trader_config.py",
    "tools/watch.py", "tools/order_guard.py", "tools/setup_settings.py", "tools/setup_workspace.py",
    "tools/doctor.py",
}
CONTRACT_TEMPLATES = {
    "templates/PLAN.md", "templates/STATE.md.tmpl", "templates/journal-entry.md",
    "templates/RULES.md.tmpl", "templates/STRATEGY.sample.md", "templates/ROBINHOOD-SOURCES.md",
    "templates/NEWS-SOURCES.md", "templates/trader.example.json",
}

# Personal data that must never appear in a skill, template or doc. This file holds only the generic part;
# the maintainer's own terms (names, an account number, a handle) live in tests/personal-terms.local.txt,
# which git ignores, one term per line. Without that file only the generic checks run.
PERSONAL = ["/" + "Users/"]
_LOCAL_TERMS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "personal-terms.local.txt")
if os.path.exists(_LOCAL_TERMS):
    with open(_LOCAL_TERMS, encoding="utf-8") as _f:
        PERSONAL += [ln.strip() for ln in _f if ln.strip() and not ln.lstrip().startswith("#")]


def _public_repo():
    """The repository slug in the README's `/plugin marketplace add <owner>/<repo>` line: the one place an
    owner's handle may appear."""
    readme = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "README.md")
    with open(readme, encoding="utf-8") as f:
        m = re.search(r"/plugin marketplace add ([\w.-]+/[\w.-]+)", f.read())
    return m.group(1).lower() if m else ""


PUBLIC_REPO = _public_repo()


def personal_hits(text):
    low = text.lower().replace(PUBLIC_REPO, "") if PUBLIC_REPO else text.lower()
    return [s for s in PERSONAL if s.lower() in low]

CLOCK = re.compile(r"(?<![\d.:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])")
DOLLAR_DIGIT = re.compile(r"(?<!\\)\$\d")
TOOL_REF = re.compile(r"\btools/[\w.-]+\.py\b")
TEMPLATE_REF = re.compile(r"\btemplates/[\w./-]+")


def skill_files():
    out = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.isfile(path):
            out.append((name, path))
    return out


def text_files(*dirs):
    for d in dirs:
        for base, _, files in os.walk(d):
            for f in files:
                if f.endswith((".md", ".tmpl", ".json", ".txt")):
                    yield os.path.join(base, f)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def split_frontmatter(text):
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.S)
    assert m, "SKILL.md must start with a --- frontmatter block"
    fields = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        key, sep, value = line.partition(":")
        assert sep, "frontmatter line without a colon: %r" % line
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, m.group(2)


def unexampled_lines(body):
    """Lines of the body outside clearly marked examples.

    Exempt: a line containing 'example' or 'e.g.', and a fenced block whose preceding non-empty
    line contains 'example'.
    """
    lines = body.splitlines()
    out = []
    in_fence = fence_exempt = False
    prev = ""
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence, fence_exempt = True, "example" in prev.lower()
            else:
                in_fence = fence_exempt = False
            continue
        if line.strip():
            if not in_fence:
                prev = line
        if in_fence and fence_exempt:
            continue
        low = line.lower()
        if "example" in low or "e.g." in low:
            continue
        out.append((i, line))
    return out


def test_routine_skills_exist():
    names = {n for n, _ in skill_files()}
    for name in ROUTINE_SKILLS:
        assert name in names, "skills/%s/SKILL.md is missing" % name


@pytest.mark.parametrize("name,path", skill_files())
def test_frontmatter(name, path):
    fields, body = split_frontmatter(read(path))
    assert fields.get("name") == name, "frontmatter name must equal the folder name %r" % name
    desc = fields.get("description", "")
    assert desc, "frontmatter needs a description"
    assert len(desc) <= 1536, "description is truncated above 1,536 characters in the skill listing"
    assert body.strip(), "skill body is empty"


@pytest.mark.parametrize("path", sorted(text_files(SKILLS_DIR, TEMPLATES_DIR)))
def test_no_personal_data(path):
    found = personal_hits(read(path))
    assert not found, "%s contains personal data: %s" % (os.path.relpath(path, ROOT), found)


@pytest.mark.parametrize("name,path", skill_files())
def test_no_hard_coded_clock_times(name, path):
    _, body = split_frontmatter(read(path))
    hits = [(i, line.strip()) for i, line in unexampled_lines(body) if CLOCK.search(line)]
    assert not hits, "clock times belong in schedule.py / trader.json, not %s: %s" % (name, hits)


@pytest.mark.parametrize("name,path", skill_files())
def test_no_argument_placeholders_by_accident(name, path):
    _, body = split_frontmatter(read(path))
    hits = [(i, line.strip()) for i, line in enumerate(body.splitlines(), 1) if DOLLAR_DIGIT.search(line)]
    assert not hits, "$<digit> is replaced by a skill argument in %s (escape it as \\$): %s" % (name, hits)


@pytest.mark.parametrize("name,path", skill_files())
def test_tool_and_template_paths_are_contracted(name, path):
    text = read(path)
    tools = set(TOOL_REF.findall(text))
    unknown = tools - CONTRACT_TOOLS
    assert not unknown, "%s names tools outside the contract: %s" % (name, sorted(unknown))
    templates = {t.rstrip(".,)`'\"") for t in TEMPLATE_REF.findall(text)}
    unknown = templates - CONTRACT_TEMPLATES
    assert not unknown, "%s names templates outside the contract: %s" % (name, sorted(unknown))


@pytest.mark.parametrize("name,path", skill_files())
def test_plugin_paths_use_the_plugin_root(name, path):
    """A tool path must be reached through ${CLAUDE_PLUGIN_ROOT}, which Claude Code substitutes in plugin skills."""
    for i, line in enumerate(read(path).splitlines(), 1):
        for m in re.finditer(r"python3?\s+\"?([^\s\"]*tools/[\w.-]+\.py)", line):
            assert m.group(1).startswith("${CLAUDE_PLUGIN_ROOT}/"), \
                "%s line %d runs %s without ${CLAUDE_PLUGIN_ROOT}" % (name, i, m.group(1))


def test_lint_catches_what_it_should():
    """The checks above are only useful if they fire: feed them known-bad text."""
    assert CLOCK.search("prep at 6:05 and wrap at 13:16")
    assert not CLOCK.search("limit 10-15% below; ratio 0.90; HH:MM")
    assert DOLLAR_DIGIT.search("cost $224") and not DOLLAR_DIGIT.search(r"cost \$224")
    body = "Example:\n```\n## 6:05 prep\n```\nrun at 7:33\n"
    assert [line for _, line in unexampled_lines(body)] == ["run at 7:33"]


# ---------------------------------------------------------------- commands an allow rule can match

# A Bash allow rule matches each simple command's own text (Claude Code permissions docs: compound commands,
# wrappers). A shell function, a `cd ... &&` prefix or `exec` in front of a tool means no rule matches and an
# unattended run stops at a prompt, so the skills call every tool directly.
SHELL_FUNCTION = re.compile(r"(?:^|[\s`;&|({])(?:function\s+[\w-]+|[A-Za-z_][\w-]*\s*\(\s*\))\s*\{")
L_COMMAND = re.compile(r"(?:^|[`;&|(]|&&|\|\|)\s*L\s")
CD_CHAIN = re.compile(r"\bcd\s+[^`\n]*?(?:&&|;|\|\|)")
EXEC_TOOL = re.compile(r"\bexec\s+(?:python3?|trading/)")
LEDGER_FULL = 'trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py"'


def command_lines(text):
    """Backticked spans and fenced-block lines: the places a skill writes a command."""
    spans = re.findall(r"`([^`\n]+)`", text)
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        spans += block.splitlines()
    return spans


# A watcher is stopped through `watch.py --stop` (its own workspace's record), never by process name.
PROCESS_NAME_KILL = re.compile(r"^\s*(?:pkill|pgrep|killall)\b")


def lint_commands(text):
    problems = []
    for span in command_lines(text):
        if SHELL_FUNCTION.search(span):
            problems.append("shell function: " + span)
        if L_COMMAND.search(span):
            problems.append("`L` used as a command: " + span)
        if CD_CHAIN.search(span):
            problems.append("cd chained to a command: " + span)
        if EXEC_TOOL.search(span):
            problems.append("exec in front of a tool: " + span)
        if PROCESS_NAME_KILL.search(span):
            problems.append("process-name match (use watch.py --stop / --status): " + span)
    return problems


ROUTINE_TEMPLATES = ("PLAN.md", "STATE.md.tmpl")


@pytest.mark.parametrize("path", [p for _, p in skill_files()]
                         + [os.path.join(TEMPLATES_DIR, t) for t in ROUTINE_TEMPLATES])
def test_no_shell_functions_or_wrapped_commands(path):
    text = read(path)
    problems = lint_commands(text)
    # Anywhere in the text, not only in commands: no shell function definition at all.
    if SHELL_FUNCTION.search(text):
        problems.append("shell function definition in the text")
    assert not problems, "%s: %s" % (os.path.relpath(path, ROOT), problems)


@pytest.mark.parametrize("name,path", skill_files())
def test_ledger_is_called_in_full(name, path):
    """Every ledger call is `trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" <subcommand>`."""
    for span in command_lines(read(path)):
        if "ledger.py" in span and "python" in span:
            assert LEDGER_FULL + " " in span, "%s: ledger not called in full: %s" % (name, span)


def test_command_lint_catches_what_it_should():
    bad = [
        '`cd "${CLAUDE_PROJECT_DIR}" && L() { trading/.venv/bin/python x/ledger.py "$@"; } && L show open`',
        "`L show open`",
        "`L build`",
        "```\nfunction led { python3 ledger.py; }\n```",
        '`cd "${CLAUDE_PROJECT_DIR}" && python3 "${CLAUDE_PLUGIN_ROOT}/tools/doctor.py"`',
        '`exec python3 "${CLAUDE_PLUGIN_ROOT}/tools/watch.py" --interval 300`',
        "`pkill -f \"${CLAUDE_PLUGIN_ROOT}/tools/watch.py\"`",
        '`pgrep -fl "${CLAUDE_PLUGIN_ROOT}/tools/watch.py"`',
        "`pkill -f tools/watch.py`",
    ]
    for text in bad:
        assert lint_commands(text), text
    good = ['`trading/.venv/bin/python "${CLAUDE_PLUGIN_ROOT}/tools/ledger.py" show open`',
            '`python3 "${CLAUDE_PLUGIN_ROOT}/tools/watch.py" --interval 300 "XYZ above 1"`',
            '`python3 "${CLAUDE_PLUGIN_ROOT}/tools/watch.py" --stop`', "`get_option_quotes`", "`L0001`",
            "Level 2 (`option level`)"]
    for text in good:
        assert not lint_commands(text), text
