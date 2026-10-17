"""El ingest partido en pasos (entrada durable, fase 2, paso 1).

`InboundMessageWorkflow` corre cada paso del ingest en su propia activity:
el estado de la sesión con sus lecturas, el carrito y el cupón, el historial y
la entrega. Lo que un paso le pasa al siguiente queda en el historial del
workflow, así que tiene que sobrevivir al serializador de Temporal. Un
reintento del último paso no repite los anteriores, porque Temporal ya tiene su
resultado.

Contrato: correr los pasos con sus resultados serializados y rehidratados entre
uno y otro deja en el historial y en la sesión exactamente lo mismo que
`execute`.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest
from temporalio.converter import DataConverter

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.plugins.chats.agent.sales.contracts import IngestRequest, IngestState, IngestTurn
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

_SID = "wa_573001234567"
_NOW_MS = 1_791_000_000_000
_DAY_MS = 86_400_000


class _History:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def append_user_event(self, session_id: str, content: str, **kwargs: Any) -> None:
        self.lines.append((content, kwargs))


class _Bot:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None, **kwargs: Any) -> None:
        self.turns.append({"message": message, **kwargs})


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self.store: dict[str, dict] = {_SID: doc} if doc else {}

    def read(self, session_id: str) -> dict:
        import copy

        return copy.deepcopy(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        import copy

        self.store[session_id] = copy.deepcopy(data)


def _text(text: str, *, wamid: str = "wamid.A", sent_ms: int = _NOW_MS - 5_000) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=wamid,
        from_number=_SID.removeprefix("wa_"),
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp=str(sent_ms // 1000),
    )


def _returning_customer() -> dict[str, Any]:
    """Volvió tras un episodio cerrado sin compra: el turno lleva la nota de frontera."""
    closed_at = _NOW_MS - 3 * _DAY_MS
    return {
        "active_route": "ventas",
        "episodes": [
            {
                "episode_id": "ep_1",
                "started_at_ms": closed_at - _DAY_MS,
                "closed_at_ms": closed_at,
                "closing_tag": "RECHAZO",
            }
        ],
    }


def _ingest(metadata: _Metadata) -> tuple[IngestInboundMessage, _History, _Bot]:
    history, bot = _History(), _Bot()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )
    return use_case, history, bot


async def _across_temporal(value: Any, type_: type) -> Any:
    """Lo que el workflow guarda en su historial y le da al paso siguiente."""
    converter = DataConverter.default
    [decoded] = await converter.decode(await converter.encode([value]), [type_])
    return decoded


async def _run_steps(use_case: IngestInboundMessage, message: WhatsAppMessage, *, received_at_ms: int) -> str:
    request = await _across_temporal(IngestRequest(message=asdict(message), received_at_ms=received_at_ms), IngestRequest)
    state = await _across_temporal(await use_case.read_state(request), IngestState)
    turn = await _across_temporal(await use_case.prepare_turn(request, state), IngestTurn)
    await use_case.write_history(request, state, turn)
    await use_case.deliver(request, state, turn)
    return turn.outcome


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        pytest.param(None, _text("hola, ¿tienen velas?"), id="primer-contacto"),
        pytest.param(_returning_customer(), _text("hola de nuevo, ¿y las de lavanda?"), id="vuelve-tras-cierre"),
        pytest.param(None, _text("¿siguen?", sent_ms=_NOW_MS - 3 * _DAY_MS), id="llega-fuera-de-ventana"),
    ],
)
@pytest.mark.asyncio
async def test_the_steps_leave_what_execute_leaves(doc, message) -> None:
    by_execute, history_e, bot_e = _ingest(_Metadata(doc))
    by_steps, history_s, bot_s = _ingest(_Metadata(doc))

    await by_execute.execute(message, received_at_ms=_NOW_MS)
    await _run_steps(by_steps, message, received_at_ms=_NOW_MS)

    assert history_s.lines == history_e.lines
    assert bot_s.turns == bot_e.turns


@pytest.mark.asyncio
async def test_the_boundary_note_of_a_returning_customer_survives_between_steps() -> None:
    """La nota la decide el paso del estado (el episodio se rota ahí) y la usa
    la entrega: un reintento de la entrega ya no la pierde."""
    use_case, _history, bot = _ingest(_Metadata(_returning_customer()))

    outcome = await _run_steps(use_case, _text("hola de nuevo"), received_at_ms=_NOW_MS)

    assert outcome == "deliver"
    [turn] = bot.turns
    assert any("nueva" in note.lower() or "anterior" in note.lower() for note in turn["extra_context"])
