variable "region" {
  description = "Región AWS. us-east-1 es obligatoria si se usa cert ACM para CloudFront."
  type        = string
  default     = "us-east-1"
}

variable "aws_endpoint" {
  description = "VACÍO = AWS real. Setear a http://localhost:4566 para apuntar a robotocore (ver providers.tf)."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "owner/repo de GitHub que asume el rol OIDC para deployar (ej. einsteindark-edgm/AgencyHubara)."
  type        = string
  default     = "einsteindark-edgm/AgencyHubara"
}

variable "github_branches" {
  description = "Refs de git autorizados a asumir el rol OIDC (sub claim). Default: solo main."
  type        = list(string)
  default     = ["main"]
}

variable "create_github_oidc_provider" {
  description = "El OIDC provider de GitHub es 1 por cuenta AWS. true (default) = este proyecto lo crea (el repo madre). false = referenciarlo como data source — lo setea forge en project.auto.tfvars para proyectos clonados en la misma cuenta."
  type        = bool
  default     = true
}

# ── Multi-tenant ────────────────────────────────────────────────────────────
# Un módulo reusable instanciado por tenant. Single-tenant = un solo entry.
# El detalle de cada tenant vive en tenants.auto.tfvars.
variable "tenants" {
  description = "Mapa de tenants → su config de frontend (S3+CF), auth (Cognito) y secretos (SSM)."
  type = map(object({
    # Frontend / CloudFront
    domain_aliases      = optional(list(string), []) # dominios custom; [] = usar *.cloudfront.net
    acm_certificate_arn = optional(string, "")       # cert pre-validado en us-east-1; "" = cert default de CloudFront
    price_class         = optional(string, "PriceClass_100")

    # Build del frontend: base URL del FastAPI del tenant (VITE_API_URL). Debe
    # coincidir con el `domain` del mismo tenant en ../compute/tenants.auto.tfvars.
    api_url = string # se materializa en SSM (HUBARA_PUBLIC_API_URL) y entra a agent.yaml de MBA: sin comillas/espacios/#

    # Auth / Cognito
    callback_urls = list(string) # URLs de redirect OAuth (https://dashboard.<tenant>...)
    logout_urls   = list(string)

    # Operación
    enabled_plugins = optional(string, "ads,agents_admin,catalog,chats,eta,orders,system_map")

    # Meta Business Agent (plugin `mba`). Estamos en producción: MBA solo puede
    # responderle a una LISTA CERRADA de clientes, y esa decisión vive ACÁ (git +
    # PR + apply), no en un put-parameter a mano. Defaults = todo apagado y nadie.
    # Se materializa como SSM String en /hubara/<tenant>/<VAR> (modules/mba-config).
    mba = optional(object({
      standby_enabled        = optional(bool, false)      # oído `standby` del webhook (D1.4)
      customer_allowlist     = optional(list(string), []) # E.164 (+573001234567); [] = NADIE
      episode_boundary_event = optional(bool, false)      # nota de frontera episode_closed (D1.10)
      allow_everyone         = optional(bool, false)      # permite ai_audience=EVERYONE desde la tab (D2.3)
      advisor_phone          = optional(string, "")       # E.164 del asesor humano (botón "Escribir al equipo", D3.1); "" = sin resolver
    }), {})
  }))

  validation {
    condition = alltrue([
      for t in values(var.tenants) : t.mba.advisor_phone == "" || can(regex("^\\+[1-9][0-9]{7,14}$", t.mba.advisor_phone))
    ])
    error_message = "tenants.*.mba.advisor_phone: E.164 con '+' (p.ej. +573001234567) o vacío."
  }

  validation {
    condition     = alltrue([for t in values(var.tenants) : can(regex("^https://[^\\s\"'#]+$", t.api_url))])
    error_message = "tenants.*.api_url: https://… sin espacios, comillas ni '#' (entra al agent.yaml de MBA)."
  }

  validation {
    condition = alltrue(flatten([
      for t in values(var.tenants) : [for p in t.mba.customer_allowlist : can(regex("^\\+[1-9][0-9]{7,14}$", p))]
    ]))
    error_message = "tenants.*.mba.customer_allowlist: cada teléfono debe ser E.164 con '+' (p.ej. +573001234567)."
  }
}

# ── Secretos (SSM SecureString) ─────────────────────────────────────────────
# Las CLAVES que se crean por tenant en /hubara/<tenant>/<KEY>. Los VALORES NO
# viven en Terraform: se crean con placeholder y se setean fuera de banda
# (consola / aws ssm put-parameter / CI), así no entran al state ni al git.
variable "secret_keys" {
  description = "Nombres de los parámetros SSM SecureString a crear por tenant (valor = placeholder)."
  type        = list(string)
  default = [
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_VERIFY_TOKEN",
    # App Secret de Meta para verificar el HMAC X-Hub-Signature-256 del webhook.
    # Sin valor real, el webhook RECHAZA en prod (fail-closed) — SEC-02. El
    # operador setea el valor con `aws ssm put-parameter --overwrite`.
    "WHATSAPP_APP_SECRET",
    # Bearer de SERVICIO M2M (workers → API). Necesario cuando la auth de
    # Cognito está enforced, sino los workers (ej. order-sentinel) caen 401.
    # Generar con `openssl rand -hex 32` y setear out-of-band.
    "HUBARA_SERVICE_TOKEN",
    # API key que Meta Business Agent presenta en `X-API-Key` al invocar las
    # connector tools (/api/mba/tools/*). Sin valor real el connector responde
    # 503 (fail-closed) y el resto del sistema no se entera. Generar con
    # `openssl rand -hex 32`, setear out-of-band y usar el MISMO valor al
    # registrar el connector en Meta (auth_config.api_key).
    "HUBARA_MBA_API_KEY",
    # Los interruptores de Meta Business Agent (MBA_STANDBY_ENABLED,
    # MBA_CUSTOMER_ALLOWLIST, MBA_EPISODE_BOUNDARY_EVENT, MBA_ALLOW_EVERYONE) NO
    # son secretos: los gestiona modules/mba-config desde tenants.<t>.mba (git es
    # la fuente de verdad). Acá solo quedan los secretos de MBA.
    # App id de NUESTRA app de Meta suscrita al WABA (el APP_ID del CLI de
    # provisioning de WhatsApp). D1.5: decide si un `messaging_handovers` nos
    # da el hilo a nosotros o a Business Agent. Placeholder/vacío = no se
    # decide nada (WARNING). Distinto de META_APP_ID (OAuth/ads).
    "WHATSAPP_APP_ID",
    # Token de system user para la Meta Business Agent Cloud API
    # (api.facebook.com: thread_control, agent_event, configuración del
    # agente). Permisos whatsapp_business_messaging + management.
    # Placeholder/vacío = el adapter no llama a nadie (not_configured).
    "META_MBA_TOKEN",
    "MEDUSA_BASE_URL",
    "MEDUSA_ADMIN_TOKEN",
    "META_CATALOG_ID",
    # Flow v2 (formulario de envío) publicado en el WABA del tenant: lo resuelve el
    # CLI de provisioning (`flows`) y lo consumen chats + el agent.yaml de MBA
    # (`${META_FLOW_ID_SHIPPING}`). Tenant existente: `terraform import` del param antes del apply.
    "META_FLOW_ID_SHIPPING",
    "META_SYSTEM_USER_TOKEN",
    "TEMPORAL_ADDRESS",
    "TEMPORAL_NAMESPACE",
    "TEMPORAL_API_KEY",
    "GHCR_PULL_TOKEN",
    # OAuth + Marketing API de Meta (plugin `ads`). El backend-deploy los rinde al
    # `.env`; el plugin degrada limpio (/api/ads/meta/login = 503) si están vacíos.
    # El token OAuth NO va acá: lo escribe el backend en runtime en
    # /hubara/<tenant>/meta/oauth (ver el statement WriteMetaToken del app-instance).
    "META_APP_ID",
    "META_APP_SECRET",
    "META_OAUTH_REDIRECT_URI",
    "META_OAUTH_SCOPES",
  ]
}

# ── Config de schedulers (PR #69) ───────────────────────────────────────────
# Knobs de timing NO-secretos en /hubara/<tenant>/scheduler/<VAR> (type String).
# Cambiables sin redeploy con `aws ssm put-parameter --overwrite`. Defaults =
# los del código (ver hubara_agency/docs/scheduler-config-aws.md + el ConfigMap
# de la ruta EKS). Mismos defaults para todos los tenants; divergí por tenant
# con put-parameter (el ignore_changes preserva el override).
variable "scheduler_config" {
  description = "VAR → valor default de cada knob de timing de scheduler."
  type        = map(string)
  default = {
    ORDER_RECONCILE_INTERVAL_MINUTES = "5"
    SALES_EVAL_SCHEDULE_ENABLED      = "true"
    SALES_EVAL_SCHEDULE_CRON         = "0 23 * * *"
    GOLDEN_EVAL_SCHEDULE_ENABLED     = "false"
    GOLDEN_EVAL_SCHEDULE_CRON        = "0 6 * * *"
    WATCHDOG_ENABLED                 = "false"
    WATCHDOG_PRE_EXPIRY_MINUTES      = "30"
    WATCHDOG_QUIET_HOURS_START       = "8"
    WATCHDOG_QUIET_HOURS_END         = "22"
  }
}

# ── GraphAgents (subsistema separado) — secretos en SSM /graphagents/ ────────
variable "graphagents_secret_keys" {
  description = "Claves SecureString a crear en /graphagents/ (valor = placeholder; el real se setea fuera de banda)."
  type        = list(string)
  default = [
    "AGENTSPAN_MASTER_KEY", # base64 32 bytes (openssl rand -base64 32)
    "POSTGRES_PASSWORD",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "META_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
    "GHCR_PULL_TOKEN", # para bajar la imagen de la app desde GHCR
    # Nodo narrativo del reporter CTWA (opción D: DeepSeek DIRECTO, sin proxy LiteLLM). El vendor
    # `LiteLLMProxy` manda Bearer = GRAPHAGENTS_LLM_API_KEY y pega a LITELLM_PROXY_URL (base
    # OpenAI-compatible). GRAPHAGENTS_LLM_MODEL queda en el default `deepseek-flash` (no hace
    # falta declararla). Si la narrativa falla, el reporte degrada visible — no crashea (L-26).
    "GRAPHAGENTS_LLM_API_KEY", # valor real = tu DEEPSEEK_API_KEY (fuera de banda)
    "LITELLM_PROXY_URL",       # valor = https://api.deepseek.com (no es secreto; va acá por uniformidad)
  ]
}
