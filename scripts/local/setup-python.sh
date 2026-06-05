#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

for dir in web crawler desktop; do
    VENV_DIR="$REPO_ROOT/$dir/.venv"
    REQ_FILE="$REPO_ROOT/$dir/requirements.txt"

    if [ ! -f "$REQ_FILE" ]; then
        echo "[$dir] No requirements.txt, skipping."
        continue
    fi

    if command -v uv >/dev/null 2>&1; then
        echo "[$dir] uv detected — using uv for faster venv + install..."
        if [ -d "$VENV_DIR" ]; then
            echo "[$dir] uv venv already exists."
        else
            uv venv --python 3.11 "$VENV_DIR"
        fi
        uv pip install --python "$VENV_DIR/bin/python" -r "$REQ_FILE"
        echo "[$dir] Done (uv)."
        continue
    fi

    PYTHON_BIN="$VENV_DIR/bin/python"
    if [ -x "$PYTHON_BIN" ]; then
        echo "[$dir] venv already exists: $PYTHON_BIN"
        continue
    fi

    echo "[$dir] Creating venv (python -m venv)..."
    python3 -m venv "$VENV_DIR"
    "$PYTHON_BIN" -m pip install --upgrade pip
    "$PYTHON_BIN" -m pip install -r "$REQ_FILE"
    echo "[$dir] Done."
done

echo "All Python environments ready."
echo ""
echo "Tip: Install 'uv' (https://astral.sh/uv) for much faster future runs of this script."
