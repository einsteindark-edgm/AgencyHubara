"""Plugin ``catalog`` — agente DEHA programatico (sin LLM) que sincroniza el
catalogo desde Medusa Admin API y escribe un snapshot atomico que el sub-agente
Sales (plugin chats) lee via ``src.platform.catalog.local_snapshot``.

────────────────────────────────────────────────────────────────────────
TRIGGER MODEL (v1)
────────────────────────────────────────────────────────────────────────
El workflow ``CatalogSyncWorkflow`` se ejecuta **on-demand**, NO en
Schedule periodico. Quien lo dispara:

  - Manual (ops/debug):
      uv run python scripts/trigger_catalog_sync.py [--no-wait]

  - Programmatic (futuro ``product_sync_agent`` — cuando exista):
      desde una activity del agente que muta el catalogo en Medusa,
      iniciar el workflow con ``client.start_workflow(...)``. Ver
      docstring completa en ``workflows/sync.py`` con el snippet
      canonico (~20 LOC) o el script ``scripts/trigger_catalog_sync.py``
      como referencia.

Razon de no-Schedule: ver ``workflows/sync.py`` docstring "RAZÓN".

────────────────────────────────────────────────────────────────────────
REGISTRO DE WORKFLOWS / ACTIVITIES
────────────────────────────────────────────────────────────────────────
El worker (``src.plugins.catalog.workers.sync``) importa
``CatalogSyncWorkflow`` y las dos activities (``pull_medusa_catalog_activity``,
``write_snapshot_activity``) explícitamente y las pasa al ``Worker(...)``
constructor de Temporal. **No hay** un agregador ``WORKFLOWS``/``ACTIVITIES``
en este ``__init__`` — el patrón "cada worker conoce su lista" se aplica
igual que en el plugin chats.

El campo ``agent.python_module: src.plugins.catalog.agent`` del
``plugin.yaml`` está reservado para introspección futura; el meta-launcher
real (``src.run_workers``) usa ``agent.workers`` directamente.
"""
