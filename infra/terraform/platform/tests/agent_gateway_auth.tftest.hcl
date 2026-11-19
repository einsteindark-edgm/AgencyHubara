# Cognito para el Agent Gateway (ADR docs/adr/2026-10-14-agent-gateway-auth.md),
# con el provider SIMULADO: no toca AWS.
#
#   terraform -chdir=infra/terraform/platform init -backend=false
#   terraform -chdir=infra/terraform/platform test -filter=tests/agent_gateway_auth.tftest.hcl

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { name = "us-east-1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
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

# Mergear este cambio NO migra el login: el root `platform` se aplica a menudo por
# otros motivos (config del bot) y la pantalla nueva necesita sus estilos al momento.
run "sin_pedirlo_el_login_sigue_en_la_version_clasica" {
  command = plan
  module { source = "./modules/auth" }

  assert {
    condition     = aws_cognito_user_pool_domain.this.managed_login_version == 1
    error_message = "por defecto el dominio queda en Hosted UI clásica: un apply de rutina no cambia el login"
  }
}

run "managed_login_se_activa_por_tienda_y_a_proposito" {
  command = plan
  module { source = "./modules/auth" }
  variables {
    managed_login = true
  }

  assert {
    condition     = aws_cognito_user_pool_domain.this.managed_login_version == 2
    error_message = "resource binding (aud = URL del gateway) es exclusivo de managed login (v2)"
  }
}

run "la_tienda_decide_managed_login_en_su_entrada_de_tenants" {
  command = plan
  variables {
    tenants = {
      t1 = {
        api_url       = "https://api.example.test"
        callback_urls = ["https://dashboard.example/callback"]
        logout_urls   = ["https://dashboard.example/"]
      }
      t2 = {
        api_url       = "https://api2.example.test"
        callback_urls = ["https://dashboard2.example/callback"]
        logout_urls   = ["https://dashboard2.example/"]
        managed_login = true
      }
    }
  }

  assert {
    condition     = module.auth["t1"].managed_login_version == 1 && module.auth["t2"].managed_login_version == 2
    error_message = "managed_login es por tienda y nace apagado"
  }
}
