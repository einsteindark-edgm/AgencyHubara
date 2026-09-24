#!/bin/bash
# Orden de la API de producción a la caja del laboratorio (LABORATORIO_CONVERSACIONES_PLAN.md §3.7).
# Llega por SSM SendCommand (AWS-RunShellScript), no por red:
#
#   /opt/lab/dispatch.sh <RUN_ID> <IMAGEN_GHCR>
#
# Idempotente por RUN_ID: si la activity que da la orden se reintenta, la caja
# reconoce el id y NO arranca otra corrida ("already_dispatched"). El .env sale
# SOLO de /hubara-lab (el rol de la caja no puede leer los parámetros de
# producción). Imprime en la última línea: dispatched | already_dispatched.
set -euo pipefail

LAB_HOME="${LAB_HOME:-/opt/lab}"
LAB_ROOT="${LAB_ROOT:-/lab}"
RUN_ID="${1:-}"
IMAGE="${2:-}"

[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,63}$ ]] || { echo "invalid_run_id" >&2; exit 2; }
[[ "$IMAGE" =~ ^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$ ]] || { echo "invalid_image" >&2; exit 2; }

# shellcheck disable=SC1091
source "$LAB_HOME/box.env"   # AWS_REGION, LAB_BUCKET, GHCR_OWNER
RUN_DIR="$LAB_ROOT/runs/$RUN_ID"
mkdir -p "$LAB_ROOT/runs"

exec 9>"$LAB_ROOT/runs/.dispatch.lock"
flock -w 60 9 2>/dev/null || true   # sin flock (tests) sigue: el marcador sigue siendo la defensa

if [ -f "$RUN_DIR/dispatched" ]; then
  echo "already_dispatched"
  exit 0
fi
mkdir -p "$RUN_DIR"

umask 077
tmp="$(mktemp)"
aws ssm get-parameters-by-path \
  --region "$AWS_REGION" \
  --path /hubara-lab \
  --with-decryption --recursive \
  --query "Parameters[].[Name,Value]" --output text \
  | awk -F'\t' '{ n=$1; sub(/.*\//,"",n); print n "=" $2 }' > "$tmp"
{
  echo "LAB_BUCKET=$LAB_BUCKET"
  echo "HUBARA_IMAGE=$IMAGE"
} >> "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$LAB_HOME/.env"

grep -m1 '^GHCR_PULL_TOKEN=' "$LAB_HOME/.env" | cut -d= -f2- \
  | docker login ghcr.io -u "${GHCR_OWNER:-lab}" --password-stdin >/dev/null 2>&1 || true

COMPOSE=(docker compose -f "$LAB_HOME/docker-compose.lab.yml" --env-file "$LAB_HOME/.env")
"${COMPOSE[@]}" pull --quiet sales_lab >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d temporal litellm
"${COMPOSE[@]}" run -d --name "lab-run-$RUN_ID" -e "LAB_RUN_ID=$RUN_ID" sales_lab

date -u +%FT%TZ > "$RUN_DIR/dispatched"
echo "dispatched"
