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


def test_mensaje_de_campana_no_guarda_pie_ni_boton(client: TestClient) -> None:
    # La plantilla aprobada solo tiene cuerpo (saludo + mensaje + oferta +
    # baja fija): pie y botón no viajan, así que tampoco se guardan. Un
    # dashboard viejo que los mande no rompe el PUT: se ignoran.
    campaign_id = client.post("/api/marketing/campaigns", json={"name": "A"}).json()["id"]
    created = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert created["message"] == {"header": "", "body": ""}
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"message": {"header": "H", "body": "B", "footer": "Pie", "cta": "Ver"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["message"] == {"header": "H", "body": "B"}


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
    # La sesión del vault es `wa_57…` (sin `+`, como llega del webhook): el
    # número tecleado con `+57` y espacios tiene que caer en ESA sesión.
    _seed_session(
        _isolate_vault_dir, "wa_573001234567", {"tag": "INTERESADO", "profile": {"name": "Ana Ruiz"}}
    )
    sent = []

    async def _fake_send(session_id, template_name, variables):
        sent.append((session_id, template_name, variables))
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "+57 300 123 4567"},
    )
    assert res.status_code == 200, res.text
    assert sent[0][0] == "wa_573001234567"
    assert sent[0][1] == "campaign_promo_marketing_v1"
    assert sent[0][2]["greeting"] == "Hola Ana"
    # La prueba queda en el historial de la campaña, con el número normalizado.
    saved = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert saved["test_sends"][0]["phone"] == "573001234567"
    # El contacto queda marcado con la campaña (como prueba): si responde, el
    # bot sabe qué recibió — igual que en el envío real.
    metadata = json.loads(
        (_isolate_vault_dir / "wa_573001234567" / "metadata.json").read_text()
    )
    touch = metadata["campaign_touches"][-1]
    assert touch["campaign_id"] == campaign_id
    assert touch["test"] is True
    assert touch["message"]
    # El tag del contacto sobrevive (merge, no clobber).
    assert metadata["tag"] == "INTERESADO"


def test_test_send_celular_sin_indicativo_recibe_el_57(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    # Caso real: el operador escribe "3229…" (10 dígitos) y el endpoint buscaba
    # `wa_3229…` — la sesión existe como `wa_573229…`. Misma regla que el CSV.
    _seed_session(_isolate_vault_dir, "wa_573001234567", {"tag": "INTERESADO"})
    sent = []

    async def _fake_send(session_id, template_name, variables):
        sent.append(session_id)
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": "300 123 4567"}
    )
    assert res.status_code == 200, res.text
    assert sent == ["wa_573001234567"]


def test_test_send_sin_sesion_envia_igual_con_el_numero_del_negocio(
    client: TestClient, monkeypatch
) -> None:
    # Igual que los contactos importados: el envío usa el número del negocio
    # (WHATSAPP_PHONE_NUMBER_ID) y el primer mensaje crea la sesión. Exigir
    # conversación previa dejaba al operador sin poder probar desde su celular.
    sent = []

    async def _fake_send(session_id, template_name, variables):
        sent.append((session_id, variables))
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "3002223344"},
    )
    assert res.status_code == 200, res.text
    assert sent[0][0] == "wa_573002223344"
    assert sent[0][1]["greeting"] == "Hola"  # sin nombre conocido: saludo neutro


def test_test_send_numero_no_usable_es_422(client: TestClient, monkeypatch) -> None:
    async def _fake_send(session_id, template_name, variables):  # pragma: no cover
        raise AssertionError("no debe llegar al send")

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)
    for phone in ("6012345678", "12345 67", "abc-def-ghij"):  # fijo, corto, letras
        res = client.post(
            f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": phone}
        )
        assert res.status_code == 422, (phone, res.text)
        assert "celular" in res.json()["detail"].lower()


def test_test_send_rechazo_de_meta_es_502_con_el_motivo(
    client: TestClient, monkeypatch
) -> None:
    # El send lanza (plantilla no aprobada, número inválido para Meta, config
    # faltante): antes era un 500 pelado; el operador tiene que ver el motivo.
    from temporalio.exceptions import ApplicationError

    async def _fake_send(session_id, template_name, variables):
        raise ApplicationError(
            "WhatsApp template send failed (non-retryable, code=132001): "
            "Template name does not exist in the translation",
            non_retryable=True,
            type="TemplateMetaError132001",
        )

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": "3002223344"}
    )
    assert res.status_code == 502, res.text
    assert "132001" in res.json()["detail"]
    # Un envío fallido NO queda en el historial de pruebas.
    saved = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert saved["test_sends"] == []


def test_test_send_rechaza_un_numero_que_es_una_ruta(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    # El `phone` del body arma `wa_<phone>` y ese id termina en un Path del
    # vault: con una sesión real delante, `/../../x` resolvía FUERA del vault
    # (`<padre del vault>/x/metadata.json`) y el envío de prueba salía igual.
    _seed_session(_isolate_vault_dir, "wa_15550001111", {})
    _seed_session(_isolate_vault_dir.parent, "x", {"customer_name": "canario"})
    sent = []

    async def _fake_send(session_id, template_name, variables):
        sent.append(session_id)
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    campaign_id = _ready_campaign(client)

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "15550001111/../../x"},
    )

    assert res.status_code == 422
    assert sent == []


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

    # Se dio de baja por esta campaña (Meta la rechazó con 131050).
    _seed_session(
        vault,
        "wa_+574",
        {
            "campaign_touches": [touch],
            "marketing_opt_out": True,
            "marketing_opt_out_at_ms": t0 + hour,
            "marketing_opt_out_source": "meta",
            "marketing_opt_out_campaign_id": campaign_id,
        },
    )

    res = client.get(f"/api/marketing/campaigns/{campaign_id}/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["sent"] == 3
    assert stats["spent_usd_micros"] == 37500
    assert stats["replied"] == 2
    assert stats["attributed_orders"] == 1
    assert stats["attributed_revenue_cop"] == 44000
    assert stats["opted_out"] == 1


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


def test_get_campaign_audience_muestra_las_bajas_con_fecha_y_campana(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    vault = _isolate_vault_dir
    otra = client.post("/api/marketing/campaigns", json={"name": "Promo madre"}).json()
    _seed_session(
        vault,
        "wa_+571",
        {
            "tag": "COMPRA_EXITOSA",
            "marketing_opt_out": True,
            "marketing_opt_out_at_ms": 1_750_000_000_000,
            "marketing_opt_out_source": "texto",
            "marketing_opt_out_campaign_id": otra["id"],
        },
    )
    # Baja cuya campaña ya no existe (borrada) o baja vieja sin detalle.
    _seed_session(
        vault,
        "wa_+572",
        {"tag": "COMPRA_EXITOSA", "marketing_opt_out": True,
         "marketing_opt_out_campaign_id": "mkt-borrada"},
    )
    campaign_id = _ready_campaign(client)  # segments=["clientes"]

    body = client.get(f"/api/marketing/campaigns/{campaign_id}/audience").json()
    assert body["recipients"] == []
    by_id = {s["session_id"]: s for s in body["skipped"]}
    assert by_id["wa_+571"] == {
        "session_id": "wa_+571",
        "phone": "+571",
        "reason": "dado_de_baja",
        "opted_out_at_ms": 1_750_000_000_000,
        "opted_out_source": "texto",
        "opted_out_campaign_id": otra["id"],
        "opted_out_campaign_name": "Promo madre",
    }
    assert by_id["wa_+572"]["reason"] == "dado_de_baja"
    assert by_id["wa_+572"]["opted_out_campaign_id"] == "mkt-borrada"
    assert by_id["wa_+572"]["opted_out_campaign_name"] is None
    assert by_id["wa_+572"]["opted_out_at_ms"] is None
    assert body["opted_out_count"] == 2

def test_get_campaign_audience_oculta_las_sesiones_de_prueba(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    _seed_session(
        _isolate_vault_dir,
        "wa_573000000004",
        {"tag": "COMPRA_EXITOSA", "seeded_test": True},
    )
    _seed_session(_isolate_vault_dir, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    campaign_id = _ready_campaign(client)  # segments=["clientes"]
    body = client.get(f"/api/marketing/campaigns/{campaign_id}/audience").json()
    assert [r["session_id"] for r in body["recipients"]] == ["wa_+571"]
    # Ni en "No reciben": es ruido de desarrollo, no un contacto.
    assert body["skipped"] == []
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


def test_get_audience_conversation_rechaza_salto_de_linea_final(
    client: TestClient,
) -> None:
    # El `$` de `re.match` acepta un `\n` final: `wa_123%0A` pasaba el guard.
    res = client.get("/api/marketing/audience/wa_123%0A/conversation")
    assert res.status_code == 422


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


@pytest.mark.parametrize("field", ["excluded_session_ids", "extra_session_ids"])
def test_put_id_con_salto_de_linea_final_es_422_y_no_se_guarda(
    client: TestClient, _isolate_vault_dir: Path, field: str
) -> None:
    # Un id pegado con su `\n` quedaba guardado en la curaduría: un "quitado
    # por el operador" que no matchea ninguna sesión → a ese cliente le llega.
    # (El dir con `\n` existe para que `extra` no se salve por "sin sesión".)
    _seed_session(_isolate_vault_dir, "wa_123\n", {})
    campaign_id = _ready_campaign(client)

    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}", json={field: ["wa_123\n"]}
    )

    assert res.status_code == 422
    assert "session_id inválido" in res.json()["detail"]
    saved = CampaignStore(_isolate_vault_dir).get(campaign_id)
    assert not saved.get(field)


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


def test_get_segments_no_cuenta_sesiones_de_prueba(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    # Incidente 2026-09-22: la card de "clientes" decía 16 y la audiencia 4 —
    # 12 eran sesiones sembradas para probar Ads (`seeded_test`), que la
    # audiencia ya saltaba pero el conteo no. Misma regla en los dos.
    vault = _isolate_vault_dir
    _seed_session(vault, "wa_+571", {"tag": "COMPRA_EXITOSA"})
    _seed_session(vault, "wa_573000000004", {"tag": "COMPRA_EXITOSA", "seeded_test": True})
    _seed_session(vault, "wa_573000000005", {"tag": "INTERESADO", "seeded_test": True})

    body = client.get("/api/marketing/segments").json()
    by_key = {s["key"]: s for s in body["segments"]}
    assert by_key["clientes"]["count"] == 1
    assert by_key["interesados"]["count"] == 0
    # Tampoco son "excluidos" (humano/baja): no son contactos.
    assert body["excluded_count"] == 0


# --- Importación de contactos (CSV) ----------------------------------------


def _import(client: TestClient, campaign_id: str, content: str, name="lista.csv"):
    return client.post(
        f"/api/marketing/campaigns/{campaign_id}/contacts/import",
        files={"file": (name, content.encode("utf-8"), "text/csv")},
    )


def test_import_contacts_csv_persiste_y_resume(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    res = _import(
        client,
        campaign_id,
        "nombre,telefono\nCamila,3001234567\nPepe,basura\nAna,300 123 4567\n",
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 1
    assert body["rejected"] == [{"line": 3, "reason": "numero_invalido"}]
    assert body["total"] == 1
    saved = client.get(f"/api/marketing/campaigns/{campaign_id}").json()
    assert saved["imported_contacts"] == [
        {"phone": "573001234567", "name": "Camila"}
    ]


def test_import_contacts_mergea_sin_duplicar_con_lo_ya_importado(
    client: TestClient,
) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    _import(client, campaign_id, "3001234567\n")
    res = _import(client, campaign_id, "3001234567\n3109876543\n")
    assert res.json()["imported"] == 1
    assert res.json()["total"] == 2


def test_import_contacts_rechaza_archivo_no_texto_y_muy_grande(
    client: TestClient,
) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/contacts/import",
        files={"file": ("foto.png", b"\x89PNG\r\n", "image/png")},
    )
    assert res.status_code == 415
    big = "3001234567\n" * 200_000  # > 1 MB
    res = _import(client, campaign_id, big)
    assert res.status_code == 413


def test_import_contacts_en_campana_enviada_es_409(
    client: TestClient, _isolate_vault_dir: Path
) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    store = CampaignStore(_isolate_vault_dir)
    campaign = store.get(campaign_id)
    campaign["status"] = "sent"
    store.save(campaign)
    assert _import(client, campaign_id, "3001234567\n").status_code == 409


def test_delete_contacts_limpia_la_lista(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    _import(client, campaign_id, "3001234567\n")
    res = client.delete(f"/api/marketing/campaigns/{campaign_id}/contacts")
    assert res.status_code == 200
    assert res.json()["imported_contacts"] == []


def test_send_con_solo_importados_es_valido(client: TestClient, monkeypatch) -> None:
    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"goal": "launch", "message": {"body": "Nueva colección."}},
    )
    _import(client, campaign_id, "3001234567\n")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 200, res.text


def test_audience_incluye_importados_sin_sesion(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "Feria"}
    ).json()["id"]
    _import(client, campaign_id, "nombre,telefono\nCamila,3001234567\n")
    res = client.get(f"/api/marketing/campaigns/{campaign_id}/audience")
    assert res.status_code == 200
    assert res.json()["recipients"] == [
        {
            "session_id": "wa_573001234567",
            "phone": "573001234567",
            "customer_name": "Camila",
            "segment": "importados",
        }
    ]


# --- Carrusel de productos --------------------------------------------------


def test_put_carousel_handles_valida_cantidad(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    ok = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"carousel_handles": ["vela-buda", "cubo-love"]},
    )
    assert ok.status_code == 200
    assert ok.json()["carousel_handles"] == ["vela-buda", "cubo-love"]
    # 1 producto no es carrusel (Meta: 2..10); 11 tampoco.
    assert (
        client.put(
            f"/api/marketing/campaigns/{campaign_id}",
            json={"carousel_handles": ["solo-uno"]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/marketing/campaigns/{campaign_id}",
            json={"carousel_handles": [f"p{i}" for i in range(11)]},
        ).status_code
        == 422
    )
    # Vaciar = volver a la plantilla simple.
    cleared = client.put(
        f"/api/marketing/campaigns/{campaign_id}", json={"carousel_handles": []}
    )
    assert cleared.json()["carousel_handles"] == []


def test_put_carousel_handles_dedup_y_rechaza_handles_raros(client: TestClient) -> None:
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"carousel_handles": ["vela-buda", "vela-buda", "cubo-love"]},
    )
    assert res.status_code == 200
    assert res.json()["carousel_handles"] == ["vela-buda", "cubo-love"]
    assert (
        client.put(
            f"/api/marketing/campaigns/{campaign_id}",
            json={"carousel_handles": ["../etc", "cubo-love"]},
        ).status_code
        == 422
    )


def test_test_send_con_carrusel_manda_las_tarjetas(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    from src.sdk.messagingkit import CarouselCard

    _seed_session(_isolate_vault_dir, "wa_573001234567", {"tag": "INTERESADO"})
    sent = []

    async def _fake_send(session_id, template_name, variables, **kwargs):
        sent.append((session_id, template_name, variables, kwargs))
        return type("R", (), {"wa_message_id": "wamid-1", "ok": True, "error": None})()

    cards = [
        CarouselCard(body_text="A · $1", product_retailer_id="HUB-A", catalog_id="868"),
        CarouselCard(body_text="B · $2", product_retailer_id="HUB-B", catalog_id="868"),
    ]

    async def _fake_resolve(campaign, *, now_ms):
        return cards

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    monkeypatch.setattr(api_mod, "resolve_campaign_carousel", _fake_resolve)
    campaign_id = _ready_campaign(client)
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"carousel_handles": ["a", "b"]},
    )
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "300 123 4567"},
    )
    assert res.status_code == 200, res.text
    assert sent[0][1] == "campaign_carousel_marketing_v1_2"
    assert sent[0][3]["carousel_cards"] == cards


def test_test_send_con_carrusel_roto_es_422_legible(
    client: TestClient, _isolate_vault_dir: Path, monkeypatch
) -> None:
    from src.plugins.marketing.carousel import CarouselError

    _seed_session(_isolate_vault_dir, "wa_573001234567", {"tag": "INTERESADO"})

    async def _fake_resolve(campaign, *, now_ms):
        raise CarouselError("producto 'a' sin foto en el catálogo")  # mensaje del resolver

    monkeypatch.setattr(api_mod, "resolve_campaign_carousel", _fake_resolve)
    campaign_id = _ready_campaign(client)
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"carousel_handles": ["a", "b"]},
    )
    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/test",
        json={"phone": "300 123 4567"},
    )
    assert res.status_code == 422
    assert "sin foto" in res.json()["detail"]


# --- Cupones (promociones de Medusa) ----------------------------------------


def test_put_coupon_code_rechaza_forma_de_tag_interno(client: TestClient) -> None:
    """`VELAS_10` dispara el guard anti-leak del bot (enmudece): el cupón solo
    admite letras y números (memoria coupon-tag-shape-collision)."""
    campaign_id = client.post(
        "/api/marketing/campaigns", json={"name": "A"}
    ).json()["id"]
    res = client.put(
        f"/api/marketing/campaigns/{campaign_id}", json={"coupon_code": "velas_10"}
    )
    assert res.status_code == 422
    assert "VELAS_10" in res.json()["detail"]
    for bad in ("PAPA-20", "MAMA 15"):
        assert (
            client.put(
                f"/api/marketing/campaigns/{campaign_id}", json={"coupon_code": bad}
            ).status_code
            == 422
        )
    ok = client.put(f"/api/marketing/campaigns/{campaign_id}", json={"coupon_code": " mama15 "})
    assert ok.status_code == 200 and ok.json()["coupon_code"] == "MAMA15"
    cleared = client.put(f"/api/marketing/campaigns/{campaign_id}", json={"coupon_code": ""})
    assert cleared.json()["coupon_code"] == ""


def test_get_promotions_lista_los_cupones_vigentes_de_medusa(
    client: TestClient, monkeypatch
) -> None:
    from src.sdk.connectorkit import FakePromotionsPort, PromotionDTO

    promo = PromotionDTO(
        id="p1", code="MAMA15", discount_type="percentage", value=15, currency_code="cop",
        target_type="items", allocation="across", max_quantity=None,
        product_ids=("prod_a",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=1_800_000_000_000,
        budget_type=None, budget_limit=None, budget_used=None, description="Madres",
    )
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    res = client.get("/api/marketing/promotions")
    assert res.status_code == 200
    body = res.json()
    assert body["unavailable"] is False
    assert body["promotions"] == [
        {
            "code": "MAMA15",
            "discount_type": "percentage",
            "value": 15,
            "target_type": "items",
            "name": "Madres",
            "ends_at_ms": 1_800_000_000_000,
            "min_subtotal_cop": None,
            "product_count": 1,
        }
    ]


def test_get_promotions_no_ofrece_cupones_de_envio(client: TestClient, monkeypatch) -> None:
    """El builder ofrece solo los cupones que el bot aplica: sin cupones de
    envío (decisión del operador, 2026-09-23)."""
    from src.sdk.connectorkit import FakePromotionsPort

    monkeypatch.setattr(
        api_mod, "get_promotions_port",
        lambda: FakePromotionsPort(
            [_promo_dto("AMOR26"), _promo_dto("ENVIOGRATIS", target_type="shipping_methods")]
        ),
    )
    res = client.get("/api/marketing/promotions")
    assert [p["code"] for p in res.json()["promotions"]] == ["AMOR26"]


def test_get_promotions_con_medusa_caido_es_vacio_y_lo_dice(
    client: TestClient, monkeypatch
) -> None:
    from src.sdk.connectorkit import PromotionsUnavailableError

    class Down:
        async def list_active(self):
            raise PromotionsUnavailableError("timeout")

    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: Down())
    res = client.get("/api/marketing/promotions")
    assert res.status_code == 200
    assert res.json() == {"promotions": [], "unavailable": True}


# --- Producto único retirado (los productos van en el carrusel) -------------


def test_campana_nueva_no_tiene_producto_unico_y_el_put_lo_ignora(client: TestClient) -> None:
    """El selector de producto único se quitó del builder: los productos de
    la campaña son los del carrusel. Un dashboard viejo en caché que todavía
    mande `product_handle` no rompe el PUT ni persiste el campo."""
    created = client.post("/api/marketing/campaigns", json={"name": "A"}).json()
    assert "product_handle" not in created
    res = client.put(
        f"/api/marketing/campaigns/{created['id']}",
        json={"product_handle": "vela-buda", "goal": "discount_product"},
    )
    assert res.status_code == 200
    assert "product_handle" not in res.json()
    assert res.json()["goal"] == "discount_product"


def test_send_producto_existente_sin_producto_unico_arranca(client: TestClient, monkeypatch) -> None:
    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    campaign_id = client.post("/api/marketing/campaigns", json={"name": "P"}).json()["id"]
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={
            "goal": "discount_product",
            "percent": 10,
            "segments": ["clientes"],
            "carousel_handles": ["vela-buda", "cubo-love"],
            "message": {"body": "Velas con 10%."},
        },
    )
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 200, res.text



# --- El cupón que anuncia la campaña tiene que existir en Medusa -------------
# Incidente 2026-09-22: la campaña anunció "AMOR" y el código real era AMOR26.


def _promo_dto(code: str, **over):
    from src.sdk.connectorkit import PromotionDTO

    base = dict(
        id=f"p_{code}", code=code, discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="each", max_quantity=None,
        product_ids=("prod_a",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
        budget_type=None, budget_limit=None, budget_used=None, description=None,
    )
    base.update(over)
    return PromotionDTO(**base)


def _campaign_with_coupon(client: TestClient, code: str) -> str:
    campaign_id = _ready_campaign(client)
    client.put(f"/api/marketing/campaigns/{campaign_id}", json={"coupon_code": code})
    return campaign_id


def test_send_con_cupon_inexistente_en_medusa_es_422(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([_promo_dto("AMOR26")]))
    campaign_id = _campaign_with_coupon(client, "AMOR")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 422
    assert "AMOR" in res.json()["detail"] and "Medusa" in res.json()["detail"]
    assert fake.calls == []


def test_send_con_cupon_vencido_es_422(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(
        api_mod, "get_promotions_port",
        lambda: FakePromotionsPort([_promo_dto("AMOR26", ends_at_ms=1_000)]),
    )
    campaign_id = _campaign_with_coupon(client, "AMOR26")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 422
    assert "venció" in res.json()["detail"]


def test_send_con_cupon_de_envio_es_422_y_explica_por_que(client: TestClient, monkeypatch) -> None:
    """El envío lo cobra la transportadora sin descuentos (decisión del
    operador, 2026-09-23): el bot rechaza los cupones de envío, así que una
    campaña no puede anunciarlos."""
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(
        api_mod, "get_promotions_port",
        lambda: FakePromotionsPort([_promo_dto("ENVIOGRATIS", target_type="shipping_methods")]),
    )
    campaign_id = _campaign_with_coupon(client, "ENVIOGRATIS")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 422
    assert "transportadora" in res.json()["detail"]
    assert fake.calls == []


def test_send_con_cupon_valido_arranca(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([_promo_dto("AMOR26")]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1


def test_test_send_con_cupon_inexistente_es_422_sin_enviar(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    async def _fake_send(*a, **kw):  # pragma: no cover
        raise AssertionError("no debe enviar")

    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([_promo_dto("AMOR26")]))
    campaign_id = _campaign_with_coupon(client, "AMOR")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": "3001234567"})
    assert res.status_code == 422
    assert "AMOR" in res.json()["detail"]


def test_send_con_medusa_caido_no_puede_validar_el_cupon(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import PromotionsUnavailableError

    class Down:
        async def list_active(self):
            raise PromotionsUnavailableError("timeout")

        async def get_by_code(self, code):
            raise PromotionsUnavailableError("timeout")

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: Down())
    campaign_id = _campaign_with_coupon(client, "AMOR26")
    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})
    assert res.status_code == 503
    assert "cupón" in res.json()["detail"]
    assert fake.calls == []


def test_campaign_uses_coupon_percent_and_inclusive_end_date(
    client: TestClient, monkeypatch, _isolate_vault_dir: Path
) -> None:
    """Central de cupones (Fase 7.1): la campaña ya no inventa el % ni el
    "válido hasta" — salen del cupón elegido, con el último día incluido en
    hora de Bogotá (campaña hasta 28-sep 00:00 Bogotá = "27 de septiembre")."""
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    promo = _promo_dto("AMOR26", value=15, ends_at_ms=1_790_571_600_000)  # 2026-09-28T05:00Z
    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")
    client.put(
        f"/api/marketing/campaigns/{campaign_id}",
        json={"percent": 40, "valid_until": "cuando quieras"},
    )

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})

    assert res.status_code == 200, res.text
    saved = CampaignStore(_isolate_vault_dir).get(campaign_id)
    assert (saved["percent"], saved["valid_until"]) == (15, "27 de septiembre")



def test_test_send_announces_the_coupon_terms_not_what_the_operator_typed(
    client: TestClient, monkeypatch
) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    sent: list[dict] = []

    async def _fake_send(session_id, template, variables, **kw):
        sent.append(variables)
        return type("R", (), {"wa_message_id": "wamid.x"})()

    promo = _promo_dto("AMOR26", value=15, ends_at_ms=1_790_571_600_000)  # hasta 27-sep
    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")
    client.put(f"/api/marketing/campaigns/{campaign_id}", json={"valid_until": "cuando quieras"})

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": "3001234567"})

    assert res.status_code == 200, res.text
    assert "27 de septiembre" in json.dumps(sent[0], ensure_ascii=False)
    assert "cuando quieras" not in json.dumps(sent[0], ensure_ascii=False)


# --- El cupón se valida para el INSTANTE del envío (premortem A1) --------------

_DAY_MS = 24 * 3_600_000


def _now_ms() -> int:
    import time as _time

    return int(_time.time() * 1000)


def _started_fake_temporal(monkeypatch) -> _FakeTemporalClient:
    fake = _FakeTemporalClient()

    async def _fake_client():
        return fake

    monkeypatch.setattr(api_mod, "get_temporal_client", _fake_client)
    return fake


def test_scheduling_a_send_after_a_programmed_coupon_starts_is_accepted(
    client: TestClient, monkeypatch
) -> None:
    """El constructor ofrece cupones PROGRAMADOS: programar el envío para
    cuando ya rigen no puede dar 422 "todavía no empieza"."""
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _started_fake_temporal(monkeypatch)
    now = _now_ms()
    promo = _promo_dto("AMOR26", starts_at_ms=now + _DAY_MS, ends_at_ms=now + 10 * _DAY_MS)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/send", json={"schedule_at_ms": now + 2 * _DAY_MS}
    )

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1


def test_scheduling_a_send_after_the_coupon_ends_is_422_even_if_it_rules_today(
    client: TestClient, monkeypatch
) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _started_fake_temporal(monkeypatch)
    now = _now_ms()
    promo = _promo_dto("AMOR26", ends_at_ms=now + _DAY_MS)  # vigente hoy, vence mañana
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")

    res = client.post(
        f"/api/marketing/campaigns/{campaign_id}/send", json={"schedule_at_ms": now + 3 * _DAY_MS}
    )

    assert res.status_code == 422
    assert "envío programado" in res.json()["detail"]
    assert fake.calls == []


def test_sending_now_a_coupon_that_has_not_started_is_still_422(client: TestClient, monkeypatch) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    fake = _started_fake_temporal(monkeypatch)
    promo = _promo_dto("AMOR26", starts_at_ms=_now_ms() + _DAY_MS)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([promo]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/send", json={})

    assert res.status_code == 422
    assert "todavía no empieza" in res.json()["detail"]
    assert fake.calls == []


@pytest.mark.parametrize(
    ("over", "accepted", "fragment"),
    [
        ({"starts_at_ms": "tomorrow"}, True, None),  # programado: la prueba sale
        ({"status": "inactive"}, False, "pausado"),
        ({"status": "draft"}, False, "borrador"),
        ({"ends_at_ms": 1_000}, False, "venció"),
    ],
)
def test_test_send_accepts_a_programmed_coupon_but_not_a_paused_draft_or_expired_one(
    client: TestClient, monkeypatch, over, accepted, fragment
) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    sent: list[dict] = []

    async def _fake_send(session_id, template, variables, **kw):
        sent.append(variables)
        return type("R", (), {"wa_message_id": "wamid.x"})()

    over = {k: (_now_ms() + _DAY_MS if v == "tomorrow" else v) for k, v in over.items()}
    monkeypatch.setattr(api_mod, "send_template_to_session", _fake_send)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([_promo_dto("AMOR26", **over)]))
    campaign_id = _campaign_with_coupon(client, "AMOR26")

    res = client.post(f"/api/marketing/campaigns/{campaign_id}/test", json={"phone": "3001234567"})

    if accepted:
        assert res.status_code == 200, res.text
        assert len(sent) == 1
    else:
        assert res.status_code == 422
        assert fragment in res.json()["detail"]
        assert sent == []
