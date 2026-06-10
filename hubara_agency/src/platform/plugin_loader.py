"""Validación de arranque del set de plugins habilitados (P-6 / P-ENABLED).

INV-2 (PLUGIN_CONTRACT.md §1): la presencia de un plugin la gobierna SOLO
``ENABLED_PLUGINS``. Este módulo agrega la mitad que faltaba del contrato: si
habilitás un plugin cuyas dependencias DURAS (``depends_on``) no están
habilitadas, el proceso falla AL BOOT con un mensaje accionable — en vez de
fallar en runtime con un 404 silencioso o una conversación muda.

``depends_on`` = SOLO deps duras (el plugin no funciona sin ellas; ej. ``eta``
necesita el ingest de WhatsApp que hoy vive en ``chats``). Las transitions
cross-plugin son SOFT y las cubre el dispatcher-skip (P-7) — NO van acá
(P-DEPS, PLUGIN_CONTRACT.md §4).

Lo llaman los tres loaders al boot:

- ``src.main`` (FastAPI) — antes de registrar routers.
- ``src.run_workers`` (meta-launcher dev) — antes de levantar workers.
- ``frontend_dashboard/scripts/plugins-sync.ts`` — port TS equivalente.

Gate de CI: ``tests/architecture/test_plugin_conformance.py::test_p6_*``.
"""
from __future__ import annotations

from typing import Any, Mapping

from src.platform.plugin_manifest import all_manifests, enabled_plugins


class PluginDependencyError(RuntimeError):
    """``ENABLED_PLUGINS`` viola el ``depends_on`` de un plugin habilitado."""


def validate_enabled(
    enabled: set[str] | None = None,
    manifests: Mapping[str, dict[str, Any]] | None = None,
) -> None:
    """Falla rápido si el set habilitado es incoherente (P-ENABLED).

    Args:
        enabled: set de plugin ids habilitados. ``None`` (default) lo resuelve
            de ``ENABLED_PLUGINS``; si el env tampoco está, significa "todos
            los descubiertos" y no hay nada que validar.
        manifests: mapping ``{plugin_id: manifest_dict}``. ``None`` (default)
            escanea los manifests reales del repo. Inyectable para tests.

    Reglas:
        1. Todo id habilitado tiene manifest — un typo en ``ENABLED_PLUGINS``
           es un error de boot, no un plugin silenciosamente ausente.
        2. Para cada plugin habilitado, ``depends_on ⊆ enabled``.

    Raises:
        PluginDependencyError: con TODOS los problemas encontrados (no solo
            el primero), para que un set mal armado se arregle en una pasada.
    """
    if enabled is None:
        enabled = enabled_plugins()
    if enabled is None:
        return
    if manifests is None:
        manifests = dict(all_manifests())

    problems: list[str] = []
    for pid in sorted(enabled - manifests.keys()):
        problems.append(
            f"ENABLED_PLUGINS incluye {pid!r} pero no existe ningún manifest con ese id"
        )
    for pid in sorted(enabled & manifests.keys()):
        deps = manifests[pid].get("depends_on") or []
        missing = sorted(str(d) for d in deps if d not in enabled)
        if missing:
            problems.append(
                f"plugin {pid!r} declara depends_on={missing} pero no están en "
                f"ENABLED_PLUGINS — habilitá las deps o deshabilitá {pid!r}"
            )
    if problems:
        raise PluginDependencyError(
            "ENABLED_PLUGINS inválido (P-ENABLED, PLUGIN_CONTRACT.md §5.4):\n  - "
            + "\n  - ".join(problems)
        )
