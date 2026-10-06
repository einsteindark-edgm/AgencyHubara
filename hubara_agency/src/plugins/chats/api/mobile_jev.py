"""Jev en la App Operador: qué burbuja va primero y cómo se clasifica cada incendio.

Las reglas (`chats/shared/mobile_rules.py`) arman lo legal —las burbujas que se
pueden enviar en la etapa, los chats que esperan a una persona— y son el
respaldo. Jev, con el paquete `operador` (`chats/shared/operator/decisions`),
elige la burbuja principal y la gravedad, el tipo y si empeora de cada
incendio de chat. Los incendios de pedidos (retraso, pago sin verificar) son
hechos: no se le preguntan a Jev.

* **Interruptor:** `OPERATOR_APP_JEV` = off | shadow | on (Terraform
  `lab.operator_app_jev`, nace en off). En sombra Jev contesta y queda en el
  registro de decisiones de la conversación, pero la app ve las reglas.
* **La app nunca espera a Jev:** la burbuja lo espera a lo sumo
  `BUBBLE_WAIT_S` y los incendios nada; lo que no llegó sale por reglas y la
  lectura queda guardada para la MISMA versión del chat (la próxima consulta
  la usa). Máximo `CONCURRENCY` preguntas a la vez y `MAX_FIRE_READS` nuevas
  por consulta de la bandeja.
* **Costo y registro:** cada pregunta suma a `jev_usage` de la conversación
  (`record_jev_cost`) y cada decisión va a `<vault>/<sid>/evals/decisions.jsonl`
  con `stage: "operador"` y `bundle: "operador@1"`, anonimizada.

La caché vive en este proceso (se pierde al desplegar: la siguiente consulta
vuelve a preguntar). Una lectura fallida se guarda `FAILED_TTL_MS` para no
martillar a Jev caído.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog
from src.plugins.chats.agent.sales.decisions.guards import session_redact_terms
from src.plugins.chats.shared.mobile_rules import ChatFireFacts
from src.plugins.chats.shared.operator import decisions
from src.plugins.chats.shared.operator.decisions.builtins import BubbleInput, FireInput
from src.sdk.connectorkit import PerceptionResult, TypedQuestion, oracle_timeout_s, record_jev_cost
from src.sdk.decisionkit import DOUBT, answers_from_result

MODE_ENV = "OPERATOR_APP_JEV"
MODES = ("off", "shadow", "on")
ORACLE = "jev-1.13"
#: Lo más que la app espera la burbuja de Jev antes de mostrar la de las reglas.
BUBBLE_WAIT_S = 2.5
#: Preguntas nuevas a Jev por cada consulta de la bandeja de incendios.
MAX_FIRE_READS = 20
CONCURRENCY = 4
FAILED_TTL_MS = 60_000
CACHE_MAX = 500

#: Lo que dice el veredicto (el mismo vocabulario que las capacidades de ventas).
BY_JEV, BY_FLOOR, BY_FALLBACK = "jev", "piso", "respaldo"
_SEVERITY_RANK = {"grave": 0, "hoy": 1, "espera": 2}
_SEVERITY_WORDS = {"grave": "grave", "hoy": "hoy", "espera": "puede esperar"}
_KIND_WORDS = {
    "wants_human": "pide una persona",
    "angry": "queja o molestia",
    "asking_status": "pregunta por su pedido",
    "payment_proof": "comprobante de pago",
    "order_problem": "problema con el pedido o el envío",
    "health": "tema de salud",
    "praise": "felicita o agradece",
    "sale_at_risk": "quiere comprar",
    "bot_stuck": "el bot no pudo seguir",
    "other": "otra cosa",
}


def jev_mode() -> str:
    """off | shadow | on (lo que no se entiende, off)."""
    raw = (os.getenv(MODE_ENV) or "off").strip().lower()
    return raw if raw in MODES else "off"


@dataclass(frozen=True)
class Reading:
    """Una lectura terminada: lo que vale (Jev con su piso, o las reglas)."""

    value: Any
    by: str
    reason: str = ""
    at_ms: int = 0

    @property
    def from_jev(self) -> bool:
        return self.by in (BY_JEV, BY_FLOOR)


def apply_bubble(payload: dict[str, Any], value: Any) -> dict[str, Any]:
    """La burbuja que eligió Jev va primero y resaltada; "" = ninguna resaltada."""
    items = list(payload.get("suggestions") or [])
    if value == "":
        ordered = [{**s, "prominence": "normal"} for s in items]
    else:
        try:
            index = int(value)
            chosen = items[index]
        except (TypeError, ValueError, IndexError):
            return payload
        ordered = [{**chosen, "prominence": "primary"}] + [
            {**s, "prominence": "normal"} for i, s in enumerate(items) if i != index
        ]
    return {**payload, "decided_by": "jev", "suggestions": ordered}


def _fire_words(value: Mapping[str, Any]) -> str:
    """Lo que Jev leyó, en palabras: la «evaluación anterior» de la próxima vez."""
    severity = _SEVERITY_WORDS.get(str(value.get("severity")), str(value.get("severity")))
    kind = _KIND_WORDS.get(str(value.get("kind")), "otra cosa")
    return f"{severity}, {kind}"


def _feed_order(card: Mapping[str, Any]) -> tuple[int, bool, int]:
    """El orden de la app (`FireOrdering.FEED`): grave primero, lo que empeora, lo más viejo."""
    return (_SEVERITY_RANK.get(str(card.get("severity")), 9), not card.get("getting_worse"), int(card.get("updated_ms") or 0))


class OperatorJev:
    """Jev para la API móvil: una instancia por proceso (`get_mobile_deps`)."""

    def __init__(
        self,
        *,
        port: Any,
        vault_dir: Path,
        now_ms: Callable[[], int],
        mode: Callable[[], str] = jev_mode,
    ) -> None:
        #: Un `PerceptionPort` o una fábrica sin argumentos (se construye al primer uso).
        self._port_ref = port
        self._vault = Path(vault_dir)
        self._now_ms = now_ms
        self._mode = mode
        self._cache: OrderedDict[tuple[Any, ...], Reading] = OrderedDict()
        self._inflight: dict[tuple[Any, ...], asyncio.Task[Reading]] = {}
        #: Lo último que Jev leyó de cada incendio de chat (para «¿empeoró?»).
        self._previous_fire: dict[str, str] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None

    # ── burbujas ──

    async def suggestions(
        self, payload: dict[str, Any], *, events: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        mode = self._mode()
        items = payload.get("suggestions") or []
        if mode not in ("shadow", "on") or not items:
            return payload
        session_id = str(payload.get("session_id"))
        inp = BubbleInput(stage=str(payload.get("stage") or ""), suggestions=tuple(items), events=tuple(events))
        key = ("burbuja", session_id, payload.get("version"),
               tuple(json.dumps(s.get("action"), sort_keys=True, ensure_ascii=False) for s in items))
        reading = self._cached(key)
        if reading is None:
            names = _profile_names(metadata)
            task = self._start(key, lambda: self._read(
                "burbuja", session_id, inp, {"stage": inp.stage}, names=names,
            ))
            if mode == "on":
                try:
                    reading = await asyncio.wait_for(asyncio.shield(task), BUBBLE_WAIT_S)
                except TimeoutError:
                    reading = None  # sigue en segundo plano: la próxima consulta la usa
        if mode != "on" or reading is None or not reading.from_jev:
            return payload
        return apply_bubble(payload, reading.value)

    # ── incendios ──

    async def fires(
        self,
        cards: list[dict[str, Any]],
        *,
        chats: Mapping[str, ChatFireFacts],
        events_for: Callable[[str], Sequence[Mapping[str, Any]]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """(tarjetas, si alguna salió de Jev). Nunca espera: lo que falta se
        pregunta en segundo plano y vale desde la próxima consulta."""
        mode = self._mode()
        if mode not in ("shadow", "on"):
            return cards, False
        now = self._now_ms()
        out: list[dict[str, Any]] = []
        used, started = False, 0
        for card in cards:
            subject = card.get("subject") or {}
            facts = chats.get(str(subject.get("session_id"))) if subject.get("kind") == "chat" else None
            if facts is None:
                out.append(card)
                continue
            key = ("incendio", facts.session_id, facts.last_inbound_ms, facts.unanswered_count, facts.escalation_reason)
            reading = self._cached(key)
            if reading is None and key not in self._inflight and started < MAX_FIRE_READS:
                started += 1
                since = facts.waiting_since_ms or facts.last_inbound_ms or now
                inp = FireInput(
                    card=card, reason=facts.escalation_reason, unanswered_count=facts.unanswered_count,
                    wait_ms=now - since, events=tuple(events_for(facts.session_id)),
                    previous=self._previous_fire.get(facts.session_id),
                )
                self._start(key, self._fire_reader(facts.session_id, inp))
            if mode == "on" and reading is not None and reading.from_jev:
                card = {**card, **reading.value}
                used = True
            out.append(card)
        if used:
            out.sort(key=_feed_order)
        return out, used

    def _fire_reader(self, session_id: str, inp: FireInput) -> Callable[[], Awaitable[Reading]]:
        async def read() -> Reading:
            reading = await self._read(
                "incendio", session_id, inp, {"previous": inp.previous, "reason": inp.reason},
                names=_profile_names(_metadata(self._vault, session_id)),
            )
            if reading.from_jev:
                self._previous_fire[session_id] = _fire_words(reading.value)
            return reading

        return read

    async def drain(self) -> None:
        """Espera las preguntas en curso (pruebas y apagado ordenado)."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)

    # ── una pregunta ──

    async def _read(
        self, capability: str, session_id: str, inp: Any, fields: Mapping[str, Any], *, names: Sequence[str]
    ) -> Reading:
        now = self._now_ms()
        rule: Any = None
        try:
            bundle = decisions.active_bundle()
            table = bundle.capability(capability)
            spec = table.spec
            rule = decisions.call(spec.rule, inp)
            state = decisions.call(spec.state, inp)
            if state is None:
                return Reading(rule, BY_FALLBACK, "no_question", now)
            questions, options = _questions(table, inp, fields)
        except Exception as exc:  # noqa: BLE001 — un paquete roto nunca tumba la app
            logger.warning("operator_jev.bundle_failed capability={} error={}", capability, str(exc)[:200])
            return Reading(rule, BY_FALLBACK, "bundle_error", now)
        redact = (*session_redact_terms(session_id, self._vault), *names)
        result = await self._ask(state, questions, redact)
        record_jev_cost(session_id, result.cost_usd, vault_dir=self._vault)
        answers: dict[str, Any] = {}
        if not result.ok:
            reading = Reading(rule, BY_FALLBACK, result.error or "provider_error", now)
        else:
            answers = answers_from_result(questions, result)
            decision = table.decide_explained(answers=answers, rule=rule, inp=dict(fields), options=options)
            if decision.value is DOUBT:
                reading = Reading(rule, BY_FALLBACK, "bundle_error" if decision.error else "duda", now)
            else:
                final = decisions.call(spec.floor, inp, rule, decision.value)
                reading = Reading(final, BY_JEV if final == decision.value else BY_FLOOR, "", now)
        self._log(session_id, capability, bundle.ref, reading, rule, result, redact)
        return reading

    async def _ask(self, state: str, questions: list[TypedQuestion], redact: Sequence[str]) -> PerceptionResult:
        try:
            return await self._port().ask(state, questions, timeout_s=oracle_timeout_s(ORACLE, 10.0), redact=redact)
        except Exception as exc:  # noqa: BLE001 — el puerto no debería lanzar; si lo hace, decide la regla
            logger.warning("operator_jev.port_failed error={}", str(exc)[:200])
            return PerceptionResult(ok=False, error="provider_error")

    def _log(self, session_id: str, capability: str, bundle: str, reading: Reading, rule: Any,
             result: PerceptionResult, redact: Sequence[str]) -> None:
        trace = {
            "capability": capability, "by": reading.by, "provider": result.provider or "jev",
            "value": reading.value, "rule": rule, "reason": reading.reason, "model": result.model,
            "latency_ms": result.latency_ms, "agree": reading.value == rule if reading.from_jev else None,
            "answers": [
                {"q": a.id, "p": a.p, "choice": a.choice, "confidence": a.confidence} for a in result.answers
            ],
            "bundle": bundle,
        }
        try:
            SessionDecisionLog(self._vault).record(session_id, trace, stage="operador", redact=redact,
                                                   at_ms=reading.at_ms)
        except Exception as exc:  # noqa: BLE001 — registrar nunca frena la app
            logger.warning("operator_jev.log_failed session={} error={}", session_id, str(exc)[:200])

    # ── caché y tareas ──

    def _port(self) -> Any:
        if not hasattr(self._port_ref, "ask"):
            self._port_ref = self._port_ref()
        return self._port_ref

    def _cached(self, key: tuple[Any, ...]) -> Reading | None:
        reading = self._cache.get(key)
        if reading is None:
            return None
        if not reading.from_jev and reading.reason not in ("duda", "no_question") and (
            self._now_ms() - reading.at_ms > FAILED_TTL_MS
        ):
            del self._cache[key]  # Jev estaba caído: se vuelve a preguntar
            return None
        self._cache.move_to_end(key)
        return reading

    def _remember(self, key: tuple[Any, ...], reading: Reading) -> None:
        self._cache[key] = reading
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:
            self._sem, self._sem_loop = asyncio.Semaphore(CONCURRENCY), loop
        return self._sem

    def _start(self, key: tuple[Any, ...], read: Callable[[], Awaitable[Reading]]) -> asyncio.Task[Reading]:
        task = self._inflight.get(key)
        if task is not None and not task.done():
            return task

        async def run() -> Reading:
            try:
                async with self._semaphore():
                    reading = await read()
            except Exception as exc:  # noqa: BLE001 — una lectura que falla es «decide la regla»
                logger.warning("operator_jev.read_failed key={} error={}", key[:2], str(exc)[:200])
                reading = Reading(None, BY_FALLBACK, "error", self._now_ms())
            self._remember(key, reading)
            return reading

        task = asyncio.create_task(run())
        self._inflight[key] = task

        def done(finished: asyncio.Task[Reading]) -> None:
            if self._inflight.get(key) is finished:
                del self._inflight[key]

        task.add_done_callback(done)
        return task


def _questions(table: Any, inp: Any, fields: Mapping[str, Any]) -> tuple[list[TypedQuestion], dict[str, dict[str, Any]]]:
    """Las preguntas a Jev (las de la entrada primero; las fijas al final) y el
    valor de cada opción armada desde la entrada."""
    questions: list[TypedQuestion] = []
    options: dict[str, dict[str, Any]] = {}
    for q in table.questions_for(dict(fields)):
        criteria = dict(q.criteria)
        if q.options is not None:
            built = decisions.call(q.options, inp, reserved=tuple(criteria))
            options[q.id] = {key: value for key, (_label, value) in built.items()}
            criteria = {**{key: label for key, (label, _value) in built.items()}, **criteria}
        questions.append(TypedQuestion(id=q.id, kind=q.kind, text=q.text, criteria=criteria))
    return questions, options


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


__all__ = ["BUBBLE_WAIT_S", "MODE_ENV", "OperatorJev", "Reading", "apply_bubble", "jev_mode"]
