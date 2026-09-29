#!/bin/bash

set -euo pipefail

if [ "$#" -gt 1 ]; then
    printf 'Usage: bash setup.sh [home-directory]\n' >&2
    exit 2
fi

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [ "$(git -C "$repo_dir" rev-parse --show-toplevel)" != "$repo_dir" ]; then
    printf 'Run setup.sh from a clone of this repository.\n' >&2
    exit 1
fi
target_home=${1:-$HOME}
if [ ! -d "$target_home" ]; then
    printf 'Home directory does not exist: %s\n' "$target_home" >&2
    exit 1
fi
target_home=$(cd -- "$target_home" && pwd -P)

link() {
    # keep correct links unchanged on repeated runs
    [ "$1" -ef "$2" ] && return
    mkdir -p -- "$(dirname -- "$2")"
    if [ -e "$2" ] || [ -L "$2" ]; then
        # keep backups outside skill discovery
        mkdir -p -- "$target_home/.agents/backups"
        backup_dir=$(mktemp -d "$target_home/.agents/backups/$(basename -- "$2").XXXXXX")
        mv -- "$2" "$backup_dir/original"
    fi
    ln -sv -- "$1" "$2"
}

link "$repo_dir/AGENTS.md" "$target_home/.codex/AGENTS.md"
link "$repo_dir/AGENTS.md" "$target_home/.claude/CLAUDE.md"

# native harness tools manage skills outside local/skills
for manifest in "$repo_dir"/local/skills/*/SKILL.md; do
    [ -f "$manifest" ] || continue
    skill=${manifest%/SKILL.md}
    name=${skill##*/}
    link "$skill" "$target_home/.agents/skills/$name"
    # this adapter delegates from codex to claude
    if [ "$name" != claude-agent ]; then
        link "$skill" "$target_home/.claude/skills/$name"
    fi
done

printf '\nAgent configuration installed.\n'
