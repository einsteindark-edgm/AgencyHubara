"""Un caso en su proceso (PR 11): el runner de la caja lanza el entrypoint,
lee el resultado y borra el sandbox del caso (se crea, se usa y se borra)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales_lab.sandbox.process import run_case_in_subprocess


@pytest.mark.asyncio
async def test_a_refused_case_reports_why_and_leaves_no_sandbox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LAB_ROOT", str(tmp_path / "lab"))
    monkeypatch.setenv("TEMPORAL_URL", "temporal:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "hubara-lab")
    monkeypatch.setenv("HUBARA_ENV", "lab")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "EAAG-real")  # el guard del caso lo frena
    sandbox = tmp_path / "lab" / "runs" / "run-x" / "smoke" / "c1"

    result = await run_case_in_subprocess({"case_id": "wa_x/ep_001/t1"}, bench_dir=tmp_path / "bench",
                                          sandbox_dir=sandbox, timeout_s=60)

    assert result["case_id"] == "wa_x/ep_001/t1"
    assert "salió con 2" in result["error"] and "WHATSAPP_ACCESS_TOKEN" in result["error"]
    assert not sandbox.exists()
