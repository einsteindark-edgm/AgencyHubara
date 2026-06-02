import os
from temporalio.client import Client, TLSConfig
from temporalio.contrib.opentelemetry import TracingInterceptor
from src.platform.config import (
    TEMPORAL_URL, 
    TEMPORAL_NAMESPACE,
    TEMPORAL_TLS_CERT_PATH,
    TEMPORAL_TLS_KEY_PATH
)
import structlog

logger = structlog.get_logger()

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
            logger.info("Connecting to Temporal Cloud via mTLS", namespace=TEMPORAL_NAMESPACE)
        else:
            logger.warning("TLS certificates defined but not found. Falling back to insecure connection.")

    # OTel obs: TracingInterceptor crea spans OTel automáticos para cada client call,
    # workflow execution y activity execution, propagados a través del server (un trace
    # por Workflow Execution). Si OTel no se inicializó (init_otel no corrió o
    # OTEL_SDK_DISABLED=true), usa el TracerProvider no-op global → cero efecto.
    return await Client.connect(
        TEMPORAL_URL,
        namespace=TEMPORAL_NAMESPACE,
        tls=tls_config,
        interceptors=[TracingInterceptor()],
    )
