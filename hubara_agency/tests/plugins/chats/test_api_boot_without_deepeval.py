"""La API arranca sin cargar deepeval (memoria de la caja de app, 2026-10-14).

Medido en prod: con #383 el arranque de la API pasó de 294 a 331 MB.
`chats/api/scorecards.py` lee el interruptor del juez (`llm_judge_enabled`) de
`sales_eval/evals/judge.py`, y ese módulo importa deepeval a nivel módulo. La
imagen de prod instala todos los extras, así que la API cargaba deepeval
(~1.500 módulos) sin usarlo: el juez corre en el worker sales_eval.

Corre en SUBPROCESS para medir un import limpio. CI no instala el extra
`evals`, así que el probe pone un `deepeval` falso adelante en el PYTHONPATH:
si algo de la API lo importa, se ve igual con o sin el extra.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[3]

_PROBE = "import sys\nimport src.main\nprint('DEEPEVAL_LOADED=' + str('deepeval' in sys.modules))\n"


def test_api_boot_does_not_import_deepeval(tmp_path: Path) -> None:
    fake = tmp_path / "deepeval"
    fake.mkdir()
    (fake / "__init__.py").write_text("")
    (fake / "models.py").write_text("class DeepEvalBaseLLM:\n    pass\n")
    pythonpath = os.pathsep.join(p for p in (str(tmp_path), os.environ.get("PYTHONPATH")) if p)
    env = {**os.environ, "OTEL_SDK_DISABLED": "true", "PYTHONPATH": pythonpath}

    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=HUB_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert proc.returncode == 0, f"importar la API falló:\n{proc.stderr[-2000:]}"
    loaded = [ln for ln in proc.stdout.splitlines() if ln.startswith("DEEPEVAL_LOADED=")]
    assert loaded == ["DEEPEVAL_LOADED=False"], (
        "la API cargó deepeval al arrancar: algún módulo que monta `src.main` lo "
        "importa a nivel módulo (el juez es del worker sales_eval)"
    )
