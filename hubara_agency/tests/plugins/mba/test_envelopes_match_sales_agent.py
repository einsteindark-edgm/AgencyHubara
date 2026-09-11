"""Guard de deriva: los envelopes de catálogo que ve MBA son los mismos que ve
el agente Sales de Hubara (misma verdad, dos frentes). Si alguien cambia uno,
este test obliga a cambiar el otro o a promover los helpers al SDK."""

from __future__ import annotations

from src.plugins.chats.agent.sales.tools import catalog as sales
from src.plugins.mba.tools import catalog as mba
from tests.plugins.mba.test_tools_catalog import _VELA, _ZODIAC


def test_summary_and_full_envelopes_are_identical() -> None:
    for p in (_VELA, _ZODIAC):
        assert mba._product_summary(p) == sales._product_summary(p)
        assert mba._product_full(p) == sales._product_full(p)
