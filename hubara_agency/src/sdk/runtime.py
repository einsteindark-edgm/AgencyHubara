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
"""
from __future__ import annotations

from src.platform.config import (
    WORKSPACE_VAULT_DIR as WORKSPACE_VAULT_DIR,
)
from src.platform.logging import (
    setup_logging as setup_logging,
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
