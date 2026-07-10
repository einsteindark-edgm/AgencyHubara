"""Gate build↔consumes (regla de oro, análogo L-12: ningún campo sin su check).

`build_agent` (sdk/loader.py) inyecta los ports consumidos como KWARGS al
`build()` de una capability DIRECTA: `_resolve_capability(...)(**bound)`. Sin
un check estructural, una capability puede declarar `consumes: [x]` con un
`build()` que no acepta el kwarg — carga limpio, certifica, y revienta con
TypeError recién cuando `durable_vendors()` gana el vendor de ese port (drift
latente detectado por el cert-reviewer en meta-insights).

El check exige: si el manifest declara `consumes:` y el módulo expone `build`,
la firma acepta un kwarg POR CADA port consumido (o **kwargs). Incluye el caso
NEGATIVO (el gate que nunca falla es un gate roto).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from sdk.manifest_model import AgentNode, load_manifest
from sdk.testkit.checks import check_capability_build_accepts_consumed_ports

GA = Path(__file__).resolve().parents[2]
MANIFESTS = sorted((GA / "manifests").glob("*.yaml"))


def _fake_capability(name: str, build_fn) -> AgentNode:
    """Un módulo sintético en sys.modules + el AgentNode que lo referencia."""
    mod = types.ModuleType(name)
    mod.run = lambda input, *, ports=None, tools=None: {}
    if build_fn is not None:
        mod.build = build_fn
    sys.modules[name] = mod
    return AgentNode(
        name=name.replace("_", "-"),
        archetype="analyzer",
        capability=f"{name}:build",
        consumes=["llm"],
    )


# --- caso NEGATIVO primero: el gate CAZA el drift -----------------------------


def test_detector_caza_build_sin_kwarg_del_port_consumido() -> None:
    """`consumes: [llm]` + `def build():` → error (el loader llamaría build(llm=...))."""
    node = _fake_capability("fake_cap_build_sin_kwargs", lambda: None)
    errs = check_capability_build_accepts_consumed_ports(node)
    assert len(errs) == 1
    assert "llm" in errs[0]


def test_detector_acepta_kwarg_explicito_por_port() -> None:
    """`def build(*, llm=None):` — el contrato que siguen ctwa_report/order_sentinel."""
    node = _fake_capability(
        "fake_cap_build_kwarg", lambda *, llm=None: None
    )
    assert check_capability_build_accepts_consumed_ports(node) == []


def test_detector_acepta_var_keyword() -> None:
    """`def build(**kwargs):` absorbe cualquier port futuro."""
    node = _fake_capability(
        "fake_cap_build_varkw", lambda **kwargs: None
    )
    assert check_capability_build_accepts_consumed_ports(node) == []


def test_detector_ignora_capability_sin_build_o_sin_consumes() -> None:
    """`build` es opcional; y sin `consumes:` no hay nada que inyectar."""
    sin_build = _fake_capability("fake_cap_sin_build", None)
    assert check_capability_build_accepts_consumed_ports(sin_build) == []

    sin_consumes = _fake_capability("fake_cap_sin_consumes", lambda: None)
    sin_consumes = sin_consumes.model_copy(update={"consumes": []})
    assert check_capability_build_accepts_consumed_ports(sin_consumes) == []


# --- el árbol real: cero ofensores --------------------------------------------


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_ningun_manifest_real_declara_port_que_su_build_no_recibe(path: Path) -> None:
    assert check_capability_build_accepts_consumed_ports(load_manifest(path)) == []
