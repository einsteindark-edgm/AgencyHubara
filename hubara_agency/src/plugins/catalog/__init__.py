"""Plugin `catalog` — sincronización del catálogo de productos.

Pull periódico (Schedule de Temporal o disparo manual) → snapshot atómico en
filesystem (`/var/lib/hubara/catalog`) → consumido por el plugin chats
(sales tools `search_products` / `get_product_by_handle` leen el snapshot
con cache mtime-aware).

Subpaquetes:
- ``agent/``: workflows / activities / use_cases / contracts del worker.
- ``workers/sync.py``: composition root del worker. Registra activities
  + arranca el ``Worker(...)`` en ``CATALOG_SYNC_QUEUE``.

Plugin sin API HTTP — el frontend (features upload) interactúa via las APIs
del plugin chats (`/api/dashboard/*`) cuando aplica. La sync se dispara
desde ``scripts/trigger_catalog_sync.py`` o desde otro plugin (ej. un futuro
``product_sync_agent`` que dispare el workflow al final de su pipeline).

Manifest: ``frontend_dashboard/src/plugins/catalog/plugin.yaml``.
"""
