"""Guards de runtime por plugin — P-21 (worker self-gate).

Defensa en profundidad del toggle (INV-2). La capa primaria es el render de
artefactos (``scripts/render-compose.py`` solo emite los workers de plugins
habilitados e inyecta ``ENABLED_PLUGINS`` en cada service). Este guard cubre
lo que esa capa no puede:

- **Containers huérfanos** (PM-1/AP-9): un ``docker compose up`` sin
  ``--remove-orphans`` tras renombrar/deshabilitar deja el container viejo
  corriendo código stale sobre la MISMA task queue. Con este guard, el
  container viejo muere al boot en cuanto su env diga que no corresponde.
- **k8s mal configurado**: un deployment aplicado con un ``ENABLED_PLUGINS``
  que no incluye su propio plugin = error de configuración → CrashLoop
  visible, no un worker fantasma sirviendo tráfico.

Cada worker lo llama PRIMERO en su ``main()``:

    from src.platform.plugin_runtime import ensure_plugin_enabled

    async def main() -> None:
        ensure_plugin_enabled("eta")
        ...

Gate de CI: ``tests/architecture/test_plugin_conformance.py::test_p21_*``
verifica por AST que todo worker se auto-gatea con SU plugin id.
"""
from __future__ import annotations

from src.platform.plugin_manifest import enabled_plugins


def ensure_plugin_enabled(plugin_id: str) -> None:
    """Aborta el proceso si ``plugin_id`` no está en ``ENABLED_PLUGINS``.

    Semántica idéntica al resto del sistema: env ausente/vacío = todos
    habilitados → no-op (dev-friendly). Con el env presente, un worker cuyo
    plugin no está habilitado NO debe pollear su queue — salir con error es
    el comportamiento correcto y visible (exit code 1 → restart loop que un
    operador ve, en vez de un worker zombie que un operador no ve).
    """
    enabled = enabled_plugins()
    if enabled is not None and plugin_id not in enabled:
        raise SystemExit(
            f"[plugin_runtime] plugin {plugin_id!r} NO está en ENABLED_PLUGINS="
            f"{sorted(enabled)} — este worker no debe correr en este deployment "
            f"(P-21, PLUGIN_CONTRACT.md INV-2). Si esto es un container huérfano, "
            f"levantá compose con --remove-orphans (PM-1)."
        )
