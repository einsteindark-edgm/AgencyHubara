# Tenants del doc (§4): Hubara + Vincenzo. Single-tenant = borrá el bloque vincenzo.
#
# `domain_aliases` / `acm_certificate_arn` vacíos = CloudFront sirve por su dominio
# *.cloudfront.net con el cert default (funciona YA, real y local). Cuando tengas
# dominio propio: validá un cert ACM en us-east-1, poné su ARN y el alias acá.
#
# `callback_urls` / `logout_urls` = a dónde redirige Cognito tras login/logout.
# Ajustá al dominio real de cada dashboard.

tenants = {
  hubara = {
    # URLs REALES del deploy actual (interim por sslip.io / CloudFront). build_config
    # las expone a frontend-deploy.yml: api_url → VITE_API_URL, y la callback de
    # CloudFront va al app client de Cognito. Cuando haya dominio propio, reemplazá.
    api_url         = "https://98-88-237-207.sslip.io"
    callback_urls   = ["https://d1hvhzkh01tri0.cloudfront.net/callback", "http://localhost:5174/callback"]
    logout_urls     = ["https://d1hvhzkh01tri0.cloudfront.net/", "http://localhost:5174/"]
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"

    # Meta Business Agent (F0): SOLO teléfonos nuestros. Cambiar esta lista es un
    # PR + `terraform apply` de platform + redeploy (render del .env desde SSM).
    # standby_enabled = true (2026-09-11, D3.2): enciende el plugin en Hubara —
    # el sync puede escribir la config en Meta y el webhook guarda los ecos
    # `standby` SOLO de la lista cerrada. NO enciende el agente en Meta: eso es
    # `rollout.enabled`, que se decide desde la tab (dos pasos) en D4.5.
    mba = {
      standby_enabled    = true
      customer_allowlist = ["+573125671604"]
    }
  }

  vincenzo = {
    api_url         = "https://api.vincenzo.example"
    callback_urls   = ["https://dashboard.vincenzo.example/callback", "http://localhost:5174/callback"]
    logout_urls     = ["https://dashboard.vincenzo.example/", "http://localhost:5174/"]
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
  }
}
