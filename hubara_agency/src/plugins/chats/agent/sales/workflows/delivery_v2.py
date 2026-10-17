"""Lo que el turno siguiente tiene que saber de una respuesta que no se
confirmó (premortem de la Ola 0 de Temporal, 2026-10-16).

El turno graba su respuesta en el historial del modelo ANTES de enviarla
(`record_turn` corre dentro del turno). Si el envío falla, el modelo la da por
dicha y el cliente pudo no verla. Dos casos distintos:

* la activity lanzó tras sus intentos (un timeout, el worker que se apagó, el
  vault después del POST): es AMBIGUO, Meta pudo entregarla → «pudo no
  llegarle»; el panel y la traza la muestran «sin confirmación»;
* WhatsApp no aceptó ninguna burbuja (la activity devuelve `[]`, no lanza): es
  definitivo → «no le llegó».

La nota va en el `plugin_context` del turno siguiente (no al rol user) y se va
cuando un envío vuelve a salir. Solo payload (L-22). Tuteo (guard
`test_no_voseo_in_agent_strings.py`).
"""
from __future__ import annotations

#: Cuántos avisos guarda el turno siguiente (los últimos).
UNDELIVERED_MAX = 2
_CLIP = 300


def undelivered_note(text: str, *, rejected: bool) -> str:
    """La nota para el modelo sobre su respuesta anterior `text`."""
    quoted = text if len(text) <= _CLIP else text[:_CLIP].rstrip() + "…"
    if rejected:
        return (
            "[SISTEMA — TU MENSAJE NO SALIÓ] Tu respuesta anterior no le llegó al cliente "
            f"(WhatsApp la rechazó): «{quoted}». El cliente no la vio: si sigue siendo relevante, díselo de nuevo."
        )
    return (
        "[SISTEMA — ENVÍO SIN CONFIRMAR] Tu respuesta anterior pudo no llegarle al cliente por una falla técnica "
        f"al enviarla: «{quoted}». Si lo que escribe ahora muestra que no la vio, díselo de nuevo; si no, sigue normal."
    )
