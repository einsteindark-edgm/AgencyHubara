"""Workers Temporal del plugin `eta`.

- ``eta.py``: ``main()`` arranca el worker del Agente ETA (queue
  ``queue-eta-agent``). Registra sus dos tools LLM (escalar a humano /
  transferir a Ventas) via ``register_tool_extension`` en module-load time,
  después construye un ``Worker(...)`` con ``HubaraEtaSessionWorkflow`` + las
  activities de conversación (exoclaw + platform) y las domain-specific del
  agente ETA.

El meta-launcher (``hubara_agency/src/run_workers.py``) lo descubre via
``manifest.agent.workers`` y lo arranca junto a los demás. En producción es un
container/pod independiente (ver `k8s/aws-produccion/worker-eta.yaml`).
"""
