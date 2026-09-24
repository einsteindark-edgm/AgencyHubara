#!/bin/bash
# Autoapagado de la caja del laboratorio (cron cada 5 min). La caja se apaga sola
# tras IDLE_MIN minutos SIN trabajo. "Sin trabajo" = ningún contenedor
# lab-run-* vivo Y CPU < 10 %: una corrida pasa casi todo el tiempo esperando a
# los LLM con la CPU baja, así que la CPU sola la apagaría a mitad de camino.
set -euo pipefail

IDLE_MIN="${IDLE_MIN:-10}"
LAB_STATE="${LAB_STATE:-/var/lib/lab}"
COUNT_FILE="$LAB_STATE/idle_count"
mkdir -p "$LAB_STATE"
[ "$IDLE_MIN" -le 0 ] && exit 0
NEED=$(( IDLE_MIN / 5 )); [ "$NEED" -lt 1 ] && NEED=1

if [ -n "$(docker ps --filter name=lab-run- -q 2>/dev/null)" ]; then
  rm -f "$COUNT_FILE"
  exit 0
fi

USAGE=$(vmstat 1 2 | tail -1 | awk '{print 100 - $15}')
if [ "$USAGE" -ge 10 ]; then
  rm -f "$COUNT_FILE"
  exit 0
fi

C=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$C" > "$COUNT_FILE"
if [ "$C" -ge "$NEED" ]; then
  TOK=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/instance-id)
  REG=$(curl -s -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/placement/region)
  logger "lab autostop: sin trabajo $IDLE_MIN min -> stopping $IID"
  rm -f "$COUNT_FILE"
  aws ec2 stop-instances --region "$REG" --instance-ids "$IID"
fi
