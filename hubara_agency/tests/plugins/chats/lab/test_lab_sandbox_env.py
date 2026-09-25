"""Entorno del proceso de UN caso (plan §3.5 candado 3, PR 11): todas las
carpetas que el código de producción escribe apuntan al sandbox del caso,
ANTES de importar la app (la composición guarda rutas con lru_cache), y la
telemetría queda apagada. El catálogo del banco nunca se ve "viejo"."""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.agent.sales_lab.guard import check_lab_env
from src.plugins.chats.agent.sales_lab.sandbox.env import prepare_case_env


def test_every_folder_points_inside_the_case_sandbox(tmp_path: Path) -> None:
    box = tmp_path / "lab" / "runs" / "run-x" / "A1" / "0" / "case-1"
    env: dict[str, str] = {"WORKSPACE_VAULT_DIR": "/app/hubara_vault", "OTEL_SDK_DISABLED": "false"}

    prepare_case_env(env, box)

    assert env["WORKSPACE_VAULT_DIR"] == str(box / "vault")
    assert env["EXOCLAW_STATE_DIR"] == str(box / "agent_state")
    assert env["CATALOG_SNAPSHOT_DIR"] == str(box / "catalog")
    assert env["MEDIA_STORE_DIR"] == str(box / "media")
    assert env["EVAL_CANDIDATES_DIR"].startswith(str(box)) and env["EVAL_HISTORY_DIR"].startswith(str(box))
    assert env["OTEL_SDK_DISABLED"] == "true"
    assert env["WHATSAPP_PHONE_NUMBER_ID"] == "lab-sandbox"
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"  # litellm no baja su tabla de precios de GitHub
    assert "WHATSAPP_ACCESS_TOKEN" not in env
    assert int(env["CATALOG_MAX_AGE_MINUTES"]) >= 60 * 24 * 365


def test_the_prepared_case_env_passes_the_lab_guard(tmp_path: Path) -> None:
    root = tmp_path / "lab"
    env = {"LAB_ROOT": str(root), "TEMPORAL_URL": "temporal:7233", "TEMPORAL_NAMESPACE": "hubara-lab", "HUBARA_ENV": "lab"}

    prepare_case_env(env, root / "runs" / "run-x" / "A1" / "0" / "case-1")

    assert check_lab_env(env) == []
