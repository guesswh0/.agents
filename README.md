# .agents

Personal instructions and skills shared across coding agents. This repository lives
at `~/.agents`; harness-specific paths link back to it.

## Installation

```bash
git clone https://github.com/guesswh0/.agents.git ~/.agents
bash ~/.agents/setup.sh
```

If `~/.agents` already exists, clone to another directory and run `setup.sh` there.
The installer links this repository's tracked skills into `~/.agents/skills`.
Existing third-party skills and their links stay in place.

The installer creates these links:

| Path | Target |
| --- | --- |
| `~/.codex/AGENTS.md` | `AGENTS.md` in this repository |
| `~/.claude/CLAUDE.md` | `AGENTS.md` in this repository |
| `~/.claude/skills/<name>` | Each compatible tracked skill in `skills/` |

Codex reads `~/.agents/skills` directly. `claude-agent` is a Codex skill for
delegating to Claude Code, so it is not linked into Claude's own skills.

Run the installer again after adding skills or relocating this repository.
After deleting or renaming a skill, remove its old harness links.
Conflicting files are saved
beside the new links with a `.backup.<timestamp>.<pid>` suffix. Existing links to the
same target are left alone. Restart the harness after installation.

The installer uses the standard paths under your home directory. To install into a
different home directory, pass it as the first argument to `setup.sh`.

## Claude Agent runtime

```bash
python3 ~/.agents/skills/claude-agent/scripts/install_runtime.py
```

Requires Python 3.11+ and an authenticated Claude Code CLI. Recreate this runtime
after moving the repository. See [Claude Agent](skills/claude-agent/README.md).

## Repository boundary

`AGENTS.md` holds shared instructions. `skills/` holds owned or explicitly imported
skills. Other skill collections, installed third-party skills, and installer lock
files are outside this repository's scope.

Untracked skills are ignored by default. To add one, add its directory to the
allowlist in `.gitignore`, then track it with Git. The installer uses tracked
`SKILL.md` files, so add a new skill to Git before running setup.

## Checks

```bash
bash -n setup.sh
shellcheck setup.sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s skills/claude-agent/tests -v
```
