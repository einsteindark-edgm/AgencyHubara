"""Lo que el agente lee de la tienda en sus tools y en el gancho de
remarketing, en una forma comparable (PAQUETES_DE_DECISION.md F5).

`test_store_domain.py` lo compara contra la foto congelada de la tienda
actual: pasar los ejemplos de la tienda al vocabulario del paquete no puede
cambiar ni un carácter de lo que ve el LLM.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any

#: Los módulos cuyas tools llevan ejemplos de la tienda (las 21 reglas de forge).
TOOL_MODULES = (
    "src.plugins.chats.agent.sales.tools.catalog",
    "src.plugins.chats.agent.sales.tools.order_draft",
    "src.plugins.chats.agent.sales.tools.order_registration",
    "src.plugins.chats.agent.sales.tools.ui_intents",
)

#: Ganchos de remarketing representativos (con y sin escalera, catálogo, compra).
REMARKETING_CASES: dict[str, dict[str, Any]] = {
    "basico": {"motivo": "x", "has_order_draft": False},
    "escalera": {
        "motivo": "preguntó por el Cubo Love y no eligió", "has_order_draft": True,
        "transcript": "cliente: hola\ntienda: ¡Hola! ¿En qué te ayudo?", "touch_number": 2,
        "silence_minutes": 90, "catalog_facts": "Cubo Love: $45.000",
    },
    "post_compra": {"motivo": "x", "has_order_draft": False, "post_purchase": "closing"},
}


def agent_texts() -> dict[str, Any]:
    from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger

    tools: dict[str, Any] = {}
    for module_name in TOOL_MODULES:
        module = importlib.import_module(module_name)
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name or not isinstance(getattr(cls, "description", None), str):
                continue
            tools[f"{module_name.rsplit('.', 1)[1]}.{name}"] = {
                "name": getattr(cls, "name", None),
                "description": cls.description,
                "parameters": getattr(cls, "parameters", None),
            }
    from src.plugins.chats.agent.sales.tools import ui_intents

    remarketing = {
        case: build_remarketing_trigger(kw.pop("motivo"), **kw)
        for case, kw in ((c, dict(k)) for c, k in REMARKETING_CASES.items())
    }
    # Lo que las tools le devuelven al LLM (resultado de la tool).
    tool_results = {
        "no_more_photos": ui_intents.no_more_photos_message("Cubo Love"),
        "photos_sent_next": ui_intents.PHOTOS_SENT_NEXT,
        "picker_sent": ui_intents.picker_sent_summary("scent", 5),
    }
    return {"tools": tools, "remarketing": remarketing, "tool_results": tool_results}
