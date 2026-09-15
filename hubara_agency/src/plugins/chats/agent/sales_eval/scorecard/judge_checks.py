"""Checks de juez del scorecard (HU-SC-3) — un criterio por llamada.

Práctica 2025-2026 (Anthropic "Demystifying evals for AI agents", Hamel Husain,
CheckEval): cada criterio se juzga con una llamada AISLADA, binaria
(`pasa`/`falla`/`no_aplica`/`desconocido`), con evidencia textual y crítica.
Nunca un puntaje 0-10 ni varios criterios en un prompt.

Diferencias con el juez legado que calificó 0.93 al episodio del PR #281:

  * El juez ve la TRAYECTORIA (tools con su resultado, componentes enviados,
    texto suprimido, narración descartada, etapa, señal del cliente), no solo
    el texto.
  * El estado del sistema (etiquetas, escalaciones) NO es prueba: lo pone el
    mismo bot que se evalúa. El prompt lo dice explícitamente.
  * Dos muestras por check; si no coinciden, `desconocido` (a la cola de
    etiquetado humano) en vez de una moneda al aire.
  * Prefiltro de aplicabilidad en código: no se gasta una llamada en un
    criterio cuya situación no ocurrió.

Un check de juez no calibrado nunca reprueba el episodio solo (ver
`verdict.effective_level`).
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    CATALOG_DISPLAY_INTENTS,
    Trajectory,
)

SAMPLES = 2
_CONCURRENCY = 4
_EVIDENCE_MAX = 280

# Límite por minuto del proveedor (Gemini): primer informe 9-15 sep, 71 de 76
# llamadas cayeron en 429 por ráfaga. Se reintenta con espera creciente; un
# error que no es de cuota (clave inválida, prompt rechazado) no se reintenta.
RATE_LIMIT_BACKOFF_S: tuple[float, ...] = (5.0, 15.0, 30.0)
_RATE_LIMIT_RE = re.compile(r"ratelimit|\b429\b|resource_exhausted|exceeded your current quota", re.IGNORECASE)

Sleep = Callable[[float], Awaitable[None]]


def is_rate_limit(exc: BaseException) -> bool:
    return bool(_RATE_LIMIT_RE.search(f"{type(exc).__name__} {exc}"))


class JudgePort(Protocol):
    async def a_generate(self, prompt: str) -> str: ...


# Guía específica por criterio: cómo decidir, en pocas líneas verificables.
JUDGE_PROMPTS: dict[str, str] = {
    "DES-04": (
        "- Identifica qué dijo buscar el cliente: para quién (para sí o regalo), diseño, momento, espacio, aroma.\n"
        "- `falla` si lo que el bot mostró o recomendó no responde a eso (catálogo sin filtrar cuando ya había "
        "intención clara, preguntas que ignoran lo dicho, recomendación que lo contradice).\n"
        "- `pasa` si lo mostrado es coherente con lo pedido o si el cliente pidió ver todo."
    ),
    "DES-06": (
        "- Contrasta CADA producto, precio, aroma, color y conteo de opciones que el bot afirmó en sus textos "
        "enviados contra el CATÁLOGO de abajo.\n"
        "- `falla` si afirmó algo que no está en el catálogo o un conteo que no coincide.\n"
        "- Los componentes (catálogo, fotos, pickers) los arma el sistema con datos reales: juzga solo los textos."
    ),
    "DES-07": (
        "- Aplica si el cliente pidió algo que Hubara no vende (otro tipo de producto, materia prima, servicios).\n"
        "- `pasa` si el bot lo dijo con claridad y ofreció lo que sí hay o escaló por CATALOG_GAP.\n"
        "- `falla` si inventó el producto, fingió tenerlo o presionó con velas sin reconocer lo pedido."
    ),
    "VAR-05": (
        "- `falla` si el bot fijó o dio por elegido un aroma, color o cantidad que el cliente no dijo "
        "(en sus textos o en set_order_slot).\n"
        "- Repetir lo que el cliente sí dijo para confirmarlo es `pasa`."
    ),
    "CON-04": (
        "- Aplica cuando el pedido ya tenía producto y variantes completas.\n"
        "- `pasa` si antes de pedir datos de envío o de enviar el resumen, el bot preguntó explícitamente si el "
        "cliente quiere comprar y el cliente conocía el precio.\n"
        "- `falla` si pasó a datos de envío o al resumen sin ese sí explícito."
    ),
    "ENV-06": (
        "- Formas de pago vigentes: contra entrega (pedidos de productos por más de $45.000), pago anticipado "
        "por transferencia (los datos los manda el sistema) y link de pago (con recargo).\n"
        "- `falla` si el bot inventó una forma de pago, cambió sus condiciones o escribió datos bancarios.\n"
        "- `no_aplica` si el bot no habló de formas de pago."
    ),
    "POS-02": (
        "- Aplica si el cliente pidió cambiar un pedido ya registrado (producto, cantidad, dirección).\n"
        "- `pasa` si el bot escaló a humano; `falla` si prometió o hizo el cambio por su cuenta."
    ),
    "TAG-03": (
        "- Disparadores que exigen escalar a humano: pedido de descuento, mayoreo o compra empresarial, evento "
        "corporativo, pregunta de salud o seguridad, envío internacional, pedido explícito de hablar con una persona.\n"
        "- `no_aplica` si ninguno ocurrió. `pasa` si el bot llamó escalate_to_human con un motivo coherente. "
        "`falla` si ocurrió y no escaló."
    ),
    "TAG-04": (
        "- Aplica si la sesión escaló a humano.\n"
        "- `falla` si la escalación fue por algo que el bot podía resolver con el catálogo y el guion "
        "(una duda de producto, precio o envío normal). Las escalaciones de cierre de pedido son correctas."
    ),
    "TAG-07": (
        "- RECHAZO exige que el cliente haya rechazado la compra de forma explícita.\n"
        "- INTERESADO exige interés real sin confirmación de compra (preguntó, pidió ver, aplazó).\n"
        "- `falla` si la etiqueta no la sostiene la conversación."
    ),
    "EST-04": (
        "- `falla` si algún texto enviado dice o insinúa que el bot es una IA, un sistema, un asistente virtual "
        "o un modelo.\n- Desviar la pregunta con naturalidad es `pasa`."
    ),
    "EST-07": (
        "- `falla` si el bot volvió a preguntar o contradijo un dato que el cliente ya había dado en el episodio "
        "(para quién, producto, aroma, color, cantidad, ciudad, dirección).\n"
        "- Confirmar un dato una vez al resumir el pedido es `pasa`."
    ),
    "EST-08": (
        "- Para cada pregunta directa del cliente, revisa la respuesta del bot en ese turno o el siguiente.\n"
        "- `falla` si una pregunta quedó sin responder mientras el bot empujaba la venta (pidió datos, mandó "
        "formularios o cambió de tema)."
    ),
}


_ADVANCED_STAGES = ("confirmacion", "datos_envio", "cierre", "postcierre")
_PAYMENT_RE = re.compile(r"\b(pago|pagar|contra ?entrega|nequi|transferencia|link)\b", re.IGNORECASE)


def _any_sent(t: Trajectory) -> bool:
    return any(turn.sent_texts for turn in t.turns)


def _escalated(t: Trajectory) -> bool:
    return any(
        turn.tool_ok("escalate_to_human")
        or turn.state.get("route") == "humano"
        or (t.fidelity != "trace" and turn.tool_attempted("escalate_to_human"))
        for turn in t.turns
    )


# Prefiltro: (aplica?, motivo si no). Código barato antes de gastar llamadas.
_APPLIES: dict[str, Callable[[Trajectory, CheckContext], tuple[bool, str]]] = {
    "DES-04": lambda t, c: (
        any(set(x.intents) & CATALOG_DISPLAY_INTENTS or x.tool_attempted("search_products") for x in t.turns),
        "el bot no mostró ni buscó productos",
    ),
    "DES-06": lambda t, c: (_any_sent(t), "el bot no envió texto"),
    "DES-07": lambda t, c: (any(x.is_customer for x in t.turns), "sin mensajes del cliente"),
    "VAR-05": lambda t, c: (
        any((x.stage_out or "") not in ("", "descubrimiento") or x.tool_attempted("set_order_slot") for x in t.turns),
        "el episodio no llegó a variantes",
    ),
    "CON-04": lambda t, c: (
        any((x.stage_out or "") in _ADVANCED_STAGES or {"shipping_flow", "order_confirmation"} & set(x.intents)
            for x in t.turns),
        "el pedido no llegó a variantes completas",
    ),
    "ENV-06": lambda t, c: (
        any(_PAYMENT_RE.search(s) for x in t.turns for s in x.sent_texts), "el bot no habló de formas de pago",
    ),
    "POS-02": lambda t, c: (
        bool(t.order_id) or any(x.stage_in == "postcierre" or x.state.get("order_id") for x in t.turns),
        "sin pedido registrado",
    ),
    "TAG-03": lambda t, c: (any(x.is_customer or x.trigger == "handoff" for x in t.turns), "sin mensajes del cliente"),
    "TAG-04": lambda t, c: (_escalated(t), "la sesión no escaló"),
    "TAG-07": lambda t, c: (
        t.closing_tag == "RECHAZO"
        or (t.last is not None and t.last.state.get("tag") == "INTERESADO")
        or t.closing_tag == "INTERESADO",
        "el episodio no quedó en RECHAZO ni INTERESADO",
    ),
    "EST-04": lambda t, c: (_any_sent(t), "el bot no envió texto"),
    "EST-07": lambda t, c: (len(t.turns) > 1, "episodio de un turno"),
    "EST-08": lambda t, c: (
        any("?" in x.inbound_text for x in t.turns if x.is_customer or x.trigger == "handoff"),
        "el cliente no hizo preguntas",
    ),
}

_SIGNAL_LABEL = {"deferral": "aplazamiento", "affirmation": "afirmación de compra"}


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_transcript(traj: Trajectory, *, text_limit: int = 400) -> str:
    """La trayectoria como texto numerado por turno (lo que ve el juez)."""
    lines: list[str] = []
    for t in traj.turns:
        p = f"T{t.turn} ·"
        who = {"ghost": "sistema (ghosting)", "handoff": "handoff (otro agente → ventas)"}.get(t.trigger, "cliente")
        if t.inbound_text:
            lines.append(f"{p} {who}: {_clip(t.inbound_text, text_limit)}")
        if t.signal:
            lines.append(f"{p} señal del cliente: {_SIGNAL_LABEL.get(t.signal, t.signal)}")
        for s in t.sent_texts:
            lines.append(f'{p} bot envió: "{_clip(s, text_limit)}"')
        if t.suppressed_reason and t.llm_text:
            lines.append(f'{p} texto suprimido ({t.suppressed_reason}), el cliente NO lo vio: "{_clip(t.llm_text, text_limit)}"')
        for n in t.discarded_narration:
            lines.append(f'{p} narración descartada: "{_clip(n, 200)}"')
        if t.intents:
            lines.append(f"{p} componentes enviados: {', '.join(t.intents)}")
        if t.tools:
            parts = []
            for c in t.tools:
                status = "ok" if c.ok is True else f"RECHAZADA: {c.error}" if c.ok is False else "?"
                args = ", ".join(f"{k}={v}" for k, v in c.args.items() if k in ("tag", "reason_category", "q", "producto", "aroma", "color", "cantidad"))
                lines_args = f" [{args}]" if args else ""
                parts.append(f"{c.name}({status}){lines_args}")
            lines.append(f"{p} tools: " + " · ".join(parts))
        if t.guards:
            lines.append(f"{p} guardas que actuaron: {', '.join(t.guards)}")
        if t.stage_in or t.stage_out:
            lines.append(f"{p} etapa: {t.stage_in or '?'} → {t.stage_out or '?'}")
        tag = t.state.get("tag") if isinstance(t.state, dict) else None
        if tag:
            route = t.state.get("route")
            lines.append(f"{p} estado: {tag}" + (f" (ruta {route})" if route else ""))
    return "\n".join(lines)


_TEMPLATE = """Eres auditor de calidad del asesor de ventas por WhatsApp de Hubara (velas artesanales hechas en Colombia).
Evalúas UN solo criterio sobre la conversación de abajo.

CRITERIO {id} — {name}
Aplica cuando: {applies}
Regla: {rule}
Cómo decidir:
{guidance}

Reglas de evaluación:
- Juzga SOLO este criterio; ignora otros problemas.
- `no_aplica` si la situación del criterio no ocurre en la conversación.
- `desconocido` si la conversación no alcanza para decidir.
- Las etiquetas y escalaciones del estado las pone el mismo bot que evalúas: el estado del sistema no es prueba de que actuó bien.
- Lo que el cliente vio son los textos enviados y los componentes; el texto suprimido y la narración descartada no le llegaron.
- La evidencia es una cita textual corta (máximo 25 palabras) y el turno es el número T donde ocurre.
{catalog}
CONVERSACIÓN (episodio {episode_id}, {n} turnos, fidelidad {fidelity}):
{transcript}

Devuelve SOLO un JSON válido:
{{"veredicto": "pasa|falla|no_aplica|desconocido", "turno": <número o null>, "evidencia": "...", "critica": "..."}}
"""


def build_prompt(check_id: str, traj: Trajectory, ctx: CheckContext) -> str:
    spec = SPECS_BY_ID[check_id]
    catalog = ""
    if check_id == "DES-06" and ctx.catalog_summary:
        catalog = "\nCATÁLOGO REAL VIGENTE (lista cerrada):\n" + ctx.catalog_summary + "\n"
    return _TEMPLATE.format(
        id=spec.id,
        name=spec.name,
        applies=spec.applies,
        rule=spec.rule,
        guidance=JUDGE_PROMPTS[check_id],
        catalog=catalog,
        episode_id=traj.episode_id or "?",
        n=len(traj.turns),
        fidelity=traj.fidelity,
        transcript=render_transcript(traj),
    )


_VERDICT_ALIASES = {
    "pasa": "pasa", "pass": "pasa",
    "falla": "falla", "fail": "falla",
    "no_aplica": "no_aplica", "no aplica": "no_aplica", "n/a": "no_aplica",
    "desconocido": "desconocido", "unknown": "desconocido",
}


def parse_judge_output(check_id: str, raw: str) -> CheckResult | None:
    """JSON del juez → CheckResult. `None` si no se pudo interpretar."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data: Any = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = _VERDICT_ALIASES.get(str(data.get("veredicto", "")).strip().lower())
    if verdict is None:
        return None
    turn = data.get("turno")
    return CheckResult(
        check_id,
        verdict,
        turn=int(turn) if isinstance(turn, (int, float)) else None,
        evidence=_clip(str(data.get("evidencia") or ""), _EVIDENCE_MAX),
        critique=_clip(str(data.get("critica") or ""), _EVIDENCE_MAX),
        source="judge",
    )


JUDGE_ERROR_PREFIX = "error del juez"


def is_judge_error(result: CheckResult) -> bool:
    return result.source == "judge" and result.critique.startswith(JUDGE_ERROR_PREFIX)


async def _generate(judge: JudgePort, prompt: str, sleep: Sleep) -> str:
    """Una llamada al juez con reintentos solo ante límite de cuota."""
    for wait in (*RATE_LIMIT_BACKOFF_S, None):
        try:
            return await judge.a_generate(prompt)
        except Exception as exc:  # noqa: BLE001 — se clasifica abajo
            if wait is None or not is_rate_limit(exc):
                raise
            await sleep(wait)
    raise RuntimeError("inalcanzable")  # pragma: no cover


async def _judge_one(
    check_id: str, traj: Trajectory, ctx: CheckContext, judge: JudgePort, samples: int, sleep: Sleep
) -> CheckResult:
    applies, reason = _APPLIES[check_id](traj, ctx)
    if not applies:
        return CheckResult(check_id, "no_aplica", evidence=reason, source="judge")
    if check_id == "DES-06" and not ctx.catalog_available:
        return CheckResult(check_id, "desconocido", evidence="catálogo no disponible", source="judge")
    prompt = build_prompt(check_id, traj, ctx)
    parsed: list[CheckResult] = []
    for _ in range(max(1, samples)):
        try:
            raw = await _generate(judge, prompt, sleep)
        except Exception as exc:  # noqa: BLE001 — el juez caído no tumba el scorecard
            return CheckResult(
                check_id, "desconocido", critique=f"{JUDGE_ERROR_PREFIX}: {exc!r}"[:200], source="judge"
            )
        result = parse_judge_output(check_id, raw)
        if result is None:
            return CheckResult(check_id, "desconocido", critique="respuesta del juez ilegible", source="judge")
        parsed.append(result)
    verdicts = {r.verdict for r in parsed}
    if len(verdicts) > 1:
        return CheckResult(
            check_id,
            "desconocido",
            critique="juez inconsistente entre muestras: " + " vs ".join(r.verdict for r in parsed),
            source="judge",
        )
    return parsed[0]


async def run_judge_checks(
    traj: Trajectory,
    ctx: CheckContext,
    judge: JudgePort,
    *,
    samples: int = SAMPLES,
    only: Iterable[str] | None = None,
    sleep: Sleep = asyncio.sleep,
) -> list[CheckResult]:
    """Todos los checks de juez (o `only`), en orden de registro."""
    wanted = set(only) if only is not None else None
    ids = [c.id for c in CHECKS if c.kind == "judge" and (wanted is None or c.id in wanted)]
    if not traj.turns:
        return [CheckResult(i, "no_aplica", evidence="episodio sin turnos", source="judge") for i in ids]
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def bounded(check_id: str) -> CheckResult:
        async with semaphore:
            return await _judge_one(check_id, traj, ctx, judge, samples, sleep)

    return list(await asyncio.gather(*(bounded(i) for i in ids)))
