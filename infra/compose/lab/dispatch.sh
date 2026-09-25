#!/bin/bash
# Orden de la API de producción a la caja del laboratorio (LABORATORIO_CONVERSACIONES_PLAN.md §3.7).
# Llega por SSM SendCommand (AWS-RunShellScript), no por red:
#
#   /opt/lab/dispatch.sh <RUN_ID> <IMAGEN_GHCR>
#
# Imprime en la última línea:
#   dispatched          arrancó el runner de la corrida
#   already_dispatched  la orden se repitió (reintento de la activity) y el
#                       runner sigue vivo o ya terminó bien: no arranca otro
#   lost                la corrida se ordenó pero su runner ya no existe o murió
#                       (la caja se reinició: Temporal se borra en cada arranque)
#   busy                otra corrida sigue viva en la caja: una a la vez
#
# Las llaves salen SOLO de /hubara-lab (el rol de la caja no puede leer los
# parámetros de producción) y cada servicio recibe solo las suyas. El
# litellm_config.yaml sale de la imagen de la corrida: el mismo de producción.
set -euo pipefail

LAB_HOME="${LAB_HOME:-/opt/lab}"
LAB_ROOT="${LAB_ROOT:-/lab}"
RUN_ID="${1:-}"
IMAGE="${2:-}"
CONFIG_IN_IMAGE=/app/exoclaw-temporal/litellm_config.yaml

[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,63}$ ]] || { echo "invalid_run_id" >&2; exit 2; }
[[ "$IMAGE" =~ ^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$ ]] || { echo "invalid_image" >&2; exit 2; }

# shellcheck disable=SC1091
source "$LAB_HOME/box.env"   # AWS_REGION, LAB_BUCKET, GHCR_OWNER
RUN_DIR="$LAB_ROOT/runs/$RUN_ID"
NAME="lab-run-$RUN_ID"
mkdir -p "$LAB_ROOT/runs" "$LAB_ROOT/bench"

# Una orden a la vez. Un reintento de la activity mientras la primera orden
# todavía baja la imagen espera acá; si no consigue el candado, falla (la
# activity reintenta) en vez de arrancar un segundo runner.
exec 9>"$LAB_ROOT/runs/.dispatch.lock"
flock -w 240 9 || { echo "dispatch_lock_timeout: otra orden sigue en curso" >&2; exit 75; }

if [ -f "$RUN_DIR/dispatched" ]; then
  state="$(timeout 20 docker container inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$NAME" 2>/dev/null || true)"
  case "$state" in
    "running "*|"exited 0") echo "already_dispatched" ;;
    *) echo "lost" ;;
  esac
  exit 0
fi

running="$(timeout 20 docker ps --filter name=lab-run- --format '{{.Names}}' 2>/dev/null || true)"
if [[ $'\n'"$running"$'\n' == *$'\n'"$NAME"$'\n'* ]]; then
  # El runner de ESTA corrida ya arrancó pero la orden anterior murió antes de
  # dejar el marcador: es la misma corrida, no otra.
  date -u +%FT%TZ > "$RUN_DIR/dispatched"
  echo "already_dispatched"
  exit 0
fi
if [ -n "$running" ]; then
  echo "busy"
  exit 0
fi

mkdir -p "$RUN_DIR"
umask 077
params="$(mktemp)"
trap 'rm -f "$params"' EXIT
aws ssm get-parameters-by-path \
  --region "$AWS_REGION" \
  --path /hubara-lab \
  --with-decryption --recursive \
  --query "Parameters[].[Name,Value]" --output text \
  | awk -F'\t' '{ n=$1; sub(/.*\//,"",n); print n "=" $2 }' > "$params"

# El token de GHCR va directo a docker login: nunca a un env file.
grep -m1 '^GHCR_PULL_TOKEN=' "$params" | cut -d= -f2- \
  | docker login ghcr.io -u "${GHCR_OWNER:-lab}" --password-stdin >/dev/null 2>&1 || true

_write_env() {  # destino, contenido: escribe 0600 y reemplaza de una vez
  local tmp
  tmp="$(mktemp "$LAB_HOME/.env.XXXXXX")"
  printf '%s\n' "$2" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$1"
}
keys="$(grep -v '^GHCR_PULL_TOKEN=' "$params" || true)"
_write_env "$LAB_HOME/litellm.env" "$keys"
_write_env "$LAB_HOME/sales_lab.env" "$keys
LAB_BUCKET=$LAB_BUCKET"
_write_env "$LAB_HOME/compose.env" "HUBARA_IMAGE=$IMAGE"

COMPOSE=(docker compose -f "$LAB_HOME/docker-compose.lab.yml" --env-file "$LAB_HOME/compose.env")

# Disco: cada corrida con una imagen nueva deja ~1 GB; sin esto el disco se
# llena (mismo incidente que el deploy de producción, #240).
docker container prune -f --filter until=168h >/dev/null 2>&1 || true
docker image prune -af --filter until=168h >/dev/null 2>&1 || true
"${COMPOSE[@]}" pull --quiet sales_lab >/dev/null 2>&1 || true

# El proxy de LLM corre con la configuración de ESTA imagen (la de producción).
config="$(mktemp)"
if ! docker run --rm --entrypoint cat "$IMAGE" "$CONFIG_IN_IMAGE" > "$config" 2>/dev/null || [ ! -s "$config" ]; then
  rm -f "$config"
  echo "no se pudo leer litellm_config.yaml de la imagen $IMAGE" >&2
  exit 3
fi
if ! cmp -s "$config" "$LAB_HOME/litellm_config.yaml"; then
  install -m 0644 "$config" "$LAB_HOME/litellm_config.yaml"
  # El bind-mount sigue viendo el archivo reemplazado: hay que recrear el contenedor.
  "${COMPOSE[@]}" rm -sf litellm >/dev/null 2>&1 || true
fi
rm -f "$config"

"${COMPOSE[@]}" up -d --wait --wait-timeout 180 temporal litellm
"${COMPOSE[@]}" run -d --name "$NAME" -e "LAB_RUN_ID=$RUN_ID" sales_lab

date -u +%FT%TZ > "$RUN_DIR/dispatched"
echo "dispatched"
