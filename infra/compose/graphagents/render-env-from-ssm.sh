#!/usr/bin/env bash
# Renderiza /opt/graphagents/.env para la prod compose de GraphAgents. Corre EN la
# caja EC2 (instance profile con ssm:GetParametersByPath sobre /graphagents/*).
#
# Lo invoca graphagents-deploy.yml por SSH, pasando GRAPHAGENTS_IMAGE por entorno.
# Para un cambio sin re-deploy de imagen, reusa la imagen del .env actual.
set -euo pipefail

BOX_ENV=/opt/graphagents/box.env
OUT=/opt/graphagents/.env
[ -f "$BOX_ENV" ] || { echo "FALTA $BOX_ENV (lo escribe cloud-init)"; exit 1; }
# shellcheck disable=SC1090
source "$BOX_ENV"   # AWS_REGION, IMAGE_REPO

if [ -f "$OUT" ]; then
  : "${GRAPHAGENTS_IMAGE:=$(grep -m1 '^GRAPHAGENTS_IMAGE=' "$OUT" | cut -d= -f2-)}"
fi
: "${GRAPHAGENTS_IMAGE:?pasá GRAPHAGENTS_IMAGE (o que exista $OUT de un deploy previo)}"

umask 077
tmp="$(mktemp)"

# Secretos desde SSM /graphagents/ (AGENTSPAN_MASTER_KEY, POSTGRES_PASSWORD,
# OPENAI/ANTHROPIC, META_*, GHCR_PULL_TOKEN). Strip a basename → KEY=VALUE.
aws ssm get-parameters-by-path \
  --region "$AWS_REGION" \
  --path "/graphagents" \
  --with-decryption --recursive \
  --query "Parameters[].[Name,Value]" --output text \
  | awk -F'\t' '{ n=$1; sub(/.*\//,"",n); print n "=" $2 }' >> "$tmp"

# La imagen de la app (la interpolación ${GRAPHAGENTS_IMAGE} de la compose la toma de acá).
echo "GRAPHAGENTS_IMAGE=${GRAPHAGENTS_IMAGE}" >> "$tmp"

mv "$tmp" "$OUT"
echo "Escrito $OUT ($(grep -c = "$OUT") vars)"
