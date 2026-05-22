#!/bin/bash
# stop-arch-gate.sh — Stop hook
# Eleva T13 (deterministic arch enforcement). Si la sesión modificó código bajo
# plugins/ o platform/ o shared/ entidades, corre architecture gates al cerrar para
# que el operador no quede ciego ante una regresión silenciosa.
#
# NO bloquea (el modelo ya cerró). Solo informa al operador via stderr + log file.
#
# Gates corridos según paths tocados:
#   - hubara_agency/src/{plugins,platform}/ → lint-imports + pytest -m architecture
#   - frontend_dashboard/src/{plugins,shared,entities}/ → npm run test:arch
#
# Timeouts: cada gate tiene cap individual para no colgar el cierre de sesión.
#
# Output: archivo en hubara_agency/.hubara/sessions/<session-id>-arch-gate.log + stderr.

set +e

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"
SESSIONS_DIR="$REPO_ROOT/hubara_agency/.hubara/sessions"
mkdir -p "$SESSIONS_DIR"

INPUT=$(cat 2>/dev/null || echo "{}")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

if [[ -z "$SESSION_ID" ]] || [[ "$SESSION_ID" == "null" ]]; then
  SESSION_ID=$(date -u +"session-%Y%m%d-%H%M%S")
fi

LOG_FILE="$SESSIONS_DIR/${SESSION_ID}-arch-gate.log"

cd "$REPO_ROOT" 2>/dev/null || exit 0

# Detectar paths tocados en esta sesión (uncommitted)
TOUCHED=$(git status --porcelain 2>/dev/null | awk '{print $NF}')

NEEDS_BACKEND_GATE=false
NEEDS_FRONTEND_GATE=false

if echo "$TOUCHED" | grep -qE '^hubara_agency/src/(plugins|platform)/'; then
  NEEDS_BACKEND_GATE=true
fi

if echo "$TOUCHED" | grep -qE '^frontend_dashboard/src/(plugins|shared|entities|app)/'; then
  NEEDS_FRONTEND_GATE=true
fi

# Si no se tocó nada relevante, salir silenciosamente
if [[ "$NEEDS_BACKEND_GATE" == "false" ]] && [[ "$NEEDS_FRONTEND_GATE" == "false" ]]; then
  exit 0
fi

# Header del log
{
  echo "=== Architecture gates — $(date -u +'%Y-%m-%dT%H:%M:%SZ') ==="
  echo "Session: $SESSION_ID"
  echo "Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "Backend gate needed: $NEEDS_BACKEND_GATE"
  echo "Frontend gate needed: $NEEDS_FRONTEND_GATE"
  echo
} > "$LOG_FILE"

GATE_FAILED=false

# === Backend gates ===
if [[ "$NEEDS_BACKEND_GATE" == "true" ]]; then
  echo "--- backend: lint-imports ---" >> "$LOG_FILE"
  cd "$REPO_ROOT/hubara_agency"

  LINT_OUTPUT=$(timeout 60 uv run lint-imports 2>&1)
  LINT_EXIT=$?
  echo "$LINT_OUTPUT" >> "$LOG_FILE"
  echo "exit=$LINT_EXIT" >> "$LOG_FILE"
  echo >> "$LOG_FILE"

  if [[ $LINT_EXIT -ne 0 ]]; then
    GATE_FAILED=true
    echo "❌ stop-arch-gate: lint-imports FALLÓ — ver $LOG_FILE" >&2
  fi

  echo "--- backend: pytest -m architecture ---" >> "$LOG_FILE"
  PYTEST_OUTPUT=$(timeout 180 uv run pytest -m architecture --tb=no -q 2>&1)
  PYTEST_EXIT=$?
  echo "$PYTEST_OUTPUT" >> "$LOG_FILE"
  echo "exit=$PYTEST_EXIT" >> "$LOG_FILE"
  echo >> "$LOG_FILE"

  if [[ $PYTEST_EXIT -ne 0 ]]; then
    GATE_FAILED=true
    echo "❌ stop-arch-gate: pytest -m architecture FALLÓ — ver $LOG_FILE" >&2
  fi
fi

# === Frontend gates ===
if [[ "$NEEDS_FRONTEND_GATE" == "true" ]]; then
  echo "--- frontend: npm run test:arch ---" >> "$LOG_FILE"
  cd "$REPO_ROOT/frontend_dashboard"

  NPM_OUTPUT=$(timeout 180 npm run test:arch 2>&1)
  NPM_EXIT=$?
  echo "$NPM_OUTPUT" >> "$LOG_FILE"
  echo "exit=$NPM_EXIT" >> "$LOG_FILE"
  echo >> "$LOG_FILE"

  if [[ $NPM_EXIT -ne 0 ]]; then
    GATE_FAILED=true
    echo "❌ stop-arch-gate: npm run test:arch FALLÓ — ver $LOG_FILE" >&2
  fi
fi

if [[ "$GATE_FAILED" == "true" ]]; then
  echo "⚠️  stop-arch-gate: uno o más gates fallaron. Arreglar antes de commit/PR." >&2
else
  echo "✅ stop-arch-gate: todos los gates pasaron." >&2
fi

exit 0
