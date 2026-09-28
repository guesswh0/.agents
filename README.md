# .agents

Shared configuration for coding agents.

- `AGENTS.md` — common instructions.
- `skills/` — tracked skills.
- `setup.sh` — links for Codex and Claude Code.

## Install

```bash
git clone https://github.com/guesswh0/.agents.git ~/.agents
bash ~/.agents/setup.sh
```

If `~/.agents` already exists, clone elsewhere and run
`setup.sh` there. Conflicting files are backed up.

External skills are ignored by Git. Add new directories to the `.gitignore`
allowlist before tracking them.
