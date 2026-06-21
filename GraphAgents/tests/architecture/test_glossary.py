"""El GLOSARIO de convenciones del explorer — fuente única del vocabulario de certificación
(niveles C0–C3 + reglas G-*/T-*/CASE-*). Guard = la regla de oro aplicada al vocabulario:
**ningún código que emiten los checks del TestKit sin su glosa**. Si agregás un check con un
código nuevo y no lo glosás, este test te frena (el glosario del viewer no miente por omisión).
"""
from __future__ import annotations

import re
from pathlib import Path

from sdk.glossary import glossary
from viewer.server import api_route

GA = Path(__file__).resolve().parents[2]
TESTKIT = GA / "sdk" / "testkit"


def _emitted_codes() -> set[str]:
    """Los códigos `[X]` que emiten los checks del TestKit (la fuente de verdad de las reglas)."""
    codes: set[str] = set()
    for p in TESTKIT.glob("*.py"):
        codes |= set(re.findall(r"\[([A-Z][A-Z0-9-]{2,})\]", p.read_text(encoding="utf-8")))
    return codes


def _terms() -> set[str]:
    return {it["term"] for s in glossary()["sections"] for it in s["items"]}


def test_glossario_cubre_todo_codigo_emitido_por_los_checks() -> None:
    emitted = _emitted_codes()
    assert emitted, "no se encontró ningún código en sdk/testkit (¿cambió el formato?)"
    missing = emitted - _terms()
    assert not missing, f"códigos de check SIN glosa en el glosario: {sorted(missing)}"


def test_glossario_explica_los_niveles_de_cert() -> None:
    assert {"C0", "C1", "C2"} <= _terms()


def test_api_legend_sirve_el_glosario() -> None:
    status, payload = api_route("GET", "/api/legend", {}, None, ga_root=GA)
    assert status == 200
    assert payload["sections"]
    terms = {it["term"] for s in payload["sections"] for it in s["items"]}
    assert "G-RUN-SIG" in terms and "C2" in terms
