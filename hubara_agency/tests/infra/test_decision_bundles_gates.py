"""Los paquetes de decisión no llegan rotos a producción (premortem 2026-10-02).

Un paquete que no compila no tumba el arranque: el dominio de la tienda sí se
lee al importar las tools, pero las 29 capacidades y el turno compilan en la
primera decisión. Entonces cada mensaje del cliente fallaba en el ingest. Y un
paquete que SSM nombra y la imagen no trae tumbaba la API y el worker de
ventas al importar: el webhook de WhatsApp en 502. Dos compuertas:

* **CI certifica todos los paquetes** (`decisions check`) y corre sus pruebas
  (la paridad, el turno, el centinela, el dominio): ningún paquete roto llega
  a `main`.
* **El deploy certifica el paquete que nombra SSM DENTRO de la imagen nueva**,
  después del `pull` y antes del `up -d`: si no está o no compila, aborta y los
  containers viejos siguen sirviendo (el rollback de imagen con una promoción
  más nueva en SSM es justo ese caso).

Se leen los workflows como texto (mismo criterio que test_lab_box_iam.py).
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO / ".github" / "workflows"
#: Las suites que exigen que los paquetes compilan y deciden como se diseñó.
_SUITES = (
    "tests/platform/decisions",
    "tests/plugins/chats/sales/decisions",
    "tests/plugins/order_sentinel/test_sentinel_bundle.py",
    "tests/sdk/test_decisions_cli.py",
    "tests/plugins/chats/test_store_domain.py",
)


def test_ci_certifies_every_bundle_and_runs_their_suites() -> None:
    gates = (_WORKFLOWS / "architecture-gates.yml").read_text(encoding="utf-8")

    assert re.search(r"run:\s*uv run python -m src\.sdk\.cli decisions check\s*$", gates, re.M), (
        "architecture-gates.yml no certifica los paquetes de decisión"
    )
    missing = [suite for suite in _SUITES if suite not in gates]
    assert missing == [], f"architecture-gates.yml no corre {missing}"


def test_the_deploy_certifies_the_configured_bundle_in_the_new_image_before_switching() -> None:
    deploy = (_WORKFLOWS / "backend-deploy.yml").read_text(encoding="utf-8")

    pull = deploy.index("docker compose pull")
    check = deploy.find("decisions check")
    first_up = deploy.index("docker compose up -d")

    assert check != -1, "backend-deploy.yml no certifica el paquete de decisión configurado"
    assert pull < check < first_up, "el paquete se certifica después del pull y ANTES de cambiar los containers"
    line = deploy[deploy.rfind("\n", 0, check):deploy.find("\n", check)]
    assert "$HUBARA_IMAGE" in line and "SALES_DECISIONS_BUNDLE" in deploy[pull:check], (
        "se certifica el paquete que nombra SSM (.env) dentro de la imagen NUEVA"
    )


def test_the_golden_eval_can_run_the_bundle_the_store_runs_and_says_which() -> None:
    """La evaluación del agente real corría siempre el paquete por defecto del
    código (`ventas`) aunque la tienda corra otro (`ventas-2` desde Terraform):
    el reporte no decía cuál se evaluó."""
    golden = (_WORKFLOWS / "golden-eval.yml").read_text(encoding="utf-8")

    assert re.search(r"^\s{6}bundle:\s*$", golden, re.M), "golden-eval.yml no tiene la entrada `bundle`"
    assert re.search(r"SALES_DECISIONS_BUNDLE:\s*\$\{\{\s*inputs\.bundle\s*\}\}", golden), (
        "la entrada `bundle` no llega como SALES_DECISIONS_BUNDLE"
    )
    assert "GITHUB_STEP_SUMMARY" in golden and "Paquete de decisión" in golden, "el reporte no dice qué paquete corrió"
