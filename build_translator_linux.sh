#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[ERROR] build_translator_linux.sh must be run on Linux."
  exit 1
fi

PYTHON_CMD="${PYTHON_CMD:-python3}"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  PYTHON_CMD="python"
fi

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "[ERROR] Python 3 was not found in PATH."
  exit 1
fi

if [[ -x ".venv/bin/python" ]]; then
  VENV_DIR=".venv"
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  VENV_DIR="venv"
  PYTHON_BIN="venv/bin/python"
elif [[ -x ".venv-linux/bin/python" ]]; then
  VENV_DIR=".venv-linux"
  PYTHON_BIN=".venv-linux/bin/python"
fi

if [[ -n "${PYTHON_BIN:-}" ]] && ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  unset VENV_DIR
  unset PYTHON_BIN
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -d ".venv" || -d "venv" ]]; then
    VENV_DIR=".venv-linux"
  else
    VENV_DIR=".venv"
  fi
  rm -rf "$VENV_DIR"
  "$PYTHON_CMD" -m venv "$VENV_DIR"
  PYTHON_BIN="$VENV_DIR/bin/python"
  "$PYTHON_BIN" -m pip install --upgrade pip
fi

REQ_FILE="requirements-translator-only.txt"
SPEC_FILE="translatorFork-translator-only.spec"
BUILD_STAMP="$VENV_DIR/.translator-build.sha256"
CURRENT_HASH="$("$PYTHON_BIN" -c "from pathlib import Path; import hashlib; files = [Path('$REQ_FILE'), Path('$SPEC_FILE')]; digest = hashlib.sha256(); [digest.update(path.read_bytes()) for path in files]; print(digest.hexdigest())")"
SAVED_HASH=""

if [[ -f "$BUILD_STAMP" ]]; then
  SAVED_HASH="$(cat "$BUILD_STAMP")"
fi

if [[ "$CURRENT_HASH" != "$SAVED_HASH" ]] || ! "$PYTHON_BIN" -c "import PyInstaller, PyInstaller.utils.hooks" >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --upgrade -r "$REQ_FILE" pyinstaller pyinstaller-hooks-contrib
  printf '%s' "$CURRENT_HASH" > "$BUILD_STAMP"
fi

rm -rf "build/translatorFork-translator"
rm -f "dist/translatorFork-translator" "dist/translatorFork-translator.exe"

"$PYTHON_BIN" -m PyInstaller --clean --noconfirm "$SPEC_FILE"

ARTIFACT="dist/translatorFork-translator"
if [[ -f "${ARTIFACT}.exe" ]]; then
  ARTIFACT="${ARTIFACT}.exe"
fi

if [[ ! -f "$ARTIFACT" ]]; then
  echo "[ERROR] Build finished without an output artifact."
  exit 1
fi

echo
echo "[OK] Translator-only build finished:"
echo "     $SCRIPT_DIR/$ARTIFACT"
