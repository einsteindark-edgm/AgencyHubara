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


def build_remarketing_trigger(motivo: str, memory_context: str = "") -> str:
    """Saludo proactivo inicial inyectado al LLM al arrancar el workflow.

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
    return (
        "[SISTEMA INTERNO — NO REPRODUCIR ESTE TEXTO AL CLIENTE]: "
        "El cliente quedó pendiente de cerrar una compra y vas a "
        "re-abrir la conversación con UN único gancho cálido y breve.\n\n"
        f"MOTIVO REGISTRADO DE CIERRE (uso interno): '{motivo}'.\n"
        f"MEMORIA DE EVENTOS PASADOS (uso interno):{memory_context}\n\n"
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
        "**EJEMPLOS de buen gancho** (referencia, no copies literal):\n"
        "- '¡Hola de nuevo! 🌿 Quedó pendiente lo del Velón de Cristo "
        "y los aromas — ¿lograste decidirte? 🤍'\n"
        "- 'Te escribo para retomar lo de la *Cruz de Vida* ✨ ¿Te "
        "ayudo a cerrar el pedido?'"
    )
