"""Workers Temporal del plugin `chats`.

Un worker por sub-dominio (cada uno corre en su propia task queue exclusiva
y se deploya como container/pod independiente):

- ``sales.py``: ``main()`` arranca el worker de ventas (queue ``SALES_QUEUE``).
- ``remarketing.py``: ``main()`` arranca el worker de remarketing
  (queue ``REMARKETING_QUEUE``).

Patrón actual: cada worker registra sus tools via ``register_tool_extension``
en module-load time, después construye un ``Worker(...)`` con el workflow
+ activities relevantes.

PR3 agregará un meta-launcher (``hubara_agency/src/run_workers.py``) que
descubre ambos workers via ``manifest.agent.workers`` y los arranca en
paralelo (asyncio.gather) para conveniencia de dev local. En producción cada
worker sigue siendo un container separado por motivos de aislamiento +
escalado independiente.
"""
