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
        if "*CONVERSATIONAL_TURN_ACTIVITIES" in source:
            continue
        # Excepción BLESSED (motor de decisiones F2): spread de la misma
        # tupla a través de una TABLA DE SUSTITUCIONES DECLARADAS
        # (`ACTIVITY_SUBSTITUTIONS`: nombre de activity → override homónimo;
        # hoy `build_prompt` → `sales_build_prompt`, guion por etapa). Sigue
        # siendo la tupla como fuente (nada listado a mano); cada sustitución
        # la valida `test_declared_substitutions_keep_names_and_replace_one_to_one`.
        # Cualquier otro desvío reabre la clase L-3.
        assert (
            "for a in CONVERSATIONAL_TURN_ACTIVITIES" in source
            and "ACTIVITY_SUBSTITUTIONS" in source
        ), (
            f"{worker_path.name} no spread-ea CONVERSATIONAL_TURN_ACTIVITIES "
            f"en su activities=[...] (ni usa el patrón blessed de override de "
            f"build_prompt) — listar el set conversacional a mano reabre la "
            f"clase de error L-3 (activity faltante = NotFoundError en "
            f"runtime, no en boot)."
        )


def test_sales_build_prompt_override_keeps_activity_name() -> None:
    """El override de Sales DEBE registrarse con el nombre 'build_prompt'.

    Si el nombre difiere, el workflow (que invoca por la referencia genérica
    → nombre "build_prompt") muere en runtime con NotFoundError — la misma
    clase L-3 que este archivo protege."""
    override = (
        _HUBARA_ROOT
        / "src/plugins/chats/agent/sales/activities/build_prompt_stage.py"
    )
    source = override.read_text(encoding="utf-8")
    assert '@activity.defn(name="build_prompt")' in source, (
        "sales_build_prompt debe declararse @activity.defn(name=\"build_prompt\") "
        "— con otro nombre el workflow no la encuentra (NotFoundError runtime)."
    )


def test_declared_substitutions_keep_names_and_replace_one_to_one() -> None:
    """Motor de decisiones F2 (enchufe 2): las activities se sustituyen por
    NOMBRE desde una tabla declarada en el worker. Cada sustituta se registra
    con el mismo nombre que la del turno compartido (si no, el workflow no la
    encuentra: NotFoundError en runtime, L-3), reemplaza exactamente a UNA y
    el worker no registra dos activities con el mismo nombre."""
    import src.plugins.chats.workers.sales as sales_worker

    shared = {a.__temporal_activity_definition.name: a for a in CONVERSATIONAL_TURN_ACTIVITIES}
    registered = [a.__temporal_activity_definition.name for a in sales_worker.SALES_ACTIVITIES]

    assert sales_worker.ACTIVITY_SUBSTITUTIONS, "sin sustituciones declaradas"
    for name, substitute in sales_worker.ACTIVITY_SUBSTITUTIONS.items():
        assert name in shared, f"{name} no es una activity del turno compartido"
        assert substitute.__temporal_activity_definition.name == name
        assert substitute in sales_worker.SALES_ACTIVITIES and shared[name] not in sales_worker.SALES_ACTIVITIES
    assert len(registered) == len(set(registered)), "nombres de activity duplicados en el worker"

