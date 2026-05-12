"""catalog_sync — agente DEHA programatico (sin LLM) que sincroniza el
catalogo desde Medusa Admin API y escribe un snapshot atomico que el
agente Sales lee via src/platform/catalog/local_snapshot.py.

────────────────────────────────────────────────────────────────────────
TRIGGER MODEL (v1)
────────────────────────────────────────────────────────────────────────
El workflow `CatalogSyncWorkflow` se ejecuta **on-demand**, NO en
Schedule periodico. Quien lo dispara:

  - Manual (ops/debug):
      uv run python scripts/trigger_catalog_sync.py [--no-wait]

  - Programmatic (futuro `product_sync_agent` — cuando exista):
      desde una activity del agente que muta el catalogo en Medusa,
      iniciar el workflow con `client.start_workflow(...)`. Ver
      docstring completa en `workflows/sync.py` con el snippet
      canonico (~20 LOC) o el script `scripts/trigger_catalog_sync.py`
      como referencia.

Razon de no-Schedule: ver `workflows/sync.py` docstring "RAZÓN".
"""
