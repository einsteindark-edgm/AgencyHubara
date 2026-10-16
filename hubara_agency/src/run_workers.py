"""Meta-launcher — arranca los workers de los plugins habilitados en paralelo.

PR3 introdujo este script para conveniencia de dev local:

    ENABLED_PLUGINS=chats uv run python -m src.run_workers

…levanta TODOS los workers declarados en ``manifest.agent.workers`` de los
plugins habilitados, en un mismo proceso, via ``asyncio.gather``. Cada worker
mantiene su propia task queue exclusiva.

En producción (K8s/docker-compose) cada worker sigue siendo un container
independiente — el aislamiento operacional + escalado por dominio son
ortogonales a este launcher. Este script existe para que un dev pueda
arrancar la stack sin levantar ``docker compose up``.

Convenciones del manifest:

  agent:
    workers:
      - { name: sales,       module: src.plugins.chats.workers.sales }
      - { name: remarketing, module: src.plugins.chats.workers.remarketing }

Cada ``module`` debe exponer un ``async def main() -> None`` que conecta a
Temporal y entra al loop del Worker.

Si el plugin sólo tiene un worker, también acepta el atajo:

  agent:
    worker_module: src.plugins.<id>.workers.default

Shutdown: cada worker corre con ``run_worker`` y SIGINT/SIGTERM lo apaga en
orden (deja de pedir tareas y espera a sus activities en curso, auditoría de
Temporal 2026-10-15). El launcher comparte esa señal (``stop_signal``): espera
a que todos terminen solos y solo cancela a los que pasen el timeout duro
(15 s por defecto). Si un worker se cae, cancela a los demás.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

import yaml
from loguru import logger

from src.platform.plugin_loader import validate_enabled
from src.platform.plugin_manifest import enabled_plugins
from src.platform.temporal.worker_lifecycle import stop_signal


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


def _resolve_shutdown_timeout() -> float:
    """Tiempo máximo que esperamos por workers después de pedir cancel.

    Default 15s — razonable para dev local. En producción con miles de
    workflows in-flight Temporal puede tardar más en flushar (premortem PR9).
    Configurable via ``RUN_WORKERS_SHUTDOWN_TIMEOUT_S``. Si el valor no
    parsea, fallback al default + warning visible.
    """
    raw = os.environ.get("RUN_WORKERS_SHUTDOWN_TIMEOUT_S", "").strip()
    if not raw:
        return 15.0
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "[run_workers] RUN_WORKERS_SHUTDOWN_TIMEOUT_S={!r} no parsea como float; "
            "usando default 15.0s",
            raw,
        )
        return 15.0


_SHUTDOWN_TIMEOUT_S = _resolve_shutdown_timeout()


def _enabled_plugins() -> set[str] | None:
    """Delegado a la implementación canónica (una sola semántica — N-7)."""
    return enabled_plugins()


def _validate_worker_entry(plugin_id: str, idx: int, entry: object) -> tuple[str, str]:
    """Valida una entrada de ``agent.workers`` y devuelve (name, module)."""
    if not isinstance(entry, dict):
        raise RuntimeError(
            f"plugin {plugin_id!r}: agent.workers[{idx}] no es dict (got "
            f"{type(entry).__name__})"
        )
    name = entry.get("name")
    module = entry.get("module")
    if not isinstance(name, str) or not name:
        raise RuntimeError(
            f"plugin {plugin_id!r}: agent.workers[{idx}].name missing or not str"
        )
    if not isinstance(module, str) or not module:
        raise RuntimeError(
            f"plugin {plugin_id!r}: agent.workers[{idx}].module missing or not str"
        )
    return name, module


def _discover_workers() -> list[tuple[str, str, str]]:
    """[(plugin_id, worker_name, worker_module)] de los plugins habilitados."""
    enabled = _enabled_plugins()
    # P-ENABLED: fail-fast si el set viola algún depends_on (PLUGIN_CONTRACT §5.4).
    validate_enabled(enabled)
    out: list[tuple[str, str, str]] = []
    if not _PLUGINS_MANIFEST_DIR.exists():
        logger.warning(
            "[run_workers] plugins manifest dir not found: {}", _PLUGINS_MANIFEST_DIR
        )
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
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error(
                "[run_workers] skip {!r}: invalid YAML — {}", plugin_dir.name, exc
            )
            continue
        if manifest.get("id") != plugin_dir.name:
            # Fail-fast (N-9) — mismo criterio que src.main: un typo de id no
            # puede apagar un plugin en silencio.
            raise RuntimeError(
                f"plugin manifest inválido: {plugin_dir.name}/plugin.yaml declara "
                f"id={manifest.get('id')!r} pero el directorio se llama "
                f"{plugin_dir.name!r} — deben coincidir (P-PARITY)."
            )
        agent_cfg = manifest.get("agent") or {}
        workers = agent_cfg.get("workers")
        if workers:
            if not isinstance(workers, list):
                raise RuntimeError(
                    f"plugin {plugin_dir.name!r}: agent.workers must be a list, "
                    f"got {type(workers).__name__}"
                )
            for idx, w in enumerate(workers):
                name, module = _validate_worker_entry(plugin_dir.name, idx, w)
                out.append((plugin_dir.name, name, module))
        elif agent_cfg.get("worker_module"):
            mod = agent_cfg["worker_module"]
            if not isinstance(mod, str) or not mod:
                raise RuntimeError(
                    f"plugin {plugin_dir.name!r}: agent.worker_module must be a "
                    f"non-empty str"
                )
            out.append((plugin_dir.name, "default", mod))
    return out


async def _run_worker(plugin_id: str, worker_name: str, module_path: str) -> None:
    """Importa el módulo del worker y entra a su ``main()``."""
    label = f"{plugin_id}/{worker_name}"
    logger.info("[{}] importing {}", label, module_path)
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(
            f"worker {label}: cannot import {module_path!r}: {exc}"
        ) from exc
    worker_main = getattr(mod, "main", None)
    if worker_main is None or not asyncio.iscoroutinefunction(worker_main):
        raise RuntimeError(
            f"worker {label}: module {module_path!r} debe exponer "
            f"`async def main() -> None`"
        )
    logger.info("[{}] starting", label)
    try:
        await worker_main()
    except asyncio.CancelledError:
        logger.info("[{}] received cancel — flushing", label)
        raise
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
        "[run_workers] launching {} worker(s): {}",
        len(workers),
        [f"{p}/{n}" for p, n, _ in workers],
    )

    async with stop_signal() as stop:
        await _supervise(workers, stop)


async def _supervise(workers: list[tuple[str, str, str]], stop: asyncio.Event) -> None:
    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(_run_worker(p, n, m), name=f"{p}/{n}")
        for p, n, m in workers
    ]

    # Esperamos a: (a) la primera task que termine/falle, o (b) un signal.
    waiter = asyncio.create_task(stop.wait(), name="run_workers/stop_waiter")
    done, pending = await asyncio.wait(
        [*tasks, waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if waiter in done:
        # SIGTERM/SIGINT: cada worker se está apagando solo (`run_worker`); se
        # espera a que drenen y solo se cancela a los que pasen el timeout.
        logger.info("[run_workers] shutdown via signal — waiting for workers to drain")
        _, stragglers = await asyncio.wait(tasks, timeout=_SHUTDOWN_TIMEOUT_S)
        for t in stragglers:
            logger.error("[run_workers] worker {!r} did not drain in {}s — cancelling", t.get_name(), _SHUTDOWN_TIMEOUT_S)
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return

    # Diagnóstico — un worker terminó o se cayó: se baja a los demás.
    for d in done:
        try:
            d.result()
            logger.warning(
                "[run_workers] worker {!r} terminated cleanly (unexpected)", d.get_name()
            )
        except Exception:
            logger.exception(
                "[run_workers] worker {!r} crashed — tearing down peers",
                d.get_name(),
            )

    # Cancelar todos los workers todavía vivos + el waiter.
    for t in pending:
        t.cancel()

    # Esperar shutdown ordenado (timeout duro para no colgar el proceso).
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, waiter, return_exceptions=True),
            timeout=_SHUTDOWN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            "[run_workers] shutdown timeout ({}s) exceeded — exiting hard",
            _SHUTDOWN_TIMEOUT_S,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # En el path donde add_signal_handler no funciona (Windows),
        # KeyboardInterrupt cae hasta aquí.
        logger.info("[run_workers] KeyboardInterrupt — bye")
