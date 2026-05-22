#!/bin/bash
# smoke-test.sh — Bearings smoke test del repo AgencyHubara.
# Eleva la Técnica 4 del HARNESS_ENGINEERING.md (no construir sobre un sistema roto).
#
# Pragmático: NO levanta dev servers (eso es responsabilidad del operador). Verifica:
#   1. Git state — no merge conflicts pendientes
#   2. Backend Python — el código importa sin errores (catch syntax errors, broken imports)
#   3. Frontend TS — type check pasa (catch type errors heredados)
#   4. Spinal files — los critical no están corruptos
#   5. (Opcional) si dev servers detectados como UP, health checks
#
# Exit code:
#   0 = todo OK, podés implementar tranquilo
#   1 = sistema broken antes de empezar — STOP, arreglar primero
#   2 = warnings (degraded mode pero ejecutable)
#
# Usage:
#   bash hubara_agency/.hubara/smoke-test.sh
# Variables:
#   SKIP_FRONTEND=1   omitir verificación frontend (e.g., backend-only HU)
#   SKIP_BACKEND=1    omitir verificación backend (e.g., frontend-only HU)
#   VERBOSE=1         imprimir cada step

set +e

REPO_ROOT="${REPO_ROOT:-/Users/edgm/Documents/Projects/AgencyHubara}"
EXIT_CODE=0

vlog() {
  if [[ "${VERBOSE:-0}" == "1" ]]; then
    echo "  $1" >&2
  fi
}

step_ok() {
  echo "  ✅ $1"
}

step_fail() {
  echo "  ❌ $1"
  EXIT_CODE=1
}

step_warn() {
  echo "  ⚠️  $1"
  if [[ $EXIT_CODE -eq 0 ]]; then EXIT_CODE=2; fi
}

echo "🔍 smoke-test: bearings del repo"
cd "$REPO_ROOT" || { echo "❌ no puedo entrar a $REPO_ROOT"; exit 1; }

# === 1. Git state ===
echo "[1/5] Git state"
if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
  step_fail "merge conflicts pendientes — resolver antes de continuar"
  git diff --name-only --diff-filter=U
elif ! git rev-parse HEAD &>/dev/null; then
  step_fail "no es un repo git válido"
else
  HEAD=$(git rev-parse --short HEAD)
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  step_ok "branch=$BRANCH HEAD=$HEAD"
fi

# === 2. Spinal files (syntax check) ===
echo "[2/5] Spinal files no corruptos"
if [[ -f "hubara_agency/.hubara/spinal-files.yaml" ]]; then
  if python3 -c "import yaml; yaml.safe_load(open('hubara_agency/.hubara/spinal-files.yaml'))" 2>/dev/null; then
    step_ok "spinal-files.yaml parsea"
  else
    step_fail "spinal-files.yaml corrupto"
  fi
else
  step_warn "spinal-files.yaml no existe (esperado en hubara_agency/.hubara/)"
fi

# === 3. Backend Python importable ===
if [[ "${SKIP_BACKEND:-0}" != "1" ]]; then
  echo "[3/5] Backend importa sin errores"
  cd "$REPO_ROOT/hubara_agency"

  if ! command -v uv &> /dev/null; then
    step_warn "uv no disponible — saltando backend check"
  else
    # Smoke import — main + run_workers son los entry points críticos
    IMPORT_OUT=$(timeout 30 uv run --quiet python -c "
import src.main
import src.run_workers
print('OK')
" 2>&1)
    IMPORT_EXIT=$?

    if [[ $IMPORT_EXIT -eq 0 ]] && [[ "$IMPORT_OUT" == *"OK"* ]]; then
      step_ok "src.main + src.run_workers importan"
    else
      step_fail "imports backend rotos — arreglar antes de continuar"
      echo "$IMPORT_OUT" | head -10 | sed 's/^/      /'
    fi
  fi
  cd "$REPO_ROOT"
else
  vlog "[3/5] Backend check skipped (SKIP_BACKEND=1)"
fi

# === 4. Frontend TS type-checks ===
if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  echo "[4/5] Frontend type-check pasa"
  cd "$REPO_ROOT/frontend_dashboard"

  if [[ ! -f "node_modules/.bin/tsc" ]] && ! command -v npx &> /dev/null; then
    step_warn "tsc/npx no disponibles — saltando frontend check (correr npm install)"
  else
    TSC_OUT=$(timeout 60 npx tsc -b --noEmit 2>&1)
    TSC_EXIT=$?
    if [[ $TSC_EXIT -eq 0 ]]; then
      step_ok "tsc -b limpio"
    else
      ERROR_COUNT=$(echo "$TSC_OUT" | grep -cE "error TS[0-9]+")
      step_fail "$ERROR_COUNT type error(s) heredados — arreglar antes de continuar"
      echo "$TSC_OUT" | grep -E "error TS" | head -5 | sed 's/^/      /'
    fi
  fi
  cd "$REPO_ROOT"
else
  vlog "[4/5] Frontend check skipped (SKIP_FRONTEND=1)"
fi

# === 4.5. Codegraph staleness (informativo, F8 fix) ===
# Si el index está stale, los hints del CODEMAP / impact analysis no son confiables.
if [[ -d "$REPO_ROOT/.codegraph" ]]; then
  LAST_INDEXED_FILE=$(find "$REPO_ROOT/.codegraph" -name "db.sqlite" -type f 2>/dev/null | head -1)
  if [[ -n "$LAST_INDEXED_FILE" ]]; then
    INDEX_AGE_MIN=$(( ($(date +%s) - $(stat -f %m "$LAST_INDEXED_FILE" 2>/dev/null || stat -c %Y "$LAST_INDEXED_FILE" 2>/dev/null || echo 0)) / 60 ))
    if [[ "$INDEX_AGE_MIN" -gt 60 ]]; then
      step_warn "codegraph index ${INDEX_AGE_MIN}min stale (>1h) — `codegraph_*` tools pueden devolver resultados desactualizados. Re-indexar con: codegraph init -i"
    else
      step_ok "codegraph index reciente (${INDEX_AGE_MIN}min)"
    fi
  fi
fi

# === 5. Dev servers (opcional, no-blocking) ===
echo "[5/5] Dev servers (informativo)"
if curl -s -m 2 -o /dev/null -w "%{http_code}" http://localhost:8000/api/dashboard/sessions 2>/dev/null | grep -q "200\|404"; then
  step_ok "backend dev server UP en :8000"
else
  vlog "backend dev server DOWN (esperado si no está corriendo)"
fi

if curl -s -m 2 -o /dev/null -w "%{http_code}" http://localhost:5173 2>/dev/null | grep -q "200"; then
  step_ok "frontend dev server UP en :5173"
else
  vlog "frontend dev server DOWN (esperado si no está corriendo)"
fi

# === Resumen ===
echo
case $EXIT_CODE in
  0) echo "✅ smoke-test: OK — podés implementar" ;;
  2) echo "⚠️  smoke-test: warnings (degraded mode) — implementar con cuidado" ;;
  *) echo "❌ smoke-test: BROKEN — arreglar antes de implementar" ;;
esac

exit $EXIT_CODE
