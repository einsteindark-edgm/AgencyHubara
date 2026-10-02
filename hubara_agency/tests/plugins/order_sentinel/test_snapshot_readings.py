"""La activity del snapshot le pregunta a Jev el estado del pedido (F8).

El lector lo fija Terraform (`ORDER_SENTINEL_READER`: off | shadow | on) y
nace apagado: sin él, el snapshot es EXACTAMENTE el de hoy y Jev no se llama.
En sombra o en jev, cada conversación viaja con su lectura (la forma del
veredicto del LLM) y cada decisión deja su métrica para la vara de
producción. Si Jev cae, la lectura no trae veredicto: decide el LLM.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from temporalio.testing import ActivityEnvironment

from src.plugins.order_sentinel.agent.cycle import activities as acts
from src.sdk.connectorkit import DecisionMetrics, FakePerceptionAdapter, TypedAnswer
from tests.plugins.order_sentinel.test_snapshot_activity import _order_detail, _seed_session

BASE = "http://hubara-api.invalid"
SID = "wa_573009876543"


class RecordingPort:
    """El fake oficial + lo que se tapó en cada pregunta."""

    def __init__(self, answers=None, *, error=None, cost_usd=0.0) -> None:
        self.inner = FakePerceptionAdapter(answers, error=error, cost_usd=cost_usd)
        self.redacts: list[tuple[str, ...]] = []

    @property
    def calls(self):
        return self.inner.calls

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.redacts.append(tuple(redact))
        return await self.inner.ask(state, questions, timeout_s=timeout_s, redact=redact)


def _seed(vault: Path) -> None:
    metadata = {
        "tag": "HUMANO",
        "episodes": [
            {
                "episode_id": "ep_1",
                "order_id": "order_01SALIO",
                "order_draft": {"slots": {"nombre_recibe": "Ana María Pérez"}},
            }
        ],
    }
    _seed_session(
        vault,
        SID,
        metadata,
        [
            {"role": "user", "content": "hola, ¿mi pedido ya salió?", "timestamp": "2026-09-27T15:00:00+00:00"},
            {
                "role": "assistant",
                "sender": "human",
                "content": "Hola Ana! ya salió con el mensajero",
                "timestamp": "2026-09-27T15:05:00+00:00",
            },
        ],
    )
    respx.get(f"{BASE}/api/orders/orders/order_01SALIO").mock(
        return_value=httpx.Response(200, json=_order_detail("ready", "paid"))
    )


_JEV_SALIO = {
    "estado_pedido.cambio": TypedAnswer(
        id="estado_pedido.cambio", kind="choice", choice="en_camino", probs=(("en_camino", 0.93),), confidence=0.93
    ),
    "estado_pedido.evidencia.1": TypedAnswer(id="estado_pedido.evidencia.1", kind="noul", p=0.1),
    "estado_pedido.evidencia.2": TypedAnswer(id="estado_pedido.evidencia.2", kind="noul", p=0.96),
}


@pytest.mark.asyncio
@respx.mock
async def test_sin_lector_el_snapshot_es_el_de_hoy_y_jev_no_se_llama(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.delenv("ORDER_SENTINEL_READER", raising=False)
    port = RecordingPort(_JEV_SALIO)
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)
    _seed(_isolate_vault_dir)

    snapshot = await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    [convo] = snapshot["conversations"]
    assert "reader" not in snapshot
    assert "reading" not in convo
    assert port.calls == []
    assert DecisionMetrics(_isolate_vault_dir).rows("estado_pedido", since_ms=0) == []


@pytest.mark.asyncio
@respx.mock
async def test_en_sombra_cada_conversacion_viaja_con_la_lectura_de_jev(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.setenv("ORDER_SENTINEL_READER", "shadow")
    port = RecordingPort(_JEV_SALIO)
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)
    _seed(_isolate_vault_dir)

    snapshot = await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    [convo] = snapshot["conversations"]
    assert snapshot["reader"] == "sombra"
    assert convo["reading"]["verdict"] == {
        "action": "transition",
        "to_stage": "shipping",
        "evidence": ["Hola Ana! ya salió con el mensajero"],
        "confidence": "high",
    }
    assert [ids for _, ids in port.calls] == [
        ("estado_pedido.cambio",),
        ("estado_pedido.evidencia.1", "estado_pedido.evidencia.2"),
    ], "la evidencia se pregunta solo cuando Jev está seguro de que algo cambió"
    assert all("Ana María Pérez" in terms for terms in port.redacts), "el nombre del borrador no sale hacia Jev"
    [row] = DecisionMetrics(_isolate_vault_dir).rows("estado_pedido", since_ms=0)
    assert (row["provider"], row["ok"]) == ("sombra", True)


@pytest.mark.asyncio
@respx.mock
async def test_si_jev_cae_la_lectura_no_trae_veredicto_y_la_metrica_cuenta_la_caida(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.setenv("ORDER_SENTINEL_READER", "on")
    port = RecordingPort(error="timeout")
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)
    _seed(_isolate_vault_dir)

    snapshot = await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    [convo] = snapshot["conversations"]
    assert snapshot["reader"] == "jev"
    assert (convo["reading"]["verdict"], convo["reading"]["error"]) == (None, "timeout")
    [row] = DecisionMetrics(_isolate_vault_dir).rows("estado_pedido", since_ms=0)
    assert (row["provider"], row["ok"]) == ("jev", False)


@pytest.mark.asyncio
@respx.mock
async def test_la_evidencia_se_pregunta_solo_sobre_lo_nuevo_desde_el_watermark(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.setenv("ORDER_SENTINEL_READER", "shadow")
    port = RecordingPort(_JEV_SALIO)
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)
    _seed(_isolate_vault_dir)
    # El ciclo anterior analizó hasta el primer mensaje (15:00): solo el del
    # equipo (15:05) es nuevo.
    (_isolate_vault_dir / SID / "order_sentinel.json").write_text(
        '{"last_analyzed_at_ms": 1790521200000}', encoding="utf-8"
    )

    snapshot = await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    [convo] = snapshot["conversations"]
    assert [ids for _, ids in port.calls][1] == ("estado_pedido.evidencia.2",)
    assert convo["reading"]["verdict"]["evidence"] == ["Hola Ana! ya salió con el mensajero"]


@pytest.mark.asyncio
@respx.mock
async def test_lo_que_cobra_jev_queda_en_la_conversacion(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Las dos preguntas del lector (¿cambió? y la evidencia) se suman al
    costo de Jev de esa conversación, como en ventas."""
    import json

    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.setenv("ORDER_SENTINEL_READER", "shadow")
    port = RecordingPort(_JEV_SALIO, cost_usd=0.00002)
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)
    _seed(_isolate_vault_dir)

    await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    metadata = json.loads((_isolate_vault_dir / SID / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["episodes"][0]["jev_usage"] == {"calls": 2, "cost_usd_micros": 40}


@pytest.mark.asyncio
@respx.mock
async def test_un_paquete_roto_no_se_cuenta_como_caida_de_jev(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Premortem 2026-10-02: con el paquete del lector roto, cada conversación
    cae al LLM (bien), pero la métrica lo contaba como Jev caído y ensuciaba la
    vara de producción del lector. Queda `bundle_error` en la lectura, un error
    en el log y ninguna métrica de Jev."""
    import structlog

    from src.plugins.order_sentinel.agent import decisions
    from src.sdk.decisionkit import BundleError, Diagnostic

    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    monkeypatch.setenv("ORDER_SENTINEL_READER", "shadow")
    port = RecordingPort(_JEV_SALIO)
    monkeypatch.setattr(acts, "get_reader_port", lambda: port)

    def broken():
        raise BundleError([Diagnostic("DB005", "capabilities/cambio.yaml: decide[0].when", "roto")])

    monkeypatch.setattr(decisions, "active_bundle", broken)
    _seed(_isolate_vault_dir)

    with structlog.testing.capture_logs() as logs:
        snapshot = await ActivityEnvironment().run(acts.build_order_sentinel_snapshot_activity)

    [convo] = snapshot["conversations"]
    assert convo["reading"]["verdict"] is None and convo["reading"]["error"] == "bundle_error"
    assert port.calls == []
    assert DecisionMetrics(_isolate_vault_dir).rows("estado_pedido", since_ms=0) == []
    assert any(e["event"] == "order_sentinel.bundle_broken" and e["log_level"] == "error" for e in logs), logs
