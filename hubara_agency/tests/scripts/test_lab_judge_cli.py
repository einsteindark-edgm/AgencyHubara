"""`scripts/lab_judge.py`: Claude Code califica la cola del juez de una corrida
del laboratorio desde S3 (`runs/<corrida>/judge/`) y ordena a la caja aplicar
las calificaciones (modo solo evaluar, la misma imagen de la corrida)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import lab_judge as cli
from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import JudgeQueue
from src.sdk.labkit import FilesystemLabStore

RUN = "run-20260928-a1b2"


def _prompt(check: str, turns: str) -> str:
    return (f"Eres auditor.\nCRITERIO {check} — nombre\n"
            f"- Juzga SOLO este criterio y SOLO los turnos marcados ★ CANDIDATA: {turns}.\n"
            "CONVERSACIÓN:\n...\n")


def _store_with_queue(tmp_path: Path) -> tuple[FilesystemLabStore, str]:
    store = FilesystemLabStore(tmp_path / "bucket")
    local = JudgeQueue(tmp_path / "box")
    pid = local.add_pending(_prompt("EST-07", "T3, T6"), unit="A1/0/wa_573001234567/ep_001")
    store.put_bytes(f"runs/{RUN}/judge/pending.jsonl", (tmp_path / "box" / "pending.jsonl").read_bytes())
    store.put_bytes(f"orders/{RUN}.json", json.dumps({"run_id": RUN, "image": "ghcr.io/o/agencyhubara:abc"}).encode())
    return store, pid


def _turns(*pairs) -> str:
    return json.dumps({"turnos": [{"turno": k, "veredicto": v, "evidencia": "", "critica": "ok"} for k, v in pairs]})


def test_the_page_brings_the_pending_prompts_of_the_run(tmp_path: Path) -> None:
    store, pid = _store_with_queue(tmp_path)

    assert pid in cli.run_page(store, RUN, max_chars=20_000)
    assert cli.run_summary(store, RUN)["pendientes"] == 1


def test_my_answers_go_back_to_s3_and_leave_nothing_pending(tmp_path: Path) -> None:
    store, pid = _store_with_queue(tmp_path)

    saved, errors = cli.run_respond(store, RUN, [json.dumps({"id": pid, "raw": _turns((3, "pasa"), (6, "falla"))})])

    assert (saved, errors) == (1, [])
    answers = [json.loads(line) for line in store.get_bytes(f"runs/{RUN}/judge/answers.jsonl").decode().splitlines()]
    assert [a["id"] for a in answers] == [pid]
    assert cli.run_summary(store, RUN)["pendientes"] == 0


def test_an_answer_that_misses_a_candidate_does_not_reach_s3(tmp_path: Path) -> None:
    store, pid = _store_with_queue(tmp_path)

    saved, errors = cli.run_respond(store, RUN, [json.dumps({"id": pid, "raw": _turns((3, "pasa"))})])

    assert saved == 0 and len(errors) == 1
    assert store.get_bytes(f"runs/{RUN}/judge/answers.jsonl") is None


def test_apply_starts_the_box_and_orders_an_evaluate_only_run_with_the_run_image(tmp_path: Path) -> None:
    store, _ = _store_with_queue(tmp_path)
    calls: list = []

    class Launcher:
        def start_box(self) -> None:
            calls.append("start")

        def evaluate(self, run_id: str, image: str) -> str:
            calls.append(("evaluate", run_id, image))
            return "dispatched_evaluate"

    assert cli.run_apply(store, RUN, Launcher()) == "dispatched_evaluate"
    assert calls == ["start", ("evaluate", RUN, "ghcr.io/o/agencyhubara:abc")]
