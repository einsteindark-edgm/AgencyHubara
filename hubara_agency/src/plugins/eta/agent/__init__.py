"""Agentes conversacionales del plugin `eta`.

Sigue la convención `agent/<worker_name>/` (igual que `chats/agent/{sales,
remarketing}`): cada agente nombrado vive en su propio subpaquete. Hoy hay uno:

- ``eta/`` — el Agente de seguimiento (workspace + workflow + activities). El
  worker ``src.plugins.eta.workers.eta`` lo arranca; el dispatcher resuelve su
  workflow en ``src.plugins.eta.agent.eta.workflows`` (lo valida
  ``test_workflow_classes_exist_in_code``).
"""
