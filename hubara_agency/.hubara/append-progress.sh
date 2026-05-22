#!/bin/bash
# append-progress.sh — helper para appendear entries al progress log narrativo por HU.
# Eleva T1 (log narrativo cronológico, distinto del plan estructurado).
#
# El plan estructurado (feature-plan-manifest.yaml) dice QUÉ falta hacer.
# El progress log dice QUÉ PASÓ entre sesiones — útil para resumir tras un reset.
#
# Usage (invocado al final de un skill):
#   bash hubara_agency/.hubara/append-progress.sh \
#     --hu-id HU-20260521-100000-add-tag \
#     --skill hubara-implementer-archon \
#     --what "Implementé F03 add-customer-tag; gates OK" \
#     --blockers "ninguno" \
#     --next "Iteración del evaluator pre-PR"
#
# Output: appendea a hubara_agency/.hubara/progress-log/<HU_ID>.md

set +e

REPO_ROOT="${REPO_ROOT:-/Users/edgm/Documents/Projects/AgencyHubara}"
LOG_DIR="$REPO_ROOT/hubara_agency/.hubara/progress-log"
mkdir -p "$LOG_DIR"

HU_ID=""
SKILL=""
WHAT=""
BLOCKERS=""
NEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hu-id)    HU_ID="$2";    shift 2 ;;
    --skill)    SKILL="$2";    shift 2 ;;
    --what)     WHAT="$2";     shift 2 ;;
    --blockers) BLOCKERS="$2"; shift 2 ;;
    --next)     NEXT="$2";     shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$HU_ID" ]] || [[ -z "$SKILL" ]] || [[ -z "$WHAT" ]]; then
  echo "Usage: $0 --hu-id <HU_ID> --skill <name> --what '<descripción>' [--blockers '...'] [--next '...']" >&2
  exit 1
fi

LOG_FILE="$LOG_DIR/${HU_ID}.md"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
HEAD_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null)

# Si el log file no existe, crear header
if [[ ! -f "$LOG_FILE" ]]; then
  cat > "$LOG_FILE" <<EOF
# Progress log — $HU_ID

> Log cronológico de qué pasó por sesión / por skill. Diferente del plan estructurado
> (que dice QUÉ falta). Este dice QUÉ PASÓ — útil para reconstruir contexto tras un reset.

EOF
fi

# Append entry
cat >> "$LOG_FILE" <<EOF

## $TIMESTAMP — \`$SKILL\`

- **Branch:** $BRANCH @ $HEAD_SHA
- **What:** $WHAT
EOF

[[ -n "$BLOCKERS" ]] && echo "- **Blockers:** $BLOCKERS" >> "$LOG_FILE"
[[ -n "$NEXT" ]]     && echo "- **Next:** $NEXT" >> "$LOG_FILE"

echo "📒 progress-log: entry appendeada a $LOG_FILE"
