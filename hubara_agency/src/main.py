"""Entry point HTTP — auto-discovery de routers FastAPI desde manifests.

PR3 reemplaza los imports estáticos por un loader que lee
``frontend_dashboard/src/plugins/<id>/plugin.yaml`` y registra los routers
declarados en ``api.python_module`` y ``api.legacy_routers``.

Filtrado por env var ``ENABLED_PLUGINS`` (csv, vacío = todos los descubiertos).

Convenciones del manifest (ver ``frontend_dashboard/src/plugins/_schema/plugin.schema.yaml``):

  api:
    python_module: src.plugins.<id>.api      # opcional — módulo con `router`
    prefix: /api/<id>
    tags: [<Tag>]
    legacy_routers:                           # opcional — múltiples routers
      - { module: src.plugins.<id>.api.<sub>, prefix: /api, tags: [...] }

Si ambos están presentes, ambos se registran. Si ninguno, el plugin no
contribuye routers.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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


# ---------------------------------------------------------------------------
# Plugin discovery — escanea frontend_dashboard/src/plugins/<id>/plugin.yaml.
# ---------------------------------------------------------------------------

# El manifest es la única fuente de verdad y vive del lado frontend (donde el
# operador agrega plugins más seguido). El loader Python lee el mismo archivo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


def _enabled_plugins() -> set[str] | None:
    """Devuelve el set de plugin ids habilitados, o None si todos."""
    raw = os.environ.get("ENABLED_PLUGINS", "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def _discover_plugin_manifests() -> list[tuple[str, dict]]:
    """[(plugin_id, manifest_dict)] sorted by id, filtrado por ENABLED_PLUGINS."""
    enabled = _enabled_plugins()
    out: list[tuple[str, dict]] = []
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
        if manifest.get("id") != plugin_dir.name:
            # El sync script ya valida esto; defensa en profundidad.
            continue
        out.append((plugin_dir.name, manifest))
    return out


def _register_router(plugin_id: str, module_path: str, prefix: str, tags: list[str]) -> None:
    """Importa el módulo y registra su `router` (APIRouter) en `app`."""
    mod = importlib.import_module(module_path)
    router = getattr(mod, "router", None)
    if router is None:
        raise RuntimeError(
            f"plugin {plugin_id!r}: module {module_path!r} no exporta `router`"
        )
    app.include_router(router, prefix=prefix, tags=tags)


def _bootstrap_routers() -> list[str]:
    """Registra routers de todos los plugins habilitados. Devuelve los ids cargados."""
    loaded: list[str] = []
    for plugin_id, manifest in _discover_plugin_manifests():
        api_cfg = manifest.get("api") or {}
        registered_any = False

        # Modo "single router": el módulo apunta a `<pkg>` con un `router` en su __init__.
        if "python_module" in api_cfg and api_cfg.get("python_module"):
            # Si el módulo no expone `router` (e.g. `chats.api` que solo agrupa
            # sub-routers), saltamos sin error — `legacy_routers` lo cubre.
            try:
                mod = importlib.import_module(api_cfg["python_module"])
            except ImportError as exc:
                raise RuntimeError(
                    f"plugin {plugin_id!r}: cannot import api.python_module "
                    f"{api_cfg['python_module']!r}: {exc}"
                ) from exc
            if getattr(mod, "router", None) is not None:
                _register_router(
                    plugin_id,
                    api_cfg["python_module"],
                    api_cfg.get("prefix", f"/api/{plugin_id}"),
                    api_cfg.get("tags", [plugin_id]),
                )
                registered_any = True

        # Modo "legacy routers": lista de módulos con prefix/tags propios.
        for legacy in api_cfg.get("legacy_routers", []) or []:
            _register_router(
                plugin_id,
                legacy["module"],
                legacy.get("prefix", "/api"),
                legacy.get("tags", [plugin_id]),
            )
            registered_any = True

        if registered_any:
            loaded.append(plugin_id)
    return loaded


# El bootstrap se ejecuta en module-load time para que `app` quede listo cuando
# uvicorn lo importe. Las excepciones suben — un plugin roto rompe el boot
# en lugar de quedar parcialmente cargado (fail-fast).
_LOADED_PLUGINS = _bootstrap_routers()


@app.get("/")
def health_check() -> dict:
    """Liveness probe + diagnostic — qué plugins quedaron cargados."""
    return {
        "status": "ok",
        "agency_agentic": "active",
        "temporal_connection": "delegated_to_routes",
        "plugins_loaded": _LOADED_PLUGINS,
    }
