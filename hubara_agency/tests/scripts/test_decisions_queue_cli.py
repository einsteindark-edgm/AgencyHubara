"""CLI de la cola de desacuerdos regla ↔ Jev (motor de decisiones, paso 2).

Claude Code califica los desacuerdos (decisión del operador, 2026-09-28) por
SSM en la caja de producción, como la cola del juez: `resumen`, `siguiente`
(los pendientes enteros hasta un presupuesto de caracteres: la salida de SSM
se corta en ~24.000), `responder` (una línea JSON por etiqueta) y `puntaje`
(quién tenía razón, por capacidad)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import decisions_queue as cli
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog


def _seed(vault: Path) -> tuple[str, str]:
    log = DisagreementLog(vault)
    a = log.record(capability="compra", state="CONTEXTO …\n[1] Te confirmo, sí la quiero", rule=["deferral", "text"],
                   jev=["affirmation", "text"], model="typesafe/jev-1.13-20260917", answers=[], session_id="wa_573001234567")
    b = log.record(capability="baja", state="[1] no me escriban más", rule=False, jev=True, model="m", answers=[],
                   session_id="wa_573001234567")
    return a, b


def test_summary_counts_pending_by_capability(tmp_path: Path) -> None:
    _seed(tmp_path)

    assert cli.summary(DisagreementLog(tmp_path)) == {"pendientes": 2, "por_capacidad": {"baja": 1, "compra": 1}}


def test_next_prints_whole_items_within_the_budget(tmp_path: Path) -> None:
    a, b = _seed(tmp_path)

    page = cli.page(DisagreementLog(tmp_path), max_chars=100_000)
    small = cli.page(DisagreementLog(tmp_path), max_chars=10)

    assert a in page and b in page and "Te confirmo, sí la quiero" in page
    assert '"rule"' in page and '"jev"' in page
    assert small.count("===") >= 1  # siempre al menos uno entero


def test_respond_labels_and_score_counts_who_won(tmp_path: Path) -> None:
    a, b = _seed(tmp_path)
    lines = [
        json.dumps({"id": a, "label": ["affirmation", "text"], "note": "confirma la compra"}),
        json.dumps({"id": b, "label": True}),
        json.dumps({"id": "no-existe", "label": True}),
        "no es json",
    ]

    saved, errors = cli.respond(DisagreementLog(tmp_path), lines)

    assert saved == 2 and len(errors) == 2
    assert cli.summary(DisagreementLog(tmp_path))["pendientes"] == 0
    assert cli.score(DisagreementLog(tmp_path)) == {
        "baja": {"labeled": 1, "jev": 1, "rule": 0, "neither": 0},
        "compra": {"labeled": 1, "jev": 1, "rule": 0, "neither": 0},
    }


def test_main_reads_the_vault_queue(tmp_path: Path, capsys) -> None:
    _seed(tmp_path)

    assert cli.main(["--vault", str(tmp_path), "resumen"]) == 0
    assert json.loads(capsys.readouterr().out)["pendientes"] == 2
