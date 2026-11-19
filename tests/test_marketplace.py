""".claude-plugin/marketplace.json: parses, follows the marketplace schema's required fields, and lists this
plugin (the repository root) under the name the README tells friends to install."""
import json
import os
import re

from conftest import ROOT

MARKETPLACE = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
MANIFEST = os.path.join(ROOT, ".claude-plugin", "plugin.json")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Names Claude Code refuses for a marketplace (marketplace reference, "Reserved names"), the ones a name like
# ours could collide with.
RESERVED = {"claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
            "anthropic-plugins", "agent-skills", "inline", "builtin", "skills-dir", "synced", "npm", "github", "gh"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_marketplace_parses_and_names_the_plugin():
    m = load(MARKETPLACE)
    manifest = load(MANIFEST)
    assert NAME.match(m["name"]) and ".." not in m["name"] and m["name"].lower() not in RESERVED
    assert not m["name"].startswith("claudeai-")
    assert isinstance(m["owner"], dict) and m["owner"]["name"].strip()
    assert m.get("description")
    entries = m["plugins"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert entry["name"] == manifest["name"] == "personal-ai-trader"
    assert NAME.match(entry["name"])
    # The plugin is the repository root, a relative path from the marketplace root.
    assert entry["source"] in ("./", ".")
    assert os.path.isfile(os.path.join(ROOT, entry["source"], ".claude-plugin", "plugin.json"))
    # plugin.json holds the version (it wins over an entry's, and validate warns on both).
    assert "version" not in entry and manifest.get("version")


def test_readme_installs_from_this_marketplace():
    m = load(MARKETPLACE)
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    assert re.search(r"/plugin marketplace add [\w.-]+/personal-ai-trader\b", readme)
    assert "/plugin install %s@%s" % (m["plugins"][0]["name"], m["name"]) in readme
    assert "<owner>" not in readme and "<repo>" not in readme and "<marketplace name>" not in readme
