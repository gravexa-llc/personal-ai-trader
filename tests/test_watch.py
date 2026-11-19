"""tools/watch.py: rule and argument parsing, and the one-watcher-per-workspace record (no network)."""
import sys

import pytest

from conftest import TOOLS

sys.path.insert(0, TOOLS)
import watch  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("XYZ above 101.25", ("XYZ", "above", 101.25)),
    ("xyz below 95.5", ("XYZ", "below", 95.5)),
    ("BRK.B above 500", ("BRK.B", "above", 500.0)),
    ("  DEF   BELOW  40 ", ("DEF", "below", 40.0)),
])
def test_parse_rule(text, expected):
    assert watch.parse_rule(text) == expected


@pytest.mark.parametrize("text", [
    "XYZ over 101", "XYZ above", "XYZ above 101 now", "XYZ above abc", "XYZ above -5", "XYZ above 0",
    "XYZ above inf", "XYZ above nan", "$$$ above 5", "",
])
def test_parse_rule_rejects(text):
    with pytest.raises(watch.UsageError):
        watch.parse_rule(text)


def test_parse_args_defaults_and_options():
    o = watch.parse_args(["XYZ above 101.25", "XYZ below 95.5"])
    assert o["interval"] == 300 and o["start"] is None and o["until"] is None and o["workspace"] is None
    assert o["rules"] == [("XYZ", "above", 101.25), ("XYZ", "below", 95.5)]
    o = watch.parse_args(["--interval", "120", "--from", "06:31", "--until", "13:00", "--workspace", "/tmp/ws",
                          "ABC above 250.75"])
    assert (o["interval"], o["start"], o["until"], o["workspace"]) == (120, "06:31", "13:00", "/tmp/ws")


@pytest.mark.parametrize("argv", [
    [], ["--interval", "5", "XYZ above 1"], ["--interval", "x", "XYZ above 1"], ["--until", "1pm", "XYZ above 1"],
    ["--until"], ["--bogus", "XYZ above 1"], ["XYZ sideways 1"],
])
def test_parse_args_rejects(argv):
    with pytest.raises(watch.UsageError):
        watch.parse_args(argv)


def test_crossed():
    assert watch.crossed(("XYZ", "above", 101.25), 101.25)
    assert not watch.crossed(("XYZ", "above", 101.25), 101.24)
    assert watch.crossed(("XYZ", "below", 95.5), 95.0)
    assert not watch.crossed(("XYZ", "below", 95.5), 95.51)


# ---------------------------------------------------------------- one watcher per workspace

import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from conftest import make_workspace  # noqa: E402

WATCH = os.path.join(TOOLS, "watch.py")


def start(ws, *rules):
    """A real watcher that only waits (its window opens at 23:59), so no network is touched."""
    p = subprocess.Popen([sys.executable, WATCH, "--workspace", str(ws), "--from", "23:58", "--until", "23:59",
                          *(rules or ("XYZ above 1000000",))], stdout=subprocess.PIPE, text=True)
    record = os.path.join(str(ws), watch.PID_RELATIVE)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if open(record).read().split(" ")[0] == str(p.pid):
                return p
        except OSError:
            pass
        time.sleep(0.05)
    p.kill()
    raise AssertionError("watcher never recorded itself")


def tool(ws, flag):
    return subprocess.run([sys.executable, WATCH, "--workspace", str(ws), flag], capture_output=True, text=True)


def alive(p):
    return p.poll() is None


@pytest.fixture
def two_workspaces(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    make_workspace(str(a)), make_workspace(str(b))
    procs = []
    yield a, b, procs
    for p in procs:
        if alive(p):
            p.kill()


@pytest.mark.parametrize("argv", [["--stop"], ["--status"], ["--workspace", "/x", "--stop"]])
def test_stop_and_status_take_no_rules(argv):
    assert watch.parse_args(argv)["action"] == argv[-1][2:]


@pytest.mark.parametrize("argv", [["--stop", "XYZ above 1"], ["--stop", "--status"], ["--status", "--until", "13:00"]])
def test_stop_and_status_reject_extras(argv):
    with pytest.raises(watch.UsageError):
        watch.parse_args(argv)


def test_stop_touches_only_its_own_workspaces_watcher(two_workspaces):
    a, b, procs = two_workspaces
    pa, pb = start(a), start(b)
    procs += [pa, pb]
    assert "RUNNING pid %d" % pa.pid in tool(a, "--status").stdout
    assert "RUNNING pid %d" % pb.pid in tool(b, "--status").stdout
    out = tool(a, "--stop")
    assert out.returncode == 0 and "STOPPED pid %d" % pa.pid in out.stdout
    pa.wait(timeout=10)
    assert pa.returncode == 143 and "STOPPED" in pa.stdout.read()
    assert alive(pb), "the other workspace's watcher must keep running"
    assert "NOT RUNNING" in tool(a, "--status").stdout
    assert "RUNNING pid %d" % pb.pid in tool(b, "--status").stdout
    assert not os.path.exists(os.path.join(str(a), watch.PID_RELATIVE))
    assert "NOT RUNNING" in tool(a, "--stop").stdout  # a second stop is harmless
    assert alive(pb)


def test_a_new_start_replaces_only_its_own_workspaces_watcher(two_workspaces):
    a, b, procs = two_workspaces
    pa, pb = start(a), start(b)
    procs += [pa, pb]
    pa2 = start(a, "DEF below 1")
    procs.append(pa2)
    pa.wait(timeout=10)
    assert pa.returncode == 143
    assert alive(pa2) and alive(pb)
    assert "RUNNING pid %d" % pa2.pid in tool(a, "--status").stdout
    with open(os.path.join(str(a), "trading", "logs", "watch.log")) as f:
        assert "REPLACED this workspace's earlier watcher (pid %d)" % pa.pid in f.read()


def test_a_stale_or_foreign_record_is_never_signalled(two_workspaces):
    a, b, procs = two_workspaces
    sleeper = subprocess.Popen(["sleep", "30"])   # a live process that is not a watcher
    procs.append(sleeper)
    record = os.path.join(str(a), watch.PID_RELATIVE)
    os.makedirs(os.path.dirname(record), exist_ok=True)
    with open(record, "w") as f:
        f.write("%d Tue Nov 17 09:34:00 2026\n" % sleeper.pid)
    assert "NOT RUNNING" in tool(a, "--status").stdout
    assert "NOT RUNNING" in tool(a, "--stop").stdout
    assert alive(sleeper)
    # A real watcher of another workspace, copied into this workspace's record with the wrong start time.
    pb = start(b)
    procs.append(pb)
    with open(record, "w") as f:
        f.write("%d Mon Jan  1 00:00:00 2001\n" % pb.pid)
    assert "NOT RUNNING" in tool(a, "--stop").stdout
    assert alive(pb)
    for junk in ("", "abc", "1 x", "-5 x", "0 x"):
        with open(record, "w") as f:
            f.write(junk)
        assert "NOT RUNNING" in tool(a, "--stop").stdout
