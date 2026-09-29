# Contributing

Rules for maintaining global agent instructions and skills.

## Changes

- Keep shared agent rules in [AGENTS.md](AGENTS.md) and skill-specific rules in the skill's `SKILL.md`.
- Write instructions in English, keep them short, and link to existing rules instead of repeating them.
- Keep each skill's scripts and references in its own directory; update them together when behavior changes.

## Commits

Use Conventional Commits in English: `<type>(<scope>): <summary>`.

- Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
- Scope: `agents` for global instructions or the skill name, such as `claude-agent`; optional for other changes.
- Summary: imperative mood, lowercase first letter, no trailing period; maximum 72 characters for the full subject.
- Add a body when the reason needs explanation; mark breaking changes with `!` before `:` and explain the migration.
- Keep each commit to one logical change.
- Omit agent signatures, session metadata, and generated-by trailers.

```text
docs(agents): clarify comment rules
docs(claude-agent): explain session continuation
```
