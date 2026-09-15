"""Servicio del scorecard (HU-SC-1): carga la trayectoria del episodio desde el
vault (trazas si existen, reconstrucción legada si no) y arma el registro."""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.shared import turn_traces

SESSION = "wa_100000000001"


def _write(vault: Path, rel: str, content: str) -> None:
    path = vault / SESSION / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _metadata(vault: Path) -> None:
    _write(vault, "metadata.json", json.dumps({
        "episodes": [
            {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 2, "closing_tag": "RECHAZO"},
            {"episode_id": "ep_002", "msgs_count_at_start": 2, "closing_tag": "INTERESADO"},
        ]
    }))


def test_load_trajectory_prefers_turn_traces(tmp_path: Path) -> None:
    _metadata(tmp_path)
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "trigger": "customer",
                                                 "sent_texts": ["¿Para ti o para regalo?"], "tools": []})
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_001", "turn": 1, "trigger": "customer"})

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "trace"
    assert [t.sent_texts for t in traj.turns] == [("¿Para ti o para regalo?",)]
    assert traj.closing_tag == "INTERESADO"


def test_load_trajectory_falls_back_to_legacy_dashboard_events(tmp_path: Path) -> None:
    _metadata(tmp_path)
    events = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Buenos días! Bienvenido a *Hubara*"},
        {"role": "user", "content": "quiero ver velas"},
        {"role": "assistant", "kind": "ui_component", "component_kind": "products_list", "content": "catálogo"},
    ]
    _write(tmp_path, f"sessions/{SESSION}.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "legacy"
    assert [t.inbound_text for t in traj.turns] == ["quiero ver velas"]
    assert traj.turns[0].intents == ("products_list",)


def test_score_trajectory_builds_record_with_verdict_and_judge_flag(tmp_path: Path) -> None:
    from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix

    record = service.score_trajectory(pr281_before_fix(), CATALOG_CTX)

    assert record["verdict"] == "FALLA"
    assert record["judge"] is False
    assert record["registry_version"] >= 1
    assert any(r["check_id"] == "CON-01" and r["verdict"] == "falla" for r in record["results"])

    with_judge = service.score_trajectory(
        pr281_before_fix(), CheckContext(), judge_results=[CheckResult("DES-04", "falla", turn=3, source="judge")]
    )
    assert with_judge["judge"] is True
    assert any(r["check_id"] == "DES-04" and r["source"] == "judge" for r in with_judge["results"])


def test_episode_that_started_before_traces_is_partial(tmp_path: Path) -> None:
    _metadata(tmp_path)
    events = [
        {"role": "user", "content": "hola", "timestamp": "2026-09-14T14:00:00+00:00"},
        {"role": "assistant", "content": "¡Buenos días!", "timestamp": "2026-09-14T14:00:05+00:00"},
        {"role": "user", "content": "¿cuánto cuesta?", "timestamp": "2026-09-14T14:10:00+00:00"},
        {"role": "user", "content": "gracias", "timestamp": "2026-09-14T15:00:00+00:00"},
    ]
    _write(tmp_path, f"sessions/{SESSION}.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")
    # La traza arranca con el deploy, a las 15:00 (turn_started_ms en epoch ms).
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "trigger": "customer",
                                                 "turn_started_ms": 1_789_398_000_000})

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "partial"


# ── Espera de la traza del turno de cierre ───────────────────────────────────
# El evento de cierre se despacha ANTES de que el turno termine de enviar y de
# persistir su traza (sales_session.py). Sin esperar, el scorecard evalúa el
# episodio sin su último turno y reprueba en falso.

_CLOSED_AT = 1_789_398_000_000


def _closed_metadata(vault: Path, closed_at_ms: int | None) -> None:
    ep = {"episode_id": "ep_002", "started_at_ms": _CLOSED_AT - 600_000, "closing_tag": "INTERESADO"}
    if closed_at_ms is not None:
        ep["closed_at_ms"] = closed_at_ms
    _write(vault, "metadata.json", json.dumps({"episodes": [ep]}))


async def test_await_closing_trace_returns_once_the_closing_turn_landed(tmp_path: Path) -> None:
    _closed_metadata(tmp_path, _CLOSED_AT)
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "recorded_at_ms": _CLOSED_AT - 60_000})
    polls: list[float] = []

    async def sleep(s: float) -> None:
        polls.append(s)
        if len(polls) == 2:  # la traza del turno de cierre aterriza en el 2º sondeo
            turn_traces.append_trace(
                tmp_path, SESSION, {"episode_id": "ep_002", "turn": 2, "recorded_at_ms": _CLOSED_AT + 8_000}
            )

    landed = await service.await_closing_trace(
        tmp_path, SESSION, "ep_002", timeout_s=5, poll_s=0.001, now_ms=_CLOSED_AT + 30_000, sleep=sleep,
    )

    assert landed is True
    assert len(polls) == 2


async def test_await_closing_trace_gives_up_after_the_timeout(tmp_path: Path) -> None:
    _closed_metadata(tmp_path, _CLOSED_AT)
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "recorded_at_ms": _CLOSED_AT - 60_000})

    async def sleep(_s: float) -> None:
        return None

    landed = await service.await_closing_trace(
        tmp_path, SESSION, "ep_002", timeout_s=0.01, poll_s=0.001, now_ms=_CLOSED_AT + 30_000, sleep=sleep,
    )

    assert landed is False


async def test_await_closing_trace_skips_old_closures_and_open_episodes(tmp_path: Path) -> None:
    calls: list[float] = []

    async def sleep(s: float) -> None:
        calls.append(s)

    _closed_metadata(tmp_path, _CLOSED_AT)  # cerró hace una hora: ningún turno de cierre en vuelo
    assert await service.await_closing_trace(
        tmp_path, SESSION, "ep_002", now_ms=_CLOSED_AT + 3_600_000, sleep=sleep
    ) is True
    _closed_metadata(tmp_path, None)  # episodio abierto: nada que esperar
    assert await service.await_closing_trace(tmp_path, SESSION, "ep_002", now_ms=_CLOSED_AT, sleep=sleep) is True
    assert calls == []


def test_score_trajectory_records_the_episode_date_from_its_closure() -> None:
    from datetime import datetime, timezone

    from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

    closed = Trajectory(session_id=SESSION, episode_id="ep_002", fidelity="empty", turns=(),
                        started_at_ms=_CLOSED_AT - 86_400_000 * 3, closed_at_ms=_CLOSED_AT)
    expected = datetime.fromtimestamp(_CLOSED_AT / 1000, timezone.utc).date().isoformat()

    assert service.score_trajectory(closed, CheckContext())["episode_date"] == expected
    undated = Trajectory(session_id=SESSION, episode_id="ep_003", fidelity="empty", turns=())
    assert service.score_trajectory(undated, CheckContext())["episode_date"] is None


def test_message_waiting_through_a_deploy_does_not_make_the_episode_partial(tmp_path: Path) -> None:
    """Un worker reiniciado (deploy ~5 min) procesa el mensaje pendiente con
    retraso: eso no es un episodio anterior a la traza."""
    _metadata(tmp_path)
    events = [  # los dos primeros son de ep_001 (msgs_count_at_start=2 en ep_002)
        {"role": "user", "content": "hola", "timestamp": "2026-09-14T13:00:00+00:00"},
        {"role": "assistant", "content": "¡Buenos días!", "timestamp": "2026-09-14T13:00:05+00:00"},
        {"role": "user", "content": "hola de nuevo", "timestamp": "2026-09-14T14:54:00+00:00"},
        {"role": "user", "content": "¿cuánto cuesta?", "timestamp": "2026-09-14T14:55:00+00:00"},
    ]
    _write(tmp_path, f"sessions/{SESSION}.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")
    # El turno arranca a las 15:00:00 UTC (epoch ms), 6 minutos después del primer mensaje.
    turn_started = 1_789_398_000_000
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "trigger": "customer",
                                                 "turn_started_ms": turn_started})

    assert service.load_trajectory(tmp_path, SESSION, "ep_002").fidelity == "trace"


def test_episode_date_ignores_implausible_timestamps() -> None:
    """Un `started_at_ms` roto (época chica, segundos, fixture sintética) no
    puede fechar el episodio en 1970 y sacarlo de la ventana en silencio."""
    from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

    tiny = Trajectory(session_id=SESSION, episode_id="ep_004", fidelity="empty", turns=(), started_at_ms=60_000)
    seconds = Trajectory(session_id=SESSION, episode_id="ep_005", fidelity="empty", turns=(), closed_at_ms=1_789_398_000)

    assert service.episode_date(tiny) is None
    assert service.episode_date(seconds) is None


def test_judge_flag_is_false_when_every_judge_call_errored() -> None:
    """Primer informe: el registro decía `judge=True` aunque las 13 llamadas
    fallaron por 429. El panel prendía «con juez» sobre resultados vacíos."""
    from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

    empty = Trajectory(session_id=SESSION, episode_id="ep_002", fidelity="empty", turns=())
    errored = [
        CheckResult("EST-04", "desconocido", critique="error del juez: RateLimitError 429", source="judge"),
        CheckResult("TAG-03", "desconocido", critique="error del juez: RateLimitError 429", source="judge"),
    ]
    mixed = [*errored, CheckResult("DES-04", "falla", turn=3, source="judge")]

    rec = service.score_trajectory(empty, CheckContext(), judge_results=errored)
    assert (rec["judge"], rec["judge_errors"]) == (False, 2)
    rec = service.score_trajectory(empty, CheckContext(), judge_results=mixed)
    assert (rec["judge"], rec["judge_errors"]) == (True, 2)


def _daily_vault(vault: Path) -> None:
    import os

    def session(sid: str, episodes: list[dict], events: list[dict]) -> None:
        d = vault / sid
        (d / "sessions").mkdir(parents=True, exist_ok=True)
        (d / "metadata.json").write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
        (d / "sessions" / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        os.utime(d / "sessions" / f"{sid}.jsonl", (_CLOSED_AT / 1000, _CLOSED_AT / 1000))

    hi = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "¡Buenas tardes!"}]
    base = {"episode_id": "ep_001", "started_at_ms": _CLOSED_AT - 3_600_000, "msgs_count_at_start": 0}
    closed = {**base, "closed_at_ms": _CLOSED_AT - 600_000, "msgs_count_at_close": 2, "closing_tag": "RECHAZO"}
    session("wa_100000000011", [base], hi)
    session("wa_100000000012", [closed], hi)
    session("wa_100000000013", [closed], hi)
    session("wa_100000000014", [base], [{"role": "assistant", "content": "Hola de nuevo 🌿"}])
    from src.plugins.chats.agent.sales_eval.scorecard import store

    closed_ts = datetime_iso(_CLOSED_AT - 300_000)
    store.append_scorecard(store.scorecards_dir(vault), {"session_id": "wa_100000000012", "episode_id": "ep_001",
                                                        "verdict": "PASA", "ts": closed_ts, "results": []})


def datetime_iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def test_daily_units_score_open_and_unscored_closed_episodes_of_any_length(tmp_path: Path) -> None:
    """Barrido diario del scorecard: el cierre ya califica al episodio que
    cerró; el barrido suma los episodios ABIERTOS con actividad (INTERESADO,
    ruta humano… nunca emiten cierre) y los cerrados que se quedaron sin
    scorecard. Sin mínimo de turnos: un saludo sin respuesta también se evalúa
    (APE-03). Episodios sin mensajes del cliente no son del asesor."""
    from src.plugins.chats.agent.sales_eval.evals.contracts import EvalWindowInput

    _daily_vault(tmp_path)

    units = service.daily_scorecard_units(
        tmp_path, EvalWindowInput(lookback_hours=24, max_conversations=100), now_ms=_CLOSED_AT + 60_000
    )

    assert sorted(units) == ["wa_100000000011::ep_001", "wa_100000000013::ep_001"]
