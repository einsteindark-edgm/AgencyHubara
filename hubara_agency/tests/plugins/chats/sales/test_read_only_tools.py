"""Las tools que solo leen: la lista del workflow y las clases dicen lo mismo.

El reinicio del turno conserva el resultado de estas tools (no las vuelve a
llamar) y la nota del contrato las trata como «datos» (turno 1 de …7392,
2026-10-08). Si una de ellas empieza a escribir algo, o una tool nueva solo
lee, la declaración vive en la clase (`read_only = True`) y en la lista.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

from exoclaw.agent.tools import ToolBase

import src.plugins.chats.agent.sales.tools as sales_tools
from src.plugins.chats.agent.sales.read_only_tools import READ_ONLY_TOOLS


def _tool_classes() -> list[type]:
    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(sales_tools.__path__):
        module = importlib.import_module(f"{sales_tools.__name__}.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, ToolBase) and cls is not ToolBase and isinstance(getattr(cls, "name", None), str):
                found[f"{cls.__module__}.{cls.__qualname__}"] = cls
    return list(found.values())


def test_the_read_only_list_matches_what_each_tool_declares() -> None:
    declared = {cls.name for cls in _tool_classes() if getattr(cls, "read_only", False) is True}

    assert declared == READ_ONLY_TOOLS


def test_no_tool_that_shows_or_changes_something_is_read_only() -> None:
    for name in READ_ONLY_TOOLS:
        assert not name.startswith(("present_", "send_", "request_")), name
    assert not READ_ONLY_TOOLS & {"set_order_slot", "register_order", "apply_coupon", "manage_conversation_tag"}
