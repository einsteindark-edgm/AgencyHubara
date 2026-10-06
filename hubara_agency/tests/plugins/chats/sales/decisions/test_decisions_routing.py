"""El plugin le dice a la plataforma qué versión del workflow de ventas
arranca cada conversación (motor de decisiones F2/F7). Los workers que
despachan eventos hacia Ventas (ventas y remarketing) registran el
enrutador; sin él, la plataforma arrancaría siempre V1 aunque el registro de
bots dijera V2."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.workflow_routing import clear_workflow_routers, route_workflow
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.routing import register_sales_workflow_router


@pytest.fixture(autouse=True)
def _clean(monkeypatch, _isolate_vault_dir):
    clear_workflow_routers()
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    yield
    clear_workflow_routers()


def test_the_router_follows_the_bot_registry(monkeypatch, _isolate_vault_dir: Path) -> None:
    register_sales_workflow_router()

    assert route_workflow("chats", "sales", "manifiesto", session_id="wa_573001234567") == bots.WORKFLOW_V1
    monkeypatch.setenv("SALES_WORKFLOW_V2_CEILING", "on")
    bots.write_workflow_mode(_isolate_vault_dir, "on")
    assert route_workflow("chats", "sales", "manifiesto", session_id="wa_573001234567") == bots.WORKFLOW_V2


@pytest.mark.parametrize("worker", ["src.plugins.chats.workers.sales", "src.plugins.chats.workers.remarketing"])
def test_the_workers_that_start_sales_register_the_router(worker: str) -> None:
    import importlib

    module = importlib.import_module(worker)
    clear_workflow_routers()
    module.register_decisions_routing()

    assert route_workflow("chats", "sales", "manifiesto", session_id="wa_573001234567") == bots.WORKFLOW_V1
