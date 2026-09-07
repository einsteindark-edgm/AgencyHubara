"""Captura determinista de la cantidad en respuestas compuestas.

Incidente 2026-09-07 (run 943e6bff, session wa_573229041190): el agente
preguntó "¿Cuántas unidades deseas?" y el cliente respondió "Una que colores
tienes?" — cantidad + pregunta nueva en la misma frase. El LLM atendió solo
la pregunta (mandó el picker de colores) y NUNCA llamó `set_order_slot`
(cantidad). En el run siguiente (a9492a6f) volvió a preguntar la cantidad y
el cliente contestó "Ya te había dicho que una".

Fix (determinismo PREVENTIVO, mismo espíritu que `order_draft.py`): el
override `build_prompt` de Sales ve el historial (última burbuja del agente)
y el mensaje entrante. Si el agente acababa de preguntar la cantidad y el
cliente ARRANCA su respuesta con una cantidad, el sistema la fija en el
`order_draft` ANTES de armar el prompt y refresca el bloque
`[DATOS DEL PEDIDO YA CONFIRMADOS]` — el LLM lee "Cantidad: 1" pineado en vez
de tener que inferirlo del texto.

Parser deliberadamente CONSERVADOR: solo cantidad al INICIO del mensaje y
solo si la palabra siguiente no es un uso no-numérico común de "una/un"
("una pregunta", "un momento", "una más"). Falso negativo = comportamiento de
hoy (el LLM puede capturarla igual); falso positivo = cantidad equivocada
persistida, que es peor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from exoclaw_temporal.activities.conversation import _build_conversation
from exoclaw_temporal.config import BuildPromptInput, LLMConfig, WorkspaceConfig

from src.plugins.chats.agent.sales.activities.build_prompt_stage import (
    sales_build_prompt,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
)
from src.plugins.chats.agent.sales.use_cases.quantity_capture import (
    agent_asked_quantity,
    capture_quantity_from_reply,
    last_visible_agent_text,
    parse_leading_quantity,
)

_AGENT_ASKED = (
    "¡Excelente elección! 🤍 El *Velón Amor Eterno* en aroma *Lavanda* es una "
    "pieza preciosa.\n\n¿Cuántas unidades deseas?"
)
_DRAFT_HEADER = "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE"


# ---------------------------------------------------------------------------
# Parser puro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Una que colores tienes?", 1),  # el mensaje exacto del run 943e6bff
        ("una", 1),
        ("Una.", 1),
        ("Uno", 1),
        ("1", 1),
        ("2 unidades", 2),
        ("Dos, ¿qué colores tienes?", 2),
        ("Solo una", 1),
        ("una sola por ahora", 1),
        ("Un velón", 1),
        ("tres velas para regalo", 3),
        ("12", 12),
        ("🤍 una", 1),
    ],
)
def test_parse_leading_quantity_accepts(text: str, expected: int) -> None:
    assert parse_leading_quantity(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Una pregunta, ¿hacen envíos?",
        "Una preguntica",
        "Una duda",
        "Una consulta",
        "un momento",
        "una más",
        "un par",
        "una docena",
        "una amiga me recomendó",
        "Otra cosa: ¿tienen envío?",
        "Rosado",
        "quiero 3",  # no arranca con la cantidad: lo deja al LLM
        "[SISTEMA]: El usuario dejó de responder",
        "[el cliente tocó el botón: ✅ Confirmar]",
        "",
        "0",
    ],
)
def test_parse_leading_quantity_rejects(text: str) -> None:
    assert parse_leading_quantity(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_AGENT_ASKED, True),
        ("¿Qué cantidad necesitas?", True),
        ("Perfecto 🤍 ¿cuántas quieres?", True),
        ("El *Velón Amor Eterno* está disponible en estos colores:", False),
        ("¿Me confirmas tu número de teléfono completo?", False),
        (None, False),
        ("", False),
    ],
)
def test_agent_asked_quantity(text: str | None, expected: bool) -> None:
    assert agent_asked_quantity(text) is expected


def test_last_visible_agent_text_skips_tool_narration() -> None:
    """La última burbuja VISIBLE es el assistant sin tool_calls; la narración
    pre-tool ("Registro el producto y el aroma.") no se envía al cliente."""
    history = [
        {"role": "user", "content": "Velón amor eterno"},
        {
            "role": "assistant",
            "content": "Registro el producto y el aroma.",
            "tool_calls": [{"id": "x", "type": "function", "function": {}}],
        },
        {"role": "tool", "content": "{\"updated\": true}", "tool_call_id": "x"},
        {"role": "assistant", "content": _AGENT_ASKED},
        {"role": "user", "content": "Una que colores tienes?"},
    ]
    assert last_visible_agent_text(history) == _AGENT_ASKED
    assert last_visible_agent_text([]) is None


# ---------------------------------------------------------------------------
# Use case puro sobre metadata
# ---------------------------------------------------------------------------


def _metadata(slots: dict, *, order_id: str | None = None) -> dict:
    episode: dict = {
        "id": "ep_009",
        "opened_at_ms": 1,
        "order_draft": {"slots": dict(slots)},
    }
    if order_id:
        episode["order_id"] = order_id
    return {"episodes": [episode]}


def test_capture_sets_cantidad_when_agent_asked_and_reply_leads_with_it() -> None:
    meta = _metadata({"producto": "Velón Amor Eterno", "aroma": "Lavanda"})
    got = capture_quantity_from_reply(
        meta,
        last_agent_text=_AGENT_ASKED,
        inbound_text="Una que colores tienes?",
        now_ms=10,
    )
    assert got == 1
    assert meta["episodes"][-1]["order_draft"]["slots"]["cantidad"] == "1"


@pytest.mark.parametrize(
    ("slots", "last_agent_text", "inbound", "order_id"),
    [
        # el agente NO preguntó cantidad → "una" es ambiguo, no se toca
        ({"producto": "X"}, "¿Qué aroma prefieres?", "una", None),
        # sin producto elegido no hay pedido en curso
        ({}, _AGENT_ASKED, "una", None),
        # orden ya registrada: el draft dejó de ser mutable/proyectable
        ({"producto": "X"}, _AGENT_ASKED, "una", "order_01"),
        # cantidad distinta ya fijada: no la pisamos a ciegas (lo decide el LLM)
        ({"producto": "X", "cantidad": "2"}, _AGENT_ASKED, "una", None),
        # respuesta que no arranca con cantidad
        ({"producto": "X"}, _AGENT_ASKED, "Una pregunta, ¿hacen envíos?", None),
    ],
)
def test_capture_gates(slots, last_agent_text, inbound, order_id) -> None:
    meta = _metadata(slots, order_id=order_id)
    before = json.dumps(meta, sort_keys=True)
    assert (
        capture_quantity_from_reply(
            meta, last_agent_text=last_agent_text, inbound_text=inbound, now_ms=10
        )
        is None
    )
    assert json.dumps(meta, sort_keys=True) == before  # no muta nada


def test_capture_is_idempotent_on_same_value() -> None:
    """Retry de la activity: cantidad ya fijada al MISMO valor → sigue
    reportando la captura (para re-inyectar la nota) sin cambiar el draft."""
    meta = _metadata({"producto": "X", "cantidad": "1"})
    assert (
        capture_quantity_from_reply(
            meta, last_agent_text=_AGENT_ASKED, inbound_text="una", now_ms=10
        )
        == 1
    )
    assert meta["episodes"][-1]["order_draft"]["slots"]["cantidad"] == "1"


# ---------------------------------------------------------------------------
# Activity: repro end-to-end del run 943e6bff sobre `build_prompt`
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for stage, marker in (
        ("etapa_descubrimiento", "MARKER_DESCUBRIMIENTO"),
        ("etapa_variantes", "MARKER_VARIANTES"),
        ("etapa_datos_envio", "MARKER_DATOS_ENVIO"),
    ):
        d = ws / "skills" / stage
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\ndescription: guion {stage}\n---\n\n{marker}\n",
            encoding="utf-8",
        )
    return ws


def _seed_metadata(vault: Path, session_id: str, slots: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "episodes": [
            {"id": "ep_009", "opened_at_ms": 1, "order_draft": {"slots": slots}}
        ]
    }
    (d / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _seed_history(ws: Path, session_id: str, last_agent_text: str) -> None:
    """Historial como lo deja `record_turn` tras el turno anterior."""
    conv = _build_conversation(LLMConfig(model="fake"), WorkspaceConfig(path=str(ws)))
    session = conv.history.get_or_create(session_id)
    session.add_message("user", "Velón amor eterno")
    session.add_message(
        "assistant",
        "Registro el producto y el aroma.",
        tool_calls=[{"id": "t1", "type": "function", "function": {"name": "set_order_slot", "arguments": "{}"}}],
    )
    session.add_message("tool", "{\"updated\": true}", tool_call_id="t1")
    session.add_message("assistant", last_agent_text)
    conv.history.save(session)


def _input(ws: Path, session_id: str, message: str, plugin_context: list[str] | None) -> BuildPromptInput:
    return BuildPromptInput(
        session_id=session_id,
        message=message,
        channel="whatsapp",
        chat_id=session_id,
        llm=LLMConfig(model="fake"),
        workspace=WorkspaceConfig(path=str(ws)),
        media=None,
        plugin_context=plugin_context,
    )


@pytest.mark.asyncio
async def test_run_943e6bff_quantity_in_compound_reply_is_pinned(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    ws = _make_workspace(tmp_path)
    sid = "wa_573229041190"
    slots = {"producto": "Velón Amor Eterno", "aroma": "Lavanda"}
    _seed_metadata(_isolate_vault_dir, sid, slots)
    _seed_history(ws, sid, _AGENT_ASKED)
    stale_note = build_order_draft_note(slots)  # lo que armó el ingest

    messages = await sales_build_prompt(
        _input(ws, sid, "Una que colores tienes?", ["[CONTEXTO DE TURNO] hora", stale_note])
    )

    # 1. persistido en el vault (sobrevive al turno; el stage lo ve)
    meta = json.loads((_isolate_vault_dir / sid / "metadata.json").read_text("utf-8"))
    assert meta["episodes"][-1]["order_draft"]["slots"]["cantidad"] == "1"

    # 2. el LLM lee la cantidad pineada y NO la nota vieja sin cantidad
    system = messages[0]["content"]
    assert "Cantidad: 1" in system
    assert system.count(_DRAFT_HEADER) == 1
    assert "[CONTEXTO DE TURNO] hora" in system  # el resto del contexto sigue
    # 3. sigue en variantes (falta color) — la etapa se resuelve POST captura
    assert "MARKER_VARIANTES" in system


@pytest.mark.asyncio
async def test_quantity_capture_advances_stage_when_variants_complete(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    ws = _make_workspace(tmp_path)
    sid = "wa_stage_adv"
    _seed_metadata(_isolate_vault_dir, sid, {"producto": "X", "aroma": "Lavanda", "color": "Rosado"})
    _seed_history(ws, sid, _AGENT_ASKED)
    messages = await sales_build_prompt(_input(ws, sid, "dos", None))
    system = messages[0]["content"]
    assert "MARKER_DATOS_ENVIO" in system
    assert "MARKER_VARIANTES" not in system
    assert "Cantidad: 2" in system  # nota creada aunque el ingest no mandó ninguna


@pytest.mark.asyncio
async def test_no_capture_when_reply_is_a_question_opener(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    ws = _make_workspace(tmp_path)
    sid = "wa_no_capture"
    slots = {"producto": "X", "aroma": "Lavanda"}
    _seed_metadata(_isolate_vault_dir, sid, slots)
    _seed_history(ws, sid, _AGENT_ASKED)
    note = build_order_draft_note(slots)
    messages = await sales_build_prompt(
        _input(ws, sid, "Una pregunta, ¿hacen envíos a Cali?", [note])
    )
    meta = json.loads((_isolate_vault_dir / sid / "metadata.json").read_text("utf-8"))
    assert "cantidad" not in meta["episodes"][-1]["order_draft"]["slots"]
    system = messages[0]["content"]
    assert "Cantidad:" not in system
    assert system.count(_DRAFT_HEADER) == 1


@pytest.mark.asyncio
async def test_no_capture_when_agent_did_not_ask_quantity(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    ws = _make_workspace(tmp_path)
    sid = "wa_not_asked"
    _seed_metadata(_isolate_vault_dir, sid, {"producto": "X"})
    _seed_history(ws, sid, "El *X* está disponible en estos aromas:")
    await sales_build_prompt(_input(ws, sid, "una", None))
    meta = json.loads((_isolate_vault_dir / sid / "metadata.json").read_text("utf-8"))
    assert "cantidad" not in meta["episodes"][-1]["order_draft"]["slots"]


@pytest.mark.asyncio
async def test_common_turn_leaves_no_vault_side_effect(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    """Turno común (sin cantidad en juego) sobre una sesión sin metadata: no
    debe aparecer un dir de sesión/lock en el vault por el pre-check."""
    ws = _make_workspace(tmp_path)
    sid = "wa_fresh_no_side_effect"
    await sales_build_prompt(_input(ws, sid, "Hola, quiero ver velas", None))
    assert not (_isolate_vault_dir / sid).exists()


def test_stage_script_tells_llm_to_pin_quantity_from_compound_reply() -> None:
    """Cinturón y tirantes: el guion de variantes también nombra el caso."""
    skill = Path(
        "src/plugins/chats/agent/sales/workspace/skills/etapa_variantes/SKILL.md"
    ).read_text("utf-8")
    assert "Una, ¿qué colores tienes?" in skill
    assert "set_order_slot(cantidad" in skill
