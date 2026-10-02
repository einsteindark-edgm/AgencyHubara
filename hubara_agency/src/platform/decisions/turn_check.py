"""El certificador del turno de un paquete (`turn.yaml`, PAQUETES_DE_DECISION.md F7).

Mismos códigos que las capacidades donde el error es el mismo (estructura
DB001, llaves DB005, CEL DB006, resultado DB007, filas DB008, umbrales DB009,
ejemplos DB010, p sobre una choice DB011) y uno propio:

  DB015 el turno nombra algo que el catálogo (`turn:`) no declara —política,
        hecho o su valor, asunto, tool, etapa o dato—; un umbral que la
        política no lee (o falta uno que lee); el cuestionario no hace una
        pregunta que la política lee, o le cambia el id; o falta `turn.yaml`
        (o sobra, si el catálogo no declara turno).

Un `{campo}` de una plantilla del cuestionario que el motor no llena es
DB005: fallaría al armar la pregunta, en pleno turno.
"""
from __future__ import annotations

import string
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.platform.decisions.engine import CompiledRow, Diagnostic
from src.platform.decisions.expressions import ExpressionError, ExpressionPort
from src.platform.decisions.model import Catalog, OtherwiseRow, TurnCatalog, TurnPolicySpec, WhenRow
from src.platform.decisions.parsing import (
    _ITEM_FIELD,
    _parse,
    _read_yaml,
    _references,
    _sample,
    domain_problems,
    literal_comparisons,
    unkeyed_reads,
)
from src.platform.decisions.turn import (
    BANDS,
    VERIFY_ITEM,
    BurstQuestion,
    CompiledContractRow,
    CompiledTurn,
    EachMessageEntry,
    EachTopicEntry,
    Turn,
)

LABEL = "turn.yaml"

#: Los `{campo}` que el motor llena en cada plantilla del cuestionario.
_EACH_TOPIC_FIELDS = {"topic", "label", "hint"}
_TEMPLATES: dict[str, set[str]] = {
    "verify.id": {"topic"},
    "verify.text": {"label", "where"},
    "verify.where_msg": {"msg"},
    "state.message": {"k", "offset", "text"},
    "state.pending": {"pending"},
    "state.sections.quoted": {"text"},
    "reply_state.components": {"components"},
}
_CONTRACT_VARS = {"p": "map<string,double>", "th": "map<string,double>", "inp": "map<string,dyn>", "dom": "map<string,dyn>"}
_VERIFY_VARS = {"item": "map<string,dyn>", "th": "map<string,double>", "dom": "map<string,dyn>"}


def _fields(template: str) -> set[str] | None:
    """Los `{campo}` de una plantilla de `str.format`; None si no se puede leer."""
    try:
        return {name for _lit, name, _spec, _conv in string.Formatter().parse(template) if name is not None}
    except ValueError:
        return None


class _TurnCheck:
    def __init__(self, spec: Turn, catalog: Catalog, vocab: TurnCatalog, expressions: ExpressionPort) -> None:
        self.spec = spec
        self.catalog = catalog
        self.vocab = vocab
        self.expressions = expressions
        self.out: list[Diagnostic] = []
        self.topics = [t.id for t in spec.questionnaire.topics]
        #: id de cada pregunta que se puede leer (fijas y las de cada asunto) → su clase.
        self.kinds: dict[str, str] = {}
        #: Las opciones de cada pregunta fija de opciones.
        self.options: dict[str, list[str]] = {}

    def add(self, code: str, where: str, message: str) -> None:
        self.out.append(Diagnostic(code, f"{LABEL}: {where}", message))

    # ── la política y sus umbrales ────────────────────────────────────────

    def policy(self) -> TurnPolicySpec | None:
        policy = self.vocab.policies.get(self.spec.policy)
        if policy is None:
            self.add("DB015", "policy", f"política desconocida {self.spec.policy!r} (hay: {', '.join(self.vocab.policies)})")
        return policy

    def thresholds(self, policy: TurnPolicySpec | None) -> None:
        for name, value in self.spec.thresholds.items():
            if not 0.0 <= value <= 1.0:
                self.add("DB009", f"thresholds.{name}", f"{value} está fuera de [0, 1]")
        if policy is None:
            return
        missing = [name for name in policy.thresholds if name not in self.spec.thresholds]
        if missing:
            self.add("DB015", "thresholds", f"faltan umbrales que lee la política {self.spec.policy}: {missing}")
        for name in self.spec.thresholds:
            if name not in policy.thresholds:
                self.add("DB015", f"thresholds.{name}", f"umbral que la política {self.spec.policy} no lee (lee: {', '.join(policy.thresholds)})")

    # ── el cuestionario ───────────────────────────────────────────────────

    def _template(self, where: str, template: str, allowed: set[str]) -> None:
        fields = _fields(template)
        if fields is None:
            self.add("DB005", where, "llaves sin cerrar: un `{` o `}` que no es un campo se escribe doble (`{{`)")
            return
        unknown = sorted(fields - allowed)
        if unknown:
            self.add("DB005", where, f"campos que el motor no llena {unknown} (hay: {', '.join(sorted(allowed)) or 'ninguno'})")

    def _facts(self, where: str, when: Mapping[str, Any] | None) -> None:
        for fact, expected in (when or {}).items():
            allowed = self.vocab.facts.get(fact)
            if allowed is None:
                self.add("DB015", f"{where}.when.{fact}", f"hecho desconocido (hay: {', '.join(self.vocab.facts) or 'ninguno'})")
                continue
            values = expected if isinstance(expected, list) else [expected]
            bad = [v for v in values if not any(v is a or (type(v) is type(a) and v == a) for a in allowed)]
            if bad:
                self.add("DB015", f"{where}.when.{fact}", f"valores que el hecho no toma {bad} (toma: {allowed})")

    def questionnaire(self, policy: TurnPolicySpec | None) -> None:
        q = self.spec.questionnaire
        seen_topics: set[str] = set()
        for i, topic in enumerate(q.topics):
            if topic.id in seen_topics:
                self.add("DB005", f"questionnaire.topics[{i}].id", f"asunto repetido {topic.id!r}")
            seen_topics.add(topic.id)
        each_topic = each_message = None
        for i, entry in enumerate(q.questions):
            where = f"questionnaire.questions[{i}]"
            if isinstance(entry, EachTopicEntry):
                each_topic = entry.each_topic
                self._facts(f"{where}.each_topic", each_topic.when)
                self._template(f"{where}.each_topic.id", each_topic.id, {"topic"})
                self._template(f"{where}.each_topic.text", each_topic.text, _EACH_TOPIC_FIELDS)
                if policy is not None and each_topic.id != policy.topic_question:
                    self.add("DB015", f"{where}.each_topic.id", f"la política {self.spec.policy} lee {policy.topic_question!r}")
                for topic in self.topics:
                    self._declare(f"{where}.each_topic.id", each_topic.id.replace("{topic}", topic), each_topic.kind)
            elif isinstance(entry, EachMessageEntry):
                each_message = entry.each_message
                self._facts(f"{where}.each_message", each_message.when)
                self._template(f"{where}.each_message.id", each_message.id, {"k"})
                self._template(f"{where}.each_message.text", each_message.text, {"k"})
                if policy is not None and each_message.id != policy.message_question:
                    self.add("DB015", f"{where}.each_message.id", f"la política {self.spec.policy} lee {policy.message_question!r}")
            else:
                assert isinstance(entry, BurstQuestion)
                self._facts(where, entry.when)
                self._declare(f"{where}.id", entry.id, entry.kind)
                if entry.kind == "choice":
                    self.options[entry.id] = list(entry.criteria)
        for path, allowed in _TEMPLATES.items():
            node: Any = q
            for part in path.split("."):
                node = getattr(node, part)
            self._template(f"questionnaire.{path}", node, allowed)
        if policy is None:
            return
        if q.verify.id != policy.verify_question:
            self.add("DB015", "questionnaire.verify.id", f"la política {self.spec.policy} lee {policy.verify_question!r}")
        if each_topic is None:
            self.add("DB015", "questionnaire.questions", f"falta la pregunta de cada asunto (`each_topic`, {policy.topic_question!r})")
        if each_message is None:
            self.add("DB015", "questionnaire.questions", f"falta la pregunta de cada mensaje (`each_message`, {policy.message_question!r})")
        for kind, ids in policy.reads.items():
            for qid in ids:
                have = self.kinds.get(qid)
                if have is None:
                    self.add("DB015", f"questionnaire: {qid}", f"la política {self.spec.policy} lee esta pregunta y el cuestionario no la hace")
                elif have != kind:
                    self.add("DB015", f"questionnaire: {qid}", f"es una pregunta {have}; la política la lee como {kind}")

    def _declare(self, where: str, qid: str, kind: str) -> None:
        if qid in self.kinds:
            self.add("DB005", where, f"pregunta repetida {qid!r}")
        self.kinds[qid] = kind

    # ── ② la regla de cada asunto ─────────────────────────────────────────

    def coverage(self) -> None:
        for topic, rule in self.spec.coverage.items():
            if topic not in self.topics:
                self.add("DB015", f"coverage.{topic}", f"asunto que el cuestionario no tiene (hay: {', '.join(self.topics)})")
            unknown = sorted(set(rule.tools) - set(self.vocab.tools))
            if unknown:
                self.add("DB015", f"coverage.{topic}.tools", f"tools que el agente no tiene {unknown}")
        for topic in self.topics:
            if topic not in self.spec.coverage:
                self.add(
                    "DB015", f"coverage.{topic}",
                    "falta la regla de ② del asunto (tools o words que lo atienden; `{}` lo deja siempre sin atender "
                    "y pide otra ronda del LLM en cada turno con ese asunto)",
                )

    # ── la lectura del hilo ───────────────────────────────────────────────

    def reading(self, policy: TurnPolicySpec | None) -> None:
        reading = self.spec.reading
        if policy is None or (not reading.answered and not reading.any):
            return
        asked_q, answer_q = policy.reading.get("asked"), policy.reading.get("answer")
        if asked_q is None or answer_q is None:
            self.add("DB015", "reading", f"la política {self.spec.policy} no lee notas de lectura")
            return
        asked, answers = self.options.get(asked_q, []), self.options.get(answer_q, [])
        for key, notes in reading.answered.items():
            if key not in asked:
                self.add("DB015", f"reading.answered.{key}", f"no es una opción de {asked_q} ({', '.join(asked)})")
            for answer in notes:
                if answer not in answers:
                    self.add("DB015", f"reading.answered.{key}.{answer}", f"no es una opción de {answer_q} ({', '.join(answers)})")
        for key in reading.any:
            if key not in asked:
                self.add("DB015", f"reading.any.{key}", f"no es una opción de {asked_q} ({', '.join(asked)})")

    # ── el contrato ───────────────────────────────────────────────────────

    def _keys(self, where: str, source: str, readable: set[str]) -> bool:
        ok = True
        refs, bad = _references(source)
        for var in bad:
            self.add("DB005", where, f"{var}[…] lleva una llave literal entre comillas, p. ej. {var}['…']")
            ok = False
        for var, key in refs:
            problem = self._key_problem(var, key, readable)
            if problem is not None:
                self.add(problem[0], where, problem[1])
                ok = False
        for var in unkeyed_reads(source):
            self.add("DB005", where, f"lee {var} sin una llave literal a la vista ({var}['…'] o '…' in {var}): no se puede validar qué lee")
            ok = False
        for problem in domain_problems(source, self.catalog.domain):
            self.add("DB005", where, problem)
            ok = False
        return ok

    def _key_problem(self, var: str, key: str, readable: set[str]) -> tuple[str, str] | None:
        if var not in readable:
            return "DB005", f"aquí se lee {', '.join(sorted(readable))} (no {var})"
        if var == "th":
            if key not in self.spec.thresholds:
                return "DB005", f"umbral no declarado {key!r} (hay: {', '.join(self.spec.thresholds)})"
        elif var == "inp":
            if key not in self.vocab.inputs:
                return "DB005", f"campo no declarado {key!r} (hay: {', '.join(self.vocab.inputs) or 'ninguno'})"
        elif var == "dom":
            if key not in self.catalog.domain:
                return "DB005", f"campo del dominio no declarado {key!r} (hay: {', '.join(self.catalog.domain) or 'ninguno'})"
        elif var == "p":
            if key not in self.kinds:
                return "DB005", f"pregunta no declarada {key!r}"
            if self.kinds[key] != "noul":
                return "DB011", f"{key!r} es una pregunta {self.kinds[key]}: p lee solo las sí/no"
        return None

    def _condition(self, where: str, source: str, variables: dict[str, str], sample: dict[str, Any]) -> Any:
        try:
            expression = self.expressions.compile(source, variables)
        except ExpressionError as exc:
            self.add("DB006", where, f"no compila: {exc}")
            return None
        if expression.returns_bool:
            return expression
        if expression.returns == "dyn":
            try:
                if isinstance(expression.evaluate(sample), bool):
                    return expression
            except ExpressionError:
                pass
        self.add("DB006", where, "la condición tiene que dar true o false")
        return None

    def contract(self) -> tuple[CompiledContractRow, ...] | None:
        rows: list[CompiledContractRow] = []
        failed = False
        unconditional: set[str] = set()
        sample = {
            "p": {}, "th": self.spec.thresholds, "dom": {},
            "inp": {name: _sample(text) for name, text in self.vocab.inputs.items()},
        }
        for i, row in enumerate(self.spec.contract):
            where = f"contract[{i}]"
            if row.topic not in self.topics:
                self.add("DB015", f"{where}.topic", f"asunto que el cuestionario no tiene (hay: {', '.join(self.topics)})")
                failed = True
            unknown = sorted(set(row.any_of) - set(self.vocab.tools))
            if unknown:
                self.add("DB015", f"{where}.any_of", f"tools que el agente no tiene {unknown}")
                failed = True
            if row.topic in unconditional:
                self.add("DB008", where, f"nunca se lee: una fila anterior de {row.topic!r} no tiene condición")
                failed = True
            when = None
            if row.when is None:
                unconditional.add(row.topic)
            elif not self._keys(f"{where}.when", row.when, set(_CONTRACT_VARS)):
                failed = True
            elif bad := [lit for _k, lit in literal_comparisons(row.when, r"inp\.stage") if lit not in self.vocab.stages]:
                self.add("DB015", f"{where}.when", f"etapas que no existen {bad} (hay: {', '.join(self.vocab.stages)})")
                failed = True
            else:
                when = self._condition(f"{where}.when", row.when, _CONTRACT_VARS, sample)
                failed = failed or when is None
            rows.append(CompiledContractRow(row.topic, when, tuple(row.any_of), row.nudge))
        if not failed:
            failed = not self._walks_without_answers(rows, sample)
        return None if failed else tuple(rows)

    def _walks_without_answers(self, rows: tuple[CompiledContractRow, ...] | list[CompiledContractRow], sample: dict[str, Any]) -> bool:
        """En ejecución, una respuesta puede no llegar (Jev no la contestó o la
        pregunta no se hizo): cada asunto se recorre con `p` vacío como en el
        turno, y ninguna fila puede fallar antes de que una decida."""
        ok = True
        for topic in dict.fromkeys(row.topic for row in rows):
            for i, row in enumerate(rows):
                if row.topic != topic:
                    continue
                if row.when is None:
                    break
                try:
                    if row.when.evaluate(sample) is True:
                        break
                except ExpressionError as exc:
                    self.add("DB005", f"contract[{i}].when", (
                        f"falla si la respuesta no llegó ({exc}): pregunta antes si está, p. ej. `'x' in p && p['x'] …`"
                    ))
                    ok = False
                    break
        return ok

    # ── ③ la banda de cada asunto ─────────────────────────────────────────

    def verify_rows(self) -> tuple[CompiledRow, ...] | None:
        rows: list[CompiledRow] = []
        failed = False
        decide = self.spec.verify_decide
        sample = {"item": {"topic": "x", "msg": 1, "p": 0.5}, "th": self.spec.thresholds, "dom": {}}
        for i, row in enumerate(decide):
            where = f"verify_decide[{i}]"
            if isinstance(row, OtherwiseRow):
                if i != len(decide) - 1:
                    self.add("DB008", where, "`otherwise` va al final: las filas después nunca se leen")
                    failed = True
                if row.otherwise not in BANDS:
                    self.add("DB007", f"{where}.otherwise", f"{row.otherwise!r} no es una banda ({', '.join(BANDS)})")
                    failed = True
                rows.append(CompiledRow(None, row.otherwise))
                continue
            assert isinstance(row, WhenRow)
            if row.then not in BANDS:
                self.add("DB007", f"{where}.then", f"{row.then!r} no es una banda ({', '.join(BANDS)})")
                failed = True
            unknown = sorted({m.group(1) for m in _ITEM_FIELD.finditer(row.when)} - set(VERIFY_ITEM))
            if unknown:
                self.add("DB005", f"{where}.when", f"campos que el asunto no trae {unknown} (hay: {', '.join(VERIFY_ITEM)})")
                failed = True
                continue
            if not self._keys(f"{where}.when", row.when, {"th", "dom"}):
                failed = True
                continue
            when = self._condition(f"{where}.when", row.when, _VERIFY_VARS, sample)
            failed = failed or when is None
            rows.append(CompiledRow(when, row.then))
        if not isinstance(decide[-1], OtherwiseRow):
            self.add("DB008", "verify_decide", "falta la fila final `otherwise` (qué pasa si ninguna condición se cumple)")
            failed = True
        if not failed:
            for item in ({"topic": "x", "msg": None}, {"topic": "x", "msg": 1, "p": 0.5}):
                for k, row in enumerate(rows):
                    if row.when is None:
                        break
                    try:
                        if row.when.evaluate({**sample, "item": item}) is True:
                            break
                    except ExpressionError as exc:
                        self.add("DB005", f"verify_decide[{k}].when", (
                            f"falla con un asunto que Jev no contestó ({exc}): antes, `!('p' in item)`"
                        ))
                        failed = True
                        break
        return None if failed else tuple(rows)

    # ── la guía de etapas ─────────────────────────────────────────────────

    def guide(self) -> None:
        guide = self.spec.guide
        unknown = sorted(set(guide.choice_topics) - set(self.topics))
        if unknown:
            self.add("DB015", "guide.choice_topics", f"asuntos que el cuestionario no tiene {unknown}")
        unknown = sorted(set(guide.no_sale_stages) - set(self.vocab.stages))
        if unknown:
            self.add("DB015", "guide.no_sale_stages", f"etapas desconocidas {unknown} (hay: {', '.join(self.vocab.stages)})")
        for slot in guide.slot_labels:
            if slot not in self.vocab.slots:
                self.add("DB015", f"guide.slot_labels.{slot}", f"dato desconocido (hay: {', '.join(self.vocab.slots)})")

    # ── ejemplos ──────────────────────────────────────────────────────────

    def examples(self, turn: CompiledTurn) -> None:
        for i, example in enumerate(self.spec.examples.contract):
            where = f"examples.contract[{i}]"
            problem = self._example_problem(example.topics, example.answers, kinds=("noul",))
            fields = sorted(set(example.input) - set(self.vocab.inputs))
            if problem is None and fields:
                problem = f"campos no declarados {fields} (hay: {', '.join(self.vocab.inputs)})"
            if problem is not None:
                self.add("DB010", where, problem)
                continue
            inp = {**{name: None for name in self.vocab.inputs}, **example.input}
            got = [(r["topic"], r["any_of"]) for r in turn.required(example.topics, p=example.answers, inp=inp)]
            want = [(r.topic, r.any_of) for r in example.expect]
            if got != want:
                self.add("DB010", where, f"se esperaba {want} y el contrato da {got}")
        verify_id = self.spec.questionnaire.verify.id
        for i, example in enumerate(self.spec.examples.verify):
            where = f"examples.verify[{i}]"
            topics = [(t, None) if isinstance(t, str) else (t.topic, t.msg) for t in example.topics]
            problem = self._example_problem([t for t, _m in topics], {}, kinds=())
            asked = {verify_id.format(topic=t) for t, _m in topics}
            unknown = sorted(set(example.answers) - asked)
            if problem is None and unknown:
                problem = f"respuestas que no son de ③ de estos asuntos {unknown}"
            if problem is not None:
                self.add("DB010", where, problem)
                continue
            got = turn.verify(topics, p=example.answers)
            want = (example.expect.decision, tuple(example.expect.missing))
            if (got.decision, got.missing) != want:
                self.add("DB010", where, f"se esperaba {want} y ③ da {(got.decision, got.missing)}")

    def _example_problem(self, topics: list[str], answers: Mapping[str, float], *, kinds: tuple[str, ...]) -> str | None:
        unknown = sorted(set(topics) - set(self.topics))
        if unknown:
            return f"asuntos que el cuestionario no tiene {unknown}"
        bad = sorted(qid for qid in answers if self.kinds.get(qid) not in kinds)
        if bad:
            return f"respuestas de preguntas que no son sí/no del cuestionario {bad}"
        return None


def _normalized(raw: Any) -> Any:
    """El cuestionario tal cual lo escribió el paquete, con las llaves de
    `criteria` que YAML lee como booleanas (`{true: …}` sin comillas) como el
    modelo las certificó: "true"/"false". Es lo que llega a Jev."""
    if isinstance(raw, list):
        return [_normalized(v) for v in raw]
    if not isinstance(raw, dict):
        return raw
    out = {k: _normalized(v) for k, v in raw.items()}
    if isinstance(out.get("criteria"), dict):
        out["criteria"] = {str(k).lower() if isinstance(k, bool) else k: v for k, v in out["criteria"].items()}
    return out


def check_turn(
    bundle_dir: Path, catalog: Catalog, expressions: ExpressionPort, *, bundle: str, domain: Mapping[str, Any],
    out: list[Diagnostic],
) -> CompiledTurn | None:
    """El `turn.yaml` del paquete, certificado y compilado (None si el
    catálogo no declara turno, o si no pasa: los diagnósticos van a `out`)."""
    path = Path(bundle_dir) / LABEL
    if catalog.turn is None:
        if path.is_file():
            out.append(Diagnostic("DB015", LABEL, "el catálogo no declara el vocabulario del turno (`turn:`)"))
        return None
    if not path.is_file():
        out.append(Diagnostic("DB015", LABEL, "falta el turno del paquete: el catálogo declara `turn:`"))
        return None
    spec = _parse(Turn, path, LABEL, out)
    if spec is None:
        return None
    check = _TurnCheck(spec, catalog, catalog.turn, expressions)
    policy = check.policy()
    check.thresholds(policy)
    check.questionnaire(policy)
    check.coverage()
    check.reading(policy)
    contract = check.contract()
    verify = check.verify_rows()
    check.guide()
    if check.out or contract is None or verify is None:
        out += check.out
        return None
    raw, _error = _read_yaml(path)
    turn = CompiledTurn(spec, _normalized(raw["questionnaire"]), contract, verify, bundle=bundle, domain=domain)
    check.examples(turn)
    out += check.out
    return None if check.out else turn


__all__ = ["check_turn"]
