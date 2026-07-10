"""El system map proyecta los workers schedule-driven (reloj en el canvas).

Un worker cuyo ciclo lo dispara un Temporal Schedule lo DECLARA en su
plugin.yaml — `schedule:` acepta UN objeto {id, cadence} o una LISTA (caso
sales_eval: eval online diaria + golden suite opt-in). El builder normaliza a
lista y lo proyecta al nodo worker (`data.schedules`) y al contenedor del
plugin (`data.has_schedule`) para que Acktos Studio badgee las cajitas sin
re-parsear manifests.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.plugins.system_map.domain.builder import build_system_graph


def _write_manifest(plugins_dir: Path, plugin_id: str, manifest: dict) -> None:
    p = plugins_dir / plugin_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "plugin.yaml").write_text(yaml.dump(manifest), encoding="utf-8")


def _manifest(worker_extra: dict) -> dict:
    return {
        "id": "reeng",
        "version": "0.1.0",
        "agent": {
            "workers": [
                {
                    "name": "cycle",
                    "module": "src.plugins.reeng.workers.cycle",
                    "task_queue": "queue-reeng-cycle",
                    **worker_extra,
                }
            ]
        },
    }


def test_worker_con_schedule_objeto_lo_proyecta_como_lista(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "reeng",
        _manifest({"schedule": {"id": "reeng-cycle-schedule", "cadence": "cada 45 min"}}),
    )

    g = build_system_graph(tmp_path)

    worker = next(n for n in g.nodes if n.kind == "worker")
    assert worker.data["schedules"] == [
        {"id": "reeng-cycle-schedule", "cadence": "cada 45 min"}
    ]
    plugin = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin.data["has_schedule"] is True


def test_worker_con_lista_de_schedules(tmp_path: Path) -> None:
    # caso sales_eval: la eval online diaria + la golden suite (opt-in) — un
    # worker puede crear MÁS de un Temporal Schedule al bootear.
    _write_manifest(
        tmp_path,
        "reeng",
        _manifest(
            {
                "schedule": [
                    {"id": "eval-schedule", "cadence": "diario 23:00"},
                    {"id": "golden-schedule", "cadence": "diario 06:00 (opt-in)"},
                ]
            }
        ),
    )

    g = build_system_graph(tmp_path)

    worker = next(n for n in g.nodes if n.kind == "worker")
    assert [s["id"] for s in worker.data["schedules"]] == [
        "eval-schedule",
        "golden-schedule",
    ]
    plugin = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin.data["has_schedule"] is True


def test_worker_sin_schedule_queda_lista_vacia_y_plugin_false(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "reeng", _manifest({}))

    g = build_system_graph(tmp_path)

    worker = next(n for n in g.nodes if n.kind == "worker")
    assert worker.data["schedules"] == []
    plugin = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin.data["has_schedule"] is False


def test_schedule_malformado_no_rompe_el_mapa(tmp_path: Path) -> None:
    # tolerancia del builder (el mapa nunca truena por un manifest a medio
    # hacer): schedule string / lista con basura → se ignora lo inválido.
    _write_manifest(tmp_path, "reeng", _manifest({"schedule": "cada 45 min"}))

    g = build_system_graph(tmp_path)

    worker = next(n for n in g.nodes if n.kind == "worker")
    assert worker.data["schedules"] == []
    plugin = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin.data["has_schedule"] is False
