#!/usr/bin/env sh
# Jarvis - start script (macOS / Linux)
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/ or your package manager."
  exit 1
fi
"$PY" setup.py
"$PY" main.py
