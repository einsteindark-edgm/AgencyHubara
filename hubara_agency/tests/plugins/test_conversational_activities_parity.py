"""Guard L-3: paridad entre lo que invocan los helpers conversacionales y
lo que registran los workers.

Clase de error que protege: un workflow invoca una activity que su worker
NO registró. No falla al boot ni en tests de import — muere en RUNTIME con
`NotFoundError` cuando un cliente real conversa (caso eta 2026-06-10: el
worker registraba 5 de las 6 activities del turno; `record_episode_llm_usage`
vivía detrás de `workflow.patched("episode-llm-cost-v1")` y la detonó la
primera respuesta de un cliente — workflow FAILED tras agotar retries).

Dos candados:

1. **AST**: toda activity que `workflow_helpers.py` pase a
   `execute_activity(...)` debe estar en `CONVERSATIONAL_TURN_ACTIVITIES`
   (la tupla vive en el mismo módulo — quien agrega una invocación nueva
   la suma ahí o este test lo frena).
2. **Spread literal**: los workers conversacionales registran via
   `*CONVERSATIONAL_TURN_ACTIVITIES` — nunca listando esas activities a
   mano (volver a la lista manual reabre la clase entera).
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.platform.workflow_helpers import CONVERSATIONAL_TURN_ACTIVITIES

_HUBARA_ROOT = Path(__file__).resolve().parents[2]
_HELPERS = _HUBARA_ROOT / "src/platform/workflow_helpers.py"

# Workers cuyos workflows corren `run_agent_turn` (sales_eval NO — no es
# conversacional). Un worker conversacional nuevo se agrega ACÁ.
_CONVERSATIONAL_WORKERS = (
    _HUBARA_ROOT / "src/plugins/chats/workers/sales.py",
    _HUBARA_ROOT / "src/plugins/chats/workers/remarketing.py",
    _HUBARA_ROOT / "src/plugins/eta/workers/eta.py",
)


def _activities_invoked_by_helpers() -> set[str]:
    tree = ast.parse(_HELPERS.read_text(encoding="utf-8"))
    invoked: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_execute = (
            isinstance(func, ast.Attribute) and func.attr == "execute_activity"
        )
        if is_execute and node.args and isinstance(node.args[0], ast.Name):
            invoked.add(node.args[0].id)
    return invoked


def test_shared_tuple_covers_every_helper_invocation() -> None:
    invoked = _activities_invoked_by_helpers()
    assert invoked, "el AST no encontró execute_activity — ¿cambió el helper?"
    tuple_names = {a.__name__ for a in CONVERSATIONAL_TURN_ACTIVITIES}
    missing = invoked - tuple_names
    assert not missing, (
        f"workflow_helpers invoca {sorted(missing)} pero no está(n) en "
        f"CONVERSATIONAL_TURN_ACTIVITIES — sumalas a la tupla EN ESTE MISMO "
        f"COMMIT o todo worker conversacional muere en runtime (L-3)."
    )


def test_conversational_workers_spread_the_shared_tuple() -> None:
    for worker_path in _CONVERSATIONAL_WORKERS:
        source = worker_path.read_text(encoding="utf-8")
        assert "*CONVERSATIONAL_TURN_ACTIVITIES" in source, (
            f"{worker_path.name} no spread-ea CONVERSATIONAL_TURN_ACTIVITIES "
            f"en su activities=[...] — listar el set conversacional a mano "
            f"reabre la clase de error L-3 (activity faltante = NotFoundError "
            f"en runtime, no en boot)."
        )
