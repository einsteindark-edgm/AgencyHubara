#!/usr/bin/env bash
# Crea (idempotente) el estilo de managed login con los valores por defecto de Cognito
# para cada app client con login web de un tenant. Correr JUSTO DESPUÉS del
# `terraform apply` que pasa el dominio a managed_login_version = 2 (el provider
# fijado no tiene `aws_cognito_managed_login_branding`). ADR 2026-10-14-agent-gateway-auth.
#
#   infra/scripts/cognito-managed-login-branding.sh <user_pool_id> <client_id> [<client_id>...]
#
# Ids: `terraform -chdir=infra/terraform/platform output` (app_client_id del dashboard y
# agent_gateway.<tenant>.dsh_hubara_client_id). No imprime secretos.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "uso: $0 <user_pool_id> <client_id> [<client_id>...]" >&2
  exit 2
fi
POOL="$1"; shift
REGION="${AWS_REGION:-us-east-1}"

for CLIENT in "$@"; do
  if aws cognito-idp describe-managed-login-branding-by-client \
      --region "$REGION" --user-pool-id "$POOL" --client-id "$CLIENT" >/dev/null 2>&1; then
    echo "ok   $CLIENT ya tiene estilo"
    continue
  fi
  aws cognito-idp create-managed-login-branding \
    --region "$REGION" --user-pool-id "$POOL" --client-id "$CLIENT" \
    --use-cognito-provided-values >/dev/null
  echo "new  $CLIENT estilo por defecto creado"
done
