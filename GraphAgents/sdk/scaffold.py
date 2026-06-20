"""Scaffolding determinista del catálogo — el generador del CLI (`create`).

Hace nacer cada unidad IDÉNTICA en forma (contrato + impl pura + adapters + test) y
con su golden ROJO por construcción (la impl stub levanta NotImplementedError), para
que la ley TDD y la regla de oro queden forzadas por el generador, no por la memoria
del que programa. Sin time/random (L-1): mismo id → mismos bytes (reproducible).

Una sola fuente de plantillas (abajo); el CLI (`sdk/cli.py`) delega acá.
"""
from __future__ import annotations

import re
from pathlib import Path

_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
_SIDE_EFFECTS = ("pure", "read", "outward")


class ScaffoldError(Exception):
    """El scaffold se rehúsa (id inválido, side_effect inválido, ya existe)."""


def _render(template: str, **subs: str) -> str:
    out = template
    for key, value in subs.items():
        out = out.replace(f"__{key}__", value)
    return out


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def create_tool(
    tool_id: str,
    ga_root: Path | str,
    *,
    description: str = "",
    side_effect: str = "pure",
) -> list[Path]:
    """Scaffoldea `tools/<mod>/` (+ su test) desde el patrón de tool agnóstica.

    `tool_id` es kebab (`meta-ads-insights`); el directorio/módulo es snake
    (`meta_ads_insights`) y el id del contrato queda kebab — como `recommend-budget`.
    Devuelve las rutas escritas. Se rehúsa a sobrescribir un id existente.
    """
    if not _SLUG.match(tool_id):
        raise ScaffoldError(f"id '{tool_id}' debe ser kebab-case [a-z][a-z0-9-]*")
    if side_effect not in _SIDE_EFFECTS:
        raise ScaffoldError(f"side_effect '{side_effect}' inválido; usá {_SIDE_EFFECTS}")

    root = Path(ga_root)
    mod = tool_id.replace("-", "_")
    tdir = root / "tools" / mod
    if tdir.exists():
        raise ScaffoldError(f"la tool '{tool_id}' ya existe en {tdir} — no sobrescribo")

    approval = "true" if side_effect == "outward" else "false"
    desc = description or f"TODO: describí qué hace {tool_id}"
    subs = {"TOOL_ID": tool_id, "MOD": mod, "DESC": desc, "SE": side_effect, "APPROVAL": approval}

    written = [
        _write(tdir / "__init__.py", ""),
        _write(tdir / "tool.yaml", _render(_TOOL_YAML, **subs)),
        _write(tdir / "impl.py", _render(_IMPL_PY, **subs)),
        _write(tdir / "adapters" / "__init__.py", ""),
        _write(tdir / "adapters" / "langgraph.py", _render(_ADAPTER_LG, **subs)),
        _write(tdir / "adapters" / "agentspan.py", _render(_ADAPTER_AS, **subs)),
        _write(root / "tests" / "tools" / f"test_{mod}.py", _render(_TEST_PY, **subs)),
    ]
    return written


_TOOL_YAML = """# __DESC__
# Tool de catálogo generada por `sdk.cli create tool`. Completá inputs/outputs + la
# impl, y poné VERDE el golden de tests/tools/test___MOD__.py (nace rojo por construcción).
id: __TOOL_ID__
version: 1.0.0
description: "__DESC__"
tags: [generated]

side_effect: __SE__
idempotent: true
approval_required: __APPROVAL__
credentials: []

inputs:
  payload:
    type: object
    required: true
    description: "TODO: definí los inputs reales de la tool"
outputs:
  result:
    type: object
    description: "TODO: definí los outputs reales de la tool"

# impl PURA (G-AGNOSTIC: no importa runtime). Adapters en tools/__MOD__/adapters/.
impl: tools.__MOD__.impl:run
"""

_IMPL_PY = '''"""Lógica PURA de `__TOOL_ID__` — G-AGNOSTIC: NO importa ningún runtime
(langgraph/agentspan/langchain). Determinista e idempotente.

Generada por `create tool`. Implementá `run`: mientras levante NotImplementedError,
el golden de tests/tools/test___MOD__.py queda ROJO (la presión de diseño de TDD).
"""
from __future__ import annotations


def run(*, payload: dict) -> dict:
    """TODO: implementá la lógica pura (input -> output exacto, sin IO ni runtime)."""
    raise NotImplementedError("implementá run() de __TOOL_ID__")
'''

_ADAPTER_LG = '''"""Adapter LangGraph de `__TOOL_ID__`: nodo state->patch. Un nodo es solo un
callable; la impl pura no se toca."""
from __future__ import annotations

from tools.__MOD__.impl import run


def node(state: dict) -> dict:
    return run(payload=state["payload"])
'''

_ADAPTER_AS = '''"""Adapter AgentSpan de `__TOOL_ID__`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.__MOD__.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def __MOD__(payload: dict) -> dict:
        """__DESC__"""
        return run(payload=payload)

    return __MOD__
'''

_TEST_PY = '''"""Per-tool TCK de `__TOOL_ID__` (generado). El contrato certifica C2; el golden
nace ROJO: completá EXPECTED + implementá la impl para ponerlo verde."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.__MOD__.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "__MOD__" / "tool.yaml"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_impl_golden() -> None:
    # TODO: reemplazá payload/EXPECTED por el golden real. Nace ROJO (run levanta
    # NotImplementedError) — TDD por construcción.
    out = run(payload={})
    assert out == {"result": "TODO"}
'''
