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

Shutdown: SIGINT/SIGTERM cancela todas las tasks. Cada worker tiene su propio
manejo de cancel (Temporal Worker hace flush graceful). Esperamos a que
todas terminen antes de salir — si alguna no responde, el proceso muere
después de 15s (timeout duro).
"""
from __future__ import annotations

import asyncio
import importlib
import os
import signal
from pathlib import Path

import yaml
from loguru import logger


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
    raw = os.environ.get("ENABLED_PLUGINS", "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


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


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM → setea `stop` para que el supervisor cancele las tasks."""
    def _request_stop(signame: str) -> None:
        if not stop.is_set():
            logger.info("[run_workers] received {} — requesting shutdown", signame)
            stop.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, signame), _request_stop, signame)
        except NotImplementedError:
            # Windows: add_signal_handler no implementado. SIGINT funciona
            # via KeyboardInterrupt en el `await stop.wait()` igual.
            pass


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

    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)

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

    # Diagnóstico — qué disparó el shutdown.
    for d in done:
        if d is waiter:
            logger.info("[run_workers] shutdown via signal — cancelling workers")
            continue
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

    # Cancelar todos los workers todavía vivos + el waiter si no disparó.
    for t in pending:
        t.cancel()

    # Esperar shutdown ordenado (timeout duro para no colgar el proceso).
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, *([waiter] if waiter in pending else []), return_exceptions=True),
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
