"""Tests del helper `coalesce_inbox` (bandeja durable con watermark).

Espejo de `coalesce_pending` pero sobre `InboxMsg` (con `seq`/`wamid`/`ts_ms`).
Reglas nuevas respecto a `coalesce_pending`:
  * Ordena por `seq` ascendente antes de concatenar (una ráfaga puede llegar
    desordenada; el orden canónico es el de secuencia asignada por el workflow).
  * Cuando hay MÁS de un mensaje real del cliente, inyecta una "nota de ráfaga"
    determinista al `plugin_context` para que el LLM responda al conjunto como
    un hilo, sin ignorar ninguno ni contestar solo el último.
  * Preserva el manejo de `is_handoff` (va a plugin_context, no al rol user).
"""
from __future__ import annotations

from src.platform.workflow_helpers import InboxMsg, coalesce_inbox


def _msg(seq: int, text: str, **kw) -> InboxMsg:
    return InboxMsg(seq=seq, wamid=f"wamid-{seq}", text=text, ts_ms=1000 + seq, **kw)


def test_coalesce_inbox_orders_by_seq_and_concatenates() -> None:
    """Una ráfaga desordenada se concatena en orden de `seq`."""
    batch = [
        _msg(3, "para Bogotá"),
        _msg(1, "Hola"),
        _msg(2, "quiero el difusor"),
    ]
    result = coalesce_inbox(batch)
    assert result.message == "Hola\nquiero el difusor\npara Bogotá"
    assert result.is_handoff is False


def test_coalesce_inbox_single_message_has_no_burst_note() -> None:
    """Un solo mensaje → sin nota de ráfaga (caso fluido normal)."""
    result = coalesce_inbox([_msg(1, "Hola")])
    assert result.message == "Hola"
    assert result.plugin_context is None


def test_coalesce_inbox_multiple_messages_injects_burst_note() -> None:
    """>1 mensaje → nota de ráfaga determinista que los lista en orden."""
    batch = [_msg(1, "Hola"), _msg(2, "quiero el difusor"), _msg(3, "de lavanda")]
    result = coalesce_inbox(batch)
    assert result.plugin_context is not None
    note = result.plugin_context[0]
    assert "3 mensajes seguidos" in note
    assert '"Hola"' in note
    assert '"quiero el difusor"' in note
    assert '"de lavanda"' in note


def test_coalesce_inbox_handoff_moves_to_plugin_context() -> None:
    """El handoff NO contamina el rol user — va a plugin_context (L-12)."""
    batch = [
        InboxMsg(seq=1, wamid=None, text="Resumen del handoff", ts_ms=1, is_handoff=True),
        _msg(2, "Me recuerdas el precio?"),
    ]
    result = coalesce_inbox(batch)
    assert result.message == "Me recuerdas el precio?"
    assert result.plugin_context is not None
    assert any(
        "[HANDOFF_REMARKETING]: Resumen del handoff" in c
        for c in result.plugin_context
    )


def test_coalesce_inbox_combines_media_in_seq_order() -> None:
    batch = [_msg(2, "otra", media=["img-2"]), _msg(1, "foto", media=["img-1"])]
    result = coalesce_inbox(batch)
    assert result.media == ["img-1", "img-2"]


# --- version=2 (burst-note-v2): fixes post-review del PR #100 -----------------
#
# v1 queda congelada porque ya corre en prod (deploy 2026-07-01): el workflow
# la elige vía `workflow.patched("burst-note-v1")` para replays de histories
# viejas. v2 es la que toman ejecuciones nuevas (gate "burst-note-v2").


def test_coalesce_inbox_v2_media_only_does_not_inflate_burst_note() -> None:
    """Un mensaje solo-media (text vacío) NO cuenta ni se lista en la nota.

    Bug v1: cliente manda foto + "cuánto vale?" → la nota decía "2 mensajes
    seguidos" y listaba `1) ""` — una entrada vacía que confunde al LLM.
    Con un solo mensaje CON texto, no hay hilo que seguir → sin nota.
    """
    batch = [_msg(1, "", media=["img-1"]), _msg(2, "cuánto vale?")]
    result = coalesce_inbox(batch, version=2)
    assert result.message == "cuánto vale?"
    assert result.media == ["img-1"]
    assert result.plugin_context is None  # sin nota: solo 1 mensaje con texto


def test_coalesce_inbox_v2_burst_note_counts_only_texted_messages() -> None:
    """Con 3 mensajes donde 1 es solo-media, la nota dice 2 y no lista `""`."""
    batch = [_msg(1, "Hola"), _msg(2, "", media=["img-1"]), _msg(3, "el difusor")]
    result = coalesce_inbox(batch, version=2)
    assert result.plugin_context is not None
    note = result.plugin_context[0]
    assert "2 mensajes seguidos" in note
    assert '"Hola"' in note
    assert '"el difusor"' in note
    assert '""' not in note


def test_coalesce_inbox_v2_dedupes_repeated_plugin_context() -> None:
    """Ráfaga de N mensajes con el mismo plugin_context → 1 sola copia.

    Bug v1: cada inbound Sales trae `build_bogota_context_string()` en su
    plugin_context; una ráfaga de 3 metía 3 bloques "Hora actual en Colombia"
    casi idénticos al system prompt. v2 dedupea duplicados EXACTOS preservando
    el orden de primera aparición.
    """
    bogota = "[CONTEXTO DE TURNO] Hora actual en Colombia: 14:30"
    batch = [
        _msg(1, "Hola", plugin_context=[bogota]),
        _msg(2, "quiero el difusor", plugin_context=[bogota]),
        _msg(3, "de lavanda", plugin_context=[bogota, "extra"]),
    ]
    result = coalesce_inbox(batch, version=2)
    assert result.plugin_context is not None
    assert result.plugin_context.count(bogota) == 1
    assert "extra" in result.plugin_context
    # La nota de ráfaga sigue primera (metadata del turno antes del contexto).
    assert "3 mensajes seguidos" in result.plugin_context[0]


def test_coalesce_inbox_v1_default_preserves_deployed_behavior() -> None:
    """Sin `version=2`, el comportamiento es el v1 congelado (replay prod)."""
    batch = [_msg(1, "", media=["img-1"]), _msg(2, "cuánto vale?")]
    result = coalesce_inbox(batch)
    assert result.plugin_context is not None  # v1 SÍ inyecta la nota (2 msgs)
    assert "2 mensajes seguidos" in result.plugin_context[0]
