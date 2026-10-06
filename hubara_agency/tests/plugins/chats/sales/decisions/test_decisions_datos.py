"""Revisión de cada dato de `set_order_slot` (diseño v2 §04, fase F6).

El LLM a veces guarda un dato de envío que el cliente nunca dio (lo supone,
lo trae de un pedido anterior). Antes de escribirlo, el motor le pregunta a
Jev si el cliente lo dio en la conversación (con sus palabras o
confirmándolo). Solo BLOQUEA con certeza alta (p ≤ 0,15); si Jev duda, cae o
tarda, el dato se guarda como hoy. Hoy no hay ninguna revisión: con
`reglas` todo pasa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions.capabilities.datos import DATOS, DatosDelPedido
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

EVENTS = (
    {"role": "assistant", "content": "¿A qué ciudad y dirección te lo enviamos?"},
    {"role": "user", "content": "A Medellín, calle 10 # 43-12"},
)


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def _inp(**values: str) -> DatosDelPedido:
    return DatosDelPedido(values=tuple(values.items()), events=EVENTS)


def test_today_every_value_passes() -> None:
    assert DATOS.rule(_inp(ciudad="Medellín", telefono="3001234567")) == ()


def test_one_question_per_value_with_the_conversation_in_view() -> None:
    state, questions = DATOS.ask(_inp(ciudad="Bogotá", telefono="3001234567"))

    assert [q.id for q in questions] == ["datos.ciudad", "datos.telefono"]
    assert "[cliente] A Medellín, calle 10 # 43-12" in state
    assert DATOS.ask(DatosDelPedido(values=(), events=EVENTS)) is None


#: Laboratorio caso-fotos-0930, 4567 t22 (r10 y r11; nombre cambiado): el
#: cliente mandó todo en un mensaje. Producción guardó los siete campos que
#: desglosó el LLM. El bot nuevo, con el mismo desglose, le preguntó a Jev por
#: cada dato; Jev ve la conversación con los datos personales tapados
#: («[dirección] casa 3, recibe [nombre]») y contestó 0,08 al nombre: no se
#: guardó y el complemento del turno se lo volvió a pedir al cliente.
PACKED = (
    {"role": "assistant", "content": "¿Cuántas unidades del Velón Gorrión quieres?"},
    {"role": "user", "content": "Mejor 2, una lila y otra azul. Me las mandas a Chía, calle 10 # 5-20 casa 3, "
                                "recibe Ana Pérez, cuánto vale el envío? pago contra entrega"},
)
PACKED_VALUES = (
    ("ciudad", "Chía"),
    ("direccion", "calle 10 # 5-20 casa 3"),
    ("metodo_pago", "contra entrega"),
    ("nombre_recibe", "Ana Pérez"),
)


def test_what_the_customer_wrote_is_kept_without_asking_jev() -> None:
    assert DATOS.ask(DatosDelPedido(values=PACKED_VALUES, events=PACKED)) is None


def test_what_the_llm_writes_out_in_full_still_counts_as_given() -> None:
    """El LLM completa abreviaturas, tildes y separadores: sigue siendo lo que
    el cliente escribió."""
    events = ({"role": "user", "content": "a nombre de ana perez, cll 10 #5-20 cs 3 en chia. "
                                          "cel 300 123 45 67, cc 1.020.345.678"},)
    values = (("nombre_recibe", "Ana Pérez"), ("direccion", "Calle 10 # 5-20 casa 3"), ("ciudad", "Chía"),
              ("telefono", "3001234567"), ("cedula", "1020345678"))

    assert DATOS.ask(DatosDelPedido(values=values, events=events)) is None


def test_a_value_the_customer_never_wrote_is_still_checked() -> None:
    inp = DatosDelPedido(values=(("ciudad", "Chía"), ("nombre_recibe", "Carlos Ruiz"), ("telefono", "3009998877")),
                         events=PACKED)

    _state, questions = DATOS.ask(inp)

    assert [q.id for q in questions] == ["datos.nombre_recibe", "datos.telefono"]


def test_only_the_customer_own_words_count() -> None:
    """Si el dato solo lo escribió el asesor, Jev dice si el cliente lo confirmó."""
    events = (
        {"role": "assistant", "content": "¿Te lo enviamos a nombre de Ana Pérez?"},
        {"role": "user", "content": "sí"},
    )

    _state, questions = DATOS.ask(DatosDelPedido(values=(("nombre_recibe", "Ana Pérez"),), events=events))

    assert [q.id for q in questions] == ["datos.nombre_recibe"]


def test_personal_data_never_travels_in_the_question() -> None:
    """Jev ve los datos personales tapados: preguntarle por el nombre era
    preguntarle algo que no podía ver, y además el valor salía sin tapar en la
    pregunta. La ciudad y el método de pago no son personales: van."""
    values = (("nombre_recibe", "Carlos Ruiz"), ("direccion", "Carrera 7 # 12-30"), ("barrio", "Chapinero"),
              ("telefono", "3009998877"), ("cedula", "79111222"), ("ciudad", "Bogotá"), ("metodo_pago", "Nequi"))

    _state, questions = DATOS.ask(DatosDelPedido(values=values, events=PACKED))

    texts = {q.id.split(".", 1)[1]: q.text for q in questions}
    for slot, value in values[:5]:
        assert value not in texts[slot], slot
        assert "tapad" in texts[slot], slot
    assert "Bogotá" in texts["ciudad"] and "Nequi" in texts["metodo_pago"]


def test_only_a_confident_no_blocks_a_value() -> None:
    inp = _inp(ciudad="Medellín", telefono="3001234567")

    assert DATOS.decide(inp, _result(_noul("datos.ciudad", 0.97), _noul("datos.telefono", 0.04)), (), {}) == ("telefono",)
    assert DATOS.decide(inp, _result(_noul("datos.ciudad", 0.97), _noul("datos.telefono", 0.4)), (), {}) == ()
    assert DATOS.decide(inp, _result(), (), {}) is None


# --- la tool -----------------------------------------------------------------

SID = "wa_573001234567"


def _tool(vault: Path, events: tuple[dict, ...] = EVENTS) -> "object":
    from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

    history = [dict(e) for e in events]
    return SetOrderSlotTool(workspace=str(vault), vault_dir=vault, history_reader=lambda _sid: history)


def _jev(monkeypatch, answers: dict[str, float]):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit

    fake = FakePerceptionAdapter({qid: _noul(qid, p) for qid, p in answers.items()})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


def _seed(vault: Path) -> None:
    session = vault / SID
    session.mkdir(parents=True, exist_ok=True)
    episode = {"episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")


@pytest.fixture
def jev_says_the_phone_was_never_given(monkeypatch):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit

    fake = FakePerceptionAdapter({"datos.ciudad": _noul("datos.ciudad", 0.97), "datos.telefono": _noul("datos.telefono", 0.03)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


async def test_with_jev_an_invented_value_is_not_saved_and_the_llm_is_told(tmp_path: Path, monkeypatch,
                                                                            jev_says_the_phone_was_never_given) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)

    out = json.loads(await _tool(tmp_path).execute_with_context(ctx, ciudad="Medellín", telefono="3001234567"))

    assert out["captured"] == {"ciudad": "Medellín"}
    assert [r["field"] for r in out["rejected"]] == ["telefono"]
    assert "pídeselo" in out["summary"]


async def test_by_default_every_value_is_saved_like_today(tmp_path: Path, monkeypatch,
                                                          jev_says_the_phone_was_never_given) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)

    out = json.loads(await _tool(tmp_path).execute_with_context(ctx, ciudad="Medellín", telefono="3001234567"))

    assert out["captured"] == {"ciudad": "Medellín", "telefono": "3001234567"}
    assert "rejected" not in out


async def test_with_jev_what_the_customer_wrote_is_saved_like_in_production(tmp_path: Path, monkeypatch) -> None:
    """4567 t22: lo que Jev contestó en r11 con la conversación tapada. Los
    datos están en el mensaje del cliente: se guardan sin preguntarle."""
    fake = _jev(monkeypatch, {"datos.ciudad": 0.98, "datos.direccion": 0.2, "datos.nombre_recibe": 0.08,
                              "datos.metodo_pago": 0.96})
    monkeypatch.setenv("DECISIONS_BOT", "B")
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)

    out = json.loads(await _tool(tmp_path, PACKED).execute_with_context(ctx, **dict(PACKED_VALUES)))

    assert out["captured"] == dict(PACKED_VALUES)
    assert "rejected" not in out
    assert fake.calls == []


async def test_a_value_already_saved_is_not_checked_again(tmp_path: Path, monkeypatch) -> None:
    """Al confirmar, el LLM vuelve a mandar lo que ya guardó, y el mensaje
    donde el cliente lo dio ya no está en lo último de la conversación."""
    fake = _jev(monkeypatch, {"datos.direccion": 0.03})
    _seed(tmp_path)
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    await _tool(tmp_path).execute_with_context(ctx, direccion="Calle 10 # 43-12")
    monkeypatch.setenv("DECISIONS_BOT", "B")
    later = ({"role": "assistant", "content": "¿Confirmas el pedido?"}, {"role": "user", "content": "sí, confirmo"})

    out = json.loads(await _tool(tmp_path, later).execute_with_context(ctx, direccion="Calle 10 # 43-12"))

    assert "rejected" not in out
    assert out["order_draft"]["direccion"] == "Calle 10 # 43-12"
    assert fake.calls == []
