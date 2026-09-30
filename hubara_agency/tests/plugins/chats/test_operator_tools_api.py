"""Acción del operador desde la app: ejecutar una tool de UI del bot COMO EL HUMANO.

`POST /api/chats/session-actions/{session_key}/tools/{tool}` corre la MISMA tool
que usa el bot (misma validación contra el catálogo), manda SOLO los intents
que produjo esta llamada (nunca otros de la cola), deja el envío en el
historial firmado por el humano y es idempotente por `client_action_id`.

Se usa el flush REAL con el cliente de WhatsApp falso: se verifica lo que le
llega al cliente y lo que queda en el vault (gotcha 1), no solo el status.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.catalog.errors import ProductNotFoundError
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.activities.flush_ui_intents import flush_pending_ui_intents
from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATES_MESSAGE
from src.plugins.chats.agent.sales.tools import ui_intents
from src.plugins.chats.api import operator_tools
from src.plugins.chats.api.operator_tools import OperatorToolsDeps

_S = "wa_100200300"  # formato wa_<dígitos> del contrato session-actions; no es un teléfono
_MIN = 60_000

_DUO = CatalogProductDTO(
    id="p_duo", handle="duo-zodiacal", title="Dúo Zodiacal", status="published",
    thumbnail="https://cdn.test/duo-leo.jpg",
    images=[CatalogImageDTO(url="https://cdn.test/duo-leo.jpg"), CatalogImageDTO(url="https://cdn.test/duo-aries.jpg", rank=1)],
    variants=[CatalogVariantDTO(id="v1", title="Unico", prices=[CatalogPriceDTO(amount="89900", currency_code="cop")])],
    tags=["Aroma: Lavanda", "Aroma: Vainilla", "Color: Azul", "Color: Rosa"],
)


class _Catalog:
    def __init__(self, products=(_DUO,)) -> None:
        self._by = {p.handle: p for p in products}

    async def get_by_handle(self, handle: str):
        if handle not in self._by:
            raise ProductNotFoundError(handle)
        return self._by[handle]

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        results = list(self._by.values())[:limit]
        return SearchResult(query=q, count=len(results), truncated=False, stale=False,
                            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=len(results)),
                            results=results)


def _now() -> int:
    return int(time.time() * 1000)


@dataclass
class _Harness:
    client: TestClient
    vault: Path
    wa: dict[str, AsyncMock]
    deps: OperatorToolsDeps

    def seed(self, metadata: dict[str, Any]) -> None:
        (self.vault / _S).mkdir(parents=True, exist_ok=True)
        (self.vault / _S / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    def meta(self) -> dict[str, Any]:
        return json.loads((self.vault / _S / "metadata.json").read_text(encoding="utf-8"))

    def history(self) -> list[dict[str, Any]]:
        path = self.vault / _S / "sessions" / f"{_S}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def run(self, tool: str, args: dict[str, Any] | None = None, cid: str = "act-1"):
        return self.client.post(
            f"/api/chats/session-actions/{_S}/tools/{tool}",
            json={"client_action_id": cid, "args": args or {}},
        )

    def sent_texts(self) -> list[str]:
        return [call.args[2] for call in self.wa["send_text"].await_args_list]


@pytest.fixture
def h(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    """Vault aislado (el MISMO que usan las tools y el flush vía
    `WORKSPACE_VAULT_DIR`) + cliente de WhatsApp falso (todo envío OK)."""
    from src.platform.whatsapp import client as wa_client

    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3000000000")
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    wa: dict[str, AsyncMock] = {}
    for fn in ("send_text", "send_image", "send_interactive_buttons", "send_interactive_list",
               "send_product_list", "send_flow"):
        wa[fn] = AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id=f"wamid.{fn}", error=None))
        monkeypatch.setattr(wa_client, fn, wa[fn])
    deps = OperatorToolsDeps(
        vault_dir=_isolate_vault_dir, catalog=_Catalog(), promotions=None, quotas=None, sales=None,
        flush=flush_pending_ui_intents, now_ms=_now,
    )
    app = FastAPI()
    app.include_router(operator_tools.router, prefix="/api/chats")
    app.dependency_overrides[operator_tools.get_operator_tools_deps] = lambda: deps
    return _Harness(TestClient(app), _isolate_vault_dir, wa, deps)


def _human(**extra: Any) -> dict[str, Any]:
    """Sesión intervenida por un humano, con la ventana de 24h abierta."""
    return {"phone_number_id": "pnid-1", "active_route": "humano", "tag": "HUMANO",
            "service_window_expires_at_ms": _now() + 60 * _MIN, **extra}


_PAYINSTR = {"id": "payinstr-ord_1", "kind": "payment_instructions", "params": {"method": "transfer"}}


# ── lo básico: la tool del bot, como el humano, y SOLO lo suyo ───────────────


def test_operator_sends_the_shipping_rates_and_nothing_else_from_the_queue(h: _Harness) -> None:
    # "Crear pedido" dejó las instrucciones de pago encoladas SIN enviar, a propósito
    queued = {**_PAYINSTR, "queued_at_ms": _now()}
    h.seed(_human(pending_ui_intents=[queued]))

    r = h.run("send_shipping_rates", cid="act-rates-1")

    assert r.status_code == 200, r.text
    assert r.json() == {"sent": True, "tool": "send_shipping_rates", "client_action_id": "act-rates-1",
                        "deduplicated": False}
    assert h.sent_texts() == [SHIPPING_RATES_MESSAGE]
    assert h.meta()["pending_ui_intents"] == [queued]
    [event] = h.history()
    assert (event["role"], event["sender"], event["operator_tool"]) == ("assistant", "human", "send_shipping_rates")
    assert event["content"] == SHIPPING_RATES_MESSAGE


def test_an_intent_the_bot_queues_meanwhile_from_another_process_is_not_sent_as_the_operators(
    h: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El candado del endpoint es por proceso: un turno del bot en el worker de Ventas (otro proceso) puede
    encolar mientras corre la tool del operador. Lo que sale y se firma como humano es SOLO lo que encoló
    esta tool, no la diferencia de la cola antes y después."""
    h.seed(_human())
    bot_intent = {"id": "ui_turno_del_bot", "kind": "payment_instructions", "params": {"method": "transfer"},
                  "queued_at_ms": _now()}
    real_append = ui_intents._append_intent

    def the_bot_queues_first(session_key: str, intent: dict[str, Any]) -> Any:
        FilesystemMetadataStore(h.vault).update(
            session_key, lambda d: {**d, "pending_ui_intents": [*(d.get("pending_ui_intents") or []), bot_intent]})
        return real_append(session_key, intent)

    monkeypatch.setattr(ui_intents, "_append_intent", the_bot_queues_first)

    r = h.run("send_shipping_rates", cid="act-race")

    assert r.status_code == 200, r.text
    assert h.sent_texts() == [SHIPPING_RATES_MESSAGE]  # nada del bot salió como del operador
    assert [i["id"] for i in h.meta()["pending_ui_intents"]] == ["ui_turno_del_bot"]  # queda para el flush del bot
    assert [e["operator_tool"] for e in h.history()] == ["send_shipping_rates"]


def test_the_same_client_action_id_again_sends_nothing_and_says_deduplicated(h: _Harness) -> None:
    h.seed(_human())
    assert h.run("send_shipping_rates", cid="act-dup").status_code == 200

    again = h.run("send_shipping_rates", cid="act-dup")

    assert again.status_code == 200
    assert again.json() == {"sent": True, "tool": "send_shipping_rates", "client_action_id": "act-dup",
                            "deduplicated": True}
    assert h.sent_texts() == [SHIPPING_RATES_MESSAGE]  # una sola vez
    assert len(h.history()) == 1
    # el mismo id con OTRA tool: nada sale y la respuesta dice lo que se hizo con ese id
    reused = h.run("send_payment_methods", cid="act-dup").json()
    assert (reused["deduplicated"], reused["tool"]) == (True, "send_shipping_rates")
    assert len(h.sent_texts()) == 1
    # otra acción (otro id) sí sale
    assert h.run("send_shipping_rates", cid="act-other").json()["deduplicated"] is False
    assert len(h.sent_texts()) == 2


def test_the_ledger_records_the_args_each_action_was_sent_with(h: _Harness) -> None:
    """Las burbujas comparan la jugada por acción + args («Enviar aromas» no es
    «Enviar colores»): el ledger guarda con qué args salió cada acción."""
    h.seed(_human())
    aromas = {"product": "duo-zodiacal", "attribute": "aroma"}

    assert h.run("present_variant_picker", aromas, cid="act-aromas").status_code == 200

    [entry] = h.meta()["operator_tool_actions"]
    assert (entry["id"], entry["tool"], entry.get("args")) == ("act-aromas", "present_variant_picker", aromas)


def test_the_idempotency_ledger_is_bounded(h: _Harness) -> None:
    old = [{"id": f"old-{i}", "tool": "send_shipping_rates", "sent": True, "at_ms": i} for i in range(200)]
    h.seed(_human(operator_tool_actions=old))

    assert h.run("send_shipping_rates", cid="act-new").status_code == 200

    ledger = h.meta()["operator_tool_actions"]
    assert len(ledger) == 200
    assert ledger[-1]["id"] == "act-new" and ledger[-1]["tool"] == "send_shipping_rates"
    assert ledger[0]["id"] == "old-1"


# ── guardas ──────────────────────────────────────────────────────────────────


def test_unknown_tools_are_404_and_nothing_runs(h: _Harness) -> None:
    h.seed(_human())
    for tool in ("register_order", "escalate_to_human", "send_cta_url", "nope"):
        r = h.run(tool)
        assert (r.status_code, r.json()) == (404, {"error": "unknown_tool"}), tool
    assert h.sent_texts() == [] and "pending_ui_intents" not in h.meta()


def test_the_operator_must_hold_the_chat_and_the_window_must_be_open(h: _Harness) -> None:
    h.seed({**_human(), "active_route": "ventas", "tag": "INTERESADO"})
    r = h.run("send_shipping_rates", cid="act-bot")
    assert (r.status_code, r.json()) == (409, {"error": "not_in_control"})

    h.seed(_human(service_window_expires_at_ms=_now() - 1))
    r = h.run("send_shipping_rates", cid="act-closed")
    assert (r.status_code, r.json()) == (409, {"error": "window_closed"})

    assert h.sent_texts() == [] and not h.meta().get("pending_ui_intents")


def test_a_retry_of_something_already_sent_is_still_a_dedup_after_the_window_closes(h: _Harness) -> None:
    h.seed(_human())
    assert h.run("send_shipping_rates", cid="act-late").status_code == 200
    h.seed({**h.meta(), "service_window_expires_at_ms": _now() - 1})

    r = h.run("send_shipping_rates", cid="act-late")

    assert r.status_code == 200 and r.json()["deduplicated"] is True
    assert len(h.sent_texts()) == 1


def test_a_session_that_does_not_exist_is_404(h: _Harness) -> None:
    r = h.run("send_shipping_rates")
    assert (r.status_code, r.json()) == (404, {"error": "session_not_found"})
    assert not (h.vault / _S).exists()  # no se inventa la sesión


def test_a_malformed_session_key_is_the_same_404_as_an_unknown_session(h: _Harness) -> None:
    """La app parsea `{"error": ...}`: una llave que no puede nombrar una sesión
    (no es `wa_<dígitos>`, `..`, demasiado larga) es una sesión que no existe."""
    h.seed(_human())
    for key in ("wa_test_laura", "%2E%2E", "wa_1", "wa_" + "1" * 70):
        r = h.client.post(
            f"/api/chats/session-actions/{key}/tools/send_shipping_rates",
            json={"client_action_id": "act-bad-key", "args": {}},
        )
        assert (r.status_code, r.json()) == (404, {"error": "session_not_found"}), key
    assert h.sent_texts() == []


# ── args: la misma validación que el bot ─────────────────────────────────────


def test_args_outside_the_tool_schema_are_422(h: _Harness) -> None:
    h.seed(_human())

    unknown = h.run("send_shipping_rates", {"texto": "gratis!"}, cid="act-x")
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "invalid_args" and unknown.json()["problems"]

    missing = h.run("present_product_detail", {}, cid="act-y")
    assert missing.status_code == 422
    assert any("handle" in p for p in missing.json()["problems"])

    wrong_type = h.run("present_product_detail", {"handle": 7}, cid="act-z")
    assert wrong_type.status_code == 422
    assert h.sent_texts() == [] and not h.meta().get("pending_ui_intents")


def test_what_the_bot_tool_rejects_is_422_tool_rejected_and_nothing_is_sent(h: _Harness) -> None:
    h.seed(_human())

    r = h.run("present_product_detail", {"handle": "no-existe"}, cid="act-bad")

    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "tool_rejected" and body["reason"] == "handle_not_found" and body["message"]
    assert not h.wa["send_image"].await_count and not h.meta().get("pending_ui_intents")
    # el id no quedó gastado: corregido, el operador puede reintentar con él
    ok = h.run("present_product_detail", {"handle": "duo-zodiacal"}, cid="act-bad")
    assert ok.status_code == 200 and ok.json()["deduplicated"] is False
    assert h.wa["send_image"].await_count == 1


# ── args del operador: lo que emiten las burbujas se ejecuta tal cual ────────


def test_the_variant_picker_takes_the_suggestion_args_and_offers_the_real_catalog_list(h: _Harness) -> None:
    h.seed(_human())

    r = h.run("present_variant_picker", {"product": "duo-zodiacal", "attribute": "aroma"}, cid="act-aromas")

    assert r.status_code == 200, r.text
    [text] = h.sent_texts()
    assert text.startswith("Tenemos estos aromas:") and "Lavanda" in text and "Vainilla" in text
    [event] = h.history()
    assert (event["sender"], event["operator_tool"], event["content"]) == ("human", "present_variant_picker", text)

    assert h.run("present_variant_picker", {"product": "duo-zodiacal", "attribute": "color"}, cid="act-colores").status_code == 200
    assert "Azul" in h.sent_texts()[1] and "Rosa" in h.sent_texts()[1]


def test_the_variant_picker_rejects_unknown_attributes_or_products(h: _Harness) -> None:
    h.seed(_human())

    bad_attr = h.run("present_variant_picker", {"product": "duo-zodiacal", "attribute": "signo"}, cid="a1")
    assert bad_attr.status_code == 422 and bad_attr.json()["error"] == "invalid_args"
    no_product = h.run("present_variant_picker", {"product": "no-existe", "attribute": "aroma"}, cid="a2")
    assert no_product.status_code == 422
    assert h.sent_texts() == []


# ── send_payment_methods: el texto de pago de register_order, sin registrar ──


def test_payment_methods_sends_the_order_payment_text_without_registering_anything(h: _Harness) -> None:
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _render_payment_instructions_text

    queued = {**_PAYINSTR, "queued_at_ms": _now()}  # el de "Crear pedido", no se toca
    h.seed(_human(pending_ui_intents=[queued]))

    r = h.run("send_payment_methods", cid="act-pay")

    assert r.status_code == 200, r.text
    assert r.json() == {"sent": True, "tool": "send_payment_methods", "client_action_id": "act-pay",
                        "deduplicated": False}
    [text] = h.sent_texts()
    assert text == _render_payment_instructions_text({"method": "transfer"}) and "3000000000" in text
    meta = h.meta()
    assert "registered_order" not in meta and meta["pending_ui_intents"] == [queued]
    [event] = h.history()
    assert (event["sender"], event["operator_tool"], event["content"]) == ("human", "send_payment_methods", text)


def test_payment_methods_without_payment_data_or_with_args_is_422(h: _Harness, monkeypatch) -> None:
    h.seed(_human())
    assert h.run("send_payment_methods", {"monto": 1}, cid="p1").json()["error"] == "invalid_args"

    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "")  # sin llave y sin banco: no hay datos que mandar
    r = h.run("send_payment_methods", cid="p2")
    assert r.status_code == 422
    assert (r.json()["error"], r.json()["reason"]) == ("tool_rejected", "payment_methods_unavailable")
    assert h.sent_texts() == []


def _draft(slots: dict[str, Any], *, confirmed: bool = True) -> list[dict[str, Any]]:
    draft: dict[str, Any] = {"slots": slots}
    if confirmed:
        draft["confirmed_at_ms"] = _now() - _MIN
    return [{"episode_id": "ep_1", "started_at_ms": _now() - 60 * _MIN, "closed_at_ms": None, "order_draft": draft}]


_CHOSEN = {"producto": "Dúo Zodiacal", "aroma": "Lavanda", "color": "Azul", "cantidad": "2"}


def test_asking_shipping_details_builds_the_items_from_the_draft(h: _Harness) -> None:
    h.seed(_human(episodes=_draft(_CHOSEN)))

    r = h.run("request_shipping_details", cid="act-ship")

    assert r.status_code == 200, r.text
    [text] = h.sent_texts()
    # 2 × $89.900 desde el catálogo ≥ $45.000 → se ofrece contra entrega
    assert "*Ciudad*" in text and "Contra entrega" in text
    [event] = h.history()
    assert event["component_kind"] == "shipping_flow" and event["operator_tool"] == "request_shipping_details"
    assert event["content"] == "📋 El operador pidió los datos de envío (formulario)"


def test_asking_shipping_details_keeps_the_bot_guard_and_needs_a_resolvable_draft(h: _Harness) -> None:
    h.seed(_human(episodes=_draft(_CHOSEN, confirmed=False)))
    r = h.run("request_shipping_details", cid="s1")
    assert (r.status_code, r.json()["reason"]) == (422, "purchase_not_confirmed")

    h.seed(_human(episodes=_draft({**_CHOSEN, "producto": "Vela que no existe"})))
    r = h.run("request_shipping_details", cid="s2")
    assert r.status_code == 422 and r.json()["error"] == "invalid_args"
    assert h.sent_texts() == []


_CLOSING = {**_CHOSEN, "ciudad": "Bogotá", "barrio": "Chapinero", "direccion": "Cl 1 # 2-3",
            "telefono": "3000000000", "nombre_recibe": "Ana", "metodo_pago": "Nequi"}


def test_the_order_summary_is_built_from_the_draft_with_catalog_prices(h: _Harness) -> None:
    h.seed(_human(episodes=_draft(_CLOSING)))

    r = h.run("present_order_confirmation", cid="act-summary")

    assert r.status_code == 200, r.text
    card = h.wa["send_interactive_buttons"].await_args.args[2]
    assert "2× Dúo Zodiacal — $89.900" in card.body
    assert "Subtotal productos: $179.800 COP" in card.body
    assert "Envío (tarifa mínima): $7.900 COP" in card.body and "Total: $187.700 COP" in card.body
    assert "Cl 1 # 2-3, Chapinero, Bogotá" in card.body and "Pago anticipado (Nequi)" in card.body
    [event] = h.history()
    assert event["operator_tool"] == "present_order_confirmation" and event["component_kind"] == "order_confirmation"


def test_the_order_summary_needs_address_and_a_known_payment_method(h: _Harness) -> None:
    h.seed(_human(episodes=_draft({**_CLOSING, "metodo_pago": "lo que sea", "direccion": ""})))

    r = h.run("present_order_confirmation", cid="act-summary-2")

    assert r.status_code == 422 and r.json()["error"] == "invalid_args"
    problems = " ".join(r.json()["problems"])
    assert "dirección" in problems and "medio de pago" in problems
    assert not h.wa["send_interactive_buttons"].await_count


def test_products_with_no_args_sends_the_visible_catalog(h: _Harness) -> None:
    h.seed(_human())

    r = h.run("present_products", cid="act-catalogo")

    assert r.status_code == 200, r.text
    listing = h.wa["send_interactive_list"].await_args.args[2]
    assert listing.body == "Estos son nuestros productos:"
    assert [row.id for section in listing.sections for row in section.rows] == ["duo-zodiacal"]
    [event] = h.history()
    assert event["operator_tool"] == "present_products" and event["content"].startswith("🛍️ El operador envió")


# ── apply_coupon: aplica en el pedido, no manda nada ─────────────────────────


def _promo(code: str):
    from src.sdk.connectorkit import PromotionDTO

    return PromotionDTO(
        id="promo_1", code=code, discount_type="percentage", value=10, currency_code="cop",
        target_type="items", allocation="each", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description=None,
    )


def test_apply_coupon_stores_it_on_the_order_without_sending_anything(h: _Harness) -> None:
    from src.sdk.connectorkit import FakePromotionsPort

    h.deps.promotions = FakePromotionsPort([_promo("AMOR26")])
    h.seed(_human(episodes=_draft(_CHOSEN)))

    r = h.run("apply_coupon", {"code": "amor26"}, cid="act-cupon")

    assert r.status_code == 200, r.text
    assert r.json() == {"sent": False, "tool": "apply_coupon", "client_action_id": "act-cupon", "deduplicated": False}
    assert h.meta()["episodes"][-1]["applied_coupon"]["code"] == "AMOR26"
    assert h.sent_texts() == [] and h.history() == []
    again = h.run("apply_coupon", {"code": "amor26"}, cid="act-cupon").json()
    assert (again["sent"], again["deduplicated"]) == (False, True)

    unknown = h.run("apply_coupon", {"code": "NOEXISTE"}, cid="act-cupon-2")
    assert (unknown.status_code, unknown.json()["error"], unknown.json()["reason"]) == (422, "tool_rejected", "not_found")


def test_missing_catalog_or_promotions_is_503_not_a_crash(h: _Harness) -> None:
    h.seed(_human())
    h.deps.catalog = None
    r = h.run("present_product_detail", {"handle": "duo-zodiacal"}, cid="c-1")
    assert (r.status_code, r.json()) == (503, {"error": "catalog_unavailable"})

    r = h.run("apply_coupon", {"code": "AMOR26"}, cid="c-2")
    assert (r.status_code, r.json()) == (503, {"error": "promotions_unavailable"})
    # lo que no depende de nada sigue andando
    assert h.run("send_shipping_rates", cid="c-3").status_code == 200


# ── args nativos + fallos de envío + concurrencia ────────────────────────────


def test_native_args_pass_through_with_the_bot_guards(h: _Harness) -> None:
    h.seed(_human())
    buttons = {"body": "¿Te lo envío hoy?", "buttons": [{"id": "ship.today", "title": "Sí, hoy"},
                                                       {"id": "ship.later", "title": "Más tarde"}]}
    assert h.run("send_quick_replies", buttons, cid="q1").status_code == 200
    assert h.wa["send_interactive_buttons"].await_args.args[2].body == "¿Te lo envío hoy?"

    # la guarda anti-selector del bot: botones que son productos → no
    selector = {"body": "¿Cuál?", "buttons": [{"id": "product.duo", "title": "Dúo Zodiacal"}]}
    r = h.run("send_quick_replies", selector, cid="q2")
    assert (r.status_code, r.json()["reason"]) == (422, "catalog_choice_not_allowed")

    assert h.run("present_product_gallery", {"handle": "duo-zodiacal"}, cid="g1").status_code == 200
    assert h.wa["send_image"].await_args.args[2].link == "https://cdn.test/duo-aries.jpg"


def test_when_whatsapp_rejects_it_is_502_and_the_id_can_be_retried(h: _Harness) -> None:
    h.seed(_human())
    h.wa["send_text"].return_value = SimpleNamespace(ok=False, wa_message_id=None, error="131047")

    r = h.run("send_shipping_rates", cid="act-fail")

    assert (r.status_code, r.json()) == (502, {"error": "send_failed"})
    meta = h.meta()
    assert not meta.get("pending_ui_intents") and not meta.get("operator_tool_actions")
    h.wa["send_text"].return_value = SimpleNamespace(ok=True, wa_message_id="wamid.ok", error=None)
    assert h.run("send_shipping_rates", cid="act-fail").json()["deduplicated"] is False


@pytest.mark.asyncio
async def test_two_concurrent_taps_with_the_same_id_send_once(h: _Harness) -> None:
    """Doble tap / reintento agresivo del celular: el lock por sesión (el de
    `/order`) serializa y el ledger corta al segundo."""
    import asyncio

    from src.plugins.chats.api.operator_tools import ToolBody, run_tool

    h.seed(_human())
    body = ToolBody(client_action_id="act-race", args={})

    async def slow_send(*_args, **_kwargs):
        await asyncio.sleep(0.02)  # como el HTTP real: cede el event loop a mitad del envío
        return SimpleNamespace(ok=True, wa_message_id="wamid.slow", error=None)

    h.wa["send_text"].side_effect = slow_send

    first, second = await asyncio.gather(
        run_tool(_S, "send_shipping_rates", body, h.deps),
        run_tool(_S, "send_shipping_rates", body, h.deps),
    )

    assert sorted([first["deduplicated"], second["deduplicated"]]) == [False, True]
    assert h.sent_texts() == [SHIPPING_RATES_MESSAGE]


# ── composición real + registro en el manifest ───────────────────────────────


def test_real_composition_wires_the_vault_and_the_real_scoped_flush() -> None:
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    operator_tools.get_operator_tools_deps.cache_clear()
    try:
        deps = operator_tools.get_operator_tools_deps()
        assert deps.vault_dir == WORKSPACE_VAULT_DIR
        assert deps.flush is flush_pending_ui_intents
        assert deps.now_ms() > 1_700_000_000_000
    finally:
        operator_tools.get_operator_tools_deps.cache_clear()


def test_the_chats_manifest_mounts_the_operator_tools_route_behind_auth() -> None:
    import sys

    from src.platform.auth import require_auth

    sys.modules.pop("src.main", None)
    import src.main as main

    routes = {r.path: r for r in main.app.routes if hasattr(r, "path")}
    route = routes.get("/api/chats/session-actions/{session_key}/tools/{tool}")
    assert route is not None, "falta registrar src.plugins.chats.api.operator_tools en plugin.yaml"
    assert any(dep.call is require_auth for dep in route.dependant.dependencies)


def test_a_sent_card_schedules_the_capi_outbox_flush_like_order_does(h: _Harness, monkeypatch) -> None:
    """El flush encola la señal de embudo (ViewContent…) de lo que el cliente
    VIO; sin turno del bot, alguien tiene que despachar el outbox."""
    scheduled: list[str] = []
    monkeypatch.setattr(operator_tools, "schedule_capi_flush", scheduled.append)
    h.seed(_human())

    assert h.run("present_product_detail", {"handle": "no-existe"}, cid="k0").status_code == 422
    assert scheduled == []
    assert h.run("present_product_detail", {"handle": "duo-zodiacal"}, cid="k1").status_code == 200
    assert scheduled == [_S]


# ── contrato compuesto: lo que sugieren las burbujas se ejecuta tal cual ─────


@pytest.mark.parametrize(
    ("slots", "confirmed", "expected"),
    [
        ({}, False, ["present_products", "send_shipping_rates", "send_payment_methods"]),
        ({"producto": "Dúo Zodiacal", "cantidad": "2"}, True,
         ["present_variant_picker", "present_product_gallery", "request_shipping_details"]),
        (_CLOSING, False, ["present_order_confirmation", "send_payment_methods"]),
    ],
)
def test_every_suggested_action_runs_verbatim_on_the_tools_endpoint(h: _Harness, slots, confirmed, expected) -> None:
    from src.plugins.chats.api import mobile
    from src.plugins.chats.api.mobile import MobileDeps
    from src.sdk.connectorkit import InMemoryOrderFacts

    mobile_deps = MobileDeps(
        vault_dir=h.vault, catalog=_Catalog(), order_facts=InMemoryOrderFacts(), now_ms=_now,
        payment_instructions_text=lambda: "datos de pago",
    )
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats")
    app.include_router(operator_tools.router, prefix="/api/chats")
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: mobile_deps
    app.dependency_overrides[operator_tools.get_operator_tools_deps] = lambda: h.deps
    client = TestClient(app)
    h.seed(_human(episodes=_draft(slots, confirmed=confirmed)))

    suggested = client.get(f"/api/chats/mobile/suggestions/{_S}").json()["suggestions"]
    assert [s["id"] for s in suggested] == expected

    for i, suggestion in enumerate(suggested):
        action = suggestion["action"]
        r = client.post(
            f"/api/chats/session-actions/{_S}/tools/{action['name']}",
            json={"client_action_id": f"act-{i}", "args": action["args"]},
        )
        assert r.status_code == 200, (action, r.text)
        assert r.json()["sent"] is True
