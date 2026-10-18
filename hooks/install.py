"""Install Coalesce hooks into a repo the agents work on (contract 5 and 6.6).

  python3 hooks/install.py --repo ../coalesce_demo_repo --name Kent --server http://192.168.1.20:8787 [--base main]

Writes REPO/.coalesce/config.json, merges the hooks into REPO/.claude/settings.json
and REPO/.gemini/settings.json (other keys kept, re-running is safe), and
gitignores .coalesce/. Python stdlib only.
"""
import argparse
import json
import os
import shlex

COALESCE_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(COALESCE_HOME, "hooks")


def cmd(script, agent):
    return f"python3 {shlex.quote(os.path.join(HOOKS_DIR, script))} --agent {agent}"


CLAUDE_HOOKS = {
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": cmd("on_prompt.py", "claude"), "timeout": 5}]}],
    "PreToolUse": [
        {"matcher": "Edit|Write|MultiEdit",
         "hooks": [{"type": "command", "command": cmd("on_pre_edit.py", "claude"), "timeout": 5}]}],
    "Stop": [
        {"hooks": [{"type": "command", "command": cmd("on_stop.py", "claude"), "async": True, "timeout": 30}]}],
}

GEMINI_HOOKS = {
    "BeforeAgent": [
        {"matcher": "*", "hooks": [{"name": "coalesce-prompt", "type": "command",
                                    "command": cmd("on_prompt.py", "gemini"), "timeout": 5000}]}],
    "BeforeTool": [
        {"matcher": "write_file|replace", "hooks": [{"name": "coalesce-pre-edit", "type": "command",
                                                     "command": cmd("on_pre_edit.py", "gemini"), "timeout": 5000}]}],
    "AfterAgent": [
        {"matcher": "*", "hooks": [{"name": "coalesce-stop", "type": "command",
                                    "command": cmd("on_stop.py", "gemini"), "timeout": 10000}]}],
}


def is_ours(group):
    return any(HOOKS_DIR in h.get("command", "") for h in group.get("hooks", []))


def merge_settings(path, new_hooks):
    """Add our hook groups to settings.json, replacing older Coalesce entries, keeping everything else."""
    settings = {}
    if os.path.isfile(path):
        with open(path) as f:
            settings = json.load(f)
    hooks = settings.setdefault("hooks", {})
    for event, groups in new_hooks.items():
        kept = [g for g in hooks.get(event, []) if not is_ours(g)]
        hooks[event] = kept + groups
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def gitignore(repo, line):
    path = os.path.join(repo, ".gitignore")
    existing = open(path).read() if os.path.isfile(path) else ""
    if line not in existing.splitlines():
        with open(path, "a") as f:
            f.write(("" if existing.endswith("\n") or not existing else "\n") + line + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--server", required=True)
    p.add_argument("--base", default="main")
    a = p.parse_args()
    repo = os.path.abspath(a.repo)

    os.makedirs(os.path.join(repo, ".coalesce"), exist_ok=True)
    config = {"dev": a.name, "server": a.server.rstrip("/"), "base_branch": a.base,
              "coalesce_home": COALESCE_HOME}
    with open(os.path.join(repo, ".coalesce", "config.json"), "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    merge_settings(os.path.join(repo, ".claude", "settings.json"), CLAUDE_HOOKS)
    merge_settings(os.path.join(repo, ".gemini", "settings.json"), GEMINI_HOOKS)
    gitignore(repo, ".coalesce/")

    print(f"Coalesce installed in {repo}")
    print(f"  dev={a.name} server={config['server']} base={a.base}")
    print("  wrote .coalesce/config.json, merged .claude/settings.json and .gemini/settings.json")


if __name__ == "__main__":
    main()
