"""Lo que el premortem de los paquetes de decisión encontró (2026-10-02).

La promesa del certificador es «si compila, corre»: un paquete que pasa
`decisions check` no puede fallar en producción de una forma que el
certificador podía ver. Estos son los huecos que el premortem encontró, cada
uno con el caso que lo reproduce:

* la REGLA de la baja legal se podía apagar (DB012 solo miraba el piso);
* una fila del contrato del turno que falla al evaluarse apagaba el contrato
  del asunto (y el certificador no exigía preguntar antes si la respuesta
  llegó);
* una llave repetida en el YAML se quedaba con la última, en silencio;
* `criteria` sin comillas en el turno llegaba a Jev como «True»/«False»;
* en una pregunta de opciones, `no:` sin comillas se volvía la opción «false»;
* la pregunta de cada ítem podía repetir id (todas las oraciones con la
  respuesta de la primera) o meter el texto del cliente en el id;
* un resultado literal mutable (`{zona: …}`) se compartía entre decisiones.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.decisions import check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import BAJA, CATALOG, _write
from tests.platform.decisions.test_bundle_turn import TURN, _bundle


def _codes(tmp_path: Path, capabilities: dict[str, dict[str, Any]], **kw: Any) -> list[str]:
    return [str(d) for d in check_bundle(*_write(tmp_path, capabilities, **kw))]


# ── la regla de la baja legal ───────────────────────────────────────────────


def test_the_rule_of_a_legal_capability_cannot_be_changed(tmp_path: Path) -> None:
    catalog = {**CATALOG, "required_rules": {"baja": "opt_out_text"}}
    baja = {**BAJA, "rule": {"builtin": "constant_false"}}

    codes = _codes(tmp_path, {"baja": baja}, catalog=catalog)

    assert any(c.startswith("DB012 capabilities/baja.yaml: rule.builtin") for c in codes), codes


def test_the_required_rule_kept_certifies(tmp_path: Path) -> None:
    catalog = {**CATALOG, "required_rules": {"baja": "opt_out_text"}}

    assert _codes(tmp_path, {"baja": BAJA}, catalog=catalog) == []


# ── el contrato del turno no se apaga por una fila que falla ───────────────


def _turn_with_contract(rows: list[dict[str, Any]]) -> dict[str, Any]:
    turn = copy.deepcopy(TURN)
    turn["contract"] = rows
    turn["examples"]["contract"] = [{"topics": ["catalogo"], "expect": [{"topic": "catalogo", "any_of": ["show_catalog"]}]}]
    return turn


def test_a_contract_row_that_reads_an_answer_without_asking_if_it_came_does_not_compile(tmp_path: Path) -> None:
    turn = _turn_with_contract([
        {"topic": "catalogo", "any_of": ["show_catalog"], "nudge": "Usa show_catalog."},
        {"topic": "envio", "when": "p['envio.costo'] < th['detect']", "any_of": []},   # sin `'envio.costo' in p`
        {"topic": "envio", "any_of": ["rates"], "nudge": "Usa rates."},
    ])

    codes = [str(d) for d in check_bundle(*_bundle(tmp_path, turn))]

    assert any(c.startswith("DB005 turn.yaml: contract[1].when") for c in codes), codes


def test_at_runtime_a_contract_row_that_fails_goes_on_to_the_next_row(tmp_path: Path) -> None:
    """Defensa en ejecución: la fila que falla no decide; decide la siguiente
    del asunto (antes, el asunto dejaba de pedir tool)."""
    from src.platform.decisions.expressions import CelExpressions
    from src.platform.decisions.turn import CompiledContractRow

    turn = load_bundle(*_bundle(tmp_path, TURN)).turn
    failing = CelExpressions().compile("p['envio.costo'] < th['detect']", {"p": "map<string,double>", "th": "map<string,double>"})
    rows = (
        CompiledContractRow("envio", failing, (), None),
        CompiledContractRow("envio", None, ("rates",), "Usa rates."),
    )
    patched = type(turn)(turn.spec, turn.questionnaire, rows, turn.verify_rows, turn.bundle, turn.domain)

    assert patched.required(["envio"], p={}, inp={"stage": None}) == [
        {"topic": "envio", "any_of": ["rates"], "nudge": "Usa rates."}
    ]


def test_a_verification_row_that_reads_p_before_asking_if_it_came_does_not_compile(tmp_path: Path) -> None:
    turn = copy.deepcopy(TURN)
    turn["verify_decide"] = [
        {"when": "item.p >= th['covered']", "then": "covered"},
        {"otherwise": "doubt"},
    ]

    codes = [str(d) for d in check_bundle(*_bundle(tmp_path, turn))]

    assert any(c.startswith("DB005 turn.yaml: verify_decide[0].when") for c in codes), codes


# ── el YAML ─────────────────────────────────────────────────────────────────


def test_a_repeated_key_in_the_yaml_does_not_compile(tmp_path: Path) -> None:
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA})
    path = bundle_dir / "capabilities" / "baja.yaml"
    path.write_text(path.read_text(encoding="utf-8") + 'thresholds: {"yes": 0.5, "no": 0.15}\n', encoding="utf-8")

    codes = [str(d) for d in check_bundle(bundle_dir, catalog_path)]

    assert any(c.startswith("DB001 capabilities/baja.yaml") and "thresholds" in c for c in codes), codes


def test_turn_criteria_without_quotes_reach_jev_as_true_and_false(tmp_path: Path) -> None:
    turn = copy.deepcopy(TURN)
    question = turn["questionnaire"]["questions"][4]
    question["criteria"] = {True: "sí", False: "no"}

    compiled = load_bundle(*_bundle(tmp_path, turn)).turn

    assert compiled is not None
    assert compiled.questionnaire["questions"][4]["criteria"] == {"true": "sí", "false": "no"}


def test_an_unquoted_no_option_of_a_choice_question_does_not_compile(tmp_path: Path) -> None:
    """En YAML 1.1 `no:` es false: la opción «no» se volvía «false» y la fila
    `== 'no'` quedaba muerta. En una pregunta de opciones, las llaves van con comillas."""
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA})
    path = bundle_dir / "capabilities" / "baja.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec["questions"].append({"id": "baja.como", "kind": "choice", "text": "¿Cómo lo pide?", "criteria": {"si": "sí", "no": "no"}})
    text = yaml.safe_dump(spec, allow_unicode=True).replace("'no': 'no'", "no: 'no'")
    path.write_text(text, encoding="utf-8")

    codes = [str(d) for d in check_bundle(bundle_dir, catalog_path)]

    assert any(c.startswith("DB001 capabilities/baja.yaml: questions[1]") for c in codes), codes


# ── la pregunta de cada ítem ────────────────────────────────────────────────


def _each_capability(each_id: str) -> dict[str, Any]:
    from tests.platform.decisions.test_bundle_each import PERSONA

    return {**copy.deepcopy(PERSONA), "each": {**PERSONA["each"], "id": each_id}}


@pytest.mark.parametrize("each_id", ["persona.frase", "persona.{text}"])
def test_each_item_question_needs_its_own_safe_id(tmp_path: Path, each_id: str) -> None:
    from tests.platform.decisions.test_bundle_each import CATALOG4 as EACH_CATALOG

    capability = _each_capability(each_id)

    codes = [str(d) for d in check_bundle(*_write(tmp_path, {capability["capability"]: capability}, catalog=EACH_CATALOG))]

    assert any(c.startswith(f"DB005 capabilities/{capability['capability']}.yaml: each.id") for c in codes), codes


# ── un resultado no se comparte entre decisiones ───────────────────────────


def test_a_literal_result_is_not_shared_between_decisions(tmp_path: Path) -> None:
    capability = {
        **BAJA,
        "value": "{zona: string}",
        "rule": {"builtin": "constant_zone"},
        "decide": [{"when": "!('baja.pide' in p)", "then": "doubt"}, {"otherwise": {"zona": "bogota"}}],
        "floor": {"builtin": "jev"},
        "same": {"builtin": "same_any"},
        "examples": [{"answers": {"baja.pide": 0.9}, "expect": {"zona": "bogota"}}, {"answers": {}, "expect": "doubt"}],
    }
    catalog = {**CATALOG, "builtins": {**CATALOG["builtins"], "constant_zone": {"kind": "rule"}, "same_any": {"kind": "same"}},
               "required_floors": {}}
    table = load_bundle(*_write(tmp_path, {"baja": capability}, catalog=catalog)).capability("baja")

    first = table.decide(answers={"baja.pide": 0.9})
    first["zona"] = "medellin"

    assert table.decide(answers={"baja.pide": 0.9}) == {"zona": "bogota"}


# ── un error de la tabla no se disfraza de duda ─────────────────────────────

_TYPE_BUG = {
    **BAJA,
    "input": "Reloj",
    "rule": {"builtin": "constant_false"},
    "state": None,
    "decide": [
        {"when": "!('baja.pide' in p)", "then": "doubt"},
        # `inp.now + 1` con `now` double: CEL no suma double + int (un bug del paquete).
        {"when": "inp.now + 1 > 0 && p['baja.pide'] >= th['yes']", "then": True},
        {"otherwise": False},
    ],
    "floor": {"builtin": "jev"},
    "examples": [
        {"answers": {}, "expect": "doubt"},
        {"answers": {"baja.pide": 0.9}, "input": {"now": 1}, "expect": True},
        {"answers": {"baja.pide": 0.9}, "input": {"now": 1.5}, "expect": "doubt"},
    ],
}
_CLOCK_CATALOG = {**CATALOG, "inputs": {"Inbound": {}, "Reloj": {"now": "any"}}, "required_floors": {}}


def test_an_example_that_only_passes_through_an_error_does_not_certify(tmp_path: Path) -> None:
    """`expect: doubt` pasaba aunque la tabla reventara: un ejemplo tiene que
    pasar por una fila, no por un error."""
    codes = _codes(tmp_path, {"baja": _TYPE_BUG}, catalog=_CLOCK_CATALOG)

    assert any(c.startswith("DB010 capabilities/baja.yaml: examples[2]") for c in codes), codes


def test_a_table_error_is_a_warning_with_the_bundle_and_the_row(tmp_path: Path) -> None:
    import structlog

    from src.platform.decisions import DOUBT

    spec = {**_TYPE_BUG, "examples": _TYPE_BUG["examples"][:2]}
    table = load_bundle(*_write(tmp_path, {"baja": spec}, catalog=_CLOCK_CATALOG)).capability("baja")

    with structlog.testing.capture_logs() as logs:
        missing = table.decide(answers={})
        broken = table.decide(answers={"baja.pide": 0.9}, inp={"now": 1.5})

    assert missing is DOUBT and broken is DOUBT
    errors = [e for e in logs if e["event"] == "decision_bundle.row_error"]
    assert len(errors) == 1, logs
    assert (errors[0]["log_level"], errors[0]["bundle"], errors[0]["capability"], errors[0]["row"]) == (
        "warning", "tienda-ventas@1", "baja", 1
    )


# ── un literal que no es una opción de la pregunta ──────────────────────────

_ETIQUETA = {
    **BAJA,
    "capability": "baja",
    "questions": [
        *BAJA["questions"],
        {"id": "baja.como", "kind": "choice", "text": "¿Cómo lo pide?",
         "criteria": {"explicito": "lo dice", "implicito": "lo insinúa"}},
    ],
}


@pytest.mark.parametrize(
    "condition",
    [
        "'baja.como' in choice && choice['baja.como'] == 'explicto'",       # mal escrita
        "'baja.como' in choice && \"implicto\" == choice['baja.como']",
        "'baja.como' in choice && choice['baja.como'] in ['explicito', 'otro']",
    ],
)
def test_comparing_a_choice_with_an_option_it_does_not_have_does_not_compile(tmp_path: Path, condition: str) -> None:
    spec = {**_ETIQUETA, "decide": [{"when": condition, "then": True}, *BAJA["decide"]]}

    codes = _codes(tmp_path, {"baja": spec})

    assert any(c.startswith("DB005 capabilities/baja.yaml: decide[0].when") for c in codes), codes


def test_comparing_a_choice_with_its_own_options_certifies(tmp_path: Path) -> None:
    spec = {
        **_ETIQUETA,
        "decide": [{"when": "'baja.como' in choice && choice['baja.como'] in ['explicito']", "then": True}, *BAJA["decide"]],
        "examples": [*BAJA["examples"], {"answers": {"baja.como": {"choice": "explicito", "p": 0.9}}, "expect": True}],
    }

    assert _codes(tmp_path, {"baja": spec}) == []


def test_a_contract_row_with_a_stage_that_does_not_exist_does_not_compile(tmp_path: Path) -> None:
    turn = copy.deepcopy(TURN)
    turn["contract"][3] = {"topic": "talla", "when": "inp.stage == 'etapa_bb'", "any_of": ["picker"], "nudge": "Usa picker."}

    codes = [str(d) for d in check_bundle(*_bundle(tmp_path, turn))]

    assert any(c.startswith("DB015 turn.yaml: contract[3].when") for c in codes), codes


# ── un paquete al que le falta una capacidad que el código pide ─────────────


def test_a_bundle_without_a_capability_the_code_asks_for_does_not_compile(tmp_path: Path) -> None:
    """Antes el resolutor corría en silencio la clase de Python (la
    inteligencia de velas) para la que faltaba."""
    catalog = {**CATALOG, "capabilities": ["baja", "cortesia"]}

    codes = _codes(tmp_path, {"baja": BAJA}, catalog=catalog)

    assert any(c.startswith("DB003 bundle.yaml: capabilities") and "cortesia" in c for c in codes), codes


# ── el estado de una capacidad por ítems recibe los ítems ───────────────────


def test_an_items_capability_needs_a_state_that_takes_the_items(tmp_path: Path) -> None:
    """El motor le pasa `items=` al estado de una capacidad por ítems: uno que
    no los recibe certificaba y en ejecución lanzaba TypeError (la guarda caía)."""
    from tests.platform.decisions.test_bundle_each import CATALOG4, PERSONA

    catalog = {**copy.deepcopy(CATALOG4), "items_to_state": True}
    catalog["builtins"]["plain_text"] = {"kind": "state"}
    catalog["builtins"]["numbered"] = {"kind": "state", "takes_items": True}
    broken = {**copy.deepcopy(PERSONA), "state": {"builtin": "plain_text"}}
    fine = {**copy.deepcopy(PERSONA), "state": {"builtin": "numbered"}}

    codes = [str(d) for d in check_bundle(*_write(tmp_path / "a", {"persona": broken}, catalog=catalog))]
    assert any(c.startswith("DB004 capabilities/persona.yaml: state.builtin") for c in codes), codes
    assert check_bundle(*_write(tmp_path / "b", {"persona": fine}, catalog=catalog)) == []


def test_a_bundle_calibrated_for_an_oracle_that_does_not_exist_does_not_compile(tmp_path: Path) -> None:
    """`oracle:` del paquete era decorativo: uno inventado certificaba."""
    codes = _codes(tmp_path, {"baja": BAJA}, bundle={"oracle": "jev-no-existe"})

    assert any(c.startswith("DB016 bundle.yaml: oracle") for c in codes), codes


def test_every_row_of_the_table_is_decided_by_some_example(tmp_path: Path) -> None:
    """Regla 2 del §12 («un ejemplo por fila de `decide`»), que nada exigía:
    14 de las 115 filas de `ventas` no las decidía ningún ejemplo (un literal
    mal escrito en una de ellas certificaba)."""
    baja = {**BAJA, "examples": [e for e in BAJA["examples"] if e["expect"] is not False]}

    codes = _codes(tmp_path, {"baja": baja})

    assert any(c.startswith("DB010 capabilities/baja.yaml: decide[2]") for c in codes), codes


def test_a_bundle_id_longer_than_the_lab_accepts_does_not_compile(tmp_path: Path) -> None:
    """El laboratorio fija paquetes de hasta 40 caracteres (`B@<id>`): uno más
    largo certificaba, se podía promover y no se podía comparar."""
    long_id = "v" + "x" * 40
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA}, bundle={"id": long_id})
    bundle_dir = bundle_dir.rename(bundle_dir.parent / long_id)

    codes = [str(d) for d in check_bundle(bundle_dir, catalog_path)]

    assert any(c.startswith("DB001 bundle.yaml: id") for c in codes), codes


# ── lecturas que el certificador no veía (P-H10) ────────────────────────────


@pytest.mark.parametrize(
    "condition",
    [
        "(choice)['baja.como'] == 'explicito'",          # entre paréntesis no se veía la llave
        "('baja.como') in choice",                       # el `in` con el literal entre paréntesis
        "choice.exists(k, k == 'baja.comoo')",           # el mapa entero: la llave mal escrita pasaba
        "size(p) > 0",                                   # cuántas respuestas llegaron, no cuáles
    ],
)
def test_reading_the_answers_without_a_literal_key_does_not_compile(tmp_path: Path, condition: str) -> None:
    """Una lectura que el certificador no ve no se valida: la llave mal
    escrita compila y en ejecución la fila nunca se cumple (o falla)."""
    spec = {**_ETIQUETA, "decide": [{"when": condition, "then": True}, *BAJA["decide"]]}

    codes = _codes(tmp_path, {"baja": spec})

    assert any(c.startswith("DB005 capabilities/baja.yaml: decide[0].when") for c in codes), codes


def test_a_contract_row_that_reads_the_answers_without_a_literal_key_does_not_compile(tmp_path: Path) -> None:
    turn = copy.deepcopy(TURN)
    turn["contract"][3] = {"topic": "talla", "when": "size(choice) > 0", "any_of": ["picker"], "nudge": "Usa picker."}

    codes = [str(d) for d in check_bundle(*_bundle(tmp_path, turn))]

    assert any(c.startswith("DB005 turn.yaml: contract[3].when") for c in codes), codes


@pytest.mark.parametrize("expr", ["dom.vocabulary.variant_exampels", "dom['vocabulary']['variant_exampels']", "dom.store_name.texto"])
def test_a_domain_field_inside_a_section_is_checked_too(tmp_path: Path, expr: str) -> None:
    from tests.platform.decisions.test_bundle_domain import DESPEDIDA, DOMAIN
    from tests.platform.decisions.test_bundle_domain import _codes as _domain_codes

    rows = [{"when": "!('baja.pide' in p)", "then": "doubt"}, {"otherwise": {"expr": expr}}]

    codes = _domain_codes(tmp_path, DOMAIN, capability={**DESPEDIDA, "decide": rows})

    assert any(c.startswith("DB005 capabilities/despedida.yaml: decide[1]") for c in codes), codes


def test_a_domain_field_inside_its_section_certifies(tmp_path: Path) -> None:
    from tests.platform.decisions.test_bundle_domain import DESPEDIDA, DOMAIN
    from tests.platform.decisions.test_bundle_domain import _codes as _domain_codes

    rows = [{"when": "!('baja.pide' in p)", "then": "doubt"}, {"otherwise": {"expr": "dom.vocabulary.variant_examples"}}]
    examples = [{"answers": {"baja.pide": 0.9}, "expect": "'lavanda', 'el morado'"}, {"answers": {}, "expect": "doubt"}]

    assert _domain_codes(tmp_path, DOMAIN, capability={**DESPEDIDA, "decide": rows, "examples": examples}) == []


# ── una opción sin distribución no es una opción segura (P-H12) ─────────────


def test_a_choice_without_probabilities_is_read_with_its_confidence_not_with_an_invented_one() -> None:
    """Jev responde `choice` con `confidence: 0.6` y sin `probabilities`: el
    adaptador inventaba `probs = {opción: 1.0}` y la tabla pasaba el umbral de
    0,85 con una respuesta de 0,6."""
    from src.platform.decisions.engine import answers_from_result
    from src.platform.perception.adapters._validate import check_answer
    from src.platform.perception.ports import PerceptionResult, TypedQuestion

    question = TypedQuestion(id="baja.como", kind="choice", text="¿Cómo lo pide?",
                             criteria={"explicito": "lo dice", "implicito": "lo insinúa"})
    answer = check_answer(question, {"type": "choice", "choice": "explicito", "confidence": 0.6})

    read = answers_from_result([question], PerceptionResult(ok=True, answers=(answer,)))

    assert read == {"baja.como": ("explicito", 0.6)}


# ── `{}` en la cobertura: el asunto queda SIEMPRE sin atender (V-H9) ────────


def test_the_missing_coverage_message_says_what_an_empty_rule_really_does(tmp_path: Path) -> None:
    """El mensaje sugería `{}` «si nada lo atiende», como si `{}` dejara el
    asunto sin juzgar. Es al revés: una regla vacía nunca se cumple y pide otra
    ronda del LLM en cada turno con ese asunto (el bug de `promocion`)."""
    turn = copy.deepcopy(TURN)
    del turn["coverage"]["talla"]

    [message] = [str(d) for d in check_bundle(*_bundle(tmp_path, turn)) if str(d).startswith("DB015 turn.yaml: coverage.talla")]

    assert "siempre sin atender" in message and "ronda" in message, message


# ── los campos de cada ítem dentro de `items.filter(i, …)` (P-H10) ──────────


@pytest.mark.parametrize(
    ("name", "rows", "extra", "where"),
    [
        ("persona", [{"when": "!items.exists(i, has(i.p))", "then": "doubt"},
                     {"otherwise": {"expr": "items.filter(i, has(i.p) && i.pp >= th['yes']).map(i, i.index)"}}],
         {}, "decide[1]"),
        ("rescate", [{"when": "items.exists(i, !has(i.choice) || i.cnof < th['choice'])", "then": "doubt"},
                     {"otherwise": {"expr": "items.filter(i, i.choice == 'cliente').map(i, i.text).join('\\n\\n')"}}],
         {}, "decide[0].when"),
        ("persona", [{"when": "!items.exists(i, has(i.p))", "then": "doubt"},
                     {"otherwise": {"expr": "vars.asked.filter(j, j.pp >= th['yes']).map(j, j.index)"}}],
         {"vars": {"asked": "items.filter(i, has(i.p))"}}, "decide[1]"),
    ],
    ids=["filter", "exists", "a-var-that-is-a-list-of-items"],
)
def test_a_field_that_the_items_do_not_have_does_not_compile(
    tmp_path: Path, name: str, rows: list[dict[str, Any]], extra: dict[str, Any], where: str
) -> None:
    """`has(i.pp)` es false para siempre y `i.cnof < …` falla en ejecución: la
    fila nunca decide lo que se diseñó, y certificaba."""
    from tests.platform.decisions.test_bundle_each import _codes as _each_codes
    from tests.platform.decisions.test_bundle_each import PERSONA, RESCATE

    spec = {**{"persona": PERSONA, "rescate": RESCATE}[name], "decide": rows, **extra}

    codes = _each_codes(tmp_path, {name: spec})

    assert any(c.startswith(f"DB005 capabilities/{name}.yaml: {where}") and (".pp:" in c or ".cnof:" in c) for c in codes), codes


# ── cada decisión que el código pide trae su explicación (2026-10-02) ──────


_ABOUT_CATALOG = {**CATALOG, "capabilities": ["baja"], "places": {"ingest": "Al leer cada mensaje del cliente"}}
_ABOUT = {"name": "Baja de mensajes", "where": ["ingest"], "solves": "Reconoce cuando el cliente pide no recibir más mensajes."}


def test_a_decision_the_code_asks_for_without_its_explanation_does_not_compile(tmp_path: Path) -> None:
    """Calidad LLM muestra cada decisión con dónde actúa y qué resuelve: una
    decisión nueva no puede llegar muda a la pantalla."""
    codes = _codes(tmp_path, {"baja": BAJA}, catalog=_ABOUT_CATALOG)

    assert any(c.startswith("DB003 builtins.yaml: about") and "baja" in c for c in codes), codes


def test_a_decision_placed_somewhere_the_catalog_does_not_declare_does_not_compile(tmp_path: Path) -> None:
    catalog = {**_ABOUT_CATALOG, "about": {"baja": {**_ABOUT, "where": ["en_otro_lado"]}}}

    codes = _codes(tmp_path, {"baja": BAJA}, catalog=catalog)

    assert any(c.startswith("DB003 builtins.yaml: about.baja.where") for c in codes), codes


def test_the_compiled_bundle_carries_where_each_decision_acts(tmp_path: Path) -> None:
    catalog = {**_ABOUT_CATALOG, "about": {"baja": _ABOUT}}

    bundle = load_bundle(*_write(tmp_path, {"baja": BAJA}, catalog=catalog))

    assert bundle.places == (("ingest", "Al leer cada mensaje del cliente"),)
    assert bundle.about["baja"].where == ("ingest",) and bundle.about["baja"].solves.startswith("Reconoce")
