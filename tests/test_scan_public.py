"""scripts/scan_public.py: the publish scanner finds what must never be public and passes a clean tree."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN = os.path.join(ROOT, "scripts", "scan_public.py")


def run(root, *extra):
    return subprocess.run([sys.executable, SCAN, "--root", str(root)] + list(extra), capture_output=True, text=True)


def write(tmp_path, text):
    (tmp_path / "note.md").write_text(text)
    return tmp_path


def test_clean_text_passes(tmp_path):
    p = run(write(tmp_path, "Run on 2026-11-02. Call 555 tools. See https://example.com/a?b=1\n"))
    assert p.returncode == 0, p.stdout


BAD = {
    "account-like number": "account 8123" + "45670 is mine",
    "home folder path": "see /" + "Users/someone/file",
    "e-mail address": "write to someone" + "@" + "mail.test",
    "id (uuid)": "list 0a1b2c3d-" + "1111-4222-8333-4444555" + "56666",
    "credential": "gh" + "p_" + "a" * 36,
    "credential assignment": "access_token" + " = " + "abcDEF123456ghiJKL789",
}


def test_each_kind_is_found(tmp_path):
    for kind, text in BAD.items():
        p = run(write(tmp_path, text + "\n"))
        assert p.returncode == 1 and kind in p.stdout, (kind, p.stdout)


def test_private_terms_file(tmp_path):
    terms = tmp_path / "terms.txt"
    terms.write_text("# comment\nZebraName\n")
    p = run(write(tmp_path, "written by zebraname\n"), "--terms", str(terms))
    assert p.returncode == 1 and "private term: ZebraName" in p.stdout


def test_private_terms_file_is_ignored_by_git():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        ignore = f.read()
    for line in ("*.local.txt", "tests/RESULTS-*.md", "trader.json", "trading/"):
        assert line in ignore


def test_account_numbers_are_found_next_to_letters_and_punctuation(tmp_path):
    number = "8123" + "45670"
    for text in ("acct:" + number, "acct-" + number, "/accounts/" + number + "/", "RH" + number, "id=" + number):
        p = run(write(tmp_path, text + "\n"))
        assert p.returncode == 1 and "account-like number" in p.stdout, (text, p.stdout)


def test_private_file_is_skipped_normally_and_a_finding_in_public_mode(tmp_path):
    (tmp_path / "internal.md").write_text("# Notes\n<!-- scan-public: " + "private-file -->\naccount 8123" + "45670\n")
    p = run(tmp_path)
    assert p.returncode == 0 and "skipped" in p.stdout, p.stdout
    p = run(tmp_path, "--public")
    assert p.returncode == 1 and "private development file" in p.stdout, p.stdout

