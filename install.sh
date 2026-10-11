#!/usr/bin/env bash
# Whoever clones this repo: run this once and `ovpull` works from anywhere.
#
#   ./install.sh                      # -> ~/.local/bin/ovpull (+ staged launchers)
#   ./install.sh --bin-dir /usr/local/bin
#   ./install.sh --uninstall
#
# It only calls ovpull.py --install; anything you pass here goes through.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd)"

PY="${OVPULL_PYTHON:-}"
if [ -z "$PY" ]; then
  PY="$(command -v python3 || command -v python || true)"
fi
if [ -z "$PY" ]; then
  printf 'No python3 on PATH. Install Python 3.9+ first (brew install python, apt install python3).\n' >&2
  exit 1
fi

exec "$PY" "$HERE/ovpull.py" --install "$@"
