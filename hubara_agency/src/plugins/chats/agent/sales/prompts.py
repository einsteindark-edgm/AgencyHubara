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
    "- `CONFIRMADO_SIN_DATOS` (caso edge MUY importante, sesión c4e3416f): el "
    "cliente ya confirmó la compra (apretó '✅ Confirmar' tras "
    "`present_order_confirmation`) PERO NO completó los datos de envío "
    "(ni respondió el `nfm_reply` del Flow, ni mandó ciudad/barrio/"
    "dirección/teléfono/pago por texto). Es decir: el último intento del "
    "agente fue pedir datos de envío y nunca llegaron. **En este caso usa "
    "esta tag Y llama también `escalate_to_human(reason_category="
    "'ORDER_PENDING_SHIPPING_DETAILS', summary='Cliente confirmó pedido X "
    "por valor $Y pero no completó los datos de envío')` para que un "
    "humano cierre la operación pidiendo los datos faltantes**. NO uses "
    "INTERESADO para este caso, la venta YA está casi cerrada, no es "
    "remarketing genérico.\n"
    "- `INTERESADO` (DEFAULT cuando hay duda): el usuario mostró ALGUNA señal "
    "de interés en algún momento, preguntó por productos, dijo 'sí', eligió "
    "uno por nombre, preguntó precio/envío, dio datos personales, o pidió "
    "fotos. Esto programa remarketing automático y es la decisión segura. "
    "Si el cliente al menos saludó y pidió ver catálogo, eso ya es INTERESADO.\n"
    "- `RECHAZO` (solo si es OBVIO): el usuario explícitamente dijo 'no me "
    "interesa', 'no gracias', 'no compro', o solo mandó spam/insultos/emojis "
    "sueltos sin engagement real. Si NO estás 100% seguro, usa INTERESADO.\n"
    "- `COMPRA_EXITOSA`: el cliente ya confirmó pedido completo con datos de "
    "envío + método de pago Y tú ya llamaste `register_order(...)` Y la "
    "tool devolvió `registered=true` (Medusa aceptó). Si la tool devolvió "
    "`registered=false`, NO uses COMPRA_EXITOSA, el pedido NO está "
    "formalmente cerrado: tienes que escalar con "
    "`escalate_to_human(reason_category='ORDER_REGISTRATION_FAILED')`. "
    "Esta tag solo aplica si la conversación cerró limpia con venta "
    "registrada en Medusa. Si te llegó ghosting acá, lo más probable es "
    "que YA hayas etiquetado COMPRA_EXITOSA antes; en ese caso re-confirma "
    "con la misma tag.\n\n"
    "**REGLA DE ORO**: NO generes ninguna respuesta visible al usuario. SOLO "
    "llama la(s) herramienta(s) en silencio y termina. Para "
    "CONFIRMADO_SIN_DATOS llamas DOS tools: primero `manage_conversation_tag` "
    "con la tag, después `escalate_to_human` con la razón."
)


def build_ghosting_prompt() -> str:
    """Trigger inyectado al LLM cuando el usuario lleva mucho rato sin responder."""
    return _GHOSTING_PROMPT
