"""El golden eval corre solo (Ciclo Colossus F4, 2026-11-03).

Antes solo corría a mano (`workflow_dispatch`). Ahora también:

* en cada PR que toca lo que cambia cómo vende el bot (paquetes de decisión,
  prompts del asesor) o cómo trabajan los agentes del ciclo (`CLAUDE.md`,
  `.claude/skills/`), y deja UN comentario con el resumen que se actualiza en
  cada corrida (marcador, no comentarios repetidos);
* cada domingo, con el paquete que corre la tienda.

Sigue siendo un reporte, no un check obligatorio. Sin llaves (PR desde un fork)
se salta sin fallar. Una corrida nueva del mismo PR cancela la anterior.

Se leen los workflows como texto/YAML (mismo criterio que test_decision_bundles_gates.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_GOLDEN = _REPO / ".github" / "workflows" / "golden-eval.yml"


def _workflow() -> dict:
    data = yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 lee `on:` como True
    return data


def _steps(data: dict) -> list[dict]:
    return [step for job in data["jobs"].values() for step in job.get("steps") or []]


def test_it_runs_on_prs_that_change_how_the_bot_sells_or_how_agents_work() -> None:
    paths = _workflow()["on"]["pull_request"]["paths"]

    for expected in (
        "hubara_agency/src/plugins/chats/shared/decisions/bundles/**",
        "hubara_agency/src/plugins/chats/agent/sales/workspace/**",
        "hubara_agency/src/plugins/chats/agent/sales/prompts.py",
        "CLAUDE.md",
        ".claude/skills/**",
    ):
        assert expected in paths, f"falta {expected} en pull_request.paths"


def test_it_runs_every_sunday_and_by_hand_with_the_same_inputs() -> None:
    on = _workflow()["on"]

    [cron] = [entry["cron"] for entry in on["schedule"]]
    assert cron.split()[4] in ("0", "SUN", "sun"), f"el cron {cron!r} no es de domingo"
    assert set(on["workflow_dispatch"]["inputs"]) >= {"scenario", "category", "limit", "repeat", "no_judge", "bundle"}


def test_inputs_never_go_straight_into_a_script() -> None:
    """En un PR o en el cron las entradas llegan vacías; a mano llegan como
    texto: siempre por variables de entorno, nunca interpoladas en `run:`."""
    for step in _steps(_workflow()):
        assert "${{ inputs." not in str(step.get("run") or ""), step.get("name")


def test_a_new_run_of_the_same_pr_cancels_the_previous_one() -> None:
    concurrency = _workflow()["concurrency"]

    assert concurrency["cancel-in-progress"] is True
    assert "github.head_ref" in concurrency["group"] or "pull_request.number" in concurrency["group"]


def test_without_model_keys_it_is_skipped_instead_of_failing() -> None:
    data = _workflow()
    jobs = data["jobs"]
    [checker] = [name for name, job in jobs.items() if "ok" in (job.get("outputs") or {})]
    golden = jobs["golden-eval"]

    assert checker in ([golden["needs"]] if isinstance(golden["needs"], str) else golden["needs"])
    assert f"needs.{checker}.outputs.ok == 'true'" in golden["if"]
    check_step = str(jobs[checker]["steps"])
    assert "secrets.DEEPSEEK_API_KEY" in check_step and "secrets.GEMINI_API_KEY" in check_step


def test_prs_get_one_summary_comment_that_is_updated_not_duplicated() -> None:
    data = _workflow()
    golden = data["jobs"]["golden-eval"]
    [step] = [s for s in golden["steps"] if "github.event_name == 'pull_request'" in str(s.get("if") or "")]
    script = str(step["run"])

    assert golden["permissions"]["pull-requests"] == "write"
    marker = re.search(r"<!-- [a-z-]+ -->", script)
    assert marker, "el comentario no lleva un marcador"
    assert "PATCH" in script and "POST" in script, "busca el comentario con el marcador y lo actualiza"
    assert "always()" in str(step["if"]), "el comentario sale aunque la corrida tenga escenarios rojos"


def test_prs_and_the_cron_evaluate_the_bundle_that_matters() -> None:
    """A mano: la entrada `bundle`. En el cron: el que corre la tienda
    (Terraform). En un PR que toca un solo paquete: ese paquete."""
    text = _GOLDEN.read_text(encoding="utf-8")

    assert re.search(r"SALES_DECISIONS_BUNDLE:\s*\$\{\{\s*steps\.bundle\.outputs\.id\s*\}\}", text)
    assert re.search(r"INPUT_BUNDLE:\s*\$\{\{\s*inputs\.bundle\s*\}\}", text)
    assert "tenants.auto.tfvars" in text and "decisions_bundle" in text
