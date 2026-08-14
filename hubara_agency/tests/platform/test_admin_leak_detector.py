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
]


@pytest.mark.parametrize("text", LEGIT)
def test_legit_sales_text_is_not_flagged(text: str) -> None:
    assert not looks_like_admin_leak(text), (
        f"falso positivo sobre mensaje legítimo: {text!r}"
    )


def test_none_is_not_flagged() -> None:
    assert not looks_like_admin_leak(None)
