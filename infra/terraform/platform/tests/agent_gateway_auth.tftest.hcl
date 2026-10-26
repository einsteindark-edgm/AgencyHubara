# Cognito para el Agent Gateway (ADR docs/adr/2026-10-14-agent-gateway-auth.md),
# con el provider SIMULADO: no toca AWS.
#
#   terraform -chdir=infra/terraform/platform init -backend=false
#   terraform -chdir=infra/terraform/platform test -filter=tests/agent_gateway_auth.tftest.hcl

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { name = "us-east-1" }
  }
}

variables {
  tenant        = "t1"
  callback_urls = ["https://dashboard.example/callback"]
  logout_urls   = ["https://dashboard.example/"]
}

run "el_gateway_tiene_su_resource_server_con_lectura_y_pii" {
  command = plan
  module { source = "./modules/auth" }

  assert {
    condition     = aws_cognito_resource_server.mcp.identifier == "hubara-mcp"
    error_message = "el resource server del gateway debe llamarse hubara-mcp"
  }
  assert {
    condition     = toset([for s in aws_cognito_resource_server.mcp.scope : s.scope_name]) == toset(["ads.read", "ads.pii"])
    error_message = "hubara-mcp expone exactamente ads.read y ads.pii (sin escrituras)"
  }
  assert {
    condition     = aws_cognito_resource_server.api.identifier == "hubara-api"
    error_message = "el backend tiene su propio resource server hubara-api"
  }
  assert {
    condition     = toset([for s in aws_cognito_resource_server.api.scope : s.scope_name]) == toset(["ads.read"])
    error_message = "hubara-api expone solo ads.read"
  }
}

run "el_plugin_es_un_cliente_publico_pkce_con_loopback_fijo" {
  command = plan
  module { source = "./modules/auth" }

  assert {
    condition     = aws_cognito_user_pool_client.dsh_hubara.generate_secret == false
    error_message = "dsh-hubara corre en la máquina del operador: cliente público, sin secret"
  }
  assert {
    condition     = aws_cognito_user_pool_client.dsh_hubara.allowed_oauth_flows == toset(["code"])
    error_message = "dsh-hubara usa authorization code + PKCE, nada más"
  }
  assert {
    condition     = aws_cognito_user_pool_client.dsh_hubara.callback_urls == toset(["http://localhost:53682/callback"])
    error_message = "callback loopback de puerto fijo (Cognito no admite rangos de puertos)"
  }
  assert {
    condition     = aws_cognito_user_pool_client.dsh_hubara.allowed_oauth_scopes == toset(["openid", "hubara-mcp/ads.read", "hubara-mcp/ads.pii"])
    error_message = "dsh-hubara pide solo los scopes del gateway (nunca hubara-api/*)"
  }
  assert {
    condition     = aws_cognito_user_pool_client.dsh_hubara.explicit_auth_flows == toset(["ALLOW_REFRESH_TOKEN_AUTH"])
    error_message = "dsh-hubara no hace login por contraseña: solo el flujo OAuth del navegador + refresh"
  }
}

run "el_gateway_llama_al_backend_con_su_propio_m2m" {
  command = plan
  module { source = "./modules/auth" }

  assert {
    condition     = aws_cognito_user_pool_client.agent_gateway.generate_secret == true
    error_message = "el M2M del gateway es confidencial (client_credentials exige secret)"
  }
  assert {
    condition     = aws_cognito_user_pool_client.agent_gateway.allowed_oauth_flows == toset(["client_credentials"])
    error_message = "el gateway solo hace client_credentials"
  }
  assert {
    condition     = aws_cognito_user_pool_client.agent_gateway.allowed_oauth_scopes == toset(["hubara-api/ads.read"])
    error_message = "el M2M del gateway solo lee ads (nunca el token dios ni escrituras)"
  }
}

run "el_dominio_pasa_a_managed_login" {
  command = plan
  module { source = "./modules/auth" }

  assert {
    condition     = aws_cognito_user_pool_domain.this.managed_login_version == 2
    error_message = "resource binding (aud = URL del gateway) es exclusivo de managed login (v2)"
  }
}
