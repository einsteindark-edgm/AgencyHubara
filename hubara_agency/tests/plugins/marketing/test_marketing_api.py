"""Wiring HTTP del plugin marketing — router montado en app local + vault tmp."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.marketing.api as api_mod
from src.plugins.marketing.campaign_store import CampaignStore


@pytest.fixture()
def client(_isolate_vault_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/marketing")
    return TestClient(app)


def _seed_session(vault: Path, session_id: str, metadata: dict) -> None:
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


# --- CRUD -------------------------------------------------------------------


def test_post_campaigns_crea_draft_persistido(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    res = client.post("/api/marketing/campaigns", json={"name": "Promo madre"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Promo madre"
    assert body["status"] == "draft"
    assert body["id"].startswith("mkt-")
    assert CampaignStore(_isolate_vault_dir).get(body["id"]) is not None


def test_get_campaigns_lista_las_guardadas(client: TestClient) -> None:
    client.post("/api/marketing/campaigns", json={"name": "A"})
    client.post("/api/marketing/campaigns", json={"name": "B"})
    res = client.get("/api/marketing/campaigns")
    assert res.status_code == 200
    assert {c["name"] for c in res.json()["campaigns"]} == {"A", "B"}


def test_put_campaign_actualiza_campos_editables(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={
            "name": "A renombrada",
            "goal": "discount_general",
            "percent": 15,
            "coupon_code": "mama15",
            "segments": ["clientes"],
            "message": {"header": "H", "body": "B", "footer": "F", "cta": "Ver"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "A renombrada"
    assert body["percent"] == 15
    # El cupón se normaliza a mayúsculas (consistencia con el mensaje).
    assert body["coupon_code"] == "MAMA15"
    assert body["message"]["header"] == "H"
    # id/status/created no se pisan por PUT.
    assert body["id"] == campaign_id
    assert body["status"] == "draft"


def test_put_campana_enviada_es_409(client: TestClient, _isolate_vault_dir) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    store = CampaignStore(_isolate_vault_dir)
    sent = store.get(campaign_id)
    sent["status"] = "sent"
    store.save(sent)
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}", json={"name": "X"}
    )
    assert res.status_code == 409


def test_delete_solo_borra_drafts(client: TestClient, _isolate_vault_dir) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    assert client.delete(f"/api/marketing/campaigns/{campaign_id}").status_code == 204
    assert client.get(f"/api/marketing/campaigns/{campaign_id}").status_code == 404


# --- Segmentos + costos -----------------------------------------------------


class _FakeCatalog:
    async def search(self, q, *, limit=10):
        from src.platform.catalog.dtos import (
            CatalogManifestDTO,
            CatalogPriceDTO,
            CatalogProductDTO,
            CatalogVariantDTO,
            SearchResult,
        )

        products = [
            CatalogProductDTO(
                id="prod_1",
                handle="vela-sagrado-rostro",
                title="Sagrado Rostro",
                status="published",
                thumbnail="https://cdn/x.jpg",
                categories=["Devocionales"],
                variants=[
                    CatalogVariantDTO(
                        id="v1",
                        title="Unico",
                        sku="SKU-VL-014",
                        prices=[CatalogPriceDTO(amount="36000", currency_code="COP")],
                    )
                ],
            )
        ]
        return SearchResult(
            query=q,
            count=len(products),
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1", fetched_at="2026-07-17T00:00:00+00:00", product_count=1
            ),
            results=products,
        )


def test_get_products_lista_el_catalogo_para_el_picker(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(api_mod, "get_catalog_client", lambda: _FakeCatalog())
    res = client.get("/api/marketing/products")
    assert res.status_code == 200
    products = res.json()["products"]
    assert products == [
        {
            "handle": "vela-sagrado-rostro",
            "title": "Sagrado Rostro",
            "sku": "SKU-VL-014",
            "category": "Devocionales",
            "price_amount": "36000",
            "currency": "COP",
            "thumbnail": "https://cdn/x.jpg",
        }
    ]


# --- Enviar / programar / prueba -------------------------------------------


class _FakeHandle:
    first_execution_run_id = "run-1"


class _FakeTemporalClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_workflow(self, workflow_name, *, args, id, task_queue, **kw):
        self.calls.append(
            {
                "workflow": workflow_name,
                "args": args,
                "id": id,
                "task_queue": task_queue,
                "start_delay": kw.get("start_delay"),
            }
        )
        return _FakeHandle()


def _ready_campaign(client: TestClient) -> str:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Promo"}
    ).json()["id"]
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={
            "goal": "discount_general",
            "percent": 15,
            "segments": ["clientes"],
            "message": {"body": "15% en velas hasta el viernes."},
        },
    )
    return campaign_id


def test_send_now_arranca_el_workflow(client: TestClient, monkeypatch) -> None:
    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = _ready_campaign(client)

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 200
    assert res.json()["workflow_id"] == f"campaign-send-{campaign_id}"
    call = fake.calls[0]
    assert call["workflow"] == "CampaignSendWorkflow"
    assert call["args"][0] == campaign_id
    assert call["task_queue"] == "queue-marketing-campaigns"
    assert call["start_delay"] is None


def test_send_programado_usa_start_delay_y_marca_scheduled(
    client: TestClient, monkeypatch
) -> None:
    import time as _time

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = _ready_campaign(client)
    at_ms = int(_time.time() * 1000) + 3_600_000

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/send",
        json={"schedule_at_ms": at_ms},
    )
    assert res.status_code == 200
    assert fake.calls[0]["start_delay"] is not None
    saved = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert saved["status"] == "scheduled"
    assert saved["schedule_at_ms"] == at_ms


def test_send_campana_incompleta_es_422(client: TestClient, monkeypatch) -> None:
    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Vacía"}
    ).json()["id"]
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 422
    assert fake.calls == []


def test_test_send_manda_template_a_la_sesion_del_numero(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    _seed_session(_isolate_vault_dir, "wa_+573125671604", {"tag": "INTERESADO"})
    sent = []

    async def _fake_send(session_id, template_name, variables):
        sent.append((session_id, template_name, variables))
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "+57 312 567 1604"},
    )
    assert res.status_code == 200
    assert sent[0][0] == "wa_+573125671604"
    assert sent[0][1] == "campaign_promo_marketing_v1"
    assert sent[0][2]["greeting"] == "Hola"
    # La prueba queda en el historial de la campaña.
    saved = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert saved["test_sends"][0]["phone"] == "+573125671604"


def test_test_send_numero_sin_sesion_es_404(
    client: TestClient, monkeypatch
) -> None:
    async def _fake_send(session_id, template_name, variables):  # pragma: no cover
        raise AssertionError("no debe llegar al send")

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "+57 300 000 0000"},
    )
    assert res.status_code == 404


class _FakeWorkflowHandle:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel(self) -> None:
        self.cancelled = True


def test_cancel_campana_programada_vuelve_a_draft(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    handle = _FakeWorkflowHandle()

    class _Client:
        def get_workflow_handle(self, workflow_id):
            assert workflow_id.startswith("campaign-send-")
            return handle

    async def _fake_client():
        return _Client()

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = _ready_campaign(client)
    store = CampaignStore(_isolate_vault_dir)
    campaign = store.get(campaign_id)
    campaign["status"] = "scheduled"
    campaign["schedule_at_ms"] = 9_999_999_999_999
    store.save(campaign)

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/cancel")
    assert res.status_code == 200
    assert handle.cancelled is True
    saved = store.get(campaign_id)
    assert saved["status"] == "draft"
    assert saved["schedule_at_ms"] is None


def test_cancel_workflow_desaparecido_igual_resetea(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    """Reconciliación-lite: si Temporal ya no conoce el workflow (purga,
    deploy), el cancel igual devuelve la campaña a draft — sin esto queda
    scheduled huérfana para siempre."""

    class _Client:
        def get_workflow_handle(self, workflow_id):
            raise RuntimeError("workflow not found")

    async def _fake_client():
        return _Client()

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = _ready_campaign(client)
    store = CampaignStore(_isolate_vault_dir)
    campaign = store.get(campaign_id)
    campaign["status"] = "scheduled"
    campaign["schedule_at_ms"] = 9_999_999_999_999
    store.save(campaign)

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/cancel")
    assert res.status_code == 200
    assert store.get(campaign_id)["status"] == "draft"


def test_cancel_campana_no_programada_es_409(client: TestClient) -> None:
    campaign_id = _ready_campaign(client)
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/cancel")
    assert res.status_code == 409


def test_get_campaign_stats_agrega_respuestas_y_revenue(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    vault = _isolate_vault_dir
    t0 = 1_750_000_000_000
    hour = 60 * 60 * 1000
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Promo"}
    ).json()["id"]
    store = CampaignStore(vault)
    campaign = store.get(campaign_id)
    campaign["status"] = "sent"
    campaign["sent_at_ms"] = t0
    campaign["send_result"] = {
        "planned": 3,
        "sent": 3,
        "failed": [],
        "skipped": [],
        "unit_cost_usd_micros": 12500,
        "spent_usd_micros": 37500,
    }
    store.save(campaign)

    touch = {"campaign_id": campaign_id, "campaign_name": "Promo", "sent_at_ms": t0}
    # Respondió y compró (episodio post-touch con venta congelada).
    _seed_session(
        vault,
        "wa_+571",
        {
            "campaign_touches": [touch],
            "last_inbound_at_ms": t0 + hour,
            "episodes": [
                {
                    "episode_id": "ep_1",
                    "started_at_ms": t0 + hour,
                    "closed_at_ms": t0 + 2 * hour,
                    "order_id": "OB-1",
                    "order_total_cop": 44000,
                }
            ],
        },
    )
    # Respondió sin comprar.
    _seed_session(
        vault,
        "wa_+572",
        {"campaign_touches": [touch], "last_inbound_at_ms": t0 + 2 * hour},
    )
    # No respondió (último inbound ANTERIOR al touch).
    _seed_session(
        vault,
        "wa_+573",
        {"campaign_touches": [touch], "last_inbound_at_ms": t0 - hour},
    )

    res = client.get(f"/api/marketing/campaigns/{campaign_id}/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["sent"] == 3
    assert stats["spent_usd_micros"] == 37500
    assert stats["replied"] == 2
    assert stats["attributed_orders"] == 1
    assert stats["attributed_revenue_cop"] == 44000


def test_get_campaign_audience_lista_destinatarios_y_excluidos(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    import time as _time

    vault = _isolate_vault_dir
    now = int(_time.time() * 1000)
    _seed_session(
        vault,
        "wa_+571",
        {
            "tag": "COMPRA_EXITOSA",
            "registered_order": {"customer_name": "Camila Restrepo"},
        },
    )
    _seed_session(vault, "wa_+572", {"tag": "INTERESADO"})  # fuera del segmento
    _seed_session(vault, "wa_+573", {"tag": "HUMANO"})
    _seed_session(
        vault,
        "wa_+574",
        {
            "tag": "COMPRA_EXITOSA",
            "campaign_touches": [
                {"campaign_id": "mkt-otra", "sent_at_ms": now - 3_600_000}
            ],
        },
    )
    campaign_id = _ready_campaign(client)  # segments=["clientes"]

    res = client.get(f"/api/marketing/campaigns/{campaign_id}/audience")
    assert res.status_code == 200
    body = res.json()
    assert body["recipients"] == [
        {
            "session_id": "wa_+571",
            "phone": "+571",
            "customer_name": "Camila",
            "segment": "clientes",
        }
    ]
    skipped = {s["session_id"]: s["reason"] for s in body["skipped"]}
    # Transparencia: excluidos y en cooldown SÍ; "fuera_de_segmento" es ruido.
    assert skipped == {"wa_+573": "excluido", "wa_+574": "campana_reciente"}
    assert body["total"] == 1


def test_get_audience_conversation_devuelve_historial_simplificado(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    import json as _json

    vault = _isolate_vault_dir
    _seed_session(vault, "wa_+571", {"tag": "INTERESADO"})
    history_dir = vault / "wa_+571" / "sessions"
    history_dir.mkdir(parents=True)
    lines = [
        {"role": "user", "content": "Hola, vi la promo", "timestamp": "2026-07-17T10:00:00+00:00"},
        {"role": "assistant", "content": "¡Hola! Te cuento…", "timestamp": "2026-07-17T10:01:00+00:00"},
        {
            "role": "assistant",
            "kind": "template",
            "template_name": "campaign_promo_marketing_v1",
            "content": "[Template: campaign_promo_marketing_v1] greeting=Hola",
            "timestamp": "2026-07-17T10:02:00+00:00",
        },
        "linea corrupta no-json",
    ]
    with (history_dir / "wa_+571.jsonl").open("w", encoding="utf-8") as f:
        for line in lines:
            f.write((line if isinstance(line, str) else _json.dumps(line)) + "\n")

    res = client.get("/api/marketing/audience/wa_+571/conversation")
    assert res.status_code == 200
    messages = res.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "assistant"]
    assert messages[0]["content"] == "Hola, vi la promo"
    assert messages[2]["kind"] == "template"


def test_get_audience_conversation_valida_session_id(client: TestClient) -> None:
    # Path traversal / ids raros → 422, jamás toca el filesystem.
    assert (
        client.get("/api/marketing/audience/..%2F..%2Fetc/conversation").status_code
        in (404, 422)
    )
    assert (
        client.get("/api/marketing/audience/no-wa-prefix/conversation").status_code
        == 422
    )


def test_get_audience_conversation_sin_historial_es_lista_vacia(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    _seed_session(_isolate_vault_dir, "wa_+579", {"tag": "INTERESADO"})
    res = client.get("/api/marketing/audience/wa_+579/conversation")
    assert res.status_code == 200
    assert res.json()["messages"] == []


def test_put_cura_la_audiencia_y_el_endpoint_la_refleja(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+572", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+579", {})  # frío — se agrega a mano
    campaign_id = _ready_campaign(client)  # segments=["clientes"]

    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={
            "excluded_session_ids": ["wa_+572"],
            "extra_session_ids": ["wa_+579"],
        },
    )
    assert res.status_code == 200
    assert res.json()["extra_session_ids"] == ["wa_+579"]

    audience = client.get(
        f"/api/marketing/campaigns/{campaign_id}/audience"
    ).json()
    by_id = {r["session_id"]: r["segment"] for r in audience["recipients"]}
    assert by_id == {"wa_+571": "clientes", "wa_+579": "manual"}
    reasons = {s["session_id"]: s["reason"] for s in audience["skipped"]}
    assert reasons["wa_+572"] == "quitado_por_operador"
    assert audience["total"] == 2


def test_put_extra_inexistente_en_vault_es_422(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    campaign_id = _ready_campaign(client)
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"extra_session_ids": ["wa_+570000000000"]},
    )
    assert res.status_code == 422
    assert "wa_+570000000000" in res.json()["detail"]


def test_put_extra_con_formato_invalido_es_422(client: TestClient) -> None:
    campaign_id = _ready_campaign(client)
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"extra_session_ids": ["../../etc/passwd"]},
    )
    assert res.status_code == 422


def test_get_segments_cuenta_contactos_y_expone_costo(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_+572", {"tag": "INTERESADO"})
    _seed_session(vault, "wa_+573", {"tag": "CONFIRMADO_PAGO_PENDIENTE"})
    _seed_session(vault, "wa_+574", {})
    _seed_session(vault, "wa_+575", {"tag": "HUMANO"})

    res = client.get("/api/marketing/segments")
    assert res.status_code == 200
    body = res.json()
    by_key = {s["key"]: s for s in body["segments"]}
    assert by_key["clientes"]["count"] == 1
    assert by_key["interesados"]["count"] == 2
    assert by_key["frios"]["count"] == 1
    assert body["excluded_count"] == 1
    # Costo claro: tarifa marketing CO vigente por mensaje.
    assert body["unit_cost_usd_micros"] == 12500
    assert body["currency"] == "USD"
