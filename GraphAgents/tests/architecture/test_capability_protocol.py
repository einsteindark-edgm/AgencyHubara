"""El PROTOCOLO `Capability` del kit — el contrato formal de un nodo determinista de UN
agente: `run(input, *, ports, tools)` PURA (las tools SIEMPRE por el mapping inyectado, el
seam que las hace trazables) + `build(*, checkpointer)` OPCIONAL. `assert_capability` es la
enforcement estructural que consume la cert: amarra que todas se implementen igual.
"""
from __future__ import annotations

import types
from pathlib import Path

import graphs.ctwa_campaign_funnel as funnel
import graphs.greeter as greeter
from sdk.capability import Capability, assert_capability

ROOT = Path(__file__).resolve().parents[2]


def test_real_capabilities_satisfy_the_protocol():
    assert assert_capability(funnel) == []   # tiene run(input,*,ports,tools) + build(*,checkpointer)
    assert assert_capability(greeter) == []


def test_capability_is_runtime_checkable_on_modules_with_run():
    # el Protocol es runtime_checkable → un módulo con `run` lo satisface estructuralmente
    assert isinstance(funnel, Capability)


def test_assert_capability_rejects_missing_run():
    bad = types.ModuleType("bad_norun")
    errs = assert_capability(bad)
    assert errs and "run" in errs[0].lower()


def test_assert_capability_rejects_run_without_tools_injection():
    bad = types.ModuleType("bad_sig")
    bad.run = lambda input: input  # sin (*, ports, tools) → el loader no podría inyectar/trazar
    errs = assert_capability(bad)
    assert any("ports" in e or "tools" in e for e in errs)


def test_assert_capability_rejects_build_with_wrong_signature():
    bad = types.ModuleType("bad_build")
    bad.run = lambda input, *, ports=None, tools=None: input
    bad.build = lambda x: x  # build debe ser (*, checkpointer), no posicional
    errs = assert_capability(bad)
    assert any("build" in e.lower() for e in errs)
