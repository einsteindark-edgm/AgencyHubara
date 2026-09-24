"""Sonda del test de fugas (plan §3.5 candado 4, PR 11). Corre en un proceso
aparte, como un caso en la caja: entorno del sandbox ANTES de importar la
app, un turno real del banco con el workflow de producción sobre el servidor
de pruebas de Temporal y un LLM falso, y hooks de auditoría de Python que
anotan cada conexión y cada escritura. Escribe un reporte JSON.

Uso: python sandbox_turn_probe.py <case.json> <bench_dir> <sandbox_dir> <report.json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

CASE, BENCH, SANDBOX, REPORT = (Path(p) for p in sys.argv[1:5])

from src.plugins.chats.agent.sales_lab.sandbox.env import prepare_case_env  # noqa: E402

prepare_case_env(os.environ, SANDBOX)

from exoclaw_temporal.config import LLMChatInput, LLMResponseData, ToolCallData  # noqa: E402

_connects: list[str] = []
_lookups: list[str] = []
_writes: list[str] = []
_tools_called: list[str] = []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND


def _hook(event: str, args: tuple) -> None:
    if event == "socket.connect":
        address = args[1]
        _connects.append(str(address[0]) if isinstance(address, tuple) else str(address))
    elif event == "socket.getaddrinfo":
        _lookups.append(str(args[0]))
    elif event == "open":
        path, mode, flags = args[0], args[1], args[2]
        if isinstance(path, (str, bytes, os.PathLike)):
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & _WRITE_FLAGS
            )
            if writing:
                _writes.append(os.path.abspath(os.fsdecode(path)))
    elif event in ("os.rename", "os.replace"):
        _writes.append(os.path.abspath(os.fsdecode(args[1])))
    elif event in ("os.mkdir", "os.remove", "os.unlink", "os.rmdir"):
        if isinstance(args[0], (str, bytes, os.PathLike)):
            _writes.append(os.path.abspath(os.fsdecode(args[0])))


async def main() -> dict:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment

    from src.plugins.chats.agent.sales_lab.sandbox.turn import run_case

    calls = {"n": 0}
    all_tools = os.environ.get("PROBE_ALL_TOOLS") == "1"

    @activity.defn(name="llm_chat")
    async def fake_llm(input: LLMChatInput) -> LLMResponseData:
        calls["n"] += 1
        if calls["n"] == 1 and all_tools:
            # TODAS las tools que el turno le ofrece al LLM, con argumentos
            # vacíos: cada una corre su código hasta donde llegue.
            names = [(d.get("function") or {}).get("name") or d.get("name") for d in input.tool_definitions()]
            names = [n for n in names if isinstance(n, str) and n]
            _tools_called.extend(names)
            return LLMResponseData(
                content="",
                finish_reason="tool_calls",
                has_tool_calls=True,
                tool_calls=[ToolCallData(id=f"t{i}", name=n, arguments={}) for i, n in enumerate(names)],
            )
        if calls["n"] == 1:
            return LLMResponseData(
                content="",
                finish_reason="tool_calls",
                has_tool_calls=True,
                tool_calls=[ToolCallData(id="t1", name="send_reply", arguments={"text": "¡Claro! Te cuento del catálogo 😊"})],
            )
        return LLMResponseData(content="ok", finish_reason="stop", has_tool_calls=False, tool_calls=[])

    case = json.loads(CASE.read_text(encoding="utf-8"))
    async with await WorkflowEnvironment.start_time_skipping() as env:
        sys.addaudithook(_hook)  # el servidor de pruebas ya bajó y arrancó: de acá en adelante, el turno
        result = await run_case(case, bench_dir=BENCH, sandbox_dir=SANDBOX, client=env.client, llm_chat=fake_llm, timeout_s=120)
    result["llm_calls"] = calls["n"]
    return result


if __name__ == "__main__":
    report = {"result": asyncio.run(main())}
    report.update(connects=_connects, lookups=_lookups, writes=sorted(set(_writes)), tools_called=_tools_called)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")
