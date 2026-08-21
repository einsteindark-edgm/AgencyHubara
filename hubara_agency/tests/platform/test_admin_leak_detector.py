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
    "Listo, un asesor humano te escribe en unos minutos.",
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
]


@pytest.mark.parametrize("text", LEGIT)
def test_legit_sales_text_is_not_flagged(text: str) -> None:
    assert not looks_like_admin_leak(text), (
        f"falso positivo sobre mensaje legítimo: {text!r}"
    )


def test_none_is_not_flagged() -> None:
    assert not looks_like_admin_leak(None)
