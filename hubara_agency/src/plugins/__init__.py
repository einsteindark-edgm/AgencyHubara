"""Namespace package raíz para los plugins de AgencyHubara.

Cada plugin Python vive como subpaquete: `src.plugins.<id>`. El loader
en `src/main.py` (FastAPI) y `src/run_workers.py` (meta-launcher) los
descubre escaneando `frontend_dashboard/src/plugins/<id>/plugin.yaml` y
los importa con `importlib.import_module(api_cfg["python_module"])`.

Convenciones:
- `src.plugins.<id>.api` exporta `router` (FastAPI APIRouter).
- `src.plugins.<id>.agent` exporta `WORKFLOWS`, `ACTIVITIES`, `TOOL_FACTORIES`.
- `src.plugins.<id>.workers.<name>` exporta `main()` (asyncio entrypoint).

Ver `PLUGIN_REFACTOR_PLAN.md` §1 para detalles del layout.
"""
