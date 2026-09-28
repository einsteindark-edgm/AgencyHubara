"""Regla de oro del SDK: `src.sdk.agentkit` re-exporta lo que un workflow
conversacional necesita del turno compartido, sin importar `src.platform`
(P-28).

Consumidor: el workflow de ventas V2 (motor de decisiones, F4). El V1 importa
esas piezas de platform directo y está congelado en la allowlist de P-28; el V2
nace importando solo el SDK.

El check es por IDENTIDAD (`is`): la fachada re-exporta EL MISMO objeto que
platform, no una re-implementación (un `InboxMsg` distinto rompería la
coalescencia de la ráfaga; una activity distinta, el registro del worker).
"""
from __future__ import annotations


def test_agentkit_reexports_the_burst_coalescing() -> None:
    import src.platform.workflow_helpers as impl
    import src.sdk.agentkit as kit

    assert getattr(kit, "InboxMsg", None) is impl.InboxMsg
    assert getattr(kit, "coalesce_inbox", None) is impl.coalesce_inbox


def test_agentkit_reexports_the_episode_closed_decision() -> None:
    import src.platform.contracts as impl
    import src.sdk.agentkit as kit

    assert getattr(kit, "EpisodeClosedDecision", None) is impl.EpisodeClosedDecision


def test_agentkit_reexports_the_dashboard_persistence_activity() -> None:
    import src.platform.session_history.activities as impl
    import src.sdk.agentkit as kit

    assert getattr(kit, "persist_assistant_message_activity", None) is impl.persist_assistant_message_activity


def test_agentkit_reexports_the_llm_activity_options() -> None:
    """Las opciones de una activity que llama al LLM (el bootstrap de la sesión
    las usa): públicas en platform (`LLM_ACTIVITY_OPTIONS`) y el MISMO dict que
    el turno compartido (`_LLM_OPTIONS`)."""
    import src.platform.temporal.retry_policies as impl
    import src.sdk.agentkit as kit

    assert getattr(impl, "LLM_ACTIVITY_OPTIONS", None) is impl._LLM_OPTIONS
    assert getattr(kit, "LLM_ACTIVITY_OPTIONS", None) is impl._LLM_OPTIONS
