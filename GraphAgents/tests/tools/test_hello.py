"""Per-tool TCK de `hello` (la plantilla): contrato C2 + impl pura golden."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.hello.impl import run

GA = Path(__file__).resolve().parents[2]


def test_hello_contrato_certifica_C2() -> None:
    c = load_tool(GA / "tools" / "hello" / "tool.yaml")
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_hello_golden() -> None:
    assert run(name="mundo") == {"greeting": "hola, mundo"}
