"""Las decisiones de la App Operador, por el motor de decisiones oficial (PR #372).

Qué burbuja va primero en el chat (`burbuja`) y cómo se clasifica cada incendio
de chat (`incendio`) son capacidades del paquete `operador`
(`chats/shared/operator/decisions/`). Se resuelven como las de ventas (el
resolutor `registry.foreign_capability`: `BundledCapability` con los builtins
de ese paquete) y se deciden con
`decide_for_session`: el modo de cada conversación sale del control del motor
(`_rollout/decisions.json`, que se mueve por comando con `decisions/control.py`,
dentro del techo `SALES_CAPABILITIES_CEILING`) y el motor deja la decisión en la conversación
(`stage: "operador"`), sus métricas, la cola de desacuerdos y el costo de Jev.
Las reglas de `mobile_rules` arman lo legal y son la regla de cada capacidad.

Aquí solo vive lo de la app: la latencia. La app nunca espera a Jev —la
burbuja, a lo sumo `BUBBLE_WAIT_S`; los incendios, nada— y el veredicto queda
guardado para lo mismo que Jev lee (etapa, burbujas y conversación) y el mismo
modo (la siguiente consulta lo usa). El veredicto se aplica tal cual: con la regla, la respuesta es la de
las reglas. Los incendios de pedidos (retraso, pago) son hechos: no son una
decisión y no pasan por acá.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities import BY_FLOOR, BY_JEV, Verdict
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session, session_redact_terms
from src.plugins.chats.shared.mobile_rules import ChatFireFacts
from src.plugins.chats.shared.operator import decisions
from src.plugins.chats.shared.operator.decisions.builtins import BubbleInput, FireInput, reading_words

#: Lo más que la app espera la burbuja de Jev antes de mostrar la de las reglas.
BUBBLE_WAIT_S = 2.5
#: Decisiones nuevas por cada consulta de la bandeja de incendios.
MAX_FIRE_READS = 20
CONCURRENCY = 4
#: Un veredicto sin Jev por una falla (no por duda) se vuelve a pedir pasado esto.
FAILED_TTL_MS = 60_000
CACHE_MAX = 500
#: Dónde queda cada decisión en la conversación (Calidad LLM).
STAGE = "operador"

_SEVERITY_RANK = {"grave": 0, "hoy": 1, "espera": 2}
_HOUR_MS = 3_600_000


def operator_capability(name: str) -> Any:
    """La capacidad `name` del paquete `operador`, pedida al resolutor del motor
    (el único que arma capacidades) con los builtins de ese paquete."""
    return registry.foreign_capability(name, bundle=decisions.active_bundle(), builtins=decisions.builtin)


def _from_jev(verdict: Verdict) -> bool:
    return verdict.by in (BY_JEV, BY_FLOOR)


def apply_bubble(payload: dict[str, Any], verdict: Verdict) -> dict[str, Any]:
    """La burbuja del veredicto va primero y resaltada ("" = ninguna resaltada).
    Con la regla ("0") queda la respuesta de las reglas."""
    items = list(payload.get("suggestions") or [])
    if verdict.value == "":
        ordered = [{**s, "prominence": "normal"} for s in items]
    else:
        try:
            index = int(verdict.value)
            chosen = items[index]
        except (TypeError, ValueError, IndexError):
            return payload
        ordered = [{**chosen, "prominence": "primary"}] + [
            {**s, "prominence": "normal"} for i, s in enumerate(items) if i != index
        ]
    return {**payload, "decided_by": "jev" if _from_jev(verdict) else "rules", "suggestions": ordered}


def _feed_order(card: Mapping[str, Any]) -> tuple[int, bool, int]:
    """El orden de la app (`FireOrdering.FEED`): grave primero, lo que empeora, lo más viejo."""
    return (_SEVERITY_RANK.get(str(card.get("severity")), 9), not card.get("getting_worse"), int(card.get("updated_ms") or 0))


class OperatorDecisions:
    """Las decisiones de la app para la API móvil: una instancia por proceso (`get_mobile_deps`)."""

    def __init__(self, *, vault_dir: Path, now_ms: Callable[[], int]) -> None:
        self._vault = Path(vault_dir)
        self._now_ms = now_ms
        self._cache: OrderedDict[tuple[Any, ...], tuple[Verdict, int]] = OrderedDict()
        self._inflight: dict[tuple[Any, ...], asyncio.Task[Verdict | None]] = {}
        #: Lo último que Jev leyó de cada incendio de chat (la entrada de «¿empeoró?»).
        self._previous_fire: dict[str, str] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None

    # ── burbujas ──

    async def suggestions(
        self, payload: dict[str, Any], *, events: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        items = payload.get("suggestions") or []
        if not items:
            return payload  # sin jugadas legales no hay nada que decidir
        session_id = str(payload.get("session_id"))
        inp = BubbleInput(stage=str(payload.get("stage") or ""), suggestions=tuple(items), events=tuple(events))
        # Lo que Jev lee, no la versión del chat: al decidir anota su costo en `metadata.json`, eso mueve la
        # versión, el dashboard avisa y la app vuelve a pedir; con la versión en la llave, Jev se volvía a
        # preguntar cada ~2,7 s mientras el chat estuviera abierto (caso 2026-10-09).
        key = (
            "burbuja", session_id, self._provider("burbuja", session_id), inp.stage, _conversation_mark(events),
            tuple(json.dumps(s.get("action"), sort_keys=True, ensure_ascii=False) for s in items),
        )
        verdict = self._cached(key)
        if verdict is None:
            names = _profile_names(metadata)
            task = self._start(key, lambda: self._decide("burbuja", session_id, inp, names))
            try:
                verdict = await asyncio.wait_for(asyncio.shield(task), BUBBLE_WAIT_S)
            except TimeoutError:
                return payload  # sigue en segundo plano: la próxima consulta lo usa
        return payload if verdict is None else apply_bubble(payload, verdict)

    # ── incendios ──

    async def fires(
        self,
        cards: list[dict[str, Any]],
        *,
        chats: Mapping[str, ChatFireFacts],
        events_for: Callable[[str], Sequence[Mapping[str, Any]]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """(tarjetas, si alguna salió de Jev). Nunca espera: lo que falta se
        decide en segundo plano y vale desde la próxima consulta."""
        now = self._now_ms()
        out: list[dict[str, Any]] = []
        used, started = False, 0
        for card in cards:
            subject = card.get("subject") or {}
            facts = chats.get(str(subject.get("session_id"))) if subject.get("kind") == "chat" else None
            if facts is None:
                out.append(card)  # un pedido: hechos, no una decisión
                continue
            sid = facts.session_id
            wait_ms = now - (facts.waiting_since_ms or facts.last_inbound_ms or now)
            # La hora de espera entra en la llave: el paquete decide distinto con horas de espera, y así Jev relee
            # un chat callado una vez por hora (no en cada consulta).
            key = (
                "incendio", sid, self._provider("incendio", sid), facts.last_inbound_ms, facts.unanswered_count,
                facts.escalation_reason, facts.handoff_pending, wait_ms // _HOUR_MS,
                card.get("severity"), card.get("kind"), card.get("getting_worse"),
            )
            verdict = self._cached(key)
            if verdict is None and key not in self._inflight and started < MAX_FIRE_READS:
                started += 1
                inp = FireInput(
                    card=card, reason=facts.escalation_reason, unanswered_count=facts.unanswered_count,
                    wait_ms=wait_ms, events=tuple(events_for(sid)), previous=self._previous_fire.get(sid),
                    handoff=facts.handoff_pending, waited_min=max(0, wait_ms) // 60_000,
                )
                self._start(key, self._fire_decision(sid, inp))
            if verdict is not None and isinstance(verdict.value, Mapping):
                card = {**card, **verdict.value}
                used = used or _from_jev(verdict)
            out.append(card)
        if used:
            out.sort(key=_feed_order)
        return out, used

    def _fire_decision(self, session_id: str, inp: FireInput) -> Callable[[], Awaitable[Verdict | None]]:
        async def run() -> Verdict | None:
            verdict = await self._decide("incendio", session_id, inp, _profile_names(_metadata(self._vault, session_id)))
            if verdict is not None and isinstance(verdict.jev, Mapping):
                self._previous_fire[session_id] = reading_words(verdict.jev)
            return verdict

        return run

    async def drain(self) -> None:
        """Espera las decisiones en curso (pruebas y apagado ordenado)."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)

    # ── el motor ──

    async def _decide(self, name: str, session_id: str, inp: Any, names: Sequence[str]) -> Verdict | None:
        redact = (*session_redact_terms(session_id, self._vault), *names)
        try:
            return await decide_for_session(
                operator_capability(name), inp, session_id=session_id, vault_dir=self._vault,
                redact=redact, stage=STAGE,
            )
        except Exception as exc:  # noqa: BLE001 — una regla o un paquete roto nunca tumban la app
            logger.warning("operator_decisions.failed capability={} error={}", name, str(exc)[:200])
            return None

    def _provider(self, name: str, session_id: str) -> str:
        """El proveedor que el control le da a la capacidad en esta conversación
        (reglas | sombra | jev): parte de la llave de la caché, para que mover
        el control valga en la siguiente consulta."""
        return bot_for_session(session_id, vault_dir=self._vault).provider(name)

    # ── caché y tareas ──

    def _cached(self, key: tuple[Any, ...]) -> Verdict | None:
        found = self._cache.get(key)
        if found is None:
            return None
        verdict, at_ms = found
        failed = verdict.reason not in (None, "duda", "no_question")
        if failed and self._now_ms() - at_ms > FAILED_TTL_MS:
            del self._cache[key]  # Jev estaba caído: se vuelve a preguntar
            return None
        self._cache.move_to_end(key)
        return verdict

    def _remember(self, key: tuple[Any, ...], verdict: Verdict) -> None:
        self._cache[key] = (verdict, self._now_ms())
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:
            self._sem, self._sem_loop = asyncio.Semaphore(CONCURRENCY), loop
        return self._sem

    def _start(self, key: tuple[Any, ...], decide: Callable[[], Awaitable[Verdict | None]]) -> asyncio.Task[Verdict | None]:
        task = self._inflight.get(key)
        if task is not None and not task.done():
            return task

        async def run() -> Verdict | None:
            async with self._semaphore():
                verdict = await decide()
            if verdict is not None:
                self._remember(key, verdict)
            return verdict

        task = asyncio.create_task(run())
        self._inflight[key] = task

        def done(finished: asyncio.Task[Verdict | None]) -> None:
            if self._inflight.get(key) is finished:
                del self._inflight[key]

        task.add_done_callback(done)
        return task


#: Cuántos mensajes del final entran en la huella de la conversación (Jev lee 12).
_MARK_EVENTS = 40


def _conversation_mark(events: Sequence[Mapping[str, Any]]) -> str:
    """Huella de lo que hay en la conversación: cambia con cada mensaje nuevo, no con lo que se anota aparte."""
    tail = json.dumps([len(events), list(events[-_MARK_EVENTS:])], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(tail.encode("utf-8")).hexdigest()


def _metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    try:
        data = json.loads((vault_dir / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _profile_names(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """El nombre del perfil de WhatsApp: se tapa antes de que el texto salga hacia Jev."""
    profile = metadata.get("profile") if isinstance(metadata.get("profile"), dict) else {}
    name = str(profile.get("name") or "").strip()
    return (name,) if name else ()


__all__ = ["BUBBLE_WAIT_S", "STAGE", "OperatorDecisions", "apply_bubble", "operator_capability"]
