"""Meta-launcher — arranca los workers de los plugins habilitados en paralelo.

PR3 introduce este script para conveniencia de dev local:

    ENABLED_PLUGINS=chats uv run python -m src.run_workers

…levanta TODOS los workers declarados en ``manifest.agent.workers`` de los
plugins habilitados, en un mismo proceso, via ``asyncio.gather``. Cada worker
mantiene su propia task queue exclusiva.

En producción (K8s/docker-compose) cada worker sigue siendo un container
independiente — el aislamiento operacional + escalado por dominio son
ortogonales a este launcher. Este script existe para que un dev pueda
arrancar la stack sin levantar `docker compose up`.

Convenciones del manifest:

  agent:
    workers:
      - { name: sales,       module: src.plugins.chats.workers.sales }
      - { name: remarketing, module: src.plugins.chats.workers.remarketing }

Cada `module` debe exponer un `async def main() -> None` que conecta a
Temporal y entra al loop del Worker.

Si el plugin sólo tiene un worker, también acepta el atajo:

  agent:
    worker_module: src.plugins.<id>.workers.default
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

import yaml
from loguru import logger


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


def _enabled_plugins() -> set[str] | None:
    raw = os.environ.get("ENABLED_PLUGINS", "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def _discover_workers() -> list[tuple[str, str, str]]:
    """[(plugin_id, worker_name, worker_module)] de los plugins habilitados."""
    enabled = _enabled_plugins()
    out: list[tuple[str, str, str]] = []
    if not _PLUGINS_MANIFEST_DIR.exists():
        return out
    for plugin_dir in sorted(_PLUGINS_MANIFEST_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name.startswith("_") or plugin_dir.name.startswith("."):
            continue
        if enabled is not None and plugin_dir.name not in enabled:
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        agent_cfg = manifest.get("agent") or {}
        workers = agent_cfg.get("workers")
        if workers:
            for w in workers:
                out.append((plugin_dir.name, w["name"], w["module"]))
        elif agent_cfg.get("worker_module"):
            out.append((plugin_dir.name, "default", agent_cfg["worker_module"]))
    return out


async def _run_worker(plugin_id: str, worker_name: str, module_path: str) -> None:
    """Importa el módulo del worker y entra a su `main()`."""
    label = f"{plugin_id}/{worker_name}"
    logger.info("[{}] importing {}", label, module_path)
    mod = importlib.import_module(module_path)
    worker_main = getattr(mod, "main", None)
    if worker_main is None or not asyncio.iscoroutinefunction(worker_main):
        raise RuntimeError(
            f"worker {label}: module {module_path!r} debe exponer `async def main() -> None`"
        )
    logger.info("[{}] starting", label)
    try:
        await worker_main()
    except Exception:
        logger.exception("[{}] worker raised — re-raising to bubble up", label)
        raise


async def main() -> None:
    workers = _discover_workers()
    if not workers:
        logger.warning(
            "[run_workers] no workers discovered. ENABLED_PLUGINS={!r}",
            os.environ.get("ENABLED_PLUGINS", ""),
        )
        return
    logger.info(
        "[run_workers] launching {} workers: {}",
        len(workers),
        [f"{p}/{n}" for p, n, _ in workers],
    )
    tasks = [
        asyncio.create_task(_run_worker(p, n, m), name=f"{p}/{n}")
        for p, n, m in workers
    ]
    # Cualquier worker que muera tira el grupo entero — fail-fast en dev local.
    # En producción cada worker es un container separado (no se usa este script).
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
