"""`scripts/lab_bench_labels.py`: el banco de referencia del motor de
decisiones, en el S3 del laboratorio (`bench/<banco>/reference/`).

`preparar` baja el banco a un directorio temporal (que se borra), elige los
turnos y sube los ítems del perfil; Claude Code los etiqueta (`siguiente` y
`responder`); `preguntar` guarda lo que responde Jev y `metricas` lo compara
con las etiquetas, pregunta por pregunta. Las etiquetas son por cuestionario:
dos perfiles con el mismo cuestionario las comparten; con otro, no."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import lab_bench_labels as cli
from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_lab.reference_bank import build_item
from src.sdk.connectorkit import FakePerceptionAdapter
from src.sdk.labkit import FilesystemLabStore

BENCH = "bench-20260928-a1b2"
SID = "wa_573001234567"
WS = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
T0 = 1_789_500_000_000
CUT = 1_789_000_000_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


EVENTS = [
    {"role": "user", "content": "Hola, ¿cuánto vale el Duo Zodiacal?", "timestamp": _iso(T0 + 1_000), "wamid": "wamid.c1"},
    {"role": "assistant", "content": "¡Hola! El Duo Zodiacal vale $58.000. ¿Te lo enviamos?", "timestamp": _iso(T0 + 9_000)},
    {"role": "user", "content": "Si", "timestamp": _iso(T0 + 60_000), "wamid": "wamid.c2"},
    {"role": "user", "content": "a Medellín", "timestamp": _iso(T0 + 62_000), "wamid": "wamid.c3"},
    {"role": "assistant", "kind": "ui_component", "component_kind": "order_confirmation",
     "content": "🧾 El bot envió el resumen del pedido con botones para confirmar", "timestamp": _iso(T0 + 70_000)},
    {"role": "user", "content": "[el cliente tocó el botón: Sí, lo quiero]", "timestamp": _iso(T0 + 90_000), "wamid": "wamid.c4"},
    {"role": "assistant", "content": "¡Listo! Tu pedido quedó confirmado.", "timestamp": _iso(T0 + 99_000)},
]
TRACES = [
    {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 3_000,
     "turn_key": "run:r1/t:1", "draft": {}, "state": {}},
    {"turn": 2, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 64_000,
     "turn_key": "run:r1/t:2", "draft": {"producto": "Duo Zodiacal"}, "state": {}},
    {"turn": 3, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 92_000,
     "turn_key": "run:r1/t:3", "draft": {"producto": "Duo Zodiacal", "ciudad": "Medellín"}, "state": {}},
    {"turn": 4, "episode_id": "ep_001", "trigger": "handoff", "turn_started_ms": T0 + 150_000,
     "turn_key": "run:r1/t:4", "draft": {}, "state": {}},
]
TURN_KEYS = ["run:r1/t:1", "run:r1/t:2", "run:r1/t:3"]  # el handoff no entra al banco


def _write_bench(root: Path) -> Path:
    bench = root / "bench"
    s = bench / "vault" / SID
    (s / "sessions").mkdir(parents=True)
    (s / "evals").mkdir()
    episodes = [{"episode_id": "ep_001", "started_at_ms": T0, "closed_at_ms": None}]
    (s / "metadata.json").write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    (s / "sessions" / f"{SID}.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in EVENTS), encoding="utf-8"
    )
    (s / "evals" / "turn_traces.jsonl").write_text("".join(json.dumps(t) + "\n" for t in TRACES), encoding="utf-8")
    llm = bench / "agent_state" / WS / "sessions"
    llm.mkdir(parents=True)
    (llm / f"{SID}.jsonl").write_text(json.dumps({"_type": "metadata", "key": SID}) + "\n", encoding="utf-8")
    (bench / "manifest.json").write_text(json.dumps({"bench_id": BENCH, "sessions": [SID], "since_ms": CUT}), encoding="utf-8")
    return bench


def _store(tmp_path: Path) -> FilesystemLabStore:
    """El banco ya exportado al S3 del laboratorio (un directorio en los tests)."""
    bench = _write_bench(tmp_path / "export")
    store = FilesystemLabStore(tmp_path / "bucket")
    for path in sorted(bench.rglob("*")):
        if path.is_file():
            store.put_file(f"bench/{BENCH}/{path.relative_to(bench).as_posix()}", path)
    return store


def _rows(raw: bytes | None) -> list[dict]:
    return [json.loads(line) for line in (raw or b"").decode("utf-8").splitlines() if line.strip()]


def _items_key(profile_id: str) -> str:
    return f"bench/{BENCH}/reference/{profile_id}/items.jsonl"


def _prepared(tmp_path: Path, *profile_ids: str) -> FilesystemLabStore:
    """El banco con los ítems de cada perfil ya subidos, como los deja `preparar`."""
    store = _store(tmp_path)
    for pid in profile_ids or ("jev-v2",):
        bench = _write_bench(tmp_path / "local" / pid)
        items = [
            build_item(c, EVENTS, profile_id=pid)
            for c in build_cases(bench, sales_workspace=WS).cases
            if c.trigger == "customer"
        ]
        store.put_bytes(_items_key(pid), "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items).encode())
    return store


def _answers(item: dict, **overrides) -> dict:
    """Respuestas válidas: no a los sí/no y la primera opción de las demás."""
    return {q["id"]: (False if q["kind"] == "noul" else q["options"][0]) for q in item["questions"]} | overrides


def test_prepare_uploads_the_items_of_the_profile_and_leaves_nothing_on_disk(tmp_path: Path, monkeypatch) -> None:
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    store = _store(tmp_path)

    summary = cli.run_prepare(store, BENCH, profile_id="jev-v2")

    items = _rows(store.get_bytes(_items_key("jev-v2")))
    assert sorted(i["turn_key"] for i in items) == TURN_KEYS
    assert {i["questionnaire"] for i in items} == {"rafaga-v2"}
    assert all(i["state"] and i["questions"] and i["conversation"] for i in items)
    assert (summary["items"], summary["cuestionario"]) == (3, "rafaga-v2")
    assert summary["por_categoria"] == {"respuesta_corta": 1, "rafaga_multiple": 1, "confirmacion": 1}
    assert list(scratch.iterdir()) == []  # el banco bajado se borró


def test_prepare_needs_a_complete_bench(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="manifiesto"):
        cli.run_prepare(FilesystemLabStore(tmp_path / "vacio"), BENCH, profile_id="jev-v2")


def test_the_page_brings_the_unlabeled_turns_whole_with_the_answer_format(tmp_path: Path) -> None:
    store = _prepared(tmp_path)
    items = _rows(store.get_bytes(_items_key("jev-v2")))

    page = cli.run_page(store, BENCH, profile_id="jev-v2", max_chars=100_000)

    for item in items:
        assert f"· {item['turn_key']} ·" in page
        assert item["state"] in page
        assert all(line in page for line in item["conversation"])
    templates = [json.loads(line) for line in page.splitlines() if line.startswith('{"turn_key"')]
    assert [t["turn_key"] for t in templates] == [i["turn_key"] for i in items]
    for template, item in zip(templates, items):
        assert set(template["answers"]) == {q["id"] for q in item["questions"]} and template["note"] == ""
    assert f"responder {BENCH} --perfil jev-v2" in page


def test_options_repeated_by_the_next_question_are_shown_once(tmp_path: Path) -> None:
    """Los 17 asuntos tienen las mismas opciones sí/no: se muestran una vez por
    turno (150 turnos que leer; cada carácter cuenta)."""
    store = _prepared(tmp_path)
    item = _rows(store.get_bytes(_items_key("jev-v2")))[0]

    block = cli.render_item(item, index=1, total=1)

    assert block.count("    true: sí, lo plantea en un mensaje de este turno") == 1
    assert block.count("    ninguno: ningún asunto de la lista") == 1


def test_the_page_stops_at_its_budget(tmp_path: Path) -> None:
    store = _prepared(tmp_path)
    items = _rows(store.get_bytes(_items_key("jev-v2")))
    first = cli.render_item(items[0], index=1, total=3)

    page = cli.run_page(store, BENCH, profile_id="jev-v2", max_chars=len(first) + 1_500)

    assert f"· {items[0]['turn_key']} ·" in page and f"· {items[1]['turn_key']} ·" not in page
    assert "quedan 2 turnos sin etiquetar" in page


def test_a_turn_that_does_not_fit_loses_the_start_of_its_conversation(tmp_path: Path) -> None:
    store = _prepared(tmp_path)
    item = max(_rows(store.get_bytes(_items_key("jev-v2"))), key=lambda i: len(i["conversation"]))
    full = cli.render_item(item, index=1, total=1)

    trimmed = cli.render_item(item, index=1, total=1, max_chars=len(full) - 20)

    assert len(trimmed) <= len(full) - 20
    assert "anteriores omitidos" in trimmed and item["state"] in trimmed and item["conversation"][-1] in trimmed


def test_my_labels_go_to_s3_per_questionnaire(tmp_path: Path) -> None:
    store = _prepared(tmp_path, "jev-v2", "jev-v1")
    item = _rows(store.get_bytes(_items_key("jev-v2")))[0]
    line = json.dumps({"turn_key": item["turn_key"], "answers": _answers(item), "note": "el Si confirma el envío"})

    saved, errors = cli.run_respond(store, BENCH, [line], profile_id="jev-v2")

    assert (saved, errors) == (1, [])
    [row] = _rows(store.get_bytes(f"bench/{BENCH}/reference/labels-rafaga-v2.jsonl"))
    assert (row["turn_key"], row["questionnaire"], row["answers"], row["note"]) == (
        item["turn_key"], "rafaga-v2", _answers(item), "el Si confirma el envío"
    )
    assert f"· {item['turn_key']} ·" not in cli.run_page(store, BENCH, profile_id="jev-v2")
    summary = cli.run_summary(store, BENCH, profile_id="jev-v2")
    assert (summary["etiquetados"], summary["pendientes"]) == (1, 2)
    assert cli.run_summary(store, BENCH, profile_id="jev-v1")["etiquetados"] == 0  # otro cuestionario, otras etiquetas


def test_a_wrong_answer_does_not_reach_s3(tmp_path: Path) -> None:
    store = _prepared(tmp_path)
    item = _rows(store.get_bytes(_items_key("jev-v2")))[0]
    incomplete = _answers(item)
    incomplete.pop("topic.precio")
    lines = [
        json.dumps({"turn_key": item["turn_key"], "answers": incomplete}),
        json.dumps({"turn_key": "run:otro/t:9", "answers": _answers(item)}),
        "esto no es json",
    ]

    saved, errors = cli.run_respond(store, BENCH, lines, profile_id="jev-v2")

    assert saved == 0 and len(errors) == 3
    assert "topic.precio: sin respuesta" in errors[0] and "turno desconocido" in errors[1] and "no es JSON" in errors[2]
    assert store.get_bytes(f"bench/{BENCH}/reference/labels-rafaga-v2.jsonl") is None


def test_jev_answers_are_stored_and_measured_against_my_labels(tmp_path: Path, monkeypatch) -> None:
    from src.sdk import connectorkit

    store = _prepared(tmp_path)
    items = _rows(store.get_bytes(_items_key("jev-v2")))
    lines = [
        json.dumps({"turn_key": i["turn_key"], "answers": _answers(i, **{"topic.precio": i["turn_key"] == "run:r1/t:1"})})
        for i in items
    ]
    assert cli.run_respond(store, BENCH, lines, profile_id="jev-v2") == (3, [])
    fake = FakePerceptionAdapter()
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)

    asked = cli.run_ask(store, BENCH, profile_id="jev-v2")

    rows = _rows(store.get_bytes(f"bench/{BENCH}/reference/jev-v2/jev.jsonl"))
    assert [(r["turn_key"], r["ok"]) for r in rows] == [(i["turn_key"], True) for i in items]
    assert (asked["respondidos"], asked["fallidos"]) == (3, 0)
    metrics = cli.run_metrics(store, BENCH, profile_id="jev-v2")
    assert metrics["turnos"] == {"items": 3, "etiquetados": 3, "con_jev": 3, "medidos": 3}
    precio = metrics["preguntas"]["topic.precio"]
    assert (precio["n"], precio["positives"]) == (3, 1)


def test_the_cli_asks_for_what_it_needs_before_touching_s3(monkeypatch, capsys) -> None:
    monkeypatch.delenv("LAB_BUCKET", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PERCEPTION_PROVIDER", raising=False)

    with pytest.raises(SystemExit):
        cli.main(["resumen", BENCH])
    assert "--bucket" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--bucket", "lab", "resumen", BENCH, "--perfil", "jev-v9"])
    assert "jev-v9" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--bucket", "lab", "preguntar", BENCH, "--perfil", "jev-v2"])
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
