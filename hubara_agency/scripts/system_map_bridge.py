"""Puente stdio JSON-lines para la extensión de VS Code (Acktos Studio).

NO levanta FastAPI. Lee requests JSON (una línea = un request) por stdin y
responde por stdout (una línea = una respuesta), delegando en el MISMO
`certified_graph_payload()` (src/plugins/system_map/domain/serialize.py) que
sirve `GET /api/system-map/graph` — fuente única, guard anti-drift en
tests/plugins/system_map/test_serialize.py. Read-only: no toca manifests ni
spinal files.

Protocolo (una línea JSON por mensaje):
    → {"id": 1, "method": "GET", "path": "/api/graph", "params": {}, "body": null}
    ← {"id": 1, "status": 200, "payload": {"nodes": [...], "edges": [...], ...}}

Rutas (path space LÓGICO compartido con el puente de GraphAgents, para que la
extensión no cargue tablas de paths por provider; los paths largos del router
FastAPI se aceptan como alias):
    GET /api/graph   (alias /api/system-map/graph)  → SystemGraph + certs TCK
    GET /api/health  (alias /api/system-map/health) → mismo shape que routes.health

Contrato de stdout (gotcha del repo — stdout pollution rompe el parseo):
    stdout lleva SOLO respuestas JSON, una por línea. Se salva el fd real de
    stdout y se re-apunta el fd 1 a stderr (os.dup2) ANTES de los imports del
    backend — así también los SUBPROCESOS hijos heredan stderr y no pueden
    contaminar el protocolo.

Cache: el TCK cuesta ~450ms/request (medido, 7 plugins); como el puente es un
proceso largo, memoiza el payload con un fingerprint barato por mtime sobre
los manifests + el código de plugins (~ms). El endpoint FastAPI sigue sin
cache a propósito (otro proceso, otra vida útil).

Correr:  `cd hubara_agency && uv run python scripts/system_map_bridge.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Resolver `from src....` desde hubara_agency/ aunque se invoque por path.
_HUBARA_ROOT = Path(__file__).resolve().parent.parent
if str(_HUBARA_ROOT) not in sys.path:
    sys.path.insert(0, str(_HUBARA_ROOT))

# stdout real salvado y fd 1 re-apuntado a stderr ANTES de cualquier import
# del backend: los prints propios Y los de subprocesos hijos caen en stderr.
_real_stdout = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
sys.stdout = sys.stderr

_ID_SALVAGE_RE = re.compile(r'"id"\s*:\s*(\d+)')

# NOTA de paridad: el plumbing stdio (dup2 + _write + _ID_SALVAGE_RE + el
# read-loop con (Exception, SystemExit)) está DUPLICADO a propósito en
# GraphAgents/viewer/bridge.py — los dos bridges viven en roots de Python
# distintos (GraphAgents es standalone, sin acople al monorepo). Si arreglás
# un bug del protocolo acá, portalo allá también.

# Raíces del fingerprint del cache — TODO lo que el TCK realmente lee (el
# CheckContext): manifests frontend, código de plugins backend Y el TCK
# instanciado por plugin (tests/conformance, P-27).
_MANIFESTS_ROOT = _HUBARA_ROOT.parent / "frontend_dashboard" / "src" / "plugins"
_CODE_ROOT = _HUBARA_ROOT / "src" / "plugins"
_CONFORMANCE_ROOT = _HUBARA_ROOT / "tests" / "conformance"
# .md incluido: los workspaces de agentes (IDENTITY/SOUL/TOOLS, P-29) son .md.
_FP_SUFFIXES = (".py", ".yaml", ".yml", ".md")


def _write(msg: dict) -> None:
    _real_stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _real_stdout.flush()


def _fingerprint() -> tuple:
    """Invalidador barato del cache: (archivos, dirs, max-mtime) de las fuentes
    que alimentan el grafo/TCK. Un os.walk de metadata (~ms) vs ~450ms de TCK.
    Cuenta también DIRECTORIOS: los checks de arquetipo (P-29) validan la
    EXISTENCIA de dirs (p. ej. agent/*/workspace) — crear uno vacío debe
    invalidar el cache aunque no agregue archivos contados."""
    count, dirs, latest = 0, 0, 0.0
    for root in (_MANIFESTS_ROOT, _CODE_ROOT, _CONFORMANCE_ROOT):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            dirs += len(dirnames)
            for f in filenames:
                if f.endswith(_FP_SUFFIXES):
                    count += 1
                    try:
                        latest = max(latest, os.stat(os.path.join(dirpath, f)).st_mtime)
                    except OSError:
                        pass
    return (count, dirs, latest)


_cache: tuple[tuple, dict] | None = None


def _graph_payload() -> dict:
    global _cache
    fp = _fingerprint()
    if _cache is not None and _cache[0] == fp:
        return _cache[1]
    from src.plugins.system_map.domain.serialize import certified_graph_payload

    payload = certified_graph_payload()
    _cache = (fp, payload)
    return payload


def _route(method: str, path: str, params: dict, body: dict | None) -> tuple[int, dict]:
    # params/body forman parte del contrato del wire aunque hoy ninguna ruta
    # los consuma — llegan acá para que una futura ruta (scope, filtros) no
    # tenga que re-cablear el dispatcher.
    del params, body
    if method == "GET" and path in ("/api/health", "/api/system-map/health"):
        # La MISMA función que sirve routes.health (fuente única, guard
        # anti-drift en test_serialize.py) — el import lazy protege el boot.
        try:
            from src.plugins.system_map.domain.serialize import health_payload

            return 200, health_payload()
        except Exception as exc:  # noqa: BLE001 — liveness honesto, no crash
            return 200, {"status": "error", "detail": str(exc)}
    if method == "GET" and path in ("/api/graph", "/api/system-map/graph"):
        return 200, _graph_payload()
    return 404, {"error": f"no existe la ruta {method} {path}"}


def main() -> None:
    _write({"ready": True, "root": str(_HUBARA_ROOT)})  # handshake

    # Warm-up: los imports pesados del backend (~1.1s bajo uv) se pagan acá,
    # entre el spawn y el primer click — no dentro del primer request.
    try:
        from src.plugins.system_map.domain import serialize  # noqa: F401
    except Exception:  # noqa: BLE001 — se reporta por-request, no en el boot
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            # Rescatar el id si es legible — un 400 sin id deja colgada la
            # promise del caller hasta su timeout.
            m = _ID_SALVAGE_RE.search(line)
            salvaged = int(m.group(1)) if m else None
            _write({"id": salvaged, "status": 400, "payload": {"error": f"request no es JSON: {e}"}})
            continue
        req_id = req.get("id")
        try:
            status, payload = _route(
                req.get("method", "GET"), req.get("path", ""), req.get("params") or {}, req.get("body")
            )
            _write({"id": req_id, "status": status, "payload": payload})
        except (Exception, SystemExit) as e:  # noqa: BLE001 — un request roto no mata el bridge
            _write({"id": req_id, "status": 500, "payload": {"error": f"{type(e).__name__}: {e}"}})


if __name__ == "__main__":
    main()
