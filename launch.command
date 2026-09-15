#!/bin/zsh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "The project environment is missing. Run these commands first:"
    echo "  cd \"$PROJECT_DIR\""
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
    read -r "REPLY?Press Return to close..."
    exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON" sailboat_monitor.py
