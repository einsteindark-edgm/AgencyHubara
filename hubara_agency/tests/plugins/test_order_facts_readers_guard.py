"""Guarda: los lectores migrados a OrderFacts no vuelven a leer copias del vault.

Pedido #31 (2026-09-17): Ads y Campañas mostraban un total distinto al de
Orders porque sumaban `order_total_cop` / `total_cop` congelados en el chat.
Regla: en estos plugins, toda función que toque esas copias tiene que recibir
`order_facts` (la copia solo es respaldo si Medusa no responde). Un lector
nuevo que sume la copia directo rompe este test.

Los otros lectores de copias (CAPI, scoring, remarketing, funnel…) están
inventariados en `docs/_sdk/07-connectorkit.md` §OrderFacts y se migran uno a
uno; al migrarlos, se suman acá.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_MIGRATED = [
    _SRC / "plugins" / "ads",
    _SRC / "plugins" / "marketing",
]
_COPY_KEYS = {"order_total_cop", "total_cop"}

# (archivo relativo a src/, función) que leen la copia a propósito:
_ALLOWED = {
    # Alimenta el respaldo de `_episode_revenue_cop` (que sí usa order_facts).
    ("plugins/ads/aggregation.py", "_session_order_totals"),
    # Lee `total_cop` del OrderSummaryDTO VIVO (Medusa), no del vault.
    ("plugins/ads/sales_join.py", "manual_sales_from_orders"),
    # Genera datos sintéticos de demo (escribe la copia, no la agrega).
    ("plugins/ads/synthetic_seed.py", "*"),
}


def _functions_reading_copies(path: Path) -> list[tuple[str, bool]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads_copy = any(
            isinstance(n, ast.Constant) and n.value in _COPY_KEYS for n in ast.walk(node)
        )
        if not reads_copy:
            continue
        uses_facts = any(
            (isinstance(n, ast.Name) and n.id == "order_facts")
            or (isinstance(n, ast.arg) and n.arg == "order_facts")
            for n in ast.walk(node)
        )
        found.append((node.name, uses_facts))
    return found


def _cases() -> list[tuple[str, str]]:
    cases = []
    for root in _MIGRATED:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(_SRC).as_posix()
            if (rel, "*") in _ALLOWED:
                continue
            for name, uses_facts in _functions_reading_copies(path):
                if not uses_facts and (rel, name) not in _ALLOWED:
                    cases.append((rel, name))
    return cases


def test_guard_detects_a_reader_that_ignores_order_facts(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def revenue(ep):\n    return ep.get('order_total_cop')\n")
    good = tmp_path / "good.py"
    good.write_text(
        "def revenue(ep, order_facts):\n"
        "    return order_facts.revenue_cop(ep['order_id'], frozen_total=ep.get('order_total_cop'))\n"
    )
    assert _functions_reading_copies(bad) == [("revenue", False)]
    assert _functions_reading_copies(good) == [("revenue", True)]


@pytest.mark.parametrize("offender", _cases() or [None])
def test_migrated_plugins_read_order_values_from_order_facts(offender) -> None:
    assert offender is None, (
        f"{offender[0]}::{offender[1]} lee el total congelado del vault sin "
        "`order_facts`. El valor de un pedido sale de OrderFacts "
        "(src.sdk.connectorkit.get_order_facts_port) — ver pedido #31."
    )
