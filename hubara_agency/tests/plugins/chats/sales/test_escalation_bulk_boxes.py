"""`escalate_to_human` con BULK_ORDER no pasa a un colega unas velas sueltas
de lo que se cobra por caja (laboratorio, 2026-10-14).

«Me vendes 36 velitas?» y «quiero 30 velitas de las onduladas» se pasaron a
un colega como pedido al por mayor («>20 unidades»): las velas de deseos van
por caja (12 la cuadrada, 10 la ondulada) y eran 3 cajas. Si el cliente dio
una cifra que, contada en velas, cabe en 20 cajas o menos, decide la
capacidad `por_mayor` (ventas-7): «velas» no escala y el bot recibe las
cuentas; «duda» (sin Jev, así nace) lo devuelve UNA vez por mensaje del
cliente; «por_mayor» escala. Otra razón, una cifra que no cabe en cajas y
`ventas-6` escalan como siempre.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.tools.escalation import EscalateToHumanTool
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

#: Las cajas son de la tienda madre (`domain.yaml: sold_by_box`, las velas de
#: deseos): en un clon de forge (sin `forge/`) su dominio no las trae.
pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[5] / "forge").is_dir(),
    reason="clon de forge: las cajas (las velas de deseos) son de la tienda madre",
)

SID = "wa_bulk"
QUESTION = "por_mayor.velas_sueltas"
PRODUCT = "por_mayor.producto_por_caja"


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


def _jev(monkeypatch, p: float):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({QUESTION: TypedAnswer(id=QUESTION, kind="noul", p=p),
                                  PRODUCT: TypedAnswer(id=PRODUCT, kind="noul", p=0.9)})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


def _tool(tmp_path: Path, asked: str = "Hola! me vendes 36 velitas?"):
    vault = tmp_path / "vault"
    (vault / SID).mkdir(parents=True)
    (vault / SID / "metadata.json").write_text(
        json.dumps({"last_inbound_message_id": "wamid.A", "episodes": []}), encoding="utf-8"
    )
    tool_cls = guarded_escalation_tool(EscalateToHumanTool)
    return tool_cls(workspace=str(tmp_path), vault_dir=vault,
                    history_reader=lambda _key: [{"role": "user", "content": asked}])


async def _escalate(tool, reason: str = "BULK_ORDER") -> dict:
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id="c")
    return json.loads(await tool.execute_with_context(
        ctx, reason_category=reason, summary="Cliente pide 36 velitas",
        customer_message="Para esa cantidad te coordino con un colega del equipo 🤍",
    ))


def _route(tmp_path: Path) -> str | None:
    return json.loads((tmp_path / "vault" / SID / "metadata.json").read_text(encoding="utf-8")).get("active_route")


@pytest.mark.asyncio
async def test_with_the_rule_candles_that_fit_in_boxes_go_back_once_with_the_counts(tmp_path: Path) -> None:
    result = await _escalate(_tool(tmp_path))

    assert result["escalated"] is False and _route(tmp_path) is None
    assert "Velas de deseos cuadradas" in result["error"] and "36 velas = 3 cajas" in result["error"]
    assert "set_order_slot" in result["error"]


@pytest.mark.asyncio
async def test_the_second_try_in_the_same_customer_message_escalates(tmp_path: Path) -> None:
    tool = _tool(tmp_path)

    await _escalate(tool)
    result = await _escalate(tool)

    assert "escalation_decision" in result and _route(tmp_path) == "humano"


@pytest.mark.asyncio
async def test_jev_reads_loose_candles_and_it_does_not_escalate(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.93)

    result = await _escalate(_tool(tmp_path))

    assert result["escalated"] is False and "no es un pedido al por mayor" in result["error"]
    [state] = [s for s, qs in fake.calls if QUESTION in qs]
    assert "Hola! me vendes 36 velitas?" in state and "Cliente pide 36 velitas" in state


@pytest.mark.asyncio
async def test_jev_reads_a_real_bulk_order_and_it_escalates(tmp_path: Path, monkeypatch) -> None:
    _jev(monkeypatch, 0.04)

    result = await _escalate(_tool(tmp_path, "Necesito 25 cajas de velas de deseos cuadradas"))

    assert "escalation_decision" in result and _route(tmp_path) == "humano"


@pytest.mark.asyncio
async def test_a_count_that_does_not_fit_in_boxes_escalates_without_asking(tmp_path: Path, monkeypatch) -> None:
    fake = _jev(monkeypatch, 0.93)

    result = await _escalate(_tool(tmp_path, "Hola, necesito 300 velitas para un evento"))

    assert "escalation_decision" in result
    assert not any(QUESTION in qs for _s, qs in fake.calls)


@pytest.mark.asyncio
async def test_another_reason_escalates_as_always(tmp_path: Path) -> None:
    result = await _escalate(_tool(tmp_path), reason="DISCOUNT_REQUEST")

    assert "escalation_decision" in result


@pytest.mark.asyncio
async def test_with_ventas_6_nothing_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()

    result = await _escalate(_tool(tmp_path))

    assert "escalation_decision" in result


def test_the_sales_worker_gives_the_escalation_what_the_customer_wrote(tmp_path: Path, monkeypatch) -> None:
    """Gotcha #6: ejecutar la fábrica caza el nombre que falte. Sin el
    historial, `por_mayor` no tendría las cifras del cliente."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    tool = dict(_EXTENSIONS)["sales.escalate_to_human"](tmp_path)

    assert tool._history_reader is not None
