"""Propagación de contexto OTel a través de boundaries async (HTTP → Temporal).

El webhook de WhatsApp (``src/plugins/chats/api/sales.py``) recibe el mensaje y
arranca el workflow en un **background task** de FastAPI. FastAPI corre esos tasks
DESPUÉS de enviar la respuesta HTTP — para entonces el span del request ya cerró y
el contextvar de OTel se perdió. Si el workflow arranca ahí sin más, su
``RunWorkflow`` abre un trace NUEVO, desconectado del webhook → se rompe el
"sistema a sistema".

``add_traced_background_task`` captura el contexto OTel vigente (con el span del
request activo) y lo re-attachea DENTRO del task. Cuando la función llama al
Temporal client, el ``TracingInterceptor`` inyecta ese contexto en el header del
workflow → ``RunWorkflow`` cuelga del span del webhook: webhook → workflow →
activity → LLM → tool en un único trace distribuido.

DEHA: vive en ``platform/`` (los plugins SÍ pueden importarlo; R-DIP-clean). Sin
estado de módulo (R-STATELESS). Inofensivo con ``OTEL_SDK_DISABLED`` — ``get_current``
devuelve el contexto raíz y ``attach``/``detach`` son baratos.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def add_traced_background_task(
    background_tasks: Any,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Encola ``func`` en ``background_tasks`` preservando el contexto OTel actual.

    Drop-in de ``background_tasks.add_task(func, *args, **kwargs)`` que, además,
    propaga el trace del request al task (ver docstring del módulo). Soporta
    ``func`` sync o async (igual que Starlette).
    """
    from opentelemetry import context as _otel_context

    captured = _otel_context.get_current()

    async def _runner() -> None:
        token = _otel_context.attach(captured)
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        finally:
            _otel_context.detach(token)

    background_tasks.add_task(_runner)
