import os
from pathlib import Path
from dotenv import load_dotenv

# Carga variables de entorno, por ejemplo desde un archivo .env si usas local
load_dotenv()

# Temporal Cluster
TEMPORAL_URL = os.getenv("TEMPORAL_URL", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TLS_CERT_PATH = os.getenv("TEMPORAL_TLS_CERT_PATH", "")
TEMPORAL_TLS_KEY_PATH = os.getenv("TEMPORAL_TLS_KEY_PATH", "")

# Modelos y Proveedores Base
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "deepseek/sales-agent")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "mispolainas")
API_BASE_LLMLITE = os.getenv("API_BASE_LLMLITE", "http://localhost:4000")

# Workspace Configurations
WORKSPACE_VAULT_DIR = Path(os.getenv("WORKSPACE_VAULT_DIR", "./hubara_vault")).resolve()

# WhatsApp Configs
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my_secret_verify_token")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_API_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"
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
