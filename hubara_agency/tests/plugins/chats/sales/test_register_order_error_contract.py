"""Guarda del contrato de rechazos de `register_order` (#353).

`registered=false` CON `error` = rechazo de validación: el bot corrige y
reintenta, y el workflow marca la tool como fallida (no sale un send_reply
del mismo paso ni corta un cierre). SIN `error` = el registro no pudo
hacerse (Medusa rechazó / config rota): el bot escala
ORDER_REGISTRATION_FAILED.

Los rechazos del cupo por unidad se agregaron en paralelo a #353 y quedaron
sin `error` — el bot habría escalado un `quota_changed` como falla de Medusa
(premortem 2026-09-24). Esta guarda lee el código: todo envelope con
`"registered": False` lleva `error`, salvo los dos que escalan a propósito.
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.plugins.chats.agent.sales.tools import order_registration

#: Envelopes SIN `error` a propósito (escalan): el de la falla del port
#: (trae `audit_id`, el pedido quedó guardado para reconciliar) y el cupón
#: con cupo sin candado configurado (config rota).
_ESCALATES = {"audit_id", "quota_unavailable"}


def _registered_false_dicts(source: str) -> list[ast.Dict]:
    found: list[ast.Dict] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant) and key.value == "registered"
                and isinstance(value, ast.Constant) and value.value is False
            ):
                found.append(node)
    return found


def _keys_and_details(node: ast.Dict) -> tuple[set[str], set[str]]:
    keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    details = {
        v.value
        for k, v in zip(node.keys, node.values)
        if isinstance(k, ast.Constant) and k.value == "error_detail"
        and isinstance(v, ast.Constant) and isinstance(v.value, str)
    }
    return keys, details


def test_every_register_order_rejection_says_whether_to_retry_or_escalate() -> None:
    source = Path(order_registration.__file__).read_text(encoding="utf-8")
    envelopes = _registered_false_dicts(source)
    assert len(envelopes) >= 8, "la guarda dejó de ver los rechazos"

    missing = []
    for node in envelopes:
        keys, details = _keys_and_details(node)
        if "error" in keys or (_ESCALATES & (keys | details)):
            continue
        missing.append(f"línea {node.lineno}: {sorted(details) or sorted(keys)}")

    assert not missing, (
        "rechazos de register_order sin `error` (el bot los escalaría como falla de Medusa): "
        + "; ".join(missing)
    )
