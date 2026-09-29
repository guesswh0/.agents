# .agents

Shared configuration for coding agents.

- `AGENTS.md` — common instructions.
- `local/skills/` — our skill sources.
- `skills/` — installed skills, outside Git.
- `setup.sh` — links for Codex and Claude Code.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution and commit rules.

## Install

```sh
git clone https://github.com/guesswh0/.agents.git ~/.agents
bash ~/.agents/setup.sh
```

If `~/.agents` already exists, clone elsewhere and run `setup.sh` there.
Setup links the global instructions and each skill under `local/skills/`.
Skills are shared with Codex and Claude Code; `claude-agent` is linked only for Codex.
Conflicting files are preserved under `~/.agents/backups/`, outside skill discovery.

Edit local skills directly; symlinks make changes available without reinstalling.
Run setup again after adding a local skill.
Use each harness's own tools to install and update third-party skills and plugins.
See [claude-agent](local/skills/claude-agent/README.md) for its Python runtime setup.

## Checks

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s local/skills/claude-agent/tests -v
```
