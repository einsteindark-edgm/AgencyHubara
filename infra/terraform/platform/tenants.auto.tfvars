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

    # Paquete de decisión de la tienda (PAQUETES_DE_DECISION.md §9): promover
    # una versión (ya desplegada) = nombrarla aquí + `terraform apply` de
    # platform + dispatch de Backend deploy, que no se dispara con Terraform
    # (render del .env desde SSM → SALES_DECISIONS_BUNDLE; certifica el paquete
    # en la imagen antes de cambiar containers). `ventas-2` (2026-10-02) es
    # `ventas` con la regla ② de `promocion`. `ventas-3` (2026-10-07, #390) es
    # `ventas-2` con los arreglos de la conversación de prueba del 2026-10-06
    # (cortesía, compra, afirmación, cantidad, asunto `gusto`): solo cambia lo
    # que decide Jev, es decir, los números de prueba con `prueba-jev si`. El
    # resto de `lab` queda en sus defaults (todo apagado). Volver atrás =
    # "ventas-2".
    #
    # `internal_numbers`: teléfonos del equipo; sus conversaciones no entran al
    # banco del laboratorio. El del operador (2026-10-05) es el número con el
    # que se prueba el bot nuevo: sus pruebas no deben volverse casos del banco.
    #
    # Techos en `canary` (decisión del operador, 2026-10-05) para probar el bot
    # nuevo con ese número. Un techo NO enciende nada: el modo lo pone el
    # control del dashboard (vault `_rollout/`), que nace en `off`, y en canary
    # solo actúa sobre los números de prueba y el porcentaje que fije el
    # control. Subir percepción o capacidades a canary exige además la vara
    # (7 días en sombra, etc.). `signal_inbound_meta`: el 4.º argumento de la
    # señal; se aplica DESPUÉS del primer deploy de #372 (worker antes que la
    # API). Volver atrás = "off" + apply + Backend deploy.
    lab = {
      decisions_bundle        = "ventas-3"
      internal_numbers        = ["+573125671604"]
      perception_mode_ceiling = "canary"
      capabilities_ceiling    = "canary"
      workflow_v2_ceiling     = "canary"
      signal_inbound_meta     = true
    }
  }

  vincenzo = {
    api_url         = "https://api.vincenzo.example"
    callback_urls   = ["https://dashboard.vincenzo.example/callback", "http://localhost:5174/callback"]
    logout_urls     = ["https://dashboard.vincenzo.example/", "http://localhost:5174/"]
    enabled_plugins = "ads,agents_admin,catalog,chats,eta,orders,system_map"
  }
}
