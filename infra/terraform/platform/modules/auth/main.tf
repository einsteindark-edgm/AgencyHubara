# Auth de un tenant: Cognito user pool + app client PUBLIC (PKCE, sin secret).
# Usuarios internos (operadores) → admin-create-only, no self-signup.
# Coincide con INFRASTRUCTURE.md §3.2.

variable "tenant" { type = string }
variable "callback_urls" { type = list(string) }
variable "logout_urls" { type = list(string) }
# Loopback del login PKCE del plugin @acktos/dsh-hubara (puerto FIJO: Cognito no admite rangos).
variable "dsh_hubara_callback_urls" {
  type    = list(string)
  default = ["http://localhost:53682/callback"]
}

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
  # Managed login (v2): requisito del resource binding RFC 8707 (`aud` = URL del
  # Agent Gateway). Cambia la pantalla de login del dashboard (decisión 2026-10-14).
  managed_login_version = 2
}

# Con managed login cada app client con login web lleva un estilo. El provider
# fijado (aws ~> 5.40, lock 5.100) no tiene `aws_cognito_managed_login_branding`:
# los estilos (valores por defecto de Cognito) los crea, idempotente, el script
# infra/scripts/cognito-managed-login-branding.sh justo después del apply.

# ── Agent Gateway (ADR docs/adr/2026-10-14-agent-gateway-auth.md) ──────
# Dos resource servers: el gateway MCP (lo que el usuario autoriza al plugin) y la
# API del backend (lo que el gateway M2M puede leer). Sin scopes de escritura.
resource "aws_cognito_resource_server" "mcp" {
  user_pool_id = aws_cognito_user_pool.this.id
  identifier   = "hubara-mcp"
  name         = "Agent Gateway (MCP)"

  scope {
    scope_name        = "ads.read"
    scope_description = "Leer los datos del menú Ads"
  }
  scope {
    scope_name        = "ads.pii"
    scope_description = "Ver teléfono y nombre de clientes sin enmascarar (además exige grupo admin)"
  }
}

resource "aws_cognito_resource_server" "api" {
  user_pool_id = aws_cognito_user_pool.this.id
  identifier   = "hubara-api"
  name         = "API del backend"

  scope {
    scope_name        = "ads.read"
    scope_description = "GET /api/ads/* (solo el M2M del Agent Gateway)"
  }
}

# Plugin @acktos/dsh-hubara: corre en la máquina del operador → cliente PÚBLICO,
# authorization code + PKCE desde el navegador, refresh 30 d. Solo scopes del gateway.
resource "aws_cognito_user_pool_client" "dsh_hubara" {
  name         = "dsh-hubara-${var.tenant}"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes = [
    "openid",
    "${aws_cognito_resource_server.mcp.identifier}/ads.read",
    "${aws_cognito_resource_server.mcp.identifier}/ads.pii",
  ]
  supported_identity_providers = ["COGNITO"]
  callback_urls                = var.dsh_hubara_callback_urls

  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"
}

# El gateway hacia el backend: M2M confidencial PROPIO (sin token passthrough, sin
# HUBARA_SERVICE_TOKEN). El backend lo acepta solo para GET /api/ads/*.
resource "aws_cognito_user_pool_client" "agent_gateway" {
  name         = "agent-gateway-${var.tenant}"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.api.identifier}/ads.read"]
  supported_identity_providers         = ["COGNITO"]

  access_token_validity = 1
  token_validity_units {
    access_token = "hours"
  }
  enable_token_revocation = true
}

locals {
  issuer = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "app_client_id" { value = aws_cognito_user_pool_client.dashboard.id }
output "user_pool_domain" { value = aws_cognito_user_pool_domain.this.domain }
output "issuer" { value = local.issuer }
output "jwks_uri" { value = "${local.issuer}/.well-known/jwks.json" }
output "dsh_hubara_client_id" { value = aws_cognito_user_pool_client.dsh_hubara.id }
output "agent_gateway_client_id" { value = aws_cognito_user_pool_client.agent_gateway.id }
output "agent_gateway_client_secret" {
  value     = aws_cognito_user_pool_client.agent_gateway.client_secret
  sensitive = true
}
