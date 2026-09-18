"""Detector determinista de texto administrativo en el path de send.

Incidente origen (run 5f43bcd0, evento 783, 2026-08-13): el cliente recibió
por WhatsApp "La conversación queda etiquetada como `INTERESADO`. El cliente
eligió su *Duo Zodiacal Leo*... Remarketing automático activado. 🤍" — el
resumen interno del cierre por ghosting. El premortem (D1) mostró que la
misma clase puede salir en turnos NORMALES (el LLM regurgita el `message` de
una tool: "Éxito. Interacción etiquetada como 'INTERESADO'.") sin que ningún
flag lo suprima.

`looks_like_admin_leak` es la última línea determinista: patrones que un
mensaje legítimo de venta esencialmente nunca contiene. Cada positivo de
esta suite es un leak REAL (histórico) o un envelope de tool vivo en el
código; cada negativo es un mensaje de venta plausible que NO debe
bloquearse (el costo de un falso positivo es un cliente sin respuesta).
"""
from __future__ import annotations

import pytest

from src.platform.llm_text_sanitizer import looks_like_admin_leak


# --- Positivos: leaks reales e inventario de envelopes internos -------------

LEAKS = [
    # Run 5f43bcd0 evento 783 — el incidente que motivó el detector.
    (
        "La conversación queda etiquetada como `INTERESADO`. El cliente "
        "eligió su *Duo Zodiacal Leo* en *Amarillo* y estaba por escoger "
        "el aroma cuando se retiró. Remarketing automático activado. 🤍"
    ),
    # Run 5f43bcd0 llm 753 — deliberación junto a la tool call.
    (
        "El cliente está en plena selección de variantes: eligió producto "
        "(Duo Zodiacal Leo), color (Amarillo) y estaba viendo los aromas "
        "cuando ghosteó. Alto interés, remarketing aplica."
    ),
    # Envelope de ManageConversationTagTool regurgitado (premortem D1).
    "Éxito. Interacción etiquetada como 'INTERESADO'.",
    # L-12 (run 3607aecc) — envelope de routing regurgitado.
    "El control ha sido transferido al agente de ventas.",
    # Incidente wa_573125671604 (2026-07-17) — deliberación de declinación.
    "No hay mensaje nuevo del cliente, así que no genero respuesta.",
    "No genero un nuevo mensaje: el cliente ya completó su compra.",
    # Marker de sistema filtrado tal cual.
    "[SISTEMA]: cierre de episodio por inactividad.",
    # Tag token estilo interno (ALL_CAPS con underscore), sin backticks.
    "Cierro con CONFIRMADO_PAGO_PENDIENTE y escalo a humano.",
    # Envelope de escalación regurgitado.
    "Escalación registrada. Un humano tomará el caso en breve.",
    # Vocabulario de infraestructura que jamás va en un mensaje de venta.
    "El workflow de remarketing queda programado para mañana.",
    "Detecté ghosting, aplico el cierre automático.",
    "Handoff a ventas completado, retomo la conversación.",
    # Run 1c9ef231 (2026-08-19) — narración de proceso junto a
    # present_products, enviada al cliente como burbuja. "al cliente" en
    # posición NO inicial: el ancla ^ del patrón de tercera persona no la
    # veía.
    "Encontré 10 velas religiosas. Las muestro al cliente.",
    # Run 5ed9af2d (2026-09-18) — acuse al sistema tras `escalate_to_human`,
    # enviado al cliente. "la conversación" en posición NO inicial: el
    # "Listo, " evadió el ancla ^ (misma evasión que el caso de arriba). El
    # fix primario es estructural (la escalación termina el turno); esto es
    # el tripwire por si otro camino vuelve a producir el acuse.
    "Listo, la conversación quedó en manos del equipo humano.",
    "Hecho. La conversación pasó al equipo.",
    # Run b06636a6 (2026-09-18) — acuse tras `manage_conversation_tag`. No
    # salió solo porque el turno era de cierre (admin); en un turno normal
    # ningún patrón lo frenaba.
    "Etiqueta registrada.",
    "Etiqueta INTERESADO registrada.",
    # Mismo molde parafraseado (el sujeto cambia, el complemento de relevo no).
    "Listo, el caso quedó en manos del equipo.",
    "Conversación transferida al equipo.",
    # Misma clase: referencia al cliente en tercera persona a mitad de texto.
    "Le muestro al cliente las opciones disponibles del catálogo.",
    # Deliberación de cierre del mismo run (llm 17:27) — "Etiqueté como"
    # primera persona + tag single-word CAPS (sin underscore ni backticks,
    # los patrones de token no la matchean).
    "El cliente pidió ver los productos y dejó de responder. Etiqueté "
    "como INTERESADO y programé el seguimiento.",
    "Etiqueté como INTERESADO.",
]


@pytest.mark.parametrize("text", LEAKS)
def test_admin_text_is_detected(text: str) -> None:
    assert looks_like_admin_leak(text), f"debió detectarse como admin: {text!r}"


# --- Negativos: mensajes de venta plausibles (falso positivo = cliente mudo)

LEGIT = [
    "¡Hola! Bienvenido a *Hubara*, velas artesanales de cera de palma. "
    "¿Qué buscas hoy?",
    "¿Sigues interesado en el *Duo Zodiacal Leo* en Amarillo? Nos quedan "
    "pocas unidades.",
    "Tu pedido quedó registrado. Te contactamos para coordinar la entrega.",
    "El *Duo Zodiacal* viene en amarillo, rosa y blanco. ¿Cuál te gusta?",
    "Perfecto, lo dejamos etiquetado como regalo con el nombre de Ana.",
    # La despedida del relevo es voz de venta legítima (NO es reporte
    # interno). Antes este caso decía "un asesor humano te escribe…": eso
    # rompe la persona (run 5ed9af2d) y lo audita `breaks_human_persona`
    # (tests/platform/test_persona_guard.py), no este detector.
    "Listo, un colega del equipo te escribe por este mismo chat.",
    "Va perfecto para quien cumple el 3 de agosto: Leo. 🤍",
    "Ok",
    "",
    # Segunda persona legítima — la voz de venta correcta para el mismo caso
    # del run 1c9ef231. NO debe confundirse con la tercera persona.
    "Te muestro nuestras velas religiosas 🤍 ¿Cuál te llama la atención?",
    # Colocaciones comerciales legítimas de "cliente" que NO son reporte
    # interno en tercera persona.
    "Nuestro equipo de atención al cliente está pendiente de tu pedido.",
    "Ofrecemos servicio al cliente todos los días de la semana.",
    "Es una de las favoritas de nuestros clientes. 🕯️",
    # Gift-tagging legítimo (ya cubierto arriba con "etiquetado"): la forma
    # conjugada de cortesía tampoco debe caer.
    "¿Quieres que lo etiquetemos como regalo?",
    # Falsos positivos de la 1ª versión de los patrones V2 (run 5ed9af2d),
    # cazados en revisión: "la conversación quedó…" SIN complemento de relevo
    # es voz de venta (el propio workspace de remarketing siembra "Quedó
    # pendiente…"), y "etiqueta aplicada/guardada" dentro de una frase es
    # gift-tagging. Un bloqueo acá = cliente sin respuesta / gancho suprimido.
    "¡Hola de nuevo! Vi que la conversación quedó a medias, ¿retomamos? 🤍",
    "Vi que la conversación quedó pendiente con lo del aroma, ¿te decidiste?",
    "Tranquilo, la conversación está guardada, retomamos cuando quieras.",
    # Voz dirigida al cliente ("tu caso"), no reporte en tercera persona.
    "Tu caso quedó a cargo de un colega del equipo, te responde por aquí 🤍",
    "Va con la etiqueta aplicada a mano y tu dedicatoria.",
    "Queda la etiqueta guardada con el nombre de Ana 🤍",
]


@pytest.mark.parametrize("text", LEGIT)
def test_legit_sales_text_is_not_flagged(text: str) -> None:
    assert not looks_like_admin_leak(text), (
        f"falso positivo sobre mensaje legítimo: {text!r}"
    )


def test_none_is_not_flagged() -> None:
    assert not looks_like_admin_leak(None)


# --- El set de patrones es LÓGICA DE WORKFLOW (replay) -----------------------
# `looks_like_admin_leak` corre DENTRO de los workflows de Sales/Remarketing y
# su veredicto decide si se agenda `send_whatsapp_message_activity`. Ampliar
# los patrones cambia los commands de una history ya grabada: la history real
# del run 5ed9af2d (que SÍ envió el acuse) dejaba de replayear —
# NondeterminismError 'send_whatsapp_message_activity' vs
# 'flush_pending_ui_intents_activity'— apenas se agregó el patrón que lo caza
# (lo detectó tests/test_replay_sales.py::test_prepatch_escalation_history_
# still_replays). Por eso los patrones posteriores al set original viven en un
# set EXTENDIDO que los workflows activan con
# `workflow.patched("admin-leak-patterns-v2")`; activities, tools y evals (no
# se replayean) usan siempre el extendido.

_POST_V1_LEAKS = [
    "Listo, la conversación quedó en manos del equipo humano.",
    "Etiqueta registrada.",
]


@pytest.mark.parametrize("text", _POST_V1_LEAKS)
def test_frozen_v1_set_ignores_later_patterns_for_replay(text: str) -> None:
    assert looks_like_admin_leak(text) is True
    assert looks_like_admin_leak(text, extended=False) is False


def test_frozen_v1_set_still_catches_the_original_leaks() -> None:
    assert looks_like_admin_leak(
        "Éxito. Interacción etiquetada como 'INTERESADO'.", extended=False
    )


def test_original_pattern_set_is_frozen() -> None:
    """El set ORIGINAL es lógica de replay: NO se edita en sitio. Si este test
    falla porque agregaste/cambiaste un patrón en `_ADMIN_LEAK_PATTERNS`,
    deshazlo y ponlo en `_ADMIN_LEAK_PATTERNS_V2` (o en un set v3 con su
    propio `workflow.patched`): un patrón nuevo cambia el veredicto sobre
    textos que histories en vuelo YA enviaron → NondeterminismError al
    replayear (pasó con la history real del run 5ed9af2d). Se puede recalcular
    el hash SOLO junto con un `deprecate_patch` que retire el set viejo."""
    import hashlib
    import re

    from src.platform import llm_text_sanitizer as sanitizer

    digest = hashlib.sha256(
        "\n".join(
            f"{p.pattern}|{bool(p.flags & re.IGNORECASE)}"
            for p in sanitizer._ADMIN_LEAK_PATTERNS
        ).encode()
    ).hexdigest()
    assert digest == "48a125b37610cb8ad93ddbe3e4241c793104b184364703725d5c5bad567781d9"
