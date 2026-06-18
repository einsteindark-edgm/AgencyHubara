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

# ── CI/CD: OIDC provider de GitHub + roles de deploy (compartido) ───────────
# Se crea también en local: IAM lo emula bien robotocore, así el test valida las
# trust policies del OIDC. GOTCHA real: el OIDC provider de GitHub es global por
# cuenta — si ya lo tenés creado a mano, importalo (terraform import) o quitá el
# módulo, sino el apply real falla con EntityAlreadyExists. Ver ../README.md.
module "github_oidc" {
  source = "./modules/github-oidc"

  github_repo     = var.github_repo
  github_branches = var.github_branches
  tenants         = keys(var.tenants)
  region          = var.region
}
