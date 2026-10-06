"""La cola de desacuerdos regla ↔ Jev es UNA para todo el sistema.

El motor de decisiones de ventas la llena desde el F2; el Order Sentinel
(F8) también tiene una regla de hoy (su LLM) y una lectura de Jev, y sus
desacuerdos los califica Claude Code con el mismo `scripts/decisions_queue.py`.
Por eso la cola vive en la plataforma y los plugins la toman del SDK
(`src.sdk.connectorkit`), nunca de otro plugin.
"""
from __future__ import annotations

import importlib
import json


def test_el_sdk_expone_la_cola_de_desacuerdos():
    ck = importlib.import_module("src.sdk.connectorkit")
    assert hasattr(ck, "DisagreementLog"), "connectorkit no expone la cola de desacuerdos"


def test_ventas_y_el_sdk_escriben_en_la_misma_cola(tmp_path):
    ck = importlib.import_module("src.sdk.connectorkit")
    from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

    assert DisagreementLog is ck.DisagreementLog

    item_id = ck.DisagreementLog(tmp_path).record(
        capability="estado_pedido",
        state="[1] equipo de la tienda: ya salió tu pedido, llámame al 3001234567",
        rule={"action": "none"},
        jev={"action": "transition", "to_stage": "shipping"},
        model="typesafe/jev-1.13",
        answers=[{"id": "estado_pedido.cambio", "choice": "en_camino", "p": 0.93}],
        session_id="wa_573001234567",
    )

    [row] = [json.loads(line) for line in (tmp_path / "_decisions" / "disagreements.jsonl").read_text().splitlines()]
    assert row["id"] == item_id
    assert row["capability"] == "estado_pedido"
    assert "3001234567" not in row["state"], "el estado se guarda anonimizado"
    assert [i["id"] for i in DisagreementLog(tmp_path).pending("estado_pedido")] == [item_id]


def test_las_metricas_de_cada_decision_tambien_son_una_sola(tmp_path):
    """La vara de producción (caídas < 1 %, días en sombra, decisiones
    medidas) se mide igual para ventas y para el Order Sentinel."""
    ck = importlib.import_module("src.sdk.connectorkit")
    assert hasattr(ck, "DecisionMetrics"), "connectorkit no expone las métricas de decisión"
    from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics

    assert DecisionMetrics is ck.DecisionMetrics
    ck.DecisionMetrics(tmp_path).record(
        capability="estado_pedido", provider="sombra", ok=False, latency_ms=3000, agree=None, at_ms=86_400_000
    )
    [row] = DecisionMetrics(tmp_path).rows("estado_pedido", since_ms=0)
    assert (row["provider"], row["ok"], row["latency_ms"]) == ("sombra", False, 3000)
