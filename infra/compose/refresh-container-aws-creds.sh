#!/usr/bin/env bash
# Credenciales TEMPORALES de AWS para los contenedores que las usan (api,
# worker-order-sentinel-cycle, worker-reengagement-cycle). Corre EN la caja,
# como root, por el timer `hubara-aws-creds` (cada 15 min; duran 1 h).
#
# Con hop limit 1 los contenedores no llegan al rol de la caja. El host (que sí
# llega) asume `agencyhubara-<tenant>-app-containers`, que solo lee/escribe el
# token de Meta y despierta a GraphAgents, y deja el resultado en el formato de
# `credential_process` de botocore. Los contenedores montan el directorio de solo
# lectura (`/opt/hubara/aws:/run/hubara-aws:ro`): se monta el DIRECTORIO, no el
# archivo, porque el `mv` atómico cambia el inode y un archivo montado quedaría
# viejo. Revisión de seguridad 2026-10-26.
#
# Guarda: hubara_agency/tests/infra/test_container_aws_credentials.py.
set -euo pipefail

BOX_ENV="${BOX_ENV:-/opt/hubara/box.env}"
OUT_DIR="${OUT_DIR:-/opt/hubara/aws}"
# shellcheck disable=SC1090
source "$BOX_ENV"   # AWS_REGION, TENANT
: "${TENANT:?falta TENANT en $BOX_ENV}"
: "${AWS_REGION:?falta AWS_REGION en $BOX_ENV}"

ACCOUNT="$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)"
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/agencyhubara-${TENANT}-app-containers"
CREDS="$(aws sts assume-role --region "$AWS_REGION" \
  --role-arn "$ROLE_ARN" \
  --role-session-name "hubara-${TENANT}-containers" \
  --duration-seconds 3600 \
  --query Credentials --output json)"

umask 077
mkdir -p "$OUT_DIR"
tmp="$(mktemp "$OUT_DIR/.creds.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf '%s' "$CREDS" | python3 -c '
import json, sys
c = json.load(sys.stdin)
out = {"Version": 1, "AccessKeyId": c["AccessKeyId"], "SecretAccessKey": c["SecretAccessKey"],
       "SessionToken": c["SessionToken"], "Expiration": c["Expiration"]}
with open(sys.argv[1], "w") as fh:
    json.dump(out, fh)
' "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$OUT_DIR/creds.json"
trap - EXIT
echo "credenciales de los contenedores renovadas (rol ${ROLE_ARN##*/})" >&2
