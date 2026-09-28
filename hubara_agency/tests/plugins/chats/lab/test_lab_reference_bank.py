"""Banco de referencia del motor de decisiones (MOTOR_DECISIONES_PLAN, F1).

Claude Code etiqueta ~150 turnos difíciles respondiendo el MISMO cuestionario
que responde Jev, leyendo la conversación completa. Con eso se mide a Jev
pregunta por pregunta (precisión, cobertura, calibración) y se decide qué
preguntas pueden actuar: precisión ≥ 0,95 con ≥ 30 positivos.

* La selección es determinista (sorteo con sha256 de semilla + caso) y llena
  cuotas por categoría: respuestas cortas, ráfagas de varios mensajes, citas,
  fotos y confirmaciones; el resto del tamaño, con los demás turnos.
* Cada ítem lleva EXACTAMENTE el `state` y las preguntas que el motor le
  manda a Jev en ese turno (anonimizados) y la conversación completa.
* Las etiquetas se validan contra las preguntas del ítem, y la medición
  compara a Jev con ellas.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.decisions import profiles
from src.plugins.chats.agent.sales.decisions.activities import perceive_burst_activity
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.plugins.chats.agent.sales_lab.cases import LabCase, build_cases
from src.plugins.chats.agent.sales_lab.reference_bank import (
    ask_bank,
    build_item,
    score,
    select_reference_turns,
    turn_categories,
    validate_answers,
)
from src.plugins.chats.agent.sales_lab.run.arena import calibration
from src.sdk.connectorkit import FakePerceptionAdapter, TypedAnswer, anonymize_text

SID = "wa_573001234567"
WS = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
T0 = 1_789_500_000_000
CUT = 1_789_000_000_000
JEV_MODEL = "typesafe/jev-1.13-20260917"


def _user(text: str, wamid: str | None = None, **extra) -> dict:
    event = {"role": "user", "content": text}
    if wamid:
        event["wamid"] = wamid
    return {**event, **extra}


def _card(kind: str, text: str) -> dict:
    return {"role": "assistant", "kind": "ui_component", "component_kind": kind, "content": text}


BOT = {"role": "assistant", "content": "¿Qué aroma prefieres?"}


def _lab_case(i: int, burst: list[dict], *, prefix: int, trigger: str = "customer", **extra) -> LabCase:
    fields = dict(
        case_id=f"{SID}/ep_{i:03d}/t1",
        session_id=SID,
        episode_id=f"ep_{i:03d}",
        turn=1,
        turn_key=f"run:lab/t:{i}",
        at_ms=T0 + i * 60_000 + 30_000,
        trigger=trigger,
        burst=burst,
        dashboard_prefix=prefix,
        llm_prefix=0,
        stage_in=None,
        draft={},
        state={},
        episodes_at=[],
    )
    return LabCase(**(fields | extra))


# ── selección ────────────────────────────────────────────────────────────────

_TEXTS = {
    "corta": ["Si"],
    "varios": ["Quiero ver el catálogo de velas", "y cuánto cuesta el envío a Medellín"],
    "cita": ["Me interesa este modelo de la foto"],
    "foto": ["[el cliente envió una foto: una vela roja con flores]"],
    "confirmacion": ["Perfecto, así lo quiero confirmar"],
    "otro": ["Quisiera saber qué aromas tienen disponibles"],
    "handoff": ["Si"],
    "vacio": ["   "],
}


def _synthetic(kinds: list[str]) -> tuple[list[LabCase], dict[str, list[dict]]]:
    """Un turno por tipo pedido, todos en la misma conversación: antes de cada
    ráfaga, la respuesta del bot (la tarjeta de confirmación en las
    confirmaciones); las citas llevan `reply_to` en el dashboard."""
    events: list[dict] = []
    cases: list[LabCase] = []
    for i, kind in enumerate(kinds):
        if kind == "confirmacion":
            events.append(_card("order_confirmation", "🧾 El bot envió el resumen del pedido con botones para confirmar"))
        else:
            events.append({"role": "assistant", "content": "¿Te puedo ayudar con algo más?"})
        prefix = len(events)
        burst = []
        for j, text in enumerate(_TEXTS[kind]):
            wamid = f"wamid.{i}.{j}"
            event = _user(text, wamid)
            if kind == "cita":
                event["reply_to"] = {"id": "wamid.bot"}
            events.append(event)
            burst.append({"text": text, "ts_ms": T0 + i * 60_000 + j * 1_000, "wamid": wamid})
        cases.append(_lab_case(i, burst, prefix=prefix, trigger="handoff" if kind == "handoff" else "customer"))
    return cases, {SID: events}


def _kinds(selected: list[str], cases: list[LabCase], kinds: list[str]) -> Counter:
    kind_of = {c.case_id: k for c, k in zip(cases, kinds)}
    return Counter(kind_of[cid] for cid in selected)


def _draw_rank(seed: str, case_id: str) -> str:
    return hashlib.sha256((seed + case_id).encode("utf-8")).hexdigest()


_PLENTY = ["corta"] * 60 + ["varios"] * 50 + ["cita"] * 30 + ["foto"] * 30 + ["confirmacion"] * 40 + ["otro"] * 20


def test_the_bank_fills_the_quota_of_each_hard_category() -> None:
    cases, events = _synthetic(_PLENTY)

    selected = select_reference_turns(cases, events_by_session=events)

    assert _kinds(selected, cases, _PLENTY) == Counter(
        {"corta": 45, "varios": 35, "cita": 20, "foto": 20, "confirmacion": 30}
    )
    assert len(selected) == len(set(selected)) == 150


def test_a_scarce_category_leaves_its_room_to_the_rest_of_the_turns() -> None:
    kinds = ["corta"] * 60 + ["varios"] * 50 + ["cita"] * 5 + ["foto"] * 30 + ["confirmacion"] * 40 + ["otro"] * 30
    cases, events = _synthetic(kinds)

    selected = select_reference_turns(cases, events_by_session=events)
    got = _kinds(selected, cases, kinds)

    assert len(selected) == len(set(selected)) == 150
    assert got["cita"] == 5
    assert got["corta"] >= 45 and got["varios"] >= 35 and got["foto"] >= 20 and got["confirmacion"] >= 30


def test_only_customer_turns_with_text_enter_the_bank() -> None:
    kinds = ["corta"] * 4 + ["handoff"] * 3 + ["vacio"] * 3 + ["otro"] * 2
    cases, events = _synthetic(kinds)

    selected = select_reference_turns(cases, events_by_session=events)

    assert sorted(selected) == sorted(c.case_id for c, k in zip(cases, kinds) if k in ("corta", "otro"))


def test_the_draw_is_deterministic_and_changes_with_the_seed() -> None:
    cases, events = _synthetic(_PLENTY)

    first = select_reference_turns(cases, events_by_session=events)
    again = select_reference_turns(list(reversed(cases)), events_by_session=events)
    other = select_reference_turns(cases, events_by_session=events, seed="banco-ref-v2")

    assert first == again  # el orden en que llegan los casos no importa
    assert first == sorted(first, key=lambda cid: _draw_rank("banco-ref-v1", cid))  # en el orden del sorteo
    assert set(other) != set(first)


def test_with_another_size_the_quotas_scale() -> None:
    kinds = ["corta"] * 20 + ["varios"] * 20 + ["cita"] * 10 + ["foto"] * 10 + ["confirmacion"] * 10 + ["otro"] * 10
    cases, events = _synthetic(kinds)

    got = _kinds(select_reference_turns(cases, events_by_session=events, size=10), cases, kinds)

    assert sum(got.values()) == 10
    assert got["corta"] >= 3 and got["varios"] >= 2 and got["cita"] >= 1 and got["foto"] >= 1 and got["confirmacion"] >= 2


# ── categorías, leídas del dashboard como lo escribe el ingest ───────────────


def _categories(before: list[dict], burst_events: list[dict], *, burst: list[dict] | None = None) -> list[str]:
    if burst is None:
        burst = [{"text": e["content"], "ts_ms": T0 + k, "wamid": e.get("wamid")} for k, e in enumerate(burst_events)]
    return turn_categories(_lab_case(0, burst, prefix=len(before)), [*before, *burst_events])


def test_a_short_reply_is_any_message_of_up_to_15_characters() -> None:
    assert "respuesta_corta" in _categories([BOT], [_user("  Dale  ", "w1")])
    assert "respuesta_corta" in _categories([BOT], [_user("Quiero el de lavanda por favor", "w1"), _user("Ok", "w2")])
    assert "respuesta_corta" not in _categories([BOT], [_user("Quiero el de lavanda", "w1")])


def test_a_multi_message_burst_has_two_or_more_messages() -> None:
    assert "rafaga_multiple" in _categories([BOT], [_user("Hola buenas tardes", "w1"), _user("¿tienen velas de soya?", "w2")])
    assert "rafaga_multiple" not in _categories([BOT], [_user("¿tienen velas de soya?", "w1")])


def test_a_quote_is_a_burst_message_that_cites_another_on_the_dashboard() -> None:
    """El ingest guarda la cita en el evento del dashboard (`reply_to`), a
    veces solo con el id del mensaje citado."""
    assert "cita" in _categories([BOT], [_user("Esta", "w1", reply_to={"id": "wamid.bot"})])
    assert "cita" not in _categories([BOT], [_user("Esta", "w1")])


def test_a_photo_is_what_the_ingest_writes_after_vision() -> None:
    """Visión describe la foto y la reinyecta como texto; si falla, queda el
    aviso de imagen que no se pudo ver; sin visión, el marcador genérico."""
    for text in (
        '[el cliente envió una foto: una vela roja con flores] con el texto: "esta"',
        "[el cliente envió un comprobante de pago: transferencia Nequi]",
        "[el cliente envió una imagen que no pude ver bien]",
        "[el cliente envió un image]",
    ):
        assert "foto" in _categories([BOT], [_user(text, "w1")]), text
    assert "foto" in _categories([BOT], [_user("esta", "w1", image_url="/media/p.jpg")])
    assert "foto" not in _categories([BOT], [_user("¿tienen fotos de la vela roja?", "w1")])


def test_a_vision_reentry_is_matched_with_its_dashboard_message() -> None:
    """El reentry de visión viaja con el wamid de la foto + `_vision`; en el
    dashboard queda el wamid real (el ingest quita el sufijo)."""
    event = _user("[el cliente envió una foto: una vela roja]", "wamid.P", reply_to={"id": "wamid.bot"})
    burst = [{"text": event["content"], "ts_ms": T0, "wamid": "wamid.P_vision"}]

    assert {"foto", "cita"} <= set(_categories([BOT], [event], burst=burst))


def test_a_confirmation_answers_a_card_with_buttons_or_the_shipping_form() -> None:
    """Lo último que el bot le mostró al cliente antes de la ráfaga."""
    si = [_user("Si", "w1")]
    for kind in ("order_confirmation", "quick_replies", "shipping_flow"):
        assert "confirmacion" in _categories([BOT, _card(kind, "la tarjeta")], si), kind
    assert "confirmacion" not in _categories([_card("quick_replies", "botones"), BOT], si)
    assert "confirmacion" not in _categories([_card("product_detail", "📷 El bot envió una foto del producto")], si)
    team = {"role": "assistant", "sender": "human", "content": "Hola, soy Laura del equipo"}
    assert "confirmacion" not in _categories([_card("quick_replies", "botones"), team], si)


# ── ítems ────────────────────────────────────────────────────────────────────


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


SLOTS = {
    "producto": "Duo Zodiacal Aries",
    "color": "amarillo",
    "aroma": "lavanda",
    "cantidad": 1,
    "ciudad": "Medellín",
    "nombre_recibe": "Carolina Pérez",
    "barrio": "Chapinero Alto",
}
DRAFT = {
    "slots": SLOTS,
    "items": [{"producto": "Duo Zodiacal Aries", "color": "amarillo", "aroma": "lavanda", "cantidad": 1}],
    "updated_at_ms": T0 + 50_000,
}
EVENTS = [
    _user("Hola, quiero el Duo Zodiacal Aries", "wamid.c1", timestamp=_iso(T0 + 1_000)),
    {"role": "assistant", "content": "¡Hola! El Duo Zodiacal Aries viene en amarillo con aroma lavanda.",
     "timestamp": _iso(T0 + 9_000)},
    {"role": "assistant", "sender": "human", "content": "Hola, soy Laura del equipo", "timestamp": _iso(T0 + 20_000)},
    _user("Es para Carolina, en Chapinero Alto", "wamid.c2", timestamp=_iso(T0 + 40_000)),
    {**_card("product_detail", "📷 El bot envió una foto del producto: «Vela Cubo Love»"),
     "timestamp": _iso(T0 + 55_000), "wamid": "wamid.b0"},
    {**_card("quick_replies", "🔘 El bot envió botones: Sí · Cambiar algo — con el mensaje: "
                              "«¿Te lo enviamos a Chapinero Alto, Carolina?»"),
     "timestamp": _iso(T0 + 60_000), "wamid": "wamid.b1"},
    _user("Si", "wamid.c3", timestamp=_iso(T0 + 120_000),
          reply_to={"id": "wamid.b0", "author": "agent", "text": "Vela Cubo Love"}),
    {"role": "assistant", "content": "¡Listo, Carolina! Te confirmo el pedido.", "timestamp": _iso(T0 + 130_000)},
]


def _write_bench(root: Path, *, draft: dict | None = None, draft_before: dict | None = None) -> Path:
    """Un banco como lo exporta producción: una conversación con tres turnos
    del cliente y un handoff. El turno 3 es un «Si» que responde botones y
    cita una foto del bot."""
    bench = root / "bench"
    s = bench / "vault" / SID
    (s / "sessions").mkdir(parents=True)
    (s / "evals").mkdir()
    episodes = [{"episode_id": "ep_001", "started_at_ms": T0, "closed_at_ms": None, "order_draft": draft or DRAFT}]
    (s / "metadata.json").write_text(json.dumps({"episodes": episodes}, ensure_ascii=False), encoding="utf-8")
    body = "\n".join(json.dumps(e, ensure_ascii=False) for e in EVENTS) + "\n"
    (s / "sessions" / f"{SID}.jsonl").write_text(body, encoding="utf-8")
    before = dict(SLOTS) if draft_before is None else draft_before
    traces = [
        {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 3_000,
         "draft": {}, "state": {"route": "ventas"}},
        {"turn": 2, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 45_000,
         "draft": before, "state": {"route": "ventas"}},
        {"turn": 3, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 125_000,
         "turn_key": "run:r1/t:3", "draft": dict(SLOTS), "state": {"route": "ventas"},
         "inbound": [{"seq": 1, "wamid": "wamid.c3", "ts_ms": T0 + 120_000, "kind": "text", "text": "Si"}]},
        {"turn": 4, "episode_id": "ep_001", "trigger": "handoff", "turn_started_ms": T0 + 200_000,
         "draft": dict(SLOTS), "state": {}},
    ]
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    manifest = {"bench_id": "bench-ref", "sessions": [SID], "since_ms": CUT}
    (bench / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bench


def _turn(bench: Path, turn: int) -> LabCase:
    return next(c for c in build_cases(bench, sales_workspace=WS).cases if c.turn == turn)


class _RecordingPort:
    """Lo que de verdad sale hacia Jev: el `state` como lo anonimiza el
    adaptador (el perfil del oráculo anonimiza) y las preguntas."""

    def __init__(self) -> None:
        self.fake = FakePerceptionAdapter()
        self.sent: list[str] = []
        self.questions: list[list[str]] = []

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.sent.append(anonymize_text(state, redact=redact))
        self.questions.append([q.id for q in questions])
        return await self.fake.ask(state, questions, timeout_s=timeout_s, redact=redact)


async def test_the_item_state_is_exactly_what_the_engine_sends_jev_in_production(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch
) -> None:
    """En producción la activity lee el vault: el historial ya trae la ráfaga
    (con la cita) y la metadata es la del inicio del turno. El ítem del banco
    tiene que ser, carácter por carácter, lo que Jev recibió."""
    from src.sdk import connectorkit

    bench = _write_bench(tmp_path)
    case = _turn(bench, 3)
    item = build_item(case, EVENTS, profile_id="jev-v2")

    vault = _isolate_vault_dir / SID
    (vault / "sessions").mkdir(parents=True)
    history = EVENTS[: case.dashboard_prefix + 1]  # hasta la ráfaga, inclusive
    (vault / "sessions" / f"{SID}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in history) + "\n", encoding="utf-8"
    )
    (vault / "metadata.json").write_text((bench / "vault" / SID / "metadata.json").read_text(encoding="utf-8"))
    port = _RecordingPort()
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: port)
    burst = [{"text": "Si", "ts_ms": T0 + 120_000, "wamid": "wamid.c3"}]

    await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v2", messages=burst)
    )

    assert port.sent == [item["state"]]
    assert port.questions == [[q["id"] for q in item["questions"]]]
    assert "(el cliente cita este mensaje: «Vela Cubo Love»)" in item["state"]
    for secret in ("Carolina", "Chapinero"):
        assert secret not in item["state"]


def test_the_item_carries_the_questionnaire_of_the_profile_with_its_options(tmp_path: Path) -> None:
    item = build_item(_turn(_write_bench(tmp_path), 3), EVENTS, profile_id="jev-v2")

    ids = [q["id"] for q in item["questions"]]
    assert ids == [
        *(f"topic.{t}" for t in load_questionnaire("rafaga-v2").topic_ids),
        "thread.bot_asked",
        "thread.answers_bot",
        "thread.answer",
        "msg.1.topic",
    ]
    answer = next(q for q in item["questions"] if q["id"] == "thread.answer")
    assert answer["kind"] == "choice" and answer["options"] == ["si", "no", "otra"]
    assert answer["criteria"]["otra"].startswith("otra cosa")
    precio = next(q for q in item["questions"] if q["id"] == "topic.precio")
    assert precio["kind"] == "noul" and precio["options"] == [] and set(precio["criteria"]) == {"true", "false"}
    assert (item["turn_key"], item["case_id"], item["session_id"]) == ("run:r1/t:3", f"{SID}/ep_001/t3", SID)
    assert (item["profile"], item["questionnaire"]) == ("jev-v2", "rafaga-v2")
    assert item["categories"] == ["respuesta_corta", "cita", "confirmacion"]


def test_the_conversation_is_every_earlier_event_and_then_this_turn_without_personal_data(tmp_path: Path) -> None:
    item = build_item(_turn(_write_bench(tmp_path), 3), EVENTS, profile_id="jev-v2")

    assert item["conversation"] == [
        "[cliente] Hola, quiero el Duo Zodiacal Aries",
        "[asesor] ¡Hola! El Duo Zodiacal Aries viene en amarillo con aroma lavanda.",
        "[equipo] Hola, soy Laura del equipo",
        "[cliente] Es para [nombre], en [nombre]",
        "[asesor] 📷 El bot envió una foto del producto: «Vela Cubo Love»",
        "[asesor] 🔘 El bot envió botones: Sí · Cambiar algo — con el mensaje: «¿Te lo enviamos a [nombre], [nombre]?»",
        "ESTE TURNO",
        "[1] Si",
    ]


def test_the_conversation_is_not_cut_like_the_window() -> None:
    """La ventana de Jev son los últimos 8 eventos; quien etiqueta lee todo."""
    before = [{"role": "assistant" if k % 2 else "user", "content": f"mensaje {k}"} for k in range(12)]
    case = _lab_case(0, [{"text": "Ok", "ts_ms": T0, "wamid": "w1"}], prefix=len(before))

    item = build_item(case, [*before, _user("Ok", "w1")], profile_id="jev-v2")

    assert len(item["conversation"]) == 12 + 2
    assert item["conversation"][0] == "[cliente] mensaje 0" and item["conversation"][-2:] == ["ESTE TURNO", "[1] Ok"]


def test_v1_items_ask_the_v1_questions_without_context(tmp_path: Path) -> None:
    item = build_item(_turn(_write_bench(tmp_path), 3), EVENTS, profile_id="jev-v1")

    assert item["questionnaire"] == "rafaga-v1"
    assert item["state"] == "Mensajes del cliente en este turno:\n[1] (+0 s) Si"
    assert "stage" in [q["id"] for q in item["questions"]]


def test_the_facts_are_those_of_the_start_of_the_turn(tmp_path: Path) -> None:
    """El borrador del banco es el del FINAL. Si cambió después de que empezó
    el turno, los hechos salen del que dejó la traza anterior (como en el
    sandbox), con cada producto del pedido en su ítem."""
    final = {
        "slots": {**SLOTS, "ciudad": "Cali", "direccion": "Calle 5 # 10-20"},
        "items": DRAFT["items"],
        "updated_at_ms": T0 + 500_000,  # después del turno 3
    }
    before = {
        "producto": "Duo Zodiacal Aries + Vela Cubo Love",
        "items": [
            {"producto": "Duo Zodiacal Aries", "color": "amarillo", "cantidad": 1},
            {"producto": "Vela Cubo Love", "aroma": "vainilla", "cantidad": 2},
        ],
    }

    item = build_item(_turn(_write_bench(tmp_path, draft=final, draft_before=before), 3), EVENTS, profile_id="jev-v2")

    assert "Ítem 1: Duo Zodiacal Aries, amarillo, 1 unidad" in item["state"]
    assert "Ítem 2: Vela Cubo Love, vainilla, 2 unidades" in item["state"]
    assert "Ciudad: falta" in item["state"] and "Cali" not in item["state"]


# ── etiquetas ────────────────────────────────────────────────────────────────

ITEM = {
    "turn_key": "run:r1/t:3",
    "questions": [
        {"id": "topic.precio", "kind": "noul", "text": "¿…?", "options": [], "criteria": {"true": "sí", "false": "no"}},
        {"id": "thread.answer", "kind": "choice", "text": "¿…?", "options": ["si", "no", "otra"],
         "criteria": {"si": "sí", "no": "no", "otra": "otra cosa"}},
    ],
}


def test_a_complete_answer_is_valid() -> None:
    assert validate_answers(ITEM, {"topic.precio": False, "thread.answer": "si"}) == []


def test_each_answer_has_the_type_of_its_question() -> None:
    errors = validate_answers(ITEM, {"topic.precio": "false", "thread.answer": "quizás"})

    assert len(errors) == 2
    assert errors[0].startswith("topic.precio:") and "true o false" in errors[0]
    assert errors[1].startswith("thread.answer:") and "si, no, otra" in errors[1]


def test_a_missing_or_unknown_question_is_rejected() -> None:
    assert validate_answers(ITEM, {"topic.precio": True, "topic.nueva": True}) == [
        "thread.answer: sin respuesta",
        "topic.nueva: no es una pregunta de este turno",
    ]
    assert validate_answers(ITEM, ["si"]) == ["answers tiene que ser un objeto {pregunta: respuesta}"]


# ── medición ─────────────────────────────────────────────────────────────────


def _question(qid: str) -> dict:
    if qid.startswith("topic."):
        return {"id": qid, "kind": "noul", "text": "", "options": [], "criteria": {}}
    return {"id": qid, "kind": "choice", "text": "", "options": ["si", "no", "otra"], "criteria": {}}


def _bank(rows: list[tuple[dict, dict]], *, qids: tuple[str, ...] = ("topic.precio",)) -> tuple[list, list, list]:
    """(respuestas de Claude Code, respuestas de Jev) por turno."""
    items, labels, jev = [], [], []
    for k, (label, answers) in enumerate(rows):
        key = f"t{k}"
        items.append({"turn_key": key, "questions": [_question(q) for q in qids]})
        labels.append({"turn_key": key, "answers": label})
        jev.append({"turn_key": key, "ok": True, "model": JEV_MODEL, "error": None, "answers": answers, "confidence": {}})
    return items, labels, jev


def _measure(bank: tuple[list, list, list], qid: str = "topic.precio", *, profile_id: str = "jev-v2") -> dict:
    out = score(*bank, profile_id=profile_id)
    assert qid in out, out
    return out[qid]


def test_a_yes_no_question_is_measured_at_the_detect_threshold_of_the_profile() -> None:
    items, labels, jev = _bank([
        ({"topic.precio": True}, {"topic.precio": 0.9}),  # acierto
        ({"topic.precio": True}, {"topic.precio": 0.5}),  # se le pasó
        ({"topic.precio": False}, {"topic.precio": 0.8}),  # falsa alarma
        ({"topic.precio": False}, {"topic.precio": 0.1}),  # bien descartado
        ({"topic.precio": True}, {"topic.precio": 0.7}),  # el umbral cuenta: p ≥ 0,70
    ])

    out = score(items, labels, jev, profile_id="jev-v2")

    assert set(out) == {"topic.precio"}
    m = out["topic.precio"]
    assert (m["kind"], m["threshold"], m["n"], m["tp"], m["fp"], m["fn"], m["tn"]) == ("noul", 0.70, 5, 2, 1, 1, 1)
    assert (m["precision"], m["recall"], m["positives"], m["ready"]) == (0.6667, 0.6667, 3, False)
    assert m["calibration"] == calibration([(0.9, True), (0.5, True), (0.8, False), (0.1, False), (0.7, True)])


def test_without_yes_predictions_precision_is_none_not_one() -> None:
    m = _measure(_bank([({"topic.precio": True}, {"topic.precio": 0.2}), ({"topic.precio": False}, {"topic.precio": 0.1})]))

    assert (m["precision"], m["recall"], m["ready"]) == (None, 0.0, False)


def test_a_yes_no_question_may_act_with_precision_095_and_30_positives() -> None:
    hit = ({"topic.precio": True}, {"topic.precio": 0.9})
    false_alarm = ({"topic.precio": False}, {"topic.precio": 0.9})

    assert _measure(_bank([hit] * 30))["ready"] is True
    assert _measure(_bank([hit] * 29))["ready"] is False  # pocos positivos
    worse = _measure(_bank([hit] * 40 + [false_alarm] * 3))
    assert worse["precision"] == 0.9302 and worse["ready"] is False


def test_a_choice_question_may_act_with_accuracy_095_and_30_answers() -> None:
    right = ({"thread.answer": "si"}, {"thread.answer": "si"})

    assert _measure(_bank([right] * 30, qids=("thread.answer",)), "thread.answer")["ready"] is True
    assert _measure(_bank([right] * 29, qids=("thread.answer",)), "thread.answer")["ready"] is False


def test_the_topic_of_each_message_is_measured_as_one_question() -> None:
    items = [
        {"turn_key": "t0", "questions": [_question("msg.1.topic"), _question("msg.2.topic")]},
        {"turn_key": "t1", "questions": [_question("msg.1.topic")]},
    ]
    labels = [
        {"turn_key": "t0", "answers": {"msg.1.topic": "precio", "msg.2.topic": "envio"}},
        {"turn_key": "t1", "answers": {"msg.1.topic": "saludo"}},
    ]
    jev = [
        {"turn_key": "t0", "ok": True, "answers": {"msg.1.topic": "precio", "msg.2.topic": "catalogo"},
         "confidence": {"msg.1.topic": 0.9, "msg.2.topic": 0.5}},
        {"turn_key": "t1", "ok": True, "answers": {"msg.1.topic": "saludo"}, "confidence": {"msg.1.topic": 0.7}},
    ]

    out = score(items, labels, jev, profile_id="jev-v2")

    assert out == {
        "msg.*.topic": {"kind": "choice", "n": 3, "correct": 2, "accuracy": 0.6667, "mean_confidence": 0.7, "ready": False}
    }


def test_only_turns_with_a_label_and_an_answer_from_jev_count_and_the_last_label_wins() -> None:
    items, labels, jev = _bank([
        ({"topic.precio": True}, {"topic.precio": 0.9}),
        ({"topic.precio": True}, {"topic.precio": 0.9}),
        ({"topic.precio": True}, {"topic.precio": 0.9}),
    ])
    labels = [labels[0], labels[1], {"turn_key": "t0", "answers": {"topic.precio": False}}]  # t2 sin etiqueta; t0 corregido
    jev[1] = {"turn_key": "t1", "ok": False, "error": "timeout", "answers": {}}

    m = _measure((items, labels, jev))

    assert (m["n"], m["tp"], m["fp"]) == (1, 0, 1)


def test_the_threshold_comes_from_the_profile(monkeypatch) -> None:
    v2 = profiles.get_engine_profile("jev-v2")
    strict = dataclasses.replace(v2, id="jev-estricto", thresholds={**v2.thresholds, "detect": 0.95})
    table = dict(profiles.load_engine_profiles()) | {"jev-estricto": strict}
    monkeypatch.setattr(profiles, "load_engine_profiles", lambda: table)

    m = _measure(_bank([({"topic.precio": True}, {"topic.precio": 0.9})]), profile_id="jev-estricto")

    assert (m["threshold"], m["tp"], m["fn"]) == (0.95, 0, 1)


# ── preguntarle a Jev ────────────────────────────────────────────────────────


class _Oracle:
    """Jev falso: responde lo que se le fije, registra lo que recibe y puede
    caerse en un turno."""

    def __init__(self, *, answers: dict | None = None, error: str | None = None, boom_in: str | None = None) -> None:
        self.fake = FakePerceptionAdapter(answers or {}, error=error)
        self.boom_in = boom_in
        self.calls: list[tuple[str, list[tuple], tuple]] = []

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.calls.append((state, [(q.id, q.kind, q.text, q.criteria) for q in questions], tuple(redact)))
        if self.boom_in and self.boom_in in state:
            raise RuntimeError("se cayó la conexión")
        result = await self.fake.ask(state, questions, timeout_s=timeout_s, redact=redact)
        return dataclasses.replace(result, model=JEV_MODEL)


def _install(monkeypatch, port: _Oracle) -> list[str]:
    from src.sdk import connectorkit

    asked: list[str] = []

    def _get(oracle_id: str) -> _Oracle:
        asked.append(oracle_id)
        return port

    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return asked


async def test_jev_gets_exactly_the_state_and_the_questions_of_each_item(tmp_path: Path, monkeypatch) -> None:
    bench = _write_bench(tmp_path)
    items = [build_item(c, EVENTS, profile_id="jev-v2") for c in build_cases(bench, sales_workspace=WS).cases
             if c.trigger == "customer"]
    choice = TypedAnswer(id="thread.answer", kind="choice", choice="si", probs=(("si", 0.8), ("otra", 0.2)), confidence=0.8)
    port = _Oracle(answers={"thread.answer": choice})
    asked = _install(monkeypatch, port)

    rows = await ask_bank(items, profile_id="jev-v2")

    assert set(asked) == {"jev-1.13"}
    assert [r["turn_key"] for r in rows] == [i["turn_key"] for i in items]
    sent = {state: (questions, redact) for state, questions, redact in port.calls}
    for item in items:
        questions, redact = sent[item["state"]]
        assert questions == [(q["id"], q["kind"], q["text"], q["criteria"]) for q in item["questions"]]
        assert redact == ()  # el ítem ya viaja anonimizado
    last = rows[-1]
    assert (last["ok"], last["model"], last["error"]) == (True, JEV_MODEL, None)
    assert last["answers"]["thread.answer"] == "si" and last["confidence"] == {**last["confidence"], "thread.answer": 0.8}
    assert last["answers"]["topic.precio"] in (0.1, 0.9)


def _simple_item(key: str, state: str) -> dict:
    return {"turn_key": key, "state": state, "questions": [_question("topic.precio")]}


async def test_a_failure_in_one_turn_does_not_stop_the_bank(monkeypatch) -> None:
    items = [_simple_item("t0", "hola"), _simple_item("t1", "BOOM")]
    _install(monkeypatch, _Oracle(boom_in="BOOM"))

    rows = await ask_bank(items, profile_id="jev-v2")

    assert [(r["turn_key"], r["ok"]) for r in rows] == [("t0", True), ("t1", False)]
    assert "se cayó la conexión" in rows[1]["error"] and rows[1]["answers"] == {}


async def test_when_jev_fails_the_reason_is_kept(monkeypatch) -> None:
    _install(monkeypatch, _Oracle(error="timeout"))

    rows = await ask_bank([_simple_item("t0", "hola")], profile_id="jev-v2")

    assert [(r["ok"], r["error"], r["answers"]) for r in rows] == [(False, "timeout", {})]


@pytest.mark.parametrize("profile_id", ["jev-v1", "jev-v2"])
def test_every_item_is_json(tmp_path: Path, profile_id: str) -> None:
    bench = _write_bench(tmp_path)
    for case in build_cases(bench, sales_workspace=WS).cases:
        json.dumps(build_item(case, EVENTS, profile_id=profile_id), ensure_ascii=False)


def test_the_bank_imports_without_temporal() -> None:
    """PURO: importar el banco no carga Temporal (lo usan el CLI del operador
    y los tests sin un worker)."""
    code = "import sys, src.plugins.chats.agent.sales_lab.reference_bank; print('temporalio' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[4], check=True
    )

    assert out.stdout.strip() == "False"
