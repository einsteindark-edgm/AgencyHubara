"""Composition root del analytics layer.

Inicializa el `EventBus` global con los sinks apropiados según
configuración. Idempotente: si ya fue inicializado, devuelve el bus tal
cual. Útil para tests que quieren overridear.

Llamado típicamente desde el boot de workers / HTTP layer (ej:
`src/plugins/chats/workers/sales.py` antes de `Worker(...)`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import structlog

from src.platform.analytics.bus import EventBus, get_event_bus
from src.platform.analytics.filesystem_sink import FilesystemAnalyticsSink
from src.platform.config import WORKSPACE_VAULT_DIR

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def setup_analytics() -> EventBus:
    """Construye el bus con todos los sinks configurables. Idempotente."""
    bus = get_event_bus()
    # Filesystem sink siempre activo — auditoría / post-mortem
    fs_dir = Path(WORKSPACE_VAULT_DIR) / "_analytics"
    bus.add_sink(FilesystemAnalyticsSink(fs_dir))
    logger.info("analytics.fs_sink_registered", dir=str(fs_dir))

    # NOTA (2026-07-01): el `MetaConversionsAPISink` fue ELIMINADO. Era un
    # stub divergente del camino CAPI real (`send_capi_event_activity` en
    # platform/whatsapp/capi_activity.py): mandaba Lead al primer touch (la
    # activity manda LeadSubmitted al cierre), event_id random (sin dedup),
    # sin ventana de 7 días. Tener ambos activos inflaba Leads (doble-Lead).
    # El ÚNICO camino a Meta CAPI es la activity; este bus queda para
    # auditoría local (filesystem).
    return bus
