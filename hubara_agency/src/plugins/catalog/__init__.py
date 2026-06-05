"""Plugin `catalog` — sincronización del catálogo de productos.

Pull periódico (Schedule de Temporal o disparo manual) → snapshot atómico en
filesystem (`/var/lib/hubara/catalog`) → consumido por el plugin chats
(sales tools `search_products` / `get_product_by_handle` leen el snapshot
con cache mtime-aware).

Subpaquetes:
- ``agent/``: workflows / activities / use_cases / contracts del worker.
- ``workers/sync.py``: composition root del worker. Registra activities
  + arranca el ``Worker(...)`` en ``CATALOG_SYNC_QUEUE``.

Subpaquete ``api/``: router FastAPI (`/api/catalog/*`) que el dashboard usa
para disparar el sync (`POST /sync`), seguir el step-by-step en vivo
(`GET /sync/{id}`), listar el historial (`GET /syncs`) y leer el estado de la
copia local (`GET /snapshot`). El handler arranca/consulta el
``CatalogSyncWorkflow`` vía ``get_temporal_client()`` — un endpoint HTTP no es
workflow ni tool, así que R-DIP lo permite. La sync también se dispara
standalone desde ``scripts/trigger_catalog_sync.py``.

Manifest: ``frontend_dashboard/src/plugins/catalog/plugin.yaml``.
"""
