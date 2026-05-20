"""Manifest ↔ code consistency for the Level 3 declarative orchestration
(ADR-2026-05-20).

Each worker that participates in cross-worker dispatch declares 3 things in
``plugin.yaml``:

  - ``workflow_classes:`` — the canonical workflow class names this worker
    registers.
  - ``emits:`` — completion events the workflow can produce.
  - ``transitions:`` — what the dispatcher should do for each (event, when)
    pair.

These tests detect drift between manifest and code:

1. Every ``workflow_classes[i]`` matches a Python class with
   ``@workflow.defn(name=<i>)`` in the worker's module tree.
2. Every ``transitions[].on_event`` matches an entry in the same worker's
   ``emits[]``.
3. Every ``transitions[].action.target_workflow`` matches a workflow_class of
   the target worker.
4. Every event referenced is importable from a known location.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_DIR = _REPO_ROOT.parent / "frontend_dashboard" / "src" / "plugins"


def _iter_manifests() -> list[tuple[str, dict[str, Any]]]:
    """Yield ``(plugin_id, parsed_manifest)`` for every plugin."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not _MANIFEST_DIR.exists():
        return out
    for plugin_dir in sorted(_MANIFEST_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        manifest = plugin_dir / "plugin.yaml"
        if not manifest.exists():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            out.append((plugin_dir.name, data))
    return out


def _workers_for(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [w for w in (manifest.get("agent") or {}).get("workers", []) if isinstance(w, dict)]


def _find_temporal_workflow_name(module_path: str, target_name: str) -> bool:
    """AST-scan the worker's workflow package for ``@workflow.defn(name=<target_name>)``.

    ``workflow_classes:`` in the manifest holds the *Temporal name* (what the
    dispatcher passes to ``client.start_workflow(name, ...)``) — which can
    differ from the Python class name (Python class
    ``RemarketingSessionWorkflow`` is registered as ``"RemarketingWorkflow"``
    via ``@workflow.defn(name="RemarketingWorkflow")``).

    This function returns True iff any class in any submodule under
    ``src/plugins/<plugin>/agent/<worker>/workflows/`` has a decorator
    ``@workflow.defn`` with ``name=target_name``.

    Detection uses AST only — no module import, no Temporal worker boot.
    Importing the workflow module triggers Temporal client wiring that's
    too heavyweight for an architecture test.
    """
    m = re.match(
        r"^src\.plugins\.(?P<plugin>[a-z_]+)\.workers\.(?P<name>[a-z_]+)$",
        module_path,
    )
    if not m:
        return False
    plugin = m.group("plugin")
    name = m.group("name")
    workflows_dir = _REPO_ROOT / "src" / "plugins" / plugin / "agent" / name / "workflows"
    if not workflows_dir.exists():
        return False

    import ast as _ast

    for py in workflows_dir.glob("*.py"):
        if py.stem.startswith("__"):
            continue
        try:
            tree = _ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef):
                continue
            for dec in node.decorator_list:
                # Look for @workflow.defn(name="...") (a Call decorator).
                if not isinstance(dec, _ast.Call):
                    continue
                # Resolve the decorator path: must end in .defn
                fn = dec.func
                if isinstance(fn, _ast.Attribute) and fn.attr == "defn":
                    # Find a keyword name= ; if missing, default is the
                    # Python class name (Temporal SDK convention).
                    declared_name: str | None = None
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, _ast.Constant):
                            declared_name = kw.value.value
                            break
                    effective = declared_name or node.name
                    if effective == target_name:
                        return True
    return False


@pytest.mark.parametrize("plugin_id,manifest", _iter_manifests(), ids=lambda x: x if isinstance(x, str) else "")
def test_workflow_classes_exist_in_code(plugin_id: str, manifest: dict[str, Any]) -> None:
    """Every workflow_classes[i] must match a @workflow.defn(name=...) in the
    worker's workflows package.

    Validates the Temporal-side identity (what ``client.start_workflow(name,
    ...)`` uses) — NOT the Python class name. They can differ:
    ``RemarketingSessionWorkflow`` (class) → ``"RemarketingWorkflow"``
    (Temporal name) is a legitimate case.
    """
    for worker in _workers_for(manifest):
        worker_name = worker.get("name")
        module_path = worker.get("module")
        classes = worker.get("workflow_classes") or []
        if not classes or not module_path:
            continue

        for class_name in classes:
            assert _find_temporal_workflow_name(module_path, class_name), (
                f"{plugin_id}/{worker_name}: workflow_classes declares "
                f"{class_name!r} but no @workflow.defn(name={class_name!r}) "
                f"found under src.plugins.{plugin_id}.agent.{worker_name}.workflows.*. "
                f"Fix the manifest or add the decorator name."
            )


@pytest.mark.parametrize("plugin_id,manifest", _iter_manifests(), ids=lambda x: x if isinstance(x, str) else "")
def test_transitions_reference_emitted_events(
    plugin_id: str, manifest: dict[str, Any]
) -> None:
    """Each transition.on_event must appear in the SAME worker's emits[]."""
    for worker in _workers_for(manifest):
        worker_name = worker.get("name")
        emits = set(worker.get("emits") or [])
        for t in worker.get("transitions") or []:
            on_event = t.get("on_event")
            assert on_event in emits, (
                f"{plugin_id}/{worker_name}: transition {t.get('id')!r} "
                f"references on_event={on_event!r}, but it's not declared in "
                f"emits={sorted(emits)}. Add it to emits[] or fix the typo."
            )


@pytest.mark.parametrize("plugin_id,manifest", _iter_manifests(), ids=lambda x: x if isinstance(x, str) else "")
def test_transition_targets_exist(plugin_id: str, manifest: dict[str, Any]) -> None:
    """Each transition.action.target_workflow must be in the target worker's
    workflow_classes[]."""
    # Build (plugin, worker) → workflow_classes index across ALL manifests.
    index: dict[tuple[str, str], list[str]] = {}
    for pid, m in _iter_manifests():
        for w in _workers_for(m):
            if w.get("name"):
                index[(pid, w["name"])] = list(w.get("workflow_classes") or [])

    for worker in _workers_for(manifest):
        worker_name = worker.get("name")
        for t in worker.get("transitions") or []:
            action = t.get("action") or {}
            target_plugin = action.get("target_plugin") or plugin_id
            target_worker = action.get("target_worker")
            target_workflow = action.get("target_workflow")
            assert target_worker, (
                f"{plugin_id}/{worker_name}: transition {t.get('id')!r} has no "
                f"action.target_worker."
            )
            assert (target_plugin, target_worker) in index, (
                f"{plugin_id}/{worker_name}: transition {t.get('id')!r} targets "
                f"{target_plugin}/{target_worker}, which is not declared in any "
                f"manifest. Known: {sorted(index.keys())}"
            )
            assert target_workflow in index[(target_plugin, target_worker)], (
                f"{plugin_id}/{worker_name}: transition {t.get('id')!r} targets "
                f"{target_plugin}/{target_worker}.{target_workflow}, which is not "
                f"in workflow_classes={index[(target_plugin, target_worker)]}."
            )


@pytest.mark.parametrize("plugin_id,manifest", _iter_manifests(), ids=lambda x: x if isinstance(x, str) else "")
def test_emitted_events_are_importable(plugin_id: str, manifest: dict[str, Any]) -> None:
    """Every event in emits[] must be importable from either:
    - ``src.plugins.<plugin>.shared.contracts.events``
    - ``src.platform.contracts``
    """
    candidates = [
        f"src.plugins.{plugin_id}.shared.contracts.events",
        "src.platform.contracts",
    ]
    for worker in _workers_for(manifest):
        worker_name = worker.get("name")
        for event_name in worker.get("emits") or []:
            found = False
            for module_path in candidates:
                try:
                    mod = importlib.import_module(module_path)
                except ImportError:
                    continue
                if hasattr(mod, event_name):
                    found = True
                    break
            assert found, (
                f"{plugin_id}/{worker_name}: emits[] declares {event_name!r} but "
                f"it's not importable from any of {candidates}. Define it as a "
                f"@dataclass(frozen=True) in shared/contracts/events.py."
            )
