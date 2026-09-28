"""Guardia del motor para las tools (diseño v2 §03, regla 5).

Las tools le piden decisiones al motor por `guards.decide_for_session`: el
proveedor sale del registro de bots de la conversación y los desacuerdos van
a la cola. Importar la guardia NO trae Temporal: las tools no pueden
depender del SDK de Temporal (contrato `tools-no-temporal`)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from tests.plugins.chats.sales.decisions.test_decisions_capabilities import Ask, Unsubscribe, _Port

_HUBARA = Path(__file__).resolve().parents[5]


def test_importing_the_guard_does_not_load_temporal() -> None:
    code = (
        "import sys\n"
        "import src.plugins.chats.agent.sales.decisions.guards\n"
        "print(sorted(m for m in sys.modules if m == 'temporalio' or m.startswith('temporalio.')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=_HUBARA, capture_output=True, text=True, check=True)

    assert out.stdout.strip() == "[]"


async def test_a_tool_gets_the_rule_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)

    verdict = await decide_for_session(Unsubscribe(), Ask("no me escriban"), session_id="wa_1", vault_dir=tmp_path)

    assert (verdict.value, verdict.by) == (False, "reglas")


async def test_a_tool_in_shadow_queues_the_disagreement(tmp_path: Path, monkeypatch) -> None:
    from src.sdk import connectorkit

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"juguete": "shadow"})
    port = _Port(0.95)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)

    verdict = await decide_for_session(Unsubscribe(), Ask("no me escriban"), session_id="wa_1", vault_dir=tmp_path)

    assert (verdict.value, verdict.by, verdict.jev) == (False, "reglas", True)
    assert [i["capability"] for i in DisagreementLog(tmp_path).pending()] == ["juguete"]
