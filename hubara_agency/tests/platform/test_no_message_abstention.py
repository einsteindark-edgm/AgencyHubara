"""Tests del sentinel de abstención `NO_MESSAGE` (`is_no_message_abstention`).

Incidente wa_573229041190 (2026-07-17, run 019f7234): el LLM de remarketing
recibió un trigger que ya no correspondía (el cliente ya había respondido al
gancho anterior y comprado). Razonó bien — "no genero un nuevo mensaje" — pero
el runtime no tiene canal de abstención: todo `final_content` no vacío se envía
a WhatsApp, así que el cliente vio la deliberación interna del bot.

El sentinel es ese canal: el prompt instruye al LLM a responder exactamente
`NO_MESSAGE` cuando el toque proactivo sobra, y el workflow lo trata como
no-op (no envía, no persiste, devuelve el routing a ventas).
"""
from __future__ import annotations


from src.platform.llm_text_sanitizer import is_no_message_abstention


class TestAbstains:
    def test_exact_sentinel(self):
        assert is_no_message_abstention("NO_MESSAGE") is True

    def test_sentinel_with_whitespace(self):
        assert is_no_message_abstention("  NO_MESSAGE\n") is True

    def test_sentinel_lowercase(self):
        assert is_no_message_abstention("no_message") is True

    def test_sentinel_wrapped_in_quotes_or_backticks(self):
        assert is_no_message_abstention('"NO_MESSAGE"') is True
        assert is_no_message_abstention("`NO_MESSAGE`") is True
        assert is_no_message_abstention("*NO_MESSAGE*") is True

    def test_sentinel_with_trailing_punctuation(self):
        assert is_no_message_abstention("NO_MESSAGE.") is True

    def test_spanish_drift(self):
        # DeepSeek code-switchea a español — aceptamos la traducción obvia.
        assert is_no_message_abstention("NO_MENSAJE") is True

    def test_sentinel_first_line_with_explanation_after(self):
        # El modelo a veces desobedece el "sin explicación" — la intención
        # de abstenerse sigue clara: nada de esto debe llegar al cliente.
        assert (
            is_no_message_abstention(
                "NO_MESSAGE\n\nEl cliente ya respondió al gancho anterior."
            )
            is True
        )


class TestDoesNotAbstain:
    def test_empty_is_not_abstention(self):
        # Vacío ya tiene su propio manejo (no-send por falsy) — el helper
        # solo reporta abstención EXPLÍCITA.
        assert is_no_message_abstention("") is False
        assert is_no_message_abstention(None) is False

    def test_normal_message(self):
        assert is_no_message_abstention("¡Hola! ¿Seguimos con tu pedido?") is False

    def test_message_mentioning_sentinel_mid_text(self):
        # Solo cuenta al INICIO — un mensaje legítimo que menciona la palabra
        # en el medio no se suprime.
        assert (
            is_no_message_abstention("Te cuento que NO_MESSAGE no aplica acá")
            is False
        )

    def test_deliberation_without_sentinel_is_not_abstention(self):
        # El caso del incidente ANTES del fix: deliberación en prosa. El
        # helper NO adivina intención — eso queda para el prompt (el LLM debe
        # usar el sentinel). Documentamos el límite explícitamente.
        assert (
            is_no_message_abstention(
                "Este es un nuevo trigger interno, no genero un nuevo mensaje."
            )
            is False
        )


def test_agentkit_reexports_abstention_symbols():
    """Regla de oro del SDK: el consumidor (RemarketingWorkflow, plugin chats)
    importa el canal de abstención desde `src.sdk.agentkit`, no desde
    `src.platform` (P-28). Mismo patrón que test_messagingkit."""
    import src.platform.llm_text_sanitizer as impl
    import src.sdk.agentkit as kit

    assert kit.is_no_message_abstention is impl.is_no_message_abstention
    assert kit.NO_MESSAGE_SENTINEL == impl.NO_MESSAGE_SENTINEL
