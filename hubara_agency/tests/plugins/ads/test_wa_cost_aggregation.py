"""Costos de WhatsApp en Ads: por conversación y acumulados por campaña.

Pedido del operador (2026-09-18): ver en Ads cuánto costó en WhatsApp cada
conversación (y en qué categorías de Meta: marketing / utility / service /
authentication) y, en el panel de la campaña, el acumulado por categoría.

El dato YA existe: `IngestDeliveryStatus` (chats) materializa
`episode.cost_summary` cuando llega el webhook `message_status` de Meta
(categoría + cobrado/gratis) cruzado con la tarjeta de tarifas. Unidad: USD
micros (1e-6 USD) — enteros, sin errores de float. Ads lo lee crudo del vault
(P-3: no importa chats) y lo atribuye por EPISODIO, igual que el costo LLM.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.ads.aggregation import (
    list_ads_campaigns,
    list_attributed_conversations,
)
from tests.plugins.ads.test_aggregation import _ep, _only, _write_episodic_session


def _summary(
    by_category: dict[str, tuple[int, int]], *, pending: int = 0
) -> dict:
    """Shape de `_summary_to_dict` (chats): {cat: (count, usd_micros)}."""
    total = sum(micros for _count, micros in by_category.values())
    count = sum(c for c, _micros in by_category.values())
    billable = sum(c for c, micros in by_category.values() if micros > 0)
    return {
        "total_usd_micros": total,
        "messages_count": count + pending,
        "messages_billable_count": billable,
        "messages_free_count": count - billable,
        "messages_pending_count": pending,
        "by_category": {
            cat: {"count": c, "usd_micros": micros}
            for cat, (c, micros) in by_category.items()
        },
        "by_pricing_type": {},
    }


def test_conversacion_expone_costo_y_categorias(_isolate_vault_dir: Path):
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep(
                "ep_001",
                started_at_ms=1,
                cost_summary=_summary(
                    {"service": (12, 9_600), "marketing": (1, 12_500)}, pending=2
                ),
            )
        ],
    )
    (conv,) = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert conv.wa_cost_usd_micros == 22_100
    assert conv.wa_cost_by_category == {
        "service": {"count": 12, "usd_micros": 9_600},
        "marketing": {"count": 1, "usd_micros": 12_500},
    }
    assert conv.wa_msgs_pending == 2


def test_categoria_gratis_igual_aparece_con_su_conteo(_isolate_vault_dir: Path):
    # Dentro de la ventana de 72h del anuncio todo es gratis: el operador igual
    # quiere ver QUÉ categorías se usaron en la conversación.
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, cost_summary=_summary({"service": (7, 0)}))
        ],
    )
    (conv,) = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert conv.wa_cost_usd_micros == 0
    assert conv.wa_cost_by_category == {"service": {"count": 7, "usd_micros": 0}}


def test_conversacion_sin_cost_summary_queda_en_none(_isolate_vault_dir: Path):
    # None ≠ 0: "no hay dato" (sesión vieja / sin outbounds registrados) no es
    # "costó cero".
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[_ep("ep_001", started_at_ms=1)],
    )
    (conv,) = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert conv.wa_cost_usd_micros is None
    assert conv.wa_cost_by_category is None
    assert conv.wa_msgs_pending == 0


def test_cost_summary_corrupto_no_tumba_el_listado(_isolate_vault_dir: Path):
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep(
                "ep_001",
                started_at_ms=1,
                cost_summary={
                    "total_usd_micros": "mucho",
                    "by_category": {"service": "roto", "utility": {"count": 2, "usd_micros": 1_600}},
                },
            )
        ],
    )
    (conv,) = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    # El total se recompone desde las categorías legibles.
    assert conv.wa_cost_by_category == {"utility": {"count": 2, "usd_micros": 1_600}}
    assert conv.wa_cost_usd_micros == 1_600


def test_campana_acumula_por_categoria_todos_sus_episodios(_isolate_vault_dir: Path):
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, closed_at_ms=2,
                cost_summary=_summary({"service": (10, 8_000), "utility": (1, 800)})),
            _ep("ep_002", started_at_ms=3,
                cost_summary=_summary({"service": (4, 3_200)}, pending=1)),
        ],
    )
    _write_episodic_session(
        _isolate_vault_dir,
        phone="222",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=5,
                cost_summary=_summary({"marketing": (2, 25_000)})),
        ],
    )
    # Otra campaña: no contamina.
    _write_episodic_session(
        _isolate_vault_dir,
        phone="333",
        source_id="AD_OTRA",
        episodes=[
            _ep("ep_001", started_at_ms=7,
                cost_summary=_summary({"service": (99, 79_200)})),
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.wa_cost_usd_micros == 37_000
    assert camp.wa_cost_by_category == {
        "service": {"count": 14, "usd_micros": 11_200},
        "utility": {"count": 1, "usd_micros": 800},
        "marketing": {"count": 2, "usd_micros": 25_000},
    }
    assert camp.wa_msgs_pending == 1


def test_campana_sin_ningun_dato_de_costo_queda_en_none(_isolate_vault_dir: Path):
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[_ep("ep_001", started_at_ms=1)],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.wa_cost_usd_micros is None
    assert camp.wa_cost_by_category is None
