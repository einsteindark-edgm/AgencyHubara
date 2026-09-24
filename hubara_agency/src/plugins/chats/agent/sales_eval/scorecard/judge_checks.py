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
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    CATALOG_DISPLAY_INTENTS,
    Trajectory,
    Turn,
    focus_trajectory,
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
        "por Nequi o llave del negocio, y link de pago (con recargo).\n"
        "- La llave Nequi del negocio{nequi} es un dato PÚBLICO: escribirla está permitido por el guion.\n"
        "- `falla` si el bot inventó una forma de pago, cambió sus condiciones o escribió datos bancarios "
        "(banco, número de cuenta, titular) u otra llave distinta.\n"
        "- `no_aplica` si el bot no habló de formas de pago."
    ),
    "POS-02": (
        "- Aplica si el cliente pidió cambiar un pedido ya registrado (producto, cantidad, dirección).\n"
        "- `pasa` si el bot escaló a humano; `falla` si prometió o hizo el cambio por su cuenta."
    ),
    "TAG-03": (
        "- Disparadores que exigen escalar a humano: pedido de descuento SIN cupón válido (un código que "
        "`apply_coupon` aceptó se aplica sin escalar), mayoreo o compra empresarial, evento "
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
        "o un modelo.\n"
        "- `falla` también si al pasar el caso delata el relevo oponiendo persona y sistema: \"un humano\", "
        "\"equipo humano\", \"asesor humano\", \"una persona real\", o un reporte de estado como \"la conversación "
        "quedó en manos de…\" (run 5ed9af2d). Lo correcto es nombrar a \"un colega\" o \"un compañero del "
        "equipo\" que responde en el mismo chat.\n"
        "- Desviar la pregunta con naturalidad es `pasa`."
    ),
    "EST-07": (
        "- `falla` si el bot volvió a preguntar o contradijo un dato que el cliente ya había dado en el episodio "
        "(para quién, producto, aroma, color, cantidad, ciudad, dirección).\n"
        "- Confirmar un dato una vez al resumir el pedido es `pasa`."
    ),
    "EST-08": (
        "- Saca TÚ los asuntos que planteó el cliente en cada turno: preguntas (con o sin \"?\"), pedidos "
        "(\"me mandas el catálogo\", \"quiero ver los precios\") y datos que piden una reacción. En una "
        "ráfaga cada mensaje [k] puede traer uno o varios asuntos. Un saludo, un \"gracias\" o un \"ok\" no "
        "son asuntos.\n"
        "- Un asunto está cubierto si lo atiende el texto que el bot envió o un componente que el cliente "
        "recibió (catálogo, fotos, tarifas, formulario), en ese turno o en el siguiente.\n"
        "- `falla` si algún asunto quedó sin atender, sobre todo si el bot empujó la venta en su lugar (pidió "
        "datos, mandó formularios o cambió de tema). `pasa` si todos quedaron atendidos.\n"
        "- Lista TODOS los asuntos en `asuntos`, cubiertos o no, con el turno T y el número de mensaje [k]."
    ),
}

# Forma de la respuesta del juez. EST-08 v2 agrega la cobertura por asunto.
_OUTPUT_DEFAULT = '{"veredicto": "pasa|falla|no_aplica|desconocido", "turno": <número o null>, "evidencia": "...", "critica": "..."}'
_OUTPUT_BY_CHECK: dict[str, str] = {
    "EST-08": (
        '{"veredicto": "pasa|falla|no_aplica|desconocido", "turno": <número o null>, "evidencia": "...", '
        '"critica": "...", "asuntos": [{"asunto": "...", "turno": <T>, "mensaje": <k o null>, '
        '"cubierto": true|false, "evidencia": "..."}]}'
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
    # v2: sin exigir "?" — una ráfaga de pedidos ("me mandas el catálogo") no lo trae.
    "EST-08": lambda t, c: (
        any(x.inbound_text.strip() for x in t.turns if x.is_customer or x.trigger == "handoff"),
        "el cliente no escribió",
    ),
}

_SIGNAL_LABEL = {"deferral": "aplazamiento", "affirmation": "afirmación de compra"}


def _clip(text: str, limit: int | None) -> str:
    text = " ".join((text or "").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]


def _quoted(prefix: str, head: str, text: str) -> list[str]:
    """Texto entre comillas que conserva sus saltos de línea (listas, párrafos)."""
    parts = _lines(text) or [""]
    if len(parts) == 1:
        return [f'{prefix} {head}: "{parts[0]}"']
    return [f'{prefix} {head}: "{parts[0]}', *(f"{prefix}   {x}" for x in parts[1:-1]), f'{prefix}   {parts[-1]}"']


def _since(ms: int) -> str:
    seconds = max(0, round(ms / 1000))
    return f"+{seconds} s" if seconds < 90 else f"+{round(seconds / 60)} min"


def _customer_lines(prefix: str, turn: Any) -> list[str]:
    """Lo que escribió el cliente, un renglón por mensaje de la ráfaga.

    Con la traza v2 cada mensaje trae su hora; en v1 y en episodios legados
    los mensajes llegan unidos por saltos de línea (`coalesce_inbox`)."""
    if turn.inbound:
        texts = [_clip(m.text, None) for m in turn.inbound if m.text.strip()]
        stamps = [m.ts_ms for m in turn.inbound if m.text.strip()]
    else:
        texts, stamps = _lines(turn.inbound_text), []
    if len(texts) <= 1:
        return [f"{prefix} cliente: {texts[0] if texts else ''}"]
    first = stamps[0] if stamps else None
    out = [f"{prefix} cliente escribió {len(texts)} mensajes:"]
    for k, text in enumerate(texts, 1):
        ts = stamps[k - 1] if k - 1 < len(stamps) else None
        delta = f"({_since(ts - first)}) " if k > 1 and ts is not None and first is not None else ""
        out.append(f"{prefix}   [{k}] {delta}{text}")
    return out


def _turn_lines(t: Any, text_limit: int | None) -> list[str]:
    """Los renglones de un turno en el transcript del juez."""
    lines: list[str] = []
    p = f"T{t.turn} ·"
    if t.inbound_text or t.inbound:
        if t.is_customer:
            lines.extend(_customer_lines(p, t))
        else:
            who = {"ghost": "sistema (ghosting)", "handoff": "handoff (otro agente → ventas)"}.get(t.trigger, t.trigger)
            lines.append(f"{p} {who}: {_clip(t.inbound_text, text_limit or 600)}")
    if t.signal:
        lines.append(f"{p} señal del cliente: {_SIGNAL_LABEL.get(t.signal, t.signal)}")
    for s in t.sent_texts:
        lines.extend(_quoted(p, "bot envió", s if text_limit is None else _clip(s, text_limit)))
    if t.suppressed_reason and t.llm_text:
        lines.extend(_quoted(p, f"texto suprimido ({t.suppressed_reason}), el cliente NO lo vio", t.llm_text))
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
    return lines


CANDIDATE_MARK = "★ CANDIDATA (a juzgar)"


def render_transcript(
    traj: Trajectory,
    *,
    text_limit: int | None = None,
    candidates: Mapping[int, Turn] | None = None,
) -> str:
    """La trayectoria como texto numerado por turno (lo que ve el juez).

    v2: sin tope por texto (la traza ya acota a 600) y con los saltos de
    línea: cada mensaje de una ráfaga en su renglón, con su hora si la hay.

    Modo turno (`candidates`): los turnos reales son contexto y cada candidato
    reemplaza al turno real de su número, bajo `T{k} ★ CANDIDATA (a juzgar)`.
    Nada después de la última candidata."""
    if not candidates:
        lines: list[str] = []
        for t in traj.turns:
            lines.extend(_turn_lines(t, text_limit))
        return "\n".join(lines)
    last = max(candidates)
    real = {t.turn: t for t in traj.turns if t.turn <= last}
    out: list[str] = []
    for k in sorted(set(real) | set(candidates)):
        if k in candidates:
            out.append(f"T{k} {CANDIDATE_MARK}")
            out.extend(_turn_lines(candidates[k], text_limit))
        else:
            out.extend(_turn_lines(real[k], text_limit))
    return "\n".join(out)


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
{output}
"""


def build_prompt(check_id: str, traj: Trajectory, ctx: CheckContext) -> str:
    spec = SPECS_BY_ID[check_id]
    catalog = ""
    if check_id == "DES-06" and ctx.catalog_summary:
        catalog = "\nCATÁLOGO REAL VIGENTE (lista cerrada):\n" + ctx.catalog_summary + "\n"
    guidance = JUDGE_PROMPTS[check_id]
    if check_id == "ENV-06":
        from src.plugins.chats.agent.sales.config.payments import get_nequi_number

        key = get_nequi_number()
        guidance = guidance.replace("{nequi}", f" ({key})" if key else "")
    return _TEMPLATE.format(
        id=spec.id,
        name=spec.name,
        applies=spec.applies,
        rule=spec.rule,
        guidance=guidance,
        catalog=catalog,
        episode_id=traj.episode_id or "?",
        n=len(traj.turns),
        fidelity=traj.fidelity,
        transcript=render_transcript(traj),
        output=_OUTPUT_BY_CHECK.get(check_id, _OUTPUT_DEFAULT),
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
    return _result_from(check_id, data)


def _result_from(check_id: str, data: dict[str, Any]) -> CheckResult | None:
    verdict = _VERDICT_ALIASES.get(str(data.get("veredicto", "")).strip().lower())
    if verdict is None:
        return None
    turn = data.get("turno")
    result = CheckResult(
        check_id,
        verdict,
        turn=int(turn) if isinstance(turn, (int, float)) else None,
        evidence=_clip(str(data.get("evidencia") or ""), _EVIDENCE_MAX),
        critique=_clip(str(data.get("critica") or ""), _EVIDENCE_MAX),
        source="judge",
    )
    if "asuntos" not in data:
        return result  # no es un check por asuntos (solo EST-08 los pide)
    return _with_topics(result, _parse_topics(data.get("asuntos")))


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _parse_topics(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        return ()
    topics: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("asunto") or "").strip():
            continue
        topics.append(
            {
                "topic": _clip(str(item["asunto"]), 80),
                "turn": _int_or_none(item.get("turno")),
                "msg": _int_or_none(item.get("mensaje")),
                "covered": item.get("cubierto") is True,
                "evidence": _clip(str(item.get("evidencia") or ""), 160),
            }
        )
    return tuple(topics)


def _with_topics(result: CheckResult, topics: tuple[dict[str, Any], ...]) -> CheckResult:
    """El veredicto sale de los asuntos: uno sin cubrir es `falla` en su turno,
    aunque el juez haya escrito `pasa`. Un juez que no supo decidir se respeta.

    Sin asuntos no hay nada que cubrir (un "hola"): no aplica. Un `falla` que
    no nombra ningún asunto sin cubrir no se puede sostener: desconocido."""
    if result.verdict == "desconocido":
        return replace(result, topics=topics)
    if not topics:
        return replace(result, verdict="desconocido" if result.verdict == "falla" else "no_aplica", topics=topics)
    missed = [t for t in topics if not t["covered"]]
    if not missed:
        return replace(result, verdict="desconocido" if result.verdict == "falla" else "pasa", topics=topics)
    first = min(missed, key=lambda t: t["turn"] if t["turn"] is not None else 10**9)
    where = f"T{first['turn']}" if first["turn"] is not None else "T?"
    msg = f", mensaje {first['msg']}" if first["msg"] is not None else ""
    evidence = result.evidence or f"sin respuesta: {first['topic']} ({where}{msg})"
    return replace(
        result, verdict="falla", turn=first["turn"] if first["turn"] is not None else result.turn,
        evidence=evidence, topics=topics,
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


# ── Modo turno (laboratorio, plan §5.2–5.5) ─────────────────────────────────
# Una llamada por check y por episodio (no por turno): el juez ve el prefijo
# real como contexto y todas las candidatas marcadas, y responde una entrada
# por candidata. El prefiltro de aplicabilidad corre por candidata sobre su
# trayectoria foco.

_FOCUS_TEMPLATE = """Eres auditor de calidad del asesor de ventas por WhatsApp de Hubara (velas artesanales hechas en Colombia).
Evalúas UN solo criterio sobre las respuestas CANDIDATAS del bot marcadas con ★ en la conversación de abajo.

CRITERIO {id} — {name}
Aplica cuando: {applies}
Regla: {rule}
Cómo decidir:
{guidance}

Reglas de evaluación:
- Juzga SOLO este criterio y SOLO los turnos marcados ★ CANDIDATA: {turns}.
- Cada candidata es una respuesta alternativa del bot en ese turno. Su contexto son los turnos ANTERIORES a ella. Lo que aparece después de una candidata es la conversación real, que siguió a otra respuesta: no lo uses para juzgarla.
- Si la regla admite atender algo "en ese turno o el siguiente", juzga la candidata sola: dejarlo pendiente sin empujar la venta en su lugar no es `falla`.
- `no_aplica` si la situación del criterio no ocurre en ese turno candidato.
- `desconocido` si la conversación no alcanza para decidir.
- Las etiquetas y escalaciones del estado las pone el mismo bot que evalúas: el estado del sistema no es prueba de que actuó bien.
- Lo que el cliente vio son los textos enviados y los componentes; el texto suprimido y la narración descartada no le llegaron.
- La evidencia es una cita textual corta (máximo 25 palabras) del turno candidato.
{catalog}
CONVERSACIÓN (episodio {episode_id}, fidelidad {fidelity}):
{transcript}

Devuelve SOLO un JSON válido, con una entrada por turno candidato:
{output}
"""

_FOCUS_ITEM_DEFAULT = (
    '{"turno": <T de la candidata>, "veredicto": "pasa|falla|no_aplica|desconocido", '
    '"evidencia": "...", "critica": "..."}'
)
_FOCUS_ITEM_BY_CHECK: dict[str, str] = {
    "EST-08": (
        '{"turno": <T de la candidata>, "veredicto": "pasa|falla|no_aplica|desconocido", "evidencia": "...", '
        '"critica": "...", "asuntos": [{"asunto": "...", "turno": <T>, "mensaje": <k o null>, '
        '"cubierto": true|false, "evidencia": "..."}]}'
    ),
}


def _guidance(check_id: str) -> str:
    guidance = JUDGE_PROMPTS[check_id]
    if check_id == "ENV-06":
        from src.plugins.chats.agent.sales.config.payments import get_nequi_number

        key = get_nequi_number()
        guidance = guidance.replace("{nequi}", f" ({key})" if key else "")
    return guidance


def build_focus_prompt(
    check_id: str, real: Trajectory, candidates: Mapping[int, Turn], ctx: CheckContext
) -> str:
    """Prompt de un check en modo turno: el prefijo real y las candidatas marcadas."""
    spec = SPECS_BY_ID[check_id]
    catalog = ""
    if check_id == "DES-06" and ctx.catalog_summary:
        catalog = "\nCATÁLOGO REAL VIGENTE (lista cerrada):\n" + ctx.catalog_summary + "\n"
    item = _FOCUS_ITEM_BY_CHECK.get(check_id, _FOCUS_ITEM_DEFAULT)
    return _FOCUS_TEMPLATE.format(
        id=spec.id,
        name=spec.name,
        applies=spec.applies,
        rule=spec.rule,
        guidance=_guidance(check_id),
        turns=", ".join(f"T{k}" for k in sorted(candidates)),
        catalog=catalog,
        episode_id=real.episode_id or "?",
        fidelity=real.fidelity,
        transcript=render_transcript(real, candidates=candidates),
        output='{"turnos": [' + item + ", ...]}",
    )


def parse_focus_output(check_id: str, raw: str, turns: Iterable[int]) -> dict[int, CheckResult] | None:
    """JSON del juez en modo turno → un resultado por turno candidato pedido.

    `None` si la respuesta no se puede leer. Un turno que el juez no respondió
    no aparece. Los asuntos de EST-08 se quedan con los del turno candidato."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data: Any = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    items = data.get("turnos") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    wanted = set(turns)
    out: dict[int, CheckResult] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        k = _int_or_none(item.get("turno"))
        if k is None or k not in wanted or k in out:
            continue
        asuntos = item.get("asuntos")
        if isinstance(asuntos, list):
            asuntos = [a for a in asuntos if isinstance(a, dict) and _int_or_none(a.get("turno")) in (k, None)]
        result = _result_from(check_id, {**item, "turno": k, "asuntos": asuntos})
        if result is not None:
            out[k] = replace(result, turn=k)
    return out


def _focus_unknown(check_id: str, turns: Iterable[int], critique: str) -> dict[int, CheckResult]:
    return {k: CheckResult(check_id, "desconocido", turn=k, critique=critique, source="judge") for k in turns}


def _agreed(check_id: str, k: int, answers: list[CheckResult | None]) -> CheckResult:
    """El veredicto de un turno si todas las muestras lo respondieron igual."""
    got = [a for a in answers if a is not None]
    if len(got) < len(answers):
        return CheckResult(
            check_id, "desconocido", turn=k, critique="el juez no respondió el turno candidato", source="judge"
        )
    verdicts = [a.verdict for a in got]
    if len(set(verdicts)) > 1:
        return CheckResult(
            check_id, "desconocido", turn=k,
            critique="juez inconsistente entre muestras: " + " vs ".join(verdicts), source="judge",
        )
    return got[0]


async def _judge_focus_one(
    check_id: str,
    real: Trajectory,
    candidates: Mapping[int, Turn],
    focus: Mapping[int, Trajectory],
    ctx: CheckContext,
    judge: JudgePort,
    samples: int,
    sleep: Sleep,
) -> dict[int, CheckResult]:
    out: dict[int, CheckResult] = {}
    future = SPECS_BY_ID[check_id].focus == "future"
    applicable: list[int] = []
    for k in sorted(candidates):
        applies, reason = _APPLIES[check_id](focus[k], ctx)
        if applies:
            applicable.append(k)
            continue
        # Un check `future` que todavía no aplica puede aplicar con el cierre.
        verdict = "sin_senal" if future else "no_aplica"
        out[k] = CheckResult(check_id, verdict, turn=k, evidence=reason, source="judge")
    if not applicable:
        return out
    if check_id == "DES-06" and not ctx.catalog_available:
        for k in applicable:
            out[k] = CheckResult(check_id, "desconocido", turn=k, evidence="catálogo no disponible", source="judge")
        return out
    # El transcript llega hasta la última candidata: con varias, el juez de
    # T3 vería T4…Tn. Para un check `future` eso es información del futuro
    # (un `pasa` con evidencia posterior): se pregunta una candidata por vez,
    # con el transcript cortado en ella. Los checks `turn` comparten llamada
    # (el prompt pide juzgar cada candidata solo con lo anterior).
    groups = [[k] for k in applicable] if future else [applicable]
    for group in groups:
        out |= await _ask_focus(check_id, real, {k: candidates[k] for k in group}, ctx, judge, samples, sleep)
    return out


async def _ask_focus(
    check_id: str,
    real: Trajectory,
    candidates: Mapping[int, Turn],
    ctx: CheckContext,
    judge: JudgePort,
    samples: int,
    sleep: Sleep,
) -> dict[int, CheckResult]:
    turns = sorted(candidates)
    prompt = build_focus_prompt(check_id, real, candidates, ctx)
    parsed: list[dict[int, CheckResult]] = []
    for _ in range(max(1, samples)):
        try:
            raw = await _generate(judge, prompt, sleep)
        except Exception as exc:  # noqa: BLE001 — el juez caído no tumba el scorecard
            return _focus_unknown(check_id, turns, f"{JUDGE_ERROR_PREFIX}: {exc!r}"[:200])
        by_turn = parse_focus_output(check_id, raw, turns)
        if by_turn is None:
            return _focus_unknown(check_id, turns, "respuesta del juez ilegible")
        parsed.append(by_turn)
    return {k: _agreed(check_id, k, [p.get(k) for p in parsed]) for k in turns}


async def run_judge_checks_focus(
    real: Trajectory,
    candidates: Mapping[int, Turn],
    ctx: CheckContext,
    judge: JudgePort,
    *,
    samples: int = SAMPLES,
    only: Iterable[str] | None = None,
    episodes_at: Mapping[int, dict[str, Any]] | None = None,
    sleep: Sleep = asyncio.sleep,
) -> dict[int, list[CheckResult]]:
    """Checks de juez en modo turno: turno candidato → resultados (orden de registro).

    UNA llamada por check (y muestra) para todo el episodio, con las candidatas
    marcadas en el transcript; el acuerdo entre muestras se mide por turno."""
    if not candidates:
        return {}
    wanted = set(only) if only is not None else None
    ids = [c.id for c in CHECKS if c.kind == "judge" and (wanted is None or c.id in wanted)]
    focus = {
        k: focus_trajectory(real, candidates[k], episode_at=(episodes_at or {}).get(k)) for k in candidates
    }
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def bounded(check_id: str) -> dict[int, CheckResult]:
        async with semaphore:
            return await _judge_focus_one(check_id, real, candidates, focus, ctx, judge, samples, sleep)

    per_check = await asyncio.gather(*(bounded(i) for i in ids))
    return {k: [results[k] for results in per_check] for k in sorted(candidates)}
