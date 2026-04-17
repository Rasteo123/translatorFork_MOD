#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_CMD="${PYTHON_CMD:-python3}"
  if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    PYTHON_CMD="python"
  fi

  if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "[!] Python 3 was not found in PATH."
    exit 1
  fi

  VENV_DIR=".venv"
  "$PYTHON_CMD" -m venv "$VENV_DIR"
  PYTHON_BIN="$VENV_DIR/bin/python"
  "$PYTHON_BIN" -m pip install --upgrade pip
  "$PYTHON_BIN" -m pip install --upgrade -r requirements.txt
fi

exec "$PYTHON_BIN" main.py "$@"
