"""Boundary DTOs del sub-agente ETA.

Aplicación de R-JSON: cualquier valor que cruce ``workflow.execute_workflow`` /
``workflow.run`` es un dataclass plano JSON-serializable.

``EtaSessionInput`` es el input del ``HubaraEtaSessionWorkflow``. La
construcción del ``SessionInput`` (LLMConfig + WorkspaceConfig + ToolRegistry +
tool_definitions_json) ocurre DENTRO del workflow vía
``bootstrap_eta_session_activity`` (R-DET), no en los callers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EtaSessionInput:
    """Input del ``HubaraEtaSessionWorkflow``.

    **Todos los campos llevan default.** El workflow se arranca por el
    dispatcher declarativo (``start_workflow_with_replace`` en la transición
    ``to_stage == preparing``), que pasa un **dict** construido desde el
    ``input_mapping`` del manifest (``session_id`` / ``order_id`` / ``to_stage``).
    Temporal's DataConverter reconstruye este dataclass desde ese dict en el
    worker side leyendo los type hints de ``@workflow.run``; los campos ausentes
    del dict (``runtime_workspace_path``, ``turn_count``) DEBEN tener default o
    la deserialización falla (ver contrato del dispatcher en
    ``src.platform.orchestration.dispatcher._build_input``).

    Campos:
      * ``session_id``: id de la conversación (``{WHATSAPP_SESSION_PREFIX}{phone}``).
      * ``order_id``: id Medusa del pedido que se está siguiendo. El workflow lo
        usa para fetchear los datos vivos del pedido (nombre, total, tipo de
        pago, ventana de entrega) vía el order query port en cada notificación.
      * ``to_stage``: el stage que disparó el arranque (``preparing``). El
        workflow encola la notificación inicial de este stage.
      * ``runtime_workspace_path``: ruta del workspace canónico del agente ETA.
        El dispatcher NO la conoce (sería R-DIP #10 conocer config de un sibling),
        así que llega ``None`` y ``bootstrap_eta_session_activity`` la resuelve
        localmente vía ``config/env.py:get_workspace_path()``.
      * ``turn_count``: contador preservado a través de ``continue_as_new``.
    """

    session_id: str = ""
    order_id: str = ""
    to_stage: str = ""
    runtime_workspace_path: str | None = None
    turn_count: int = 0
