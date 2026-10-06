"""Contrato HTTP de la app móvil del operador (`chats/api/mobile`).

La app Android parsea estas respuestas byte a byte: el test fija las claves.
Las rutas solo juntan hechos (vault, catálogo, `OrderFacts`) — decide
`shared/mobile_rules` (probado aparte, sin HTTP). Acá se verifica que los
hechos REALES del vault llegan a la regla (gotcha 1: comportamiento, no schema).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.catalog.errors import CatalogUnavailableError, ProductNotFoundError
from src.plugins.chats.api import mobile
from src.plugins.chats.api.mobile import MobileDeps
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts

NOW = 1_790_000_000_000  # 2026-09-21 (ms)
_MIN = 60_000

_LAURA = "wa_test_laura"

_DUO = CatalogProductDTO(
    id="p_duo", handle="duo-zodiacal", title="Dúo Zodiacal", status="published",
    thumbnail="https://cdn.test/duo-leo.jpg",
    images=[CatalogImageDTO(url="https://cdn.test/duo-leo.jpg"), CatalogImageDTO(url="https://cdn.test/duo-aries.jpg", rank=1)],
    variants=[CatalogVariantDTO(id="v1", title="Unico", prices=[CatalogPriceDTO(amount="89900", currency_code="cop")])],
    tags=["Aroma: Lavanda", "Aroma: Vainilla", "Color: Azul", "Color: Rosa"],
)
_LUZ = CatalogProductDTO(
    id="p_luz", handle="luz-serena", title="Luz Serena", status="published",
    thumbnail="https://cdn.test/luz.jpg",
    variants=[CatalogVariantDTO(id="v2", title="Unico", prices=[CatalogPriceDTO(amount="29000", currency_code="cop")])],
    tags=["Aroma: Lavanda", "Aroma: Canela"],
)


class _Catalog:
    def __init__(self, products=(_DUO, _LUZ), *, down: bool = False) -> None:
        self._by = {p.handle: p for p in products}
        self._down = down

    async def get_by_handle(self, handle: str):
        if self._down:
            raise CatalogUnavailableError("sin snapshot")
        if handle not in self._by:
            raise ProductNotFoundError(handle)
        return self._by[handle]

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        if self._down:
            raise CatalogUnavailableError("sin snapshot")
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        results = list(self._by.values())[:limit]
        return SearchResult(query=q, count=len(results), truncated=False, stale=False,
                            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=len(results)),
                            results=results)


@dataclass
class _Harness:
    client: TestClient
    vault: Path
    deps: MobileDeps

    def seed(self, session: str, metadata: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        d = self.vault / session
        d.mkdir(parents=True, exist_ok=True)
        (d / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        if events is not None:
            (d / "sessions").mkdir(exist_ok=True)
            (d / "sessions" / f"{session}.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
            )


@pytest.fixture
def h(tmp_path: Path) -> _Harness:
    vault = tmp_path / "vault"
    vault.mkdir()
    deps = MobileDeps(
        vault_dir=vault,
        catalog=_Catalog(),
        order_facts=InMemoryOrderFacts(),
        now_ms=lambda: NOW,
        payment_instructions_text=lambda: "Aquí tienes los datos para tu pago anticipado 🤍",
    )
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats")
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: deps
    return _Harness(TestClient(app), vault, deps)


def _episode(slots: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    ep: dict[str, Any] = {"episode_id": "ep_1", "started_at_ms": NOW - 60 * _MIN, "closed_at_ms": None}
    if slots is not None:
        ep["order_draft"] = {"slots": slots}
    ep.update(extra)
    return ep


def _open_window() -> dict[str, Any]:
    return {"service_window_expires_at_ms": NOW + 20 * 60 * _MIN, "last_inbound_at_ms": NOW - 5 * _MIN}


# ── GET /mobile/suggestions/{session_id} ─────────────────────────────────────


def test_suggestions_read_the_real_draft_and_catalog_of_the_session(h: _Harness) -> None:
    h.seed(_LAURA, {
        **_open_window(),
        "active_route": "humano",
        "episodes": [_episode({"producto": "Dúo Zodiacal", "cantidad": "2"})],
    })

    r = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"session_id", "version", "decided_by", "stage", "window_open", "in_control", "suggestions"}
    assert body["session_id"] == _LAURA and body["decided_by"] == "rules"
    assert body["stage"] == "etapa_variantes" and body["window_open"] is True and body["in_control"] == "human"
    assert isinstance(body["version"], int) and body["version"] > 0
    assert body["suggestions"][0] == {
        "id": "present_variant_picker",
        "label": "Enviar aromas",
        "prominence": "primary",
        "editable": True,
        "action": {"name": "present_variant_picker", "args": {"product": "duo-zodiacal", "attribute": "aroma"}},
    }
    # el Dúo tiene 2 fotos → "Más fotos" es legal
    assert [s["id"] for s in body["suggestions"]] == ["present_variant_picker", "present_product_gallery"]


def test_suggestions_404_for_unknown_or_unsafe_session_ids(h: _Harness) -> None:
    for sid in ("wa_test_nadie", "%2E%2E", "_mobile", "wa_test_laura%0A"):
        r = h.client.get(f"/api/chats/mobile/suggestions/{sid}")
        assert r.status_code == 404, sid
        assert r.json() == {"error": "session_not_found"}


def test_closed_window_means_no_suggestions_and_the_bot_route_is_reported(h: _Harness) -> None:
    h.seed(_LAURA, {"service_window_expires_at_ms": NOW - 1, "active_route": "ventas", "episodes": [_episode()]})

    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()

    assert body["window_open"] is False and body["suggestions"] == []
    assert body["in_control"] == "bot" and body["stage"] == "etapa_descubrimiento"


def test_the_last_card_sent_in_the_jsonl_is_not_suggested_again(h: _Harness) -> None:
    h.seed(
        _LAURA,
        {**_open_window(), "active_route": "humano", "episodes": [_episode()]},
        events=[
            {"role": "user", "content": "hola"},
            {"role": "assistant", "kind": "ui_component", "component_kind": "products_list", "content": "🛍️ catálogo"},
            {"role": "user", "content": "y el envío?"},
        ],
    )

    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()

    assert [s["id"] for s in body["suggestions"]] == ["send_shipping_rates", "send_payment_methods"]


def test_what_the_operator_sent_since_the_customer_last_wrote_is_not_suggested_again(h: _Harness) -> None:
    """Con un humano al mando nada actualiza el borrador: la jugada del operador
    vuelve al estado. Tras mandar los aromas y después las fotos desde la app,
    «Enviar aromas» no vuelve como primaria hasta que el cliente escriba."""
    from src.plugins.chats.api.operator_tools import _record_action
    from src.sdk.runtime import FilesystemMetadataStore

    h.seed(
        _LAURA,
        {**_open_window(), "active_route": "humano", "episodes": [_episode({"producto": "Dúo Zodiacal", "cantidad": "2"})]},
        events=[
            {"role": "user", "content": "me gusta el dúo"},
            {"role": "assistant", "sender": "human", "operator_tool": "present_variant_picker",
             "content": "Tenemos estos aromas:\n💜 Lavanda\n🍦 Vainilla"},
            {"role": "assistant", "sender": "human", "operator_tool": "present_product_gallery", "kind": "ui_component",
             "component_kind": "product_gallery", "content": "🖼️ El operador envió 1 fotos del producto"},
        ],
    )
    # el ledger que deja `POST .../tools/{tool}` con cada acción enviada
    store = FilesystemMetadataStore(h.vault)
    _record_action(store, _LAURA, client_action_id="act-aromas", tool="present_variant_picker", sent=True,
                   now_ms=NOW - 4 * _MIN)
    _record_action(store, _LAURA, client_action_id="act-fotos", tool="present_product_gallery", sent=True,
                   now_ms=NOW - 3 * _MIN)

    assert h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()["suggestions"] == []

    # el cliente escribe otra vez: los aromas vuelven (las fotos fueron lo último enviado)
    store.update(_LAURA, lambda md: {**md, "last_inbound_at_ms": NOW - _MIN})
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert [s["id"] for s in body["suggestions"]] == ["present_variant_picker"]


def test_after_tapping_send_aromas_the_colors_of_the_same_product_are_still_offered(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E con las rutas reales de la app: la burbuja «Enviar aromas» se toca
    (`POST .../tools/{tool}` con sus args, flush real) y las burbujas siguen
    ofreciendo «Enviar colores» del mismo producto, no «Enviar aromas»."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import flush_pending_ui_intents
    from src.plugins.chats.api import operator_tools

    monkeypatch.setattr(wa_client, "send_text", AsyncMock(
        return_value=SimpleNamespace(ok=True, wa_message_id="wamid.aromas", error=None)
    ))
    vault = _isolate_vault_dir  # el mismo vault que usan las tools y el flush
    mobile_deps = MobileDeps(vault_dir=vault, catalog=_Catalog(), order_facts=InMemoryOrderFacts(),
                             now_ms=lambda: NOW, payment_instructions_text=lambda: "datos de pago")
    tools_deps = operator_tools.OperatorToolsDeps(vault_dir=vault, catalog=_Catalog(), promotions=None, quotas=None,
                                                  sales=None, flush=flush_pending_ui_intents, now_ms=lambda: NOW)
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats")
    app.include_router(operator_tools.router, prefix="/api/chats")
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: mobile_deps
    app.dependency_overrides[operator_tools.get_operator_tools_deps] = lambda: tools_deps
    client = TestClient(app)
    session = "wa_100200300"  # formato wa_<dígitos> de session-actions; no es un teléfono
    (vault / session).mkdir()
    (vault / session / "metadata.json").write_text(json.dumps({
        **_open_window(), "phone_number_id": "pnid-1", "active_route": "humano", "tag": "HUMANO",
        "episodes": [_episode({"producto": "Dúo Zodiacal", "cantidad": "2"})],
    }), encoding="utf-8")

    aromas = client.get(f"/api/chats/mobile/suggestions/{session}").json()["suggestions"][0]
    assert aromas["label"] == "Enviar aromas"
    tapped = client.post(f"/api/chats/session-actions/{session}/tools/{aromas['action']['name']}",
                         json={"client_action_id": "act-aromas", "args": aromas["action"]["args"]})
    assert tapped.status_code == 200, tapped.text

    after = client.get(f"/api/chats/mobile/suggestions/{session}").json()["suggestions"]
    assert [s["label"] for s in after] == ["Enviar colores", "Más fotos"]


_FULL_SLOTS = {
    "producto": "Luz Serena", "aroma": "Lavanda", "color": "Blanco", "cantidad": "2",
    "ciudad": "Bogotá", "direccion": "Cl 1 # 2-3", "telefono": "3000000000",
    "nombre_recibe": "Ana", "metodo_pago": "Nequi",
}


def test_closing_stage_suggests_the_summary_only_with_a_recognizable_payment_method(h: _Harness) -> None:
    h.seed(_LAURA, {**_open_window(), "active_route": "humano", "episodes": [_episode(dict(_FULL_SLOTS))]})
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert body["stage"] == "etapa_cierre"
    assert [s["id"] for s in body["suggestions"]] == ["present_order_confirmation", "send_payment_methods"]

    h.seed(_LAURA, {**_open_window(), "active_route": "humano",
                    "episodes": [_episode({**_FULL_SLOTS, "metodo_pago": "lo que sea"})]})
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert [s["id"] for s in body["suggestions"]] == ["send_payment_methods"]


def test_confirmed_purchase_in_the_draft_unlocks_the_shipping_form(h: _Harness) -> None:
    slots = {k: _FULL_SLOTS[k] for k in ("producto", "aroma", "color", "cantidad")}
    ep = _episode(slots)
    ep["order_draft"]["confirmed_at_ms"] = NOW - _MIN
    h.seed(_LAURA, {**_open_window(), "active_route": "humano", "episodes": [ep]})

    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()

    assert body["stage"] == "etapa_datos_envio"
    assert [s["id"] for s in body["suggestions"]][0] == "request_shipping_details"

    # el último mensaje del cliente aplaza ("voy en camino"): la misma guarda que el bot
    deferred = {**_open_window(), "active_route": "humano", "episodes": [ep],
                "last_inbound_message_id": "wamid.9",
                "last_inbound_signal": {"kind": "deferral", "message_id": "wamid.9", "text": "voy en camino"}}
    h.seed(_LAURA, deferred)
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert "request_shipping_details" not in [s["id"] for s in body["suggestions"]]


def _facts(order_id: str, *, pay: str, stage: str = "preparing", due_iso: str | None = None) -> OrderFacts:
    return OrderFacts(order_id=order_id, display_id="#32", total_cop=179_800, currency_code="COP",
                      pay_status=pay, stage=stage, customer="Laura Gómez", is_draft=False,
                      created_at_ms=NOW - 3 * 60 * _MIN)


def test_after_the_order_payment_methods_follow_the_real_payment_state(h: _Harness) -> None:
    meta = {**_open_window(), "active_route": "humano",
            "registered_order": {"success": True, "order_id": "order_1", "payment_method": "transfer"},
            "episodes": [_episode(dict(_FULL_SLOTS), order_id="order_1")]}
    h.seed(_LAURA, meta)

    h.deps.order_facts = InMemoryOrderFacts([_facts("order_1", pay="pending")])
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert body["stage"] == "etapa_postcierre"
    assert [s["id"] for s in body["suggestions"]] == ["send_payment_methods"]

    h.deps.order_facts = InMemoryOrderFacts([_facts("order_1", pay="paid")])
    assert h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()["suggestions"] == []

    # contra entrega: se paga al recibir — nada que cobrar por chat
    h.seed(_LAURA, {**meta, "registered_order": {**meta["registered_order"], "payment_method": "cash_on_delivery"}})
    h.deps.order_facts = InMemoryOrderFacts([_facts("order_1", pay="pending")])
    assert h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()["suggestions"] == []


def test_after_the_order_closed_the_episode_the_chat_is_post_sale_not_discovery(h: _Harness) -> None:
    """El episodio se cierra en el MISMO turno en que se registra el pedido, y
    con un humano al mando el siguiente mensaje del cliente no abre otro: sin
    episodio activo manda el último, que registró el pedido. A quien ya compró
    no se le ofrece «Enviar productos» ni «Tarifas de envío»."""
    closed = _episode(dict(_FULL_SLOTS), order_id="order_1", closed_at_ms=NOW - 30 * _MIN,
                      closing_tag="CONFIRMADO_PAGO_PENDIENTE")
    h.seed(_LAURA, {**_open_window(), "active_route": "humano", "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
                    "registered_order": {"success": True, "order_id": "order_1", "payment_method": "transfer"},
                    "episodes": [closed]})

    h.deps.order_facts = InMemoryOrderFacts([_facts("order_1", pay="pending")])
    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert body["stage"] == "etapa_postcierre"
    assert [s["id"] for s in body["suggestions"]] == ["send_payment_methods"]

    h.deps.order_facts = InMemoryOrderFacts([_facts("order_1", pay="paid")])
    assert h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()["suggestions"] == []


def test_a_last_episode_closed_without_an_order_is_discovery_even_with_an_older_order(h: _Harness) -> None:
    bought = _episode(dict(_FULL_SLOTS), order_id="order_1", closed_at_ms=NOW - 40 * 24 * 60 * _MIN)
    declined = {**_episode(), "episode_id": "ep_2", "closed_at_ms": NOW - 10 * _MIN, "closing_tag": "RECHAZO"}
    h.seed(_LAURA, {**_open_window(), "active_route": "humano",
                    "registered_order": {"success": True, "order_id": "order_1", "payment_method": "transfer"},
                    "episodes": [bought, declined]})

    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()

    assert body["stage"] == "etapa_descubrimiento"
    assert [s["id"] for s in body["suggestions"]][0] == "present_products"


def test_real_composition_uses_the_vault_a_clock_and_the_order_payment_text(monkeypatch) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
        _render_payment_instructions_text,
    )
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3000000000")
    mobile.get_mobile_deps.cache_clear()
    try:
        deps = mobile.get_mobile_deps()
        assert deps.vault_dir == WORKSPACE_VAULT_DIR
        assert abs(deps.now_ms() - NOW) > 0 and deps.now_ms() > NOW
        # el mismo texto que `register_order` manda tras un pedido por transferencia
        assert deps.payment_instructions_text() == _render_payment_instructions_text({"method": "transfer"})
        assert "3000000000" in (deps.payment_instructions_text() or "")
    finally:
        mobile.get_mobile_deps.cache_clear()


def _real_app_routes() -> dict[str, Any]:
    import sys

    sys.modules.pop("src.main", None)
    import src.main as main

    return {r.path: r for r in main.app.routes if hasattr(r, "path")}


def test_the_chats_manifest_mounts_the_mobile_routes_behind_auth() -> None:
    from src.platform.auth import require_auth

    routes = _real_app_routes()
    route = routes.get("/api/chats/mobile/suggestions/{session_id}")
    assert route is not None, "falta registrar src.plugins.chats.api.mobile en plugin.yaml"
    assert any(dep.call is require_auth for dep in route.dependant.dependencies)


# ── GET /mobile/fires ────────────────────────────────────────────────────────


def _iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _bogota_midnight_ms(day: str) -> int:
    from datetime import datetime, timedelta, timezone

    return int(datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(hours=-5))).timestamp() * 1000)


def test_fires_join_waiting_chats_with_late_and_unverified_orders(h: _Harness) -> None:
    # Sofía pidió un humano y lleva 12 min escribiendo sin respuesta
    h.seed("wa_test_sofia", {
        "active_route": "humano", "escalation_reason": "EXPLICIT_REQUEST", "profile": {"name": "Sofía Pérez"},
        "last_inbound_at_ms": NOW - 5 * _MIN,
    }, events=[
        {"role": "user", "content": "hola", "timestamp": _iso(NOW - 60 * _MIN)},
        {"role": "assistant", "sender": "human", "content": "¡Hola! Ya te ayudo", "timestamp": _iso(NOW - 59 * _MIN)},
        *({"role": "user", "content": "¿hola?", "timestamp": _iso(NOW - m * _MIN)} for m in (12, 10, 8, 5)),
    ])
    # Ana: el bot tiene el chat, pero su pedido debía llegar hace 4 días (dato de OrderFacts)
    h.seed("wa_test_ana", {
        "active_route": "ventas",
        "registered_order": {"success": True, "order_id": "order_32", "payment_method": "transfer"},
    })
    # Carla: humano verificando su pago; ella no ha escrito
    h.seed("wa_test_carla", {
        "active_route": "humano", "escalation_reason": "PAYMENT_VERIFICATION_PENDING", "profile": {"name": "Carla"},
        "registered_order": {"success": True, "order_id": "order_40", "payment_method": "transfer"},
    }, events=[{"role": "assistant", "sender": "human", "content": "Te confirmo en un rato", "timestamp": _iso(NOW - _MIN)}])
    # un chat tranquilo con el bot: nada
    h.seed("wa_test_bot", {"active_route": "ventas"})
    h.deps.order_facts = InMemoryOrderFacts([
        OrderFacts(order_id="order_32", display_id="#32", total_cop=179_800, currency_code="COP", pay_status="paid",
                   stage="preparing", customer="Ana María", is_draft=False, created_at_ms=NOW - 6 * 24 * 60 * _MIN,
                   due_iso="2026-09-17"),
        OrderFacts(order_id="order_40", display_id="#40", total_cop=95_000, currency_code="COP", pay_status="pending",
                   stage="new", customer="Cliente WhatsApp", is_draft=True, created_at_ms=NOW - 3 * 60 * _MIN),
    ])

    r = h.client.get("/api/chats/mobile/fires")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decided_by"] == "rules"
    assert [f["fire_id"] for f in body["fires"]] == ["order:order_32", "chat:wa_test_sofia", "order:order_40"]
    ana, sofia, carla = body["fires"]
    assert ana == {
        "fire_id": "order:order_32",
        "subject": {"kind": "order", "session_id": "wa_test_ana", "order_id": "order_32"},
        "severity": "grave", "kind": "delayed", "getting_worse": False,
        "title": "El pedido #32 de Ana va retrasado", "subtitle": "4 días de retraso",
        "primary_action": {"name": "open_order", "args": {"order_id": "order_32", "session_id": "wa_test_ana"}},
        "updated_ms": _bogota_midnight_ms("2026-09-18"),
    }
    assert (sofia["severity"], sofia["kind"], sofia["title"]) == ("grave", "wants_human", "Sofía pide un humano")
    assert sofia["subtitle"] == "12 min sin respuesta · 4 mensajes" and sofia["updated_ms"] == NOW - 5 * _MIN
    assert (carla["severity"], carla["kind"], carla["title"]) == ("hoy", "payment_proof", "Verificar el pago de Carla")
    assert carla["subtitle"] == "Pedido #40 · $95.000" and carla["updated_ms"] == NOW - 3 * 60 * _MIN


def test_fires_survive_medusa_down_with_the_inbox_legacy_rule(h: _Harness) -> None:
    h.seed("wa_test_carla", {
        "active_route": "humano", "escalation_reason": "PAYMENT_VERIFICATION_PENDING", "tag": "HUMANO",
        "registered_order": {"success": True, "order_id": "order_40", "payment_method": "transfer",
                             "registered_at_ms": NOW - 30 * _MIN},
    })
    h.deps.order_facts = InMemoryOrderFacts(available=False)

    r = h.client.get("/api/chats/mobile/fires")

    assert r.status_code == 200
    [carla] = r.json()["fires"]
    assert (carla["kind"], carla["subtitle"]) == ("payment_proof", "Pedido sin número")
    assert carla["updated_ms"] == NOW - 30 * _MIN


# ── GET /mobile/hot ──────────────────────────────────────────────────────────


def test_hot_lists_bot_sales_closing_now_with_catalog_names_and_cart_value(h: _Harness) -> None:
    closing = {**_FULL_SLOTS, "producto": "duo zodiacal", "aroma": "Lavanda", "color": "Azul"}
    h.seed("wa_test_laura", {"active_route": "ventas", "profile": {"name": "Laura"},
                              "last_inbound_at_ms": NOW - 4 * _MIN, "episodes": [_episode(closing)]})
    # la tiene un humano / todavía eligiendo / hace una hora: no
    h.seed("wa_test_human", {"active_route": "humano", "last_inbound_at_ms": NOW - _MIN,
                              "episodes": [_episode(dict(_FULL_SLOTS))]})
    h.seed("wa_test_early", {"active_route": "ventas", "last_inbound_at_ms": NOW - _MIN,
                              "episodes": [_episode({"producto": "Luz Serena"})]})
    h.seed("wa_test_cold", {"active_route": "ventas", "last_inbound_at_ms": NOW - 60 * _MIN,
                             "episodes": [_episode(dict(_FULL_SLOTS))]})

    r = h.client.get("/api/chats/mobile/hot")

    assert r.status_code == 200, r.text
    assert r.json() == {"hot": [{
        "session_id": "wa_test_laura",
        "name": "Laura",
        "stage": "etapa_cierre",
        "product": "Dúo Zodiacal Lavanda Azul × 2",
        "cart_value_cop": 179_800,
        "risk": False,
        "updated_ms": NOW - 4 * _MIN,
    }]}


# ── POST/DELETE /mobile/devices (registro de tokens FCM) ─────────────────────


@pytest.fixture
def devices(h: _Harness) -> TestClient:
    """App con un `require_auth` de prueba: deja el actor VERIFICADO donde lo
    deja el real (`request.state`), tomado de un header de test."""
    from src.platform.auth import ACTOR_STATE_KEY

    def _auth(request: Request) -> None:
        setattr(request.state, ACTOR_STATE_KEY, request.headers.get("x-test-actor", "ana@equipo.test"))

    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats", dependencies=[Depends(_auth)])
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: h.deps
    return TestClient(app)


def _registry(h: _Harness) -> dict[str, Any]:
    return json.loads((h.vault / "_mobile" / "devices.json").read_text(encoding="utf-8"))


def test_registering_a_device_is_idempotent_and_stored_per_operator(h: _Harness, devices: TestClient) -> None:
    body = {"token": "fcm:tok-1", "platform": "android", "app_version": "1.0.0"}

    assert devices.post("/api/chats/mobile/devices", json=body).status_code == 204
    assert devices.post("/api/chats/mobile/devices", json={**body, "app_version": "1.0.1"}).status_code == 204

    reg = _registry(h)
    [device] = reg["operators"]["ana@equipo.test"]
    # solo el token y datos de la app: nada del operador ni del teléfono
    assert set(device) == {"token", "platform", "app_version", "registered_at_ms", "updated_at_ms"}
    assert (device["token"], device["platform"], device["app_version"]) == ("fcm:tok-1", "android", "1.0.1")
    assert device["registered_at_ms"] == device["updated_at_ms"] == NOW


def test_a_token_belongs_to_the_last_operator_that_registered_it(h: _Harness, devices: TestClient) -> None:
    body = {"token": "fcm:shared", "platform": "android", "app_version": "1.0.0"}
    devices.post("/api/chats/mobile/devices", json=body)
    devices.post("/api/chats/mobile/devices", json=body, headers={"x-test-actor": "beto@equipo.test"})

    reg = _registry(h)["operators"]
    assert [d["token"] for d in reg["beto@equipo.test"]] == ["fcm:shared"]
    assert reg.get("ana@equipo.test", []) == []


def test_deleting_a_device_removes_only_that_token_and_is_idempotent(h: _Harness, devices: TestClient) -> None:
    for tok in ("fcm:a", "fcm:b"):
        devices.post("/api/chats/mobile/devices", json={"token": tok, "platform": "android", "app_version": "1"})

    assert devices.delete("/api/chats/mobile/devices/fcm:a").status_code == 204
    assert devices.delete("/api/chats/mobile/devices/fcm:a").status_code == 204
    assert [d["token"] for d in _registry(h)["operators"]["ana@equipo.test"]] == ["fcm:b"]


def test_device_body_is_validated(devices: TestClient) -> None:
    ok = {"token": "fcm:x", "platform": "android", "app_version": "1.0.0"}
    for bad in ({**ok, "platform": "ios"}, {**ok, "token": ""}, {**ok, "token": "x" * 4097},
                {"token": "fcm:x", "platform": "android"}, {**ok, "extra": 1}):
        assert devices.post("/api/chats/mobile/devices", json=bad).status_code == 422, bad


# ── GET /catalog (selector de productos de la app) ───────────────────────────


def test_catalog_lists_visible_products_with_their_closed_lists(h: _Harness) -> None:
    hidden = CatalogProductDTO(id="p_x", handle="borrador", title="Borrador", status="draft")
    h.deps.catalog = _Catalog(products=(_DUO, _LUZ, hidden))

    r = h.client.get("/api/chats/catalog")

    assert r.status_code == 200, r.text
    assert r.json() == {"products": [
        {"handle": "duo-zodiacal", "title": "Dúo Zodiacal", "price_cop": 89_900,
         "thumbnail_url": "https://cdn.test/duo-leo.jpg", "aromas": ["Lavanda", "Vainilla"],
         "colors": ["Azul", "Rosa"], "designs": ["Duo leo", "Duo aries"]},
        {"handle": "luz-serena", "title": "Luz Serena", "price_cop": 29_000,
         "thumbnail_url": "https://cdn.test/luz.jpg", "aromas": ["Lavanda", "Canela"],
         "colors": [], "designs": []},
    ]}


def test_catalog_is_503_without_a_snapshot(h: _Harness) -> None:
    h.deps.catalog = _Catalog(down=True)
    r = h.client.get("/api/chats/catalog")
    assert (r.status_code, r.json()) == (503, {"error": "catalog_unavailable"})

    h.deps.catalog = None
    r = h.client.get("/api/chats/catalog")
    assert (r.status_code, r.json()) == (503, {"error": "catalog_unavailable"})


# ── El motor de decisiones (paquete `operador`, panel «Motor de decisiones») ───────────────────────────────


def _jev_on(h: _Harness, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Any:
    """Las dos decisiones de la app en «on» desde el panel (motor oficial) y un Jev falso."""
    from src.plugins.chats.agent.sales.decisions import bots
    from src.plugins.chats.api.mobile_decisions import OperatorDecisions
    from src.sdk import connectorkit
    from src.sdk.connectorkit import FakePerceptionAdapter

    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    bots.write_capability_modes(h.vault, {"burbuja": "on", "incendio": "on"})
    fake = FakePerceptionAdapter(answers)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    h.deps.decisions = OperatorDecisions(vault_dir=h.vault, now_ms=lambda: NOW)
    return h.deps.decisions


def _choice(qid: str, option: str, p: float) -> Any:
    from src.sdk.connectorkit import TypedAnswer

    return TypedAnswer(id=qid, kind="choice", choice=option, probs=((option, p),), confidence=p)


def test_with_jev_on_its_bubble_goes_first(h: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h.seed(_LAURA, {
        **_open_window(),
        "active_route": "humano",
        "episodes": [_episode({"producto": "Dúo Zodiacal", "cantidad": "2"})],
    }, events=[{"role": "user", "content": "¿me muestras más fotos del dúo?", "timestamp": _iso(NOW - 5 * _MIN)}])
    rules = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()
    assert rules["decided_by"] == "rules" and len(rules["suggestions"]) >= 2
    last = rules["suggestions"][-1]
    _jev_on(h, {"burbuja.cual": _choice("burbuja.cual", "mas_fotos", 0.9)}, monkeypatch)

    body = h.client.get(f"/api/chats/mobile/suggestions/{_LAURA}").json()

    assert last["label"] == "Más fotos"
    assert body["decided_by"] == "jev"
    assert (body["suggestions"][0]["label"], body["suggestions"][0]["prominence"]) == ("Más fotos", "primary")
    assert sorted(s["label"] for s in body["suggestions"]) == sorted(s["label"] for s in rules["suggestions"])


def test_with_jev_on_a_fire_is_classified_from_the_next_look(h: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h.seed("wa_test_sofia", {
        "active_route": "humano", "profile": {"name": "Sofía Pérez"}, "last_inbound_at_ms": NOW - 3 * _MIN,
    }, events=[
        {"role": "assistant", "sender": "human", "content": "¡Hola! Ya te ayudo", "timestamp": _iso(NOW - 10 * _MIN)},
        {"role": "user", "content": "esto es una falta de respeto, llevo días esperando", "timestamp": _iso(NOW - 3 * _MIN)},
    ])
    jev = _jev_on(h, {
        "incendio.gravedad": _choice("incendio.gravedad", "grave", 0.9),
        "incendio.tipo": _choice("incendio.tipo", "queja", 0.9),
    }, monkeypatch)

    with h.client as client:
        first = client.get("/api/chats/mobile/fires").json()
        client.portal.call(jev.drain)
        second = client.get("/api/chats/mobile/fires").json()

    assert first["decided_by"] == "rules" and first["fires"][0]["severity"] == "hoy"
    assert second["decided_by"] == "jev"
    assert (second["fires"][0]["severity"], second["fires"][0]["kind"]) == ("grave", "angry")
