#!/bin/bash
# Uso: ssm_run.sh <script.py> [args...]  — corre un script Python DENTRO del
# container del API de prod vía SSM y devuelve stdout/stderr.
#
# La caja de app se busca por su tag Name (como infra/scripts/bot_control.sh;
# forge lo traduce al cliente nuevo) o se pasa con API_INSTANCE_ID=i-…. Nunca
# un id escrito acá: en la cuenta compartida, desde un clon correría código en
# la producción de otro proyecto.
set -euo pipefail
SCRIPT="$1"; shift || true
ARGS="$*"
NAME=$(basename "$SCRIPT")
REGION="${AWS_REGION:-us-east-1}"
CONTAINER="hubara-prod-api-1"
INSTANCE="${API_INSTANCE_ID:-}"
if [ -z "$INSTANCE" ]; then
  INSTANCE="$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=agencyhubara-hubara-app" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)"
  if [[ ! "$INSTANCE" =~ ^i-[0-9a-f]+$ ]]; then
    echo "no encontré la caja de app prendida; pasa API_INSTANCE_ID=i-…" >&2
    exit 3
  fi
fi
B64=$(base64 < "$SCRIPT" | tr -d '\n')
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --comment "probe: $NAME" \
  --parameters "commands=[\"echo $B64 | base64 -d > /tmp/$NAME && docker cp /tmp/$NAME $CONTAINER:/tmp/$NAME && docker exec -e PYTHONPATH=/app/hubara_agency -w /app/hubara_agency $CONTAINER python /tmp/$NAME $ARGS\"]" \
  --query Command.CommandId --output text)
for i in $(seq 1 30); do
  STATUS=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break;; esac
  sleep 2
done
echo "STATUS=$STATUS"
aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$INSTANCE" --query StandardOutputContent --output text
aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$INSTANCE" --query StandardErrorContent --output text | tail -15
