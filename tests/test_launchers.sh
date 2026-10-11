#!/usr/bin/env bash
# Smoke tests for the Claude launcher (POSIX side).
# A stub binary plays the role of `claude`, so no real session is ever started.
set -uo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
LAUNCH="$ROOT/launchers/launch-claude.sh"
FAILS=0

check() { # label, expected-substring, actual
  if printf '%s' "$3" | grep -qF -- "$2"; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n     expected to contain: %s\n     got: %s\n' "$1" "$2" "$(printf '%s' "$3" | tr '\n' '|')"
    FAILS=$((FAILS + 1))
  fi
}
check_not() { # label, forbidden-substring, actual
  if printf '%s' "$3" | grep -qF -- "$2"; then
    printf 'FAIL %s\n     should not contain: %s\n     got: %s\n' "$1" "$2" "$(printf '%s' "$3" | tr '\n' '|')"
    FAILS=$((FAILS + 1))
  else
    printf 'ok   %s\n' "$1"
  fi
}

echo "== syntax =="
bash -n "$LAUNCH" && echo "ok   launch-claude.sh parses"
bash -n "$ROOT/launchers/launch-claude.command" && echo "ok   launch-claude.command parses"

echo
echo "== a stub that stands in for claude =="
STUBDIR="$(mktemp -d)"
STUB="$STUBDIR/claude"
cat > "$STUB" <<'STUBEOF'
#!/usr/bin/env bash
printf 'ARGV:'
for a in "$@"; do printf ' [%s]' "$a"; done
printf '\n'
printf 'CWD:%s\n' "$PWD"
STUBEOF
chmod +x "$STUB"

SLUG="$(printf '%s' "$ROOT" | sed 's/[^A-Za-z0-9]/-/g')"
SESS_DIR="$HOME/.claude/projects/$SLUG"
MADE_SESSIONS=0
cleanup() {
  rm -rf "$STUBDIR"
  if [ "$MADE_SESSIONS" = 1 ]; then rm -rf "$SESS_DIR"; fi
}
trap cleanup EXIT

echo
echo "== scenario: no conversation yet =="
rm -rf "$SESS_DIR"
out="$(CLAUDE_BIN="$STUB" CLAUDE_LAUNCHER_HOLD=0 "$LAUNCH" --model opus)"
check "starts a fresh session" "ARGV: [--model] [opus]" "$out"
check "runs in the project root" "CWD:$ROOT" "$out"

echo
echo "== scenario: this folder already has a transcript =="
mkdir -p "$SESS_DIR"; MADE_SESSIONS=1
: > "$SESS_DIR/aaaa-bbbb.jsonl"
out="$(CLAUDE_BIN="$STUB" CLAUDE_LAUNCHER_HOLD=0 "$LAUNCH")"
check "reuses the existing conversation" "ARGV: [--continue]" "$out"

out="$(CLAUDE_BIN="$STUB" CLAUDE_LAUNCHER_HOLD=0 "$LAUNCH" --new)"
check_not "--new forces a fresh one" "--continue" "$out"

echo
echo "== scenario: --dry-run changes nothing =="
out="$(CLAUDE_BIN="$STUB" CLAUDE_LAUNCHER_HOLD=0 "$LAUNCH" --dry-run)"
check "reports the reused session" "reuse:    yes (--continue)" "$out"
check "prints the command" "command:  $STUB --continue" "$out"
check_not "does not run claude" "ARGV:" "$out"

echo
echo "== scenario: double-click wrapper =="
: > "$SESS_DIR/cccc-dddd.jsonl"
out="$(CLAUDE_BIN="$STUB" CLAUDE_LAUNCHER_HOLD=0 "$ROOT/launchers/launch-claude.command" --dry-run)"
check "the .command wrapper reaches the same script" "project:  $ROOT" "$out"

echo
echo "== scenario: claude missing =="
mkdir -p "$STUBDIR/fakehome"
out="$(env -i PATH=/usr/bin:/bin HOME="$STUBDIR/fakehome" CLAUDE_LAUNCHER_HOLD=0 "$LAUNCH" 2>&1)"
check "fails with an install hint" "npm install -g @anthropic-ai/claude-code" "$out"

echo
if [ "$FAILS" = 0 ]; then echo "ALL PASS"; else echo "$FAILS FAILED"; fi
exit $((FAILS > 0))
