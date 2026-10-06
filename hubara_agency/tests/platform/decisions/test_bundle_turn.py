"""El turno en el paquete (PAQUETES_DE_DECISION.md F7).

Antes del turno, Jev contesta la ráfaga ① (qué asuntos plantea el cliente);
antes de enviar, la verificación ③ (¿la respuesta atiende cada asunto?).
Lo que eso decide era código: el cuestionario vivía aparte y las tablas de
la política (qué atiende cada asunto, qué tool lo resuelve, la guía de
etapas) eran constantes de Python. Ahora son el `turn.yaml` del paquete:

* el cuestionario de la ráfaga, con sus `when` sobre hechos que declara el
  catálogo;
* el contrato asunto → tools como FILAS: la primera del asunto que se cumple
  decide (`any_of: []` = no pide tool), con condiciones CEL;
* la banda de ③ por asunto como filas (covered | missing | doubt): el motor
  junta las bandas (cualquier missing → complemento; cualquier doubt →
  pendiente);
* ejemplos que el certificador corre.

El catálogo declara el vocabulario (`turn:`): políticas con los umbrales y
las preguntas que leen, hechos y sus valores, etapas, datos y tools. Un
nombre que no existe no compila (DB015), como en las capacidades.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.decisions import BundleError, check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import BAJA, CATALOG, _write

YES_NO = {"true": "sí", "false": "no"}

TURN_CATALOG: dict[str, Any] = {
    **CATALOG,
    "turn": {
        "policies": {
            "turno-x": {
                "thresholds": ["detect", "covered", "given"],
                "reads": {"noul": ["thread.answers_bot"], "choice": ["thread.bot_asked", "thread.answer"]},
                "topic_question": "topic.{topic}",
                "message_question": "msg.{k}.topic",
                "verify_question": "cover.{topic}",
                "reading": {"asked": "thread.bot_asked", "answer": "thread.answer"},
            },
        },
        "facts": {"has_context": [True, False], "stage": [None, "etapa_a", "etapa_b"]},
        "inputs": {"stage": "string?"},
        "stages": ["etapa_a", "etapa_b"],
        "slots": ["ciudad", "talla"],
        "tools": ["show_catalog", "search", "rates", "picker", "human"],
    },
}

TURN: dict[str, Any] = {
    "policy": "turno-x",
    "thresholds": {"detect": 0.70, "covered": 0.70, "given": 0.85},
    "questionnaire": {
        "id": "rafaga-z1",
        "topics": [
            {"id": "catalogo", "label": "catálogo", "hint": "pide ver el catálogo"},
            {"id": "envio", "label": "envío", "hint": "pregunta por el envío"},
            {"id": "talla", "label": "tallas", "hint": "pregunta por las tallas"},
        ],
        "questions": [
            {"each_topic": {"id": "topic.{topic}", "kind": "noul", "text": "¿En ESTE TURNO el cliente {hint}?",
                            "criteria": YES_NO}},
            {"id": "thread.bot_asked", "kind": "choice", "when": {"has_context": True},
             "text": "¿Qué le preguntó el asesor?", "criteria": {"elegir_talla": "que elija talla", "nada": "nada"}},
            {"id": "thread.answers_bot", "kind": "noul", "when": {"has_context": True},
             "text": "¿Responde la pregunta del asesor?", "criteria": YES_NO},
            {"id": "thread.answer", "kind": "choice", "when": {"has_context": True},
             "text": "¿Qué responde?", "criteria": {"si": "sí", "no": "no", "otra": "otra cosa"}},
            {"id": "envio.costo", "kind": "noul", "text": "¿Pregunta cuánto cuesta el envío?", "criteria": YES_NO},
            {"id": "talla.elige", "kind": "noul", "when": {"stage": ["etapa_b"]}, "text": "¿Elige talla?",
             "criteria": YES_NO},
            {"each_message": {"id": "msg.{k}.topic", "kind": "choice", "text": "¿Asunto del mensaje [{k}]?",
                              "criteria": {"from_topics": "label", "extra": {"ninguno": "ningún asunto"}}}},
        ],
        "verify": {"id": "cover.{topic}", "kind": "noul", "text": "¿La respuesta atiende «{label}»{where}",
                   "where_msg": " del mensaje [{msg}]?", "where_none": "?", "criteria": YES_NO},
        "state": {
            "sections": {"context": "CONTEXTO", "facts": "HECHOS", "turn": "ESTE TURNO", "quoted": "(cita: «{text}»)"},
            "message": "[{k}] ({offset}) {text}",
            "pending": "Pendientes: {pending}",
        },
        "reply_state": {"header": "Respuesta:", "empty": "(sin texto)", "components": "Tarjetas: {components}"},
    },
    "coverage": {
        "catalogo": {"tools": ["show_catalog"], "words": ["catalogo"]},
        "envio": {"tools": ["rates"], "words": ["envio"]},
        "talla": {"tools": ["picker"], "words": ["talla"], "any_text": False},
    },
    "reading": {
        "answered": {"elegir_talla": {"si": "Elige la talla que le ofreciste."}},
        "any": {"elegir_talla": "Responde tu pregunta de tallas."},
    },
    "contract": [
        {"topic": "catalogo", "any_of": ["show_catalog"], "nudge": "Usa show_catalog."},
        {"topic": "envio", "when": "'envio.costo' in p && p['envio.costo'] < th['detect']", "any_of": []},
        {"topic": "envio", "any_of": ["rates"], "nudge": "Para el costo usa rates."},
        {"topic": "talla", "when": "inp.stage == 'etapa_b'", "any_of": ["picker"], "nudge": "Usa picker."},
        {"topic": "talla", "any_of": ["picker", "search"], "nudge": "Usa picker o search."},
    ],
    "verify_decide": [
        {"when": "!('p' in item)", "then": "doubt"},
        {"when": "item.p >= th['covered']", "then": "covered"},
        {"when": "item.p <= 1.0 - th['covered']", "then": "missing"},
        {"otherwise": "doubt"},
    ],
    "guide": {
        "choice_topics": ["talla"],
        "no_sale_stages": ["etapa_a"],
        "stagnant_turns": 3,
        "slot_labels": {"ciudad": "ciudad", "talla": "talla"},
    },
    "examples": {
        "contract": [
            {"topics": ["envio"], "answers": {"envio.costo": 0.2}, "expect": []},
            {"topics": ["envio"], "expect": [{"topic": "envio", "any_of": ["rates"]}]},
            {"topics": ["talla"], "input": {"stage": "etapa_b"}, "expect": [{"topic": "talla", "any_of": ["picker"]}]},
        ],
        "verify": [
            {"topics": ["catalogo", "envio"], "answers": {"cover.catalogo": 0.9, "cover.envio": 0.1},
             "expect": {"decision": "complement", "missing": ["envio"]}},
            {"topics": ["catalogo"], "answers": {}, "expect": {"decision": "pending", "missing": ["catalogo"]}},
        ],
    },
}


def _bundle(tmp_path: Path, turn: dict[str, Any] | None, *, catalog: dict[str, Any] = TURN_CATALOG) -> tuple[Path, Path]:
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA}, catalog=catalog)
    if turn is not None:
        (bundle_dir / "turn.yaml").write_text(yaml.safe_dump(turn, allow_unicode=True), encoding="utf-8")
    return bundle_dir, catalog_path


def _turn(**changes: Any) -> dict[str, Any]:
    turn = copy.deepcopy(TURN)
    turn.update(changes)
    return turn


def _compiled(tmp_path: Path, turn: dict[str, Any] = TURN) -> Any:
    compiled = getattr(load_bundle(*_bundle(tmp_path, turn)), "turn", None)
    assert compiled is not None, "el paquete no trae su turno"
    return compiled


def _diagnostics(tmp_path: Path, turn: dict[str, Any] | None, **kw: Any) -> list[str]:
    return [str(d) for d in check_bundle(*_bundle(tmp_path, turn, **kw))]


# ── lo que compila ──────────────────────────────────────────────────────────


def test_a_certified_turn_travels_with_its_bundle(tmp_path: Path) -> None:
    turn = _compiled(tmp_path)

    assert turn.policy == "turno-x"
    assert turn.thresholds == {"detect": 0.70, "covered": 0.70, "given": 0.85}
    # El cuestionario viaja tal cual lo escribió el paquete (lo interpreta el plugin).
    assert turn.questionnaire == TURN["questionnaire"]
    assert turn.coverage["catalogo"] == {"tools": ["show_catalog"], "words": ["catalogo"], "any_text": False}
    assert turn.reading_answered == {("elegir_talla", "si"): "Elige la talla que le ofreciste."}
    assert turn.reading_any == {"elegir_talla": "Responde tu pregunta de tallas."}
    assert turn.guide.choice_topics == ["talla"] and turn.guide.stagnant_turns == 3


def test_a_bundle_without_turn_in_its_catalog_has_none(tmp_path: Path) -> None:
    assert getattr(load_bundle(*_bundle(tmp_path, None, catalog=CATALOG)), "turn", "falta") is None


def test_the_contract_first_row_of_the_topic_that_holds_decides(tmp_path: Path) -> None:
    turn = _compiled(tmp_path)

    # Sin la pregunta del costo: la fila de siempre.
    assert turn.required(["envio"], p={}, inp={"stage": None}) == [
        {"topic": "envio", "any_of": ["rates"], "nudge": "Para el costo usa rates."}
    ]
    # Pregunta otra cosa del envío: la fila con `any_of: []` decide y no pide tool.
    assert turn.required(["envio"], p={"envio.costo": 0.2}, inp={"stage": None}) == []
    assert turn.required(["envio"], p={"envio.costo": 0.7}, inp={"stage": None})[0]["any_of"] == ["rates"]
    # La etapa decide la tool de las tallas.
    assert turn.required(["talla"], p={}, inp={"stage": "etapa_b"})[0]["any_of"] == ["picker"]
    assert turn.required(["talla"], p={}, inp={"stage": "etapa_a"})[0]["any_of"] == ["picker", "search"]
    assert turn.required(["talla"], p={}, inp={"stage": None})[0]["any_of"] == ["picker", "search"]
    # En el orden de los asuntos; un asunto sin filas no pide nada.
    assert [r["topic"] for r in turn.required(["talla", "otro", "catalogo"], p={}, inp={"stage": None})] == [
        "talla", "catalogo"
    ]


@pytest.mark.parametrize(
    ("p", "decision", "missing", "covered"),
    [
        ({"cover.catalogo": 0.9, "cover.envio": 0.9}, "send", [], {"catalogo": 0.9, "envio": 0.9}),
        ({"cover.catalogo": 0.9, "cover.envio": 0.1}, "complement", ["envio"], {"catalogo": 0.9, "envio": 0.1}),
        ({"cover.catalogo": 0.5, "cover.envio": 0.9}, "pending", ["catalogo"], {"catalogo": 0.5, "envio": 0.9}),
        ({"cover.catalogo": 0.5, "cover.envio": 0.1}, "complement", ["envio"], {"catalogo": 0.5, "envio": 0.1}),
        ({"cover.envio": 0.9}, "pending", ["catalogo"], {"envio": 0.9}),
        # Los bordes: ≥ covered atiende; ≤ 1 − covered falta con claridad.
        ({"cover.catalogo": 0.70, "cover.envio": 0.30000000000000004}, "complement", ["envio"],
         {"catalogo": 0.70, "envio": 0.30000000000000004}),
        ({"cover.catalogo": 0.69, "cover.envio": 0.31}, "pending", ["catalogo", "envio"], {"catalogo": 0.69, "envio": 0.31}),
    ],
)
def test_verification_bands_by_topic_and_the_engine_joins_them(
    tmp_path: Path, p: dict[str, float], decision: str, missing: list[str], covered: dict[str, float]
) -> None:
    turn = _compiled(tmp_path)

    out = turn.verify([("catalogo", 1), ("envio", None)], p=p)

    assert (out.decision, list(out.missing), dict(out.covered)) == (decision, missing, covered)


# ── lo que no compila ───────────────────────────────────────────────────────


def _with(path: str, value: Any) -> dict[str, Any]:
    turn = copy.deepcopy(TURN)
    *head, last = path.split(".")
    node: Any = turn
    for part in head:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return turn


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        # Estructura: llave desconocida.
        (_turn(extra=1), "DB001 turn.yaml: extra"),
        # La política no existe en el catálogo.
        (_turn(policy="turno-z"), "DB015 turn.yaml: policy"),
        # Umbrales: falta uno que la política lee, sobra uno que nadie lee, fuera de [0, 1].
        (_turn(thresholds={"detect": 0.7, "covered": 0.7}), "DB015 turn.yaml: thresholds"),
        (_turn(thresholds={**TURN["thresholds"], "confidence": 0.6}), "DB015 turn.yaml: thresholds.confidence"),
        (_turn(thresholds={**TURN["thresholds"], "given": 1.5}), "DB009 turn.yaml: thresholds.given"),
        # Cuestionario: asunto repetido, hecho desconocido, valor de hecho que no existe.
        (_with("questionnaire.topics", [*TURN["questionnaire"]["topics"], TURN["questionnaire"]["topics"][0]]),
         "DB005 turn.yaml: questionnaire.topics[3].id"),
        (_with("questionnaire.questions.5.when", {"etapa": "etapa_b"}), "DB015 turn.yaml: questionnaire.questions[5].when.etapa"),
        (_with("questionnaire.questions.5.when", {"stage": ["etapa_c"]}), "DB015 turn.yaml: questionnaire.questions[5].when.stage"),
        # Una plantilla con un `{campo}` que no se llena (fallaría en el turno).
        (_with("questionnaire.verify.where_msg", " del mensaje [{mensaje}]?"), "DB005 turn.yaml: questionnaire.verify.where_msg"),
        (_with("questionnaire.state.pending", "Pendientes: {pending"), "DB005 turn.yaml: questionnaire.state.pending"),
        # Los ids que lee la política.
        (_with("questionnaire.verify.id", "cubre.{topic}"), "DB015 turn.yaml: questionnaire.verify.id"),
        (_with("questionnaire.questions.2", {**TURN["questionnaire"]["questions"][2], "id": "thread.responde"}),
         "DB015 turn.yaml: questionnaire: thread.answers_bot"),
        (_with("questionnaire.questions.1", {**TURN["questionnaire"]["questions"][1], "kind": "noul", "criteria": YES_NO}),
         "DB015 turn.yaml: questionnaire: thread.bot_asked"),
        # ②: asunto desconocido, tool desconocida, asunto sin regla.
        (_with("coverage.zapatos", {"tools": ["picker"]}), "DB015 turn.yaml: coverage.zapatos"),
        (_with("coverage.envio", {"tools": ["tarifas"]}), "DB015 turn.yaml: coverage.envio.tools"),
        (_turn(coverage={k: v for k, v in TURN["coverage"].items() if k != "talla"}), "DB015 turn.yaml: coverage.talla"),
        # Lectura: lo que preguntó el asesor o lo que responde el cliente no son opciones de sus preguntas.
        (_with("reading.any", {"elegir_color": "x"}), "DB015 turn.yaml: reading.any.elegir_color"),
        (_with("reading.answered", {"elegir_talla": {"tal_vez": "x"}}), "DB015 turn.yaml: reading.answered.elegir_talla.tal_vez"),
        # Contrato: asunto o tool desconocidos, condición que no compila, llave no declarada, fila que nunca se lee.
        (_with("contract.0", {"topic": "zapatos", "any_of": ["picker"], "nudge": "x"}), "DB015 turn.yaml: contract[0].topic"),
        (_with("contract.0", {"topic": "catalogo", "any_of": ["catalogo_pdf"], "nudge": "x"}), "DB015 turn.yaml: contract[0].any_of"),
        (_with("contract.1", {"topic": "envio", "when": "p['envio.costo'] < 'x'", "any_of": []}), "DB006 turn.yaml: contract[1].when"),
        (_with("contract.1", {"topic": "envio", "when": "p['envio.precio'] < 0.7", "any_of": []}), "DB005 turn.yaml: contract[1].when"),
        (_with("contract.1", {"topic": "envio", "when": "p['thread.bot_asked'] < 0.7", "any_of": []}), "DB011 turn.yaml: contract[1].when"),
        (_with("contract.1", {"topic": "envio", "when": "inp.etapa == 'x'", "any_of": []}), "DB005 turn.yaml: contract[1].when"),
        (_with("contract.1", {"topic": "envio", "any_of": ["rates"], "nudge": "x"}), "DB008 turn.yaml: contract[2]"),
        # Una fila que pide tool lleva su nota; una que no pide, no.
        (_with("contract.0", {"topic": "catalogo", "any_of": ["show_catalog"]}), "DB001 turn.yaml: contract[0]"),
        # ③: resultado que no es una banda, sin `otherwise`, campo del asunto que no existe.
        (_with("verify_decide.1", {"when": "item.p >= th['covered']", "then": "atendido"}), "DB007 turn.yaml: verify_decide[1].then"),
        (_turn(verify_decide=TURN["verify_decide"][:-1]), "DB008 turn.yaml: verify_decide"),
        (_with("verify_decide.1", {"when": "item.prob >= th['covered']", "then": "covered"}), "DB005 turn.yaml: verify_decide[1].when"),
        # Guía: asunto, etapa o dato que no existen.
        (_with("guide.choice_topics", ["color"]), "DB015 turn.yaml: guide.choice_topics"),
        (_with("guide.no_sale_stages", ["etapa_z"]), "DB015 turn.yaml: guide.no_sale_stages"),
        (_with("guide.slot_labels", {"cedula": "cédula"}), "DB015 turn.yaml: guide.slot_labels.cedula"),
        # Ejemplos que no dan lo esperado.
        (_with("examples.contract.0", {"topics": ["envio"], "answers": {"envio.costo": 0.2},
                                       "expect": [{"topic": "envio", "any_of": ["rates"]}]}), "DB010 turn.yaml: examples.contract[0]"),
        (_with("examples.verify.1", {"topics": ["catalogo"], "answers": {}, "expect": {"decision": "send", "missing": []}}),
         "DB010 turn.yaml: examples.verify[1]"),
    ],
)
def test_what_does_not_compile(tmp_path: Path, turn: dict[str, Any], expected: str) -> None:
    diagnostics = _diagnostics(tmp_path, turn)

    assert any(d.startswith(expected) for d in diagnostics), diagnostics


def test_the_turn_is_required_when_the_catalog_declares_it(tmp_path: Path) -> None:
    diagnostics = _diagnostics(tmp_path, None)

    assert any(d.startswith("DB015 turn.yaml") for d in diagnostics), diagnostics


def test_a_turn_without_vocabulary_in_the_catalog_does_not_compile(tmp_path: Path) -> None:
    diagnostics = _diagnostics(tmp_path, TURN, catalog=CATALOG)

    assert any(d.startswith("DB015 turn.yaml") for d in diagnostics), diagnostics


def test_a_turn_that_does_not_compile_rejects_the_bundle(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="DB015"):
        load_bundle(*_bundle(tmp_path, _turn(policy="turno-z")))
