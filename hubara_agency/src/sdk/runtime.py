"""Runtime — estado compartido, vault, Temporal y logging para plugins.

La superficie de I/O que un plugin necesita para operar (medida del uso real:
``WORKSPACE_VAULT_DIR`` ×26, ``get_temporal_client`` ×12,
``FilesystemMetadataStore`` ×11, ``with_heartbeat`` ×6, ``setup_logging`` ×6).

Uso canónico::

    from src.sdk.runtime import (
        WORKSPACE_VAULT_DIR,
        FilesystemMetadataStore,
        get_temporal_client,
        with_heartbeat,
    )

    @activity.defn(name="my_activity")
    @with_heartbeat(every=10)          # R-HEARTBEAT si worst-case > 10s
    async def my_activity(...): ...

Notas de diseño:
- ``FilesystemMetadataStore`` es el acceso al metadata de sesión del vault.
  Estado process-wide NUEVO exige ``clear()`` + fixture autouse (lección L-2).
- ``atomic_write_json`` es la única forma sancionada de escribir JSON al
  vault (write-rename, sin archivos a medio escribir).
- ``client_ip`` es la IP real del cliente detrás de Caddy/CloudFront (primer
  hop de ``X-Forwarded-For``); la clave correcta para cualquier límite por IP
  en un router expuesto (el peer es siempre el proxy).
- ``mba_standby_enabled`` / ``mba_customer_allowed``: los interruptores de
  Meta Business Agent del lado de la plataforma (flag + lista cerrada), leídos
  en cada llamada; ``mba_controls_thread(metadata, session_id)`` los combina
  con el dueño del hilo: la ÚNICA pregunta que hace cualquier envío proactivo
  ("¿responde MBA acá?") — fail-safe False; ``is_placeholder`` distingue un secreto real del
  placeholder de SSM (un adapter con placeholder NO debe llamar a nadie).
- ``CONTROL_OWNER_MBA`` / ``CONTROL_OWNER_HUBARA`` (``CONTROL_OWNERS``): los
  valores de ``metadata.control_owner`` de una sesión de WhatsApp — quién
  responde al cliente según el webhook ``messaging_handovers`` de Meta
  Business Agent (chats lo escribe, mba lo lee).
"""
from __future__ import annotations

from src.platform.config import (
    is_placeholder as is_placeholder,
    mba_controls_thread as mba_controls_thread,
    mba_customer_allowed as mba_customer_allowed,
    mba_standby_enabled as mba_standby_enabled,
    AWS_REGION as AWS_REGION,
    GRAPHAGENTS_INSTANCE_TAG as GRAPHAGENTS_INSTANCE_TAG,
    WORKSPACE_VAULT_DIR as WORKSPACE_VAULT_DIR,
)
from src.platform.constants import (
    CONTROL_OWNER_HUBARA as CONTROL_OWNER_HUBARA,
    CONTROL_OWNER_MBA as CONTROL_OWNER_MBA,
    CONTROL_OWNERS as CONTROL_OWNERS,
)
from src.platform.logging import (
    setup_logging as setup_logging,
)
from src.platform.rate_limit import (
    client_ip as client_ip,
)
from src.platform.state import (
    FilesystemMetadataStore as FilesystemMetadataStore,
    atomic_write_json as atomic_write_json,
)
from src.platform.temporal.client import (
    get_temporal_client as get_temporal_client,
)
from src.platform.temporal.heartbeat import (
    with_heartbeat as with_heartbeat,
)
