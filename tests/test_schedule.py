"""tools/schedule.py: the one-off run table, calendar, timezones and DST."""
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from conftest import TOOLS, base_config

sys.path.insert(0, TOOLS)
import schedule as sch  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

SCRIPT = os.path.join(TOOLS, "schedule.py")
CAL = sch.load_calendar()
LA, NY = ZoneInfo("America/Los_Angeles"), ZoneInfo("America/New_York")

LA_REGULAR = ["06:05", "06:36", "07:33", "08:33", "09:33", "10:33", "11:33", "12:12", "12:56", "13:16"]
NY_REGULAR = ["09:05", "09:36", "10:33", "11:33", "12:33", "13:33", "14:33", "15:12", "15:56", "16:16"]
RUN_NAMES = ["prep", "first_check"] + ["hourly"] * 5 + ["preclose", "closing_mark", "wrap"]


def times(runs):
    return [r["local_time"] for r in runs]


def run_cli(*args, ws=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if ws:
        env["CLAUDE_PROJECT_DIR"] = ws
    p = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, env=env,
                       cwd=TOOLS, timeout=30)
    return p


def make_ws(tmp_path, tz="America/Los_Angeles", sunday=None, offset=None):
    cfg = base_config("DRY_RUN")
    cfg["timezone"] = tz
    if sunday:
        cfg["schedule"] = {"sunday_review_local": sunday}
    if offset is not None:
        cfg.setdefault("schedule", {})["offset_minutes"] = offset
    os.makedirs(tmp_path / "trading", exist_ok=True)
    (tmp_path / "trading" / "trader.json").write_text(json.dumps(cfg))
    return str(tmp_path)


def test_normal_day_los_angeles():
    runs = sch.runs_for_day(date(2026, 11, 4), LA, CAL)
    assert times(runs) == LA_REGULAR
    assert [r["run"] for r in runs] == RUN_NAMES
    assert runs[0]["cron"] == "5 6 4 11 *"
    assert runs[-1]["cron"] == "16 13 4 11 *"
    assert all(r["local_date"] == "2026-11-04" for r in runs)


def test_normal_day_new_york():
    runs = sch.runs_for_day(date(2026, 11, 4), NY, CAL)
    assert times(runs) == NY_REGULAR
    assert runs[7]["cron"] == "12 15 4 11 *"


@pytest.mark.parametrize("day", [date(2026, 11, 7), date(2026, 11, 8)])
def test_weekend_is_empty(day):
    assert sch.runs_for_day(day, LA, CAL) == []


@pytest.mark.parametrize("day", [date(2026, 11, 26), date(2026, 12, 25), date(2027, 3, 26), date(2027, 7, 5),
                                 date(2027, 12, 24)])
def test_holiday_is_empty(day):
    assert sch.runs_for_day(day, LA, CAL) == []


def test_early_close_day():
    runs = sch.runs_for_day(date(2026, 11, 27), LA, CAL)
    assert [r["run"] for r in runs] == ["prep", "first_check", "hourly", "hourly", "preclose", "closing_mark", "wrap"]
    assert times(runs) == ["06:05", "06:36", "07:33", "08:33", "09:12", "09:56", "10:16"]
    assert times(sch.runs_for_day(date(2026, 12, 24), NY, CAL)) == \
        ["09:05", "09:36", "10:33", "11:33", "12:12", "12:56", "13:16"]


@pytest.mark.parametrize("before,after", [
    (date(2026, 10, 30), date(2026, 11, 2)),   # US DST ends Sunday 2026-11-01 (first Sunday in November)
    (date(2027, 3, 12), date(2027, 3, 15)),    # US DST starts Sunday 2027-03-14 (second Sunday in March)
    (date(2026, 3, 6), date(2026, 3, 9)),      # and 2026-03-08
])
def test_dst_transitions_us_zones_keep_local_times(before, after):
    for tz, expected in ((LA, LA_REGULAR), (NY, NY_REGULAR)):
        assert times(sch.runs_for_day(before, tz, CAL)) == expected
        assert times(sch.runs_for_day(after, tz, CAL)) == expected
    # ...while the instant moves by an hour (seen from UTC).
    utc = ZoneInfo("UTC")
    b, a = sch.runs_for_day(before, utc, CAL)[0]["local_time"], sch.runs_for_day(after, utc, CAL)[0]["local_time"]
    assert {b, a} == {"13:05", "14:05"}


def test_dst_mismatch_week_in_london():
    # UK clocks go back 2026-10-25, US clocks 2026-11-01: that week London is only 4 hours ahead of New York.
    assert sch.runs_for_day(date(2026, 10, 27), ZoneInfo("Europe/London"), CAL)[0]["local_time"] == "13:05"
    assert sch.runs_for_day(date(2026, 11, 3), ZoneInfo("Europe/London"), CAL)[0]["local_time"] == "14:05"


def test_far_timezone_pins_the_local_date():
    runs = sch.runs_for_day(date(2026, 11, 4), ZoneInfo("Asia/Tokyo"), CAL)
    wrap = runs[-1]
    assert wrap["local_date"] == "2026-11-05" and wrap["local_time"] == "06:16"
    assert wrap["cron"] == "16 6 5 11 *"


@pytest.mark.parametrize("tz", ["America/Los_Angeles", "America/New_York", "America/Chicago", "America/Denver",
                                "Europe/London", "Asia/Kolkata", "Asia/Kathmandu", "Australia/Adelaide"])
def test_minutes_stay_off_the_hour_and_half_hour(tz):
    for day in (date(2026, 11, 4), date(2026, 11, 27), date(2027, 6, 15)):
        for r in sch.runs_for_day(day, ZoneInfo(tz), CAL):
            assert int(r["local_time"][3:]) not in (0, 30), (tz, r)


def test_next_trading_day_across_holiday_weekends():
    assert sch.next_trading_day(date(2027, 1, 15), CAL) == date(2027, 1, 19)   # MLK Monday
    assert sch.next_trading_day(date(2026, 7, 2), CAL) == date(2026, 7, 6)     # Fri Jul 3 observed
    assert sch.next_trading_day(date(2026, 11, 25), CAL) == date(2026, 11, 27)  # Thanksgiving -> early close
    assert sch.next_trading_day(date(2026, 11, 6), CAL) == date(2026, 11, 9)   # plain weekend
    assert sch.next_trading_day(date(2026, 11, 4), CAL) == date(2026, 11, 5)


def test_sunday_review():
    run = sch.sunday_review_run(sch.next_sunday(date(2026, 11, 6)), LA)
    assert run["run"] == "sunday_review"
    assert (run["local_date"], run["local_time"], run["cron"]) == ("2026-11-08", "15:03", "3 15 8 11 *")
    assert sch.next_sunday(date(2026, 11, 8)) == date(2026, 11, 15)   # strictly after
    assert sch.next_sunday(date(2026, 11, 7)) == date(2026, 11, 8)


def test_prompts():
    for run, prompt in sch.PROMPTS.items():
        assert prompt.startswith("Personal AI Trader scheduled run: ")
        assert prompt.splitlines()[0].endswith(".")
    assert "personal-ai-trader:trader-check with argument prep" in sch.PROMPTS["prep"]
    assert "personal-ai-trader:trader-check with argument hourly" in sch.PROMPTS["first_check"]
    assert "personal-ai-trader:trader-check with argument hourly" in sch.PROMPTS["hourly"]
    assert "personal-ai-trader:trader-check with argument preclose" in sch.PROMPTS["preclose"]
    assert "personal-ai-trader:trader-wrap" in sch.PROMPTS["wrap"]
    assert "personal-ai-trader:trader-weekly-review" in sch.PROMPTS["sunday_review"]
    mark = sch.PROMPTS["closing_mark"]
    assert "Skill" not in mark and "STATE.md" in mark and "Place no orders" in mark and "Closing mark" in mark


def test_calendar_cites_nyse_and_covers_2026_2027():
    assert "nyse.com/markets/hours-calendars" in CAL["source"]
    assert CAL["covers_from"] == "2026-01-01" and CAL["covers_to"] == "2027-12-31"
    assert len([d for d in CAL["holidays"] if d.startswith("2026")]) == 10
    assert len([d for d in CAL["holidays"] if d.startswith("2027")]) == 10
    for d in list(CAL["holidays"]) + list(CAL["early_closes"]):
        assert date.fromisoformat(d).weekday() < 5, d


# ---------------------------------------------------------------- CLI

def test_cli_day_from_config(tmp_path):
    ws = make_ws(tmp_path, tz="America/New_York")
    p = run_cli("--day", "2026-11-04", ws=ws)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert times(out) == NY_REGULAR
    assert set(out[0]) == {"run", "market_date", "local_date", "local_time", "cron", "prompt"}
    assert p.stderr == ""


def test_cli_weekend_and_holiday_print_empty_list(tmp_path):
    ws = make_ws(tmp_path)
    assert json.loads(run_cli("--day", "2026-11-07", ws=ws).stdout) == []
    assert json.loads(run_cli("--day", "2026-12-25", "--workspace", ws).stdout) == []


def test_cli_next_trading_day(tmp_path):
    ws = make_ws(tmp_path)
    out = json.loads(run_cli("--next-trading-day", "--after", "2026-11-25", ws=ws).stdout)
    assert len(out) == 7 and {r["market_date"] for r in out} == {"2026-11-27"}
    out = json.loads(run_cli("--next-trading-day", "--after", "2027-01-15", ws=ws).stdout)
    assert len(out) == 10 and out[0]["market_date"] == "2027-01-19" and out[0]["cron"] == "5 6 19 1 *"


def test_cli_sunday_review_uses_configured_time(tmp_path):
    ws = make_ws(tmp_path, sunday="16:07")
    (run,) = json.loads(run_cli("--sunday-review", "--after", "2026-11-06", ws=ws).stdout)
    assert (run["local_time"], run["cron"]) == ("16:07", "7 16 8 11 *")


def test_cli_warns_outside_calendar(tmp_path):
    ws = make_ws(tmp_path)
    p = run_cli("--day", "2028-01-04", ws=ws)
    assert p.returncode == 0
    assert len(json.loads(p.stdout)) == 10
    assert "outside it" in p.stderr and "market_calendar.json" in p.stderr


def test_cli_timezone_override_without_config(tmp_path):
    p = subprocess.run([sys.executable, SCRIPT, "--day", "2026-11-04", "--timezone", "America/Chicago"],
                       capture_output=True, text=True, cwd=str(tmp_path),
                       env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)[0]["local_time"] == "08:05"


def test_cli_bad_inputs(tmp_path):
    ws = make_ws(tmp_path)
    assert run_cli("--day", "11/04/2026", ws=ws).returncode != 0
    assert run_cli("--day", "2026-11-04", "--timezone", "Mars/Olympus", ws=ws).returncode != 0
    assert run_cli(ws=ws).returncode != 0
    assert run_cli("--day", "2026-11-04", "--after", "2026-11-01", ws=ws).returncode != 0


def test_config_rejects_bad_sunday_time():
    import trader_config as tc
    cfg = base_config("DRY_RUN")
    cfg["schedule"] = {"sunday_review_local": "3pm"}
    with pytest.raises(tc.ConfigError) as e:
        tc.validate(cfg)
    assert "schedule.sunday_review_local must be HH:MM" in str(e.value)
    cfg["schedule"] = {}
    assert tc.validate(cfg).sunday_review_local == "15:03"


# ---------------------------------------------------------------- --session (used by the skills)

def test_session_regular_early_and_closed():
    assert sch.session(date(2026, 11, 4), LA, CAL) == {
        "date": "2026-11-04", "trading_day": True, "early_close": False,
        "open_local": "06:30", "close_local": "13:00"}
    assert sch.session(date(2026, 11, 27), NY, CAL) == {
        "date": "2026-11-27", "trading_day": True, "early_close": True,
        "open_local": "09:30", "close_local": "13:00"}
    for closed in (date(2026, 11, 26), date(2026, 11, 28)):
        s = sch.session(closed, LA, CAL)
        assert s["trading_day"] is False and s["early_close"] is False
        assert s["open_local"] is None and s["close_local"] is None


def test_cli_session(tmp_path):
    ws = make_ws(tmp_path)
    out = json.loads(run_cli("--session", "2026-12-24", ws=ws).stdout)
    assert out == {"date": "2026-12-24", "trading_day": True, "early_close": True,
                   "open_local": "06:30", "close_local": "10:00"}
    out = json.loads(run_cli("--session", "2026-12-25", ws=ws).stdout)
    assert out["trading_day"] is False and out["close_local"] is None
    assert run_cli("--session", "2026-12-24", "--after", "2026-12-01", ws=ws).returncode != 0


def test_cli_day_closed_days_are_exactly_empty_lists(tmp_path):
    # trader-check detects a closed day by `--day` printing [] (weekend or holiday).
    ws = make_ws(tmp_path)
    for day in ("2026-11-07", "2026-11-08", "2026-11-26", "2027-01-18"):
        p = run_cli("--day", day, ws=ws)
        assert p.returncode == 0 and p.stdout.strip() == "[]", day


@pytest.mark.parametrize("offset", [1, 2, 3])
def test_offset_moves_every_run_and_stays_inside_the_session(offset):
    base = sch.runs_for_day(date(2026, 11, 4), NY, CAL)
    moved = sch.runs_for_day(date(2026, 11, 4), NY, CAL, offset)
    assert [r["run"] for r in moved] == [r["run"] for r in base]
    for b, m in zip(base, moved):
        bh, bm = map(int, b["local_time"].split(":"))
        mh, mm = map(int, m["local_time"].split(":"))
        assert (mh * 60 + mm) - (bh * 60 + bm) == offset
        assert mm not in (0, 30)
    marks = {r["run"]: r["local_time"] for r in moved}
    assert marks["closing_mark"] < "16:00" and marks["first_check"] > "09:30"
    early = {r["run"]: r["local_time"] for r in sch.runs_for_day(date(2026, 11, 27), NY, CAL, offset)}
    assert early["closing_mark"] < "13:00"


def test_offset_out_of_range_is_refused():
    with pytest.raises(ValueError):
        sch.runs_for_day(date(2026, 11, 4), NY, CAL, 4)


def test_cli_uses_the_configured_offset(tmp_path):
    ws = make_ws(tmp_path, offset=3)
    p = run_cli("--day", "2026-11-04", ws=ws)
    assert p.returncode == 0, p.stderr
    runs = json.loads(p.stdout)
    assert runs[0]["local_time"] == "06:08" and runs[0]["cron"] == "8 6 4 11 *"
    assert [r["local_time"] for r in runs if r["run"] == "hourly"][0] == "07:36"
