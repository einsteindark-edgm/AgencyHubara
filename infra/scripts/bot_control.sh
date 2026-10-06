#!/usr/bin/env bash
# Control del bot nuevo en producción, desde una máquina del equipo (2026-10-06).
# Corre `python -m src.plugins.chats.agent.sales.decisions.control <args>` DENTRO
# del contenedor de la API por SSM (send-command) y muestra lo que respondió.
#
#   infra/scripts/bot_control.sh estado
#   infra/scripts/bot_control.sh --por ana numeros agregar wa_57…
#   infra/scripts/bot_control.sh --por ana workflow canary
#   infra/scripts/bot_control.sh --por ana prueba-jev si
#   infra/scripts/bot_control.sh --por ana percepcion off      # apagar siempre pasa
#
# Cada cambio exige --por (queda firmado como comando:<quien>). La caja:
# BOT_INSTANCE_ID (por defecto la de app de hubara). Pide credenciales de AWS
# con ssm:SendCommand sobre esa caja.
set -euo pipefail

INSTANCE="${BOT_INSTANCE_ID:-i-042d95076277b40fa}"
REGION="${AWS_REGION:-us-east-1}"

if [ $# -eq 0 ]; then
  sed -n '2,14p' "$0" >&2
  exit 2
fi

# Lista blanca: cada argumento es una palabra simple. Nada llega a la caja
# como shell (ni `;`, ni `$(…)`, ni espacios).
args=""
for a in "$@"; do
  if [[ ! "$a" =~ ^[A-Za-z0-9_.:@+-]+$ ]]; then
    echo "argumento no permitido: $a (solo letras, números y _ . : @ + -)" >&2
    exit 2
  fi
  args+=" $a"
done

cmd="cd /opt/hubara && docker compose exec -T -w /app/hubara_agency api python -m src.plugins.chats.agent.sales.decisions.control$args"
params="$(python3 -c 'import json, sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$cmd")"

id="$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --comment "bot_control" \
  --parameters "$params" --query Command.CommandId --output text)"

status="Pending"
for _ in $(seq 1 45); do
  status="$(aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Success | Failed | Cancelled | TimedOut) break ;; esac
  sleep 2
done

aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
  --query StandardOutputContent --output text
aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
  --query StandardErrorContent --output text >&2

[ "$status" = "Success" ]
