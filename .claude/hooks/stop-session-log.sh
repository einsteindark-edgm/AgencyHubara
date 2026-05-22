#!/bin/bash
# stop-session-log.sh — Stop hook
# Eleva T13 (captura de evidencia). Registra el footprint de la sesión en disco para que
# luego /review-last-session (o el operador) pueda reflexionar sobre los cambios.
#
# El hook en sí NO invoca al modelo (limitación de Stop hooks de Claude Code).
# Solo deposita evidencia. La reflexión se hace después, por demanda.
#
# Captura:
#   - session_id (del stdin JSON, fallback a timestamp)
#   - archivos modificados (git diff)
#   - commit HEAD
#   - últimos N commits del branch
#
# Output: archivo en hubara_agency/.hubara/sessions/<session-id>-<timestamp>.json
# Exit 0 siempre. Stderr para resumen visible al operador.

set +e

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"
SESSIONS_DIR="$REPO_ROOT/hubara_agency/.hubara/sessions"
mkdir -p "$SESSIONS_DIR"

INPUT=$(cat 2>/dev/null || echo "{}")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')
STOP_REASON=$(echo "$INPUT" | jq -r '.stop_reason // "unknown"')
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Fallback a timestamp si no hay session_id
if [[ -z "$SESSION_ID" ]] || [[ "$SESSION_ID" == "null" ]]; then
  SESSION_ID=$(date -u +"session-%Y%m%d-%H%M%S")
fi

OUTPUT_FILE="$SESSIONS_DIR/${SESSION_ID}.json"

cd "$REPO_ROOT" 2>/dev/null || exit 0

# Recopilar metadata
MODIFIED_TRACKED=$(git diff --name-only HEAD 2>/dev/null || echo "")
STAGED=$(git diff --name-only --cached 2>/dev/null || echo "")
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null || echo "")
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
HEAD_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
RECENT_COMMITS=$(git log --oneline -5 2>/dev/null || echo "")

# Detectar si afectó plugins (señal para el arch-gate hook)
TOUCHED_BACKEND_PLUGINS=$(echo -e "$MODIFIED_TRACKED\n$STAGED\n$UNTRACKED" | grep -c '^hubara_agency/src/plugins/' || true)
TOUCHED_FRONTEND_PLUGINS=$(echo -e "$MODIFIED_TRACKED\n$STAGED\n$UNTRACKED" | grep -c '^frontend_dashboard/src/plugins/' || true)
TOUCHED_PLATFORM=$(echo -e "$MODIFIED_TRACKED\n$STAGED\n$UNTRACKED" | grep -c '^hubara_agency/src/platform/' || true)
TOUCHED_SHARED_FE=$(echo -e "$MODIFIED_TRACKED\n$STAGED\n$UNTRACKED" | grep -cE '^frontend_dashboard/src/(shared|entities)/' || true)

# Escribir JSON
jq -n \
  --arg session_id "$SESSION_ID" \
  --arg timestamp "$TIMESTAMP" \
  --arg stop_reason "$STOP_REASON" \
  --arg branch "$CURRENT_BRANCH" \
  --arg head "$HEAD_COMMIT" \
  --arg recent "$RECENT_COMMITS" \
  --arg modified "$MODIFIED_TRACKED" \
  --arg staged "$STAGED" \
  --arg untracked "$UNTRACKED" \
  --arg b_plugins "$TOUCHED_BACKEND_PLUGINS" \
  --arg f_plugins "$TOUCHED_FRONTEND_PLUGINS" \
  --arg platform "$TOUCHED_PLATFORM" \
  --arg shared_fe "$TOUCHED_SHARED_FE" \
  '{
    session_id: $session_id,
    timestamp: $timestamp,
    stop_reason: $stop_reason,
    branch: $branch,
    head_commit: $head,
    recent_commits: ($recent | split("\n")),
    files_modified_tracked: ($modified | split("\n") | map(select(length > 0))),
    files_staged: ($staged | split("\n") | map(select(length > 0))),
    files_untracked: ($untracked | split("\n") | map(select(length > 0))),
    touched: {
      backend_plugins: ($b_plugins | tonumber),
      frontend_plugins: ($f_plugins | tonumber),
      backend_platform: ($platform | tonumber),
      frontend_shared: ($shared_fe | tonumber)
    }
  }' > "$OUTPUT_FILE"

# Resumen al operador (stderr)
MODIFIED_COUNT=$(echo -n "$MODIFIED_TRACKED" | grep -c '^' || echo 0)
echo "📝 stop-session-log: $MODIFIED_COUNT archivos modificados — log en $OUTPUT_FILE" >&2

exit 0
