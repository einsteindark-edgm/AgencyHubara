"""Tests del filtro puro del scheduler post-venta (fase roja).

`select_post_sale_sessions` decide QUÉ conversaciones devuelve el scheduler
diario al bot de ventas: las que cerraron compra (`tag=COMPRA_EXITOSA`) y
siguen abiertas en humano (`active_route=humano`) — el estado que deja
`apply_payment_confirmation_to_chat_metadata` a propósito (el humano confirmó
el pago y nadie devolvió la conversación). Todo lo demás queda afuera.
"""
from __future__ import annotations

from src.plugins.chats.agent.post_sale_return.use_cases import (
    select_post_sale_sessions,
)


def _paid_episode() -> dict:
    """Episodio como lo deja `apply_payment_confirmation_to_chat_metadata`:
    el humano verificó el pago desde el dashboard de orders."""
    return {
        "episode_id": "ep-1",
        "closing_tag": "COMPRA_EXITOSA",
        "closed_at_ms": 1_752_000_000_000,
        "payment_confirmed_at_ms": 1_752_000_100_000,
        "payment_confirmed_by": "operador",
    }


def test_selecciona_solo_compra_exitosa_en_humano_con_pago_confirmado() -> None:
    sessions = [
        (
            "wa_1",
            {
                "active_route": "humano",
                "tag": "COMPRA_EXITOSA",
                "episodes": [_paid_episode()],
            },
        ),
        # En humano pero sin compra cerrada — el humano sigue atendiendo.
        ("wa_2", {"active_route": "humano", "tag": "HUMANO"}),
        # Compra cerrada pero ya devuelta al bot — nada que hacer.
        (
            "wa_3",
            {
                "active_route": "ventas",
                "tag": "COMPRA_EXITOSA",
                "episodes": [_paid_episode()],
            },
        ),
        ("wa_4", {"active_route": "remarketing", "tag": "REMARKETING"}),
        (
            "wa_5",
            {
                "active_route": "humano",
                "tag": "COMPRA_EXITOSA",
                "episodes": [{"closing_tag": "RECHAZO"}, _paid_episode()],
            },
        ),
    ]
    assert select_post_sale_sessions(sessions) == ["wa_1", "wa_5"]


def test_sin_pago_confirmado_se_queda_en_humano() -> None:
    """Regla de negocio: NO se devuelve a sales sin pago confirmado. Un tag
    COMPRA_EXITOSA puesto por el bot (ManageConversationTagTool) sin la
    verificación humana del pago NO alcanza — la conversación se queda en
    humano hasta que el pago esté confirmado."""
    sessions = [
        # Tag de cierre del bot, sin marca de pago verificado.
        (
            "wa_1",
            {
                "active_route": "humano",
                "tag": "COMPRA_EXITOSA",
                "episodes": [
                    {"closing_tag": "COMPRA_EXITOSA", "closed_at_ms": 1}
                ],
            },
        ),
        # Orden registrada pero pago pendiente de verificación.
        (
            "wa_2",
            {
                "active_route": "humano",
                "tag": "COMPRA_EXITOSA",
                "episodes": [
                    {"closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "closed_at_ms": 1}
                ],
            },
        ),
        # Sin episodios en absoluto.
        ("wa_3", {"active_route": "humano", "tag": "COMPRA_EXITOSA"}),
        # Episodios malformados no rompen.
        (
            "wa_4",
            {
                "active_route": "humano",
                "tag": "COMPRA_EXITOSA",
                "episodes": ["basura", None],
            },
        ),
    ]
    assert select_post_sale_sessions(sessions) == []


def test_metadata_vacia_o_malformada_no_rompe_ni_selecciona() -> None:
    sessions = [
        ("wa_1", {}),
        ("wa_2", {"active_route": "humano"}),
        ("wa_3", {"tag": "COMPRA_EXITOSA"}),
        ("wa_4", {"active_route": None, "tag": None}),
    ]
    assert select_post_sale_sessions(sessions) == []
