"""La puerta de afirmaciones en el egreso del V2 (2026-11-03).

Colossus afirma hechos que nunca consultó (huecos del 10-28 al 11-03: 25 casos
en 17 conversaciones de 1.034 turnos; CON-03, POS-01, DES-10, ENV-05, EST-05).
El egreso del V2 corre antes de grabar y de enviar: si el paquete trae la
capacidad `afirmacion_previa` (el catálogo la pide desde la versión 8), Jev
dice QUÉ afirma el texto y la tabla decide con los hechos (`bundled_claims`);
su valor es la nota con que `run_agent_turn` retiene el texto y le da UNA ronda
más al modelo (`EgressOutput.retain_note`).

Lo que estas pruebas fijan:
  * `ventas-7` (el paquete de hoy) no trae la puerta: el egreso es el de hoy;
  * la capacidad propuesta (`tests/fixtures/decisions/afirmacion_previa.yaml`)
    certifica en un `ventas-8` de prueba (copia de `ventas-7`): es el YAML
    que lleva el paquete siguiente;
  * cada hecho retiene lo que no se sostiene y deja pasar lo que sí (el caso
    legítimo de cada fila: gotcha 16);
  * nace apagada (`reglas`): ni retiene ni le pregunta a Jev;
  * los hechos del sistema (el pedido, el catálogo, la llave pública, la
    frase de tiempos) los reúne la activity del egreso.

Datos sintéticos: ningún texto ni número de un cliente real.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import egress, registry
from src.plugins.chats.agent.sales.decisions.bundled_claims import ClaimFacts
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.shared import store_pack
from src.sdk.connectorkit import TypedAnswer
from src.sdk.decisionkit import check_bundle

SID = "wa_573000000001"
FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "decisions" / "afirmacion_previa.yaml"
QID = "afirmacion.que_afirma"
APPROVED = "1 a 2 dias habiles, 2 a 3 dias habiles"


def _gate_note(choice: str) -> str:
    """La nota que la tabla del YAML propuesto da para `choice` (sin hechos que la sostengan)."""
    spec = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    for row in spec["decide"]:
        when = row.get("when", "")
        if f"== '{choice}'" in when:
            return row["then"]
    raise KeyError(choice)


@pytest.fixture
def ventas_8(tmp_path: Path, monkeypatch) -> Path:
    """Un `ventas-8` de prueba: `ventas-7` + la puerta y la frase de tiempos."""
    root = tmp_path / "bundles"
    shutil.copytree(registry.BUNDLES_DIR, root)
    shutil.copytree(root / "ventas-7", root / "ventas-8")
    bundle = yaml.safe_load((root / "ventas-8" / "bundle.yaml").read_text(encoding="utf-8"))
    bundle.update(id="ventas-8", version=8, capabilities=sorted([*bundle["capabilities"], "afirmacion_previa"]))
    (root / "ventas-8" / "bundle.yaml").write_text(yaml.safe_dump(bundle, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copy(FIXTURE, root / "ventas-8" / "capabilities" / "afirmacion_previa.yaml")
    domain = yaml.safe_load((root / "ventas-8" / "domain.yaml").read_text(encoding="utf-8"))
    domain["delivery_time_phrases"] = APPROVED
    (root / "ventas-8" / "domain.yaml").write_text(yaml.safe_dump(domain, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(registry, "BUNDLES_DIR", root)
    monkeypatch.setattr(store_pack, "BUNDLES_DIR", root)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield root
    registry.reset()


@pytest.fixture
def ventas_7(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    yield
    registry.reset()


@pytest.fixture(autouse=True)
def oracle(monkeypatch):
    """Ningún test sale a la red: el oráculo es el falso."""
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


def _claims(oracle: dict, choice: str, p: float = 0.92) -> None:
    oracle["fake"] = FakePerceptionAdapter({QID: TypedAnswer(id=QID, kind="choice", choice=choice, probs=((choice, p),), confidence=p)})


def _inp(text: str, **kw) -> EgressInput:
    return EgressInput(session_id=SID, final_text=text, **kw)


async def _egress(inp: EgressInput, *, provider: str = "jev", facts: ClaimFacts | None = None) -> EgressOutput:
    async def claim_facts() -> ClaimFacts:
        return facts or ClaimFacts()

    return await egress.decide_egress(
        inp, provider_of=lambda _c: provider, profile_id="jev-v1", claim_facts=claim_facts,
    )


def _asked_gate(oracle: dict) -> bool:
    return any(QID in str(call) for call in oracle["fake"].calls)


# ── el paquete ───────────────────────────────────────────────────────────────


def test_the_proposed_capability_certifies_in_the_next_bundle(ventas_8: Path) -> None:
    assert [str(d) for d in check_bundle(ventas_8 / "ventas-8", ventas_8 / "builtins.yaml")] == []


def test_the_catalog_asks_for_the_gate_from_version_8() -> None:
    catalog = yaml.safe_load((registry.BUNDLES_DIR / "builtins.yaml").read_text(encoding="utf-8"))

    assert catalog["capabilities_since"]["afirmacion_previa"] == 8
    assert "antes_de_enviar" in catalog["about"]["afirmacion_previa"]["where"]


async def test_todays_bundle_has_no_gate_and_the_egress_is_the_one_of_today(ventas_7, oracle) -> None:
    _claims(oracle, "pedido_registrado")

    out = await _egress(_inp("¡Listo! Tu pedido quedó registrado 🤍"))

    assert out.retain_note == ""
    assert not _asked_gate(oracle)
    assert "afirmacion_previa" not in {v["capability"] for v in out.verdicts}


# ── cada hecho ──────────────────────────────────────────────────────────────


async def test_a_registered_order_claim_without_an_order_is_retained(ventas_8, oracle) -> None:
    _claims(oracle, "pedido_registrado")

    out = await _egress(_inp("¡Listo! Tu pedido quedó registrado 🤍"))

    assert out.retain_note == _gate_note("pedido_registrado")
    # El texto sigue siendo el que saldría si el turno no tiene otra vuelta.
    assert out.text == "¡Listo! Tu pedido quedó registrado 🤍"
    [verdict] = [v for v in out.verdicts if v["capability"] == "afirmacion_previa"]
    assert verdict["by"] == "jev"


async def test_a_registered_order_claim_with_the_order_goes_out(ventas_8, oracle) -> None:
    _claims(oracle, "pedido_registrado")

    out = await _egress(_inp("¡Listo! Tu pedido quedó registrado 🤍", order_registered=True, tools_used=["register_order"]))

    assert out.retain_note == ""


async def test_a_payment_claim_is_retained_unless_check_order_status_said_paid(ventas_8, oracle) -> None:
    _claims(oracle, "pago_recibido")
    paid = {"name": "check_order_status", "args": {}, "result": json.dumps({"orders": [{"payment_confirmed": True}]})}

    unverified = await _egress(_inp("Ya recibimos tu pago 🤍"))
    verified = await _egress(_inp("Ya recibimos tu pago 🤍", tools_used=["check_order_status"], tool_events=[paid]))

    assert unverified.retain_note == _gate_note("pago_recibido")
    assert verified.retain_note == ""


async def test_a_price_the_catalog_does_not_explain_is_retained(ventas_8, oracle) -> None:
    _claims(oracle, "precio")
    facts = ClaimFacts(known_prices=(25_000,))

    wrong = await _egress(_inp("La Vela Luna cuesta $23.000 🤍"), facts=facts)
    right = await _egress(_inp("La Vela Luna cuesta $25.000 🤍"), facts=facts)

    assert wrong.retain_note == _gate_note("precio")
    assert right.retain_note == ""


async def test_bank_details_are_retained_even_if_jev_sees_nothing(ventas_8, oracle) -> None:
    _claims(oracle, "nada")
    facts = ClaimFacts(public_keys=("3009990000",))

    account = await _egress(_inp("Transfiere a la cuenta de ahorros 123-456789-01 🤍"), facts=facts)
    public_key = await _egress(_inp("Puedes pagar por Nequi al 3009990000 🤍"), facts=facts)

    assert account.retain_note.startswith("Escribiste un número de cuenta")
    assert public_key.retain_note == ""


async def test_a_delivery_promise_is_retained_but_the_approved_phrase_goes_out(ventas_8, oracle) -> None:
    _claims(oracle, "entrega")
    facts = ClaimFacts(delivery_phrases=APPROVED)

    promise = await _egress(_inp("¡Te llega mañana! 🤍"), facts=facts)
    approved = await _egress(_inp("En Bogotá llega en 1 a 2 días hábiles 🤍"), facts=facts)

    assert promise.retain_note == _gate_note("entrega")
    assert approved.retain_note == ""


async def test_a_shipping_value_in_the_text_is_retained(ventas_8, oracle) -> None:
    _claims(oracle, "envio")

    out = await _egress(_inp("El envío de tu pedido te sale en $14.500 🤍"))

    assert out.retain_note == _gate_note("envio")


async def test_a_plain_answer_goes_out(ventas_8, oracle) -> None:
    """El caso legítimo de siempre: una pregunta o un saludo no afirma nada."""
    _claims(oracle, "nada")

    out = await _egress(_inp("¿Qué aroma te gustaría? 🤍"))

    assert out.retain_note == "" and out.text == "¿Qué aroma te gustaría? 🤍"


# ── cuándo no corre ─────────────────────────────────────────────────────────


async def test_born_off_it_neither_retains_nor_asks_jev(ventas_8, oracle) -> None:
    _claims(oracle, "pedido_registrado")

    out = await _egress(_inp("¡Listo! Tu pedido quedó registrado 🤍"), provider="reglas")

    assert out.retain_note == ""
    assert not _asked_gate(oracle)


async def test_admin_turns_the_sentinel_and_blocked_texts_never_reach_the_gate(ventas_8, oracle) -> None:
    _claims(oracle, "pedido_registrado")

    for inp in (
        _inp("Tu pedido quedó registrado.", admin_turn=True),
        _inp("NO_MESSAGE"),
        _inp(""),
    ):
        out = await _egress(inp)
        assert out.retain_note == "", inp
    assert not _asked_gate(oracle)


async def test_the_output_still_travels_as_json(ventas_8, oracle) -> None:
    import dataclasses

    _claims(oracle, "pedido_registrado")
    out = await _egress(_inp("¡Listo! Tu pedido quedó registrado 🤍"))

    payload = dataclasses.asdict(out)
    assert json.loads(json.dumps(payload)) == payload


# ── la activity reúne los hechos del sistema ────────────────────────────────


class _Catalog:
    """El catálogo de prueba: un producto con una variante de $25.000."""

    def __init__(self) -> None:
        self.searches = 0

    async def search(self, q: str = "", limit: int = 0):  # noqa: ANN201
        from types import SimpleNamespace

        self.searches += 1
        variant = SimpleNamespace(prices=[SimpleNamespace(amount=25_000, currency_code="cop")])
        return SimpleNamespace(results=[SimpleNamespace(title="Vela Luna", variants=[variant])])


@pytest.fixture
def activity_vault(tmp_path: Path, monkeypatch, ventas_8: Path):
    from src.plugins.chats.agent.sales.decisions import egress_activities

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(egress_activities, "_vault_dir", lambda: vault)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    catalog = _Catalog()
    monkeypatch.setattr(egress_activities, "_catalog_client", lambda: catalog)
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "300 999 0000")
    return vault, catalog


async def _activity(inp: EgressInput) -> EgressOutput:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.decisions.egress_activities import decide_egress_activity

    return await ActivityEnvironment().run(decide_egress_activity, inp)


async def test_the_activity_reads_the_registered_order_of_this_purchase(activity_vault, oracle) -> None:
    from src.plugins.chats.agent.sales.state import FilesystemMetadataStore

    vault, _catalog = activity_vault
    _claims(oracle, "pedido_registrado")
    claim = _inp("¡Listo! Tu pedido quedó registrado 🤍")

    before = await _activity(claim)
    FilesystemMetadataStore(vault).write(SID, {
        "registered_order": {"success": True, "registered_at_ms": 2_000},
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1_000}],
    })
    after = await _activity(claim)

    assert before.retain_note == _gate_note("pedido_registrado")
    assert after.retain_note == ""


async def test_the_activity_reads_the_catalog_prices(activity_vault, oracle) -> None:
    _vault, catalog = activity_vault
    _claims(oracle, "precio")

    right = await _activity(_inp("La Vela Luna cuesta $25.000 🤍"))
    wrong = await _activity(_inp("La Vela Luna cuesta $23.000 🤍"))

    assert right.retain_note == "" and wrong.retain_note == _gate_note("precio")
    assert catalog.searches == 2


async def test_the_activity_allows_the_public_key_and_the_approved_phrase(activity_vault, oracle) -> None:
    _claims(oracle, "nada")
    assert (await _activity(_inp("Puedes pagar por Nequi al 3009990000 🤍"))).retain_note == ""

    _claims(oracle, "entrega")
    assert (await _activity(_inp("En Bogotá llega en 1 a 2 días hábiles 🤍"))).retain_note == ""
    assert (await _activity(_inp("¡Te llega mañana! 🤍"))).retain_note == _gate_note("entrega")


async def test_a_source_that_fails_never_stops_the_text(activity_vault, oracle, monkeypatch) -> None:
    """Sin catálogo (o sin vault) el hecho no se afirma: el texto no se retiene
    por un dato que falta, y la activity no falla."""
    from src.plugins.chats.agent.sales.decisions import egress_activities

    def broken():
        raise RuntimeError("catálogo caído")

    monkeypatch.setattr(egress_activities, "_catalog_client", broken)
    _claims(oracle, "precio")

    out = await _activity(_inp("La Vela Luna cuesta $23.000 🤍"))

    assert out.retain_note == "" and out.error is None
