#!/usr/bin/env python3
"""Personal AI Trader doctor: the health checks that need no Claude tool.

The /trader-doctor skill runs this first, then adds the checks that need Claude tools (the Robinhood
connection, the account, the approval switch, the live guard proof, CronList, reconcile against Robinhood).
This script reads files and runs local subprocesses only: it never calls Robinhood, never touches an order,
never edits the guard, the hook, the settings or trader.json, and never prints the full account number or a
token. Its only write is the optional support bundle (--bundle) in trading/logs/. The ledger build test runs
on a copy in a temporary folder, never on the real workbook.

Usage:
  doctor.py [--workspace PATH] [--format table|json|both] [--only ID[,ID...]] [--now YYYY-MM-DDTHH:MM]
  doctor.py --bundle [--skill-results FILE|-] [...]    also write trading/logs/doctor-YYYY-MM-DD.md
  doctor.py --tool-names FILE|-                        classify Robinhood tool names (one per line or a
                                                       JSON list) against the hook matcher; adds the
                                                       "connector" check

  --now is local time in trader.json's timezone (for tests and "what would it say at the open").
  --skill-results: a JSON list of {"area", "check", "status", "detail", "fix"} from the skill's own checks,
  merged into the report and the bundle.

Checks (id: what):
  config        trader.json parses and validates (tools/trader_config.py); sanity warnings
  rules         RULES.md matches what trader.json generates (tools/render_rules.py --check)
  hook          the plugin's hooks/hooks.json registers the order guard for both tool namings, onFailure block;
                no settings file switches hooks off
  guard_script  the guard script itself refuses a two-leg preview and lets an allowed preview through, run
                exactly as the hook runs it (python3 -I), against this workspace's config (no network)
  deny          tools/setup_settings.py --check: the deny list in .claude/settings.json covers crypto
                preview / place / cancel, crypto onboarding, option exercise and its cancel in both tool
                namings (FAIL if not); stale allow / ask rules are a WARN (re-run setup step 8)
  python        trading/.venv exists, openpyxl imports, `ledger.py build` works on a copy of the ledger
  files         trading/ journal, ledger, reviews, logs present and writable; STATE, PLAN, STRATEGY present
  strategy      STRATEGY.md has the sections the routines rely on, numbered setups, and is not the sample
  state         STATE.md rewritten within 90 minutes during a trading day's run window
  watcher       watch.log fresh when positions are open in market hours
  version       plugin version and trader.json schema agree
  calendar      tools/market_calendar.json covers today, warning within 60 days of its end
  connector     (only with --tool-names) every Robinhood tool name is covered by the hook matcher

Exit codes: 0 no FAIL, 1 at least one FAIL, 2 usage problem.
Standard library only; Python 3.9+.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    sys.exit("doctor: needs Python 3.9 or later (zoneinfo)")

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import schedule  # noqa: E402
import trader_config  # noqa: E402

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
SEVERITY = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}
SERVER = "robinhood-trading"
CHECK_IDS = ("config", "rules", "hook", "guard_script", "deny", "python", "files", "strategy", "state",
             "watcher", "version", "calendar")
STATE_STALE_MINUTES = 90
WATCH_STALE_MINUTES = 10
CALENDAR_WARN_DAYS = 60
SUBPROCESS_TIMEOUT = 120

# The section headings of templates/STRATEGY.sample.md that the routines read.
STRATEGY_SECTIONS = (
    "## Market read", "## Calendar that matters", "## Sizing", "## Setups in play", "Not in play",
    "## Exits", "## Posture this week", "## Watchlist to screen at prep", "## Open position",
    "## Amendments",
)
SAMPLE_BANNER = "SAMPLE — an example of the format, not a recommendation"
REQUIRED_DIRS = ("journal", "ledger", "reviews", "logs")
EXPECTED_FILES = ("STATE.md", "PLAN.md", "STRATEGY.md", "RULES.md")
TOKEN_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(access_token|refresh_token|id_token|token|authorization|api[_-]?key|password|secret)"
               r"(\"?\s*[:=]\s*\"?)[^\s\",}]{4,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
]


# ---------------------------------------------------------------- helpers

def check(cid, area, status, detail, fix=""):
    return {"id": cid, "area": area, "status": status, "detail": detail, "fix": fix if status in (WARN, FAIL) else ""}


def mask_account(number):
    number = str(number or "")
    return "****" + number[-4:] if len(number) > 4 else "****"


def scrub(text, secrets=(), home=False):
    """Mask every secret (the full account number) and anything that looks like a token; with home=True
    also shorten the home folder to ~ (for the support bundle)."""
    for s in secrets:
        if s and len(s) >= 5:
            text = re.sub(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(s), mask_account(s), text)
    for i, pattern in enumerate(TOKEN_PATTERNS):
        if i == 0:
            text = pattern.sub(lambda m: m.group(1) + " [redacted]", text)
        elif i == 1:
            text = pattern.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    if home:
        folder = os.path.expanduser("~")
        if folder and len(folder) > 1:
            text = text.replace(folder, "~")
    return text


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def plugin_manifest(plugin_root):
    try:
        return read_json(os.path.join(plugin_root, ".claude-plugin", "plugin.json"))
    except (OSError, ValueError):
        return {}


def tool_prefixes(plugin_name):
    return ["mcp__%s__" % SERVER, "mcp__plugin_%s_%s__" % (plugin_name, SERVER)]


def hook_python():
    """The interpreter the hook runs (`python3` on PATH), or None."""
    return shutil.which("python3")


def run(cmd, env=None, cwd=None, stdin=None, timeout=SUBPROCESS_TIMEOUT):
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out after %ds" % timeout
    except OSError as e:
        return None, "", str(e)


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env.update(extra or {})
    return env


def first_lines(text, n=3):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    out = "; ".join(lines[:n])
    return out + (" ..." if len(lines) > n else "")


# ---------------------------------------------------------------- context

class Context:
    def __init__(self, workspace, plugin_root=PLUGIN_ROOT, now=None, timezone=None, user_config_dir=None):
        self.workspace = os.path.abspath(workspace)
        self.plugin_root = plugin_root
        self.trading = os.path.join(self.workspace, "trading")
        self.manifest = plugin_manifest(plugin_root)
        self.plugin_name = self.manifest.get("name") or "personal-ai-trader"
        self.plugin_version = self.manifest.get("version") or "unknown"
        self.cfg, self.cfg_error, self.raw_cfg = None, None, None
        path = trader_config.config_path(self.workspace)
        try:
            self.raw_cfg = read_json(path)
        except (OSError, ValueError):
            self.raw_cfg = None
        try:
            self.cfg = trader_config.load(path)
        except trader_config.ConfigError as e:
            self.cfg_error = e
        tz_name = timezone or (self.cfg.timezone if self.cfg else None)
        if not tz_name and isinstance(self.raw_cfg, dict) and isinstance(self.raw_cfg.get("timezone"), str):
            tz_name = self.raw_cfg["timezone"]
        try:
            self.tz = ZoneInfo(tz_name) if tz_name else None
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = None
        self.tz_name = tz_name if self.tz else None
        if now is not None:
            self.now = now if now.tzinfo else (now.replace(tzinfo=self.tz) if self.tz else now)
        else:
            self.now = datetime.now(self.tz) if self.tz else datetime.now()
        try:
            self.calendar = schedule.load_calendar()
        except (OSError, ValueError, KeyError):
            self.calendar = None
        self.user_config_dir = user_config_dir or os.environ.get("CLAUDE_CONFIG_DIR") \
            or os.path.join(os.path.expanduser("~"), ".claude")

    @property
    def account_number(self):
        if self.cfg:
            return self.cfg.account.number
        if isinstance(self.raw_cfg, dict) and isinstance(self.raw_cfg.get("account"), dict):
            n = self.raw_cfg["account"].get("number")
            return str(n) if n is not None else None
        return None

    @property
    def today(self):
        return self.now.date()

    @property
    def schedule_offset(self):
        return self.cfg.schedule_offset_minutes if self.cfg else 0

    def trading_day(self):
        return self.calendar is not None and schedule.is_trading_day(self.today, self.calendar)

    def runs_today(self):
        if not (self.tz and self.calendar):
            return []
        return schedule.runs_for_day(self.today, self.tz, self.calendar, self.schedule_offset)

    def local_dt(self, run):
        return datetime.strptime(run["local_date"] + " " + run["local_time"], "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)

    def market_window(self):
        """(open, close) as aware local datetimes for today, or None if the market is shut."""
        if not (self.tz and self.calendar):
            return None
        o = schedule.market_open_et(self.today, self.calendar)
        c = schedule.market_close_et(self.today, self.calendar)
        if o is None or c is None:
            return None
        return o.astimezone(self.tz), c.astimezone(self.tz)

    def open_trades(self):
        path = os.path.join(self.trading, "ledger", "trades.csv")
        try:
            with open(path, newline="", encoding="utf-8") as f:
                return [r for r in csv.DictReader(f) if (r.get("status") or "") == "open"]
        except (OSError, UnicodeDecodeError, csv.Error):
            return []


# ---------------------------------------------------------------- checks

def check_config(ctx):
    path = trader_config.config_path(ctx.workspace)
    if ctx.cfg_error is not None:
        problems = ctx.cfg_error.problems
        return [check("config", "Config", FAIL,
                      "trader.json is missing or invalid: " + "; ".join(problems[:4])
                      + (" (+%d more)" % (len(problems) - 4) if len(problems) > 4 else ""),
                      "Fix trading/trader.json (run `python3 tools/trader_config.py --check` for the full list) "
                      "or re-run /personal-ai-trader:trader-setup --limits. Until it is valid the order guard refuses "
                      "every account call.")]
    cfg = ctx.cfg
    detail = "valid (phase %s, account %s, schema %s)" % (cfg.phase, mask_account(cfg.account.number), cfg.schema)
    if cfg.warnings:
        return [check("config", "Config", WARN, detail + "; warnings: " + "; ".join(cfg.warnings),
                      "These limits are allowed but outside the recommended range; confirm they are what you "
                      "want, or change them with /personal-ai-trader:trader-setup --limits.")]
    return [check("config", "Config", PASS, detail)]


def check_rules(ctx):
    if ctx.cfg is None:
        return [check("rules", "Config", SKIP, "RULES.md not compared: trader.json is not valid")]
    code, out, err = run([sys.executable, os.path.join(HERE, "render_rules.py"), "--check",
                          "--workspace", ctx.workspace], env=clean_env(), cwd=ctx.workspace)
    if code == 0:
        return [check("rules", "Config", PASS, "RULES.md matches trader.json")]
    if code == 1:
        missing = "is missing" in out
        return [check("rules", "Config", WARN,
                      "RULES.md is missing" if missing else
                      "RULES.md differs from what trader.json generates (the guard follows trader.json)",
                      "Regenerate it: `python3 \"<plugin root>/tools/render_rules.py\" --workspace <workspace>` "
                      "(trader.json is the authority; RULES.md is its human copy).")]
    return [check("rules", "Config", FAIL, "render_rules.py failed: " + first_lines(out + err),
                  "Run render_rules.py --check by hand and fix what it reports.")]


def _hooks_entries(data):
    for entry in (data.get("hooks", {}) or {}).get("PreToolUse", []) if isinstance(data, dict) else []:
        if isinstance(entry, dict):
            yield entry


def hook_matcher(plugin_root=PLUGIN_ROOT):
    """The compiled matcher regex of the guard hook in hooks/hooks.json, or None."""
    try:
        data = read_json(os.path.join(plugin_root, "hooks", "hooks.json"))
    except (OSError, ValueError):
        return None
    for entry in _hooks_entries(data):
        for h in entry.get("hooks", []) or []:
            text = json.dumps(h)
            if "order_guard.py" in text:
                try:
                    return re.compile(entry.get("matcher", ""))
                except re.error:
                    return None
    return None


def check_hook(ctx):
    path = os.path.join(ctx.plugin_root, "hooks", "hooks.json")
    try:
        data = read_json(path)
    except (OSError, ValueError) as e:
        return [check("hook", "Order guard", FAIL, "hooks/hooks.json missing or unreadable (%s)" % e,
                      "Reinstall or update the plugin (/plugin); do not trade until /personal-ai-trader:trader-doctor "
                      "passes.")]
    problems = []
    guard_hooks = []
    matcher = None
    for entry in _hooks_entries(data):
        for h in entry.get("hooks", []) or []:
            if "order_guard.py" in json.dumps(h):
                guard_hooks.append(h)
                matcher = entry.get("matcher", "")
    if not guard_hooks:
        problems.append("no PreToolUse hook runs tools/order_guard.py")
    else:
        try:
            rx = re.compile(matcher)
            for prefix in tool_prefixes(ctx.plugin_name):
                if not rx.search(prefix + "review_option_order"):
                    problems.append("the matcher does not cover %s*" % prefix)
        except re.error as e:
            problems.append("the matcher is not a valid pattern (%s)" % e)
        h = guard_hooks[0]
        if h.get("onFailure") != "block":
            problems.append('the hook does not set "onFailure": "block" (a crash would let calls through)')
    if not os.path.isfile(os.path.join(ctx.plugin_root, "tools", "order_guard.py")):
        problems.append("tools/order_guard.py is missing")
    for name in ("settings.json", "settings.local.json"):
        p = os.path.join(ctx.workspace, ".claude", name)
        if _disables_hooks(p):
            problems.append(".claude/%s sets disableAllHooks" % name)
    if _disables_hooks(os.path.join(ctx.user_config_dir, "settings.json")):
        problems.append("your user settings.json sets disableAllHooks")
    if problems:
        return [check("hook", "Order guard", FAIL, "; ".join(problems),
                      "The guard is not protecting this workspace. Do not trade: reinstall/update the plugin and "
                      "remove any disableAllHooks setting, then run /personal-ai-trader:trader-doctor again.")]
    return [check("hook", "Order guard", PASS,
                  "hooks/hooks.json registers the guard for both tool namings, onFailure block "
                  "(the live preview proof is in the skill)")]


def _disables_hooks(path):
    try:
        return read_json(path).get("disableAllHooks") is True
    except (OSError, ValueError, AttributeError):
        return False


def check_guard_script(ctx):
    py = hook_python()
    if py is None:
        return [check("guard_script", "Order guard", FAIL, "`python3` is not on PATH; the hook cannot run",
                      "Install Python 3.9+ so `python3` is on PATH (the hook command is python3).")]
    code, out, err = run([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    version = out.strip()
    try:
        too_old = tuple(int(x) for x in version.split(".")) < (3, 9)
    except ValueError:
        too_old = True
    if code != 0 or too_old:
        return [check("guard_script", "Order guard", FAIL, "python3 on PATH is %s; the guard needs 3.9+"
                      % (version or "unusable"), "Install Python 3.9 or later as python3.")]
    guard = os.path.join(ctx.plugin_root, "tools", "order_guard.py")
    account = ctx.account_number or "ACCOUNT-NOT-CONFIGURED"
    two_legs = {
        "tool_name": "mcp__%s__review_option_order" % SERVER,
        "tool_input": {"account_number": account, "quantity": 1, "price": 0.10, "direction": "debit",
                       "type": "limit", "time_in_force": "gfd", "market_hours": "regular_hours",
                       "legs": [{"option_id": "doctor-test-a", "side": "buy", "position_effect": "open",
                                 "ratio_quantity": 1},
                                {"option_id": "doctor-test-b", "side": "sell", "position_effect": "open",
                                 "ratio_quantity": 1}]},
    }
    env = clean_env({"CLAUDE_PROJECT_DIR": ctx.workspace})
    code, out, err = run([py, "-I", guard], env=env, cwd=ctx.workspace, stdin=json.dumps(two_legs), timeout=30)
    if code != 2:
        return [check("guard_script", "Order guard", FAIL,
                      "the guard script did not refuse a two-leg preview (exit %s)" % code,
                      "The guard is broken: do not trade. Reinstall the plugin and run the tests.")]
    if ctx.cfg is None:
        return [check("guard_script", "Order guard", WARN,
                      "the guard script refuses calls, but only because trader.json is invalid (fails closed)",
                      "Fix trader.json (see the Config row), then run the doctor again.")]
    if "exactly one leg" not in err:
        return [check("guard_script", "Order guard", FAIL,
                      "the guard refused a two-leg preview for an unexpected reason: " + first_lines(err, 1),
                      "Run the guard tests (python3 -m pytest tests) and fix the reason shown.")]
    # An allowed preview must pass: a buy-to-open limit for one contract at a price inside the cap, or in
    # HALTED (which refuses opening orders) a sell-to-close limit.
    cap = ctx.cfg.limits.option_cap_dollars
    price = float(min(cap / 100 / 2, 1))
    halted = ctx.cfg.phase == "HALTED"
    leg = {"option_id": "doctor-test-a", "side": "sell" if halted else "buy",
           "position_effect": "close" if halted else "open", "ratio_quantity": 1}
    allowed = {
        "tool_name": "mcp__plugin_%s_%s__review_option_order" % (ctx.plugin_name, SERVER),
        "tool_input": {"account_number": account, "quantity": 1, "price": round(price, 2),
                       "direction": "credit" if halted else "debit", "type": "limit", "time_in_force": "gfd",
                       "market_hours": "regular_hours", "legs": [leg]},
    }
    if not ctx.cfg.allowed.long_options and not halted:
        return [check("guard_script", "Order guard", PASS,
                      "the guard script refuses a two-leg preview (allowed-preview test skipped: option buys "
                      "are switched off)")]
    code, out, err = run([py, "-I", guard], env=env, cwd=ctx.workspace, stdin=json.dumps(allowed), timeout=30)
    if code != 0:
        return [check("guard_script", "Order guard", FAIL,
                      "the guard script refused an allowed single-leg preview: " + first_lines(err, 1),
                      "The guard would block every trade; check trader.json and run the guard tests.")]
    return [check("guard_script", "Order guard", PASS,
                  "the guard script refuses a two-leg preview and passes an allowed single-leg preview")]


def check_deny(ctx):
    """Delegates to tools/setup_settings.py --check, which owns the list of rules (one source of truth)."""
    script = os.path.join(ctx.plugin_root, "tools", "setup_settings.py")
    rerun = ("Re-run step 8 of /personal-ai-trader:trader-setup (workspace permissions); it shows a diff "
             "before writing.")
    if not os.path.isfile(script):
        return [check("deny", "Deny list", FAIL, "tools/setup_settings.py is missing from the plugin",
                      "Reinstall or update the plugin (/plugin).")]
    code, out, err = run([sys.executable, script, "--check", "--workspace", ctx.workspace,
                          "--plugin-root", ctx.plugin_root], env=clean_env(), cwd=ctx.workspace)
    lines = [l.strip() for l in (out + err).splitlines() if l.strip()]
    if code == 1:
        missing = [l[2:] for l in lines if l.startswith("- ")]
        head = lines[0] if lines else "deny entries missing"
        if missing:
            head = "%d deny entr%s missing, e.g. %s" % (len(missing), "y" if len(missing) == 1 else "ies",
                                                         ", ".join(missing[:3]))
        elif "does not exist" in head:
            head = ".claude/settings.json is missing"
        return [check("deny", "Deny list", FAIL, head,
                      rerun + " The guard also refuses these tools; the deny list is the second layer.")]
    if code != 0:
        return [check("deny", "Deny list", FAIL, ".claude/settings.json is unreadable: " + first_lines(out + err, 1),
                      "Fix the JSON in .claude/settings.json (or move the file away), then " + rerun[0].lower()
                      + rerun[1:])]
    warns = [l[len("WARN:"):].strip() for l in lines if l.startswith("WARN:")]
    if warns:
        return [check("deny", "Deny list", WARN,
                      "every deny entry present; %d allow/ask rule%s out of date (e.g. after a plugin update moved "
                      "the plugin folder, or a news source was added to or removed from NEWS-SOURCES.md): %s" % (len(warns), "" if len(warns) == 1 else "s", "; ".join(warns[:2])),
                      rerun + " If you chose to approve every scheduled call by hand (no allow list), the "
                      "'allow missing' lines are expected.")]
    return [check("deny", "Deny list", PASS,
                  "crypto preview/place/cancel, crypto onboarding, exercise and its cancel denied in both namings; "
                  "allow and ask rules current")]


def check_python(ctx):
    venv_py = os.path.join(ctx.trading, ".venv", "bin", "python")
    if not os.path.exists(venv_py):
        return [check("python", "Python", FAIL, "trading/.venv is missing",
                      "Create it: `python3 -m venv trading/.venv && trading/.venv/bin/python -m pip install "
                      "openpyxl` (or re-run /personal-ai-trader:trader-setup).")]
    code, out, err = run([venv_py, "-c", "import openpyxl; print(openpyxl.__version__)"])
    if code != 0:
        return [check("python", "Python", FAIL, "openpyxl does not import in trading/.venv",
                      "`trading/.venv/bin/python -m pip install openpyxl`.")]
    ledger_dir = os.path.join(ctx.trading, "ledger")
    csvs = ("trades.csv", "orders.csv", "equity.csv")
    if not all(os.path.isfile(os.path.join(ledger_dir, c)) for c in csvs):
        return [check("python", "Python", WARN, "venv and openpyxl %s OK; the ledger is not initialised" % out.strip(),
                      "`trading/.venv/bin/python \"<plugin root>/tools/ledger.py\" init`.")]
    tmp = tempfile.mkdtemp(prefix="doctor-ledger-")
    try:
        os.makedirs(os.path.join(tmp, "trading", "ledger"))
        for c in csvs:
            shutil.copy2(os.path.join(ledger_dir, c), os.path.join(tmp, "trading", "ledger", c))
        cfg_path = trader_config.config_path(ctx.workspace)
        if os.path.isfile(cfg_path):
            shutil.copy2(cfg_path, os.path.join(tmp, "trading", "trader.json"))
        code, out2, err2 = run([venv_py, os.path.join(HERE, "ledger.py"), "build", "--workspace", tmp],
                               env=clean_env(), cwd=tmp)
        built = os.path.isfile(os.path.join(tmp, "trading", "ledger", "Trades.xlsx"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if code != 0 or not built:
        return [check("python", "Python", FAIL, "`ledger.py build` failed on a copy: " + first_lines(err2 + out2, 2),
                      "Fix the ledger CSVs it names (they are the source of truth), then build again.")]
    return [check("python", "Python", PASS, "venv OK, openpyxl %s, ledger build works (tested on a copy)"
                  % out.strip())]


def check_files(ctx):
    if not os.path.isdir(ctx.trading):
        return [check("files", "Files", FAIL, "trading/ is missing", "Run /personal-ai-trader:trader-setup in this folder.")]
    bad = []
    for d in REQUIRED_DIRS:
        p = os.path.join(ctx.trading, d)
        if not os.path.isdir(p):
            bad.append("trading/%s/ missing" % d)
        elif not os.access(p, os.W_OK | os.X_OK):
            bad.append("trading/%s/ not writable" % d)
    if not os.access(ctx.trading, os.W_OK | os.X_OK):
        bad.append("trading/ not writable")
    ledger_dir = os.path.join(ctx.trading, "ledger")
    for c in ("trades.csv", "orders.csv", "equity.csv"):
        p = os.path.join(ledger_dir, c)
        if os.path.exists(p) and not os.access(p, os.W_OK):
            bad.append("trading/ledger/%s read-only" % c)
    missing_files = [f for f in EXPECTED_FILES if not os.path.isfile(os.path.join(ctx.trading, f))]
    if bad:
        return [check("files", "Files", FAIL, "; ".join(bad + ["missing " + f for f in missing_files]),
                      "Create the missing folders (`mkdir -p trading/journal trading/ledger trading/reviews "
                      "trading/logs`) and make them writable by your user (`chmod u+w`), or re-run setup.")]
    if missing_files:
        return [check("files", "Files", WARN, "missing: " + ", ".join("trading/" + f for f in missing_files),
                      "Re-run /personal-ai-trader:trader-setup; it re-creates missing files without overwriting.")]
    return [check("files", "Files", PASS, "journal, ledger, reviews, logs present and writable; STATE, PLAN, "
                  "STRATEGY and RULES present")]


def strategy_problems(text):
    """Problems with a STRATEGY.md text, as a list of strings (empty: the routines can read it)."""
    problems = []
    lines = text.splitlines()
    heads = [l.strip() for l in lines if l.startswith("#")]
    for section in STRATEGY_SECTIONS:
        found = any(h.startswith(section) for h in heads) if section.startswith("#") else \
            any(h.lstrip("#").strip().startswith(section) for h in heads)
        if not found:
            problems.append("no '%s' section" % section)
    # Numbered setups under "## Setups in play".
    in_setups, setups = False, 0
    for line in lines:
        if line.startswith("## "):
            in_setups = line.startswith("## Setups in play")
            continue
        if line.startswith("### "):
            in_setups = in_setups and not line.startswith("### Not in play")
            continue
        if in_setups and re.match(r"^\d+\.\s+\*\*.+?\*\*", line):
            setups += 1
    if setups == 0:
        problems.append("no numbered setups ('1. **Name.** ...') under '## Setups in play'")
    return problems


def check_strategy(ctx):
    path = os.path.join(ctx.trading, "STRATEGY.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return [check("strategy", "Strategy", WARN, "trading/STRATEGY.md is missing",
                      "Run /personal-ai-trader:trader-strategy. Without it no entry can cite a named setup.")]
    except (OSError, UnicodeDecodeError) as e:
        return [check("strategy", "Strategy", FAIL, "trading/STRATEGY.md is unreadable (%s)" % e,
                      "Fix the file's permissions or encoding, or rewrite it with /personal-ai-trader:trader-strategy.")]
    problems = strategy_problems(text)
    if problems:
        return [check("strategy", "Strategy", WARN, "STRATEGY.md format: " + "; ".join(problems),
                      "Re-run /personal-ai-trader:trader-strategy, or restore the missing sections from "
                      "templates/STRATEGY.sample.md; the routines look for these headings.")]
    if SAMPLE_BANNER in text:
        return [check("strategy", "Strategy", WARN, "STRATEGY.md is still the shipped sample",
                      "Run /personal-ai-trader:trader-strategy to write your own setups (the sample is not advice).")]
    return [check("strategy", "Strategy", PASS, "STRATEGY.md has every section and %s"
                  % _setup_count_text(text))]


def _setup_count_text(text):
    n = sum(1 for l in text.split("## Setups in play", 1)[-1].split("\n## ", 1)[0].splitlines()
            if re.match(r"^\d+\.\s+\*\*", l))
    return "%d numbered setup%s" % (n, "" if n == 1 else "s")


def check_state(ctx):
    path = os.path.join(ctx.trading, "STATE.md")
    if not os.path.isfile(path):
        return [check("state", "State", FAIL, "trading/STATE.md is missing",
                      "Re-run /personal-ai-trader:trader-setup (it writes STATE.md); the routines and a resumed "
                      "session depend on it.")]
    if not (ctx.tz and ctx.calendar):
        return [check("state", "State", SKIP, "no usable timezone, so the trading-day window is unknown")]
    mtime = datetime.fromtimestamp(os.path.getmtime(path), ctx.tz)
    age_min = int((ctx.now - mtime).total_seconds() // 60)
    runs = ctx.runs_today()
    if not runs:
        return [check("state", "State", PASS, "not a trading day; STATE.md last written %s"
                      % mtime.strftime("%Y-%m-%d %H:%M"))]
    first, last = ctx.local_dt(runs[0]), ctx.local_dt(runs[-1])
    # The window opens when the first check after prep is due (prep itself writes STATE.md, so allow it to
    # run) and closes a little after the wrap.
    window_start = first + timedelta(minutes=STATE_STALE_MINUTES)
    window_end = last + timedelta(minutes=30)
    if not (window_start <= ctx.now <= window_end):
        return [check("state", "State", PASS, "outside today's run window; STATE.md last written %s"
                      % mtime.strftime("%Y-%m-%d %H:%M"))]
    if age_min <= STATE_STALE_MINUTES:
        return [check("state", "State", PASS, "STATE.md rewritten %d min ago" % max(age_min, 0))]
    window = ctx.market_window()
    in_market = window is not None and window[0] <= ctx.now <= window[1]
    return [check("state", "State", FAIL if in_market else WARN,
                  "STATE.md not rewritten for %d min on a trading day (last %s): the scheduled runs have "
                  "probably stopped" % (age_min, mtime.strftime("%H:%M")),
                  "Check that the session is open and idle and that today's runs exist (the skill compares "
                  "CronList with schedule.py and offers to re-create missing ones); see docs/KEEP-RUNNING.md.")]


def check_watcher(ctx):
    trades = ctx.open_trades()
    window = ctx.market_window()
    if not trades:
        return [check("watcher", "Watcher", PASS, "no open positions; the watcher is not needed")]
    if window is None or not (window[0] <= ctx.now <= window[1]):
        return [check("watcher", "Watcher", PASS, "%d open position(s), outside market hours" % len(trades))]
    path = os.path.join(ctx.trading, "logs", "watch.log")
    fix = ("Restart it from the session (the check skill, section 6a, has the command and the levels in "
           "STATE.md \"Price watcher\"). It is optional; without it the hourly checks and resting stops remain.")
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
    except (OSError, UnicodeDecodeError):
        lines = []
    if not lines:
        return [check("watcher", "Watcher", WARN, "%d open position(s) in market hours and no watch.log" % len(trades),
                      fix)]
    last = lines[-1]
    try:
        stamp = datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ctx.tz)
    except ValueError:
        return [check("watcher", "Watcher", WARN, "the last watch.log line has no timestamp", fix)]
    age = (ctx.now - stamp).total_seconds() / 60
    if last[20:].startswith("EXIT"):
        return [check("watcher", "Watcher", WARN, "the watcher exited at %s (%s) and was not restarted"
                      % (stamp.strftime("%H:%M"), last[25:][:60]), fix)]
    if age > WATCH_STALE_MINUTES:
        return [check("watcher", "Watcher", WARN, "last watcher poll %d min ago; it is probably not running"
                      % int(age), fix)]
    return [check("watcher", "Watcher", PASS, "last poll %d min ago" % max(int(age), 0))]


def check_version(ctx):
    schema = ctx.raw_cfg.get("schema") if isinstance(ctx.raw_cfg, dict) else None
    want = trader_config.SCHEMA_VERSION
    base = "plugin %s, workspace schema %s" % (ctx.plugin_version, schema)
    if schema is None or isinstance(schema, bool) or not isinstance(schema, int):
        return [check("version", "Version", SKIP, base + " (no readable schema in trader.json)")]
    if schema < want:
        return [check("version", "Version", FAIL, base + "; this plugin expects schema %d: migration pending" % want,
                      "Run /personal-ai-trader:trader-setup; it migrates trader.json forward and says what changed.")]
    if schema > want:
        return [check("version", "Version", FAIL, base + "; written by a newer plugin (expects %d)" % want,
                      "Update the plugin (/plugin, then update personal-ai-trader).")]
    return [check("version", "Version", PASS, base)]


def check_calendar(ctx):
    cal = ctx.calendar
    if cal is None:
        return [check("calendar", "Calendar", FAIL, "tools/market_calendar.json is missing or unreadable",
                      "Reinstall or update the plugin.")]
    today = ctx.today
    end, start = cal["_to"], cal["_from"]
    if today < start or today > end:
        return [check("calendar", "Calendar", FAIL,
                      "the market calendar covers %s to %s; today is outside it, so holidays and early closes are "
                      "unknown and the schedule treats every weekday as a full trading day" % (start, end),
                      "Update the plugin (/plugin), or extend tools/market_calendar.json from "
                      "https://www.nyse.com/markets/hours-calendars.")]
    left = (end - today).days
    if left <= CALENDAR_WARN_DAYS:
        return [check("calendar", "Calendar", WARN, "the market calendar ends %s, %d days from now" % (end, left),
                      "Update the plugin before then, or extend tools/market_calendar.json from "
                      "https://www.nyse.com/markets/hours-calendars.")]
    return [check("calendar", "Calendar", PASS, "covers %s to %s (%d days left)" % (start, end, left))]


def classify_tool_names(names, plugin_root=PLUGIN_ROOT):
    """For each tool name that looks like a Robinhood tool: whether the guard hook's matcher covers it."""
    rx = hook_matcher(plugin_root)
    out = []
    for n in names:
        n = n.strip()
        if not n or "robinhood" not in n.lower():
            continue
        covered = bool(rx and rx.search(n))
        if n.startswith("mcp__%s__" % SERVER):
            naming = "user-connected (%s)" % SERVER
        elif re.match(r"^mcp__plugin_.+_%s__" % re.escape(SERVER), n):
            naming = "plugin (%s)" % SERVER
        else:
            naming = "unrecognised"
        out.append({"tool": n, "covered_by_guard": covered, "naming": naming})
    return out


def check_connector(classified):
    if not classified:
        return [check("connector", "Robinhood connection", FAIL, "no Robinhood tools in this session",
                      "Run /mcp, pick robinhood-trading and sign in through Robinhood's page.")]
    uncovered = sorted({c["tool"] for c in classified if not c["covered_by_guard"]})
    if uncovered:
        return [check("connector", "Robinhood connection", FAIL,
                      "Robinhood tools the order guard does NOT cover: " + ", ".join(uncovered[:4]),
                      "Remove that connection (in /mcp, or the connector settings) and use only the plugin's "
                      "robinhood-trading server. Do not trade until this passes.")]
    namings = sorted({c["naming"] for c in classified})
    if len(namings) > 1:
        return [check("connector", "Robinhood connection", WARN,
                      "Robinhood is connected twice (%s); the guard covers both" % ", ".join(namings),
                      "Keep one: remove the extra robinhood-trading entry in /mcp so the deny list and the "
                      "sign-in are not doubled.")]
    return [check("connector", "Robinhood connection", PASS, "connected as %s; covered by the guard" % namings[0])]


CHECKS = {
    "config": check_config, "rules": check_rules, "hook": check_hook, "guard_script": check_guard_script,
    "deny": check_deny, "python": check_python, "files": check_files, "strategy": check_strategy,
    "state": check_state, "watcher": check_watcher, "version": check_version, "calendar": check_calendar,
}


# ---------------------------------------------------------------- schedules and the report

def expected_schedules(ctx):
    """What CronList should hold right now, for the skill to compare."""
    if not (ctx.tz and ctx.calendar):
        return None
    today_runs = ctx.runs_today()
    remaining = [r for r in today_runs if ctx.local_dt(r) > ctx.now]
    wrap_passed = bool(today_runs) and ctx.local_dt(today_runs[-1]) <= ctx.now
    nxt_day = schedule.next_trading_day(ctx.today, ctx.calendar)
    nxt = schedule.runs_for_day(nxt_day, ctx.tz, ctx.calendar, ctx.schedule_offset)
    sunday_time = ctx.cfg.sunday_review_local if ctx.cfg else trader_config.DEFAULT_SUNDAY_REVIEW_LOCAL
    review = schedule.sunday_review_run(ctx.today, ctx.tz, sunday_time) if ctx.today.weekday() == 6 else None
    if review is None or ctx.local_dt(review) <= ctx.now:
        review = schedule.sunday_review_run(schedule.next_sunday(ctx.today), ctx.tz, sunday_time)
    return {
        "today_remaining": {"required": True, "runs": remaining},
        "next_trading_day": {"date": nxt_day.isoformat(),
                             "required": (not today_runs) or wrap_passed,
                             "note": "created by today's wrap" if today_runs and not wrap_passed else "",
                             "runs": nxt},
        "sunday_review": {"required": True, "runs": [review]},
    }


def open_trade_summary(trades):
    keep = ("trade_id", "mode", "underlying", "instrument", "strike", "expiry", "quantity", "entry_price",
            "planned_stop", "planned_target", "option_id", "opened_at")
    return [{k: t.get(k, "") for k in keep if t.get(k)} for t in trades]


def run_checks(ctx, only=None):
    results = []
    for cid in CHECK_IDS:
        if only and cid not in only:
            continue
        try:
            results.extend(CHECKS[cid](ctx))
        except Exception as e:  # a doctor bug must not hide the other checks
            results.append(check(cid, cid, FAIL, "the doctor's own check crashed (%s: %s)" % (type(e).__name__, e),
                                 "Report this with the support bundle."))
    return results


def sort_results(results):
    return sorted(results, key=lambda r: SEVERITY.get(r["status"], 9))


def report(ctx, results):
    counts = {s: sum(1 for r in results if r["status"] == s) for s in (PASS, WARN, FAIL, SKIP)}
    window = ctx.market_window()
    return {
        "doctor": "personal-ai-trader",
        "plugin_version": ctx.plugin_version,
        "workspace": ctx.workspace,
        "generated_at": ctx.now.strftime("%Y-%m-%d %H:%M"),
        "timezone": ctx.tz_name,
        "phase": ctx.cfg.phase if ctx.cfg else None,
        "account_masked": mask_account(ctx.account_number) if ctx.account_number else None,
        "trading_day": ctx.trading_day() if ctx.calendar else None,
        "market_hours_local": [window[0].strftime("%H:%M"), window[1].strftime("%H:%M")] if window else None,
        "summary": counts,
        "checks": sort_results(results),
        "open_trades": open_trade_summary(ctx.open_trades()),
        "expected_schedules": expected_schedules(ctx),
    }


def table(results):
    rows = [("Status", "Area", "Check", "Detail")] + [(r["status"], r["area"], r["id"], r["detail"])
                                                      for r in sort_results(results)]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    w2 = max(len(r[2]) for r in rows)
    out = []
    for i, r in enumerate(rows):
        out.append("%-*s  %-*s  %-*s  %s" % (w0, r[0], w1, r[1], w2, r[2], r[3]))
        if i == 0:
            out.append("-" * (w0 + w1 + w2 + 6 + 40))
    fixes = [r for r in sort_results(results) if r["status"] in (FAIL, WARN) and r["fix"]]
    if fixes:
        out.append("")
        out.append("Fixes:")
        for r in fixes:
            out.append("- %s %s (%s): %s" % (r["status"], r["area"], r["id"], r["fix"]))
    return "\n".join(out)


# ---------------------------------------------------------------- support bundle

def last_journal_entry(trading):
    folder = os.path.join(trading, "journal")
    try:
        files = sorted(f for f in os.listdir(folder) if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", f))
    except OSError:
        return None, None
    if not files:
        return None, None
    with open(os.path.join(folder, files[-1]), encoding="utf-8", errors="replace") as f:
        text = f.read()
    parts = re.split(r"(?m)^(?=## )", text)
    entry = parts[-1].strip() if len(parts) > 1 else text.strip()
    return files[-1], entry


def masked_config(ctx):
    if not isinstance(ctx.raw_cfg, dict):
        return None
    data = json.loads(json.dumps(ctx.raw_cfg))
    if isinstance(data.get("account"), dict) and "number" in data["account"]:
        data["account"]["number"] = mask_account(data["account"]["number"])
    return data


def bundle_text(ctx, rep, skill_results):
    lines = ["# Personal AI Trader doctor — %s" % rep["generated_at"], "",
             "- Plugin version: %s" % rep["plugin_version"],
             "- Phase: %s; account %s; timezone %s" % (rep["phase"], rep["account_masked"], rep["timezone"]),
             "- Summary: %s" % ", ".join("%s %d" % (k, v) for k, v in rep["summary"].items()), "",
             "## Checks", "", "| Status | Area | Check | Detail | Fix |", "|---|---|---|---|---|"]
    for r in rep["checks"]:
        lines.append("| %s | %s | %s | %s | %s |" % tuple(str(r.get(k, "")).replace("|", "/").replace("\n", " ")
                                                       for k in ("status", "area", "id", "detail", "fix")))
    lines += ["", "Rows from the skill's own checks (Robinhood, guard proof, schedules, reconcile) are marked "
              "`skill:`." if skill_results else "Only the script checks ran (no skill results were given).", ""]
    lines += ["## Open trades in the ledger", "", "```", json.dumps(rep["open_trades"], indent=2), "```", ""]
    lines += ["## Config (account number masked)", "", "```json", json.dumps(masked_config(ctx), indent=2), "```", ""]
    name, entry = last_journal_entry(ctx.trading)
    lines += ["## Last journal entry" + (" (%s)" % name if name else ""), ""]
    lines += [entry if entry else "(no journal yet)", ""]
    lines += ["---", "No tokens, passwords or full account numbers are included. Read it before you send it."]
    return "\n".join(lines) + "\n"


def write_bundle(ctx, rep, skill_results):
    folder = os.path.join(ctx.trading, "logs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "doctor-%s.md" % ctx.today.isoformat())
    text = scrub(bundle_text(ctx, rep, skill_results), [ctx.account_number or ""], home=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def normalise_skill_results(data):
    if not isinstance(data, list):
        raise ValueError("skill results must be a JSON list")
    out = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each skill result must be an object")
        status = str(item.get("status", "")).upper()
        if status not in SEVERITY:
            raise ValueError("status must be PASS, WARN, FAIL or SKIP (got %r)" % item.get("status"))
        out.append(check("skill:" + str(item.get("check") or item.get("id") or "check"),
                         str(item.get("area") or "Skill"), status, str(item.get("detail", "")),
                         str(item.get("fix", ""))))
    return out


def read_input(spec):
    if spec == "-":
        return sys.stdin.read()
    with open(spec, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- main

def parse_now(text, tz):
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise SystemExit("doctor: --now is YYYY-MM-DDTHH:MM (got %r)" % text)
    return dt.replace(tzinfo=tz) if tz else dt


def main(argv=None):
    p = argparse.ArgumentParser(description="Personal AI Trader health checks that need no Claude tool.")
    p.add_argument("--workspace")
    p.add_argument("--format", choices=("table", "json", "both"), default="table")
    p.add_argument("--only", help="comma-separated check ids: " + ",".join(CHECK_IDS))
    p.add_argument("--now", help="local time YYYY-MM-DDTHH:MM in trader.json's timezone")
    p.add_argument("--timezone", help="override the timezone (when trader.json is unusable)")
    p.add_argument("--bundle", action="store_true", help="write trading/logs/doctor-YYYY-MM-DD.md")
    p.add_argument("--skill-results", metavar="FILE|-")
    p.add_argument("--tool-names", metavar="FILE|-")
    a = p.parse_args(argv)

    only = None
    if a.only:
        only = {x.strip() for x in a.only.split(",") if x.strip()}
        unknown = only - set(CHECK_IDS) - {"connector"}
        if unknown:
            p.error("unknown check id(s): %s" % ", ".join(sorted(unknown)))
    if a.skill_results == "-" and a.tool_names == "-":
        p.error("only one of --skill-results and --tool-names can read stdin")
    try:
        workspace = trader_config.find_workspace(a.workspace)
    except trader_config.ConfigError as e:
        print("doctor: cannot find the workspace (%s); pass --workspace PATH" % "; ".join(e.problems))
        return 2

    ctx = Context(workspace, timezone=a.timezone)
    if a.now:
        ctx.now = parse_now(a.now, ctx.tz)
    results = run_checks(ctx, only)

    if a.tool_names:
        raw = read_input(a.tool_names)
        try:
            names = json.loads(raw)
            if not isinstance(names, list):
                raise ValueError
        except ValueError:
            names = raw.splitlines()
        classified = classify_tool_names([str(n) for n in names], ctx.plugin_root)
        results.extend(check_connector(classified))
    else:
        classified = None

    skill_results = []
    if a.skill_results:
        try:
            skill_results = normalise_skill_results(json.loads(read_input(a.skill_results)))
        except ValueError as e:
            print("doctor: bad --skill-results (%s)" % e)
            return 2
        results.extend(skill_results)

    rep = report(ctx, results)
    if classified is not None:
        rep["robinhood_tools"] = classified
    if a.bundle:
        rep["bundle"] = write_bundle(ctx, rep, skill_results)

    secrets = [ctx.account_number or ""]
    if a.format in ("json", "both"):
        print(scrub(json.dumps(rep, indent=2), secrets))
    if a.format in ("table", "both"):
        if a.format == "both":
            print()
        print(scrub(table(results), secrets))
        if a.bundle:
            print("\nSupport bundle: %s" % scrub(rep["bundle"]))
    return 1 if rep["summary"][FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
