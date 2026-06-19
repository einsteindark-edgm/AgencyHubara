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
    api_url         = "https://api.hubara.example"
    callback_urls   = ["https://dashboard.hubara.example/callback", "http://localhost:5174/callback"]
    logout_urls     = ["https://dashboard.hubara.example/", "http://localhost:5174/"]
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
  }

  vincenzo = {
    api_url         = "https://api.vincenzo.example"
    callback_urls   = ["https://dashboard.vincenzo.example/callback", "http://localhost:5174/callback"]
    logout_urls     = ["https://dashboard.vincenzo.example/", "http://localhost:5174/"]
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
  }
}
