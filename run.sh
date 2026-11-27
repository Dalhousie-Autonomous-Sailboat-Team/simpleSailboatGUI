#!/usr/bin/env sh
# Linux/macOS launcher: builds .venv on first run, then starts the GUI.
# Uses uv's managed Python because it ships with Tk (Arch's python needs the
# separate `tk` package otherwise).
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.13 --managed-python .venv
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        python3 -m venv .venv
        .venv/bin/pip install -r requirements.txt
    fi
fi
exec .venv/bin/python sailboat_monitor.py "$@"
