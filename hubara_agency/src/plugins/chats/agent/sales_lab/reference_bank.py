"""Banco de referencia del motor de decisiones (MOTOR_DECISIONES_PLAN, F1). PURO.

Decisión del operador (2026-09-28): Claude Code etiqueta ~150 turnos difíciles
respondiendo el MISMO cuestionario que responde Jev, leyendo la conversación
completa. El banco mide a Jev pregunta por pregunta (precisión, cobertura y
calibración) y dice qué preguntas pueden actuar: precisión ≥ 0,95 con ≥ 30
positivos. Las etiquetas viven en el S3 del laboratorio, nunca en el repo
(`scripts/lab_bench_labels.py`).

* `select_reference_turns`: sorteo determinista (sha256 de semilla + caso)
  que llena la cuota de cada categoría difícil (`CATEGORY_QUOTAS`) y después
  completa el tamaño con los demás turnos. Solo turnos del cliente.
* `build_item`: el `state` y las preguntas EXACTOS que el motor le manda a
  Jev en ese turno con ese perfil (`engine.burst_request`, con la ventana, los
  hechos y lo que se tapa armados como en producción), anonimizados, y la
  conversación completa para quien etiqueta.
* `validate_answers` y `score`: las etiquetas contra las preguntas del ítem y
  la medición de Jev contra las etiquetas.
* `ask_bank`: le pregunta a Jev cada ítem. Es lo único con I/O (el puerto del
  oráculo) y nunca lanza: el turno que falla queda con `ok: false`.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions import engine
from src.plugins.chats.agent.sales.decisions.context import (
    TurnContext,
    customer_window,
    missing_for_stage,
    order_facts,
    redact_terms_from_slots,
)
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.agent.sales.decisions.profiles import EngineProfile, get_engine_profile
from src.plugins.chats.agent.sales.decisions.questionnaire import Questionnaire, load_questionnaire
from src.plugins.chats.agent.sales.turn_trace import draft_slots
from src.plugins.chats.agent.sales_lab.cases import LabCase
from src.plugins.chats.agent.sales_lab.run.arena import calibration
from src.plugins.chats.agent.sales_lab.sandbox.materialize import metadata_as_of
from src.plugins.chats.shared.chat_events import detect_chat_event
from src.plugins.chats.shared.funnel import active_episode
from src.sdk.connectorkit import TypedQuestion, anonymize_text

REFERENCE_SEED = "banco-ref-v1"
REFERENCE_SIZE = 150
#: Cuotas para 150 turnos, en el orden en que se llenan. Con otro tamaño se
#: escalan en proporción; lo que una categoría no alcanza lo completa el resto.
CATEGORY_QUOTAS: tuple[tuple[str, int], ...] = (
    ("respuesta_corta", 45),  # algún mensaje de ≤ 15 caracteres: «Si», «Ok», «Ese», «Dale»
    ("rafaga_multiple", 35),  # dos mensajes o más
    ("cita", 20),  # el cliente cita un mensaje (`reply_to` en el dashboard)
    ("foto", 20),  # el cliente manda una foto
    ("confirmacion", 30),  # responde la tarjeta de confirmación, botones o el formulario
)
TURN_MARK = "ESTE TURNO"

#: Vara para que una pregunta actúe (plan §4).
DEFAULT_DETECT = 0.70
READY_PRECISION = 0.95
READY_MIN_POSITIVES = 30
READY_ACCURACY = 0.95
READY_MIN_ANSWERS = 30

#: El banco mide acierto, no latencia: tiempo máximo holgado (el de producción
#: es el del perfil del oráculo, 3 s) y pocas llamadas a la vez.
BANK_TIMEOUT_S = 15.0
BANK_CONCURRENCY = 4

_SHORT_MAX_CHARS = 15
_CONFIRMATION_COMPONENTS = frozenset({"order_confirmation", "quick_replies", "shipping_flow"})
# El reentry de audio o visión viaja con el wamid + sufijo; el ingest lo quita
# al escribir el dashboard (`ingest_inbound_message._build_reply_kwargs`).
_REENTRY_SUFFIXES = ("_transcribed", "_vision")
# Foto sin visión: la traducción deja el marcador genérico (`translate.py`).
_NO_VISION_PHOTO = "[el cliente envió un image]"


# ── la ráfaga, leída del dashboard como la lee el motor ──────────────────────


def _wamid(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    for suffix in _REENTRY_SUFFIXES:
        value = value.removesuffix(suffix)
    return value


def _burst_pairs(case: LabCase, events: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """Cada mensaje de la ráfaga con su evento del dashboard (o None): por
    wamid; sin wamids, en orden (en las trazas v1 la ráfaga son justo los
    mensajes del cliente que siguen al corte)."""
    after = [e for e in events[case.dashboard_prefix:] if isinstance(e, Mapping) and e.get("role") == "user"]
    if any(_wamid(m.get("wamid")) for m in case.burst):
        by_wamid: dict[str, Mapping[str, Any]] = {}
        for event in after:
            key = _wamid(event.get("wamid"))
            if key:
                by_wamid.setdefault(key, event)
        return [(m, by_wamid.get(_wamid(m.get("wamid")) or "")) for m in case.burst]
    return [(m, after[k] if k < len(after) else None) for k, m in enumerate(case.burst)]


def _text_of(message: Mapping[str, Any], event: Mapping[str, Any] | None) -> str:
    return str((event or {}).get("content") or message.get("text") or "")


def _messages(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any] | None]]) -> list[dict[str, Any]]:
    """Los mensajes del turno como los recibe el motor en producción
    (`_burst_messages` del workflow): el texto que guardó el ingest, sin lo
    que se le agrega al turno del LLM (la campaña citada, el episodio
    anterior), con su hora y su wamid; sin los vacíos."""
    out: list[dict[str, Any]] = []
    for message, event in pairs:
        text = _text_of(message, event)
        if text.strip():
            out.append({"text": text, "ts_ms": message.get("ts_ms"), "wamid": message.get("wamid")})
    return out


def _is_photo(text: str, event: Mapping[str, Any] | None) -> bool:
    if isinstance(event, Mapping) and event.get("image_url"):
        return True
    if _NO_VISION_PHOTO in text:
        return True
    found = detect_chat_event({"role": "user", "content": text})
    return bool(found) and found.get("kind") == "customer_photo"


def _last_bot_component(before: Sequence[Mapping[str, Any]]) -> str | None:
    """La tarjeta, si lo último que el cliente vio del asesor fue una."""
    last = next(
        (e for e in reversed(before) if e.get("role") == "assistant" and str(e.get("content") or "").strip()), None
    )
    if last is None or last.get("kind") != "ui_component":
        return None
    return str(last.get("component_kind") or "")


def turn_categories(case: LabCase, events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Las categorías difíciles del turno, en el orden de `CATEGORY_QUOTAS`."""
    pairs = _burst_pairs(case, events)
    messages = _messages(pairs)
    found = {
        "respuesta_corta": any(len(m["text"].strip()) <= _SHORT_MAX_CHARS for m in messages),
        "rafaga_multiple": len(messages) >= 2,
        "cita": any(isinstance(e, Mapping) and isinstance(e.get("reply_to"), Mapping) and e["reply_to"] for _, e in pairs),
        "foto": any(_is_photo(_text_of(m, e), e) for m, e in pairs),
        "confirmacion": _last_bot_component(events[: case.dashboard_prefix]) in _CONFIRMATION_COMPONENTS,
    }
    return [name for name, _ in CATEGORY_QUOTAS if found[name]]


# ── selección ────────────────────────────────────────────────────────────────


def _rank(seed: str, case_id: str) -> str:
    return hashlib.sha256((seed + case_id).encode("utf-8")).hexdigest()


def _quotas(size: int) -> list[tuple[str, int]]:
    return [(name, round(quota * size / REFERENCE_SIZE)) for name, quota in CATEGORY_QUOTAS]


def select_reference_turns(
    cases: Iterable[LabCase],
    *,
    events_by_session: Mapping[str, Sequence[Mapping[str, Any]]],
    size: int = REFERENCE_SIZE,
    seed: str = REFERENCE_SEED,
) -> list[str]:
    """Los ids de los casos del banco, en el orden del sorteo (así cualquier
    tramo del etiquetado mezcla las categorías). Solo turnos del cliente con
    algún mensaje con texto: sin texto, el motor ni le pregunta a Jev."""
    eligible: list[LabCase] = []
    categories: dict[str, list[str]] = {}
    for case in sorted((c for c in cases if c.trigger == "customer"), key=lambda c: _rank(seed, c.case_id)):
        events = events_by_session.get(case.session_id) or ()
        if case.case_id in categories or not _messages(_burst_pairs(case, events)):
            continue
        eligible.append(case)
        categories[case.case_id] = turn_categories(case, events)
    chosen: list[str] = []
    # Cada categoría con su cuota y, al final, el resto hasta el tamaño.
    for name, quota in [*_quotas(size), (None, size)]:
        taken = set(chosen)
        room = max(0, min(quota, size - len(chosen)))
        chosen += [
            c.case_id
            for c in eligible
            if c.case_id not in taken and (name is None or name in categories[c.case_id])
        ][:room]
    return sorted(chosen, key=lambda cid: _rank(seed, cid))


# ── ítems ────────────────────────────────────────────────────────────────────


def _profile(profile_id: str) -> tuple[EngineProfile, Questionnaire]:
    profile = get_engine_profile(profile_id)
    if profile is None:
        raise KeyError(f"perfil del motor desconocido: {profile_id}")
    return profile, load_questionnaire(profile.questions)


def _metadata_at_start(case: LabCase) -> dict[str, Any]:
    """La metadata del INICIO del turno, armada como la arma el sandbox
    (`metadata_as_of`: los episodios del momento y, si el borrador del banco
    cambió después, el que dejó la traza anterior, con los ítems de un pedido
    de varios productos en `order_draft.items`)."""
    return metadata_as_of({}, case.to_dict(), sales_workspace_path="")


def _speaker(event: Mapping[str, Any]) -> str | None:
    """Las mismas etiquetas de la ventana del motor (`decisions/context.py`)."""
    role = event.get("role")
    if role == "user":
        return "cliente"
    if role == "assistant":
        return "equipo" if event.get("sender") == "human" else "asesor"
    return None


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _conversation(
    before: Sequence[Mapping[str, Any]], messages: Sequence[Mapping[str, Any]], *, redact: Sequence[str]
) -> list[str]:
    """Todo lo anterior al turno (sin cortar, a diferencia de la ventana de
    Jev) y los mensajes de este turno numerados como en el `state`."""
    lines = [
        f"[{speaker}] {_one_line(str(e['content']))}"
        for e in before
        if (speaker := _speaker(e)) and str(e.get("content") or "").strip()
    ]
    lines.append(TURN_MARK)
    lines += [f"[{k}] {_one_line(str(m['text']))}" for k, m in enumerate(messages, 1)]
    return [anonymize_text(line, redact=redact) for line in lines]


def _question_row(q: TypedQuestion) -> dict[str, Any]:
    criteria: Any = dict(q.criteria) if isinstance(q.criteria, Mapping) else list(q.criteria)
    return {
        "id": q.id,
        "kind": q.kind,
        "text": q.text,
        "options": [] if q.kind == "noul" else list(q.options),
        "criteria": criteria,
    }


def build_item(case: LabCase, events: Sequence[Mapping[str, Any]], *, profile_id: str) -> dict[str, Any]:
    """Un turno del banco con el perfil `profile_id` (ver el docstring del
    módulo). `events`: el historial del dashboard de la conversación.

    Como en producción (`decisions/activities.py`): la ventana sale de los
    eventos anteriores a la ráfaga más los de la ráfaga (de ahí sale la cita),
    los hechos y lo que se tapa, de la metadata del inicio del turno, y sin
    asuntos pendientes (los deja la verificación de un turno anterior del
    motor, que el banco no reproduce). Se anonimiza siempre: el perfil del
    oráculo de Jev anonimiza."""
    # Al usarla: el paquete `sales.use_cases` carga el ingest (y con él
    # Temporal) apenas se importa, y el banco se importa sin Temporal.
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import resolve_funnel_stage

    _, questionnaire = _profile(profile_id)
    pairs = _burst_pairs(case, events)
    messages = _messages(pairs)
    before = list(events[: case.dashboard_prefix])
    metadata = _metadata_at_start(case)
    redact = redact_terms_from_slots(draft_slots(active_episode(metadata)))
    context = None
    if questionnaire.uses_context:
        burst_events = [e for _, e in pairs if e is not None]
        window = customer_window([*before, *burst_events], burst_wamids=(), burst_size=len(burst_events))
        stage = resolve_funnel_stage(metadata)
        # Sin `stagnant`: solo lo lee la política (la guía de etapas), nunca
        # llega a Jev; la etapa sí (filtra las preguntas de etapa).
        context = TurnContext(
            window=window,
            facts=order_facts(metadata, stage=stage),
            stage=stage,
            missing=missing_for_stage(metadata, stage),
        )
    state, questions = engine.burst_request(
        questionnaire, PerceiveInput(session_id=case.session_id, profile=profile_id, messages=messages), context=context
    )
    return {
        "turn_key": case.turn_key,
        "case_id": case.case_id,
        "session_id": case.session_id,
        "profile": profile_id,
        "questionnaire": questionnaire.id,
        "categories": turn_categories(case, events),
        "state": anonymize_text(state, redact=redact),
        "questions": [_question_row(q) for q in questions],
        "conversation": _conversation(before, messages, redact=redact),
    }


# ── etiquetas y medición ─────────────────────────────────────────────────────


def validate_answers(item: Mapping[str, Any], answers: Any) -> list[str]:
    """Errores de las respuestas de Claude Code a un ítem (vacío = válidas):
    cada pregunta respondida, sí/no con true o false, las de opción con una
    de sus opciones y ninguna pregunta que no sea del ítem."""
    if not isinstance(answers, Mapping):
        return ["answers tiene que ser un objeto {pregunta: respuesta}"]
    questions = {str(q["id"]): q for q in item.get("questions") or []}
    errors: list[str] = []
    for qid, q in questions.items():
        value = answers.get(qid)
        if value is None:
            errors.append(f"{qid}: sin respuesta")
        elif q.get("kind") == "noul":
            if not isinstance(value, bool):
                errors.append(f"{qid}: va true o false, no {value!r}")
        elif q.get("kind") == "choice":
            options = [str(o) for o in q.get("options") or []]
            if value not in options:
                errors.append(f"{qid}: «{value}» no es una opción ({', '.join(options)})")
        else:
            errors.append(f"{qid}: el banco no etiqueta preguntas de tipo {q.get('kind')}")
    errors += [f"{qid}: no es una pregunta de este turno" for qid in answers if qid not in questions]
    return errors


def _group(qid: str) -> str:
    """El asunto de cada mensaje es una sola pregunta: `msg.<k>.topic` se
    mide junto, como `msg.*.topic`."""
    parts = qid.split(".")
    return "msg.*.topic" if len(parts) == 3 and parts[0] == "msg" and parts[2] == "topic" else qid


def _by_turn(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Una fila por turno: la última (una etiqueta corregida reemplaza a la anterior)."""
    return {str(r["turn_key"]): r for r in rows if isinstance(r, Mapping) and r.get("turn_key")}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ratio(hits: int, total: int) -> float | None:
    """Sin casos no hay proporción (None, no 1,0): un Jev que nunca dice que
    sí no puede verse perfecto."""
    return round(hits / total, 4) if total else None


def _noul_metrics(pairs: list[tuple[float, bool]], detect: float) -> dict[str, Any]:
    tp = sum(1 for p, y in pairs if p >= detect and y)
    fp = sum(1 for p, y in pairs if p >= detect and not y)
    fn = sum(1 for p, y in pairs if p < detect and y)
    positives = tp + fn
    return {
        "kind": "noul",
        "threshold": detect,
        "n": len(pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": len(pairs) - tp - fp - fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, positives),
        "positives": positives,
        "calibration": calibration(pairs),
        "ready": bool(tp + fp) and tp / (tp + fp) >= READY_PRECISION and positives >= READY_MIN_POSITIVES,
    }


def _choice_metrics(rows: list[tuple[str, str, float | None]]) -> dict[str, Any]:
    correct = sum(1 for said, truth, _ in rows if said == truth)
    confidences = [c for _, _, c in rows if c is not None]
    return {
        "kind": "choice",
        "n": len(rows),
        "correct": correct,
        "accuracy": _ratio(correct, len(rows)),
        "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "ready": bool(rows) and correct / len(rows) >= READY_ACCURACY and len(rows) >= READY_MIN_ANSWERS,
    }


def score(
    items: Iterable[Mapping[str, Any]],
    labels: Iterable[Mapping[str, Any]],
    jev: Iterable[Mapping[str, Any]],
    *,
    profile_id: str,
) -> dict[str, dict[str, Any]]:
    """Jev contra las etiquetas, por pregunta (en el orden del cuestionario).

    Cuenta solo los turnos con etiqueta y con respuesta de Jev. Sí/no: Jev dice
    que sí con p ≥ `detect` del perfil; precisión, cobertura (`recall`),
    positivos de la etiqueta y calibración (Brier, ECE). Opción: acierto y
    confianza media. `ready`: la pregunta puede actuar (plan §4)."""
    profile, _ = _profile(profile_id)
    detect = float(profile.thresholds.get("detect", DEFAULT_DETECT))
    truth_by_turn = _by_turn(labels)
    jev_by_turn = _by_turn(jev)
    groups: dict[str, tuple[str, list[Any]]] = {}
    for item in items:
        key = str(item.get("turn_key") or "")
        label, said = truth_by_turn.get(key), jev_by_turn.get(key)
        if label is None or said is None or not said.get("ok"):
            continue
        truth = label.get("answers") or {}
        answers = said.get("answers") or {}
        confidence = said.get("confidence") or {}
        for q in item.get("questions") or []:
            qid, kind = str(q.get("id")), q.get("kind")
            if kind == "noul" and isinstance(truth.get(qid), bool) and _number(answers.get(qid)):
                groups.setdefault(_group(qid), ("noul", []))[1].append((float(answers[qid]), truth[qid]))
            elif kind == "choice" and truth.get(qid) is not None and answers.get(qid) is not None:
                conf = confidence.get(qid)
                row = (str(answers[qid]), str(truth[qid]), float(conf) if _number(conf) else None)
                groups.setdefault(_group(qid), ("choice", []))[1].append(row)
    return {
        qid: _noul_metrics(rows, detect) if kind == "noul" else _choice_metrics(rows)
        for qid, (kind, rows) in groups.items()
    }


# ── Jev ──────────────────────────────────────────────────────────────────────


def _typed(row: Mapping[str, Any]) -> TypedQuestion:
    raw = row.get("criteria")
    criteria = dict(raw) if isinstance(raw, Mapping) else tuple(str(c) for c in raw or ())
    return TypedQuestion(id=str(row["id"]), kind=row["kind"], text=str(row["text"]), criteria=criteria)


async def _ask_one(port: Any, item: Mapping[str, Any], *, timeout_s: float) -> dict[str, Any]:
    key = str(item.get("turn_key") or "")
    try:
        questions = [_typed(q) for q in item.get("questions") or []]
        # El `state` del ítem ya viaja anonimizado: nada más que tapar.
        result = await port.ask(str(item.get("state") or ""), questions, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — un turno que falla no para el banco
        return {"turn_key": key, "ok": False, "model": "", "error": f"unexpected: {exc!r}"[:300], "answers": {}, "confidence": {}}
    answers: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    for a in result.answers if result.ok else ():
        if a.kind == "noul":
            answers[a.id] = a.p
        elif a.kind == "choice":
            answers[a.id] = a.choice
            if a.confidence is not None:
                confidence[a.id] = a.confidence
        else:
            answers[a.id] = a.score
    return {
        "turn_key": key,
        "ok": bool(result.ok),
        "model": result.model,
        "error": result.error,
        "answers": answers,
        "confidence": confidence,
    }


async def ask_bank(
    items: Sequence[Mapping[str, Any]],
    *,
    profile_id: str,
    concurrency: int = BANK_CONCURRENCY,
    timeout_s: float = BANK_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Le pregunta a Jev (el oráculo del perfil) cada ítem con su `state` y
    sus preguntas, en el orden de los ítems: `{turn_key, ok, model, error,
    answers: {pregunta: p | opción}, confidence: {pregunta de opción: c}}`."""
    from src.sdk.connectorkit import get_perception_port  # al llamar: el puerto se elige por perfil

    profile, _ = _profile(profile_id)
    port = get_perception_port(profile.oracle)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(item: Mapping[str, Any]) -> dict[str, Any]:
        async with gate:
            return await _ask_one(port, item, timeout_s=timeout_s)

    return list(await asyncio.gather(*(one(item) for item in items)))
