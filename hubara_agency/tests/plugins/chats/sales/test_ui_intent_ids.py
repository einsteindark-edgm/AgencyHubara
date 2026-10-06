"""Cada intent encolado lleva su `id` (incidente 2026-10-06).

El flush reconoce un intent ya entregado por su id: sin id no puede saber que
la foto que volvió a la cola (por una escritura vieja) ya salió. Los docstrings
decían que había id; `_append_intent` nunca lo ponía.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales.tools.ui_intents import _append_intent

SESSION = "wa_573001234567"


def _pending(vault: Path) -> list[dict]:
    data = json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))
    return data["pending_ui_intents"]


def test_every_queued_intent_gets_its_own_id(_isolate_vault_dir: Path) -> None:
    card = {"kind": "product_detail", "params": {"handle": "cubo-love"}, "analytics": {}}

    _append_intent(SESSION, dict(card))
    _append_intent(SESSION, dict(card))

    ids = [intent.get("id") for intent in _pending(_isolate_vault_dir)]
    assert all(isinstance(i, str) and i for i in ids), ids
    assert len(set(ids)) == 2, "dos tarjetas iguales encoladas son dos intents distintos"


def test_an_id_given_by_the_caller_is_kept(_isolate_vault_dir: Path) -> None:
    _append_intent(SESSION, {"id": "payinstr-order_1", "kind": "payment_instructions", "params": {}})

    [intent] = _pending(_isolate_vault_dir)
    assert intent["id"] == "payinstr-order_1"
    assert isinstance(intent["queued_at_ms"], int)


def test_queueing_keeps_what_is_already_in_the_session(_isolate_vault_dir: Path) -> None:
    path = _isolate_vault_dir / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active_route": "humano", "pending_ui_intents": [{"id": "a", "kind": "x"}]}), encoding="utf-8")

    _append_intent(SESSION, {"kind": "product_detail", "params": {}})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active_route"] == "humano"
    assert [i["id"] for i in data["pending_ui_intents"]][0] == "a"
    assert len(data["pending_ui_intents"]) == 2
