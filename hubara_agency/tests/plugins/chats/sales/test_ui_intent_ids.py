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


# --- segunda revisión del PR #393 ------------------------------------------------
# Con `metadata.json` ilegible la tarjeta no entra a la cola: la tool no puede
# decirle al LLM «enviada» (el bot le contaría al cliente algo que no salió).


async def test_a_card_that_could_not_be_queued_is_not_reported_as_sent(_isolate_vault_dir: Path) -> None:
    from exoclaw.agent.tools import ToolContext

    from src.plugins.chats.agent.sales.tools.ui_intents import ReactToMessageTool

    path = _isolate_vault_dir / SESSION / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"active_route": "ventas", "pending_ui_intents": [', encoding="utf-8")
    on_disk = path.read_bytes()
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)

    envelope = json.loads(
        await ReactToMessageTool(workspace=str(_isolate_vault_dir)).execute_with_context(ctx, emoji="🤍")
    )

    assert envelope["queued"] is False, envelope
    assert path.read_bytes() == on_disk


def test_every_tool_checks_that_its_card_was_queued() -> None:
    """Las 11 tools que encolan tarjetas miran si la tarjeta entró a la cola
    (`if not _append_intent(...)`) antes de decir que salió."""
    import ast

    from src.plugins.chats.agent.sales.tools import ui_intents

    tree = ast.parse(Path(ui_intents.__file__).read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_append_intent"
    ]

    def checked(call: ast.Call) -> bool:
        negation = parents.get(call)
        return (
            isinstance(negation, ast.UnaryOp)
            and isinstance(negation.op, ast.Not)
            and isinstance(parents.get(negation), ast.If)
        )

    unchecked = [call.lineno for call in calls if not checked(call)]
    assert len(calls) >= 11
    assert not unchecked, f"ui_intents.py: `_append_intent` sin mirar si encoló (líneas {unchecked})"
