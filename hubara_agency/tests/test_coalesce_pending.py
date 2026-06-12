"""Tests del helper `coalesce_pending` (Fix 1 del PR debounce/coalesce).

Verifican que el coalesce combina mensajes pendientes en un solo turno con
las reglas correctas:
  * Mensajes reales del cliente se concatenan al rol "user".
  * Mensajes `is_handoff=True` se mueven a `plugin_context` (no contaminan
    la conversacion JSONL como hacia el `[SISTEMA INTERNO]: ...` legacy).
  * Si NO hay mensajes reales pero si handoff, se usa el ultimo handoff
    como mensaje principal (caso: handoff sin respuesta del cliente, Sales
    saluda proactivamente).
"""
from __future__ import annotations

from src.platform.workflow_helpers import PendingMessage, coalesce_pending


def test_coalesce_two_user_messages_concatenates_them() -> None:
    """Caso real del bug b9639be1: 'Hola si' + 'Me recuerdas...' → un turno."""
    pending = [
        PendingMessage(message="Hola si"),
        PendingMessage(message="Me recuerdas cuanto vale plegaria de luz?"),
    ]
    result = coalesce_pending(pending)
    assert result.message == "Hola si\nMe recuerdas cuanto vale plegaria de luz?"
    assert result.plugin_context is None
    assert result.is_handoff is False


def test_coalesce_handoff_only_wraps_summary_with_sales_framing() -> None:
    """Sin user msg pero con handoff → Sales saluda proactivamente.

    L-12 (run 3607aecc): el summary CRUDO en el rol user era indistinguible
    del trigger de remarketing — el LLM de ventas se "autotransfería" en vez
    de retomar la venta. El mensaje principal ahora lleva framing inequívoco:
    quién es el agente (ventas), que el control ya es suyo, y qué hacer.
    """
    pending = [
        PendingMessage(message="El cliente volvio a interactuar", is_handoff=True),
    ]
    result = coalesce_pending(pending)
    assert "El cliente volvio a interactuar" in result.message
    assert "HANDOFF DE REMARKETING A VENTAS" in result.message
    assert "Eres el agente de ventas" in result.message
    assert "YA ES TUYO" in result.message
    # El crudo pelado NO debe ser el mensaje (contrato L-12)
    assert result.message != "El cliente volvio a interactuar"
    # plugin_context tambien lo lleva como contexto explicito
    assert result.plugin_context == [
        "[HANDOFF_REMARKETING]: El cliente volvio a interactuar"
    ]


def test_coalesce_handoff_plus_user_msg_moves_handoff_to_plugin_context() -> None:
    """El handoff NO contamina el rol user — va a plugin_context."""
    pending = [
        PendingMessage(message="Resumen del handoff", is_handoff=True),
        PendingMessage(message="Me recuerdas el precio?"),
    ]
    result = coalesce_pending(pending)
    # User msg limpio
    assert result.message == "Me recuerdas el precio?"
    # Handoff context separado
    assert result.plugin_context == [
        "[HANDOFF_REMARKETING]: Resumen del handoff"
    ]
    assert result.is_handoff is False


def test_coalesce_preserves_existing_plugin_context() -> None:
    """El plugin_context preexistente de cualquier msg se conserva."""
    pending = [
        PendingMessage(
            message="msg1",
            plugin_context=["snippet-1", "snippet-2"],
        ),
        PendingMessage(message="msg2", plugin_context=["snippet-3"]),
    ]
    result = coalesce_pending(pending)
    assert result.plugin_context == ["snippet-1", "snippet-2", "snippet-3"]


def test_coalesce_combines_media_from_all_messages() -> None:
    pending = [
        PendingMessage(message="foto", media=["img-1"]),
        PendingMessage(message="otra", media=["img-2", "img-3"]),
    ]
    result = coalesce_pending(pending)
    assert result.media == ["img-1", "img-2", "img-3"]


def test_coalesce_empty_pending_returns_empty_message() -> None:
    result = coalesce_pending([])
    assert result.message == ""
    assert result.plugin_context is None
    assert result.media is None
    assert result.is_handoff is False


def test_coalesce_skips_empty_user_messages() -> None:
    """Strings vacios no agregan ruido al concat."""
    pending = [
        PendingMessage(message=""),
        PendingMessage(message="real"),
        PendingMessage(message=""),
    ]
    result = coalesce_pending(pending)
    assert result.message == "real"


def test_coalesce_multiple_handoffs_all_go_to_plugin_context() -> None:
    """Edge: dos handoffs encolados (race entre dispatcher y refresh)."""
    pending = [
        PendingMessage(message="primer handoff", is_handoff=True),
        PendingMessage(message="segundo handoff", is_handoff=True),
        PendingMessage(message="msg real"),
    ]
    result = coalesce_pending(pending)
    assert result.message == "msg real"
    assert result.plugin_context == [
        "[HANDOFF_REMARKETING]: primer handoff",
        "[HANDOFF_REMARKETING]: segundo handoff",
    ]
