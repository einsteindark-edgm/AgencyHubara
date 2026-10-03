"""Auditoría del turno (diseño v2 §04, fase F6). PURO.

Después de responder, las tools que pedía el contrato del motor (grabadas en
el paso `perception` de la traza, `tools.required`) contra las que el turno
usó (pasos `tool`). Queda en la traza (`contract`) y es la métrica
«cumplimiento del contrato» del laboratorio. No bloquea nada: la segunda
puerta del turno es la que actúa.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def contract_compliance(steps: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """`{required, missing, ok}` o None si el turno no traía contrato."""
    perception = next((s for s in steps if isinstance(s, dict) and s.get("kind") == "perception"), None)
    tools = (perception or {}).get("tools")
    required = tools.get("required") if isinstance(tools, dict) else None
    if not isinstance(required, list) or not required:
        return None
    used = {str(s.get("name")) for s in steps if isinstance(s, dict) and s.get("kind") == "tool" and s.get("name")}
    rows = [r for r in required if isinstance(r, dict) and r.get("topic")]
    missing = [
        {"topic": r["topic"], "any_of": list(r.get("any_of") or [])}
        for r in rows
        if not set(r.get("any_of") or []) & used
    ]
    return {"required": [r["topic"] for r in rows], "missing": missing, "ok": not missing}


__all__ = ["contract_compliance"]
