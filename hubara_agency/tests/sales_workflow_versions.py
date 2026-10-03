"""Paridad del workflow de ventas V2 con el V1 (motor de decisiones F4: B0 = A1).

Las suites del V1 corren también contra el V2: un fixture autouse parametriza
cada test con la versión del workflow (`[v1]` y `[v2]`) y re-apunta el nombre
`HubaraSalesSessionWorkflow` del módulo de tests a la clase del V2. Las
activities falsas son las mismas; la del egreso es la REAL (`decide_egress`):
con el bot de hoy decide con las reglas del V1.

Cada suite declara `V2_EXCLUDED = {nombre del test: motivo}` con SOLO tests de
patches, replay o ramas legacy del V1, o de las diferencias a propósito del V2
(`tests/test_sales_workflow_v2.py`). En `[v2]` un test excluido sale SKIPPED
con su motivo; `test_sales_workflow_v2.py` exige que cada exclusión nombre un
test que existe.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping

import pytest

from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

VERSIONS: dict[str, type] = {"v1": HubaraSalesSessionWorkflow, "v2": HubaraSalesSessionWorkflowV2}

#: Suites del V1 que también corren contra el V2 (las lee el guard de
#: exclusiones de `test_sales_workflow_v2.py`).
PARITY_SUITES: tuple[str, ...] = (
    "tests.test_sales_workflow_debounce",
    "tests.plugins.chats.test_sales_capi_trigger",
    "tests.test_sales_burst_inbound_ids",
    "tests.test_sales_reply_channel",
    "tests.test_sales_returning_customer_greeting",
    "tests.test_sales_turn_steps",
    "tests.test_sales_perception_layers",
    "tests.test_sales_burst_waits_for_photo",
    "tests.test_sales_promised_handoff",
)


def sales_workflow_versions(module_name: str, excluded: Mapping[str, str]) -> Callable:
    """El fixture autouse de una suite: `_sales_workflow_version = sales_workflow_versions(__name__, V2_EXCLUDED)`."""

    @pytest.fixture(autouse=True, params=tuple(VERSIONS))
    def _sales_workflow_version(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
        version = str(request.param)
        if version == "v2":
            reason = excluded.get(request.node.originalname)
            if reason is not None:
                pytest.skip(f"solo V1: {reason}")
        monkeypatch.setattr(sys.modules[module_name], "HubaraSalesSessionWorkflow", VERSIONS[version])
        return version

    return _sales_workflow_version
