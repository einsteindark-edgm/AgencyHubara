"""Juego de preguntas `rafaga-v1` (plan del laboratorio §4.2). Del dominio de
ventas: vive en `chats`, no en la plataforma (desvío anotado en el plan §12).

Una sola llamada por turno al clasificador (Jev u OpenAI por OpenRouter), con
preguntas en la forma nativa de Jev:
  * 17 asuntos (`topic.<id>`, sí/no): ¿el cliente lo plantea en la ráfaga?
  * hilo (sí/no): ¿responde a la última pregunta del bot? ¿repite algo que
    quedó sin respuesta?
  * etapa (elección entre 7) y asunto principal de cada mensaje (elección).
La verificación (③) hace una pregunta por asunto del plan: ¿la respuesta del
asesor lo atiende?

El `state` es la ráfaga mensaje por mensaje con su hora relativa, los asuntos
pendientes de turnos anteriores y la última pregunta del bot. La plataforma
lo anonimiza antes de enviarlo si el perfil lo pide (teléfonos, correos,
nombres).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.perception.plan import TOPIC_LABELS, TurnPlan
from src.sdk.connectorkit import TypedQuestion

QUESTION_SET = "rafaga-v1"
TOPICS: tuple[str, ...] = tuple(TOPIC_LABELS)
STAGE_OPTIONS: tuple[str, ...] = (
    "apertura",
    "descubrimiento",
    "variantes",
    "confirmacion",
    "datos_envio",
    "cierre",
    "postcierre",
)

_TOPIC_HINTS: dict[str, str] = {
    "catalogo": "pide ver el catálogo, los diseños o los modelos",
    "precio": "pregunta cuánto vale o cuánto cuesta algo",
    "envio": "pregunta por el envío o el domicilio (costo o cobertura)",
    "tiempos": "pregunta cuánto tarda en llegar o cuándo está listo",
    "pagos": "pregunta cómo pagar o por un medio de pago",
    "medidas": "pregunta por el tamaño o las medidas",
    "variante": "pregunta por colores o variantes",
    "aroma": "pregunta por los aromas",
    "personalizacion": "pide personalizar (nombre, dedicatoria, diseño propio)",
    "disponibilidad": "pregunta si hay disponible o en stock",
    "estado_pedido": "pregunta por un pedido que ya hizo",
    "datos_envio": "entrega sus datos de envío (ciudad, dirección, teléfono, quién recibe)",
    "confirma_compra": "confirma que quiere comprar",
    "aplaza": "aplaza la compra (luego, otro día, voy en camino)",
    "queja": "se queja o reporta un problema",
    "foto": "pregunta por una foto que envió o citó",
    "saludo": "saluda",
}


def _offset(ms: Any, first: Any) -> str:
    if isinstance(ms, (int, float)) and isinstance(first, (int, float)):
        return f"+{round((ms - first) / 1000)} s"
    return "+? s"


def burst_state(
    messages: Sequence[dict[str, Any]],
    *,
    pending: Sequence[str] = (),
    last_bot_text: str | None = None,
) -> str:
    """El `state` de la pregunta: la ráfaga mensaje por mensaje."""
    first = messages[0].get("ts_ms") if messages else None
    lines = ["Mensajes del cliente en este turno:"]
    for k, m in enumerate(messages, 1):
        lines.append(f"[{k}] ({_offset(m.get('ts_ms'), first)}) {str(m.get('text') or '').strip()}")
    if pending:
        lines.append("Asuntos que quedaron sin responder en turnos anteriores: " + ", ".join(pending))
    if last_bot_text:
        lines.append(f'Último mensaje del asesor antes de este turno: "{last_bot_text.strip()[:400]}"')
    return "\n".join(lines)


def rafaga_questions(messages: Sequence[dict[str, Any]]) -> list[TypedQuestion]:
    questions = [
        TypedQuestion(
            id=f"topic.{topic}",
            kind="noul",
            text=f"¿En estos mensajes el cliente {hint}?",
            criteria={"true": "sí, lo plantea en algún mensaje", "false": "no lo plantea"},
        )
        for topic, hint in _TOPIC_HINTS.items()
    ]
    questions += [
        TypedQuestion(
            id="thread.answers_last_bot_question",
            kind="noul",
            text="¿El cliente está respondiendo la última pregunta del asesor?",
            criteria={"true": "sí, la responde", "false": "no, habla de otra cosa"},
        ),
        TypedQuestion(
            id="thread.repeats_unanswered",
            kind="noul",
            text="¿El cliente repite algo que preguntó antes y quedó sin respuesta?",
            criteria={"true": "sí, lo repite", "false": "no"},
        ),
        TypedQuestion(
            id="stage",
            kind="choice",
            text="¿En qué etapa de la compra está la conversación?",
            criteria={s: s.replace("_", " ") for s in STAGE_OPTIONS},
        ),
    ]
    options = {topic: TOPIC_LABELS[topic] for topic in TOPICS} | {"ninguno": "ningún asunto de la lista"}
    questions += [
        TypedQuestion(id=f"msg.{k}.topic", kind="choice", text=f"¿Cuál es el asunto principal del mensaje [{k}]?", criteria=options)
        for k in range(1, len(messages) + 1)
    ]
    return questions


def verify_questions(plan: TurnPlan) -> list[TypedQuestion]:
    return [
        TypedQuestion(
            id=f"cover.{t.topic}",
            kind="noul",
            text=(
                f"¿La respuesta del asesor (texto y tarjetas) atiende el asunto «{TOPIC_LABELS.get(t.topic, t.topic)}»"
                + (f" que el cliente planteó en el mensaje [{t.msg}]?" if t.msg else " que planteó el cliente?")
            ),
            criteria={"true": "sí, lo atiende", "false": "no lo atiende"},
        )
        for t in plan.topics
    ]


def reply_state(messages: Sequence[dict[str, Any]], reply_text: str, components: Sequence[str]) -> str:
    """El `state` de la verificación: la ráfaga y lo que el asesor va a enviar."""
    lines = [burst_state(messages), "Respuesta del asesor:"]
    lines.append(reply_text.strip() or "(sin texto)")
    if components:
        lines.append("Tarjetas y componentes que también recibe el cliente: " + ", ".join(components))
    return "\n".join(lines)
