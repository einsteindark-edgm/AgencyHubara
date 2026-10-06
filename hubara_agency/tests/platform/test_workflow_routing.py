"""Enrutamiento del workflow por conversación (motor de decisiones, fase F2).

La plataforma arranca workflows de agentes por NOMBRE (manifiesto, ADR
2026-05-20) en tres lugares: el dispatcher de orquestación, la activity de
arranque de ventas y el ingest. Con el workflow V2 de ventas, cuál versión
corre lo decide el registro de bots del plugin, por conversación. La
plataforma no conoce el plugin (R-DIP #9): el plugin registra un enrutador y
la plataforma lo consulta; sin enrutador, el nombre del manifiesto.
"""
from __future__ import annotations

import pytest

from src.platform import workflow_routing as routing


@pytest.fixture(autouse=True)
def _clean():
    routing.clear_workflow_routers()
    yield
    routing.clear_workflow_routers()


def test_without_a_router_it_is_the_manifest_name() -> None:
    assert routing.route_workflow("chats", "sales", "HubaraSalesSessionWorkflow", session_id="wa_1") == "HubaraSalesSessionWorkflow"


def test_a_plugin_router_picks_the_workflow_of_each_conversation() -> None:
    routing.register_workflow_router("chats", "sales", lambda sid: "HubaraSalesSessionWorkflowV2" if sid == "wa_2" else None)

    assert routing.route_workflow("chats", "sales", "HubaraSalesSessionWorkflow", session_id="wa_2") == "HubaraSalesSessionWorkflowV2"
    assert routing.route_workflow("chats", "sales", "HubaraSalesSessionWorkflow", session_id="wa_1") == "HubaraSalesSessionWorkflow"
    assert routing.route_workflow("chats", "remarketing", "RemarketingWorkflow", session_id="wa_2") == "RemarketingWorkflow"


def test_a_broken_router_never_stops_the_start() -> None:
    def broken(_sid: str) -> str:
        raise RuntimeError("vault caído")

    routing.register_workflow_router("chats", "sales", broken)

    assert routing.route_workflow("chats", "sales", "HubaraSalesSessionWorkflow", session_id="wa_1") == "HubaraSalesSessionWorkflow"


def test_without_a_session_it_is_the_manifest_name() -> None:
    routing.register_workflow_router("chats", "sales", lambda _sid: "HubaraSalesSessionWorkflowV2")

    assert routing.route_workflow("chats", "sales", "HubaraSalesSessionWorkflow", session_id=None) == "HubaraSalesSessionWorkflow"


def test_the_sdk_exposes_the_registration_for_plugins() -> None:
    from src.sdk import foundation

    assert foundation.register_workflow_router is routing.register_workflow_router
