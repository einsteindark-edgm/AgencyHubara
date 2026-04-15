import os
from temporalio.client import Client, TLSConfig
from src.core.config import (
    TEMPORAL_URL, 
    TEMPORAL_NAMESPACE,
    TEMPORAL_TLS_CERT_PATH,
    TEMPORAL_TLS_KEY_PATH
)

async def get_temporal_client() -> Client:
    """
    Construye y retorna el conector de Temporal Client.
    Soporta conexiones limpias (HTTP local) y seguras mTLS (Temporal Cloud).
    """
    tls_config = False

    if TEMPORAL_TLS_CERT_PATH and TEMPORAL_TLS_KEY_PATH:
        if os.path.exists(TEMPORAL_TLS_CERT_PATH) and os.path.exists(TEMPORAL_TLS_KEY_PATH):
            with open(TEMPORAL_TLS_CERT_PATH, "rb") as f:
                cert = f.read()
            with open(TEMPORAL_TLS_KEY_PATH, "rb") as f:
                key = f.read()
                
            tls_config = TLSConfig(client_cert=cert, client_private_key=key)
            print(f"🔒 Conectando a Temporal Cloud ({TEMPORAL_NAMESPACE}) via mTLS...")
        else:
            print(f"⚠️ Certificados TLS definidos pero no encontrados físicos. Cayendo a Insecure...")

    return await Client.connect(
        TEMPORAL_URL,
        namespace=TEMPORAL_NAMESPACE,
        tls=tls_config
    )
