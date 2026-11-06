#!/usr/bin/env bash
# Renderiza /opt/hubara/.env para la prod compose. Corre EN la caja EC2 (tiene
# instance profile con ssm:GetParametersByPath sobre /hubara/<tenant>/*).
#
# Lo invoca backend-deploy.yml por SSH, pasando por entorno:
#   HUBARA_IMAGE       — tag GHCR a correr (ej. ghcr.io/.../agencyhubara:<sha>)
#   OTEL_ENDPOINT      — http://<ip-privada-signoz>:4317  (output de ../terraform/compute)
#
# Combina: (a) secretos de SSM  +  (b) config operacional no-secreta (este script).
# Escribe además (c) `litellm.env`, el entorno SOLO del proxy LiteLLM.
#
# HUBARA_DIR (default /opt/hubara) solo lo cambian los tests
# (hubara_agency/tests/infra/test_prod_litellm_env.py).
set -euo pipefail

HUBARA_DIR="${HUBARA_DIR:-/opt/hubara}"
BOX_ENV="$HUBARA_DIR/box.env"
OUT="$HUBARA_DIR/.env"
LITELLM_CONFIG="$HUBARA_DIR/litellm_config.yaml"
LITELLM_ENV="$HUBARA_DIR/litellm.env"
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

# (a.1) PREFLIGHT fail-closed (SECURITY_AUDIT_fable SEC-01/SEC-02). El bloque (b)
#   arma HUBARA_ENV=production → faltar los params de auth tumba la API (503) y la
#   recepción de WhatsApp (403). En vez de rendir un .env que causa ese outage en
#   SILENCIO, ABORTAMOS acá: `docker compose up` no corre, el contenedor viejo
#   sigue vivo, y el operador ve qué provisionar. Decoplar del `terraform apply`
#   (que crea COGNITO_* y es manual) es lo que evita el outage-en-el-merge.
_ssm_val() { grep -m1 "^$1=" "$tmp" | cut -d= -f2-; }
_require_real_auth_param() {
  local key="$1" val; val="$(_ssm_val "$key")"
  if [ -z "$val" ] || [ "$val" = "PLACEHOLDER_set_out_of_band" ]; then
    {
      echo "ABORT deploy: /hubara/${TENANT}/${key} falta o es placeholder en SSM."
      echo "  HUBARA_ENV=production exige los 4 params de auth provisionados:"
      echo "  COGNITO_* (via 'terraform apply' de platform) + WHATSAPP_APP_SECRET"
      echo "  y HUBARA_SERVICE_TOKEN (via 'aws ssm put-parameter --overwrite')."
      echo "  Detalle y orden: infra/DEPLOY_RUNBOOK.md FASE 2.1."
    } >&2
    exit 1
  fi
}
for _k in COGNITO_USER_POOL_ID COGNITO_APP_CLIENT_ID WHATSAPP_APP_SECRET HUBARA_SERVICE_TOKEN; do
  _require_real_auth_param "$_k"
done

# (b) Config operacional NO-secreta y ESTÁTICA (paths, endpoints internos). Los
#     knobs de scheduler (ORDER_RECONCILE_INTERVAL_MINUTES, SALES_EVAL_*, GOLDEN_EVAL_*,
#     WATCHDOG_*) NO van acá a propósito: vienen de SSM /scheduler/ (a) para poder
#     cambiarlos sin redeploy. Hardcodearlos acá los pisaría (último gana en env_file).
cat >> "$tmp" <<EOF
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

# (c) El entorno de LiteLLM (memoria de la caja de app, 2026-11-05): solo las
#     llaves que nombra su config (`os.environ/X`) y la huella del config.
#     Con el `.env` entero el proxy veía todas las llaves de producción y
#     `HUBARA_IMAGE`, que cambia en cada deploy: compose lo recreaba siempre, y
#     LiteLLM arranca en ~990 MB (con los días baja a ~200; lo demás va a swap).
#     Así se recrea solo si cambia su config, una de sus llaves o su imagen. La
#     huella hace falta porque el config es un bind-mount: sin ella, cambiar
#     solo el contenido del archivo no recrea el contenedor y seguiría con el
#     config viejo en memoria (caso: alias gemini-multimodal).
_sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
ltmp="$(mktemp)"
if [ -f "$LITELLM_CONFIG" ]; then
  for _k in $(grep -o 'os\.environ/[A-Za-z0-9_]*' "$LITELLM_CONFIG" | cut -d/ -f2 | sort -u); do
    _v="$(_ssm_val "$_k")"
    if [ -n "$_v" ]; then
      echo "$_k=$_v" >> "$ltmp"
    else
      echo "AVISO: $_k lo nombra litellm_config.yaml pero no está en /hubara/${TENANT}/" >&2
    fi
  done
  echo "LITELLM_CONFIG_SHA256=$(_sha256 "$LITELLM_CONFIG")" >> "$ltmp"
else
  echo "AVISO: falta $LITELLM_CONFIG (lo copia backend-deploy.yml)" >&2
fi

mv "$tmp" "$OUT"
mv "$ltmp" "$LITELLM_ENV"
echo "Escrito $OUT ($(grep -c = "$OUT") vars) y $LITELLM_ENV ($(grep -c = "$LITELLM_ENV") vars)"
