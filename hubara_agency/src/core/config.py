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
