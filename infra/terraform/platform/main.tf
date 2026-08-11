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
