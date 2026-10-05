"""Egreso del turno de ventas: lo que el LLM escribió, antes de grabarlo en su
historial y de que salga al cliente (diseño v2 §07 familia C, §08; F4/F5).

Cinco capacidades, cada una con la regla de hoy como respaldo (`reglas`) y su
pregunta cerrada a Jev (`sombra` / `jev`), sobre el marco de
`capabilities/__init__.py` (referencia: `capabilities/lecturas.py`):

  preambulo     por oración al principio: «¿es una muletilla de presentación
                del modelo, sin contenido para el cliente?» («Aquí tienes:»).
                Regla: el meta-prefijo del saneador (`strip_model_preamble`).
                También la piden las tools (`guards.clean_llm_text`).
  destinatario  «¿Qué es este texto?»: mensaje al cliente, razonamiento,
                reporte interno, acuse al sistema o deliberación. Regla:
                `looks_like_admin_leak` con el set extendido.
  rescate       el mismo «¿qué es?», párrafo por párrafo: quedan los que son
                para el cliente. Regla: `salvage_customer_text`. Si no queda
                nada, una sola reacción: el silencio (el turno no manda texto).
  portavelas    en un pedido SIN portavelas, por oración: «¿le afirma algo del
                portavelas?». Regla: `strip_portavelas_notice`; si no queda
                nada, la despedida aprobada.
  saludo        «¿alguno de estos mensajes ya saluda?». Regla:
                `should_send_first_contact_greeting` con las entradas del V1.

`decide_egress` las compone en el orden del V1: (0) el preámbulo, si el turno
trae lo que escribió el LLM antes del saneador (`raw_text`: el V1 sanea con la
regla dentro de `run_agent_turn`), (1) rescate antes de grabar (el V1 lo hace
dentro de `run_agent_turn`), (3) portavelas, (4) destinatario y rescate sobre
lo que quedó y (5) el saludo con lo que DE VERDAD sale (un texto frenado no
cuenta; el V1 lo decidía antes de frenarlo). Con `reglas` el resultado es
idéntico al del V1 salvo ese caso (tests de equivalencia). Lo que no es texto
del LLM no se le pregunta a Jev: el centinela `NO_MESSAGE` (protocolo) y el
texto de un turno administrativo (nunca sale) los decide la regla, y el mismo
texto se juzga una sola vez.

Sin Temporal ni I/O propio: lo llama la activity `decide_egress`
(`egress_activities.py`), que resuelve el bot de la conversación.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities import BY_JEV, Verdict, decide
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.decisions.plan import answer_of
from src.plugins.chats.agent.sales.first_contact_greeting import greeting_applies, should_send_first_contact_greeting
from src.sdk.connectorkit import TypedQuestion
from src.sdk.textkit import (
    is_no_message_abstention,
    looks_like_admin_leak,
    preamble_stage,
    salvage_customer_text,
    sanitize_llm_text,
    strip_model_preamble,
    strip_portavelas_notice,
)
from src.plugins.chats.agent.sales.decisions.retiro import en_retiro

# Despedida mínima si todo el texto hablaba del portavelas: el cliente que
# acaba de dar sus datos NUNCA recibe silencio. La MISMA línea que el V1
# (`_ORDER_REGISTERED_FALLBACK_FAREWELL`; un test lo exige: el motor no puede
# importar el workflow).
ORDER_REGISTERED_FALLBACK_FAREWELL = "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara."

DESTINATARIO = "destinatario"
RESCATE = "rescate"
PORTAVELAS = "portavelas"
SALUDO = "saludo"
PREAMBULO = "preambulo"
EGRESS_CAPABILITIES: tuple[str, ...] = (PREAMBULO, DESTINATARIO, RESCATE, PORTAVELAS, SALUDO)

_YES_NO = {"true": "sí", "false": "no"}
_FOR_CUSTOMER = "mensaje_al_cliente"
_WHAT_IS_IT = {
    _FOR_CUSTOMER: "un mensaje para el cliente (le responde, le pregunta, le informa o se despide)",
    "razonamiento": "el razonamiento del asesor sobre qué responder o qué hacer",
    "reporte_interno": "un reporte interno: etiquetas, estados, sistemas o datos para el equipo",
    "acuse_al_sistema": "un acuse de recibo a una herramienta o al sistema",
    "deliberacion": "la deliberación de si responder o no",
}
# Corte mecánico del texto (no lee su sentido): el mismo que usan las reglas
# de hoy para rescatar por párrafo y quitar el portavelas por oración.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+|(?<![\w,;:\s])\s+(?=[A-ZÁÉÍÓÚÑ¡¿])")
# Más partes que esto no se le preguntan a Jev una por una: decide la regla.
_MAX_PARTS = 8


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


def _choice(result: Any, qid: str) -> tuple[str | None, float]:
    answer = answer_of(result, qid)
    choice = getattr(answer, "choice", None)
    if not choice:
        return None, 0.0
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(choice, getattr(answer, "confidence", None))
    return choice, float(p) if isinstance(p, (int, float)) else 0.0


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text.strip()) if p.strip()]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s and s.strip()]


def _numbered(parts: Sequence[str]) -> str:
    return "\n".join(f"[{i}] {part}" for i, part in enumerate(parts, 1))


@dataclass(frozen=True)
class TextCheck:
    """Un texto que el LLM escribió para el cliente. `extended`: con qué set
    de patrones decide la regla de hoy (el egreso y `send_reply` usan el
    extendido; el flush de los intents, el básico)."""

    text: str
    extended: bool = True


@dataclass(frozen=True)
class OracionesCheck:
    """Las oraciones de un texto para el cliente (`customer_sentences`)."""

    parts: tuple[str, ...]


@dataclass(frozen=True)
class PortavelasCheck:
    text: str
    order_registered: bool = False
    portavelas_included: bool | None = None


@dataclass(frozen=True)
class GreetingCheck:
    first_contact: bool
    tools_used: tuple[str, ...] = ()
    client_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreambuloCheck:
    """El texto que el LLM escribió para el cliente tal como lo lee el paso
    del meta-prefijo del saneador de la plataforma (`preamble_stage`)."""

    text: str


# Corte mecánico para la muletilla del modelo (no lee el sentido): fin de
# línea, o dos puntos o fin de oración seguidos de espacio («Aquí tienes: ¡Hola!»).
_PREAMBLE_SPLIT_RE = re.compile(r"\n+|(?<=[:.!?…])[ \t]+")
# Solo al principio del texto: más adentro ya no es presentación.
_MAX_PREAMBLE_SENTENCES = 3


def _leading_sentences(text: str) -> list[tuple[int, str]]:
    """`(inicio, oración)` de cada oración de `text`, en orden."""
    out: list[tuple[int, str]] = []
    start = 0
    for match in _PREAMBLE_SPLIT_RE.finditer(text):
        if text[start:match.start()].strip():
            out.append((start, text[start:match.start()].strip()))
        start = match.end()
    if text[start:].strip():
        out.append((start, text[start:].strip()))
    return out


def _preamble_of(text: str, kept: str) -> str:
    """La muletilla que se cae cuando de `text` queda `kept` ("" = ninguna)."""
    return "" if kept == text else text[: len(text) - len(kept)].rstrip()


def text_without_preamble(text: str, preamble: str) -> str:
    """Lo que queda de `text` (el de `preamble_stage`) sin la muletilla que
    decidió la capacidad `preambulo` (su valor): lo que el saneador recibe en
    `without_preamble`."""
    if preamble and text.startswith(preamble):
        return text[len(preamble):].lstrip()
    return text


@en_retiro("clase:preambulo")
class Preambulo:
    """«¿Esta oración es una muletilla de presentación del modelo, sin
    contenido para el cliente?», para las primeras oraciones del texto (nunca
    la última: el texto nunca queda vacío). Es el único paso del saneador de
    la plataforma que LEE el texto; los demás (comillas, duplicados, rayas)
    siguen mecánicos y corren después, en su orden.

    Valor: la muletilla que se cae al principio ("" = ninguna); en la cola de
    desacuerdos va eso, no el mensaje. Regla: el meta-prefijo de hoy
    (`strip_model_preamble`). Jev corta lo que la regla no conoce («Claro,
    aquí va el mensaje para el cliente:») y deja una línea que sí le habla al
    cliente; corta solo con p ≥ `yes` y deja con p ≤ `no` (si duda, la regla).
    Nunca corta a mitad de frase. Piso: los prefijos que casi nunca abren una
    oración legítima («Here's my attempt:», «Final answer:») siempre se van."""

    name = PREAMBULO
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: PreambuloCheck) -> str:
        return _preamble_of(inp.text, strip_model_preamble(inp.text))

    def ask(self, inp: PreambuloCheck) -> tuple[str, list[TypedQuestion]] | None:
        parts = _leading_sentences(inp.text)
        if len(parts) < 2:
            return None  # una sola oración nunca es solo muletilla
        shown = parts[: _MAX_PREAMBLE_SENTENCES + 2]
        state = (
            "Texto que el asesor de ventas escribió para enviarle al cliente por WhatsApp, oración por oración:\n"
            + _numbered([part for _start, part in shown])
            + ("\n[…]" if len(parts) > len(shown) else "")
        )
        return state, [
            TypedQuestion(
                id=f"preambulo.{i}", kind="noul",
                text=(
                    f"¿La oración [{i}] es una muletilla de presentación del modelo (como «Aquí tienes:», "
                    "«Claro, aquí va mi respuesta:» o «Here's my attempt:»), sin contenido para el cliente?"
                ),
                criteria=_YES_NO,
            )
            for i in range(1, min(_MAX_PREAMBLE_SENTENCES, len(parts) - 1) + 1)
        ]

    def decide(self, inp: PreambuloCheck, result: Any, rule: str, thresholds: Mapping[str, float]) -> str | None:
        th = {**self.thresholds, **thresholds}
        parts = _leading_sentences(inp.text)
        cut = 0
        for i in range(1, min(_MAX_PREAMBLE_SENTENCES, len(parts) - 1) + 1):
            p = _p(result, f"preambulo.{i}")
            if p is None or th["no"] < p < th["yes"]:
                return None  # sin respuesta o con duda: decide la regla
            if p <= th["no"]:
                break
            cut = i
        if cut == 0:
            return ""
        kept = inp.text[parts[cut][0]:].lstrip()
        if not kept or kept[0].islower():
            return None  # cortaría a mitad de frase: decide la regla
        return _preamble_of(inp.text, kept)

    def floor(self, inp: PreambuloCheck, rule: str, jev: str) -> str:
        strong = _preamble_of(inp.text, strip_model_preamble(inp.text, strong_only=True))
        return strong if len(strong) > len(jev or "") else jev

    def same(self, a: str, b: str) -> bool:
        return (a or "") == (b or "")


@en_retiro("clase:destinatario")
class Destinatario:
    """¿El texto es para el cliente? Valor: True = NO es para el cliente (la
    regla de hoy: huele a parte interno). Jev decide en los dos sentidos: puede
    dejar pasar un falso positivo («Usa el código VELAS_10 al pagar») y frenar
    un reporte que la regla no ve."""

    name = DESTINATARIO
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: TextCheck) -> bool:
        return looks_like_admin_leak(inp.text, extended=inp.extended)

    def ask(self, inp: TextCheck) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.text.strip():
            return None
        state = "Texto que el asesor de ventas escribió para enviarle al cliente por WhatsApp:\n" + inp.text.strip()
        return state, [TypedQuestion(id="egreso.destinatario", kind="choice", text="¿Qué es este texto?", criteria=dict(_WHAT_IS_IT))]

    def decide(self, inp: TextCheck, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        choice, p = _choice(result, "egreso.destinatario")
        if choice is None or p < th["choice"]:
            return None
        return choice != _FOR_CUSTOMER

    def floor(self, inp: TextCheck, rule: bool, jev: bool) -> bool:
        return jev

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


@en_retiro("clase:destinatario_plantilla")
class DestinatarioDePlantilla(Destinatario):
    """El mismo «¿qué es este texto?» para un texto del LLM que viaja como
    variable de una plantilla (el `motivo` en el watchdog de remarketing).
    Hoy no se revisa nada: la regla dice «es para el cliente» (False)."""

    def rule(self, inp: TextCheck) -> bool:
        return False


@en_retiro("clase:destinatario_oracion")
class DestinatarioPorOracion:
    """El mismo «¿qué es?», oración por oración, para el filtro de oraciones
    de las tools de cierre y de escalación (misma capacidad: mismo
    interruptor). Valor: los índices de las oraciones que NO son para el
    cliente. Regla: `looks_like_admin_leak` básico por oración (el de
    `keep_customer_safe_sentences`). Si Jev duda de una oración, decide la
    regla para esa oración."""

    name = DESTINATARIO
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: OracionesCheck) -> tuple[int, ...]:
        return tuple(i for i, part in enumerate(inp.parts) if looks_like_admin_leak(part))

    def ask(self, inp: OracionesCheck) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.parts or len(inp.parts) > _MAX_PARTS:
            return None
        state = "Texto que el asesor de ventas escribió para el cliente, oración por oración:\n" + _numbered(inp.parts)
        return state, [
            TypedQuestion(id=f"egreso.oracion.{i}", kind="choice", text=f"¿Qué es la oración [{i}]?", criteria=dict(_WHAT_IS_IT))
            for i in range(1, len(inp.parts) + 1)
        ]

    def decide(
        self, inp: OracionesCheck, result: Any, rule: tuple[int, ...], thresholds: Mapping[str, float]
    ) -> tuple[int, ...] | None:
        th = {**self.thresholds, **thresholds}
        drop: list[int] = []
        answered = False
        for i in range(len(inp.parts)):
            choice, p = _choice(result, f"egreso.oracion.{i + 1}")
            if choice is not None:
                answered = True
            if choice is not None and p >= th["choice"]:
                if choice != _FOR_CUSTOMER:
                    drop.append(i)
            elif i in rule:
                drop.append(i)
        return tuple(drop) if answered else None

    def floor(self, inp: OracionesCheck, rule: tuple[int, ...], jev: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(jev)

    def same(self, a: Sequence[int], b: Sequence[int]) -> bool:
        return tuple(sorted(a)) == tuple(sorted(b))


@en_retiro("clase:rescate")
class Rescate:
    """Lo que sí es para el cliente, párrafo por párrafo (valor: el texto que
    queda; "" = nada). Jev contesta el «¿qué es?» de cada párrafo; si duda de
    alguno, decide la regla."""

    name = RESCATE
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: TextCheck) -> str:
        return salvage_customer_text(inp.text, extended=True)

    def ask(self, inp: TextCheck) -> tuple[str, list[TypedQuestion]] | None:
        parts = _paragraphs(inp.text)
        if not parts or len(parts) > _MAX_PARTS:
            return None
        state = "Texto que el asesor de ventas escribió para el cliente, por párrafos:\n" + _numbered(parts)
        return state, [
            TypedQuestion(id=f"egreso.rescate.{i}", kind="choice", text=f"¿Qué es el párrafo [{i}]?", criteria=dict(_WHAT_IS_IT))
            for i in range(1, len(parts) + 1)
        ]

    def decide(self, inp: TextCheck, result: Any, rule: str, thresholds: Mapping[str, float]) -> str | None:
        th = {**self.thresholds, **thresholds}
        kept: list[str] = []
        for i, part in enumerate(_paragraphs(inp.text), 1):
            choice, p = _choice(result, f"egreso.rescate.{i}")
            if choice is None or p < th["choice"]:
                return None
            if choice == _FOR_CUSTOMER:
                kept.append(part)
        return "\n\n".join(kept)

    def floor(self, inp: TextCheck, rule: str, jev: str) -> str:
        return jev

    def same(self, a: str, b: str) -> bool:
        return (a or "") == (b or "")


@en_retiro("clase:portavelas")
class Portavelas:
    """En un pedido registrado SIN portavelas (lo decide la tool contra el
    catálogo), las oraciones que le afirman algo del portavelas se quitan; si
    no queda nada, la despedida aprobada. Valor: el texto que queda. Decirle
    que su pedido no lo incluye es cierto y queda (la regla de hoy lo borraba
    porque dice «portavela»)."""

    name = PORTAVELAS
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    @staticmethod
    def applies(inp: PortavelasCheck) -> bool:
        return bool(inp.text) and inp.order_registered and not inp.portavelas_included

    def rule(self, inp: PortavelasCheck) -> str:
        if self.applies(inp) and "portavela" in inp.text.lower():
            return strip_portavelas_notice(inp.text) or ORDER_REGISTERED_FALLBACK_FAREWELL
        return inp.text

    def ask(self, inp: PortavelasCheck) -> tuple[str, list[TypedQuestion]] | None:
        parts = _sentences(inp.text) if self.applies(inp) else []
        if not parts or len(parts) > _MAX_PARTS:
            return None
        state = (
            "El cliente acaba de registrar un pedido que NO incluye portavelas. Despedida que el asesor "
            "escribió, por oraciones:\n" + _numbered(parts)
        )
        criteria = {
            "true": "le afirma algo del portavelas como si su pedido lo trajera (que viene, sus colores, cómo se escoge)",
            "false": "no habla del portavelas, o le aclara que su pedido no lo incluye",
        }
        return state, [
            TypedQuestion(id=f"egreso.portavelas.{i}", kind="noul", text=f"¿La oración [{i}] le afirma algo del portavelas?", criteria=criteria)
            for i in range(1, len(parts) + 1)
        ]

    def decide(self, inp: PortavelasCheck, result: Any, rule: str, thresholds: Mapping[str, float]) -> str | None:
        th = {**self.thresholds, **thresholds}
        parts = _sentences(inp.text)
        kept: list[str] = []
        for i, part in enumerate(parts, 1):
            p = _p(result, f"egreso.portavelas.{i}")
            if p is None or th["no"] < p < th["yes"]:
                return None
            if p <= th["no"]:
                kept.append(part)
        if len(kept) == len(parts):
            return inp.text
        return " ".join(kept) or ORDER_REGISTERED_FALLBACK_FAREWELL

    def floor(self, inp: PortavelasCheck, rule: str, jev: str) -> str:
        return jev

    def same(self, a: str, b: str) -> bool:
        return (a or "") == (b or "")


@en_retiro("clase:saludo")
class Saludo:
    """¿Hace falta la burbuja de bienvenida? Solo en el primer contacto y si el
    turno le manda algo al cliente — una tool que le escribe o un texto (eso
    lo sabe el código); Jev contesta si alguno de los textos que el cliente
    recibe ya saluda. Los turnos de texto cuentan desde el 2026-09-29 (caso de
    Halloween del laboratorio: saludaba DESPUÉS, en el complemento)."""

    name = SALUDO
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: GreetingCheck) -> bool:
        return should_send_first_contact_greeting(
            first_contact=inp.first_contact, tools_used=list(inp.tools_used), client_texts=list(inp.client_texts)
        )

    def ask(self, inp: GreetingCheck) -> tuple[str, list[TypedQuestion]] | None:
        # La parte estructural (primer contacto y el turno le manda algo al
        # cliente) la sabe el código; Jev solo dice si algo ya saluda.
        applies = greeting_applies(
            first_contact=inp.first_contact, tools_used=list(inp.tools_used), client_texts=list(inp.client_texts)
        )
        texts = [t.strip() for t in inp.client_texts if t and t.strip()]
        if not applies or not texts or len(texts) > _MAX_PARTS:
            return None
        state = "Primer contacto con el cliente. Mensajes que recibe en este turno:\n" + _numbered(texts)
        return state, [
            TypedQuestion(
                id="egreso.saludo", kind="noul",
                text="¿Alguno de estos mensajes ya saluda al cliente (buenos días, hola, bienvenida)?", criteria=_YES_NO,
            )
        ]

    def decide(self, inp: GreetingCheck, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "egreso.saludo")
        if p is None:
            return None
        if p >= th["yes"]:
            return False
        if p <= th["no"]:
            return True
        return None

    def floor(self, inp: GreetingCheck, rule: bool, jev: bool) -> bool:
        return jev

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


def _rules_only(_capability: str) -> str:
    return "reglas"


async def decide_egress(
    inp: EgressInput,
    *,
    provider_of: Callable[[str], str],
    profile_id: str,
    disagreements: Any = None,
    redact: Sequence[str] = (),
    metrics: Any = None,
    decisions: Any = None,
) -> EgressOutput:
    """Los veredictos del egreso para el texto final del turno (ver el módulo).
    Nunca lanza por Jev: si falla, tarda o duda, decide la regla de hoy."""
    # Cada capacidad por su nombre (paquete de la tienda o su clase).
    from src.plugins.chats.agent.sales.decisions.registry import capability

    text = inp.final_text or ""
    # Lo que no es texto del LLM para el cliente no se le pregunta a Jev.
    ask_jev = not inp.admin_turn and not is_no_message_abstention(text)
    provider = provider_of if ask_jev else _rules_only
    verdicts: list[Verdict] = []
    # (capacidad, texto) → (valor, quién decidió): el mismo texto se juzga una vez.
    judged: dict[tuple[str, str], tuple[Any, str]] = {}

    async def run(capability: Any, check: Any, *, key: str | None = None) -> tuple[Any, str]:
        if key is not None and (capability.name, key) in judged:
            return judged[(capability.name, key)]
        verdict = await decide(
            capability, check, provider=provider(capability.name), profile_id=profile_id,
            disagreements=disagreements, session_id=inp.session_id, redact=redact, metrics=metrics,
            decisions=decisions,
        )
        verdicts.append(verdict)
        if key is not None:
            judged[(capability.name, key)] = (verdict.value, verdict.by)
        return verdict.value, verdict.by

    # 0 · Preámbulo del modelo: el turno saneó con la regla de hoy; si trae
    # lo que escribió el LLM, el saneado se repite con la muletilla que decide
    # el motor (con `reglas`, el mismo texto). Si difiere, va en `sanitizer`
    # para que la traza del turno lo muestre.
    sanitizer: dict[str, Any] = {}
    stage = preamble_stage(inp.raw_text) if inp.raw_text is not None else ""
    if stage:
        preamble, _ = await run(capability("preambulo"), PreambuloCheck(stage))
        engine = sanitize_llm_text(inp.raw_text or "", without_preamble=text_without_preamble(stage, str(preamble or "")))
        if engine.text != text:
            sanitizer = {"before": inp.raw_text, "after": engine.text, "actions": list(engine.actions)}
            text = engine.text
    destinatario, rescate = capability("destinatario"), capability("rescate")
    # 1 · Rescate antes de grabar: el LLM recuerda lo que de verdad sale.
    llm_text, rescued_before_record = text, False
    if text and not inp.admin_turn and not is_no_message_abstention(text):
        leak, _ = await run(destinatario, TextCheck(text), key=text)
        if leak:
            rescued, by = await run(rescate, TextCheck(text), key=text)
            if rescued:
                llm_text, rescued_before_record = rescued, True
                if by == BY_JEV:
                    # Jev ya dijo que cada párrafo que quedó es para el cliente.
                    judged[(DESTINATARIO, rescued)] = (False, BY_JEV)
    guards: list[dict[str, str]] = []
    # 3 · Portavelas: solo en el pedido registrado sin portavelas.
    final_text = llm_text
    check = PortavelasCheck(llm_text, order_registered=bool(inp.order_registered), portavelas_included=inp.portavelas_included)
    if Portavelas.applies(check):
        stripped, _ = await run(capability("portavelas"), check)
        if stripped != llm_text:
            guards.append({"name": "portavelas_notice_guard", "before": llm_text, "after": stripped})
            final_text = stripped
    # 4 · Destinatario y rescate sobre lo que quedó.
    blocked = salvaged_after = False
    if final_text and (await run(destinatario, TextCheck(final_text), key=final_text))[0]:
        guards.append({"name": "admin_text_guard", "before": final_text, "after": ""})
        rescued, _ = await run(rescate, TextCheck(final_text), key=final_text)
        if rescued:
            guards.append({"name": "admin_text_salvaged", "before": final_text, "after": rescued})
            final_text, salvaged_after = rescued, True
        else:
            blocked = True
    # 5 · Saludo, con lo que el cliente DE VERDAD recibe en el turno (un texto
    # frenado no cuenta: sin nada que salga no hay bienvenida suelta).
    goes_out = "" if (inp.admin_turn or blocked) else final_text
    greeting_needed, _ = await run(
        capability("saludo"),
        GreetingCheck(
            first_contact=bool(inp.first_contact),
            tools_used=tuple(inp.tools_used or ()),
            client_texts=(*(inp.outbound_tool_texts or ()), goes_out),
        ),
    )
    return EgressOutput(
        text="" if (inp.admin_turn or blocked) else final_text,
        blocked=blocked,
        salvaged=rescued_before_record or salvaged_after,
        portavelas=any(g["name"] == "portavelas_notice_guard" for g in guards),
        greeting_needed=bool(greeting_needed),
        verdicts=[v.to_trace() for v in verdicts],
        llm_text=llm_text,
        final_text=final_text,
        rescued_before_record=rescued_before_record,
        guards=guards,
        sanitizer=sanitizer,
    )
