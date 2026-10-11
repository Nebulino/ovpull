#!/usr/bin/env bash
# macOS double-click entry point for launch-claude.sh.
# Finder opens .command files in Terminal; the session ends up in the same
# conversation as last time, and the window stays open afterwards.
export CLAUDE_LAUNCHER_HOLD="${CLAUDE_LAUNCHER_HOLD:-1}"
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "$HERE/launch-claude.sh" "$@"
