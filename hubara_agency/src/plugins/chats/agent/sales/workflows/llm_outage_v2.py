"""Ventas V2 ante una caída total del LLM (incidente 2026-10-15).

DeepSeek se colgó y el respaldo no alcanzó a entrar: `llm_chat` agotó sus
reintentos, el `ActivityError` tumbó el workflow y los mensajes del cliente
quedaron sin respuesta (el equipo los levantó a mano). Con este gancho de
`run_agent_turn` (`on_llm_outage`) Temporal es el respaldo: el turno no se cae,
espera con un timer durable y vuelve a llamar al modelo con el mismo turno.

Deja de esperar en dos casos, y el turno termina sin texto (sin pasar el chat
al equipo: una falla técnica no es un traspaso):
  * una persona tomó el chat (`active_route == humano`): el bot no le habla
    encima. «Intervenir» además termina el workflow; las otras tomas (escalar,
    comprobante, reverso de pago) solo escriben la metadata y se ven acá. La
    ruta `remarketing` NO corta: el ciclo de reactivación la reclama antes de
    su propio turno y, con el LLM caído, ese turno muere y la ruta queda
    pegada mientras el cliente sigue esperando su respuesta;
  * después de la espera, la respuesta ya no alcanzaría a salir antes de que
    cierre la ventana de 24 h (Meta rechaza el texto libre, 131047). Esa
    conversación queda para el ciclo de reactivación.

Replay: el gancho solo corre donde antes el workflow moría (el gate vive en el
turno compartido, `llm-outage-wait-v1`). Lo usa SOLO `HubaraSalesSessionWorkflowV2`.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.activities import read_reply_window_activity
    from src.plugins.chats.agent.sales.contracts import ReplyWindow

#: Esperas entre vueltas, en minutos: crecen y se quedan en la última. Cada
#: vuelta ya trae los reintentos cortos de `llm_chat` (`_LLM_OPTIONS`); entre
#: vueltas no corre nada (el timer lo guarda Temporal).
OUTAGE_WAITS_MIN = (1, 2, 4, 8, 10)
#: Lo que un turno necesita para salir antes de que cierre la ventana: el
#: modelo, las tools, el egreso (hasta 90 s) y el envío.
REPLY_MARGIN = timedelta(minutes=5)
#: Sin ventana conocida (chat sin ingest de ventana), el turno espera como
#: mucho un día desde la primera falla.
UNKNOWN_WINDOW_CAP = timedelta(hours=24)

#: Por qué el turno deja de esperar (traza y alerta).
HUMAN_TOOK_OVER = "human_took_over"
WINDOW_CLOSING = "window_closing"

_ROUTE_VENTAS = "ventas"
_ROUTE_HUMANO = "humano"


def _ms(delta: timedelta) -> int:
    return int(delta.total_seconds() * 1000)


def _now_ms() -> int:
    return int(workflow.now().timestamp() * 1000)


def outage_wait(round_: int) -> timedelta:
    """Cuánto espera la vuelta `round_` (1, 2, …) antes de volver a llamar."""
    return timedelta(minutes=OUTAGE_WAITS_MIN[min(max(round_, 1), len(OUTAGE_WAITS_MIN)) - 1])


def outage_verdict(window: ReplyWindow, *, now_ms: int, wait: timedelta, first_failure_ms: int) -> str | None:
    """None: esperar `wait` y volver a llamar al modelo. Si no, por qué el
    turno se rinde: una persona tomó el chat, o después de esperar la respuesta
    no alcanzaría a salir antes de que cierre la ventana."""
    if window.route == _ROUTE_HUMANO:
        return HUMAN_TOOK_OVER
    closes_ms = (
        window.window_expires_at_ms
        if window.window_expires_at_ms is not None
        else first_failure_ms + _ms(UNKNOWN_WINDOW_CAP)
    )
    if now_ms + _ms(wait) + _ms(REPLY_MARGIN) > closes_ms:
        return WINDOW_CLOSING
    return None


class LLMOutageWait:
    """El gancho `on_llm_outage` de UN turno del V2. Cada vuelta lee la ventana
    otra vez (`read_reply_window`): un mensaje del cliente que llegó mientras
    tanto la estira. `gave_up`: por qué se rindió (None si no se rindió)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.gave_up: str | None = None
        self._first_failure_ms: int | None = None

    async def _window(self) -> ReplyWindow:
        try:
            return await workflow.execute_activity(
                read_reply_window_activity,
                self.session_id,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError:
            # Sin lectura, el chat sigue siendo del bot y la ventana no se
            # conoce: el turno espera hasta su tope de un día.
            return ReplyWindow(route=_ROUTE_VENTAS)

    def _stops(self, window: ReplyWindow, *, wait: timedelta, round_: int, error: ActivityError) -> bool:
        now_ms = _now_ms()
        first_ms = self._first_failure_ms if self._first_failure_ms is not None else now_ms
        verdict = outage_verdict(window, now_ms=now_ms, wait=wait, first_failure_ms=first_ms)
        if verdict is None:
            return False
        self.gave_up = verdict
        workflow.logger.error(
            f"llm_outage_gave_up session={self.session_id} reason={verdict} round={round_} "
            f"route={window.route} window_expires_at_ms={window.window_expires_at_ms} error={error.cause!r}"
        )
        return True

    async def __call__(self, round_: int, error: ActivityError) -> bool:
        if self._first_failure_ms is None:
            self._first_failure_ms = _now_ms()
        wait = outage_wait(round_)
        if self._stops(await self._window(), wait=wait, round_=round_, error=error):
            return False
        workflow.logger.error(
            f"llm_outage_waiting session={self.session_id} round={round_} "
            f"retry_in_s={int(wait.total_seconds())} error={error.cause!r}"
        )
        await workflow.sleep(wait)
        return not self._stops(await self._window(), wait=timedelta(0), round_=round_, error=error)
