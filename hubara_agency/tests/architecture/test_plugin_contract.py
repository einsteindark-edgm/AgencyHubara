"""Tests del PLUGIN_CONTRACT.md — aislamiento (INV-1) + toggle simétrico (INV-2).

Cada test mapea a una regla P-# del contrato (ver PLUGIN_CONTRACT.md §4 y
PLUGIN_ARCHITECTURE_TESTS.md). Dos clases:

  * VERDES (candados anti-regresión): ya se cumplen; congelan lo bueno.
  * 🔴 xfail (definition-of-done del refactor): documentan violaciones VIVAS
    (split plugins eta/ads/evals, coupling orders->chats sin declarar). Cada PR
    del refactor vuelve verde el suyo y le quita el `@pytest.mark.xfail`.

Introspección pura (filesystem + AST) — no importa módulos de plugins.
"""
from __future__ import annotations

import re

import pytest

from tests.architecture._plugin_contract_helpers import (
    BE_PLUGINS,
    FE_PLUGINS,
    HUBARA_ROOT,
    PLATFORM,
    all_manifests,
    backend_has_code,
    declared_modules,
    iter_py,
    manifest_ids,
    real_imports,
)


# ----------------------------------------------------------------------------
# VERDES — candados anti-regresión (ya se cumplen)
# ----------------------------------------------------------------------------


def test_p1_manifest_modules_are_self_contained() -> None:
    """P-SELF: todo módulo declarado por el manifest de X empieza con src.plugins.<id>."""
    bad: list[str] = []
    for pid, manifest in all_manifests():
        for mod in declared_modules(manifest):
            if not mod.startswith(f"src.plugins.{pid}."):
                bad.append(f"{pid}: manifest declara `{mod}` fuera de src.plugins.{pid}.")
    assert not bad, "P-SELF (P-1) — módulo de manifest fuera del propio paquete:\n  " + "\n  ".join(bad)


def test_p3_no_cross_plugin_imports() -> None:
    """P-NOXIMPORT: ningún src/plugins/X/**.py importa src.plugins.Y (Y!=X).

    Generaliza el contrato `agents-independent` de .importlinter (que solo lista
    3 paquetes) a TODOS los plugins, presentes y futuros.
    """
    bad: list[str] = []
    for pdir in sorted(BE_PLUGINS.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith((".", "_")):
            continue
        for py in iter_py(pdir):
            for imp in real_imports(py):
                m = re.match(r"src\.plugins\.([a-z0-9_]+)", imp)
                if m and m.group(1) != pdir.name:
                    bad.append(f"{py.relative_to(HUBARA_ROOT).as_posix()} → {imp}")
    assert not bad, "P-NOXIMPORT (P-3) — import cross-plugin:\n  " + "\n  ".join(bad)


def test_p4_platform_never_imports_plugins() -> None:
    """P-PLATFORM: ningún src/platform/**.py importa src.plugins.* (AST, no docstrings)."""
    bad: list[str] = []
    for py in iter_py(PLATFORM):
        for imp in real_imports(py):
            if imp.startswith("src.plugins."):
                bad.append(f"{py.relative_to(HUBARA_ROOT).as_posix()} → {imp}")
    assert not bad, "P-PLATFORM (P-4) — platform importa plugins:\n  " + "\n  ".join(bad)


def test_p2_backend_surface_has_own_dir() -> None:
    """P-PARITY: todo manifest que declara api/agent tiene código backend propio.

    (Complemento de P-9: P-2 es vacuamente verde para los split plugins porque su
    manifest es 'frontend-only'; P-9 es el que caza el split por las llamadas API.)
    """
    bad: list[str] = []
    for pid, manifest in all_manifests():
        if (manifest.get("api") or manifest.get("agent")) and not backend_has_code(pid):
            bad.append(f"{pid}: declara backend en el manifest pero src/plugins/{pid}/ no tiene código")
    assert not bad, "P-PARITY (P-2):\n  " + "\n  ".join(bad)


def test_p14_consumes_blocks_are_well_formed() -> None:
    """P-CAST: todo `consumes:` declara provider(∈depends_on)+contract+into+cast.

    Hoy vacuamente verde (no hay bloques `consumes:` aún); enforça la forma cuando
    el casting (§5.3 del contrato) se implemente.
    """
    bad: list[str] = []
    for pid, manifest in all_manifests():
        deps = set(manifest.get("depends_on") or [])
        for c in manifest.get("consumes") or []:
            if not isinstance(c, dict):
                bad.append(f"{pid}: consumes[] entry no es dict")
                continue
            if c.get("provider") not in deps:
                bad.append(f"{pid}: consumes provider={c.get('provider')!r} no está en depends_on={sorted(deps)}")
            for req in ("provider", "contract", "into", "cast"):
                if not c.get(req):
                    bad.append(f"{pid}: consumes[] sin `{req}`")
    assert not bad, "P-CAST (P-14) — bloque consumes mal formado:\n  " + "\n  ".join(bad)


# ----------------------------------------------------------------------------
# 🔴 xfail — violaciones VIVAS (la definition-of-done del refactor)
# ----------------------------------------------------------------------------


# P-5 (transition target ∈ depends_on) ELIMINADO — refinamiento de diseño 2026-06-05:
# las transitions cross-plugin son SOFT, NO deps duras. La seguridad de toggle la da
# P-SKIP (el dispatcher skipea targets cuyo plugin no está habilitado), IMPLEMENTADO +
# testeado en tests/platform/orchestration/test_dispatcher.py::TestEnabledPluginsSkip.
# `depends_on` queda para deps DURAS (cast/consumes — ver P-14). Por eso
# `orders.depends_on: []` con transitions -> chats/eta es CORRECTO (no es violación).


@pytest.mark.xfail(
    # Reason redactado por INVARIANTE, no por lista de ofensores (lección
    # PM-12: las listas de ofensores se pudren; el invariante no).
    reason="P-OWN: el frontend de un plugin solo consume su propia API. "
    "Sale del xfail cuando todo consumo cross-plugin pase por el backend "
    "propio del consumidor (cast server-side, PLUGIN_CONTRACT.md §5.3) — "
    "plan F5 de PLUGIN_REFACTOR_PLAN_fable.md. OJO: este test grepea TEXTO "
    "(comentarios incluidos) bajo plugins/; la detección del canal real "
    "(lavado vía entities centrales) la dan P-22/P-23 en el frontend.",
    strict=False,
)
def test_p9_frontend_plugin_calls_only_own_api() -> None:
    """P-OWN: el frontend de X no llama a `/api/<otro-plugin>/*`."""
    ids = set(manifest_ids())
    bad: list[str] = []
    for pid in sorted(ids):
        fdir = FE_PLUGINS / pid / "frontend"
        if not fdir.exists():
            continue
        for f in [*fdir.rglob("*.ts"), *fdir.rglob("*.tsx")]:
            txt = f.read_text(encoding="utf-8")
            for other in ids - {pid}:
                if f"/api/{other}/" in txt:
                    bad.append(f"{pid} llama /api/{other}/ en {f.relative_to(FE_PLUGINS).as_posix()}")
    assert not bad, "P-OWN (P-9) — frontend llama API de otro plugin:\n  " + "\n  ".join(bad)
