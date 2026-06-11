"""Rúbrica del guion de ventas — datos puros derivados de `workspace/skills/sales_script/SKILL.md`.

Separa los INVARIANTES TESTEABLES del guion (regex, blocklists, allowlists, y los
`evaluation_steps` del juez) de la lógica de las métricas (`metrics.py`). Así los
tests pueden afirmar sobre la rúbrica y un cambio del guion se refleja en un solo
lugar.

Fuente: el funnel de 6 fases (Apertura → Descubrimiento → Recomendación →
Objeciones → Cierre → Despedida) + las 13 reglas globales del SKILL.md.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Apertura (Fase 1) — el saludo es lo primero que el operador quiere testear.
# ---------------------------------------------------------------------------

# Saludo por hora de Colombia (tolerante a mayúsculas/acentos faltantes).
GREETING_RE = re.compile(
    r"buen[oa]s?\s+(d[ií]as|tardes|noches)", re.IGNORECASE
)
BRAND_RE = re.compile(r"hubara", re.IGNORECASE)

# Aperturas PROHIBIDAS por el guion (rioplatense / informales).
FORBIDDEN_OPENERS = [
    re.compile(r"¡?\s*hola\b", re.IGNORECASE),  # "¡Hola!" / "hola"
    re.compile(r"\bhey\b", re.IGNORECASE),
    re.compile(r"\bbuen\s+d[ií]a\b", re.IGNORECASE),  # "Buen día" (≠ "Buenos días")
]

# ---------------------------------------------------------------------------
# Estilo (reglas globales) — voseo, em dash, emoji, cierres prohibidos.
# ---------------------------------------------------------------------------

# Marcadores de voseo rioplatense / argentinismos (alta precisión, word-boundary).
VOSEO_TOKENS = [
    "vos", "tenés", "querés", "podés", "sabés", "decime", "fijate", "mirá",
    "dale", "joya", "bárbaro", "bárbara", "che", "boludo", "posta",
]
VOSEO_RES = [re.compile(rf"(?<![\w]){re.escape(t)}(?![\w])", re.IGNORECASE) for t in VOSEO_TOKENS]

# Efusividad rioplatense de cierre.
EFFUSIVE_RES = [
    re.compile(r"¡?\s*listo+!", re.IGNORECASE),       # "¡Listoooo!"
    re.compile(r"¡?\s*s[úu]per!?", re.IGNORECASE),    # "¡Súper!"
    re.compile(r"¡?\s*genial!", re.IGNORECASE),       # "¡Genial!"
]

# Em dash (—) y en dash (–) — prohibidos en respuestas al cliente.
DASH_RE = re.compile(r"[—–]")

# Emojis permitidos (allowlist del guion). Cualquier otro emoji = violación.
ALLOWED_EMOJIS = {"🤍", "✨", "🕯️", "🕯", "🌿"}
# Rango amplio de emojis para detectar los NO permitidos / contar por burbuja.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF✀-➿☀-⛿]"
)

# Cierres / frases PROHIBIDAS (afirman compra/pago no verificado, o suenan a sistema).
FORBIDDEN_CLOSINGS = [
    re.compile(r"gracias por tu compra", re.IGNORECASE),
    re.compile(r"compra realizada", re.IGNORECASE),
    re.compile(r"compra exitosa", re.IGNORECASE),
    re.compile(r"tu pago fue procesado", re.IGNORECASE),
    re.compile(r"pago procesado", re.IGNORECASE),
    re.compile(r"te confirmo en un rato", re.IGNORECASE),
    re.compile(r"te cuadro el pedido", re.IGNORECASE),
    re.compile(r"ah[ií] te dej[oé]", re.IGNORECASE),
    re.compile(r"conversaci[óo]n\s+(queda\s+)?cerrada", re.IGNORECASE),
    re.compile(r"caso cerrado", re.IGNORECASE),
    re.compile(r"pedido transferido", re.IGNORECASE),
]


def find_emojis(text: str) -> list[str]:
    """Lista de emojis presentes en `text` (para chequear allowlist + conteo)."""
    return _EMOJI_RE.findall(text or "")


def disallowed_emojis(text: str) -> list[str]:
    """Emojis presentes que NO están en la allowlist del guion."""
    return [e for e in find_emojis(text) if e not in ALLOWED_EMOJIS]


# ---------------------------------------------------------------------------
# Contexto del guion + criterios para el juez LLM (ConversationalGEval).
# ---------------------------------------------------------------------------

# Resumen condensado del funnel — se pasa como `context` del test case para que
# el juez tenga el guion a la vista sin inyectar el SKILL.md entero (200 líneas).
SCRIPT_CONTEXT = (
    "GUION DE VENTAS HUBARA (funnel de 6 fases). "
    "1) Apertura: saludo por hora de Colombia (Buenos días/tardes/noches) + marca "
    "'Hubara' + propuesta de valor (velas artesanales de cera de palma) + ofrecer "
    "catálogo (botón 'Ver catálogo'). NUNCA '¡Hola!'/'Hey'/'Buen día'. "
    "2) Descubrimiento: UNA pregunta por turno (estilo SPIN: ¿para ti o regalo?, "
    "¿qué aroma/momento/color?, ¿qué espacio?) antes de recomendar; si el cliente "
    "ya dio intención clara, saltar a recomendar; si menciona evento corporativo, "
    "escalar. 3) Recomendación: search_products → present_product_detail/present_products; "
    "solo productos/precios/aromas que vinieron en un tool_result (cero alucinación). "
    "4) Objeciones: tono sereno; escalar a humano en descuentos, mayoreo/B2B, evento, "
    "salud/seguridad, pedido explícito de humano, internacional. 5) Cierre (BANT): "
    "request_shipping_details → verify_order_for_checkout → present_order_confirmation "
    "→ register_order → manage_conversation_tag('CONFIRMADO_PAGO_PENDIENTE') + "
    "escalate_to_human('PAYMENT_VERIFICATION_PENDING') + UN solo mensaje de cierre. "
    "NUNCA marcar 'COMPRA_EXITOSA' directo. NO un segundo mensaje tras el cierre. "
    "Reglas globales: tuteo colombiano (cero voseo rioplatense), cero em dash, "
    "máx 1 emoji por burbuja (allowlist 🤍✨🕯️🌿), no inventar datos, no prometer "
    "tiempos futuros, no revelar que es IA, no repreguntar datos ya dados."
)

SCRIPT_ADHERENCE_STEPS = [
    "Verifica si el PRIMER mensaje del asistente en la sesión abre con saludo por "
    "hora de Colombia (Buenos días/tardes/noches) + la marca 'Hubara' + propuesta "
    "de valor, y NO con '¡Hola!'/'Hey'/'Buen día'.",
    "Verifica si el asistente descubrió la intención del cliente (una pregunta por "
    "turno, estilo SPIN) antes de recomendar, salvo que el cliente ya hubiera dado "
    "intención clara.",
    "Verifica si al recomendar usó tools (search_products y luego present_*) y solo "
    "afirmó productos/precios/aromas presentes en un tool_result (penaliza fuerte "
    "cualquier dato inventado).",
    "Verifica si manejó objeciones con tono sereno y escaló a humano en los "
    "disparadores correctos (descuentos, mayoreo/B2B, evento, salud, pedido "
    "explícito de humano, internacional).",
    "Verifica si en el cierre siguió la secuencia canónica (request_shipping_details "
    "→ verify_order_for_checkout → present_order_confirmation → register_order → "
    "CONFIRMADO_PAGO_PENDIENTE + escalación PAYMENT_VERIFICATION_PENDING) con UN solo "
    "mensaje de cierre y SIN marcar 'COMPRA_EXITOSA' directo. Las tool calls del "
    "cierre pueden no estar registradas en el transcript: si los HECHOS VERIFICADOS "
    "DEL SISTEMA del Scenario registran la orden/etiqueta/escalación, considera esa "
    "parte de la secuencia CUMPLIDA.",
    "Penaliza fuerte: inventar datos, voseo rioplatense, prometer tiempos futuros, "
    "revelar que es una IA, repreguntar datos que el cliente ya dio, o mandar un "
    "segundo mensaje después del cierre.",
]

PROACTIVE_OFFERING_STEPS = [
    "Verifica si en la apertura el asistente ofreció el catálogo (botón 'Ver "
    "catálogo' / send_quick_replies) o un siguiente paso claro.",
    "Verifica si cuando el cliente mostró interés, el asistente presentó productos "
    "relevantes con las tools de UI en vez de quedarse pasivo.",
    "Verifica si invitó a elegir o avanzar ('¿Te interesa esta?', '¿Quieres ver "
    "más?') en lugar de cerrar la conversación prematuramente.",
    "EXCEPCIÓN: si el cliente pidió hablar con un humano, un descuento o "
    "mayoreo/B2B y el asistente escaló correctamente, NO penalices la ausencia de "
    "oferta de productos — escalar es lo correcto en ese escenario.",
    "Penaliza respuestas pasivas que no ofrecen catálogo, productos ni un siguiente "
    "paso cuando el contexto lo pedía Y NO era un escenario de escalación.",
]

NO_HALLUCINATION_STEPS = [
    "Si el Scenario incluye un CATÁLOGO REAL VIGENTE, esa es la fuente de verdad: "
    "contrasta CADA producto, precio, aroma, color y CONTEO de opciones ('14 "
    "aromas', '10 colores') que el asistente afirmó contra esas listas cerradas. "
    "Afirmar una opción o un conteo que NO está en el catálogo es alucinación "
    "grave, aunque haya tool calls registradas.",
    "Verifica que el asistente usó tools (search_products / get_product_by_handle) "
    "antes de afirmar datos de catálogo. OJO: el transcript puede NO registrar "
    "todas las tool calls (limitación de persistencia) — NO penalices 'no llamó "
    "tools' como única razón si lo afirmado coincide exactamente con el catálogo "
    "real del Scenario; penaliza las AFIRMACIONES que lo contradicen.",
    "Penaliza nombrar productos/precios que no existen, inventar descuentos, o "
    "prometer tiempos de envío exactos sin base.",
]

CONVERSION_PROGRESS_STEPS = [
    "Verifica si la conversación avanzó hacia el cierre (cotización, recolección de "
    "datos de envío, registro de la orden) cuando el cliente mostró intención de "
    "compra.",
    "Verifica que el avance fue sin presionar al cliente que dijo 'lo pienso' (en "
    "ese caso corresponde marcar INTERESADO, no insistir).",
    "Penaliza quedarse en loop sin avanzar, o presionar de forma agresiva.",
]

CORRECT_HANDOFF_STEPS = [
    "Verifica si el asistente escaló a humano (escalate_to_human) en los "
    "disparadores correctos: pedido de descuento, mayoreo/B2B, evento corporativo, "
    "seguridad/salud, pedido explícito de hablar con una persona, envío "
    "internacional, y tras register_order (PAYMENT_VERIFICATION_PENDING).",
    "IMPORTANTE: las tools del cierre (manage_conversation_tag, escalate_to_human) "
    "corren en turnos que el transcript puede NO registrar. Si el Scenario trae "
    "HECHOS VERIFICADOS DEL SISTEMA que registran la escalación, la etiqueta o la "
    "red de seguridad del workflow, DA LA ESCALACIÓN POR OCURRIDA — esos hechos "
    "son del runtime, más confiables que la ausencia de tool calls en el "
    "transcript. No penalices 'no escaló' si el sistema registró la escalación.",
    "Verifica que NO escaló innecesariamente consultas normales que podía resolver "
    "por sí mismo.",
    "Penaliza no escalar cuando el guion lo exige (sin evidencia en transcript NI "
    "en hechos del sistema), o escalar de más.",
]
