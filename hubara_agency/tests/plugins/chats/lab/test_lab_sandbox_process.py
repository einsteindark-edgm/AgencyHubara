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


@pytest.mark.asyncio
async def test_the_case_process_gets_its_arm(tmp_path: Path, monkeypatch) -> None:
    """El brazo (A1, B o C) viaja al proceso del caso: de él sale el modo y el
    perfil de la señal (PR 15)."""
    from src.plugins.chats.agent.sales_lab.sandbox import process

    seen: list[tuple] = []

    class _Proc:
        returncode = 1

        async def communicate(self):
            return b"", b"no corre en el test"

    async def fake_exec(*args, **kwargs):
        seen.append(args)
        return _Proc()

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", fake_exec)
    await run_case_in_subprocess({"case_id": "c1"}, bench_dir=tmp_path / "bench",
                                 sandbox_dir=tmp_path / "lab" / "c1", timeout_s=5, arm="B")

    argv = list(seen[0])
    assert argv[argv.index("--arm") + 1] == "B"
