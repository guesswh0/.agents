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
    [ "$1" -ef "$2" ] && return
    mkdir -p -- "$(dirname -- "$2")"
    if [ -e "$2" ] || [ -L "$2" ]; then
        mv -- "$2" "$2.backup.$(date +%Y%m%d%H%M%S).$$"
    fi
    ln -sv -- "$1" "$2"
}

link "$repo_dir/AGENTS.md" "$target_home/.codex/AGENTS.md"
link "$repo_dir/AGENTS.md" "$target_home/.claude/CLAUDE.md"

while IFS= read -r -d '' manifest; do
    skill=${manifest%/SKILL.md}
    name=${skill##*/}
    link "$repo_dir/$skill" "$target_home/.agents/skills/$name"
    if [ "$name" != claude-agent ]; then
        link "$repo_dir/$skill" "$target_home/.claude/skills/$name"
    fi
done < <(git -C "$repo_dir" ls-files -z -- 'skills/*/SKILL.md')

printf '\nAgent configuration installed.\n'
