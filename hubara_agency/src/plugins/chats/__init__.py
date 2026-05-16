"""Plugin `chats` — el dominio agéntico de WhatsApp (sales + remarketing) +
el dashboard (UI HTTP/SSE + handoff humano).

Subpaquetes:
- ``api/``: routers FastAPI (sales, dashboard, handoff). Composición HTTP.
- ``agent/{sales,remarketing}/``: workflows / activities / tools / use_cases
  / state / contracts / prompts / parsers — la lógica agéntica de cada
  sub-dominio. Workspaces canónicos del agente viven en
  ``agent/<sub>/workspace/`` (IDENTITY.md, SOUL.md, USER.md, TOOLS.md,
  AGENTS.md, memory/*, skills/*).
- ``workers/{sales,remarketing}.py``: composition root del worker Temporal de
  cada sub-dominio. Cada worker registra `register_tool_extension(...)`
  + arranca su propio ``Worker(...)`` en su task queue exclusiva.

PR2 conserva el modelo "un worker por sub-dominio". PR3 introducirá un
meta-launcher que arranque ambos workers desde el manifest del plugin.

Manifest del plugin: ``frontend_dashboard/src/plugins/chats/plugin.yaml``.
"""
