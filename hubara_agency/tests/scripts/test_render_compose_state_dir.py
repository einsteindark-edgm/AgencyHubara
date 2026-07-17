"""El compose local generado lleva EXOCLAW_STATE_DIR en TODOS los workers.

Paridad con prod (incidente 2026-07-17 run 019f6db3): el historial LLM debe
vivir en el volumen del vault, no en el filesystem del container. En prod la
env var entra por `render-env-from-ssm.sh`; en local la inyecta el generador
de compose — global, como ENABLED_PLUGINS, para que un worker nuevo jamás
nazca sin ella (amnesia silenciosa).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import yaml

_HUBARA_ROOT = Path(__file__).resolve().parents[2]


def _render() -> str:
    script_path = _HUBARA_ROOT / "scripts" / "render-compose.py"
    spec = importlib.util.spec_from_file_location("render_compose", script_path)
    assert spec and spec.loader, f"cannot load {script_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    saved = os.environ.pop("ENABLED_PLUGINS", None)
    try:
        return module.render()
    finally:
        if saved is not None:
            os.environ["ENABLED_PLUGINS"] = saved


def test_all_workers_carry_exoclaw_state_dir():
    services = yaml.safe_load(_render())["services"]
    workers = {
        name: spec
        for name, spec in services.items()
        if name.startswith("hubara-worker-")
    }
    assert workers, "el render no produjo workers"
    for name, spec in workers.items():
        env = spec.get("environment") or []
        assert any(
            e.startswith("EXOCLAW_STATE_DIR=/app/hubara_vault/") for e in env
        ), f"{name} sin EXOCLAW_STATE_DIR — historial LLM efímero"
