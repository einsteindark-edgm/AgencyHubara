#!/usr/bin/env bash
# /graphagents-gates [tools|arch|cert|graphs|integration|manifests|all] — panel determinístico
# de GraphAgents. Cada gate es un comando con exit code; se reporta ✓/✗. Invocado
# por el comando /graphagents-gates (plugin graphagents-dev) y por el hook Stop.
#
# Gates (todos desde GraphAgents/, vía `uv run`):
#   manifests -> `cli check`               (schema + archetype + capability + binding)
#   tools     -> `cli certify-tool` + tests (per-tool TCK: T-CONTRACT·T-DUR·G-AGNOSTIC + golden)
#   arch      -> `pytest tests/architecture` (reglas G-*, manifests + tool contracts)
#   cert      -> `pytest tests/conformance`  (TCK por agente, niveles C0–C3)
#   graphs    -> `pytest tests/graphs`       (golden-replay = G-DET determinismo)
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
GA="$ROOT/GraphAgents"
SCOPE="${1:-all}"
FAILED=0

if [[ ! -d "$GA" ]]; then
  printf '✗ No existe %s — ¿estás en el repo correcto?\n' "$GA" >&2
  exit 1
fi

gate() {  # gate "<nombre>" <cmd...>
  local name="$1"; shift
  printf '\n▶ %s\n' "$name"
  if ( cd "$GA" && "$@" ); then
    printf '✓ %s\n' "$name"
  else
    printf '✗ %s (exit %d)\n' "$name" "$?"
    FAILED=1
  fi
}

want() { [[ "$SCOPE" == "all" || "$SCOPE" == "$1" ]]; }

if want manifests; then
  gate "Manifests válidos (cli check: schema + archetype + capability + binding)" \
    uv run python -m sdk.cli check
fi
if want tools; then
  gate "Catálogo de tools (cli certify-tool: T-CONTRACT · T-DUR · G-AGNOSTIC)" \
    uv run python -m sdk.cli certify-tool
  [[ -d "$GA/tests/tools" ]] && \
    gate "Tools (per-tool TCK + golden de la impl)" \
      uv run pytest tests/tools -q
fi
if want arch; then
  [[ -d "$GA/tests/architecture" ]] && \
    gate "Arquitectura (reglas G-*, manifests + tool contracts)" \
      uv run pytest tests/architecture -q
fi
if want cert; then
  [[ -d "$GA/tests/conformance" ]] && \
    gate "Certificación (TCK por agente, C0–C3)" \
      uv run pytest tests/conformance -q
fi
if want graphs; then
  [[ -d "$GA/tests/graphs" ]] && \
    gate "Determinismo (golden-replay, G-DET)" \
      uv run pytest tests/graphs -q
fi
if want integration; then
  [[ -d "$GA/tests/integration" ]] && \
    gate "Integración (manifest→runnable corre + recovery por execution-id)" \
      uv run pytest tests/integration -q
fi

printf '\n────────────────────────\n'
if [[ "$FAILED" -eq 0 ]]; then
  printf '✓ PANEL VERDE — mergeable por arquitectura.\n'
  printf 'Recordá: tests verdes ≠ feature viva. Un cambio de comportamiento se\n'
  printf 'verifica corriendo el grafo real sobre AgentSpan (recovery por execution-id).\n'
else
  printf '✗ PANEL ROJO — ver los gates con ✗ arriba. No apliques fixes a ciegas:\n'
  printf 'abordá test-first (rojo → verde → refactor) con tu contexto completo.\n'
fi
exit "$FAILED"
