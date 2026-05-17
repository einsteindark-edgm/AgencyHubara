"""Tests defensivos derivados del premortem de la auditoría (PR9, 2026-05-16).

Cada test cubre un escenario de fallo concreto que el premortem identificó:

- **F1**: cada worker declarado en manifests debe tener su correspondiente
  K8s deployment con las env vars que su código real necesita.

- **F5**: el regex de `id` vive en dos lugares (schema YAML + plugins-sync.ts).
  Si divergen, manifests válidos por uno son rechazados por el otro.

Estos tests son baratos y prevenen regresiones concretas; no aspiran a ser
exhaustivos, solo a tapar los huecos que ya nos lastimaron una vez.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
_HUBARA_ROOT = _REPO_ROOT / "hubara_agency"
_FRONTEND_ROOT = _REPO_ROOT / "frontend_dashboard"
_PLUGINS_DIR = _FRONTEND_ROOT / "src" / "plugins"
_K8S_DIR = _HUBARA_ROOT / "k8s" / "aws-produccion"


# ---------------------------------------------------------------------------
# F1 — paridad workers manifest ↔ K8s deployments
# ---------------------------------------------------------------------------
#
# PR11: pre-PR11 había un `_EXPECTED_K8S_DEPLOYMENTS` hardcoded; agregar un
# worker requería editar ese dict → conflict de merge cuando 2 Archon agents
# trabajaban en paralelo. Post-PR11, se DESCUBRE escaneando los K8s manifests
# del dir (cada uno declara su `command` que apunta al módulo del worker), se
# infiere `(plugin_id, worker_name)` del path del módulo, y se compara con los
# workers declarados en los `plugin.yaml`.
#
# Resultado: agregar un worker nuevo = (1) entry en `plugin.yaml`, (2) crear
# `k8s/aws-produccion/worker-<name>.yaml`. CERO archivos compartidos editar.


_WORKER_MODULE_RE = re.compile(
    r"(?:hubara_agency\.)?src\.plugins\.([a-z][a-z0-9_]*)\.workers\.([a-z][a-z0-9_]*)"
)


def _discover_k8s_worker_deployments() -> dict[tuple[str, str], Path]:
    """Escanea `k8s/aws-produccion/worker-*.yaml`, infiere (plugin, worker) del `command`.

    Devuelve {(plugin_id, worker_name): deployment_path}.

    Si dos deployments declaran el mismo (plugin, worker), el último gana —
    no es un caso esperado (un test detrás detectaría doble-deployment del
    mismo worker como bug operacional).
    """
    out: dict[tuple[str, str], Path] = {}
    if not _K8S_DIR.exists():
        return out
    for k8s_file in sorted(_K8S_DIR.glob("worker-*.yaml")):
        body = k8s_file.read_text(encoding="utf-8")
        match = _WORKER_MODULE_RE.search(body)
        if not match:
            continue
        plugin_id, worker_name = match.group(1), match.group(2)
        out[(plugin_id, worker_name)] = k8s_file
    return out


def _enumerate_manifest_workers() -> list[tuple[str, str]]:
    """[(plugin_id, worker_name)] descubiertos en los manifests."""
    out: list[tuple[str, str]] = []
    for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        agent_cfg = manifest.get("agent") or {}
        workers = agent_cfg.get("workers") or []
        for w in workers:
            out.append((plugin_dir.name, w["name"]))
    return out


def test_every_worker_in_manifest_has_k8s_deployment() -> None:
    """Para cada worker en `plugin.yaml`, existe un K8s manifest que lo corre.

    Premortem PR9 detectó que el manifest `chats` declaraba 2 workers (sales +
    remarketing) pero K8s solo tenía `worker-sales.yaml`. Deploy a producción
    estaba incompleto y nadie se daba cuenta hasta el primer mensaje de
    remarketing real.

    PR11: el matching es por discovery — escaneamos los deployments + infermos
    el (plugin, worker) del `command`. Ya no hay diccionario hardcoded a
    mantener.
    """
    declared = set(_enumerate_manifest_workers())
    discovered = _discover_k8s_worker_deployments()
    missing = sorted(declared - set(discovered.keys()))
    assert not missing, (
        "Workers declarados en manifests sin K8s deployment correspondiente:\n  - "
        + "\n  - ".join(f"{p}/{n}" for p, n in missing)
        + f"\n\nDeployments encontrados:\n  - "
        + "\n  - ".join(f"{p}/{n} → {path.name}" for (p, n), path in discovered.items())
    )


def test_every_k8s_worker_corresponds_to_a_manifest_worker() -> None:
    """Inverso de F1: ningún K8s deployment apunta a un worker que ya no existe.

    Premortem PR11: alguien renombra un worker en `plugin.yaml`
    (`sales` → `sales_v2`) pero olvida actualizar el K8s — el deployment queda
    intentando importar un módulo que ya no existe. Detectado al deploy.
    """
    declared = set(_enumerate_manifest_workers())
    discovered = _discover_k8s_worker_deployments()
    orphan = sorted(set(discovered.keys()) - declared)
    assert not orphan, (
        "K8s deployments apuntan a workers no declarados en ningún manifest:\n  - "
        + "\n  - ".join(
            f"{p}/{n} → {discovered[(p, n)].name}" for p, n in orphan
        )
    )


# ---------------------------------------------------------------------------
# F5 — single source of truth del regex de plugin id
# ---------------------------------------------------------------------------


def test_plugin_id_regex_matches_between_schema_and_sync() -> None:
    """El pattern de `id` en schema.yaml y en plugins-sync.ts deben coincidir.

    Premortem PR9 detectó que el pattern original (`^[a-z][a-z0-9-]*$`) prohibía
    underscore pero `agents_admin` lo usaba — funcionaba solo porque sync.ts no
    validaba contra el schema. Lo corregí a `^[a-z][a-z0-9_]*$` en ambos lados.
    Este test previene que diverjan otra vez.
    """
    schema_path = _PLUGINS_DIR / "_schema" / "plugin.schema.yaml"
    sync_path = _FRONTEND_ROOT / "scripts" / "plugins-sync.ts"

    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    schema_pattern = schema["properties"]["id"]["pattern"]

    sync_src = sync_path.read_text(encoding="utf-8")
    # Buscar `const PLUGIN_ID_PATTERN = /<regex>/;` en el TS.
    match = re.search(r"PLUGIN_ID_PATTERN\s*=\s*/(.+?)/", sync_src)
    assert match, (
        f"No encontré `PLUGIN_ID_PATTERN = /.../` en {sync_path}. "
        "Si lo renombraste, actualizá este test."
    )
    sync_pattern = match.group(1)

    assert schema_pattern == sync_pattern, (
        f"Schema regex ({schema_pattern!r}) y sync.ts regex ({sync_pattern!r}) "
        "divergen. Ambos deben matchear para que el contrato del manifest sea "
        "consistente entre validador Python y validador TS."
    )


# ---------------------------------------------------------------------------
# PR11 — manifest como SSoT: contratos del nuevo modelo
# ---------------------------------------------------------------------------


def test_every_manifest_worker_declares_task_queue() -> None:
    """PR11: ``agent.workers[].task_queue`` es required.

    Pre-PR11 las queues vivían en `src.platform.constants`. Post-PR11 cada
    worker declara su queue en el manifest. Este test bloquea la regresión.
    """
    from src.platform.plugin_manifest import (
        TaskQueueMissingError,
        enumerate_manifest_workers,
        get_task_queue,
    )

    missing: list[str] = []
    for plugin_id, worker_name, _module in enumerate_manifest_workers():
        try:
            queue = get_task_queue(plugin_id, worker_name)
        except TaskQueueMissingError as exc:
            missing.append(str(exc))
            continue
        if not queue:
            missing.append(f"{plugin_id}/{worker_name}: task_queue resolvió vacío")
    assert not missing, "Workers sin task_queue declarado:\n  - " + "\n  - ".join(missing)


def test_task_queues_are_unique_across_workers() -> None:
    """Dos workers no pueden compartir la misma task queue (aislamiento operacional).

    Premortem: si por error 2 workers usan la misma queue, ambos compiten por
    los mismos tasks y violan el modelo de un-worker-por-queue (rompe
    deploy/scale independiente, y peor: un worker puede recibir activities que
    no implementa → `ApplicationError`).
    """
    from src.platform.plugin_manifest import (
        enumerate_manifest_workers,
        get_task_queue,
    )

    seen: dict[str, tuple[str, str]] = {}
    collisions: list[str] = []
    for plugin_id, worker_name, _module in enumerate_manifest_workers():
        queue = get_task_queue(plugin_id, worker_name)
        if queue in seen:
            other = seen[queue]
            collisions.append(
                f"queue {queue!r} declared by both {other[0]}/{other[1]} "
                f"and {plugin_id}/{worker_name}"
            )
        else:
            seen[queue] = (plugin_id, worker_name)
    assert not collisions, "\n".join(collisions)


def test_docker_compose_local_is_up_to_date_with_manifests() -> None:
    """``docker-compose.local.yml`` debe coincidir con el output de ``render-compose.py``.

    PR11: el yml se autogenera desde manifests. Si alguien edita un worker en
    `plugin.yaml` pero olvida regenerar, este test detecta el drift y fuerza
    re-corrida del script + commit.

    Bypass intencional: ``RENDER_COMPOSE_SKIP=1`` env var skipea el test —
    útil si estás en medio de un refactor del script mismo.
    """
    import os

    if os.environ.get("RENDER_COMPOSE_SKIP"):
        return

    import importlib.util

    script_path = _HUBARA_ROOT / "scripts" / "render-compose.py"
    spec = importlib.util.spec_from_file_location("render_compose", script_path)
    assert spec and spec.loader, f"cannot load {script_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected = module.render()
    actual_path = _HUBARA_ROOT / "docker-compose.local.yml"
    actual = actual_path.read_text(encoding="utf-8")
    assert expected == actual, (
        "docker-compose.local.yml is out of sync with manifests + base.yml.\n"
        "Run: uv run python scripts/render-compose.py\n"
        "(set RENDER_COMPOSE_SKIP=1 to bypass during script refactors)"
    )


def test_existing_plugin_ids_match_the_pattern() -> None:
    """Sanity: todos los plugins actuales pasan el pattern.

    Si el pattern cambia y algún plugin existente queda fuera, sale aquí
    antes de romper el sync script.
    """
    schema_path = _PLUGINS_DIR / "_schema" / "plugin.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    pattern = re.compile(schema["properties"]["id"]["pattern"])

    invalid: list[str] = []
    for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        plugin_id = manifest.get("id")
        if not plugin_id or not pattern.match(plugin_id):
            invalid.append(f"{plugin_dir.name}: id={plugin_id!r} no matchea {pattern.pattern}")
    assert not invalid, "\n".join(invalid)
