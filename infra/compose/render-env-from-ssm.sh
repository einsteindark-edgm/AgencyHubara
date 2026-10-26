#!/usr/bin/env bash
# Renderiza el .env de la prod compose (repartido por servicio). Corre EN la caja EC2 (tiene
# instance profile con ssm:GetParametersByPath sobre /hubara/<tenant>/*).
#
# Lo invoca backend-deploy.yml por SSH, pasando por entorno:
#   HUBARA_IMAGE       — tag GHCR a correr (ej. ghcr.io/.../agencyhubara:<sha>)
#   OTEL_ENDPOINT      — http://<ip-privada-signoz>:4317  (output de ../terraform/compute)
#
# Combina: (a) SSM  +  (b) config operacional no-secreta (este script), y (c) los
# reparte: .env compartido + secrets/<servicio>.env + .env.host.
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
HERE="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# (a) Secretos + config de scheduler desde SSM, recursivo bajo /hubara/<tenant>/.
#     Incluye los knobs de /hubara/<tenant>/scheduler/ (PR #69) sin tratamiento
#     especial: el basename los deja como KEY=VALUE igual que los secretos. Va en
#     JSON con el tipo de cada parámetro: el reparto separa String de SecureString.
aws ssm get-parameters-by-path \
  --region "$AWS_REGION" \
  --path "/hubara/${TENANT}" \
  --with-decryption --recursive \
  --query "{Parameters: Parameters[].{Name: Name, Type: Type, Value: Value}}" --output json \
  > "$work/params.json"

# (b) Config operacional NO-secreta y ESTÁTICA (paths, endpoints internos). Los
#     knobs de scheduler (ORDER_RECONCILE_INTERVAL_MINUTES, SALES_EVAL_*, GOLDEN_EVAL_*,
#     WATCHDOG_*) NO van acá a propósito: vienen de SSM /scheduler/ (a) para poder
#     cambiarlos sin redeploy. Hardcodearlos acá los pisaría (último gana en env_file).
cat > "$work/static.env" <<EOF
HUBARA_IMAGE=${HUBARA_IMAGE}
CADDY_DOMAIN=${CADDY_DOMAIN}
ENABLED_PLUGINS=${ENABLED_PLUGINS}
# FAIL-CLOSED de seguridad (SECURITY_AUDIT_fable SEC-01/SEC-02): en prod, faltar
# la config de auth (COGNITO_*) o del webhook (WHATSAPP_APP_SECRET) hace que la
# API rehúse servir (503/403) en vez de degradar a no-op. Provisioná esos
# parámetros en SSM ANTES de deployar con esto armado (ver DEPLOY_RUNBOOK.md).
HUBARA_ENV=production
API_BASE_LLMLITE=http://litellm:4000
WORKSPACE_VAULT_DIR=/app/hubara_vault
# Historial LLM en el volumen persistente (incidente 2026-07-17 run 019f6db3:
# el default <workspace>/sessions vive en la imagen → cada deploy borraba la
# memoria conversacional de todos los clientes activos).
EXOCLAW_STATE_DIR=/app/hubara_vault/agent_state
CATALOG_SNAPSHOT_DIR=/app/hubara_vault/catalog
CATALOG_MAX_AGE_MINUTES=30
EVAL_CANDIDATES_DIR=/app/hubara_vault/_evals/candidates
OPENLIT_PRICING_JSON=/app/hubara_agency/deploy/openlit/pricing.json
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}
EOF

# (c) Reparto (revisión de seguridad 2026-10-26): `.env` con la config no secreta,
#     `secrets/<servicio>.env` con los SecureString de cada uno según
#     service-secrets.json, y `.env.host` con lo que solo usa la caja (GHCR).
#     PREFLIGHT fail-closed (SEC-01/SEC-02), ahora dentro del reparto: sin los params
#     de auth (o con el relleno de Terraform) no escribe nada y sale con error →
#     `docker compose up` no corre y el contenedor viejo sigue vivo.
python3 "$HERE/split-env-by-service.py" \
  --params "$work/params.json" \
  --map "$HERE/service-secrets.json" \
  --static "$work/static.env" \
  --out-dir "$(dirname "$OUT")"
