import os
from pathlib import Path
from dotenv import load_dotenv

# Carga variables de entorno, por ejemplo desde un archivo .env si usas local
load_dotenv()

# Entorno de ejecución. `production`/`prod` activa el modo FAIL-CLOSED de
# seguridad: faltar la config de auth (Cognito) o del webhook
# (`WHATSAPP_APP_SECRET`) hace que la API REHÚSE servir (503/403) en vez de
# degradar a no-op. Es el candado contra el incidente "API SIN auth"
# (deployment_live_aws / SECURITY_AUDIT_fable SEC-01/SEC-02): un deploy que
# olvide provisionar el secreto falla ruidoso, no abre la puerta en silencio.
# Default `dev`: local y tests siguen siendo no-op sin config. Prod lo setea
# en `infra/compose/render-env-from-ssm.sh` (bloque estático no-secreto).
HUBARA_ENV = os.getenv("HUBARA_ENV", "dev")


def is_production() -> bool:
    """True si corremos en producción → modo fail-closed de seguridad.

    Lee el global del módulo en cada llamada (no lo cachea) para que los tests
    puedan `monkeypatch.setattr(config, "HUBARA_ENV", ...)` y para permitir
    override sin reimportar.
    """
    return HUBARA_ENV.strip().lower() in {"production", "prod"}


# Valor que Terraform escribe en cada SSM SecureString hasta que el operador
# setea el real (`infra/terraform/platform/modules/secrets/main.tf`). Es un
# string CONOCIDO en el repo → NUNCA es una credencial válida. Tratarlo como
# AUSENTE evita dos agujeros: (a) un `HUBARA_SERVICE_TOKEN` placeholder sería un
# bearer adivinable = bypass de Cognito; (b) verificar el HMAC del webhook
# contra un `WHATSAPP_APP_SECRET` placeholder. Ver SECURITY_AUDIT_fable §premortem.
SSM_PLACEHOLDER = "PLACEHOLDER_set_out_of_band"


def is_placeholder(value: str | None) -> bool:
    """True si `value` es el placeholder de SSM (o vacío) → NO es config real."""
    return not value or value.strip() == SSM_PLACEHOLDER


# CORS: orígenes permitidos para la API (CSV). Default "*" (dev). En prod el
# render script lo setea al origen del dashboard (CloudFront) — con auth por
# Bearer no hace falta `allow_credentials`, pero restringir el origen corta el
# drive-by cross-site desde cualquier web (SEC-14).
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*")


def cors_allowed_origins() -> list[str]:
    """Lista de orígenes CORS. "*"/vacío → ["*"]; sino split por coma (trim)."""
    raw = CORS_ALLOWED_ORIGINS.strip()
    if raw == "*" or not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

# Temporal Cluster
TEMPORAL_URL = os.getenv("TEMPORAL_URL", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TLS_CERT_PATH = os.getenv("TEMPORAL_TLS_CERT_PATH", "")
TEMPORAL_TLS_KEY_PATH = os.getenv("TEMPORAL_TLS_KEY_PATH", "")
# Temporal Cloud — auth por API key (preferida sobre mTLS; INFRASTRUCTURE.md §6,
# sin certs que rotar). TEMPORAL_ADDRESS = endpoint regional
# (<region>.<provider>.api.temporal.io:7233); cae a TEMPORAL_URL para dev local.
# Si TEMPORAL_API_KEY está seteada, gana sobre mTLS.
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", TEMPORAL_URL)
TEMPORAL_API_KEY = os.getenv("TEMPORAL_API_KEY", "")

# Modelos y Proveedores Base
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "deepseek/deepseek-v4-flash")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "mispolainas")
API_BASE_LLMLITE = os.getenv("API_BASE_LLMLITE", "http://localhost:4000")

# Modelo para tareas de SÍNTESIS corta de texto (no-agente, no-reasoning):
# el resumen on-demand del cliente en "Historial cliente". El modelo de
# agentes por defecto (DeepSeek V4) está tuneado para conversación con tools,
# no para síntesis batch single-turn. Gemini Flash-Lite (alias `gemini-backup`
# del litellm config) es
# rápido, barato y devuelve `content` estándar. Mismo modelo que usa el
# pipeline de transcripción de audio.
#
# El prefijo `litellm_proxy/` le dice al litellm SDK que rutee al PROXY
# (api_base) y resuelva el alias ahí — la GEMINI_API_KEY vive en el container
# litellm, no en hubara-api. Sin el prefijo, el SDK intenta resolver el
# provider localmente y falla con "LLM Provider NOT provided".
CUSTOMER_SUMMARY_MODEL = os.getenv(
    "CUSTOMER_SUMMARY_MODEL", "litellm_proxy/gemini-backup"
)
# El litellm SDK exige un api_key NO-None para el provider `litellm_proxy`,
# aunque el proxy local no valide auth. En prod, setear LITELLM_API_KEY con
# el master key del proxy. Local: cualquier string sirve.
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-litellm-proxy-local")

# Workspace Configurations
WORKSPACE_VAULT_DIR = Path(os.getenv("WORKSPACE_VAULT_DIR", "./hubara_vault")).resolve()

# WhatsApp Configs
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my_secret_verify_token")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_API_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"
# Endpoint de subida de media (multipart). Devuelve un `media_id` que luego se
# referencia en un mensaje `type=image`. Lo usa el operador humano para mandar
# fotos desde el dashboard/app sin exponer el vault (que está detrás de auth).
WHATSAPP_MEDIA_API_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/media"
# HU-WA24H-001 pre-mortem F9.2: app secret para verificar HMAC del
# X-Hub-Signature-256 header de webhook POST. Sin esto, cualquier
# atacante puede inyectar fake delivery statuses al endpoint y corromper
# cost metrics. Vacío → verification SKIPED (modo dev/local). Pre-launch
# se DEBE setear este env var.
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

# HU-WA24H-001 Sprint CAPI: Meta Conversions API for Business Messaging.
# Sin estas vars la activity `send_capi_event_activity` NOOPea (log warning
# y skip). Pre-launch, después de §13-§14 del runbook
# `meta_template_approval.md`, setear las 4. Las primeras 3 son SECRETS.
#   META_CAPI_DATASET_ID         — del Events Manager (Settings → Dataset ID).
#   META_CAPI_ACCESS_TOKEN       — System User token CAPI (§14.2 runbook).
#   META_CAPI_TEST_EVENT_CODE    — set en staging para usar Test Events panel,
#                                  vacío en prod (los events van a Overview).
#   WHATSAPP_BUSINESS_ACCOUNT_ID — la WABA_ID. Necesaria como tenant scope en
#                                  el user_data del payload (junto con ctwa_clid).
META_CAPI_DATASET_ID = os.getenv("META_CAPI_DATASET_ID", "")
META_CAPI_ACCESS_TOKEN = os.getenv("META_CAPI_ACCESS_TOKEN", "")
META_CAPI_TEST_EVENT_CODE = os.getenv("META_CAPI_TEST_EVENT_CODE", "")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")

# Dashboard auth — Cognito JWT (workstream JWT/Cognito, PENDING_IMPLEMENTATION §2).
# Si AMBOS (pool + app client) están seteados, la API valida el Bearer access-token
# de Cognito en TODAS las rutas del dashboard (los webhooks de Meta quedan fuera —
# tienen su propia auth). Vacíos → auth NO-OP (dev local / tests existentes que
# pegan rutas dashboard sin token siguen andando). En prod vienen de SSM
# /hubara/<tenant>/ (no son secretos: son ids públicos del pool/app client).
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID", "")
# Bearer de SERVICIO para llamadas machine-to-machine a la API (workers → API,
# ej. el executor de order-sentinel — un worker no tiene request entrante del
# cual portar identidad, castkit no aplica). Vacío = deshabilitado (el camino
# Cognito queda idéntico). En prod viene de SSM /hubara/<tenant>/ (ESTE sí es
# secreto: generarlo con `openssl rand -hex 32`).
HUBARA_SERVICE_TOKEN = os.getenv("HUBARA_SERVICE_TOKEN", "")
# Región del pool de Cognito (arma el issuer + el JWKS uri). Cae a la región de deploy.
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Valor del tag `Role` que identifica la caja GraphAgents (EC2 dedicada, pay-per-use con
# autostop + IP dinámica). El `Boto3Launcher` del buzón de análisis (plugin ads) resuelve la instancia por
# ESTE tag (nunca por IP) para despertarla (ec2:StartInstances) y despachar runs (ssm:SendCommand).
GRAPHAGENTS_INSTANCE_TAG = os.getenv("GRAPHAGENTS_INSTANCE_TAG", "graphagents")
