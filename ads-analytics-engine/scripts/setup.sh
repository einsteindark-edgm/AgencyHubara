#!/usr/bin/env bash
# Create the venv + install deps. The core engine has ZERO runtime deps; this
# installs the dev tools (pytest, ruff) and the `ads-engine` CLI entrypoint.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip

if ! python -m pip install --quiet -e ".[dev]"; then
  echo "editable install failed (offline?). Installing test tools only — use PYTHONPATH=src." >&2
  python -m pip install --quiet pytest ruff
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "scaffolded .env — edit it before going live"
fi

echo "setup OK — activate with: . .venv/bin/activate"
