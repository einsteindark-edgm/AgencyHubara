"""El backend de Ads EMITE los costos de WhatsApp por HTTP (gotcha #1 del repo:
tests verdes con el schema permitiendo el campo ≠ el backend mandándolo).

Camino completo: vault → scan → agrupación por campaña Meta (2 anuncios de la
misma campaña) → JSON de `/campaigns`, `/campaigns/{id}/adsets` y
`/campaigns/{id}/conversations`.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.plugins.ads.test_ads_endpoint import (  # noqa: F401 — fixtures
    ads_client,
    segmented_client,
)


def _set_cost_summary(vault: Path, phone: str, by_category: dict[str, tuple[int, int]],
                      pending: int = 0) -> None:
    path = vault / f"wa_{phone}" / "metadata.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["episodes"][0]["cost_summary"] = {
        "total_usd_micros": sum(m for _c, m in by_category.values()),
        "messages_count": sum(c for c, _m in by_category.values()) + pending,
        "messages_billable_count": 0,
        "messages_free_count": 0,
        "messages_pending_count": pending,
        "by_category": {
            cat: {"count": c, "usd_micros": m} for cat, (c, m) in by_category.items()
        },
        "by_pricing_type": {},
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _seed_costs(vault: Path) -> None:
    # AD_1 y AD_2 son de la MISMA campaña (CAMP_9), adsets distintos.
    _set_cost_summary(vault, "111", {"service": (10, 8_000), "marketing": (1, 12_500)})
    _set_cost_summary(vault, "222", {"service": (2, 1_600), "utility": (1, 800)}, pending=1)


def test_campaigns_emite_el_acumulado_por_categoria(segmented_client):
    client, vault = segmented_client
    _seed_costs(vault)
    rows = client.get("/api/ads/campaigns").json()["campaigns"]
    camp = next(c for c in rows if c["id"] == "CAMP_9")
    assert camp["wa_cost_usd_micros"] == 22_900
    assert camp["wa_cost_by_category"] == {
        "service": {"count": 12, "usd_micros": 9_600},
        "marketing": {"count": 1, "usd_micros": 12_500},
        "utility": {"count": 1, "usd_micros": 800},
    }
    assert camp["wa_msgs_pending"] == 1


def test_adsets_emite_el_costo_de_cada_segmento(segmented_client):
    client, vault = segmented_client
    _seed_costs(vault)
    rows = client.get("/api/ads/campaigns/CAMP_9/adsets").json()["ad_sets"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["ADSET_A"]["wa_cost_usd_micros"] == 20_500
    assert by_id["ADSET_B"]["wa_cost_usd_micros"] == 2_400


def test_conversations_emite_costo_y_categorias_por_conversacion(segmented_client):
    client, vault = segmented_client
    _seed_costs(vault)
    convs = client.get("/api/ads/campaigns/CAMP_9/conversations").json()["conversations"]
    by_phone = {c["phone_number"]: c for c in convs}
    assert by_phone["111"]["wa_cost_usd_micros"] == 20_500
    assert by_phone["111"]["wa_cost_by_category"]["marketing"] == {
        "count": 1, "usd_micros": 12_500,
    }
    assert by_phone["222"]["wa_msgs_pending"] == 1
