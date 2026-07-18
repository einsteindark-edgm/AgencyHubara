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
    api_url = string

    # Auth / Cognito
    callback_urls = list(string) # URLs de redirect OAuth (https://dashboard.<tenant>...)
    logout_urls   = list(string)

    # Operación
    enabled_plugins = optional(string, "ads,agents_admin,catalog,chats,eta,orders,system_map")
  }))
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
    "MEDUSA_BASE_URL",
    "MEDUSA_ADMIN_TOKEN",
    "META_CATALOG_ID",
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
    # OpenAI-compatible). GRAPHAGENTS_LLM_MODEL queda en el default `deepseek-v4-flash` (no hace
    # falta declararla). Si la narrativa falla, el reporte degrada visible — no crashea (L-26).
    "GRAPHAGENTS_LLM_API_KEY", # valor real = tu DEEPSEEK_API_KEY (fuera de banda)
    "LITELLM_PROXY_URL",       # valor = https://api.deepseek.com (no es secreto; va acá por uniformidad)
  ]
}
