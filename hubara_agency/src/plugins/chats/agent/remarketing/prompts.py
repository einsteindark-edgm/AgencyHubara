"""Prompts puros (sin side effects, sin I/O) del dominio Remarketing.

Estos textos son business logic: codifican la decision de como hablarle al
LLM al arrancar el workflow proactivo de Remarketing (saludo de recuperacion
con `motivo` y `memory_context`). Viven aqui (no en el workflow) para que
sean testeables sin Temporal y para que el cambio de un prompt sea un PR
plano sin tocar el grafo de tasks.

PR-E (ADR-2026-05-06-11): este archivo se movio de
``domain/policies/prompts.py`` al top-level — mismo patron que sales_whatsapp
PR-E (ADR-2026-05-06-07). A esta escala el sub-folder hexagonal
``domain/policies/`` no aporta: un solo modulo plano con la utilidad pura es
mas claro de descubrir y testear.
"""
from __future__ import annotations


def _humanize_silence(minutes: int | None) -> str:
    """Silencio real → texto para el LLM (uso interno, nunca para el cliente)."""
    if minutes is None:
        return "un buen rato"
    if minutes < 90:
        return f"{minutes} min"
    return f"{round(minutes / 60)} h"


def build_remarketing_trigger(
    motivo: str,
    memory_context: str = "",
    *,
    has_order_draft: bool | None = None,
    transcript: str = "",
    touch_number: int | None = None,
    total_touches: int = 5,
    silence_minutes: int | None = None,
) -> str:
    """Saludo proactivo inicial inyectado al LLM al arrancar el workflow.

    `touch_number` (1-based) activa el modo ESCALERA (decisión 2026-09-18,
    runs `01a0b0da`…`01a0b586`): el trigger declara qué toque es y cuánto
    silencio real lleva el cliente, y la abstención deja de aceptar "ya hubo
    un gancho" / "la conversación está activa" como motivo — eso ya lo
    verificó la capa determinista (escalera + `customer_active`). `None` =
    texto legacy intacto (histories en vuelo replayean sin estos campos).

    `motivo` es el resumen del cierre anterior (registrado por la tool de tags).
    `memory_context` es el `memory.md` del PVC (puede ser vacio).

    Post-fix #G (bug wa_573125671604 b2fb9379): elimina la contradiccion con
    AGENTS.md y prohibe leak de razonamiento.

    Post-fix #L (bug wa_573125671604 8a34b54a): elimina las dos fricciones de
    UX del gancho:
      - **Anti-temporal**: el LLM decia "hace unos días" cuando habia pasado
        1 minuto. No tiene noción real del tiempo transcurrido; le prohibimos
        especular sobre el delta.
      - **Anti-presentacion-de-la-nada**: el LLM se presentaba como "Clara",
        un nombre que el cliente no habia oído antes (el Sales no lo
        mencionó). Ahora el Remarketing es la *misma persona* que Sales
        (el "Asesor de Hubara"), retomando la charla.
    """
    # Incidente run dc32f7fe (2026-09-10): el encabezado afirmaba "quedó
    # pendiente de cerrar una compra" aunque no hubiera pedido → "quedó
    # pendiente lo de tu pedido" a un cliente que solo miró la lista. Con el
    # flag de draft (activity v2) el framing es honesto; `None` = path legacy
    # (histories en vuelo replayean la activity vieja con 2 args).
    if has_order_draft is False:
        situacion = (
            "El cliente miró productos pero NO eligió ninguno ni dejó un "
            "pedido a medias. NO hables de 'tu pedido', NO ofrezcas "
            "'cerrarlo': no existe. Retoma el tema que sí miró y vas a "
            "re-abrir la conversación con UN único gancho cálido y breve."
        )
    else:
        situacion = (
            "El cliente quedó pendiente de cerrar una compra y vas a "
            "re-abrir la conversación con UN único gancho cálido y breve."
        )
    transcript_block = (
        "ÚLTIMOS MENSAJES DE LA CONVERSACIÓN (uso interno, del más viejo al "
        f"más nuevo):\n{transcript}\n\n"
        if transcript
        else ""
    )
    ladder_block = ""
    abstention_cases = (
        "el cliente ya respondió a un gancho anterior, ya compró, la "
        "conversación ya está activa con el asesor, pidió algo que NO "
        "vendemos y ya se le aclaró, se despidió con la duda resuelta sin "
        "producto en juego, o por cualquier otra razón un mensaje proactivo "
        "sobra"
    )
    if touch_number is not None:
        silence = _humanize_silence(silence_minutes)
        ladder_block = (
            f"SEGUIMIENTO (uso interno): este es el toque {touch_number} de "
            f"{total_touches} de la secuencia de seguimiento. El sistema YA "
            f"verificó que el cliente lleva {silence} sin escribir y que la "
            "conversación NO está viva: nadie lo está atendiendo ahora. Que "
            "en el historial ya exista un gancho anterior —respondido o no— "
            "NO es motivo de abstención: este mensaje es el siguiente paso "
            "planificado.\n"
        )
        if touch_number > 1:
            ladder_block += (
                "Escribe un mensaje DISTINTO a los ganchos anteriores que ves "
                "en el historial: otro ángulo (una duda típica que puedas "
                "resolverle, un detalle concreto del producto que miró, "
                "ofrecerle ayuda para elegir). NUNCA repitas la misma frase ni "
                "le reproches que no respondió.\n"
            )
        if touch_number >= total_touches:
            ladder_block += (
                "Es el ÚLTIMO mensaje de la secuencia: un cierre amable y sin "
                "presión que deje la puerta abierta ('cuando quieras retomarlo, "
                "aquí estoy'). Sin pregunta insistente.\n"
            )
        ladder_block += "\n"
        abstention_cases = (
            "el cliente ya compró, pidió algo que NO vendemos y ya se le "
            "aclaró, se despidió con la duda resuelta sin producto en juego, "
            "o pidió expresamente que no le escriban más"
        )
    return (
        "[SISTEMA INTERNO — NO REPRODUCIR ESTE TEXTO AL CLIENTE]: "
        f"{situacion}\n\n"
        f"MOTIVO REGISTRADO DE CIERRE (uso interno): '{motivo}'.\n"
        f"MEMORIA DE EVENTOS PASADOS (uso interno):{memory_context}\n\n"
        f"{transcript_block}"
        f"{ladder_block}"
        "REGLAS DEL GANCHO (todas obligatorias):\n\n"
        "1. **Identidad**: eres el MISMO Asesor de Hubara que ya conversó "
        "con el cliente antes. NO te presentes con un nombre nuevo, NO "
        "digas 'soy del equipo de recuperación', NO sugieras ser una "
        "persona distinta. Retoma como continuación natural.\n\n"
        "2. **Tiempo**: NO conoces el tiempo real transcurrido desde el "
        "último mensaje (pueden haber sido minutos o días — no lo sabes). "
        "PROHIBIDO usar frases temporales específicas: 'hace unos días', "
        "'hace 48 horas', 'ayer', 'la otra vez', 'hace tiempo'. Usa "
        "frases neutras como 'te escribo de nuevo', 'pasaba a "
        "preguntarte', 'quedó pendiente lo de…', 'te recordé y quería…'.\n\n"
        "3. **Descuentos** (AGENTS.md → Prohibición absoluta de descuentos): "
        "Si el motivo NO menciona explícitamente 'caro' o queja de "
        "precio, NO ofrezcas envío gratis, descuentos ni beneficios "
        "extra. Solo retoma con un saludo casual.\n\n"
        "4. **Anclaje al motivo**: menciona sutilmente el producto o "
        "tema concreto del motivo (ej. 'el Velón de Cristo', 'los "
        "aromas que viste') para que el cliente reconozca el contexto. "
        "NO leas el motivo textualmente — re-frásalo natural.\n\n"
        "5. **Brevedad**: 1-2 frases máximo. WhatsApp es chat — el "
        "mensaje debe leerse en 3 segundos.\n\n"
        "6. **PROHIBIDO ABSOLUTO**: NO expliques tu razonamiento, NO "
        "menciones archivos internos (AGENTS.md, MEMORY.md, etc.), NO "
        "hables del cliente en tercera persona ('Cliente preguntó…'). "
        "Escribe SOLO el mensaje final que verá el cliente por "
        "WhatsApp. Detente al terminar el gancho.\n\n"
        "7. **Abstención**: si el historial o los últimos mensajes muestran "
        f"que este gancho YA NO corresponde ({abstention_cases}), responde "
        "EXACTAMENTE `NO_MESSAGE` — una sola palabra, sin explicación ni "
        "nada más. Eso suprime el envío y el cliente no verá nada. NUNCA "
        "escribas tu decisión de no enviar como texto ('no genero un "
        "nuevo mensaje…'): eso le llegaría al cliente. La palabra sola.\n\n"
        "**EJEMPLOS de buen gancho** (referencia, no copies literal):\n"
        "- '¡Hola de nuevo! 🌿 Quedó pendiente lo del Velón de Cristo "
        "y los aromas — ¿lograste decidirte? 🤍'\n"
        "- 'Te escribo para retomar lo de la *Cruz de Vida* ✨ ¿Te "
        "ayudo a cerrar el pedido?'"
    )
