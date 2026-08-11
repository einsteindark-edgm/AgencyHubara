# Auth de un tenant: Cognito user pool + app client PUBLIC (PKCE, sin secret).
# Usuarios internos (operadores) → admin-create-only, no self-signup.
# Coincide con INFRASTRUCTURE.md §3.2.

variable "tenant" { type = string }
variable "callback_urls" { type = list(string) }
variable "logout_urls" { type = list(string) }

data "aws_region" "current" {}

resource "aws_cognito_user_pool" "this" {
  name = "agencyhubara-${var.tenant}"

  # Operadores los crea el admin; no hay registro abierto.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "dashboard" {
  name         = "dashboard-${var.tenant}"
  user_pool_id = aws_cognito_user_pool.this.id

  # Public client (SPA): SIN secret → flujo Authorization Code + PKCE.
  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    # Login nativo de la app móvil (email+contraseña, sin navegador): el WebView
    # Android no puede hacer el redirect OIDC. Cliente público (sin secret) →
    # no requiere SECRET_HASH. Ver frontend_dashboard/MOBILE_ANDROID_RUNBOOK.md.
    "ALLOW_USER_PASSWORD_AUTH",
  ]

  access_token_validity  = 1  # horas
  id_token_validity      = 1  # horas
  refresh_token_validity = 30 # días
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
}

# SEC-11: grupos de operadores. Cognito agrega el claim `cognito:groups` a los
# tokens de los usuarios asignados. Por AHORA ambos grupos tienen los MISMOS
# permisos — la API todavía no diferencia por grupo. El split (ej. solo `admin`
# confirma pagos) queda para más adelante; tener los grupos ya deja asignar
# usuarios y prepara el terreno sin cambiar comportamiento.
resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Administradores (permisos = operarios por ahora)"
  precedence   = 1
}

resource "aws_cognito_user_group" "operarios" {
  name         = "operarios"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Operarios: atención de conversaciones y pedidos"
  precedence   = 10
}

# Dominio hosted-UI de Cognito (sin cert custom): <tenant>-hubara.auth.<region>.amazoncognito.com
resource "aws_cognito_user_pool_domain" "this" {
  domain       = "agencyhubara-${var.tenant}"
  user_pool_id = aws_cognito_user_pool.this.id
}

locals {
  issuer = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "app_client_id" { value = aws_cognito_user_pool_client.dashboard.id }
output "user_pool_domain" { value = aws_cognito_user_pool_domain.this.domain }
output "issuer" { value = local.issuer }
output "jwks_uri" { value = "${local.issuer}/.well-known/jwks.json" }
