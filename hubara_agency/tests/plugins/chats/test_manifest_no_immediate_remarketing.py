"""Guard: el remarketing YA NO se dispara inmediato tras el tag INTERESADO.

Hasta PR #113 el tag INTERESADO programaba un RemarketingWorkflow a los 300s
(stopgap `_REMARKETING_DELAY_SECONDS`, transition
`sales_to_remarketing_on_interested`). Con el Window Strategist ese camino se
ELIMINA: la reactivación la decide el ciclo del agente (ventana × warmth ×
cadencia × presupuesto) y la ejecuta la transition del plugin `reengagement`
— el único dispatcher declarativo del RemarketingWorkflow.

Si este test se pone rojo, alguien re-cableó un disparo directo de remarketing
desde sales — eso duplica toques (post 1-oct = plata) y esquiva el guardrail
del agente. NO re-agregar sin ADR.
"""
from __future__ import annotations

from src.sdk import get_transitions


def test_sales_no_dispara_remarketing_directo():
    sales_transitions = get_transitions("chats", "sales")
    remarketing_targets = [
        t
        for t in sales_transitions
        if t.action.target_workflow == "RemarketingWorkflow"
    ]
    assert remarketing_targets == [], (
        "sales NO debe arrancar RemarketingWorkflow directo — la reactivación "
        f"es del Window Strategist (plugin reengagement). Encontré: "
        f"{[t.id for t in remarketing_targets]}"
    )


def test_reengagement_es_el_unico_dispatcher_declarativo_de_remarketing():
    reeng = get_transitions("reengagement", "cycle")
    assert any(
        t.action.target_workflow == "RemarketingWorkflow" for t in reeng
    ), "el plugin reengagement debe ser quien arranca el RemarketingWorkflow"
