"""La forma de un brazo del laboratorio (PAQUETES_DE_DECISION.md F6).

Un brazo es un bot (`A1`, `B`) y, si fija un paquete de decisión distinto
al de la tienda, `@<paquete>`: `B@ventas-2` corre el bot B con la
inteligencia de `ventas-2`. Así se prueba un paquete nuevo contra el banco
antes de promoverlo. La plataforma solo sabe la forma; qué bots existen lo
decide chats.
"""
from __future__ import annotations

import importlib

import pytest

labkit = importlib.import_module("src.sdk.labkit")


def test_the_kit_exposes_the_arm_shape() -> None:
    assert {"split_arm", "arm_pattern", "arms_pattern"} <= set(dir(labkit))


def test_an_arm_is_a_bot_and_maybe_a_bundle() -> None:
    assert labkit.split_arm("B") == ("B", "")
    assert labkit.split_arm("B@ventas-2") == ("B", "ventas-2")


@pytest.mark.parametrize("arm", ["A1", "B", "B@ventas-2", "B0@vincenzo"])
def test_the_shape_accepts_known_bots_with_or_without_bundle(arm: str) -> None:
    assert labkit.arm_pattern(("A1", "B0", "B")).fullmatch(arm)


@pytest.mark.parametrize("arm", ["Z", "B@", "B@Ventas", "B@ventas/../x", "B@ventas@2", "@ventas", "B @ventas", "B@" + "x" * 41])
def test_the_shape_rejects_anything_else(arm: str) -> None:
    assert not labkit.arm_pattern(("A1", "B0", "B")).fullmatch(arm)


def test_a_list_of_arms() -> None:
    pattern = labkit.arms_pattern(("A0", "A1", "B0", "B", "C"), max_arms=5)
    assert pattern.fullmatch("A1,B,B@ventas-2")
    assert not pattern.fullmatch("A1,B,B@ventas-2,B0,C,A0")
    assert not pattern.fullmatch("A1,,B")
