"""Guard determinista de persona: el cliente nunca debe notar el relevo.

Incidente origen (run 5ed9af2d, 2026-09-18): tras `escalate_to_human` el
cliente recibió "Listo, la conversación quedó en manos del equipo humano." —
la frase delata que hasta ahí NO lo atendía una persona. Regla del operador:
el relevo se nombra como "un colega / un compañero del equipo", jamás con
vocabulario que oponga humano vs. sistema.

`breaks_human_persona` NO es el fix primario (ese es estructural: la tool de
escalación termina el turno y la despedida viaja en `customer_message`). Es
la validación del param: si el texto rompe la persona, la tool lo reemplaza
por una despedida aprobada — un falso positivo cuesta una frase enlatada,
nunca un cliente sin respuesta.
"""
from __future__ import annotations

import pytest

from src.platform.llm_text_sanitizer import breaks_human_persona

BREAKS = [
    # Run 5ed9af2d — el texto literal que recibió el cliente.
    "Listo, la conversación quedó en manos del equipo humano.",
    # Guion viejo de etapa_cierre / order_registration (lo dictábamos nosotros).
    "Tu pedido quedó tomado y un humano te confirma en unos minutos 🤍",
    "Te paso con un asesor humano para que te ayude con eso.",
    "Una persona real te va a responder en breve.",
    "Soy un asistente virtual, pero ya te comunico con alguien del equipo.",
    "No soy un bot, tranquilo 🤍",
    "Como inteligencia artificial no puedo darte ese descuento.",
    "Soy una IA de Hubara.",
    "Este es un mensaje automático.",
    "El sistema automatizado ya registró tu solicitud.",
    # Falsos negativos cazados en revisión: sigla en minúscula / en inglés,
    # "soy un asistente" a secas, y los giros con que un bot anuncia el relevo.
    "Soy una ia, pero te ayudo con gusto.",
    "I'm an AI assistant de la tienda.",
    "Soy un asistente de la tienda.",
    "Te comunico con un agente en vivo.",
    "Ya te paso con una persona del equipo.",
]

KEEPS = [
    "Un colega del equipo te responde en este mismo chat 🤍",
    "Para 100 unidades te coordino con un colega del equipo, que maneja ese "
    "tipo de pedidos y te responde en este mismo chat 🤍",
    "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara.",
    "Mi compañera que maneja los pedidos grandes te escribe por aquí mismo.",
    "Todas nuestras velas son hechas a mano en Colombia, con cera de palma.",
    "El pago lo confirmamos automáticamente cuando llega la transferencia.",
    "¿Buscas un aroma para ti o para regalar? 🤍",
    # "persona" como destinatario del regalo y "en vivo" de otra cosa: legítimo.
    "¿Es para una persona especial? Te recomiendo el Cubo Love 🤍",
    "Hacemos un en vivo por Instagram los viernes.",
]


@pytest.mark.parametrize("text", BREAKS)
def test_text_that_reveals_the_bot_or_the_human_relay_breaks_persona(text: str) -> None:
    assert breaks_human_persona(text), f"debió marcarse: {text!r}"


@pytest.mark.parametrize("text", KEEPS)
def test_colleague_wording_and_sales_talk_keep_persona(text: str) -> None:
    assert not breaks_human_persona(text), f"falso positivo: {text!r}"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_text_does_not_break_persona(empty: str | None) -> None:
    assert not breaks_human_persona(empty)
