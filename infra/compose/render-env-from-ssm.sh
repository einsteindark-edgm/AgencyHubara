#!/usr/bin/env bash
# Renderiza /opt/hubara/.env para la prod compose. Corre EN la caja EC2 (tiene
# instance profile con ssm:GetParametersByPath sobre /hubara/<tenant>/*).
#
# Lo invoca backend-deploy.yml por SSH, pasando por entorno:
#   HUBARA_IMAGE       — tag GHCR a correr (ej. ghcr.io/.../agencyhubara:<sha>)
#   OTEL_ENDPOINT      — http://<ip-privada-signoz>:4317  (output de ../terraform/compute)
#
# Combina: (a) secretos de SSM  +  (b) config operacional no-secreta (este script).
set -euo pipefail

BOX_ENV=/opt/hubara/box.env
OUT=/opt/hubara/.env
[ -f "$BOX_ENV" ] || { echo "FALTA $BOX_ENV (lo escribe cloud-init)"; exit 1; }
# shellcheck disable=SC1090
source "$BOX_ENV"   # AWS_REGION, TENANT, CADDY_DOMAIN, ENABLED_PLUGINS, IMAGE_REPO

# Cambio de config SIN redeploy (PR #69): si no se pasan, reusar los del .env
# actual (re-render solo desde SSM, misma imagen/endpoint). Así el flujo
# `put-parameter --overwrite → render → restart del worker` no exige re-pasar la
# imagen — solo barre los nuevos valores de SSM.
if [ -f "$OUT" ]; then
  : "${HUBARA_IMAGE:=$(grep -m1 '^HUBARA_IMAGE=' "$OUT" | cut -d= -f2-)}"
  : "${OTEL_ENDPOINT:=$(grep -m1 '^OTEL_EXPORTER_OTLP_ENDPOINT=' "$OUT" | cut -d= -f2-)}"
fi
: "${HUBARA_IMAGE:?pasá HUBARA_IMAGE (o que exista $OUT de un deploy previo)}"
: "${OTEL_ENDPOINT:=}"   # vacío → ConsoleSpanExporter (degrada solo)

umask 077
tmp="$(mktemp)"

# (a) Secretos + config de scheduler desde SSM, recursivo bajo /hubara/<tenant>/.
#     Incluye los knobs de /hubara/<tenant>/scheduler/ (PR #69) sin tratamiento
#     especial: el strip a basename los deja como KEY=VALUE igual que los secretos.
#     (Valores sin tabs/newlines — ok para crons "0 23 * * *", el separador es \t.)
aws ssm get-parameters-by-path \
  --region "$AWS_REGION" \
  --path "/hubara/${TENANT}" \
  --with-decryption --recursive \
  --query "Parameters[].[Name,Value]" --output text \
  | awk -F'\t' '{ n=$1; sub(/.*\//,"",n); print n "=" $2 }' >> "$tmp"

# (b) Config operacional NO-secreta y ESTÁTICA (paths, endpoints internos). Los
#     knobs de scheduler (ORDER_RECONCILE_INTERVAL_MINUTES, SALES_EVAL_*, GOLDEN_EVAL_*,
#     WATCHDOG_*) NO van acá a propósito: vienen de SSM /scheduler/ (a) para poder
#     cambiarlos sin redeploy. Hardcodearlos acá los pisaría (último gana en env_file).
cat >> "$tmp" <<EOF
HUBARA_IMAGE=${HUBARA_IMAGE}
CADDY_DOMAIN=${CADDY_DOMAIN}
ENABLED_PLUGINS=${ENABLED_PLUGINS}
API_BASE_LLMLITE=http://litellm:4000
WORKSPACE_VAULT_DIR=/app/hubara_vault
CATALOG_SNAPSHOT_DIR=/app/hubara_vault/catalog
CATALOG_MAX_AGE_MINUTES=30
EVAL_CANDIDATES_DIR=/app/hubara_vault/_evals/candidates
OPENLIT_PRICING_JSON=/app/hubara_agency/deploy/openlit/pricing.json
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}
EOF

mv "$tmp" "$OUT"
echo "Escrito $OUT ($(grep -c = "$OUT") vars)"
