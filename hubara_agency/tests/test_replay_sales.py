"""Replay test del workflow de Sales (F6.2 / ADR-005).

Carga la history sintetica capturada por `tests/fixtures/generate_fixtures.py`
y la re-ejecuta contra el codigo actual de `HubaraSalesSessionWorkflow`. Si la
shape de history cambia (orden de activities, signal/query signature, args
serializables) y la fixture queda desactualizada, el `Replayer` lanzara
`NonDeterminismError` y este test fallara.

Cuando se cambie legitimamente la shape, regenerar la fixture con:

    cd hubara_agency
    uv run python tests/fixtures/generate_fixtures.py

Y bumpear el `_v<N>` del filename (ver `tests/fixtures/README.md`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_session_v2.json"


async def test_sales_session_replay_does_not_diverge() -> None:
    history = WorkflowHistory.from_json("test-sales", FIXTURE.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)


# History REAL de prod (run 5ed9af2d, 2026-09-18) saneada: sin teléfono, sin API
# keys, sin system prompt ni tool definitions. Tiene la forma PRE
# `escalation-ends-turn-v1`: tras `escalate_to_human` hay un `llm_chat` extra
# (el acuse "Listo, la conversación quedó en manos del equipo humano." que le
# llegó al cliente). El código nuevo corta el turno en la escalación, así que
# este replay es la garantía de que el deploy no rompe runs en vuelo (L-9):
# sin el gate `workflow.patched(...)` falla con NondeterminismError
# ('llm_chat' scheduled vs 'record_turn' command). CONGELADA — no se regenera;
# se borra junto con `workflow.deprecate_patch("escalation-ends-turn-v1")`.
PREPATCH_ESCALATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_escalation_prepatch_v1.json"
)


async def test_prepatch_escalation_history_still_replays() -> None:
    history = WorkflowHistory.from_json(
        "test-sales-escalation-prepatch",
        PREPATCH_ESCALATION_FIXTURE.read_text(encoding="utf-8"),
    )
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)


# History PRE `tag-ends-turn-v1` (run b06636a6, misma clase que L-20): tras
# `manage_conversation_tag` el loop todavía pide un `llm_chat` extra (el acuse
# "Etiqueta registrada."). Sintética A PROPÓSITO, y por una razón de fondo: el
# corte nuevo se decide por una clave NUEVA del envelope (`tag_closure`), así
# que una history real de prod (tool results de forma vieja) jamás lo activa y
# NO puede proteger el gate. El único caso en que una history sin el marker
# trae un envelope que el código nuevo cortaría es la ventana de versiones
# mezcladas de un deploy (activity con la tool nueva + workflow task con el
# loop viejo): eso es lo que esta fixture congela. Generada con el código del
# commit 4052c29 (anterior al gate); cubre los dos sitios del corte: turno de
# cliente con `customer_message` y turno admin de cierre por ghosting.
# CONGELADA — no se regenera; se borra junto con
# `workflow.deprecate_patch("tag-ends-turn-v1")`.
PREPATCH_TAG_CLOSURE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_tag_closure_prepatch_v1.json"
)


def _prepatch_tag_closure_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-tag-closure-prepatch",
        PREPATCH_TAG_CLOSURE_FIXTURE.read_text(encoding="utf-8"),
    )


async def test_prepatch_tag_closure_history_still_replays() -> None:
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(_prepatch_tag_closure_history())


@pytest.mark.parametrize(
    ("site", "ungated_from_consultation"),
    [
        # El gate se consulta exactamente DOS veces en la fixture, una por
        # sitio del corte. Se des-gatea cada sitio por separado para probar
        # que la fixture protege a los dos (si no, el primero enmascara al
        # segundo: el replay rompe en el turno del cliente y nunca llega al
        # cierre por ghosting).
        ("turno de cliente con customer_message", 1),
        ("turno admin de cierre por ghosting", 2),
    ],
)
async def test_prepatch_tag_closure_history_breaks_without_the_gate(
    monkeypatch, site: str, ungated_from_consultation: int
) -> None:
    """Control negativo: la fixture PROTEGE el gate (un replay que no puede
    fallar no prueba nada). Se simula el corte SIN su `workflow.patched`: el
    turno termina en el tag y el replay choca con el `llm_chat` que la history
    sí agendó."""
    from temporalio import workflow

    real_patched = workflow.patched
    consultations = {"n": 0}

    def ungated(patch_id: str) -> bool:
        if patch_id != "tag-ends-turn-v1":
            return real_patched(patch_id)
        consultations["n"] += 1
        if consultations["n"] >= ungated_from_consultation:
            return True  # como si la rama nueva no estuviera detrás del gate
        return False  # lo que devuelve el gate real al replayear esta history

    monkeypatch.setattr(workflow, "patched", ungated)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    # El choque exacto: la history agendó el llm_chat del acuse y el código sin
    # gate, que ya cortó el turno, agenda `record_turn`.
    with pytest.raises(workflow.NondeterminismError, match="'llm_chat'.*'record_turn'"):
        await replayer.replay_workflow(_prepatch_tag_closure_history())
    assert consultations["n"] == ungated_from_consultation, (
        f"{site}: el gate se consultó {consultations['n']} veces"
    )


# History SINTÉTICA con el clasificador PRENDIDO (capas ①②③ del laboratorio,
# PR 14, `workflow.patched("perception-v1")`): turno en sombra + turno activo
# con verificación y complemento. Una vez en producción, las sesiones en vuelo
# traen esta forma; cambiar commands en esos caminos sin su propio gate las
# rompería al redeployar (L-9). Generada con el código de `lab/integracion`
# (9afa43b4), ANTES de que la verificación leyera todo lo que el cliente recibe
# en el turno: ese cambio solo toca el payload de `verify_coverage` (L-22) y
# este replay lo prueba. CONGELADA — no se regenera (procedencia en
# `fixtures/generate_perception_v1_fixture.py`); se suman historias REALES
# saneadas cuando el clasificador corra en sombra en producción (A-PM07) y se
# borra junto con `workflow.deprecate_patch("perception-v1")`.
PERCEPTION_V1_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_perception_v1.json"


def _perception_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-perception-v1", PERCEPTION_V1_FIXTURE.read_text(encoding="utf-8")
    )


async def test_a_session_with_the_classifier_on_still_replays() -> None:
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(_perception_history())


async def test_the_classifier_history_breaks_without_its_gate(monkeypatch) -> None:
    """Control negativo: con `perception-v1` en False (código sin capas) el
    turno no agenda la percepción y el replay choca con la history: la
    fixture sí ejercita los commands del clasificador."""
    from temporalio import workflow

    real_patched = workflow.patched
    monkeypatch.setattr(workflow, "patched", lambda patch_id: False if patch_id == "perception-v1" else real_patched(patch_id))

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    with pytest.raises(workflow.NondeterminismError):
        await replayer.replay_workflow(_perception_history())


# History SINTÉTICA del bot nuevo (`HubaraSalesSessionWorkflowV2`) con una
# ráfaga, con la forma ANTERIOR a las ráfagas sin cortes (incidente
# 2026-10-06: el cliente mandó la dirección en 6 mensajes y el bot respondió a
# mitad). Hay sesiones vivas del V2 (los números de prueba): sin sus gates, el
# deploy las rompe. Cubre los tres: el tope viejo de 2 reinicios
# (`burst-time-budget-v1`), un mensaje durante el egreso que igual se graba y
# se envía (`turn-interrupt-before-record-v1`) y los intentos cortados sin
# registrar su costo (`turn-interrupt-cost-v1`). Generada con el código de
# `main` en f83d51b4. CONGELADA — no se regenera (procedencia en
# `fixtures/generate_sales_v2_burst_prepatch_fixture.py`); se borra junto con
# el `workflow.deprecate_patch(...)` de los tres gates.
PREPATCH_V2_BURST_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_v2_burst_prepatch_v1.json"


def _prepatch_v2_burst_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-v2-burst-prepatch", PREPATCH_V2_BURST_FIXTURE.read_text(encoding="utf-8")
    )


async def test_prepatch_v2_burst_history_still_replays() -> None:
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    await replayer.replay_workflow(_prepatch_v2_burst_history())


@pytest.mark.parametrize(
    ("gate", "ungated_at", "clash"),
    [
        # Turno 1, primer corte: la history relanzó el turno enseguida; sin el
        # gate, el turno espera el silencio de la ráfaga (un timer que la
        # history no tiene) antes de relanzar.
        ("burst-time-budget-v1", 1, "Timer machine does not handle this event"),
        # Turno 1, 3.er intento (3.ª consulta: las dos primeras son las pausas
        # de los reinicios 1 y 2): la history respondió con el tope viejo; sin
        # el gate, el turno se recompone otra vez en vez de pedir el egreso.
        ("burst-time-budget-v1", 3, "'decide_egress' does not match .*'get_active_episode_id'"),
        # Turno 2: llegó un mensaje durante el egreso y la history grabó el
        # turno; sin el gate, el turno se corta antes de grabarlo y se relanza.
        ("turn-interrupt-before-record-v1", None, "'record_turn' does not match .*'get_active_episode_id'"),
        # Turno 1, primer corte: la history relanzó el turno; sin el gate, el
        # corte registra antes su costo.
        ("turn-interrupt-cost-v1", None, "'get_active_episode_id' does not match .*'record_episode_llm_usage'"),
    ],
)
async def test_prepatch_v2_burst_history_breaks_without_each_gate(
    monkeypatch, gate: str, ungated_at: int | None, clash: str
) -> None:
    """Control negativo: la fixture PROTEGE cada gate, en cada sitio (un
    replay que no puede fallar no prueba nada). Se simula la rama nueva SIN su
    `workflow.patched` (en todas las consultas, o solo en la `ungated_at`, para
    que un sitio no enmascare al otro) y el replay choca con lo que la history
    sí grabó."""
    from temporalio import workflow

    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    real_patched = workflow.patched
    consulted = {"n": 0}

    def ungated(patch_id: str) -> bool:
        if patch_id != gate:
            return real_patched(patch_id)
        consulted["n"] += 1
        # Como si la rama nueva no estuviera detrás del gate; en las demás
        # consultas, lo que devuelve el gate real al re-jugar esta history.
        return ungated_at is None or consulted["n"] == ungated_at

    monkeypatch.setattr(workflow, "patched", ungated)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    with pytest.raises(workflow.NondeterminismError, match=clash):
        await replayer.replay_workflow(_prepatch_v2_burst_history())
    assert consulted["n"] >= (ungated_at or 1), f"el gate {gate} se consultó {consulted['n']} veces"
# History SINTÉTICA de los turnos de SISTEMA del V1 ANTES del gate
# `system-turn-bogota-clock-v1` (caso 4567 del laboratorio, caso-fotos-0929-r3:
# los turnos que arma el workflow sin mensaje del cliente no traían la hora de
# Bogotá y el LLM solo veía la del contenedor, en UTC). Cubre el traspaso de
# remarketing al arrancar, el que llega con la sesión dormida, el que llega a
# mitad de sesión y el cierre por abandono; el complemento de la capa ③ lo cubre
# `history_sales_perception_v1.json`. Las sesiones en vuelo al desplegar traen
# esta forma: la activity de la hora en esos caminos sin su gate las rompería
# (L-9). CONGELADA — no se regenera (procedencia en
# `fixtures/generate_system_turns_preclock_fixture.py`); se borra junto con
# `workflow.deprecate_patch("system-turn-bogota-clock-v1")`.
SYSTEM_TURNS_PRECLOCK_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_system_turns_preclock_v1.json"
)
_CLOCK_GATE = "system-turn-bogota-clock-v1"


def _system_turns_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-system-turns-preclock",
        SYSTEM_TURNS_PRECLOCK_FIXTURE.read_text(encoding="utf-8"),
    )


async def test_system_turns_recorded_before_the_clock_still_replay() -> None:
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(_system_turns_history())


@pytest.mark.parametrize(
    ("history", "site", "ungated_from_consultation"),
    [
        # El gate se consulta una vez por turno de sistema. Se des-gatea cada
        # sitio por separado para probar que las fixtures protegen a todos (si
        # no, el primero enmascara a los demás).
        pytest.param(_system_turns_history, "traspaso de remarketing al arrancar", 1, id="handoff-start"),
        pytest.param(_system_turns_history, "traspaso con la sesión dormida", 2, id="handoff-idle"),
        pytest.param(_system_turns_history, "traspaso a mitad de sesión", 3, id="handoff-refresh"),
        pytest.param(_system_turns_history, "cierre por abandono", 4, id="ghost"),
        pytest.param(_perception_history, "complemento de la capa ③", 1, id="complement"),
    ],
)
async def test_the_system_turn_histories_break_if_the_clock_skips_its_gate(
    monkeypatch, history, site: str, ungated_from_consultation: int
) -> None:
    """Control negativo: las fixtures PROTEGEN el gate de la hora. Con el gate
    en True desde el sitio N (como si la activity no estuviera detrás de él),
    el replay choca con la history, que en ese punto no la agendó."""
    from temporalio import workflow

    real_patched = workflow.patched
    consultations = {"n": 0}

    def ungated(patch_id: str) -> bool:
        if patch_id != _CLOCK_GATE:
            return real_patched(patch_id)
        consultations["n"] += 1
        return consultations["n"] >= ungated_from_consultation

    monkeypatch.setattr(workflow, "patched", ungated)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    with pytest.raises(workflow.NondeterminismError):
        await replayer.replay_workflow(history())
    assert consultations["n"] == ungated_from_consultation, (
        f"{site}: el gate se consultó {consultations['n']} veces"
    )


# La misma forma en el bot nuevo (`HubaraSalesSessionWorkflowV2`): desde el
# 2026-10-07 todas las conversaciones corren en el V2, así que sus sesiones en
# vuelo al desplegar traen los turnos de sistema SIN la activity de la hora.
# Generada con el código de `main` en f89716ad. CONGELADA — no se regenera; se
# borra junto con el gate.
V2_SYSTEM_TURNS_PRECLOCK_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_v2_system_turns_preclock_v1.json"
)


def _v2_system_turns_history() -> WorkflowHistory:
    return WorkflowHistory.from_json(
        "test-sales-v2-system-turns-preclock",
        V2_SYSTEM_TURNS_PRECLOCK_FIXTURE.read_text(encoding="utf-8"),
    )


async def test_v2_system_turns_recorded_before_the_clock_still_replay() -> None:
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    await replayer.replay_workflow(_v2_system_turns_history())


@pytest.mark.parametrize(
    ("site", "ungated_from_consultation"),
    [
        pytest.param("traspaso de remarketing al arrancar", 1, id="handoff-start"),
        pytest.param("traspaso con la sesión dormida", 2, id="handoff-idle"),
        pytest.param("traspaso a mitad de sesión", 3, id="handoff-refresh"),
        pytest.param("cierre por abandono", 4, id="ghost"),
    ],
)
async def test_the_v2_system_turn_history_breaks_if_the_clock_skips_its_gate(
    monkeypatch, site: str, ungated_from_consultation: int
) -> None:
    """Control negativo en el V2: el gate se consulta una vez por turno de
    sistema y la fixture protege a cada sitio."""
    from temporalio import workflow

    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    real_patched = workflow.patched
    consultations = {"n": 0}

    def ungated(patch_id: str) -> bool:
        if patch_id != _CLOCK_GATE:
            return real_patched(patch_id)
        consultations["n"] += 1
        return consultations["n"] >= ungated_from_consultation

    monkeypatch.setattr(workflow, "patched", ungated)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    with pytest.raises(workflow.NondeterminismError):
        await replayer.replay_workflow(_v2_system_turns_history())
    assert consultations["n"] == ungated_from_consultation, (
        f"{site}: el gate se consultó {consultations['n']} veces"
    )


# Histories SINTÉTICAS del bot nuevo con la ronda de lo prometido (incidente
# del 2026-10-09: «Te paso el formulario» sin `request_shipping_details`; gate
# `promised-actions-round-v1`). La ronda se decide por una clave NUEVA del
# resultado de `send_reply` (`promises`): una history real de antes del deploy
# jamás la activa y no protege el gate. `..._prepatch_v1` es la ventana de
# versiones mezcladas (la activity ya devuelve `promises`, el workflow todavía
# sin la ronda: el texto sale en el `send_reply`); `..._round_v1`, el control
# positivo con el marcador (la ronda, el formulario y el texto retenido que
# sale). CONGELADAS — no se regeneran (procedencia en
# `fixtures/generate_sales_v2_promises_fixtures.py`); se borran junto con
# `workflow.deprecate_patch("promised-actions-round-v1")`.
_PROMISES_GATE = "promised-actions-round-v1"
PROMISES_PREPATCH_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_v2_promises_prepatch_v1.json"
PROMISES_ROUND_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_v2_promises_round_v1.json"


def _promises_history(path: Path) -> WorkflowHistory:
    return WorkflowHistory.from_json(f"test-{path.stem}", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture", [PROMISES_PREPATCH_FIXTURE, PROMISES_ROUND_FIXTURE], ids=["prepatch", "ronda"])
async def test_the_promise_histories_replay(fixture: Path) -> None:
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    await replayer.replay_workflow(_promises_history(fixture))


@pytest.mark.parametrize(
    ("fixture", "forced", "clash"),
    [
        # Sin el gate (la ronda siempre): la history pre-patch grabó el egreso
        # después del `send_reply`; el código pide otra ronda al modelo.
        (PROMISES_PREPATCH_FIXTURE, True, "llm_chat|decide_egress"),
        # Con el gate apagado: el control positivo grabó el marcador de la
        # ronda y el código no lo pide (deja salir el texto en el `send_reply`).
        (PROMISES_ROUND_FIXTURE, False, "patch marker encountered for change promised-actions-round-v1"),
    ],
    ids=["prepatch-sin-gate", "ronda-sin-ronda"],
)
async def test_the_promise_histories_break_without_the_gate(
    monkeypatch, fixture: Path, forced: bool, clash: str
) -> None:
    """Control negativo: las dos histories PROTEGEN el gate (un replay que no
    puede fallar no prueba nada)."""
    from temporalio import workflow

    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    real_patched = workflow.patched
    consulted = {"n": 0}

    def forced_gate(patch_id: str) -> bool:
        if patch_id != _PROMISES_GATE:
            return real_patched(patch_id)
        consulted["n"] += 1
        return forced

    monkeypatch.setattr(workflow, "patched", forced_gate)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    with pytest.raises(workflow.NondeterminismError, match=clash):
        await replayer.replay_workflow(_promises_history(fixture))
    assert consulted["n"] == 1, f"el gate se consultó {consulted['n']} veces"


# ── Gate `restart-carries-reads-v1` (turno 1 de …7392, 2026-10-08) ─────────
# El reinicio recibe lo que el intento cortado ya leyó: cuenta como usado para
# el contrato del turno y, con eso, cambia cuántas rondas pide. Las dos
# historias son sintéticas (un contrato que pide `search_products` para el
# precio): `..._prepatch_v1` es el código anterior (el reinicio sin la
# búsqueda, el contrato retiene el texto y pide otra ronda); `..._v1`, el
# control positivo con el marcador (el texto sale en la primera ronda).
# CONGELADAS — no se regeneran (procedencia en
# `fixtures/generate_sales_v2_carry_reads_fixtures.py`); se borran junto con
# `workflow.deprecate_patch("restart-carries-reads-v1")`.
_CARRY_GATE = "restart-carries-reads-v1"
CARRY_PREPATCH_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_v2_carry_reads_prepatch_v1.json"
CARRY_FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_v2_carry_reads_v1.json"


@pytest.mark.parametrize("fixture", [CARRY_PREPATCH_FIXTURE, CARRY_FIXTURE], ids=["prepatch", "lecturas"])
async def test_the_carry_reads_histories_replay(fixture: Path) -> None:
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    await replayer.replay_workflow(_promises_history(fixture))


@pytest.mark.parametrize(
    ("fixture", "forced", "clash"),
    [
        # Sin el gate (siempre lleva lo leído): la history pre-patch grabó la
        # ronda del contrato; con la búsqueda contada, el código no la pide.
        (CARRY_PREPATCH_FIXTURE, True, "llm_chat"),
        # Con el gate apagado: el control positivo grabó el marcador y el
        # código no lo pide (y el contrato pediría otra ronda).
        (CARRY_FIXTURE, False, "patch marker encountered for change restart-carries-reads-v1"),
    ],
    ids=["prepatch-sin-gate", "lecturas-sin-lecturas"],
)
async def test_the_carry_reads_histories_break_without_the_gate(
    monkeypatch, fixture: Path, forced: bool, clash: str
) -> None:
    """Control negativo: las dos histories PROTEGEN el gate."""
    from temporalio import workflow

    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    real_patched = workflow.patched
    consulted = {"n": 0}

    def forced_gate(patch_id: str) -> bool:
        if patch_id != _CARRY_GATE:
            return real_patched(patch_id)
        consulted["n"] += 1
        return forced

    monkeypatch.setattr(workflow, "patched", forced_gate)

    replayer = Replayer(workflows=[HubaraSalesSessionWorkflowV2])
    with pytest.raises(workflow.NondeterminismError, match=clash):
        await replayer.replay_workflow(_promises_history(fixture))
    assert consulted["n"] == 1, f"el gate se consultó {consulted['n']} veces"
