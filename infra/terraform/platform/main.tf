# Wiring de la capa managed. Cada módulo se instancia POR TENANT (for_each);
# github-oidc es compartido (un solo OIDC provider para todo el repo).

# ── Frontend: S3 privado + CloudFront (OAC) por tenant ──────────────────────
module "frontend" {
  source   = "./modules/frontend"
  for_each = var.tenants

  tenant              = each.key
  domain_aliases      = each.value.domain_aliases
  acm_certificate_arn = each.value.acm_certificate_arn
  price_class         = each.value.price_class
  use_local           = local.use_local
}

# ── Auth: Cognito user pool + app client (PKCE) por tenant ──────────────────
module "auth" {
  source   = "./modules/auth"
  for_each = var.tenants

  tenant        = each.key
  callback_urls = each.value.callback_urls
  logout_urls   = each.value.logout_urls
}

# ── Auth ids (NO-secretos) a SSM — el backend los lee para enforcar Cognito ──
# COGNITO_USER_POOL_ID / COGNITO_APP_CLIENT_ID son ids PÚBLICOS del pool/app
# client, con el valor REAL del output del módulo `auth`. Van como SSM `String`
# (no SecureString: no son secretos; el valor lo gobierna Terraform, sin
# ignore_changes). El `render-env-from-ssm.sh` los barre (get-parameters-by-path
# recursivo) a /opt/hubara/.env → `require_auth` ve ambos → la API enforca el
# JWT de Cognito. Sin esto la auth degradaba a NO-OP en prod (la API servía PII
# sin token) — SECURITY_AUDIT_fable SEC-01.
resource "aws_ssm_parameter" "cognito_user_pool_id" {
  for_each = var.tenants

  name  = "/hubara/${each.key}/COGNITO_USER_POOL_ID"
  type  = "String"
  value = module.auth[each.key].user_pool_id
}

resource "aws_ssm_parameter" "cognito_app_client_id" {
  for_each = var.tenants

  name  = "/hubara/${each.key}/COGNITO_APP_CLIENT_ID"
  type  = "String"
  value = module.auth[each.key].app_client_id
}

# ── Agent Gateway (ADR docs/adr/2026-10-14-agent-gateway-auth.md) ─────
# El backend acepta el M2M del gateway SOLO para GET /api/ads/* (`require_auth` §4).
# Id público → String bajo /hubara/<tenant>/ (render-env-from-ssm.sh lo barre al .env).
resource "aws_ssm_parameter" "cognito_m2m_client_ids" {
  for_each = var.tenants

  name  = "/hubara/${each.key}/COGNITO_M2M_CLIENT_IDS"
  type  = "String"
  value = module.auth[each.key].agent_gateway_client_id
}

# El secreto del M2M va FUERA de /hubara/<tenant>/ a propósito (render-env-from-ssm.sh
# vuelca ese path entero al .env de TODOS los servicios); este secreto es solo del gateway.
resource "aws_ssm_parameter" "agent_gateway_client_secret" {
  for_each = var.tenants

  name  = "/hubara/agent-gateway/${each.key}/BACKEND_M2M_CLIENT_SECRET"
  type  = "SecureString"
  value = module.auth[each.key].agent_gateway_client_secret
}

# ── Secretos: parámetros SSM SecureString por tenant ────────────────────────
module "secrets" {
  source   = "./modules/secrets"
  for_each = var.tenants

  tenant      = each.key
  secret_keys = var.secret_keys
}

# ── Config de schedulers (PR #69): SSM String por tenant, cambiable sin redeploy ─
module "scheduler_config" {
  source   = "./modules/scheduler-config"
  for_each = var.tenants

  tenant = each.key
  config = var.scheduler_config
}

# ── Meta Business Agent: interruptores + lista cerrada por tenant (git = verdad) ─
module "mba_config" {
  source   = "./modules/mba-config"
  for_each = var.tenants

  tenant  = each.key
  api_url = each.value.api_url
  config  = each.value.mba
}

# ── Laboratorio de conversaciones: techo del modo, perfil y topes (git = verdad) ─
module "lab_config" {
  source   = "./modules/lab-config"
  for_each = var.tenants

  tenant = each.key
  config = each.value.lab
}

# ── Reseñas tras la entrega: modo, tiempos, cupones y enlace (git = verdad) ──
module "review_config" {
  source   = "./modules/review-config"
  for_each = var.tenants

  tenant = each.key
  config = each.value.reviews
}

# ── GraphAgents (subsistema separado): secretos SSM en /graphagents/ ─────────
module "graphagents_secrets" {
  source      = "./modules/graphagents-secrets"
  secret_keys = var.graphagents_secret_keys
}

# ── CI/CD: OIDC provider de GitHub + roles de deploy (compartido) ───────────
# Se crea también en local: IAM lo emula bien robotocore, así el test valida las
# trust policies del OIDC. GOTCHA real: el OIDC provider de GitHub es global por
# cuenta — si ya existe (creado a mano, o por OTRO proyecto forjado en la misma
# cuenta), seteá create_github_oidc_provider = false para referenciarlo como
# data source, sino el apply real falla con EntityAlreadyExists. Ver ../README.md.
module "github_oidc" {
  source = "./modules/github-oidc"

  github_repo     = var.github_repo
  github_branches = var.github_branches
  tenants         = keys(var.tenants)
  region          = var.region
  create_provider = var.create_github_oidc_provider
}
