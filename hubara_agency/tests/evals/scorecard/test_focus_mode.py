"""Scorecard en modo turno (plan del laboratorio §5.2–5.5): validación permanente.

El laboratorio simula UN turno por caso con el prefijo real como contexto. El
motor recibe `focus_turn`: los turnos anteriores son contexto (los acumuladores
los recorren) y solo se juzga el turno foco.

**Réplica exacta.** Para cada trayectoria del corpus (incidentes reales + todas
las que arman los tests por familia, capturadas al correrlos) y cada turno k,
se usa el turno REAL como candidato. Para cada check de código de foco `turn`:

    turno de la primera falla en modo episodio == mínimo turno que falla en modo turno

Si el episodio no falla, ningún turno falla. Los checks de foco `future`
(dependen de turnos posteriores: cierre, pedido registrado) nunca dan un `pasa`
falso: en el turno de la falla del episodio no pasan, y un `pasa` siempre trae
su evidencia en el turno foco.

Exenciones documentadas (no son turno a turno por construcción del check):
  * APE-01 sin ningún texto en el episodio: la falla se ancla al turno 1 por
    convención ("no se envió saludo"), no por evidencia; en modo turno el
    primer texto puede llegar después → `sin_senal`.
  * TAG-01 anclado por la etiqueta de cierre del episodio (sin turno que la
    ponga): el cierre es futuro para cualquier turno → `sin_senal`.
"""
from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from src.plugins.chats.agent.sales_eval.scorecard import engine
from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.checks._evidence import CONFIRMED_TAGS, tag_turn
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    SIN_SENAL,
    all_sent_texts,
    judged,
    judged_turns,
    sent_texts,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import VERDICTS, CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import (
    CHECKS,
    REGISTRY_VERSION,
    SPECS_BY_ID,
    specs_payload,
)
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    build_trajectory,
    focus_trajectory,
    turn_from_trace,
)
from src.plugins.chats.agent.sales_eval.scorecard.verdict import compute_scorecard
from tests.evals.scorecard import (
    incidents,
    test_checks_apertura,
    test_checks_cierre,
    test_checks_confirmacion,
    test_checks_descubrimiento,
    test_checks_envio,
    test_checks_estado,
    test_checks_estilo,
    test_checks_ghosting,
    test_checks_postcierre,
    test_checks_variantes,
)
from tests.evals.scorecard.dsl import T, tool, traj

FUTURE_CHECKS = {"CIE-03", "CIE-03b", "CIE-04", "CIE-08", "GHO-02", "TAG-07"}
_LEAK = "falla fuera del turno foco"

_FAMILY_MODULES = (
    test_checks_apertura,
    test_checks_cierre,
    test_checks_confirmacion,
    test_checks_descubrimiento,
    test_checks_envio,
    test_checks_estado,
    test_checks_estilo,
    test_checks_ghosting,
    test_checks_postcierre,
    test_checks_variantes,
)


# ── Corpus: incidentes + trayectorias de los tests por familia ──────────────
def _harvest() -> list[tuple[str, object, CheckContext]]:
    """Corre los tests por familia espiando `CODE_CHECKS` y se queda con cada
    (trayectoria, contexto) que evaluaron. Reusa sus builders sin copiarlos."""
    seen: dict[str, tuple[str, object, CheckContext]] = {}
    originals = dict(CODE_CHECKS)
    current = {"name": ""}

    def spy(fn):
        def wrapped(t, ctx):
            seen.setdefault(repr((t, ctx)), (current["name"], t, ctx))
            return fn(t, ctx)

        return wrapped

    try:
        for check_id, fn in originals.items():
            CODE_CHECKS[check_id] = spy(fn)
        for mod in _FAMILY_MODULES:
            for name, fn in sorted(vars(mod).items()):
                if not name.startswith("test_") or not inspect.isfunction(fn):
                    continue
                current["name"] = f"{mod.__name__.rsplit('.', 1)[-1]}::{name}"
                with pytest.MonkeyPatch.context() as mp:
                    fn(*([mp] if inspect.signature(fn).parameters else []))
    finally:
        CODE_CHECKS.clear()
        CODE_CHECKS.update(originals)
    return list(seen.values())


def _corpus() -> list[tuple[str, object, CheckContext]]:
    base = [
        ("incident::pr281_before_fix", incidents.pr281_before_fix(), incidents.CATALOG_CTX),
        ("incident::pr281_after_fix", incidents.pr281_after_fix(), incidents.CATALOG_CTX),
        ("incident::ebbc203d_before_fix", incidents.ebbc203d_before_fix(), incidents.halloween_ctx()),
        ("incident::ebbc203d_after_fix", incidents.ebbc203d_after_fix(), incidents.halloween_ctx()),
    ]
    harvested = [(f"{name}#{i}", t, ctx) for i, (name, t, ctx) in enumerate(_harvest()) if t.turns]
    return base + harvested


CORPUS = _corpus()


def _episode_at(real) -> dict:
    """Lo que el laboratorio pasa por caso (`episodes_at`): el episodio sin los
    campos del cierre. Trae el `order_id` final; `focus_trajectory` lo usa solo
    si la orden ya existía en el turno."""
    return {"episode_id": real.episode_id, "order_id": real.order_id}


def _by_turn(real, ctx: CheckContext) -> dict[int, dict[str, CheckResult]]:
    out: dict[int, dict[str, CheckResult]] = {}
    for turn in real.turns:
        focus = focus_trajectory(real, turn, episode_at=_episode_at(real))
        out[turn.turn] = {r.check_id: r for r in engine.run_code_checks(focus, ctx)}
    return out


def _exempt(check_id: str, real, episode: CheckResult) -> str | None:
    if episode.verdict != "falla":
        return None
    if check_id == "APE-01" and not any(t.sent_texts for t in real.turns):
        return "APE-01 sin texto en el episodio: falla anclada al turno 1 por convención"
    if check_id == "TAG-01" and tag_turn(replace(real, closing_tag=None), CONFIRMED_TAGS) is None:
        return "TAG-01 anclado por la etiqueta de cierre del episodio"
    return None


def test_corpus_covers_incidents_and_every_family() -> None:
    names = {name.split("::", 1)[0] for name, _, _ in CORPUS}
    assert {"incident", *(m.__name__.rsplit(".", 1)[-1] for m in _FAMILY_MODULES)} <= names
    assert len(CORPUS) > 150


@pytest.mark.parametrize(("name", "real", "ctx"), CORPUS, ids=[c[0] for c in CORPUS])
def test_turn_mode_replicates_the_episode_scorecard(name: str, real, ctx: CheckContext) -> None:
    episode = {r.check_id: r for r in engine.run_code_checks(real, ctx)}
    by_turn = _by_turn(real, ctx)

    for spec in CHECKS:
        if spec.kind != "code":
            continue
        e = episode[spec.id]
        focus = {k: res[spec.id] for k, res in by_turn.items()}
        leaked = [k for k, r in focus.items() if _LEAK in r.evidence]
        assert not leaked, f"{spec.id}: falla del prefijo filtrada al turno foco en {leaked}"
        assert all(r.turn == k for k, r in focus.items()), spec.id
        fails = sorted(k for k, r in focus.items() if r.verdict == "falla")
        if spec.focus == "turn":
            if _exempt(spec.id, real, e):
                continue
            expected = e.turn if e.verdict == "falla" else None
            got = fails[0] if fails else None
            assert got == expected, f"{spec.id}: episodio {e.verdict}@{e.turn} ({e.evidence}) vs turnos {fails}"
        else:
            if e.verdict == "falla" and e.turn in focus:
                assert focus[e.turn].verdict != "pasa", f"{spec.id}: pasa falso en el turno {e.turn}"


@pytest.mark.parametrize(("name", "real", "ctx"), CORPUS, ids=[c[0] for c in CORPUS])
def test_future_checks_pass_only_with_evidence_in_the_focus_turn(name: str, real, ctx) -> None:
    for turn in real.turns:
        focus = focus_trajectory(real, turn, episode_at=_episode_at(real))
        for check_id in sorted(FUTURE_CHECKS - {"TAG-07"}):
            raw = CODE_CHECKS[check_id](focus, ctx)
            if raw.verdict in ("pasa", "falla"):
                assert raw.turn == turn.turn, f"{check_id}@{turn.turn}: {raw}"


def test_exemptions_are_the_documented_ones_only() -> None:
    exempted = set()
    for _, real, ctx in CORPUS:
        for r in engine.run_code_checks(real, ctx):
            if _exempt(r.check_id, real, r):
                exempted.add(r.check_id)
    assert exempted <= {"APE-01", "TAG-01"}


# ── PR #281 como caso del laboratorio ──────────────────────────────────────
def test_pr281_turn_mode_reproduces_the_production_map_except_ghost_turns() -> None:
    """Los casos del laboratorio excluyen los turnos de ghosting (los dispara el
    sistema, no el cliente): las fallas que caen en ellos (TAG-01, TAG-02 y
    GHO-02 en T10) no tienen caso que las reproduzca. El resto del mapa de
    `test_incident_pr281.py` sale idéntico, cada falla en su turno."""
    real = incidents.pr281_before_fix()
    failures: dict[str, int] = {}
    for turn in real.turns:
        if turn.is_ghost:
            continue
        focus = focus_trajectory(real, turn, episode_at=_episode_at(real))
        for r in engine.run_code_checks(focus, incidents.CATALOG_CTX):
            if r.verdict == "falla":
                assert r.turn == turn.turn
                failures.setdefault(r.check_id, r.turn)

    assert failures == {
        "DES-01": 4,
        "DES-09": 2,
        "VAR-01": 5,
        "CON-01": 9,
        "CON-02": 9,
        "ENV-02": 9,
        "EST-06": 9,
    }


# ── Trayectoria foco ───────────────────────────────────────────────────────
def _closed_with_order():
    return traj(
        T(1, sent=["¡Buenos días! Bienvenido a *Hubara*"]),
        T(2, inbound="sí, confírmalo", signal="affirmation",
          tools=[tool("register_order")], state={"order_id": "ord_1", "changes": []}),
        T(3, inbound="gracias", sent=["Con gusto"]),
        closing_tag="CONFIRMADO_PAGO_PENDIENTE",
        order_id="ord_1",
    )


def test_focus_trajectory_is_the_real_prefix_plus_the_candidate_without_later_turns() -> None:
    real = _closed_with_order()
    candidate = T(2, inbound="sí, confírmalo", sent=["Listo, ya casi"])

    f = focus_trajectory(real, candidate)

    assert [t.turn for t in f.turns] == [1, 2]
    assert f.turns[0] is real.turns[0]
    assert f.turns[1] is candidate
    assert f.focus_turn == 2
    assert f.fidelity == real.fidelity
    assert (f.session_id, f.episode_id) == (real.session_id, real.episode_id)
    assert (f.closing_tag, f.closing_motivo, f.closed_at_ms, f.order_id) == (None, None, None, None)


def test_focus_trajectory_takes_the_episode_as_of_the_turn() -> None:
    real = replace(_closed_with_order(), started_at_ms=1_000, closed_at_ms=9_000, closing_motivo="pagó")
    episode_at = {"episode_id": "ep_007", "order_id": "ord_1", "started_at_ms": 1_000,
                  "closing_tag": "CONFIRMADO_PAGO_PENDIENTE"}

    before = focus_trajectory(real, real.turns[0], episode_at=episode_at)
    after = focus_trajectory(real, real.turns[2], episode_at=episode_at)

    assert before.order_id is None, "la orden se registró en T2: en T1 todavía no existía"
    assert after.order_id == "ord_1"
    assert after.started_at_ms == 1_000
    assert (after.closing_tag, after.closing_motivo, after.closed_at_ms) == (None, None, None)


def test_order_from_a_previous_episode_exists_from_the_first_turn() -> None:
    real = traj(T(1, inbound="¿ya salió mi pedido?", stage_in="postcierre"), order_id="ord_9")

    f = focus_trajectory(real, real.turns[0], episode_at={"order_id": "ord_9"})

    assert f.order_id == "ord_9"


def test_episode_mode_trajectory_has_no_focus() -> None:
    assert _closed_with_order().focus_turn is None


def test_turn_from_trace_is_the_same_turn_build_trajectory_builds() -> None:
    raws = incidents.traces_from(incidents.pr281_before_fix())

    built = build_trajectory(raws, session_id="s", episode={"episode_id": "ep_007"})

    assert tuple(turn_from_trace(r) for r in raws) == built.turns


# ── Helpers ────────────────────────────────────────────────────────────────
def test_sent_texts_only_yield_the_focus_turn_and_all_sent_texts_the_prefix() -> None:
    real = _closed_with_order()
    f = focus_trajectory(real, real.turns[2])

    assert [(t.turn, x) for t, x in sent_texts(f)] == [(3, "Con gusto")]
    assert [t.turn for t, _ in all_sent_texts(f)] == [1, 3]
    assert [t.turn for t in judged_turns(f)] == [3]
    assert judged(real, real.turns[0]) and not judged(f, f.turns[0])
    assert [t.turn for t, _ in sent_texts(real)] == [1, 3]


# ── Motor ──────────────────────────────────────────────────────────────────
def test_an_unadapted_check_cannot_leak_a_prefix_failure_into_the_focus_turn(monkeypatch) -> None:
    def legacy_style(t, _ctx):
        return CheckResult("EST-06", "falla", turn=t.turns[0].turn, evidence="viejo")

    monkeypatch.setitem(CODE_CHECKS, "EST-06", legacy_style)
    real = _closed_with_order()

    results = {r.check_id: r for r in engine.run_code_checks(focus_trajectory(real, real.turns[2]), CheckContext())}

    assert results["EST-06"].verdict == "desconocido"
    assert _LEAK in results["EST-06"].evidence
    assert {r.turn for r in results.values()} == {3}


def test_a_future_pass_without_evidence_in_the_focus_turn_is_no_signal(monkeypatch) -> None:
    monkeypatch.setitem(CODE_CHECKS, "CIE-04", lambda t, _c: CheckResult("CIE-04", "pasa", turn=1))
    real = _closed_with_order()

    results = {r.check_id: r for r in engine.run_code_checks(focus_trajectory(real, real.turns[2]), CheckContext())}

    assert results["CIE-04"].verdict == SIN_SENAL


def test_episode_mode_results_keep_their_own_turns() -> None:
    real = incidents.pr281_before_fix()
    results = {r.check_id: r for r in engine.run_code_checks(real, incidents.CATALOG_CTX)}

    assert results["DES-09"].turn == 2
    assert results["APE-04"].turn is None
    assert SIN_SENAL not in {r.verdict for r in results.values()}


# ── Checks que dependen del futuro ─────────────────────────────────────────
def _registered(*extra_tools, turn_state=None):
    return traj(
        T(1, sent=["¡Buenos días! Bienvenido a *Hubara*"]),
        T(2, inbound="sí", signal="affirmation", sent=["¡Listo!"],
          tools=[tool("verify_order_for_checkout"), tool("present_order_confirmation"),
                 tool("register_order"), *extra_tools],
          state=turn_state or {"order_id": "ord_1", "changes": []}),
        T(3, inbound="gracias", sent=["Con gusto"],
          tools=[tool("escalate_to_human", reason_category="PAYMENT_VERIFICATION_PENDING")]),
        order_id="ord_1",
    )


def _focus(real, k, check_id):
    f = focus_trajectory(real, next(t for t in real.turns if t.turn == k), episode_at=_episode_at(real))
    return {r.check_id: r for r in engine.run_code_checks(f, CheckContext())}[check_id]


def test_cie04_escalation_after_the_focus_turn_is_no_signal_not_a_failure() -> None:
    real = _registered()

    assert CODE_CHECKS["CIE-04"](real, CheckContext()).verdict == "pasa"
    assert _focus(real, 2, "CIE-04").verdict == SIN_SENAL
    assert (_focus(real, 3, "CIE-04").verdict, _focus(real, 3, "CIE-04").turn) == ("pasa", 3)


def test_cie04_escalation_in_the_registration_turn_passes_there() -> None:
    real = _registered(tool("escalate_to_human", reason_category="PAYMENT_VERIFICATION_PENDING"))

    assert _focus(real, 2, "CIE-04").verdict == "pasa"


def test_cie03_order_without_the_tag_yet_is_no_signal() -> None:
    real = _registered()

    assert CODE_CHECKS["CIE-03"](real, CheckContext()).verdict == "falla"
    assert _focus(real, 2, "CIE-03").verdict == SIN_SENAL


def test_cie03_order_and_tag_in_the_focus_turn_pass() -> None:
    real = _registered(tool("manage_conversation_tag", tag="CONFIRMADO_PAGO_PENDIENTE"))

    assert _focus(real, 2, "CIE-03").verdict == "pasa"
    assert _focus(real, 3, "CIE-03").verdict == SIN_SENAL


def test_cie03b_safety_net_in_the_focus_turn_fails_there() -> None:
    real = _registered(turn_state={"order_id": "ord_1", "changes": [
        {"tag": "CONFIRMADO_PAGO_PENDIENTE", "source": "safety_net"}]})

    r = _focus(real, 2, "CIE-03b")
    assert (r.verdict, r.turn) == ("falla", 2)
    assert _focus(real, 1, "CIE-03b").verdict == SIN_SENAL


def test_cie08_guard_before_the_registration_is_no_signal() -> None:
    real = traj(
        T(1, sent=["Tu resumen"], guards=["portavelas_notice_guard"]),
        T(2, inbound="sí", signal="affirmation", tools=[tool("register_order")]),
        order_id="ord_1",
    )

    assert CODE_CHECKS["CIE-08"](real, CheckContext()).verdict == "falla"
    assert _focus(real, 1, "CIE-08").verdict == SIN_SENAL
    assert _focus(real, 2, "CIE-08").verdict == SIN_SENAL


def test_gho02_form_turn_has_no_signal_and_the_ghost_turn_is_judged() -> None:
    real = traj(
        T(1, inbound="sí", signal="affirmation", tools=[tool("request_shipping_details")], at_ms=0),
        T(2, trigger="ghost", inbound="[SISTEMA]", at_ms=100_000),
    )

    assert _focus(real, 1, "GHO-02").verdict == SIN_SENAL
    assert (_focus(real, 2, "GHO-02").verdict, _focus(real, 2, "GHO-02").turn) == ("falla", 2)


def test_tag01_closing_tag_fallback_is_no_signal_in_turn_mode() -> None:
    real = traj(T(1, sent=["Hola"]), T(2, inbound="ok", sent=["Listo"]), closing_tag="CONFIRMADO_SIN_DATOS")

    assert CODE_CHECKS["TAG-01"](real, CheckContext()).verdict == "falla"
    assert _focus(real, 2, "TAG-01").verdict == SIN_SENAL


# ── Registro y veredicto ───────────────────────────────────────────────────
def test_registry_declares_which_checks_depend_on_future_turns() -> None:
    assert {c.id for c in CHECKS if c.focus == "future"} == FUTURE_CHECKS
    assert {c.focus for c in CHECKS} == {"turn", "future"}
    payload = {c["id"]: c for c in specs_payload()}
    assert payload["CIE-04"]["focus"] == "future"
    assert payload["DES-01"]["focus"] == "turn"
    assert REGISTRY_VERSION == 4


def test_no_signal_is_a_verdict_counted_apart_and_outside_compliance() -> None:
    assert "sin_senal" in VERDICTS
    real = _registered()
    results = [
        CheckResult("DES-01", "pasa", turn=3),
        CheckResult("CIE-04", "sin_senal", turn=3),
        CheckResult("CIE-03", "sin_senal", turn=3),
    ]

    card = compute_scorecard(real, SPECS_BY_ID, results)

    assert card.counts["sin_senal"] == 2
    assert card.compliance == 1.0
    assert card.verdict == "PASA"
