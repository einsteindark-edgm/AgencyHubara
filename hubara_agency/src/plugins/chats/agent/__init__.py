"""Lógica agéntica del plugin ``chats``.

Subpaquetes:

- ``sales/``: ``HubaraSalesSessionWorkflow`` + tools + activities + use_cases
  + state + contracts + prompts + parsers + workspace canónico.
- ``remarketing/``: ``RemarketingSessionWorkflow`` + activities + contracts
  + prompts + workspace canónico.

**Cómo se registran los workflows / activities / tools**:

Cada worker (``src.plugins.chats.workers.sales`` y
``src.plugins.chats.workers.remarketing``) importa de forma explícita los
workflows + activities + tools que necesita y los pasa al ``Worker(...)``
constructor de Temporal. **No hay** un agregador ``WORKFLOWS``/``ACTIVITIES``
en este ``__init__`` — el modelo "cada worker conoce su lista" mantiene el
isolation operacional entre task queues (SALES_QUEUE vs REMARKETING_QUEUE).

El campo ``agent.python_module: src.plugins.chats.agent`` del ``plugin.yaml``
está reservado para uso futuro (e.g. introspección por un meta-launcher que
quiera enumerar workflows por plugin). Hoy nadie lo lee — el meta-launcher
real (``src.run_workers``) usa ``agent.workers`` directamente.
"""
