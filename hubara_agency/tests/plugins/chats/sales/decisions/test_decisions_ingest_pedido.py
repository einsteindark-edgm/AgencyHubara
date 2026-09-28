"""El ingest le pide al motor dos lecturas sueltas (fase F3), con el bot de la
conversación, y hace con ellas exactamente lo de hoy:

* cupón: ¿este mensaje habla del cupón aplicado? Decide si se relee el cupo
  (prueba del 2026-09-24: «¿Si tienes 2 de esa?» → «Sí, claro» con cupo 1) y
  si la nota lleva las combinaciones o solo lo recuerda en una línea.
* fuera de catálogo: lo que el cliente pide o muestra y no existe; la nota la
  arma el código de hoy con los términos que dejó el motor.

No escriben campos del metadata (no son `Readings`): el ingest las pide con
los ayudantes de `decisions/readings.py`. Con `reglas` (así nace) el turno es
el de hoy; acá se prende cada capacidad con el control de producción
(`_rollout/decisions.json`, dentro del techo de Terraform).
"""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.sdk.catalogkit import CatalogProductDTO, CatalogVariantDTO
from src.sdk.connectorkit import PromotionDTO, TypedAnswer

SID = "wa_573001234567"


class _History:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = list(events or [])

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self.events.append({"role": "user", "content": content})

    def read_events(self, session_id: str) -> list[dict]:
        return list(self.events)


class _Loader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, session_id, message, phone_number_id, extra_context=None, prefer_sales=False,
                      inbound_meta=None) -> None:
        self.calls.append({"message": message, "extra_context": list(extra_context or [])})

    @property
    def context(self) -> str:
        return "\n".join(self.calls[-1]["extra_context"])


class _Store:
    def __init__(self, data: dict) -> None:
        self.data = {SID: data}

    def read(self, session_id):
        return dict(self.data.get(session_id, {}))

    def write(self, session_id, data):
        self.data[session_id] = dict(data)

    def update(self, session_id, mutator):
        result = mutator(self.read(session_id))
        if result is not None:
            self.write(session_id, result)
        return result


class _UnitsNow:
    def __init__(self, offer: QuotaOffer) -> None:
        self.offer, self.calls = offer, 0

    async def __call__(self, promotion: dict) -> QuotaOffer:
        self.calls += 1
        return self.offer


def _cilindro(color: str, aroma: str, left: int) -> dict:
    return {"handle": "cilindro-love", "title": "Cilindro Love", "color": color, "aroma": aroma,
            "units_left": left, "price_cop": 23500, "discounted_price_cop": 21150}


def _promo() -> PromotionDTO:
    return PromotionDTO(
        id="promo_amor26", code="AMOR26", discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=("prod_cilindro",),
        variant_ids=(), collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active",
        starts_at_ms=None, ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None,
        description="Amor y amistad",
    )


def _quota_coupon_md(now_ms: int) -> dict:
    """Cupón con cupo aplicado y SIN producto del cupón en el pedido: si el
    mensaje habla del cupón lo decide el texto."""
    return {
        "active_route": "ventas", "tag": "NO_ETIQUETADO", "last_inbound_at_ms": now_ms - 60_000,
        "episodes": [{
            "episode_id": "ep_009", "started_at_ms": now_ms - 10 * 60_000, "closed_at_ms": None, "order_id": None,
            "applied_coupon": {
                "code": "AMOR26", "promotion": asdict(_promo()), "applied_at_ms": now_ms - 5 * 60_000,
                "eligible_products": [], "quota": True, "show_units_left": True,
                "units": [_cilindro("Azul", "Lavanda", 1), _cilindro("Rosado", "Caballero de la noche", 1)],
            },
        }],
    }


def _both_left() -> QuotaOffer:
    return QuotaOffer(True, None, (_cilindro("Azul", "Lavanda", 1), _cilindro("Rosado", "Caballero de la noche", 1)), True)


def _message(text: str) -> WhatsAppMessage:
    return WhatsAppMessage(message_id="wamid.F3", from_number=SID.removeprefix("wa_"), phone_number_id="PID",
                           text=text, media=None, timestamp=str(int(time.time())))


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def turn_on(monkeypatch, _isolate_vault_dir: Path):
    """Prende capacidades con el control de producción (techo de Terraform en `on`)."""
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")

    def _on(*capabilities: str) -> None:
        bots.write_capability_modes(_isolate_vault_dir, {c: "on" for c in capabilities})

    return _on


def _coupon_ingest(loader: _Loader, units: _UnitsNow, history: _History | None = None) -> IngestInboundMessage:
    now = int(time.time() * 1000)
    return IngestInboundMessage(
        history_store=history or _History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=_Store(_quota_coupon_md(now)),  # type: ignore[arg-type]
        coupon_units_now=units,
    )


POSTAL = "Te paso el código postal: 110111"
BENEFIT = "¿Todavía aplica el beneficio del mensaje?"


# --- cupón --------------------------------------------------------------------


async def test_with_rules_a_postal_code_brings_the_coupon_in_as_today(_isolate_vault_dir, oracle) -> None:
    loader, units = _Loader(), _UnitsNow(_both_left())

    await _coupon_ingest(loader, units).execute(_message(POSTAL))

    assert units.calls == 1  # «código» saca el cupón a la charla (el falso positivo de hoy)
    assert "Azul · Lavanda (queda 1)" in loader.context
    assert not any("cupon.habla" in q for _, q in oracle["fake"].calls)


async def test_jev_keeps_a_postal_code_out_of_the_coupon(_isolate_vault_dir, oracle, turn_on) -> None:
    turn_on("cupon")
    oracle["fake"] = FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.03)})
    loader, units = _Loader(), _UnitsNow(_both_left())

    await _coupon_ingest(loader, units).execute(_message(POSTAL))

    assert units.calls == 0  # sin relectura del cupo
    assert "AMOR26" in loader.context and "sin límite" in loader.context
    assert "Azul · Lavanda (queda" not in loader.context  # el cupón no se mete en la charla


async def test_jev_brings_the_question_about_the_benefit_into_the_coupon(_isolate_vault_dir, oracle, turn_on) -> None:
    turn_on("cupon")
    oracle["fake"] = FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.95)})
    loader, units = _Loader(), _UnitsNow(_both_left())
    history = _History([{"role": "assistant", "content": "Con AMOR26 tu Cilindro Love queda con 10 % menos 💝"}])

    await _coupon_ingest(loader, units, history).execute(_message(BENEFIT))

    assert units.calls == 1
    assert "Azul · Lavanda (queda 1)" in loader.context
    [(state, _)] = [call for call in oracle["fake"].calls if call[1] == ("cupon.habla",)]
    # Jev ve lo que el cliente vio ANTES de este mensaje, y el mensaje una sola vez.
    assert "10 % menos" in state and state.count(BENEFIT) == 1


# --- fuera de catálogo --------------------------------------------------------

_TAGS = ["Aroma: Café", "Aroma: Lavanda", "Color: Azul", "Color: Rosado"]
CATALOG = [
    CatalogProductDTO(
        id="prod_cubo", handle="cubo-love", title="Cubo Love", status="published",
        description="La clásica vela cúbica con la palabra LOVE.", tags=list(_TAGS),
        variants=[CatalogVariantDTO(id="var_cubo", title="Unico")], options={"Unico": ["Unico"]},
    )
]
CARTAGENA = "Vivo en Cartagena, ¿la tienen en vaso?"


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        return SimpleNamespace(results=list(CATALOG)[:limit])


def _gap_ingest(loader: _Loader) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=_Store({}),  # type: ignore[arg-type]
        catalog=_Catalog(),  # type: ignore[arg-type]
    )


def _gap_note(loader: _Loader) -> str:
    return next(n for n in loader.calls[-1]["extra_context"] if "NO existe en el catálogo" in n)


async def test_with_rules_the_gap_note_is_todays(_isolate_vault_dir, oracle) -> None:
    loader = _Loader()

    await _gap_ingest(loader).execute(_message(CARTAGENA))

    note = _gap_note(loader)
    assert "«cartagena»" in note and "«vaso»" in note


async def test_jev_takes_the_city_out_of_the_gap_note(_isolate_vault_dir, oracle, turn_on) -> None:
    turn_on("fuera_de_catalogo")
    oracle["fake"] = FakePerceptionAdapter({
        "fuera_de_catalogo.termino_1": _noul("fuera_de_catalogo.termino_1", 0.02),
        "fuera_de_catalogo.termino_2": _noul("fuera_de_catalogo.termino_2", 0.97),
    })
    loader = _Loader()

    await _gap_ingest(loader).execute(_message(CARTAGENA))

    note = _gap_note(loader)
    assert "«vaso»" in note and "cartagena" not in note.lower()


async def test_when_jev_takes_out_every_term_there_is_no_gap_note(_isolate_vault_dir, oracle, turn_on) -> None:
    turn_on("fuera_de_catalogo")
    oracle["fake"] = FakePerceptionAdapter({"fuera_de_catalogo.termino_1": _noul("fuera_de_catalogo.termino_1", 0.01)})
    loader = _Loader()

    await _gap_ingest(loader).execute(_message("Vivo en Cartagena"))

    assert not any("NO existe en el catálogo" in n for n in loader.calls[-1]["extra_context"])


async def test_when_jev_fails_the_gap_note_is_todays(_isolate_vault_dir, oracle, turn_on) -> None:
    turn_on("fuera_de_catalogo")
    oracle["fake"] = FakePerceptionAdapter({}, error="timeout")
    loader = _Loader()

    await _gap_ingest(loader).execute(_message(CARTAGENA))

    note = _gap_note(loader)
    assert "«cartagena»" in note and "«vaso»" in note
