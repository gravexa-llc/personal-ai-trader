#!/usr/bin/env python3
"""Scan the repository for things that must never be published: credentials, account-number-like digit
runs, e-mail addresses, home-folder paths and unknown ids.

It knows no secret itself, so it is safe to publish and to run in CI. A private list of extra terms (names,
an account number, a handle) can be given with --terms FILE or in tests/personal-terms.local.txt (ignored by
git); each non-empty line that does not start with # is matched case-insensitively.

  python3 scripts/scan_public.py [--root DIR] [--terms FILE] [--public]

Exit codes: 0 clean, 1 findings (printed as path:line: kind: text), 2 usage.
"""
import argparse
import os
import re
import subprocess
import sys

SKIP_DIRS = {".git", ".venv", ".venv-dev", "__pycache__", ".pytest_cache", "node_modules"}
# A file whose first lines carry this marker is a private development file. A normal run skips it and says
# so; with --public (the export and the public repository) its presence is itself a finding.
PRIVATE_MARKER = "scan-public: private-file"
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".zip", ".pyc", ".ico"}

# Ids that are public market data or test fixtures, not personal: listed with the reason.
ALLOW_IDS = {
    "3b912aa2-88f9-4682-8ae3-e39520bdf4db",  # Robinhood's public index id for VIX
    "432fbbb8-b82c-454a-852d-eb85382c7066",  # public index id quoted in templates/PLAN.md
    "50c298f7-27a8-44a1-b049-ec153cf2892f",  # public index id quoted in templates/PLAN.md
    "6b1e2c3d-0000-4000-8000-123456789abc",  # made-up id in tests/test_doctor.py
}
ALLOW_NUMBERS = {"123456789", "987654321", "999999999", "20261102"}  # dummies and a date in the tests
ALLOW_PATHS = {"/home/me"}  # a made-up path in tests/test_setup_settings.py
ALLOW_EMAIL = re.compile(r"@(example\.(com|org|net)|users\.noreply\.github\.com|anthropic\.com)$", re.I)

CHECKS = [
    ("credential", re.compile(
        r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|access[_-]?token|refresh[_-]?token|password|passwd|bearer)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+=.]{16,}")),
    ("home folder path", re.compile(r"(/Users|/home)/[A-Za-z][\w.-]*")),
    ("e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("account-like number", re.compile(r"(?<![\d.])\d{8,19}(?![\d.]|\s*(?:bytes|ms)\b)")),
    ("id (uuid)", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")),
]
UUID_RX = CHECKS[-1][1]


def tracked_files(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
        files = [f for f in out.decode().split("\0") if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            found.append(os.path.relpath(os.path.join(base, n), root))
    return sorted(found)


def load_terms(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


def scan(root, terms, public=False):
    findings = []
    for rel in tracked_files(root):
        if os.path.splitext(rel)[1].lower() in BINARY_EXT:
            continue
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except UnicodeDecodeError:
            findings.append((rel, 0, "unreadable (binary?)", ""))
            continue
        if rel != "scripts/scan_public.py" and any(PRIVATE_MARKER in ln for ln in lines[:5]):
            if public:
                findings.append((rel, 1, "private development file", "must not be published"))
            else:
                print("%s: skipped (private development file; must not be published)" % rel)
            continue
        for i, line in enumerate(lines, 1):
            if "scan-public: allow" in line:
                continue
            uuid_spans = [m.span() for m in UUID_RX.finditer(line)]
            for kind, rx in CHECKS:
                for m in rx.finditer(line):
                    hit = m.group(0)
                    if kind == "account-like number" and any(a <= m.start() and m.end() <= b for a, b in uuid_spans):
                        continue  # the digits are the tail of a uuid, reported (or allowed) as an id
                    if kind == "id (uuid)" and hit in ALLOW_IDS:
                        continue
                    if kind == "e-mail address" and ALLOW_EMAIL.search(hit):
                        continue
                    if kind == "account-like number" and hit in ALLOW_NUMBERS:
                        continue
                    if kind == "home folder path" and hit in ALLOW_PATHS:
                        continue
                    findings.append((rel, i, kind, hit))
            low = line.lower()
            for t in terms:
                if t.lower() in low:
                    findings.append((rel, i, "private term", t))
    return findings


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p.add_argument("--terms")
    p.add_argument("--public", action="store_true",
                   help="scan a tree that is about to be public: a private development file is a finding")
    a = p.parse_args(argv)
    terms = load_terms(a.terms or os.path.join(a.root, "tests", "personal-terms.local.txt"))
    findings = scan(a.root, terms, public=a.public)
    for rel, line, kind, hit in findings:
        print("%s:%d: %s: %s" % (rel, line, kind, hit))
    print("scan_public: %d finding(s) in %s%s" % (len(findings), a.root,
                                                  " (with %d private terms)" % len(terms) if terms else ""))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
