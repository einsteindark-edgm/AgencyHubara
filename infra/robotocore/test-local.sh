#!/usr/bin/env bash
# Corre el MISMO Terraform que va a AWS real, pero contra robotocore (:4566).
# "Probar para no equivocarnos en la real."
#
#   ./test-local.sh            # asume robotocore ya levantado
#   ./test-local.sh --up       # lo levanta (y lo baja al final)
#
# Qué valida (alto valor, honesto):
#   • validate + plan FULL de ambos roots (platform + compute) → caza errores de
#     wiring/tipos/refs en TODOS los recursos, incluido CloudFront/EC2.
#   • apply best-effort + ASSERTS sobre los servicios de alta fidelidad en Moto:
#     S3, Cognito, SSM, IAM. Esos asserts son el gate (CloudFront/ACM son
#     best-effort en el emulador — el plan ya los validó).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBO="http://localhost:4566"
PLATFORM="$HERE/../terraform/platform"
COMPUTE="$HERE/../terraform/compute"
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1

UP=0; [ "${1:-}" = "--up" ] && UP=1
cleanup() {
  rm -f "$PLATFORM/backend_override.tf" "$COMPUTE/backend_override.tf"
  [ "$UP" = 1 ] && docker compose -f "$HERE/docker-compose.robotocore.yml" down || true
}
trap cleanup EXIT

if [ "$UP" = 1 ]; then
  docker compose -f "$HERE/docker-compose.robotocore.yml" up -d
fi

echo "⏳ esperando robotocore…"
for i in $(seq 1 30); do
  curl -sf "$ROBO/_robotocore/health" >/dev/null 2>&1 && break
  [ "$i" = 30 ] && { echo "❌ robotocore no responde en $ROBO (¿docker compose up?)"; exit 1; }
  sleep 2
done
echo "✅ robotocore arriba"

a() { command aws --endpoint-url "$ROBO" "$@"; }    # awscli contra el emulador
assert() { if eval "$2" >/dev/null 2>&1; then echo "  ✅ $1"; else echo "  ❌ $1"; FAIL=1; fi; }
FAIL=0

run_root() {
  local dir="$1" tfvars="$2"
  echo ""; echo "▶ $(basename "$dir")"
  # Backend local solo para el test (override del backend "s3" real vía *_override.tf).
  printf 'terraform {\n  backend "local" {}\n}\n' > "$dir/backend_override.tf"
  terraform -chdir="$dir" init -reconfigure -input=false -no-color >/dev/null
  terraform -chdir="$dir" validate -no-color
  terraform -chdir="$dir" plan -input=false -no-color -var-file="$tfvars" -out=tfplan.local
  terraform -chdir="$dir" apply -input=false -auto-approve -no-color tfplan.local \
    || echo "  ⚠️  apply parcial (algunos servicios Moto son best-effort; el plan ya validó el grafo)"
}

# ── platform ────────────────────────────────────────────────────────────────
run_root "$PLATFORM" "$HERE/local.platform.tfvars"
echo "  — asserts platform —"
assert "SSM /hubara/hubara/DEEPSEEK_API_KEY existe"  "a ssm get-parameter --name /hubara/hubara/DEEPSEEK_API_KEY"
assert "Cognito pool agencyhubara-hubara existe"     "a cognito-idp list-user-pools --max-results 20 | grep -q agencyhubara-hubara"
assert "Bucket S3 agencyhubara-hubara-frontend existe" "a s3api head-bucket --bucket agencyhubara-hubara-frontend"
assert "IAM rol de deploy OIDC existe"               "a iam get-role --role-name agencyhubara-gha-deploy"
assert "SSM scheduler knob (PR #69) existe"          "a ssm get-parameter --name /hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES"

# ── compute ─────────────────────────────────────────────────────────────────
run_root "$COMPUTE" "$HERE/local.compute.tfvars"
echo "  — asserts compute —"
assert "EC2 instance(s) AgencyHubara creada(s)" "a ec2 describe-instances --filters Name=tag:Project,Values=AgencyHubara --query 'Reservations[].Instances[].InstanceId' --output text | grep -q i-"
assert "IAM instance profile de app existe"     "a iam get-instance-profile --instance-profile-name agencyhubara-hubara-app"

echo ""
if [ "$FAIL" = 0 ]; then echo "🎉 LOCAL OK — el grafo aplica y los servicios core existen en robotocore"; else echo "💥 hubo asserts en rojo (ver arriba)"; exit 1; fi
