#!/bin/bash
# metrics-aggregator.sh — Agrega session logs en métricas por HU/sesión.
# Eleva T10 (métricas continuas para detectar degradación entre stress tests trimestrales).
#
# Lee:
#   hubara_agency/.hubara/sessions/*.json   (session metadata del stop-session-log hook)
#   hubara_agency/.hubara/agent-logs/*.jsonl (tool calls por session)
#   hubara_agency/.hubara/results/*/        (task-results y evaluation por HU)
#
# Produce:
#   hubara_agency/.hubara/metrics.jsonl (append-only)
#
# Usage:
#   bash hubara_agency/.hubara/metrics-aggregator.sh [since-date]
# Default: desde la última entry en metrics.jsonl (incremental).

set +e

REPO_ROOT="${REPO_ROOT:-/Users/edgm/Documents/Projects/AgencyHubara}"
SESSIONS_DIR="$REPO_ROOT/hubara_agency/.hubara/sessions"
LOGS_DIR="$REPO_ROOT/hubara_agency/.hubara/agent-logs"
RESULTS_DIR="$REPO_ROOT/hubara_agency/.hubara/results"
OUTPUT="$REPO_ROOT/hubara_agency/.hubara/metrics.jsonl"

mkdir -p "$(dirname "$OUTPUT")"

# Por cada session.json en $SESSIONS_DIR, agregar a metrics.jsonl si no está ya
# (deduplicación by session_id).

echo "📊 Aggregando metrics..." >&2

PROCESSED=0
SKIPPED=0

if [[ ! -d "$SESSIONS_DIR" ]]; then
  echo "WARN: $SESSIONS_DIR no existe — no hay sessions para agregar." >&2
  exit 0
fi

for SESSION_JSON in "$SESSIONS_DIR"/*.json; do
  [[ ! -f "$SESSION_JSON" ]] && continue

  SESSION_ID=$(jq -r '.session_id // empty' "$SESSION_JSON" 2>/dev/null)
  [[ -z "$SESSION_ID" ]] && continue

  # Skip si ya está en metrics.jsonl
  if [[ -f "$OUTPUT" ]] && grep -q "\"session_id\":\"$SESSION_ID\"" "$OUTPUT" 2>/dev/null; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Recolectar metrics de esta session
  TIMESTAMP=$(jq -r '.timestamp // ""' "$SESSION_JSON")
  BRANCH=$(jq -r '.branch // ""' "$SESSION_JSON")
  HEAD=$(jq -r '.head_commit // ""' "$SESSION_JSON")
  FILES_MODIFIED=$(jq -r '.files_modified_tracked | length' "$SESSION_JSON")
  FILES_STAGED=$(jq -r '.files_staged | length' "$SESSION_JSON")
  TOUCHED_BE=$(jq -r '.touched.backend_plugins // 0' "$SESSION_JSON")
  TOUCHED_FE=$(jq -r '.touched.frontend_plugins // 0' "$SESSION_JSON")

  # Tool calls de la session (de agent-logs/<session>.jsonl)
  TOOL_LOG="$LOGS_DIR/${SESSION_ID}.jsonl"
  TOTAL_TOOLS=0
  TOOL_SUMMARY="{}"
  if [[ -f "$TOOL_LOG" ]]; then
    TOTAL_TOOLS=$(wc -l < "$TOOL_LOG" | tr -d ' ')
    TOOL_SUMMARY=$(jq -s 'group_by(.tool) | map({key: .[0].tool, value: length}) | from_entries' "$TOOL_LOG" 2>/dev/null || echo "{}")
  fi

  # Si la session toca un HU específico (rama hu/HU-...), capturar el HU
  HU_ID=""
  if [[ "$BRANCH" =~ ^hu/(HU-.+)$ ]]; then
    HU_ID="${BASH_REMATCH[1]}"
  fi

  # Si hay evaluation.yaml para este HU, capturar score
  EVAL_SCORE="null"
  EVAL_VERDICT="null"
  if [[ -n "$HU_ID" ]] && [[ -d "$RESULTS_DIR/$HU_ID" ]]; then
    EVAL_FILE=$(find "$RESULTS_DIR/$HU_ID" -name "evaluation.yaml" 2>/dev/null | head -1)
    if [[ -f "$EVAL_FILE" ]]; then
      EVAL_SCORE=$(grep -E "^weighted_average:" "$EVAL_FILE" | awk '{print $2}' | head -1)
      EVAL_VERDICT=$(grep -E "^verdict:" "$EVAL_FILE" | awk '{print $2}' | tr -d '"' | head -1)
      [[ -z "$EVAL_SCORE" ]] && EVAL_SCORE="null"
      [[ -z "$EVAL_VERDICT" ]] && EVAL_VERDICT="null"
    fi
  fi

  # Emit JSONL line
  jq -n \
    --arg session "$SESSION_ID" \
    --arg ts "$TIMESTAMP" \
    --arg branch "$BRANCH" \
    --arg head "$HEAD" \
    --arg hu_id "$HU_ID" \
    --arg fm "$FILES_MODIFIED" \
    --arg fs "$FILES_STAGED" \
    --arg tbe "$TOUCHED_BE" \
    --arg tfe "$TOUCHED_FE" \
    --arg tt "$TOTAL_TOOLS" \
    --argjson ts_summary "$TOOL_SUMMARY" \
    --arg eval_score "$EVAL_SCORE" \
    --arg eval_verdict "$EVAL_VERDICT" \
    '{
      session_id: $session,
      timestamp: $ts,
      branch: $branch,
      head_commit: $head,
      hu_id: $hu_id,
      files_modified: ($fm | tonumber),
      files_staged: ($fs | tonumber),
      touched_backend_plugins: ($tbe | tonumber),
      touched_frontend_plugins: ($tfe | tonumber),
      total_tool_calls: ($tt | tonumber),
      tool_calls_by_type: $ts_summary,
      evaluator_score: ($eval_score),
      evaluator_verdict: $eval_verdict
    }' >> "$OUTPUT"

  PROCESSED=$((PROCESSED + 1))
done

echo "✅ metrics-aggregator: procesadas=$PROCESSED skipped=$SKIPPED" >&2
echo "   Output: $OUTPUT ($(wc -l < "$OUTPUT" 2>/dev/null | tr -d ' ') total entries)" >&2

# Resumen rápido de tendencia (últimas 10 sesiones)
if [[ -f "$OUTPUT" ]]; then
  echo >&2
  echo "📈 Últimas 10 sesiones (tool calls / eval score / verdict):" >&2
  tail -10 "$OUTPUT" | jq -r '"\(.timestamp[:10]) | tools=\(.total_tool_calls) | eval=\(.evaluator_score // "n/a") | \(.evaluator_verdict // "n/a") | \(.hu_id // "no-hu")"' 2>/dev/null >&2
fi

exit 0
