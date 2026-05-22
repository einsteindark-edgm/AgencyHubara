#!/bin/bash
# post-tool-log.sh — Universal PostToolUse hook para observabilidad.
# Eleva la Técnica 9 del HARNESS_ENGINEERING.md (logs auditables).
#
# Registra cada tool call en JSONL (line-delimited JSON) por session, para que:
#   - Si una sesión introduce una regresión, podemos reconstruir qué hizo el agente
#   - Si una sesión consume demasiados tokens / tiempo, podemos identificar la causa
#   - Si un tool falla recurrentemente, lo detectamos en agregado post-mortem
#
# NO guarda inputs completos por privacy — solo hash (sha256:12) para correlación.
# El log es appendable, low-overhead (single jq call por tool call).
#
# Output: hubara_agency/.hubara/agent-logs/<session-id>.jsonl
# Exit 0 siempre (PostToolUse no puede bloquear).

set +e

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"
LOG_DIR="$REPO_ROOT/hubara_agency/.hubara/agent-logs"
mkdir -p "$LOG_DIR" 2>/dev/null

INPUT=$(cat)
TS=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
TS_EPOCH=$(date -u +%s)

SESSION=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // "unknown"' 2>/dev/null)

LOG_FILE="$LOG_DIR/${SESSION}.jsonl"

# Hash el input completo para correlación sin guardar datos
INPUT_HASH=$(echo -n "$INPUT" | shasum -a 256 | cut -c1-12 2>/dev/null || echo "no-hash")

# Determinar outcome
IS_ERROR=$(echo "$INPUT" | jq -r '.tool_result.is_error // false' 2>/dev/null)
OUTCOME="success"
[ "$IS_ERROR" == "true" ] && OUTCOME="error"

# Extraer info útil sin filtrar datos sensibles
TOOL_SUMMARY=""
case "$TOOL" in
  Bash)
    # Solo prefijo del comando (primeras 50 chars)
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null | head -c 50 | tr '\n' ' ')
    TOOL_SUMMARY="$CMD"
    ;;
  Read|Edit|Write)
    # File path (público; útil para audit)
    FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null)
    TOOL_SUMMARY="${FILE##*/AgencyHubara/}"  # solo path relativo
    ;;
  Agent)
    # Subagent type
    ST=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // ""' 2>/dev/null)
    TOOL_SUMMARY="subagent=$ST"
    ;;
  *)
    TOOL_SUMMARY=""
    ;;
esac

# Append a JSONL
jq -n \
  --arg ts "$TS" \
  --arg ts_epoch "$TS_EPOCH" \
  --arg session "$SESSION" \
  --arg tool "$TOOL" \
  --arg hash "$INPUT_HASH" \
  --arg outcome "$OUTCOME" \
  --arg summary "$TOOL_SUMMARY" \
  '{
    ts: $ts,
    ts_epoch: ($ts_epoch | tonumber),
    session: $session,
    tool: $tool,
    summary: $summary,
    input_hash: $hash,
    outcome: $outcome
  }' >> "$LOG_FILE" 2>/dev/null

exit 0
