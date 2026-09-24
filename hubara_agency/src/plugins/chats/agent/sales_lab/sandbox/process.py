"""Un caso en su propio proceso (plan §3.3–§3.4, PR 11).

El worker de la corrida (`LabRunWorkflow`) nunca importa el bot de ventas:
cada caso corre en un proceso aparte (`sandbox/entrypoint.py`) con el
entorno de su sandbox. Este módulo lo lanza, espera, lee el resultado y
borra el sandbox del caso (se crea, se usa y se borra: lo único que queda es
el resultado, que la corrida sube a `runs/`).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

SANDBOX_MODULE = "src.plugins.chats.agent.sales_lab.sandbox.entrypoint"
_GRACE_S = 60.0


async def run_case_in_subprocess(
    case: dict[str, Any],
    *,
    bench_dir: Path,
    sandbox_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    sandbox_dir.parent.mkdir(parents=True, exist_ok=True)
    case_file = sandbox_dir.parent / f"{sandbox_dir.name}.case.json"
    out_file = sandbox_dir.parent / f"{sandbox_dir.name}.result.json"
    case_file.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    base = {"case_id": case.get("case_id"), "session_id": case.get("session_id"), "turn_key": case.get("turn_key")}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", SANDBOX_MODULE,
            "--case", str(case_file), "--bench", str(bench_dir), "--sandbox", str(sandbox_dir),
            "--out", str(out_file), "--timeout", str(timeout_s),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ),
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + _GRACE_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {**base, "error": f"el proceso del caso no terminó en {int(timeout_s + _GRACE_S)} s"}
        if out_file.is_file():
            return json.loads(out_file.read_text(encoding="utf-8"))
        tail = (stderr or b"").decode("utf-8", errors="replace").strip()[-800:]
        return {**base, "error": f"el proceso del caso salió con {proc.returncode}: {tail}"}
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        case_file.unlink(missing_ok=True)
        out_file.unlink(missing_ok=True)
