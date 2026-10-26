"""El perfil «interesado sin compra» como público de las campañas (operador,
2026-10-20: «que ese perfilamiento sirva como un filtro de público para las
campañas de marketing que podemos lanzar con WhatsApp»).

Los segmentos de hoy salen de la etiqueta de la conversación, que cambia sola
(INTERESADO → SIN_RESPUESTA): quien quiso comprar terminaba en «Fríos». El
segmento nuevo lee el perfil de la persona (`profile_tags`, lo marca Ventas)
y deja fuera a quien compró desde entonces. Se suma a los otros sin repetir a
nadie; las exclusiones (humano, baja, prueba) valen igual.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.marketing.api as api_mod
from src.plugins.marketing.domain.campaigns import (
    SEGMENT_INTERESADOS_SIN_COMPRA,
    interested_without_purchase,
    new_campaign,
    resolve_campaign_audience,
)

_PROFILE = {"profile_tags": {"interesado_sin_compra": {"at_ms": 5_000, "episode_id": "ep_001", "source": "jev"}}}
_EP = {"episode_id": "ep_001", "started_at_ms": 1_000}


def _campaign(segments: list[str]) -> dict:
    campaign = new_campaign(campaign_id="mkt-1", name="5 % antes del 31", now_ms=1)
    campaign["segments"] = segments
    return campaign


SESSIONS = [
    ("wa_10000000001", {"tag": "SIN_RESPUESTA", "episodes": [dict(_EP)], **_PROFILE}),
    # Compró después de mostrar interés: ya no es «sin compra».
    ("wa_10000000002", {"tag": "COMPRA_EXITOSA", **_PROFILE,
                        "episodes": [dict(_EP), {"episode_id": "ep_002", "started_at_ms": 9_000, "order_id": "o_1"}]}),
    ("wa_10000000003", {"tag": "INTERESADO", "episodes": [dict(_EP)]}),
    ("wa_10000000004", {"tag": "HUMANO", "episodes": [dict(_EP)], **_PROFILE}),
    ("wa_10000000005", {"tag": "SIN_RESPUESTA", "marketing_opt_out": True, "episodes": [dict(_EP)], **_PROFILE}),
]


def test_the_profile_segment_brings_who_wanted_to_buy_and_did_not() -> None:
    audience = resolve_campaign_audience(_campaign([SEGMENT_INTERESADOS_SIN_COMPRA]), SESSIONS)

    assert [(r.session_id, r.segment) for r in audience.recipients] == [("wa_10000000001", SEGMENT_INTERESADOS_SIN_COMPRA)]
    reasons = {s.session_id: s.reason for s in audience.skipped}
    assert reasons["wa_10000000002"] == "fuera_de_segmento"
    assert reasons["wa_10000000003"] == "fuera_de_segmento"
    assert reasons["wa_10000000004"] == "excluido"
    assert reasons["wa_10000000005"] == "dado_de_baja"


def test_with_another_segment_it_adds_up_without_repeating() -> None:
    sessions = [*SESSIONS, ("wa_10000000006", {"tag": "INTERESADO", "episodes": [dict(_EP)], **_PROFILE})]

    audience = resolve_campaign_audience(_campaign(["interesados", SEGMENT_INTERESADOS_SIN_COMPRA]), sessions)

    ids = [r.session_id for r in audience.recipients]
    assert ids == ["wa_10000000001", "wa_10000000003", "wa_10000000006"]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"episodes": [dict(_EP)], **_PROFILE}, True),
        ({"episodes": [{**_EP, "order_id": "o_1"}], **_PROFILE}, False),
        ({"episodes": [dict(_EP)]}, False),
    ],
)
def test_interested_without_purchase(metadata: dict, expected: bool) -> None:
    assert interested_without_purchase(metadata) is expected


DAY = 86_400_000
_BOUGHT = {"episode_id": "ep_001", "started_at_ms": 1_000, "closed_at_ms": 10 * DAY,
           "closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "order_id": "o_1"}


def _mark(episode_id: str, at_ms: int) -> dict:
    return {"profile_tags": {"interesado_sin_compra": {"at_ms": at_ms, "episode_id": episode_id, "source": "jev"}}}


@pytest.mark.parametrize(
    "metadata",
    [
        # Revisión del 2026-10-20: «ya te transferí» abría un episodio de posventa.
        {"episodes": [dict(_BOUGHT), {"episode_id": "ep_002", "started_at_ms": 10 * DAY + 60_000,
                                       "after_order": {"order_id": "o_1"}}], **_mark("ep_002", 10 * DAY + 90_000)},
        # Sin la marca `after_order` (Medusa no respondió): compró hace 2 días.
        {"episodes": [dict(_BOUGHT), {"episode_id": "ep_002", "started_at_ms": 12 * DAY}], **_mark("ep_002", 12 * DAY)},
        # La marca apunta a un episodio que ya no está (una marca vieja): el
        # respaldo empieza en el episodio en curso a la hora de la marca.
        {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1_000, "order_id": "o_1"}], **_mark("ep_xxx", 5_000)},
    ],
)
def test_who_just_bought_is_not_interested_without_purchase(metadata: dict) -> None:
    assert interested_without_purchase(metadata) is False


def test_interest_long_after_an_old_purchase_counts() -> None:
    metadata = {"episodes": [dict(_BOUGHT), {"episode_id": "ep_002", "started_at_ms": 60 * DAY}], **_mark("ep_002", 60 * DAY)}

    assert interested_without_purchase(metadata) is True


def test_what_ventas_stamps_is_what_the_audience_reads() -> None:
    """El contrato entre plugins es el metadata de la persona: lo que marca
    `chats/shared/customer_profiles` lo lee el público de marketing."""
    from src.plugins.chats.shared import customer_profiles as cp

    metadata = {"episodes": [dict(_EP)]}
    cp.stamp_interest(metadata, now_ms=5_000, source="jev")

    assert interested_without_purchase(metadata) is True


def test_the_fallback_reads_the_time_ventas_stamps() -> None:
    """Si el episodio de la marca ya no está, el público toma el que estaba en
    curso a la hora de la marca: esa hora es la que escribe Ventas (`at_ms`).
    Sin ella caería en el primer episodio, el de la compra vieja."""
    from src.plugins.chats.shared import customer_profiles as cp

    metadata = {"episodes": [dict(_BOUGHT), {"episode_id": "ep_002", "started_at_ms": 60 * DAY}]}
    cp.stamp_interest(metadata, now_ms=60 * DAY + 5_000, source="jev")
    metadata["episodes"][-1]["episode_id"] = "ep_003"  # el episodio de la marca ya no está

    assert interested_without_purchase(metadata) is True


# ── la API ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(_isolate_vault_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/marketing")
    return TestClient(app)


def _seed(vault: Path, sid: str, metadata: dict) -> None:
    (vault / sid).mkdir(parents=True, exist_ok=True)
    (vault / sid / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_the_segments_endpoint_counts_the_profile(client: TestClient, _isolate_vault_dir: Path) -> None:
    for sid, metadata in SESSIONS:
        _seed(_isolate_vault_dir, sid, metadata)

    body = client.get("/api/marketing/segments").json()

    by_key = {s["key"]: s for s in body["segments"]}
    assert by_key[SEGMENT_INTERESADOS_SIN_COMPRA]["count"] == 1
    assert by_key[SEGMENT_INTERESADOS_SIN_COMPRA]["label"] == "Interesados sin compra"
    # Los segmentos de siempre no cambian.
    assert by_key["frios"]["count"] == 1 and by_key["clientes"]["count"] == 1


def test_a_campaign_can_choose_the_profile_segment(client: TestClient) -> None:
    campaign_id = client.post("/api/marketing/campaigns", json={"name": "5 %"}).json()["id"]

    res = client.put(f"/api/marketing/campaigns/{campaign_id}", json={"segments": [SEGMENT_INTERESADOS_SIN_COMPRA]})

    assert res.status_code == 200 and res.json()["segments"] == [SEGMENT_INTERESADOS_SIN_COMPRA]
