#!/usr/bin/env bash
# The project gate: lint clean + all golden/functional tests green.
# Works with or without an editable install (PYTHONPATH=src covers both).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

echo "== ruff =="
"$PY" -m ruff check src tests

echo "== pytest (golden + functional) =="
PYTHONPATH=src "$PY" -m pytest -q

echo "ALL GREEN"
