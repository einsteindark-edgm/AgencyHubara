"""Regla de oro del manifest: el campo `schedule:` de un worker está TIPADO y
chequeado contra la realidad.

1. `WorkerSpec.schedule` (sdk.manifest_model) tipa {id, cadence} — un worker
   disparado por un Temporal Schedule lo declara en su plugin.yaml; el system
   map lo proyecta al canvas (reloj en la cajita).
2. Drift guard bidireccional contra `scripts/create_*schedule*.py` (los que
   CREAN los Schedules en Temporal): todo `schedule.id` declarado existe como
   SCHEDULE_ID de un script, y todo script tiene su declaración en un manifest
   — ni schedules fantasma en el mapa, ni schedules reales invisibles.
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
            if isinstance(raw, dict) and raw.get("id"):
                ids.add(raw["id"])
    return ids


def _script_schedule_ids() -> set[str]:
    ids: set[str] = set()
    for script in sorted(SCRIPTS_DIR.glob("create_*schedule*.py")):
        m = re.search(r"SCHEDULE_ID\s*=\s*[\"']([^\"']+)[\"']", script.read_text(encoding="utf-8"))
        if m:
            ids.add(m.group(1))
    return ids


def test_todo_schedule_declarado_tiene_su_script_y_viceversa():
    declared = _declared_schedule_ids()
    scripted = _script_schedule_ids()
    assert declared == scripted, (
        f"drift manifest↔scripts: declarados sin script {sorted(declared - scripted)}, "
        f"scripts sin declarar {sorted(scripted - declared)} — un worker con Temporal "
        "Schedule lo declara en su plugin.yaml (schedule: {id, cadence}) y el script "
        "create_*_schedule.py usa ese mismo SCHEDULE_ID"
    )
