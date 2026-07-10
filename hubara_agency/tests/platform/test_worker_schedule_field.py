"""Regla de oro del manifest: el campo `schedule:` de un worker está TIPADO y
chequeado contra la realidad.

1. `WorkerSpec.schedule` (sdk.manifest_model) tipa {id, cadence} — un objeto o
   una LISTA (caso sales_eval: eval online + golden suite). Un worker
   disparado por Temporal Schedule lo declara en su plugin.yaml; el system map
   lo proyecta al canvas (reloj en la cajita).
2. Drift guard bidireccional contra los DOS sitios que CREAN Schedules en
   Temporal: `scripts/create_*schedule*.py` (deploy-time) y las constantes
   `*SCHEDULE_ID = "..."` de `src/plugins/*/workers/*.py` (boot-time, caso
   reconcile/sales_eval). Todo `schedule.id` declarado existe en un sitio de
   creación, y todo sitio de creación está declarado en un manifest — ni
   schedules fantasma en el mapa, ni schedules reales invisibles.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.sdk.manifest_model import WorkerSchedule, WorkerSpec

HUBARA = Path(__file__).resolve().parents[2]
PLUGINS_DIR = HUBARA.parent / "frontend_dashboard" / "src" / "plugins"
SCRIPTS_DIR = HUBARA / "scripts"
WORKERS_GLOB = "plugins/*/workers/*.py"

_SCHEDULE_ID_RE = re.compile(r"^_?[A-Z_]*SCHEDULE_ID\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def test_worker_spec_tipa_el_schedule():
    spec = WorkerSpec(
        name="cycle",
        module="src.plugins.x.workers.cycle",
        task_queue="queue-x-cycle",
        schedule={"id": "x-cycle-schedule", "cadence": "cada 45 min"},
    )
    assert isinstance(spec.schedule, WorkerSchedule)
    assert spec.schedule.id == "x-cycle-schedule"
    assert spec.schedule.cadence == "cada 45 min"


def test_worker_spec_tipa_lista_de_schedules():
    spec = WorkerSpec(
        name="sales_eval",
        module="src.plugins.x.workers.sales_eval",
        task_queue="queue-x-eval",
        schedule=[
            {"id": "eval-schedule", "cadence": "diario 23:00"},
            {"id": "golden-schedule"},
        ],
    )
    assert isinstance(spec.schedule, list)
    assert [s.id for s in spec.schedule] == ["eval-schedule", "golden-schedule"]


def test_schedule_sin_id_no_valida():
    with pytest.raises(ValidationError):
        WorkerSpec(
            name="cycle",
            module="src.plugins.x.workers.cycle",
            task_queue="queue-x-cycle",
            schedule={"cadence": "cada 45 min"},
        )


def _declared_schedule_ids() -> set[str]:
    ids: set[str] = set()
    for manifest_path in sorted(PLUGINS_DIR.glob("*/plugin.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for worker in (manifest.get("agent") or {}).get("workers") or []:
            raw = worker.get("schedule") if isinstance(worker, dict) else None
            entries = raw if isinstance(raw, list) else [raw]
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id"):
                    ids.add(entry["id"])
    return ids


def _creation_site_schedule_ids() -> set[str]:
    ids: set[str] = set()
    sources = list(SCRIPTS_DIR.glob("create_*schedule*.py")) + list(
        (HUBARA / "src").glob(WORKERS_GLOB)
    )
    for source in sorted(sources):
        ids.update(_SCHEDULE_ID_RE.findall(source.read_text(encoding="utf-8")))
    return ids


def test_todo_schedule_declarado_tiene_su_sitio_de_creacion_y_viceversa():
    declared = _declared_schedule_ids()
    created = _creation_site_schedule_ids()
    assert declared == created, (
        f"drift manifest↔creación: declarados sin sitio de creación "
        f"{sorted(declared - created)}, creados sin declarar {sorted(created - declared)} "
        "— un worker con Temporal Schedule lo declara en su plugin.yaml "
        "(schedule: {id, cadence} u [lista]) y el sitio que lo crea "
        "(scripts/create_*_schedule.py o la constante *SCHEDULE_ID del worker) "
        "usa ese mismo id"
    )
