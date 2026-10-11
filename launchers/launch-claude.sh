#!/usr/bin/env bash
# launch-claude — open a Claude Code session for this project and keep that
# same conversation every time you come back to it.
#
#   ./launch-claude.sh                 # continue this folder's last session (or start one)
#   ./launch-claude.sh --new           # start a fresh conversation instead
#   ./launch-claude.sh --dry-run       # show what would run, launch nothing
#   ./launch-claude.sh --model opus    # anything else is passed straight to claude
#
# macOS double-click: use launch-claude.command (same script, holds the window).
# Windows: launch-claude.bat / launch-claude.ps1.
#
# Env: CLAUDE_BIN (path to the CLI), CLAUDE_LAUNCHER_HOLD=1 to keep the window open.
set -euo pipefail

SELF="${BASH_SOURCE[0]:-$0}"
HERE="$(cd -- "$(dirname -- "$SELF")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
cd "$ROOT"

case "$SELF" in
  *.command) HOLD="${CLAUDE_LAUNCHER_HOLD:-1}" ;;   # double-clicked
  *)         HOLD="${CLAUDE_LAUNCHER_HOLD:-0}" ;;
esac

NEW=0
DRY_RUN=0
PASSTHRU=()
for arg in "$@"; do
  case "$arg" in
    --new)     NEW=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *)         PASSTHRU+=("$arg") ;;
  esac
done

# ---- find the CLI ---------------------------------------------------------
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [ -z "$CLAUDE_BIN" ] && command -v claude >/dev/null 2>&1; then
  CLAUDE_BIN="$(command -v claude)"
fi
if [ -z "$CLAUDE_BIN" ]; then
  for candidate in "$HOME/.local/bin/claude" /usr/local/bin/claude /opt/homebrew/bin/claude; do
    if [ -x "$candidate" ]; then CLAUDE_BIN="$candidate"; break; fi
  done
fi
if [ -z "$CLAUDE_BIN" ]; then
  printf 'claude not found.\n  install:  npm install -g @anthropic-ai/claude-code\n  or set CLAUDE_BIN to the binary.\n' >&2
  if [ "$HOLD" = 1 ] && [ -t 0 ]; then printf '\nPress Enter to close this window...'; read -r _ || true; fi
  exit 1
fi

# ---- does this folder already have a conversation? -----------------------
# Claude Code keeps transcripts in ~/.claude/projects/<cwd, non-alphanumerics -> '-'>
SLUG="$(printf '%s' "$ROOT" | sed 's/[^A-Za-z0-9]/-/g')"
SESS_DIR="$HOME/.claude/projects/$SLUG"
SESSIONS=0
if [ -d "$SESS_DIR" ]; then
  SESSIONS="$(find "$SESS_DIR" -maxdepth 1 -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')"
fi

CMD=("$CLAUDE_BIN")
CONTINUE=0
if [ "$NEW" = 0 ] && [ "$SESSIONS" -gt 0 ]; then
  CMD+=(--continue)
  CONTINUE=1
fi
CMD+=("${PASSTHRU[@]+"${PASSTHRU[@]}"}")

if [ "$DRY_RUN" = 1 ]; then
  printf 'project:  %s\n' "$ROOT"
  printf 'claude:   %s\n' "$CLAUDE_BIN"
  printf 'sessions: %s transcript(s) in %s\n' "$SESSIONS" "$SESS_DIR"
  printf 'reuse:    %s\n' "$([ "$CONTINUE" = 1 ] && echo 'yes (--continue)' || echo 'no (fresh session)')"
  printf 'command:  %s\n' "${CMD[*]}"
  exit 0
fi

printf '→ %s\n' "${CMD[*]}"
"${CMD[@]}"

if [ "$HOLD" = 1 ] && [ -t 0 ]; then
  printf '\n— Claude session closed. Press Enter to close this window —\n'
  read -r _ || true
fi
