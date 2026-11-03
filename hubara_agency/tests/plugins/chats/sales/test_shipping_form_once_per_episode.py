"""ENV-01: the shipping form goes out once per episode.

Production gaps report (2026-11-03): in 7 conversations since 10-07 the bot V2
sent the shipping form (WhatsApp Flow) twice in the same episode. #393 already
removed the duplicates caused by races in `metadata.json`; the ones left were
the model calling `request_shipping_details` again turns later (the promise
safety net was not involved: it already counts a delivered form as kept).

`request_shipping_details` refuses a second form once one was DELIVERED in the
active episode: the customer has it above in the chat, and a datum that is
missing or that the customer corrects is asked by text. What it must NOT block
(gotcha 16): the first form, a resend after a send that FAILED, a form of an
earlier episode, a second call in the same turn (it is already queued) and the
operator, who decides from the app.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, ProductNotFoundError
from src.plugins.chats.agent.sales.tools.ui_intents import RequestShippingDetailsTool

SID = "wa_570000000101"  # synthetic
EPISODE_START = 1_790_000_000_000
ITEMS = [{"handle": "vela", "quantity": 2}]


class _Catalog:
    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        if handle != "vela":
            raise ProductNotFoundError(handle)
        return CatalogProductDTO(
            id="prod_vela", handle="vela", title="Vela", status="published",
            variants=[CatalogVariantDTO(
                id="variant_vela", title="Unico", prices=[CatalogPriceDTO(amount="20000", currency_code="cop")],
            )],
        )


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)


def _seed(vault: Path, *, delivered: list[dict[str, Any]] = (), **metadata: Any) -> Path:
    md = vault / SID / "metadata.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    episode = {
        "episode_id": "ep_001", "started_at_ms": EPISODE_START, "closed_at_ms": None,
        "order_draft": {"slots": {"producto": "Vela", "cantidad": "2"}, "updated_at_ms": EPISODE_START},
    }
    md.write_text(json.dumps({"active_route": "ventas", "episodes": [episode], **metadata}), encoding="utf-8")
    if delivered:
        (md.parent / "ui_intents_delivered.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in delivered), encoding="utf-8"
        )
    return md


def _form_row(*, ok: bool, at_ms: int) -> dict[str, Any]:
    return {"id": f"f-{at_ms}", "kind": "shipping_flow", "ok": ok, "wamid": "wamid.x" if ok else None, "at_ms": at_ms}


def _queue(md: Path) -> list[dict[str, Any]]:
    return json.loads(md.read_text(encoding="utf-8")).get("pending_ui_intents") or []


async def _ask(ctx: ToolContext, vault: Path, **kwargs: Any) -> dict[str, Any]:
    tool = RequestShippingDetailsTool(str(vault), catalog=_Catalog(), **kwargs)
    return json.loads(await tool.execute_with_context(ctx, items=ITEMS))


async def test_a_form_already_delivered_in_this_episode_is_not_sent_again(_isolate_vault_dir: Path, ctx) -> None:
    md = _seed(_isolate_vault_dir, delivered=[_form_row(ok=True, at_ms=EPISODE_START + 60_000)])

    result = await _ask(ctx, _isolate_vault_dir)

    assert result["queued"] is False
    assert result["error"] == "shipping_form_already_sent"
    # What to do instead: ask by text and write it down.
    assert "send_reply" in result["message"] and "set_order_slot" in result["message"]
    assert _queue(md) == []


async def test_the_first_form_of_the_episode_goes_out(_isolate_vault_dir: Path, ctx) -> None:
    md = _seed(_isolate_vault_dir)

    result = await _ask(ctx, _isolate_vault_dir)

    assert result["queued"] is True
    assert [i["kind"] for i in _queue(md)] == ["shipping_flow"]


async def test_a_form_whose_send_failed_can_go_out_again(_isolate_vault_dir: Path, ctx) -> None:
    md = _seed(_isolate_vault_dir, delivered=[_form_row(ok=False, at_ms=EPISODE_START + 60_000)])

    result = await _ask(ctx, _isolate_vault_dir)

    assert result["queued"] is True
    assert [i["kind"] for i in _queue(md)] == ["shipping_flow"]


async def test_a_form_of_an_earlier_episode_does_not_block_this_one(_isolate_vault_dir: Path, ctx) -> None:
    md = _seed(_isolate_vault_dir, delivered=[_form_row(ok=True, at_ms=EPISODE_START - 86_400_000)])

    result = await _ask(ctx, _isolate_vault_dir)

    assert result["queued"] is True
    assert [i["kind"] for i in _queue(md)] == ["shipping_flow"]


async def test_a_second_call_in_the_same_turn_keeps_the_form_already_queued(_isolate_vault_dir: Path, ctx) -> None:
    md = _seed(_isolate_vault_dir)

    await _ask(ctx, _isolate_vault_dir)
    again = await _ask(ctx, _isolate_vault_dir)

    assert again["queued"] is True
    assert [i["kind"] for i in _queue(md)] == ["shipping_flow"]


async def test_the_operator_can_send_it_again_from_the_app(_isolate_vault_dir: Path, ctx) -> None:
    """The operator decides (the customer lost it, the order changed): the
    guard is the bot's."""
    md = _seed(_isolate_vault_dir, delivered=[_form_row(ok=True, at_ms=EPISODE_START + 60_000)])

    result = await _ask(ctx, _isolate_vault_dir, once_per_episode=False)

    assert result["queued"] is True
    assert [i["kind"] for i in _queue(md)] == ["shipping_flow"]
