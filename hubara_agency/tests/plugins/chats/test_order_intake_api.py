"""Pedido rápido desde el chat intervenido — extracción asistida (`order-intake@v1`).

Caso real (2026-09-17): el cliente NO completó el Flow de envío,
el bot escaló y el humano SACÓ los datos a mano en el chat… y ahí se quedó: no
hay forma de crear el pedido desde la conversación. Este contrato es el paso 1
del botón "Crear pedido": leer la conversación (con DeepSeek) y devolver el
formulario PRE-LLENADO. NO registra nada — eso sigue siendo
`session-actions@v1 /order`, que el humano dispara desde el formulario.

Gotcha 1 (verificar comportamiento, no schema): los tests miran el metadata.json
real del vault para probar que `suggest` NO muta nada, y que los precios salen
del CATÁLOGO aunque el LLM invente otros.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.platform.catalog.errors import ProductNotFoundError
from src.plugins.chats.api import order_intake
from src.plugins.chats.api.order_intake import OrderIntakeDeps

_S = "wa_573001234567"

_LUZ = CatalogProductDTO(
    id="p1",
    handle="luz-serena",
    title="Luz Serena",
    status="published",
    variants=[
        CatalogVariantDTO(
            id="v1",
            title="Lavanda / Blanco",
            options={"Aroma": "Lavanda", "Color": "Blanco"},
            prices=[CatalogPriceDTO(amount="29000", currency_code="cop")],
        )
    ],
    options={"Aroma": ["Lavanda"], "Color": ["Blanco"]},
)
_ZODIAC = CatalogProductDTO(
    id="p2",
    handle="duo-zodiacal",
    title="Dúo Zodiacal",
    status="published",
    variants=[
        CatalogVariantDTO(
            id="z1",
            title="Leo",
            options={"Signo": "Leo"},
            prices=[CatalogPriceDTO(amount="52000", currency_code="cop")],
        )
    ],
    options={"Signo": ["Leo"]},
)


class _Catalog:
    def __init__(self, products=(_LUZ, _ZODIAC)) -> None:
        self._by = {p.handle: p for p in products}

    async def get_by_handle(self, handle: str):
        if handle not in self._by:
            raise ProductNotFoundError(handle)
        return self._by[handle]

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

        results = list(self._by.values())[:limit]
        return SearchResult(
            query=q,
            count=len(results),
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=len(results)),
            results=results,
        )


@dataclass
class _LLM:
    """Extractor fake: devuelve el JSON crudo que devolvería DeepSeek."""

    reply: str = "{}"
    fails: bool = False
    prompts: list[str] = field(default_factory=list)
    model: str = "fake-deepseek"

    async def extract(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.fails:
            raise RuntimeError("litellm caído")
        return self.reply


@dataclass
class _Harness:
    client: TestClient
    vault: Path
    llm: _LLM

    def write_session(
        self,
        *,
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        session: str = _S,
    ) -> None:
        base = self.vault / session
        (base / "sessions").mkdir(parents=True, exist_ok=True)
        with (base / "sessions" / f"{session}.jsonl").open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        (base / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )

    def meta(self, session: str = _S) -> dict[str, Any]:
        return json.loads((self.vault / session / "metadata.json").read_text(encoding="utf-8"))

    def suggest(self, session: str = _S) -> dict[str, Any]:
        res = self.client.post(f"/api/chats/order-intake/{session}/suggest")
        assert res.status_code == 200, res.text
        return res.json()


@pytest.fixture
def h(tmp_path: Path) -> _Harness:
    llm = _LLM()
    deps = OrderIntakeDeps(vault_dir=tmp_path, catalog=_Catalog(), llm=llm)
    app = FastAPI()
    app.include_router(order_intake.router, prefix="/api/chats")
    app.dependency_overrides[order_intake.get_order_intake_deps] = lambda: deps
    return _Harness(TestClient(app), tmp_path, llm)


# ── fixture de conversación: el caso del chat escalado sin datos de envío ────

def _ts(minute: int) -> str:
    return f"2026-09-16T1{minute // 60}:{minute % 60:02d}:00+00:00"


_HANDOFF_S = 1758_000_000.0  # segundos (lo que escribe `_append_status`)


def _conversation() -> list[dict[str, Any]]:
    """Antes del handoff el cliente eligió el producto; después le dio los
    datos de envío al humano. Timestamps en ms alrededor de `_HANDOFF_S`."""

    def ev(offset_s: int, **kw: Any) -> dict[str, Any]:
        from datetime import datetime, timezone

        stamp = datetime.fromtimestamp(_HANDOFF_S + offset_s, tz=timezone.utc).isoformat()
        return {"timestamp": stamp, **kw}

    return [
        ev(-600, role="user", content="Hola, quiero el Dúo Zodiacal de Leo"),
        ev(-580, role="assistant", content="¡Claro! El Dúo Zodiacal de Leo cuesta $52.000."),
        ev(-300, role="user", content="Sí, quiero 2. Ya llené el formulario… creo"),
        ev(-60, role="assistant", content="Te paso con una asesora."),
        ev(120, role="assistant", sender="human", content="Hola, soy Liliana. ¿Me confirmas tu dirección?"),
        ev(180, role="user", content="Claro: Calle 45 #12-30, barrio Chapinero, Bogotá. Recibe Ana Pérez, 3001234567"),
        ev(240, role="assistant", sender="human", content="Perfecto, ¿pagas por transferencia?"),
        ev(300, role="user", content="Sí, por transferencia"),
    ]


def _metadata(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "active_route": "humano",
        "tag": "HUMANO",
        "status_history": [
            {"tag": "INTERESADO", "motivo": "x", "active_route": "ventas", "timestamp": _HANDOFF_S - 900},
            {
                "tag": "HUMANO",
                "motivo": "El cliente no completó los datos de envío",
                "active_route": "humano",
                "reason_category": "ORDER_PENDING_SHIPPING_DETAILS",
                "timestamp": _HANDOFF_S,
            },
        ],
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": int((_HANDOFF_S - 900) * 1000),
                "closed_at_ms": None,
                "order_draft": {"slots": {"producto": "Dúo Zodiacal", "diseno": "Leo"}},
            }
        ],
    }
    data.update(over)
    return data


_FULL_EXTRACTION = json.dumps(
    {
        "items": [
            {
                "handle": "duo-zodiacal",
                "variant_label": "Leo",
                "quantity": 2,
                "evidence": "quiero el Dúo Zodiacal de Leo … quiero 2",
            }
        ],
        "shipping": {
            "city": "Bogotá",
            "neighborhood": "Chapinero",
            "address": "Calle 45 #12-30",
            "phone": "3001234567",
            "receiver_name": "Ana Pérez",
            "national_id": None,
        },
        "payment_method": "transfer",
        "notes": "El cliente confirmó pago por transferencia.",
    },
    ensure_ascii=False,
)


# ── el camino feliz ──────────────────────────────────────────────────────────


def test_suggest_prellena_el_formulario_con_lo_que_dijo_el_cliente(h: _Harness) -> None:
    h.llm.reply = _FULL_EXTRACTION
    h.write_session(events=_conversation(), metadata=_metadata())

    body = h.suggest()

    assert body["shipping"] == {
        "city": "Bogotá",
        "neighborhood": "Chapinero",
        "address": "Calle 45 #12-30",
        "phone": "3001234567",
        "receiver_name": "Ana Pérez",
        "national_id": None,
    }
    assert body["payment_method"] == "transfer"
    assert body["missing"] == []
    assert body["degraded"] is False
    # Ítem resuelto contra el catálogo: título y precio los pone el SERVIDOR.
    assert body["items"] == [
        {
            "handle": "duo-zodiacal",
            "title": "Dúo Zodiacal",
            "variant_label": "Leo",
            "quantity": 2,
            "unit_price_cop": 52000,
            "line_total_cop": 104000,
            "variant_resolved": True,
            "evidence": "quiero el Dúo Zodiacal de Leo … quiero 2",
        }
    ]
    # Totales = catálogo + tarifa mínima de envío por ciudad (Bogotá).
    assert (body["subtotal_cop"], body["shipping_cop"], body["total_cop"]) == (104000, 7900, 111900)


def test_suggest_no_toca_el_vault(h: _Harness) -> None:
    """Sugerir es READ-ONLY: el pedido lo crea el humano desde el formulario."""
    h.llm.reply = _FULL_EXTRACTION
    h.write_session(events=_conversation(), metadata=_metadata())
    before = h.meta()

    h.suggest()

    assert h.meta() == before


def test_el_prompt_lleva_la_conversacion_marcando_donde_entro_el_humano(h: _Harness) -> None:
    """El LLM necesita ambos lados: el producto se eligió ANTES del handoff y
    los datos de envío salieron DESPUÉS. La marca le dice qué es más fresco."""
    h.llm.reply = _FULL_EXTRACTION
    h.write_session(events=_conversation(), metadata=_metadata())

    h.suggest()

    prompt = h.llm.prompts[0]
    assert "Dúo Zodiacal de Leo" in prompt          # antes del handoff
    assert "Calle 45 #12-30" in prompt              # después del handoff
    assert "EL OPERADOR HUMANO TOMA EL CONTROL" in prompt
    assert "duo-zodiacal" in prompt                 # catálogo con handles reales


def test_el_precio_lo_pone_el_catalogo_aunque_el_llm_invente_uno(h: _Harness) -> None:
    """DES-10 / SEC-07: el precio es el del catálogo, siempre."""
    h.llm.reply = json.dumps(
        {
            "items": [
                {"handle": "duo-zodiacal", "variant_label": "Leo", "quantity": 1, "unit_price_cop": 1},
            ],
            "shipping": {"city": "Bogotá"},
            "payment_method": None,
        }
    )
    h.write_session(events=_conversation(), metadata=_metadata())

    body = h.suggest()

    assert body["items"][0]["unit_price_cop"] == 52000
    assert body["subtotal_cop"] == 52000


# ── degradaciones honestas ───────────────────────────────────────────────────


def test_handle_inventado_por_el_llm_se_descarta_con_aviso(h: _Harness) -> None:
    h.llm.reply = json.dumps(
        {
            "items": [
                {"handle": "vela-que-no-existe", "quantity": 1},
                {"handle": "luz-serena", "quantity": 1},
            ],
            "shipping": {"city": "Medellín"},
            "payment_method": None,
        }
    )
    h.write_session(events=_conversation(), metadata=_metadata())

    body = h.suggest()

    assert [it["handle"] for it in body["items"]] == ["luz-serena"]
    assert any("vela-que-no-existe" in w for w in body["warnings"])
    # Ciudad fuera de Bogotá → tarifa nacional.
    assert body["shipping_cop"] == 16940


def test_si_el_llm_falla_el_formulario_igual_abre_con_el_draft_del_bot(h: _Harness) -> None:
    """El humano NUNCA se queda sin formulario: caemos a los slots que el bot
    ya había capturado (`order_draft`) y lo decimos (`degraded`)."""
    h.llm.fails = True
    meta = _metadata()
    meta["episodes"][0]["order_draft"]["slots"].update(
        {"ciudad": "Bogotá", "direccion": "Calle 45 #12-30", "nombre_recibe": "Ana Pérez"}
    )
    h.write_session(events=_conversation(), metadata=meta)

    body = h.suggest()

    assert body["degraded"] is True
    assert body["error_detail"]
    assert body["shipping"]["city"] == "Bogotá"
    assert body["shipping"]["address"] == "Calle 45 #12-30"
    assert body["field_sources"]["city"] == "draft"
    assert "address" not in body["missing"]


def test_el_draft_rellena_lo_que_el_llm_dejo_vacio_y_la_conversacion_manda(h: _Harness) -> None:
    h.llm.reply = json.dumps(
        {
            "items": [{"handle": "duo-zodiacal", "variant_label": "Leo", "quantity": 1}],
            "shipping": {"city": "Bogotá", "address": "Calle 45 #12-30", "receiver_name": "Ana Pérez"},
            "payment_method": None,
        },
        ensure_ascii=False,
    )
    meta = _metadata()
    meta["episodes"][0]["order_draft"]["slots"].update(
        {"ciudad": "Cali", "barrio": "Granada", "metodo_pago": "contra entrega"}
    )
    h.write_session(events=_conversation(), metadata=meta)

    body = h.suggest()

    assert body["shipping"]["city"] == "Bogotá"              # gana la conversación
    assert body["field_sources"]["city"] == "conversation"
    assert body["shipping"]["neighborhood"] == "Granada"     # lo rellena el draft
    assert body["field_sources"]["neighborhood"] == "draft"
    assert body["payment_method"] == "cash_on_delivery"      # normalizado del draft


def test_el_telefono_cae_al_numero_de_whatsapp_de_la_sesion(h: _Harness) -> None:
    h.llm.reply = json.dumps({"items": [], "shipping": {"city": "Bogotá"}, "payment_method": None})
    h.write_session(events=_conversation(), metadata=_metadata())

    body = h.suggest()

    assert body["shipping"]["phone"] == "573001234567"
    assert body["field_sources"]["phone"] == "session"
    assert "phone" not in body["missing"]


def test_lo_que_falta_se_nombra_para_que_el_humano_lo_complete(h: _Harness) -> None:
    h.llm.reply = json.dumps({"items": [], "shipping": {}, "payment_method": None})
    h.write_session(events=_conversation(), metadata={"active_route": "humano"})

    body = h.suggest()

    assert set(body["missing"]) >= {"city", "address", "receiver_name", "items", "payment_method"}


def test_sin_catalogo_no_se_inventan_items(h: _Harness, tmp_path: Path) -> None:
    llm = _LLM(reply=_FULL_EXTRACTION)
    deps = OrderIntakeDeps(vault_dir=tmp_path, catalog=None, llm=llm)
    app = FastAPI()
    app.include_router(order_intake.router, prefix="/api/chats")
    app.dependency_overrides[order_intake.get_order_intake_deps] = lambda: deps
    harness = _Harness(TestClient(app), tmp_path, llm)
    harness.write_session(events=_conversation(), metadata=_metadata())

    body = harness.suggest()

    assert body["items"] == []
    assert body["catalog"] == []
    assert any("catalog" in w for w in body["warnings"])
    # Los datos de envío SÍ sirven — el humano elige el producto a mano.
    assert body["shipping"]["address"] == "Calle 45 #12-30"


def test_el_formulario_avisa_si_la_sesion_ya_tiene_pedido_registrado(h: _Harness) -> None:
    h.llm.reply = _FULL_EXTRACTION
    meta = _metadata()
    meta["registered_order"] = {"order_id": "order_9", "success": True}
    h.write_session(events=_conversation(), metadata=meta)

    body = h.suggest()

    assert body["already_registered_order_id"] == "order_9"


def test_session_key_invalida_es_422(h: _Harness) -> None:
    res = h.client.post("/api/chats/order-intake/..%2Fetc/suggest")
    assert res.status_code in (404, 422)
    res = h.client.post("/api/chats/order-intake/no-es-wa/suggest")
    assert res.status_code == 422


def test_el_marcador_apunta_al_takeover_ACTUAL_no_a_una_escalacion_vieja(h: _Harness) -> None:
    """Una sesión puede ir humano → bot → humano. El marcador debe caer en el
    ÚLTIMO takeover: si cayera en el primero, el modelo leería como "fresco"
    un tramo que el bot ya manejó y volvió a cerrar."""
    h.llm.reply = _FULL_EXTRACTION
    meta = _metadata()
    meta["status_history"] = [
        {"tag": "HUMANO", "motivo": "vieja", "active_route": "humano", "timestamp": _HANDOFF_S - 800},
        {"tag": "INTERESADO", "motivo": "vuelve al bot", "active_route": "ventas", "timestamp": _HANDOFF_S - 700},
        {"tag": "HUMANO", "motivo": "ahora", "active_route": "humano", "timestamp": _HANDOFF_S},
    ]
    h.write_session(events=_conversation(), metadata=meta)

    body = h.suggest()

    assert body["handoff_at_ms"] == int(_HANDOFF_S * 1000)
    # `rpartition`: el marcador también aparece en las REGLAS del prompt; el
    # que nos interesa es el de la conversación (el último).
    head, _, tail = h.llm.prompts[0].rpartition("EL OPERADOR HUMANO TOMA EL CONTROL")
    assert "Dúo Zodiacal de Leo" in head     # lo viejo queda ANTES del marcador
    assert "Calle 45 #12-30" in tail         # lo del humano actual, DESPUÉS


def test_sesion_devuelta_al_bot_no_marca_handoff(h: _Harness) -> None:
    h.llm.reply = _FULL_EXTRACTION
    meta = _metadata(active_route="ventas")
    h.write_session(events=_conversation(), metadata=meta)

    body = h.suggest()

    assert body["handoff_at_ms"] is None
