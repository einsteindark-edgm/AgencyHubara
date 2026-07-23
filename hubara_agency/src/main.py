"""Entry point HTTP — auto-discovery de routers FastAPI desde manifests.

PR3 reemplazó los imports estáticos por un loader que lee
``frontend_dashboard/src/plugins/<id>/plugin.yaml`` y registra los routers
declarados en ``api.python_module`` y ``api.legacy_routers``.

Filtrado por env var ``ENABLED_PLUGINS`` (csv, vacío = todos los descubiertos).

Convenciones del manifest (ver ``frontend_dashboard/src/plugins/_schema/plugin.schema.yaml``):

  api:
    python_module: src.plugins.<id>.api      # opcional — módulo con `router`
    prefix: /api/<id>
    tags: [<Tag>]
    legacy_routers:                          # opcional — múltiples routers
      - { module: src.plugins.<id>.api.<sub>, prefix: /api, tags: [...] }

Si ``legacy_routers`` está presente y no vacío, **gana** y ``python_module`` se
ignora — el caso del plugin ``chats`` que tiene 3 sub-routers con prefijos
heterogéneos y ``api/__init__.py`` no expone un router agregado. Si solo está
``python_module``, se registra ése. Si ninguno, el plugin no contribuye HTTP.

Fail-fast: si un plugin habilitado declara un módulo que no se puede importar
o no expone ``router``, el boot del proceso falla. Mejor caer rápido en el
arranque que servir un endpoint silenciosamente ausente.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import yaml
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.platform.auth import require_auth
from src.platform.observability.otel import init_otel, instrument_fastapi_app
from src.platform.plugin_loader import validate_enabled
from src.platform.plugin_manifest import enabled_plugins

# OTel ANTES de `_bootstrap_routers()` (que importa los plugins → litellm/exoclaw):
# hace del proceso HTTP el servicio "api-gateway" en SigNoz, raíz del trace que se
# propaga a Temporal (webhook → workflow → activity → LLM → tool). Idempotente;
# no-op con OTEL_SDK_DISABLED=true.
init_otel("api-gateway")


app = FastAPI(
    title="Agency API",
    description="Entrada centralizada — auto-discovery de plugins.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cada request HTTP (webhook WhatsApp, endpoints del dashboard) genera un span
# SERVER raíz. La propagación del webhook a Temporal la hace add_traced_background_task
# (en el router de chats); httpx ya lo instrumenta OpenLIT (no se duplica acá).
instrument_fastapi_app(app)


# ---------------------------------------------------------------------------
# Plugin discovery — escanea frontend_dashboard/src/plugins/<id>/plugin.yaml.
# ---------------------------------------------------------------------------

# El manifest es la única fuente de verdad y vive del lado frontend (donde el
# operador agrega plugins más seguido). El loader Python lee el mismo archivo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


def _enabled_plugins() -> set[str] | None:
    """Devuelve el set de plugin ids habilitados, o None si todos.

    Delegado a la implementación canónica de ``plugin_manifest`` — una sola
    semántica de ``ENABLED_PLUGINS`` para loaders + dispatcher (N-7).
    """
    return enabled_plugins()


def _discover_plugin_manifests() -> list[tuple[str, dict]]:
    """[(plugin_id, manifest_dict)] sorted by id, filtrado por ENABLED_PLUGINS."""
    enabled = _enabled_plugins()
    out: list[tuple[str, dict]] = []
    if not _PLUGINS_MANIFEST_DIR.exists():
        logger.warning(
            "[loader] plugins manifest dir not found: {}", _PLUGINS_MANIFEST_DIR
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
                "[loader] skip {!r}: invalid YAML — {}", plugin_dir.name, exc
            )
            continue
        if manifest.get("id") != plugin_dir.name:
            # Fail-fast (N-9): un typo de `id` apagaba el plugin EN SILENCIO
            # (warning + skip). Mismo trato que un import roto: el boot muere
            # con mensaje accionable en vez de servir un sistema parcial.
            raise RuntimeError(
                f"plugin manifest inválido: {plugin_dir.name}/plugin.yaml declara "
                f"id={manifest.get('id')!r} pero el directorio se llama "
                f"{plugin_dir.name!r} — deben coincidir (P-PARITY)."
            )
        out.append((plugin_dir.name, manifest))
    return out


def _register_router_from_module(
    target_app: FastAPI,
    plugin_id: str,
    mod: ModuleType,
    module_path: str,
    prefix: str,
    tags: list[str],
) -> None:
    """Registra el ``router`` (APIRouter) de un módulo ya importado en ``target_app``."""
    router = getattr(mod, "router", None)
    if router is None:
        raise RuntimeError(
            f"plugin {plugin_id!r}: module {module_path!r} no expone `router`"
        )
    if not isinstance(router, APIRouter):
        raise RuntimeError(
            f"plugin {plugin_id!r}: module {module_path!r}.router no es APIRouter "
            f"(got {type(router).__name__})"
        )
    # Auth (workstream JWT/Cognito): por DEFAULT toda ruta de plugin exige un
    # JWT de Cognito (fail-closed — una ruta nueva nace protegida). Un router se
    # declara PÚBLICO marcando ``PUBLIC_ROUTER = True`` a nivel módulo — sólo el
    # webhook de Meta, que trae su propia auth (hub.verify_token + HMAC). La
    # dependency ``require_auth`` es NO-OP si Cognito no está configurado en
    # dev/tests; en producción (``HUBARA_ENV=production``) es FAIL-CLOSED: falta
    # de config de auth → 503, nunca acceso abierto (SEC-01).
    public = bool(getattr(mod, "PUBLIC_ROUTER", False))
    target_app.include_router(
        router,
        prefix=prefix,
        tags=tags,
        dependencies=[] if public else [Depends(require_auth)],
    )
    logger.info(
        "[loader] registered {} → prefix={!r} tags={!r} auth={}",
        module_path,
        prefix,
        tags,
        "PUBLIC" if public else "required",
    )


def _bootstrap_routers(target_app: FastAPI | None = None) -> list[str]:
    """Registra routers de todos los plugins habilitados en ``target_app``.

    Si ``target_app`` es ``None``, usa el ``app`` global de este módulo —
    comportamiento por defecto al boot. Tests pueden pasar una ``FastAPI``
    fresca para evitar mutar el singleton compartido.

    Política: ``legacy_routers`` gana sobre ``python_module``. Si un plugin
    declara ambos, ``python_module`` se ignora — útil para chats donde
    ``api/__init__.py`` no expone un router agregado pero el manifest declara
    el módulo como ancla simbólica.
    """
    if target_app is None:
        target_app = app
    # P-ENABLED (PLUGIN_CONTRACT.md §5.4): el set habilitado debe satisfacer
    # los depends_on de cada plugin habilitado — fail-fast al boot.
    validate_enabled(_enabled_plugins())
    loaded: list[str] = []
    manifests = _discover_plugin_manifests()
    logger.info(
        "[loader] discovered {} plugin(s): {}",
        len(manifests),
        [pid for pid, _ in manifests],
    )
    for plugin_id, manifest in manifests:
        api_cfg = manifest.get("api") or {}
        registered_any = False
        legacy = api_cfg.get("legacy_routers") or []

        # Modo "legacy routers" — preferido cuando hay múltiples sub-routers
        # heterogéneos. Importa y registra cada uno explícito.
        for entry in legacy:
            mod_path = entry["module"]
            try:
                mod = importlib.import_module(mod_path)
            except ImportError as exc:
                raise RuntimeError(
                    f"plugin {plugin_id!r}: cannot import legacy_routers entry "
                    f"{mod_path!r}: {exc}"
                ) from exc
            _register_router_from_module(
                target_app,
                plugin_id,
                mod,
                mod_path,
                entry.get("prefix", "/api"),
                entry.get("tags", [plugin_id]),
            )
            registered_any = True

        # Modo "single router" — fallback cuando NO hay legacy_routers. Si el
        # módulo apunta a algo sin `router` (e.g. `chats.api` que solo agrupa
        # docstrings), saltamos sin error.
        if not legacy and api_cfg.get("python_module"):
            mod_path = api_cfg["python_module"]
            try:
                mod = importlib.import_module(mod_path)
            except ImportError as exc:
                raise RuntimeError(
                    f"plugin {plugin_id!r}: cannot import api.python_module "
                    f"{mod_path!r}: {exc}"
                ) from exc
            if getattr(mod, "router", None) is not None:
                _register_router_from_module(
                    target_app,
                    plugin_id,
                    mod,
                    mod_path,
                    api_cfg.get("prefix", f"/api/{plugin_id}"),
                    api_cfg.get("tags", [plugin_id]),
                )
                registered_any = True
            else:
                logger.debug(
                    "[loader] plugin {!r}: api.python_module {!r} has no `router` attr; skipping",
                    plugin_id,
                    mod_path,
                )

        if registered_any:
            loaded.append(plugin_id)
        elif api_cfg:
            logger.warning(
                "[loader] plugin {!r}: api section present but no routers registered",
                plugin_id,
            )
    return loaded


# El bootstrap se ejecuta en module-load time para que `app` quede listo cuando
# uvicorn lo importe. Las excepciones suben — un plugin roto rompe el boot
# en lugar de quedar parcialmente cargado (fail-fast).
_LOADED_PLUGINS = _bootstrap_routers()
logger.info(
    "[loader] bootstrap complete — {} plugin(s) contributed routers: {}",
    len(_LOADED_PLUGINS),
    _LOADED_PLUGINS,
)


@app.get("/")
def health_check() -> dict:
    """Liveness probe + diagnostic — qué plugins quedaron cargados."""
    return {
        "status": "ok",
        "agency_agentic": "active",
        "temporal_connection": "delegated_to_routes",
        "plugins_loaded": _LOADED_PLUGINS,
    }
