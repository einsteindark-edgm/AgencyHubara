"""R-HEARTBEAT — Activity liveness.

Any activity whose worst-case execution time can exceed ~10s MUST be wrapped in
`@with_heartbeat(every=N)` from `src.platform.temporal.heartbeat` (or its
single-agent twin). Without heartbeats, Temporal's heartbeat_timeout (default
30s on `_TOOL_OPTIONS`) expires and the activity gets reschedule-cancelled.

This test uses a *symbol-based heuristic* over the activity body:
  - If the activity body references any of `_LONG_RUNNING_TRIGGERS`
    (httpx, requests, litellm, the WhatsApp client, the catalog/Medusa
    clients, `asyncio.sleep`), it's considered long-running and must wear
    `@with_heartbeat`.
  - Exemptions for false positives are listed in conftest.R_HEARTBEAT_EXEMPTIONS.

Test:
  #6 — `test_long_running_activities_have_heartbeat`
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import (
    AGENT_ACTIVITIES_GLOB,
    R_HEARTBEAT_EXEMPTIONS,
    iter_agent_files,
    parse_file,
    relative_to_hubara,
)

# Símbolos cuya sola aparición en el body de una activity la marca como long-running.
# Lista intencionalmente acotada — la heurística es de seguridad, no de cobertura.
_LONG_RUNNING_TRIGGERS: frozenset[str] = frozenset(
    {
        "httpx",
        "requests",
        "litellm",
        "whatsapp_client",
        "send_message",       # client de WhatsApp
        "send_whatsapp_message",
        "MedusaClient",
        "medusa",
        "CatalogClient",
        "pull_medusa_catalog",
    }
)


def _activity_modules() -> list[Path]:
    """Cada archivo bajo `activities/` que no sea `__init__.py`, más los
    cross-agent platform activities."""
    files = [p for p in iter_agent_files(AGENT_ACTIVITIES_GLOB) if p.name != "__init__.py"]
    src_root = Path(__file__).resolve().parents[2] / "src"
    for extra in (
        src_root / "platform" / "temporal" / "activities.py",
        src_root / "platform" / "whatsapp" / "activities.py",
    ):
        if extra.is_file():
            files.append(extra)
    return sorted(set(files))


def _has_activity_defn(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        # @activity.defn(name="...") or @activity.defn
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr == "defn":
                return True
        elif isinstance(dec, ast.Attribute) and dec.attr == "defn":
            return True
    return False


def _has_heartbeat_decorator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            fn = dec.func
        else:
            fn = dec
        name = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
        if name == "with_heartbeat":
            return True
    return False


def _body_references_long_running(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Devuelve el primer trigger encontrado, o None."""
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id in _LONG_RUNNING_TRIGGERS:
            return node.id
        if isinstance(node, ast.Attribute) and node.attr in _LONG_RUNNING_TRIGGERS:
            return node.attr
    return None


def test_long_running_activities_have_heartbeat() -> None:
    violations: list[str] = []

    for path in _activity_modules():
        rel = relative_to_hubara(path)
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_activity_defn(node):
                continue
            trigger = _body_references_long_running(node)
            if trigger is None:
                continue
            if _has_heartbeat_decorator(node):
                continue
            key = f"{rel}:{node.name}"
            if key in R_HEARTBEAT_EXEMPTIONS:
                continue
            violations.append(
                f"{key} touches `{trigger}` (long-running symbol) but lacks "
                f"`@with_heartbeat(...)`. Wrap it or add the path to "
                f"R_HEARTBEAT_EXEMPTIONS with a reason."
            )

    assert not violations, (
        "R-HEARTBEAT violations — long-running activities without @with_heartbeat:\n  "
        + "\n  ".join(violations)
    )
