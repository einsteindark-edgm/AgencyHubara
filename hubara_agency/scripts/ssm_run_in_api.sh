#!/bin/bash
# Uso: ssm_run.sh <script.py> [args...]  — corre un script Python DENTRO del
# container del API de prod vía SSM y devuelve stdout/stderr.
set -euo pipefail
SCRIPT="$1"; shift || true
ARGS="$*"
NAME=$(basename "$SCRIPT")
B64=$(base64 < "$SCRIPT" | tr -d '\n')
CID=$(aws ssm send-command --instance-ids i-042d95076277b40fa \
  --document-name AWS-RunShellScript --comment "hubara probe: $NAME" \
  --parameters "commands=[\"echo $B64 | base64 -d > /tmp/$NAME && docker cp /tmp/$NAME hubara-prod-api-1:/tmp/$NAME && docker exec -e PYTHONPATH=/app/hubara_agency -w /app/hubara_agency hubara-prod-api-1 python /tmp/$NAME $ARGS\"]" \
  --query Command.CommandId --output text)
for i in $(seq 1 30); do
  STATUS=$(aws ssm get-command-invocation --command-id "$CID" --instance-id i-042d95076277b40fa --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break;; esac
  sleep 2
done
echo "STATUS=$STATUS"
aws ssm get-command-invocation --command-id "$CID" --instance-id i-042d95076277b40fa --query StandardOutputContent --output text
aws ssm get-command-invocation --command-id "$CID" --instance-id i-042d95076277b40fa --query StandardErrorContent --output text | tail -15
