"""Tests para `platform.llm_text_sanitizer`.

Bug origen (sesión 579d34e7): el LLM emitió a un cliente real:

    Here's my attempt:

    "¡Hola de nuevo! 🌿 Quedó pendiente...🤍"¡Hola de nuevo! 🌿 Quedó pendiente...🤍

Combina:
  * Meta-prefijo en inglés.
  * Comillas envolviendo la respuesta.
  * El mismo párrafo emitido dos veces back-to-back.

Estos tests garantizan que:
  1. El payload exacto del bug se limpia.
  2. Inputs limpios pasan intactos (no false-positives).
  3. El sanitizador es idempotente.
  4. Las acciones se reportan correctamente para analytics.
"""
from __future__ import annotations

import pytest

from src.platform.llm_text_sanitizer import sanitize_llm_text


# =============================================================================
# Bug exacto 579d34e7
# =============================================================================


def test_exact_579d34e7_payload_cleaned():
    raw = (
        'Here\'s my attempt:\n\n'
        '"¡Hola de nuevo! 🌿 Quedó pendiente lo de la *Plegaria de Luz* — '
        '¿quieres que terminemos de ajustar los datos de envío y la dejamos lista? 🤍"'
        '¡Hola de nuevo! 🌿 Quedó pendiente lo de la *Plegaria de Luz* — '
        '¿quieres que terminemos de ajustar los datos de envío y la dejamos lista? 🤍'
    )
    result = sanitize_llm_text(raw)
    expected = (
        "¡Hola de nuevo! 🌿 Quedó pendiente lo de la *Plegaria de Luz*, "
        "¿quieres que terminemos de ajustar los datos de envío y la dejamos lista? 🤍"
    )
    assert result.text == expected
    # Sin duplicación residual
    assert result.text.count("Hola de nuevo") == 1
    # Sin "Here's"
    assert "Here" not in result.text
    # Sin comillas envolventes
    assert not result.text.startswith('"')
    assert not result.text.endswith('"')
    # Acciones reportadas para analytics
    assert "meta_prefix_stripped" in result.actions
    assert "back_to_back_dedup" in result.actions


# =============================================================================
# Idempotencia
# =============================================================================


def test_idempotent_clean_input():
    clean = "¡Hola! Te muestro tres opciones 🤍"
    result = sanitize_llm_text(clean)
    assert result.text == clean
    assert result.actions == ()
    assert not result.changed


def test_idempotent_after_first_pass():
    """Aplicar el sanitizador dos veces da el mismo resultado."""
    raw = "Here's my response: \"Mensaje al cliente.\""
    first = sanitize_llm_text(raw)
    second = sanitize_llm_text(first.text)
    assert second.text == first.text
    assert second.actions == ()


# =============================================================================
# Meta-prefijos individuales
# =============================================================================


@pytest.mark.parametrize("prefix,body", [
    ("Here's my attempt:", "¡Hola!"),
    ("Here's my response:", "¡Hola!"),
    ("Here's the message:", "¡Hola!"),
    ("Here it is:", "¡Hola!"),
    ("My response:", "¡Hola!"),
    ("Response:", "¡Hola!"),
    ("Final answer:", "¡Hola!"),
    ("Output:", "¡Hola!"),
    ("Sure!", "¡Hola!"),
    ("Okay,", "¡Hola!"),
    ("Let me try:", "¡Hola!"),
    ("Aquí va:", "¡Hola!"),
    ("Aquí está:", "¡Hola!"),
    ("Aquí tienes:", "¡Hola!"),
    ("Mi respuesta:", "¡Hola!"),
    ("Te respondo:", "¡Hola!"),
    ("Voy a:", "¡Hola!"),
    ("Intento:", "¡Hola!"),
])
def test_meta_prefix_variants(prefix, body):
    raw = f"{prefix}\n\n{body}"
    result = sanitize_llm_text(raw)
    assert result.text == body
    assert "meta_prefix_stripped" in result.actions


def test_meta_prefix_case_insensitive():
    result = sanitize_llm_text("HERE'S MY ATTEMPT:\n\n¡Hola!")
    assert result.text == "¡Hola!"


# =============================================================================
# Wrapping quotes
# =============================================================================


def test_wrapping_double_quotes_stripped():
    raw = '"Mensaje al cliente sin citas internas."'
    result = sanitize_llm_text(raw)
    assert result.text == "Mensaje al cliente sin citas internas."
    assert "wrapping_quotes_stripped" in result.actions


def test_internal_quotes_preserved():
    """Si hay comillas internas legítimas, NO removemos las externas."""
    raw = '"El cliente dijo \\"hola\\" y se fue."'
    result = sanitize_llm_text(raw)
    # Conservador — no rompamos citas legítimas internas
    assert '"' in result.text


def test_smart_quotes_stripped():
    raw = "“Mensaje al cliente.”"
    result = sanitize_llm_text(raw)
    assert result.text == "Mensaje al cliente."


# =============================================================================
# Back-to-back duplication
# =============================================================================


def test_exact_duplicate_collapsed():
    sentence = "¡Hola! ¿Te muestro nuestras velas?"
    raw = sentence + sentence
    result = sanitize_llm_text(raw)
    assert result.text == sentence
    assert "back_to_back_dedup" in result.actions


def test_no_false_positive_on_legitimate_repeat():
    """Si un mensaje legít repite palabras, NO debe colapsar."""
    raw = "Tenemos lavanda y también tenemos sándalo."
    result = sanitize_llm_text(raw)
    assert result.text == raw  # sin cambios
    assert "back_to_back_dedup" not in result.actions


def test_short_messages_not_deduped():
    """Mensajes cortos (<20 chars) no entran al dedupe — riesgo de
    falsos positivos."""
    raw = "Hola!Hola!"
    result = sanitize_llm_text(raw)
    # No debe colapsar — es demasiado corto para evaluar con seguridad
    assert "back_to_back_dedup" not in result.actions


# =============================================================================
# Unbalanced quotes residuales
# =============================================================================


def test_unbalanced_opening_quote_stripped():
    raw = '"¡Hola! ¿Cómo te ayudo hoy?'  # abre pero no cierra
    result = sanitize_llm_text(raw)
    assert result.text == "¡Hola! ¿Cómo te ayudo hoy?"
    assert "unbalanced_quote_stripped" in result.actions


def test_unbalanced_closing_quote_stripped():
    raw = "¡Hola! ¿Cómo te ayudo hoy?\""  # cierra sin abrir
    result = sanitize_llm_text(raw)
    assert result.text == "¡Hola! ¿Cómo te ayudo hoy?"
    assert "unbalanced_quote_stripped" in result.actions


# =============================================================================
# Empty input
# =============================================================================


def test_empty_input_returns_empty():
    result = sanitize_llm_text("")
    assert result.text == ""
    assert result.actions == ()


def test_only_meta_prefix_returns_original_not_empty():
    """Defensa: si el modelo SOLO devolvió un meta-prefijo (sin body),
    NO removemos — preferimos que el downstream caiga al fallback humano."""
    raw = "Here's my attempt:"
    result = sanitize_llm_text(raw)
    assert result.text  # algo queda; downstream decide fallback


# =============================================================================
# False-positive guards: cosas que NO debemos cambiar
# =============================================================================


def test_message_starting_with_colon_phrase_not_stripped():
    """`Mirá: te muestro tres opciones` empieza con un fragmento que
    termina en `:` pero no es un meta-prefijo."""
    raw = "Mirá: te muestro tres opciones."
    result = sanitize_llm_text(raw)
    assert "meta_prefix_stripped" not in result.actions
    assert result.text == raw


def test_aqui_tienes_as_sentence_start_not_stripped():
    """Bug run eda8d460 (cliente real): 'Aquí tienes todas nuestras velas...'
    es una FRASE legítima, no un meta-prefijo — el cliente recibió
    'todas nuestras velas. Míralas...' arrancando en minúscula (texto roto).
    El prefijo español solo es meta cuando lo delimita ':' o un salto de
    línea ('Aquí tienes:\\n¡Hola!')."""
    raw = (
        "Aquí tienes todas nuestras velas. Míralas con calma y dime cuál "
        "te llama la atención para contarte más detalles 🤍"
    )
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "meta_prefix_stripped" not in result.actions


def test_voy_a_as_sentence_start_not_stripped():
    """'Voy a confirmar tu pedido con el equipo.' es una promesa legítima
    de vendedor — no un meta-prefijo."""
    raw = "Voy a confirmar tu pedido con el equipo y te aviso enseguida."
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "meta_prefix_stripped" not in result.actions


def test_aca_esta_as_sentence_start_not_stripped():
    raw = "Acá está tu resumen del pedido, revísalo con calma."
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "meta_prefix_stripped" not in result.actions


def test_lowercase_remainder_guard_blocks_overstrip():
    """Guard universal: si tras strippear el resto arranca en minúscula,
    cortamos a mitad de frase — NO strippear. (Un mensaje real después de
    un meta-prefijo empieza con mayúscula, ¡/¿ o emoji.)"""
    raw = "Mi respuesta es que sí tenemos contra entrega en Bogotá."
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "meta_prefix_stripped" not in result.actions


def test_legitimate_quoted_inner_content():
    """Si el cliente dijo X, el LLM puede citar al cliente — no debemos
    romper esa cita. (El em dash sí se normaliza a coma; la cita queda intacta.)"""
    raw = 'Entiendo que dijiste "no me llegó" — déjame revisar.'
    result = sanitize_llm_text(raw)
    assert '"no me llegó"' in result.text  # la cita interna intacta
    assert "wrapping_quotes_stripped" not in result.actions
    assert result.text == 'Entiendo que dijiste "no me llegó", déjame revisar.'


# =============================================================================
# Em / en dash -> coma (regla de estilo "cero em dash")
# =============================================================================


def test_em_dash_normalized_to_comma():
    result = sanitize_llm_text("corregido — Medellín, 2 a 3 días")
    assert result.text == "corregido, Medellín, 2 a 3 días"
    assert "em_dash_normalized" in result.actions


def test_en_dash_normalized_to_comma():
    result = sanitize_llm_text("Plegaria de Luz – pendiente")
    assert "–" not in result.text
    assert result.text == "Plegaria de Luz, pendiente"


def test_em_dash_no_spaces_normalized():
    result = sanitize_llm_text("colores—¿te gusta?")
    assert result.text == "colores, ¿te gusta?"


def test_regular_hyphen_not_touched():
    """El guion-menos normal (U+002D) NO se toca: '1-2 días', 'contra-entrega'."""
    raw = "Bogotá 1-2 días, contra-entrega"
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "em_dash_normalized" not in result.actions


def test_em_dash_normalization_idempotent():
    once = sanitize_llm_text("texto — y más — y otro").text
    twice = sanitize_llm_text(once).text
    assert once == twice == "texto, y más, y otro"


# =============================================================================
# Escapes literales (\n como DOS caracteres) — incidente saludo apertura
# =============================================================================


def test_literal_backslash_n_becomes_real_newline():
    """Incidente saludo: el LLM emitió `\\n\\n` como texto literal y el
    cliente vio los backslashes crudos (además el send no pudo separar
    las burbujas, que se parten por newlines REALES)."""
    raw = (
        "Buenos días. Bienvenido a *Hubara*, velas artesanales de cera de "
        "palma hechas a mano en Colombia.\\n\\n¿Qué te gustaría ver?"
    )
    result = sanitize_llm_text(raw)
    assert "\\n" not in result.text
    assert "\n\n¿Qué te gustaría ver?" in result.text
    assert "literal_escapes_normalized" in result.actions


def test_stray_backslash_next_to_newline_dropped():
    """Variante vista en el reporte: `\\n\\` pegado a la palabra siguiente —
    tras convertir el `\\n` queda un backslash huérfano que también se va."""
    raw = "hechas a mano en Colombia.\\n\\qué te gustaría ver"
    result = sanitize_llm_text(raw)
    assert "\\" not in result.text
    assert result.text == "hechas a mano en Colombia.\nqué te gustaría ver"
    assert "literal_escapes_normalized" in result.actions


def test_backslash_before_real_newline_dropped():
    raw = "hechas a mano en Colombia.\\\n¿Qué te gustaría ver?"
    result = sanitize_llm_text(raw)
    assert "\\" not in result.text
    assert result.text == "hechas a mano en Colombia.\n¿Qué te gustaría ver?"


def test_real_newlines_untouched():
    raw = "Primera burbuja.\n\nSegunda burbuja."
    result = sanitize_llm_text(raw)
    assert result.text == raw
    assert "literal_escapes_normalized" not in result.actions


def test_literal_escape_normalization_idempotent():
    once = sanitize_llm_text("Colombia.\\n\\n¿Qué te gustaría ver?").text
    twice = sanitize_llm_text(once).text
    assert once == twice
