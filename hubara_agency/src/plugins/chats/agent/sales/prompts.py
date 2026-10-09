"""Prompts puros (sin side effects, sin I/O) del dominio Sales.

Estos textos son business logic: codifican la decision de como hablarle al
LLM al detectar un evento del workflow (ej: ghosting). Viven aqui (no en el
workflow) para que sean testeables sin Temporal y para que el cambio de un
prompt sea un PR plano sin tocar el grafo de tasks.

PR-E: este archivo se movio de ``domain/policies/prompts.py`` al top-level.
A 1.3K LoC el sub-folder hexagonal ``domain/policies/`` no aporta, un solo
modulo plano con la utilidad pura es mas claro de descubrir y testear.

Convenciones de estilo (Hubara, post-HU dialecto colombiano):
- Tuteo colombiano, NUNCA voseo rioplatense.
- Sin em dash en el texto que el LLM podria imitar.
- Saludo por hora de Colombia cuando aplique (ver USER.md / SCRIPT.md).
"""
from __future__ import annotations

_GHOSTING_PROMPT = (
    "[SISTEMA]: El usuario dejó de responder (ghosting). Lee la conversación "
    "completa y usa OBLIGATORIAMENTE la herramienta `manage_conversation_tag`. "
    "\n\n"
    "**ANTES DE DECIDIR, chequea interrupción técnica:**\n"
    "Si el ÚLTIMO mensaje del agente (assistant) es una línea de disculpa por "
    "interrupción técnica (frases como 'Justo se me cortó', 'Perdón, dame un "
    "segundo', 'se me trabó'), el cliente probablemente no recibió respuesta "
    "completa antes del corte. Esto NO es ghosting real, usa `INTERESADO` con "
    "motivo='Interrupción técnica del agente, el cliente puede no haber "
    "recibido respuesta completa'. La marca de fábrica de este caso: el "
    "agente terminó con disculpa sin entregar el contenido prometido (catálogo, "
    "precio, info que el cliente pidió).\n\n"
    "**Criterio de etiqueta (importante para no perder ventas):**\n"
    "- Pedido a medio cerrar (el cliente confirmó la compra pero no completó "
    "los datos de envío, o los dio y no tocó '✅ Confirmar' en el resumen de "
    "`present_order_confirmation`): es `INTERESADO`, con un motivo que diga "
    "qué falta ('confirmó, faltan los datos de envío' o 'dio sus datos, falta "
    "tocar Confirmar'). El pedido queda guardado y remarketing lo retoma; NO "
    "lo pases al equipo ni uses `CONFIRMADO_SIN_DATOS` (decisión del "
    "operador 2026-10-09: ningún silencio pasa solo al equipo).\n"
    "- `INTERESADO` (DEFAULT cuando hay duda): el usuario mostró ALGUNA señal "
    "de interés en algún momento, preguntó por productos, dijo 'sí', eligió "
    "uno por nombre, preguntó precio/envío, dio datos personales, o pidió "
    "fotos. Esto programa remarketing automático y es la decisión segura. "
    "Si el cliente al menos saludó y pidió ver catálogo, eso ya es INTERESADO, "
    "salvo que después haya quedado claro que lo que busca NO lo vendemos "
    "(ver RECHAZO).\n"
    "- `RECHAZO` (cierre definitivo sin venta: NO habrá remarketing, "
    "reactivarlo sería spam). Aplica si es OBVIO que no hay venta posible: "
    "(a) el usuario explícitamente dijo 'no me interesa', 'no gracias', 'no "
    "compro'; (b) solo mandó spam/insultos/emojis sueltos sin engagement "
    "real; (c) pidió algo que NO vendemos (materia prima como la cera, "
    "mayoreo, otro rubro) y tú ya se lo aclaraste; (d) su duda quedó "
    "resuelta y se despidió ('gracias', 'listo', 'ok') SIN haber elegido un "
    "producto ni dejar una pregunta de compra pendiente. Caso real (run "
    "dc32f7fe): pidió 'precio de la cera', le explicaste que no se vende "
    "aparte, respondió 'Gracias' y se fue: eso es RECHAZO con motivo "
    "'buscaba cera, no la vendemos', NO INTERESADO. Si preguntó por un "
    "producto nuestro y quedó pensándolo, es INTERESADO. Si NO estás seguro "
    "entre (c)/(d) e interés real, usa INTERESADO.\n"
    "- `COMPRA_EXITOSA`: el cliente ya confirmó pedido completo con datos de "
    "envío + método de pago Y tú ya llamaste `register_order(...)` Y la "
    "tool devolvió `registered=true` (Medusa aceptó). Si la tool devolvió "
    "`registered=false`, NO uses COMPRA_EXITOSA, el pedido NO está "
    "formalmente cerrado. Si Medusa lo rechazó (sin `error`), escala con "
    "`escalate_to_human(reason_category='ORDER_REGISTRATION_FAILED')`. "
    "Si fue un rechazo de validación (con `error`, p. ej. faltaba quien "
    "recibe) y el cliente se fue, es `INTERESADO` con ese `error` en el "
    "motivo: remarketing le pide el dato que falta. "
    "Esta tag solo aplica si la conversación cerró limpia con venta "
    "registrada en Medusa. Si te llegó ghosting acá, lo más probable es "
    "que YA hayas etiquetado COMPRA_EXITOSA antes; en ese caso re-confirma "
    "con la misma tag.\n\n"
    "**REGLA DE ORO**: NO generes ninguna respuesta visible al usuario. SOLO "
    "llama la(s) herramienta(s) en silencio y termina. No mandes "
    "`customer_message`: el cliente ya no está en la conversación."
)


def build_ghosting_prompt() -> str:
    """Trigger inyectado al LLM cuando el usuario lleva mucho rato sin responder."""
    return _GHOSTING_PROMPT


_DECIDED_GHOSTING_PROMPT = (
    "[SISTEMA]: El usuario dejó de responder (ghosting). La lectura de la "
    "conversación ya decidió cómo cerrarla: usa OBLIGATORIAMENTE "
    "`manage_conversation_tag` con tag=`{tag}` y un motivo breve de una línea "
    "que diga qué pasó en la conversación."
)
_DECIDED_CONFIRMADO_SIN_DATOS = (
    " Después llama también `escalate_to_human(reason_category="
    "'ORDER_PENDING_SHIPPING_DETAILS', summary='Cliente confirmó pedido X por "
    "valor $Y pero no completó los datos de envío')` para que un colega del "
    "equipo termine el pedido."
)
_GHOSTING_GOLDEN_RULE = (
    "\n\n**REGLA DE ORO**: NO generes ninguna respuesta visible al usuario. SOLO "
    "llama la(s) herramienta(s) en silencio y termina. No mandes "
    "`customer_message`: el cliente ya no está en la conversación."
)


def build_decided_ghosting_prompt(tag: str) -> str:
    """Aviso de ghosting cuando el motor de decisiones ya eligió la etiqueta
    (F8, workflow V2): el LLM solo ejecuta las tools del cierre."""
    prompt = _DECIDED_GHOSTING_PROMPT.format(tag=tag)
    if tag == "CONFIRMADO_SIN_DATOS":
        prompt += _DECIDED_CONFIRMADO_SIN_DATOS
    return prompt + _GHOSTING_GOLDEN_RULE

