#!/usr/bin/env bash
# /graphagents-gates [tools|arch|cert|graphs|integration|manifests|viewer|all] — panel
# determinístico de GraphAgents. Cada gate es un comando con exit code; se reporta ✓/✗.
# Invocado por el comando /graphagents-gates (plugin graphagents-dev) y por el hook Stop.
#
# Gates (todos desde GraphAgents/):
#   manifests -> `cli check`                (schema + archetype + capability + binding)
#   tools     -> `cli certify-tool` + tests (per-tool TCK: T-CONTRACT·T-DUR·G-AGNOSTIC + golden)
#   arch      -> `pytest tests/architecture` (reglas G-*, manifests + tool contracts + serializador)
#   cert      -> `pytest tests/conformance`  (TCK por agente, niveles C0–C3)
#   graphs    -> `pytest tests/graphs`       (golden-replay = G-DET determinismo)
#   integration -> `pytest tests/integration`(manifest→runnable corre + recovery + backend del explorer)
#   viewer    -> el serializador del sistema (sdk.graph) + la API del explorer
#
# RUNNER (L-3): el pre-bash hook del monorepo BLOQUEA `cd GraphAgents && uv run` (solo
# whitelistea `cd hubara_agency`), y local no hay venv sincronizado. Por eso elegimos
# el intérprete así: si el python3 del sistema ya tiene las deps (pydantic/pyyaml/pytest)
# → corremos con `python3 -m pytest` (local, sin `uv sync`, sin chocar el hook). Si no
# (caso Docker: deps en /opt/venv) → `uv run`. Las capabilities importan langgraph LAZY
# (solo en `build()`), así que el camino puro `run()` corre con el python3 del sistema.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
GA="$ROOT/GraphAgents"
SCOPE="${1:-all}"
FAILED=0

if [[ ! -d "$GA" ]]; then
  printf '✗ No existe %s — ¿estás en el repo correcto?\n' "$GA" >&2
  exit 1
fi

if python3 -c "import pydantic, yaml, pytest" >/dev/null 2>&1; then
  PY=(python3); PYTEST=(python3 -m pytest)
elif command -v uv >/dev/null 2>&1; then
  PY=(uv run python); PYTEST=(uv run pytest)
else
  PY=(python3); PYTEST=(python3 -m pytest)
fi
printf 'runner: %s\n' "${PY[*]}"

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
    "${PY[@]}" -m sdk.cli check
fi
if want tools; then
  gate "Catálogo de tools (cli certify-tool: T-CONTRACT · T-DUR · G-AGNOSTIC)" \
    "${PY[@]}" -m sdk.cli certify-tool
  [[ -d "$GA/tests/tools" ]] && \
    gate "Tools (per-tool TCK + golden de la impl)" \
      "${PYTEST[@]}" tests/tools -q
fi
if want arch; then
  [[ -d "$GA/tests/architecture" ]] && \
    gate "Arquitectura (reglas G-*, manifests + tool contracts + serializador)" \
      "${PYTEST[@]}" tests/architecture -q
fi
if want cert; then
  [[ -d "$GA/tests/conformance" ]] && \
    gate "Certificación (TCK por agente, C0–C3)" \
      "${PYTEST[@]}" tests/conformance -q
fi
if want graphs; then
  [[ -d "$GA/tests/graphs" ]] && \
    gate "Determinismo (golden-replay, G-DET)" \
      "${PYTEST[@]}" tests/graphs -q
fi
if want integration; then
  [[ -d "$GA/tests/integration" ]] && \
    gate "Integración (manifest→runnable corre + recovery por execution-id + backend)" \
      "${PYTEST[@]}" tests/integration -q
fi
if want viewer; then
  [[ -f "$GA/viewer/server.py" ]] && \
    gate "Explorer (serializador del sistema sdk.graph + API del backend)" \
      "${PYTEST[@]}" tests/architecture/test_system_graph.py tests/integration/test_viewer_api.py -q
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
