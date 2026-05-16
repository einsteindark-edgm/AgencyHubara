"""Workers Temporal del plugin `catalog`.

- ``sync.py``: ``main()`` arranca el worker que escucha ``CATALOG_SYNC_QUEUE``
  y procesa ``CatalogSyncWorkflow`` (pull desde Medusa + write atómico del
  snapshot).
"""
