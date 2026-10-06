"""Sonda diaria de Jev: una de las redes de seguridad del motor de decisiones.

Diseño v2: «Una sonda diaria. 20 ráfagas sintéticas con respuesta conocida,
sin datos de clientes. Detecta si la API alpha cambia de forma o si Jev
empieza a responder distinto.» Jev es el único clasificador del motor (el
rival OpenAI se quitó), así que nadie más avisa si cambia.

* Cada caso es una ráfaga INVENTADA (registro colombiano, sin teléfonos ni
  nombres) para un perfil del motor: `jev-v1` (solo asuntos) o `jev-v2` (con
  lo que el cliente vio antes del turno, `TurnContext`). El `state` y las
  preguntas los arma `engine.burst_request`, el mismo código del turno: la
  sonda le pregunta a Jev exactamente lo que le pregunta el turno.
* `expect` fija solo las respuestas SIN ambigüedad (`{pregunta: bool |
  opción}`): un `noul` cuenta como sí desde p ≥ 0,5; un `choice`, por la
  opción elegida.
* El reporte: `ok_rate` (respuestas válidas con todas las preguntas
  respondidas: la forma de la API), `pass_rate` (respuestas conocidas
  acertadas, sobre los casos con respuesta válida: lo que Jev entiende),
  `models` (snapshots servidos), `p95_ms` y el detalle en `shape_errors` y
  `failures`.
* El estado (`probe_status`): `down` con `ok_rate` < 0,9; `degraded` con
  `pass_rate` < 0,85 o si el snapshot servido cambió desde la sonda anterior;
  si no, `ok`. Sin llave de OpenRouter (o con el placeholder de Terraform) no
  llama a nadie: `sin_llave`.
* Se guarda en `<vault>/_decisions/probe/<día de Bogotá>.json` y en
  `latest.json` (un directorio `_*`: los recorredores de sesiones lo
  ignoran). El control «Bot nuevo» (`rollout.py`) exige la última sonda `ok`
  y de 48 h o menos para subir a canary o encendido.

Sin Temporal: la corre la activity `run_decisions_probe` del worker
`sales_eval` (Schedule diario, 07:00 Bogotá). Leer y escribir el reporte
recibe el `vault_dir` explícito.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions import engine
from src.plugins.chats.agent.sales.decisions.context import BOT_ASKED_BY_COMPONENT, TurnContext, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.agent.sales.decisions.profiles import EngineProfile, get_engine_profile
from src.plugins.chats.agent.sales.decisions.turn import turn_of
from src.sdk.decisionkit import BundleError

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"
STATUS_NO_KEY = "sin_llave"
#: Por debajo, la API cambió de forma o no responde.
MIN_OK_RATE = 0.9
#: Por debajo, Jev responde distinto de lo conocido.
MIN_PASS_RATE = 0.85
#: La sesión de la sonda: no es un cliente (no se lee ni se escribe su vault).
PROBE_SESSION_ID = "sonda"
#: Un `noul` cuenta como «sí» desde esta probabilidad.
YES_FROM_P = 0.5

# Bogotá no tiene horario de verano: UTC-5 fijo, sin depender de tzdata.
_BOGOTA = timezone(timedelta(hours=-5))
_MAX_MISSING_LISTED = 5


@dataclass(frozen=True)
class ProbeCase:
    """Una ráfaga inventada y sus respuestas conocidas."""

    id: str
    profile: str
    messages: tuple[Mapping[str, Any], ...]  # [{text, ts_ms, wamid}]
    expect: Mapping[str, bool | str]
    context: TurnContext | None = None


def _case(
    case_id: str, profile: str, texts: Sequence[str], expect: Mapping[str, bool | str], context: TurnContext | None = None
) -> ProbeCase:
    """Los mensajes del cliente llegan cada 8 s, con su `wamid` inventado."""
    messages = tuple(
        {"text": text, "ts_ms": 1_000 + 8_000 * k, "wamid": f"sonda-{case_id}-{k + 1}"} for k, text in enumerate(texts)
    )
    return ProbeCase(id=case_id, profile=profile, messages=messages, expect=dict(expect), context=context)


def _seen(*lines: str, facts: Sequence[str] = (), component: str | None = None) -> TurnContext:
    """Lo que el cliente vio antes del turno, como lo arma `customer_window`:
    si lo último fue la tarjeta de confirmación o el formulario, el código ya
    sabe qué se le preguntó."""
    return TurnContext(
        window=Window(
            lines=tuple(lines),
            last_component=component,
            bot_asked_known=BOT_ASKED_BY_COMPONENT.get(component or ""),
        ),
        facts=tuple(facts),
    )


PROBE_CASES: tuple[ProbeCase, ...] = (
    # ── jev-v1: solo los asuntos de la ráfaga ───────────────────────────────
    _case("v1-catalogo", "jev-v1", ["¿me mandas el catálogo porfa?"],
          {"topic.catalogo": True, "topic.queja": False}),
    _case("v1-precio", "jev-v1", ["¿Cuánto cuesta la vela de lavanda grande?"],
          {"topic.precio": True, "topic.queja": False}),
    _case("v1-envio", "jev-v1", ["¿cuánto vale el envío a Medellín?"],
          {"topic.envio": True, "topic.saludo": False}),
    _case("v1-saludo", "jev-v1", ["Buenas tardes"],
          {"topic.saludo": True, "topic.catalogo": False, "topic.queja": False}),
    _case("v1-queja", "jev-v1", ["Me llegó la vela partida y la caja toda aplastada, qué mal"],
          {"topic.queja": True, "topic.saludo": False}),
    _case("v1-aplaza", "jev-v1", ["Ahorita no puedo, te escribo mañana"],
          {"topic.aplaza": True, "topic.queja": False}),
    _case("v1-pagos", "jev-v1", ["¿Reciben Nequi o solo transferencia?"],
          {"topic.pagos": True, "topic.envio": False}),
    _case("v1-tiempos", "jev-v1", ["¿En cuántos días me llega si lo pido hoy?"],
          {"topic.tiempos": True, "topic.queja": False}),
    _case("v1-medidas", "jev-v1", ["¿Qué medidas tiene el portavelas mediano?"],
          {"topic.medidas": True, "topic.saludo": False}),
    _case("v1-estado-pedido", "jev-v1", ["¿Mi pedido ya salió? Lo hice el lunes"],
          {"topic.estado_pedido": True, "topic.catalogo": False}),
    _case("v1-saludo-y-precios", "jev-v1", ["Buenos días", "¿me pasas los precios de las velas aromáticas?"],
          {"topic.saludo": True, "topic.precio": True, "topic.queja": False}),
    # ── jev-v2: con lo que el cliente vio antes del turno ───────────────────
    _case("v2-si-direccion", "jev-v2", ["Si"],
          {"thread.bot_asked": "confirmar_dato_envio", "thread.answers_bot": True, "thread.answer": "si",
           "topic.queja": False},
          _seen("[cliente] Quiero la vela de canela mediana",
                "[asesor] Listo, ya te la separo. ¿Te la enviamos a la misma dirección de la vez pasada?",
                facts=("Etapa: datos de envío", "Ítem 1: Vela aromática, canela, 1 unidad", "Ciudad: Medellín",
                       "Dirección: dada · Teléfono: dado · Quien recibe: falta · Método de pago: falta",
                       "Compra confirmada: no"))),
    _case("v2-ok-aromas", "jev-v2", ["Ok"],
          {"thread.bot_asked": "ver_opciones", "topic.queja": False},
          _seen("[cliente] Hola, busco una vela para regalar",
                "[asesor] ¡Claro que sí! ¿Quieres que te muestre los aromas que tenemos?",
                facts=("Etapa: descubrimiento",))),
    _case("v2-si-tarjeta", "jev-v2", ["Sí"],
          {"thread.answers_bot": True, "thread.answer": "si", "topic.queja": False},
          _seen("[cliente] Sí, esa me gusta",
                "[asesor] 🧾 El bot envió el resumen del pedido con botones para confirmar",
                facts=("Etapa: cierre", "Ítem 1: Portavelas, dorado, 1 unidad", "Ciudad: Bogotá",
                       "Dirección: dada · Teléfono: dado · Quien recibe: dado · Método de pago: dado",
                       "Compra confirmada: no"),
                component="order_confirmation")),
    _case("v2-no-roja", "jev-v2", ["no, mejor la roja"],
          {"thread.answer": "no", "topic.queja": False},
          _seen("[cliente] Me gusta la vela en vaso", "[asesor] La tenemos en azul y en rojo. ¿Te separo la azul?",
                facts=("Etapa: variantes", "Ítem 1: Vela en vaso, 1 unidad"))),
    _case("v2-aplaza", "jev-v2", ["Déjame lo pienso y te escribo mañana"],
          {"topic.aplaza": True, "topic.queja": False},
          _seen("[asesor] Tu pedido quedaría en $85.000 con el envío. ¿Te confirmo el pedido para despacharlo hoy?",
                facts=("Etapa: cierre", "Ítem 1: Vela aromática, vainilla, 2 unidades", "Ciudad: Cali"))),
    _case("v2-queja-entregado", "jev-v2", ["La vela me llegó rota", "y el aroma no es el que pedí"],
          {"topic.queja": True, "topic.saludo": False},
          _seen("[asesor] ¡Tu pedido ya fue entregado! Gracias por tu compra",
                facts=("Etapa: postcierre", "Compra confirmada: sí"))),
    # El catálogo está en el contexto, no en este turno: v2 no lo cuenta.
    _case("v2-envio-este-turno", "jev-v2", ["Gracias. ¿Cuánto vale el envío a Cali?"],
          {"topic.envio": True, "topic.catalogo": False},
          _seen("[cliente] ¿me mandas el catálogo?", "[asesor] 🛍️ El bot envió el catálogo con 24 productos",
                facts=("Etapa: descubrimiento",))),
    _case("v2-saludo-regreso", "jev-v2", ["Buenas tardes"],
          {"topic.saludo": True, "topic.queja": False},
          _seen("[cliente] Gracias, lo voy a pensar", "[asesor] Con gusto, aquí estoy cuando quieras.",
                facts=("Etapa: descubrimiento",))),
    _case("v2-pagos", "jev-v2", ["¿Puedo pagar contra entrega?"],
          {"topic.pagos": True, "topic.queja": False},
          _seen("[asesor] Perfecto, ya tengo tu dirección. ¿Cómo prefieres pagar?",
                facts=("Etapa: datos de envío", "Ítem 1: Portavelas, negro, 1 unidad", "Ciudad: Barranquilla",
                       "Dirección: dada · Teléfono: dado · Quien recibe: dado · Método de pago: falta",
                       "Compra confirmada: no"))),
)


# ── la corrida ───────────────────────────────────────────────────────────────


def api_key_present() -> bool:
    """¿Hay una llave real de OpenRouter? El placeholder de Terraform no es una
    llave (con él, el adaptador tampoco llama: todo cae con `no_api_key`)."""
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return bool(key) and not key.upper().startswith("PLACEHOLDER")


def _request(case: ProbeCase) -> tuple[EngineProfile, str, list[Any]] | None:
    try:
        profile = get_engine_profile(case.profile)
        if profile is None:
            return None
        questionnaire = turn_of(profile).questionnaire
    except (KeyError, BundleError):
        return None
    inp = PerceiveInput(session_id=PROBE_SESSION_ID, profile=case.profile, messages=[dict(m) for m in case.messages])
    state, questions = engine.burst_request(questionnaire, inp, case.context)
    return profile, state, questions


def build_request(case: ProbeCase) -> tuple[str, list[Any]] | None:
    """El `state` y las preguntas del caso, como los arma el turno. `None` si
    su perfil (o su cuestionario) ya no existe."""
    request = _request(case)
    return None if request is None else (request[1], request[2])


def _is_prob(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _answered(question: Any, answer: Any) -> bool:
    if answer is None or answer.kind != question.kind:
        return False
    if question.kind == "noul":
        return _is_prob(answer.p)
    if question.kind == "choice":
        return answer.choice in question.options
    return answer.score is not None


def _shape_error(result: Any, questions: Sequence[Any]) -> str | None:
    """Por qué la respuesta no sirve, o `None` si tiene la forma del contrato."""
    if not result.ok:
        return str(result.error or "error")
    missing = [q.id for q in questions if not _answered(q, result.answer(q.id))]
    if not missing:
        return None
    more = " …" if len(missing) > _MAX_MISSING_LISTED else ""
    return "sin respuesta: " + ", ".join(missing[:_MAX_MISSING_LISTED]) + more


def _check(answer: Any, expected: bool | str) -> tuple[bool, Any]:
    """(¿acierta?, lo que respondió Jev: la probabilidad de un `noul` o la
    opción de un `choice`)."""
    if isinstance(expected, bool):
        p = getattr(answer, "p", None)
        if not _is_prob(p):
            return False, None
        return (p >= YES_FROM_P) == expected, round(float(p), 3)
    choice = getattr(answer, "choice", None)
    return choice == expected, choice


def _p95(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def _report(at_ms: int, *, status: str, **over: Any) -> dict[str, Any]:
    return {
        "at_ms": at_ms,
        "status": status,
        "models": [],
        "cases": 0,
        "ok_rate": None,
        "pass_rate": None,
        "p95_ms": None,
        "shape_errors": [],
        "failures": [],
    } | over


async def run_probe(cases: Sequence[ProbeCase] = PROBE_CASES, *, now_ms: int | None = None) -> dict[str, Any]:
    """Le hace a Jev las preguntas de cada caso, una por una (como el turno,
    y sin ráfagas que la API en alpha limite), y arma el reporte. Nunca
    lanza: un caso que falla cuenta como error de forma."""
    at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not api_key_present():
        return _report(at_ms, status=STATUS_NO_KEY)
    from src.sdk.connectorkit import get_perception_port, oracle_timeout_s

    ok = checked = passed = 0
    models: set[str] = set()
    latencies: list[int] = []
    shape_errors: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for case in cases:
        request = _request(case)
        if request is None:
            shape_errors.append({"case": case.id, "error": engine.ERROR_UNKNOWN_PROFILE})
            continue
        profile, state, questions = request
        try:
            port = get_perception_port(profile.oracle)
            result = await port.ask(state, questions, timeout_s=oracle_timeout_s(profile.oracle), redact=())
        except Exception as exc:  # noqa: BLE001 — el puerto nunca lanza; si lanza, la sonda lo reporta
            shape_errors.append({"case": case.id, "error": f"unexpected: {exc!r}"[:200]})
            continue
        if isinstance(result.latency_ms, int) and result.latency_ms > 0:
            latencies.append(result.latency_ms)
        error = _shape_error(result, questions)
        if error is not None:
            shape_errors.append({"case": case.id, "error": error})
            continue
        ok += 1
        if result.model:
            models.add(str(result.model))
        for question_id, expected in case.expect.items():
            hit, got = _check(result.answer(question_id), expected)
            checked += 1
            if hit:
                passed += 1
            else:
                failures.append({"case": case.id, "question": question_id, "expected": expected, "got": got})
    report = _report(
        at_ms,
        status="",
        models=sorted(models),
        cases=len(cases),
        ok_rate=round(ok / len(cases), 4) if cases else None,
        pass_rate=round(passed / checked, 4) if checked else None,
        p95_ms=_p95(latencies),
        shape_errors=shape_errors,
        failures=failures,
    )
    report["status"] = probe_status(report)
    return report


# ── el estado ────────────────────────────────────────────────────────────────


def _rate(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _models(report: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(report, Mapping):
        return set()
    return {str(m) for m in report.get("models") or [] if m}


def probe_status(report: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> str:
    """`down` si la API no responde con la forma del contrato; `degraded` si
    Jev responde distinto de lo conocido o lo sirve otro snapshot que en la
    sonda anterior; `ok` si no. `sin_llave` si la sonda no llamó a nadie."""
    if report.get("status") == STATUS_NO_KEY:
        return STATUS_NO_KEY
    ok_rate = _rate(report.get("ok_rate"))
    if ok_rate is None or ok_rate < MIN_OK_RATE:
        return STATUS_DOWN
    pass_rate = _rate(report.get("pass_rate"))
    if pass_rate is None or pass_rate < MIN_PASS_RATE:
        return STATUS_DEGRADED
    before, now = _models(previous), _models(report)
    if before and now and before != now:
        return STATUS_DEGRADED
    return STATUS_OK


# ── el reporte en el vault ───────────────────────────────────────────────────


def probe_dir(vault_dir: Path) -> Path:
    return Path(vault_dir) / "_decisions" / "probe"


def _bogota_day(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, _BOGOTA).date().isoformat()


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Escribe y renombra: quien lee nunca ve un archivo a medio escribir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(data), handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_report(vault_dir: Path, report: Mapping[str, Any]) -> Path:
    """Guarda el reporte con el día de Bogotá de la corrida y lo deja como
    `latest.json`. Devuelve el archivo del día."""
    folder = probe_dir(vault_dir)
    at_ms = report.get("at_ms")
    day = folder / f"{_bogota_day(at_ms if isinstance(at_ms, int) else 0)}.json"
    _write_json(day, report)
    _write_json(folder / "latest.json", report)
    return day


def read_latest(vault_dir: Path) -> dict[str, Any] | None:
    """El último reporte, o `None` si no hay o no se puede leer. Nunca lanza."""
    try:
        raw = json.loads((probe_dir(vault_dir) / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
