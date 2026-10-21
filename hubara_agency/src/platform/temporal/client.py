"""El cliente de Temporal del proceso.

``get_temporal_client()`` conecta UNA vez por proceso y event loop y reusa ese
cliente (canal gRPC + TLS + interceptores): antes cada llamada abría una conexión
nueva, una por webhook entrante. El cliente de temporalio es seguro de compartir
entre corutinas; lo que no se comparte es entre event loops (sus futures son del
loop que los crea), por eso la caché va por loop. Un candado por loop evita que
dos llamadas concurrentes con la caché vacía conecten dos veces. Si conectar
falla no se cachea nada: la próxima llamada reintenta.

Dentro de una activity, ``get_activity_temporal_client()``: el cliente del
worker que la corre (``activity.client()``).
"""
import asyncio
import os
from collections.abc import Awaitable, Callable

from temporalio import activity
from temporalio.client import Client, TLSConfig
from temporalio.contrib.opentelemetry import TracingInterceptor
from src.platform.config import (
    TEMPORAL_URL,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    TEMPORAL_API_KEY,
    TEMPORAL_TLS_CERT_PATH,
    TEMPORAL_TLS_KEY_PATH,
)
import structlog

logger = structlog.get_logger()

# Por event loop. Dict común (no WeakKeyDictionary: no todo loop admite weakref,
# p. ej. uvloop); las entradas de loops ya cerrados se podan al conectar.
_clients: dict[asyncio.AbstractEventLoop, Client] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def reset_temporal_client_cache() -> None:
    """Olvida los clientes cacheados (tests: estado del proceso, lección L-2)."""
    _clients.clear()
    _locks.clear()


async def get_temporal_client() -> Client:
    """El cliente de Temporal de este proceso (y event loop): conecta la primera
    vez y después lo reusa."""
    loop = asyncio.get_running_loop()
    cached = _clients.get(loop)
    if cached is not None:
        return cached
    lock = _locks.setdefault(loop, asyncio.Lock())
    async with lock:
        cached = _clients.get(loop)
        if cached is None:
            _forget_closed_loops()
            cached = await _connect()
            _clients[loop] = cached
        return cached


def _forget_closed_loops() -> None:
    for loop in [lp for lp in (*_clients, *_locks) if lp.is_closed()]:
        _clients.pop(loop, None)
        _locks.pop(loop, None)


async def get_activity_temporal_client(
    fallback: Callable[[], Awaitable[Client]] | None = None,
) -> Client:
    """El cliente para el código que corre AHORA.

    Dentro de una activity ``async def`` corriendo en un worker:
    ``activity.client()`` — el cliente con el que el worker corre la activity
    (mismo namespace e interceptores). Fuera de una activity (endpoint HTTP,
    script) o en una ``ActivityEnvironment`` de tests sin cliente: ``fallback()``,
    por defecto el cliente del proceso (``get_temporal_client``). Sirve para los
    helpers que se llaman desde los dos lados.

    ``fallback`` existe para que el call site pase SU nombre de
    ``get_temporal_client`` (el que sus tests reemplazan).
    """
    if activity.in_activity():
        try:
            return activity.client()
        except RuntimeError:
            # `def` activity o ActivityEnvironment sin cliente.
            pass
    return await (fallback or get_temporal_client)()


class TemporalTLSConfigError(RuntimeError):
    """mTLS pedido (``TEMPORAL_TLS_*`` definido) pero imposible de armar."""


def _mtls_config() -> TLSConfig | bool:
    """``TLSConfig`` de mTLS, o ``False`` (sin TLS) si no se pidió.

    Falla CERRADO: con ``TEMPORAL_TLS_CERT_PATH``/``TEMPORAL_TLS_KEY_PATH``
    definidos, un archivo ausente o un solo path definido es un error — antes
    caía a una conexión sin TLS con un warning (secret sin montar = tráfico en
    claro sin que nadie se entere). Sin ninguno de los dos (stack local/docker)
    no hay TLS, como siempre.
    """
    if not TEMPORAL_TLS_CERT_PATH and not TEMPORAL_TLS_KEY_PATH:
        return False
    if not (TEMPORAL_TLS_CERT_PATH and TEMPORAL_TLS_KEY_PATH):
        missing = "TEMPORAL_TLS_KEY_PATH" if TEMPORAL_TLS_CERT_PATH else "TEMPORAL_TLS_CERT_PATH"
        raise TemporalTLSConfigError(
            f"mTLS de Temporal a medio configurar: falta {missing} "
            "(define TEMPORAL_TLS_CERT_PATH y TEMPORAL_TLS_KEY_PATH, o ninguno de los dos)."
        )
    absent = [
        f"{name}={path}"
        for name, path in (
            ("TEMPORAL_TLS_CERT_PATH", TEMPORAL_TLS_CERT_PATH),
            ("TEMPORAL_TLS_KEY_PATH", TEMPORAL_TLS_KEY_PATH),
        )
        if not os.path.exists(path)
    ]
    if absent:
        raise TemporalTLSConfigError(
            "mTLS de Temporal configurado pero los archivos no existen: "
            + ", ".join(absent)
            + ". No se conecta sin TLS."
        )
    with open(TEMPORAL_TLS_CERT_PATH, "rb") as f:
        cert = f.read()
    with open(TEMPORAL_TLS_KEY_PATH, "rb") as f:
        key = f.read()
    logger.info("Connecting to Temporal Cloud via mTLS", namespace=TEMPORAL_NAMESPACE)
    return TLSConfig(client_cert=cert, client_private_key=key)


async def _connect() -> Client:
    """
    Construye y retorna el conector de Temporal Client.
    Soporta API key (Temporal Cloud, preferida), mTLS (Temporal Cloud) y HTTP local.
    """
    # API key auth (Temporal Cloud): endpoint regional + tls=True (bool, no TLSConfig).
    # Preferida sobre mTLS — sin certs que generar/rotar (INFRASTRUCTURE.md §6).
    if TEMPORAL_API_KEY:
        logger.info("Connecting to Temporal Cloud via API key", namespace=TEMPORAL_NAMESPACE)
        return await Client.connect(
            TEMPORAL_ADDRESS,
            namespace=TEMPORAL_NAMESPACE,
            api_key=TEMPORAL_API_KEY,
            tls=True,
            interceptors=[TracingInterceptor()],
        )

    # Sin API key: mTLS si TEMPORAL_TLS_* está definido (falla cerrado), si no
    # sin TLS (stack local/docker).
    tls_config = _mtls_config()

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
