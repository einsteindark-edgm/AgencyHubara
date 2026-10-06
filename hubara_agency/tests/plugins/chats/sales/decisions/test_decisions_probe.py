"""Sonda diaria de Jev (diseño v2: «Una sonda diaria. 20 ráfagas sintéticas con
respuesta conocida, sin datos de clientes»).

Jev es el único clasificador del motor y su API está en alpha: la sonda le
hace, una vez al día, las mismas preguntas que le hace el turno sobre 20
ráfagas inventadas cuya respuesta se conoce, y dice si:

* la API cambió de forma o se cayó (`ok_rate`: respuestas válidas con todas
  las preguntas respondidas) → `down` por debajo de 0,9;
* Jev empezó a responder distinto (`pass_rate`: respuestas conocidas que
  acierta, o el snapshot servido cambió desde la última sonda) → `degraded`
  por debajo de 0,85.

Sin llave de OpenRouter no llama a nadie (`sin_llave`). El reporte queda en
`<vault>/_decisions/probe/<día de Bogotá>.json` y `latest.json`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions import engine, probe
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

SERVED = "typesafe/jev-1.13-20260917"
AT_MS = 1_790_200_000_000


class _Jev:
    """Jev falso: responde lo que la sonda espera (o lo contrario, o con otra
    forma) según el `state`, y registra lo que recibió."""

    def __init__(
        self,
        *,
        wrong: set[str] = frozenset(),
        broken: set[str] = frozenset(),
        unanswered: set[str] = frozenset(),
        model: str = SERVED,
        latencies: list[int] | None = None,
    ) -> None:
        self.table = {}
        for case in probe.PROBE_CASES:
            request = probe.build_request(case)
            if request is not None:
                self.table[request[0]] = case
        self.wrong, self.broken, self.unanswered = wrong, broken, unanswered
        self.model = model
        self.latencies = list(latencies or [])
        self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def _answer(self, q, expected):
        if q.kind == "noul":
            p = (0.93 if expected else 0.04) if isinstance(expected, bool) else 0.1
            return TypedAnswer(id=q.id, kind="noul", p=p)
        pick = expected if isinstance(expected, str) else q.options[0]
        return TypedAnswer(id=q.id, kind="choice", choice=pick, probs=((pick, 1.0),), confidence=0.9)

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.calls.append((state, tuple(q.id for q in questions), tuple(redact)))
        latency = self.latencies.pop(0) if self.latencies else 800
        case = self.table.get(state)
        cid = case.id if case else ""
        if cid in self.broken:
            return PerceptionResult(ok=False, error="bad_shape", provider="fake", model="typesafe/jev-1.13", latency_ms=latency)
        expect = dict(case.expect) if case else {}
        if cid in self.wrong:
            expect = {k: (not v if isinstance(v, bool) else "otra_si_no" if k == "thread.bot_asked" else "otra")
                      for k, v in expect.items()}
        answers = tuple(self._answer(q, expect.get(q.id)) for q in questions if q.id not in self.unanswered)
        return PerceptionResult(ok=True, answers=answers, provider="fake", model=self.model, latency_ms=latency)


@pytest.fixture
def jev(monkeypatch):
    """Instala un Jev falso para el oráculo de los perfiles; la llave existe."""
    from src.sdk import connectorkit

    holder: dict[str, _Jev] = {"port": _Jev()}

    def _get(_oracle: str) -> _Jev:
        return holder["port"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def install(port: _Jev) -> _Jev:
        holder["port"] = port
        return port

    return install


# ── los 20 casos ─────────────────────────────────────────────────────────────


def test_there_are_twenty_cases_for_both_profiles() -> None:
    ids = [c.id for c in probe.PROBE_CASES]

    assert len(ids) == 20 and len(set(ids)) == 20
    assert {c.profile for c in probe.PROBE_CASES} == {"jev-v1", "jev-v2"}
    for case in probe.PROBE_CASES:
        assert get_engine_profile(case.profile) is not None, case.id
        assert case.messages and all(m.get("text") and isinstance(m.get("ts_ms"), int) and m.get("wamid")
                                     for m in case.messages), case.id
        assert case.expect, case.id


def test_v2_cases_carry_what_the_customer_saw_and_v1_cases_do_not() -> None:
    for case in probe.PROBE_CASES:
        uses_context = load_questionnaire(get_engine_profile(case.profile).questions).uses_context
        if uses_context:
            assert case.context is not None and case.context.window.lines, case.id
        else:
            assert case.context is None, case.id


def test_every_expectation_names_a_question_the_case_asks_with_a_valid_answer() -> None:
    for case in probe.PROBE_CASES:
        _state, questions = probe.build_request(case)
        by_id = {q.id: q for q in questions}
        for qid, expected in case.expect.items():
            assert qid in by_id, (case.id, qid)
            q = by_id[qid]
            if q.kind == "noul":
                assert isinstance(expected, bool), (case.id, qid)
            else:
                assert expected in q.options, (case.id, qid, expected)


def test_the_cases_are_synthetic_without_customer_data() -> None:
    texts: list[str] = []
    for case in probe.PROBE_CASES:
        texts += [str(m["text"]) for m in case.messages]
        if case.context is not None:
            texts += list(case.context.window.lines) + list(case.context.facts)
    blob = "\n".join(texts)

    assert not re.search(r"\d{7,}", blob), "un número de teléfono no va en la sonda"
    assert "wa_" not in blob and "@" not in blob


def test_every_case_asks_something_different() -> None:
    states = [probe.build_request(c)[0] for c in probe.PROBE_CASES]

    assert len(set(states)) == len(states)


def test_known_answers_cover_the_readings_the_engine_acts_on() -> None:
    """Los ejemplos del diseño: el «Si» tras la pregunta de dirección, el «Ok»
    tras ofrecer los aromas, el «Sí» tras la tarjeta de confirmación, el «no,
    mejor la roja», el aplazamiento, la queja, el saludo y el envío."""
    expected = [(c.profile, k, v) for c in probe.PROBE_CASES for k, v in c.expect.items()]

    for needed in [
        ("jev-v2", "thread.bot_asked", "confirmar_dato_envio"),
        ("jev-v2", "thread.bot_asked", "ver_opciones"),
        ("jev-v2", "thread.answer", "si"),
        ("jev-v2", "thread.answer", "no"),
        ("jev-v1", "topic.catalogo", True),
        ("jev-v1", "topic.aplaza", True),
        ("jev-v1", "topic.queja", True),
        ("jev-v1", "topic.saludo", True),
        ("jev-v1", "topic.envio", True),
    ]:
        assert needed in expected, needed
    card = [c for c in probe.PROBE_CASES if c.context is not None and c.context.window.bot_asked_known == "confirmar_compra"]
    assert card and card[0].expect.get("thread.answer") == "si"


# ── la corrida ───────────────────────────────────────────────────────────────


async def test_the_probe_asks_jev_exactly_what_the_turn_asks(jev) -> None:
    port = jev(_Jev())
    await probe.run_probe(now_ms=AT_MS)
    probe_calls = [(state, qids) for state, qids, _ in port.calls]

    turn = jev(_Jev())
    for case in probe.PROBE_CASES:
        await engine.perceive(
            PerceiveInput(session_id="wa_x", profile=case.profile, messages=[dict(m) for m in case.messages]),
            context=case.context,
        )
    turn_calls = [(state, qids) for state, qids, _ in turn.calls]

    assert len(probe_calls) == 20
    assert probe_calls == turn_calls


async def test_a_healthy_jev_gives_an_ok_report(jev) -> None:
    jev(_Jev(latencies=[100 * k for k in range(1, 21)]))

    report = await probe.run_probe(now_ms=AT_MS)

    assert report["at_ms"] == AT_MS and report["cases"] == 20
    assert (report["ok_rate"], report["pass_rate"]) == (1.0, 1.0)
    assert report["models"] == [SERVED]
    assert report["p95_ms"] == 1900
    assert report["shape_errors"] == [] and report["failures"] == []
    assert report["status"] == probe.STATUS_OK == probe.probe_status(report)


async def test_jev_answering_differently_is_degraded_with_each_failure(jev) -> None:
    total = sum(len(c.expect) for c in probe.PROBE_CASES)
    wrong: set[str] = set()
    flipped = 0
    for case in probe.PROBE_CASES:
        if flipped > 0.15 * total:
            break
        wrong.add(case.id)
        flipped += len(case.expect)
    jev(_Jev(wrong=wrong))

    report = await probe.run_probe(now_ms=AT_MS)

    assert report["ok_rate"] == 1.0 and report["pass_rate"] < probe.MIN_PASS_RATE
    assert probe.probe_status(report) == probe.STATUS_DEGRADED
    first = probe.PROBE_CASES[0]
    for qid, expected in first.expect.items():
        [row] = [f for f in report["failures"] if f["case"] == first.id and f["question"] == qid]
        assert row["expected"] == expected
        if isinstance(expected, bool):
            assert row["got"] == (0.04 if expected else 0.93)  # la probabilidad que dio Jev
        else:
            assert row["got"] != expected  # la opción que eligió Jev
    assert len(report["failures"]) == flipped


async def test_a_noul_counts_as_yes_from_one_half(jev) -> None:
    case = next((c for c in probe.PROBE_CASES if c.expect.get("topic.queja") is True), None)
    assert case is not None, "la sonda tiene un caso de queja"

    class _Borderline(_Jev):
        def _answer(self, q, expected):
            if q.id == "topic.queja":
                return TypedAnswer(id=q.id, kind="noul", p=0.5)
            return super()._answer(q, expected)

    jev(_Borderline())
    report = await probe.run_probe([case], now_ms=AT_MS)

    assert report["pass_rate"] == 1.0 and report["failures"] == []


async def test_another_shape_or_an_outage_is_down(jev) -> None:
    broken = {c.id for c in probe.PROBE_CASES[:3]}
    jev(_Jev(broken=broken))

    report = await probe.run_probe(now_ms=AT_MS)

    assert report["ok_rate"] == 0.85 and probe.probe_status(report) == probe.STATUS_DOWN
    assert sorted(e["case"] for e in report["shape_errors"]) == sorted(broken)
    assert {e["error"] for e in report["shape_errors"]} == {"bad_shape"}
    assert report["pass_rate"] == 1.0  # lo que respondió bien sigue acertando


async def test_an_ok_answer_missing_a_question_is_a_shape_error(jev) -> None:
    jev(_Jev(unanswered={"topic.saludo"}))

    report = await probe.run_probe(now_ms=AT_MS)

    assert report["ok_rate"] == 0.0 and probe.probe_status(report) == probe.STATUS_DOWN
    assert len(report["shape_errors"]) == 20
    assert all("topic.saludo" in e["error"] for e in report["shape_errors"])


@pytest.mark.parametrize("key", [None, "", "PLACEHOLDER_set_out_of_band"])
async def test_without_a_real_key_it_never_calls_jev(jev, monkeypatch, key) -> None:
    port = jev(_Jev())
    if key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)

    report = await probe.run_probe(now_ms=AT_MS)

    assert port.calls == []
    assert report["status"] == probe.STATUS_NO_KEY == probe.probe_status(report)
    assert report["at_ms"] == AT_MS and report["cases"] == 0 and report["models"] == []


async def test_an_unexpected_error_of_the_port_is_a_shape_error_not_a_crash(jev) -> None:
    class _Crashing(_Jev):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            raise RuntimeError("se cayó el cliente HTTP")

    jev(_Crashing())
    report = await probe.run_probe(probe.PROBE_CASES[:2], now_ms=AT_MS)

    assert report["ok_rate"] == 0.0 and len(report["shape_errors"]) == 2
    assert report["shape_errors"][0]["error"].startswith("unexpected")


# ── el estado ────────────────────────────────────────────────────────────────


def _report(**over) -> dict:
    base = {"at_ms": AT_MS, "status": "ok", "models": [SERVED], "cases": 20, "ok_rate": 1.0, "pass_rate": 0.95,
            "p95_ms": 900, "shape_errors": [], "failures": []}
    return base | over


def test_status_rules() -> None:
    assert probe.probe_status(_report()) == "ok"
    assert probe.probe_status(_report(ok_rate=0.9)) == "ok"
    assert probe.probe_status(_report(ok_rate=0.85)) == "down"
    assert probe.probe_status(_report(ok_rate=0.85, pass_rate=0.1)) == "down"
    assert probe.probe_status(_report(pass_rate=0.85)) == "ok"
    assert probe.probe_status(_report(pass_rate=0.84)) == "degraded"
    assert probe.probe_status(_report(ok_rate=None, pass_rate=None, cases=0)) == "down"
    assert probe.probe_status(_report(status="sin_llave", ok_rate=None, pass_rate=None)) == "sin_llave"


def test_a_new_served_snapshot_is_degraded() -> None:
    before = _report(models=["typesafe/jev-1.13-20260901"])

    assert probe.probe_status(_report(), before) == "degraded"
    assert probe.probe_status(_report(), _report()) == "ok"
    # Sin snapshot en la anterior (caída o sin llave) no hay con qué comparar.
    assert probe.probe_status(_report(), _report(models=[], status="down")) == "ok"


# ── el reporte en el vault ───────────────────────────────────────────────────


def test_the_report_is_kept_by_bogota_day_and_as_latest(tmp_path: Path) -> None:
    # 2026-09-29 02:30 UTC = 2026-09-28 21:30 en Bogotá.
    at = 1_790_649_000_000
    report = _report(at_ms=at)

    written = probe.write_report(tmp_path, report)

    folder = tmp_path / "_decisions" / "probe"
    assert probe.probe_dir(tmp_path) == folder
    assert written == folder / "2026-09-28.json"
    assert json.loads(written.read_text(encoding="utf-8")) == report
    assert json.loads((folder / "latest.json").read_text(encoding="utf-8")) == report
    assert probe.read_latest(tmp_path) == report


def test_reading_the_latest_never_fails(tmp_path: Path) -> None:
    assert probe.read_latest(tmp_path) is None

    folder = tmp_path / "_decisions" / "probe"
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text("{roto", encoding="utf-8")
    assert probe.read_latest(tmp_path) is None

    (folder / "latest.json").write_text("[1, 2]", encoding="utf-8")
    assert probe.read_latest(tmp_path) is None
