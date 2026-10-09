# Tenants de {{repo_name}} — generado por forge. UN solo tenant: {{slug}}.
# api_url/callbacks: completar tras el primer apply de compute (EIP → sslip.io)
# o con el dominio propio. Ver NEXT_STEPS.md F7.
tenants = {
  {{slug}} = {
    api_url = "{{api_url}}"
    # el dashboard vuelve de Cognito a <origen>/callback (frontend env.ts);
    # :5174 es el Vite local del stack docker (nunca :5173)
    callback_urls = [
      "http://localhost:5174/callback",
      "https://TODO-CLOUDFRONT.cloudfront.net/callback",
    ]
    logout_urls = [
      "http://localhost:5174/",
      "https://TODO-CLOUDFRONT.cloudfront.net/",
    ]
    # explícito: el default de la variable puede quedar detrás del compose real
    enabled_plugins = "{{enabled_plugins}}"

    # Meta Business Agent: SOLO teléfonos de {{company}} (E.164, +57…). [] = nadie.
    mba = {
      standby_enabled    = false
      customer_allowlist = []
    }

    # Política comercial de {{company}} (desde client.yaml → commerce): tarifas
    # de envío, contra entrega, pagos y códigos del catálogo → SSM → .env
    # (modules/store-config). Sin payment_nequi_number el bot NO ofrece pago
    # anticipado (el clon nace fail-closed, nunca con la llave de otro).
{{store_block}}

    # App Operador (Android): política de privacidad pública (Google Play la exige).
    mobile = {
      privacy_url = ""
    }

    # Motor de decisiones (PAQUETES_DE_DECISION.md). El clon corre la misma
    # inteligencia que la tienda madre: `{{store_bundle}}`, con el dominio de
    # {{company}} (bundles/<id>/domain.yaml). Los techos en canary NO encienden
    # nada: el modo del bot nuevo lo pone `infra/scripts/bot_control.sh`
    # (nace en off). internal_numbers: teléfonos del equipo de {{company}}
    # (E.164) — sus conversaciones no entran al banco del laboratorio.
    lab = {
      decisions_bundle        = "{{store_bundle}}"
      internal_numbers        = []
      perception_mode_ceiling = "canary"
      capabilities_ceiling    = "canary"
      workflow_v2_ceiling     = "canary"
      signal_inbound_meta     = true
    }
  }
}
