#!/bin/bash
# stop-handoff.sh — Stop hook
# Eleva la Técnica 5 del HARNESS_ENGINEERING.md (handoff artifact estructurado para resets).
#
# Si la sesión termina con trabajo PENDIENTE (uncommitted edits + branch de HU activo),
# escribe $ARTIFACTS_DIR/handoff.yaml con el estado necesario para que la próxima sesión
# arranque sin perder contexto.
#
# Fields del handoff (per §6.3 del HARNESS_ENGINEERING.md):
#   - state.files_modified
#   - state.decisions_taken (extraídos de task-result.yaml si existe)
#   - state.blockers_pending (idem)
#   - state.next_step
#
# Si la sesión NO tiene trabajo pendiente (git clean), skip.
#
# Output: hubara_agency/.hubara/handoffs/<HU_ID-or-session>-<timestamp>.yaml
# Exit 0 siempre.

set +e

REPO_ROOT="${CLAUDE_PROJECT_DIR:-${REPO_ROOT:-/Users/edgm/Documents/Projects/AgencyHubara}}"
HANDOFFS_DIR="$REPO_ROOT/hubara_agency/.hubara/handoffs"
mkdir -p "$HANDOFFS_DIR"

INPUT=$(cat 2>/dev/null || echo "{}")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FILE_TS=$(date -u +"%Y%m%d-%H%M%S")

if [[ -z "$SESSION_ID" ]] || [[ "$SESSION_ID" == "null" ]]; then
  SESSION_ID="session-$FILE_TS"
fi

cd "$REPO_ROOT" 2>/dev/null || exit 0

# === Detectar si hay trabajo pendiente ===
MODIFIED=$(git diff --name-only HEAD 2>/dev/null)
STAGED=$(git diff --name-only --cached 2>/dev/null)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Si git está clean Y branch no es de HU, no necesita handoff
if [[ -z "$MODIFIED" ]] && [[ -z "$STAGED" ]] && [[ -z "$UNTRACKED" ]]; then
  if [[ "$BRANCH" != hu/* ]]; then
    exit 0
  fi
fi

# === Detectar HU activa (si branch es hu/<HU_ID>) ===
HU_ID=""
if [[ "$BRANCH" =~ ^hu/(HU-.+)$ ]]; then
  HU_ID="${BASH_REMATCH[1]}"
fi

# === Buscar task-result.yaml más reciente para extraer decisions/blockers ===
# El pipeline escribe esto en $ARTIFACTS_DIR/ durante la sesión.
# Si la sesión murió, podría estar en hubara_agency/.hubara/results/<HU_ID>/
TASK_RESULT_FILE=""
DECISIONS=""
BLOCKERS=""
NEXT_STEP=""

if [[ -n "$HU_ID" ]] && [[ -d "$REPO_ROOT/hubara_agency/.hubara/results/$HU_ID" ]]; then
  TASK_RESULT_FILE=$(find "$REPO_ROOT/hubara_agency/.hubara/results/$HU_ID" -name "task-result.yaml" 2>/dev/null | sort | tail -1)
fi

if [[ -f "$TASK_RESULT_FILE" ]]; then
  DECISIONS=$(grep -A20 "^decisions:" "$TASK_RESULT_FILE" 2>/dev/null | head -10 | sed 's/^/  /')
  BLOCKERS=$(grep -A5 "^blocked_reason:" "$TASK_RESULT_FILE" 2>/dev/null | head -3 | sed 's/^/  /')
fi

# === Determinar next_step ===
if [[ -n "$BLOCKERS" ]]; then
  NEXT_STEP="Resolver blocker reportado: $(grep "^blocked_reason:" "$TASK_RESULT_FILE" 2>/dev/null | head -1 | sed 's/blocked_reason://' | xargs)"
elif [[ -n "$MODIFIED" ]] || [[ -n "$STAGED" ]]; then
  NEXT_STEP="Revisar diff uncommitted + commit/discard según corresponda. Re-correr suite §10 del implementer si applies."
else
  NEXT_STEP="Continuar el próximo task del feature-plan-manifest.yaml."
fi

# === Escribir handoff ===
HANDOFF_FILE="$HANDOFFS_DIR/${HU_ID:-session}-${FILE_TS}.yaml"

cat > "$HANDOFF_FILE" <<EOF
# Handoff artifact — sesión interrumpida con trabajo pendiente
# Eleva T5 (handoff structured) del HARNESS_ENGINEERING.md.
# La próxima sesión leé este archivo en su bearings ritual.

session_id: "$SESSION_ID"
timestamp: "$TIMESTAMP"
branch: "$BRANCH"
head_commit: $(git rev-parse HEAD 2>/dev/null | head -c 12)
hu_id: "${HU_ID:-null}"

state:
  files_modified:
$(echo "$MODIFIED" | sed 's/^/    - /' | grep -v '^    - $' || echo "    []")

  files_staged:
$(echo "$STAGED" | sed 's/^/    - /' | grep -v '^    - $' || echo "    []")

  files_untracked:
$(echo "$UNTRACKED" | sed 's/^/    - /' | grep -v '^    - $' || echo "    []")

decisions_taken: |
${DECISIONS:-"  (no decisions extracted — task-result.yaml not found or empty)"}

blockers_pending: |
${BLOCKERS:-"  (no blockers extracted — task-result.yaml not found or no blocker reported)"}

next_step: |
  $NEXT_STEP

# Para usar este handoff: la próxima sesión del implementer (mismo HU_ID) lo lee
# automáticamente en su bearings ritual §0.5 y arranca con contexto completo.

related_artifacts:
  task_result: "${TASK_RESULT_FILE:-not_found}"
  refinement: "$REPO_ROOT/hubara_agency/.hubara/refinements/${HU_ID}-tech.md"
  plan: "$REPO_ROOT/hubara_agency/.hubara/plans/${HU_ID}/plugin-manifest.yaml"
EOF

# Notificar al operador
if [[ -n "$HU_ID" ]]; then
  echo "📦 stop-handoff: HU=$HU_ID con trabajo pendiente — handoff escrito en $HANDOFF_FILE" >&2
else
  echo "📦 stop-handoff: sesión con trabajo pendiente — handoff en $HANDOFF_FILE" >&2
fi

exit 0
