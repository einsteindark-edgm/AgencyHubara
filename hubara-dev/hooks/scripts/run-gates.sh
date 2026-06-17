#!/usr/bin/env bash
# /hubara-gates [backend|frontend|all] — corre el panel determinístico §8 de
# AgencyHubara y reporta cada gate con su exit code. Invocado por el comando
# /hubara-gates (plugin hubara-dev). Los dummies van inline (§8): valen para
# architecture/plugins/conformance/CLI, NUNCA para tests/platform.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
SCOPE="${1:-all}"
export MEDUSA_BASE_URL="http://medusa.invalid"
export MEDUSA_ADMIN_TOKEN="ci-dummy"
export OTEL_SDK_DISABLED="true"

FAILED=0

gate() {  # gate "<nombre>" <cwd> <comando...>
  local name="$1"; shift
  local cwd="$1"; shift
  printf '\n▶ %s\n' "$name"
  if ( cd "$cwd" && "$@" ); then
    printf '✓ %s\n' "$name"
  else
    printf '✗ %s (exit %d)\n' "$name" "$?"
    FAILED=1
  fi
}

if [[ "$SCOPE" == "all" || "$SCOPE" == "backend" ]]; then
  HUB="$ROOT/hubara_agency"
  gate "R-DIP (import-linter)" "$HUB" uv run lint-imports
  gate "Arquitectura (R-rules + plugin-contract + orquestación + meta-gate)" "$HUB" \
    uv run pytest tests/architecture tests/plugins -q
  if [[ -d "$HUB/tests/conformance" ]]; then
    gate "Certificación (TCK por plugin, P-27)" "$HUB" uv run pytest tests/conformance -q
  fi
  if [[ -d "$HUB/src/sdk" ]]; then
    gate "CLI check (compilador rápido)" "$HUB" uv run python -m src.sdk.cli check
  fi
fi

if [[ "$SCOPE" == "all" || "$SCOPE" == "frontend" ]]; then
  FE="$ROOT/frontend_dashboard"
  gate "Plugin registry (codegen)" "$FE" npm run plugins:sync
  gate "Type-check composite (tsc -b)" "$FE" npx tsc -b
  gate "FSD + íconos + meta-gate (test:arch)" "$FE" npm run test:arch
fi

printf '\n────────────────────────\n'
if [[ "$FAILED" -eq 0 ]]; then
  printf '✓ PANEL VERDE — mergeable por arquitectura.\n'
  printf 'Recordá: tests verdes ≠ feature viva (gotcha #1). Si el cambio es\n'
  printf 'visible, verificá contra el stack Docker real.\n'
else
  printf '✗ PANEL ROJO — ver los gates con ✗ arriba.\n'
  printf 'Nota: 3 fallos PRE-existentes en tests/plugins/chats (voseo + 2 watchdog)\n'
  printf 'no son del cambio; cualquier OTRO rojo sí. Si es un gate de allowlist y\n'
  printf 'tu repro local pasaba: staleness (L-15) → mergeá main + regenerá.\n'
fi
exit "$FAILED"
