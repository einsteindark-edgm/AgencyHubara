"""Puente stdio JSON-lines para la extensión de VS Code (Hubara Studio).

NO levanta un servidor HTTP. Lee requests JSON (una línea = un request) por
stdin y responde por stdout (una línea = una respuesta), delegando en el MISMO
`api_route()` que usa el explorer web (`viewer/server.py`). Reusa TODOS los
endpoints (graph, checks, inspect, cases, replay, run-durable, trace,
flow-trace, validate-connection, connect, disconnect, save, publish, ...).

Protocolo (una línea JSON por mensaje):
    → {"id": 1, "method": "GET", "path": "/api/graph", "params": {}, "body": null}
    ← {"id": 1, "status": 200, "payload": {"nodes": [...], "edges": [...]}}

Contrato de stdout (gotcha del repo — stdout pollution rompe el parseo):
    stdout lleva SOLO respuestas JSON, una por línea. Se salva el fd real de
    stdout y se re-apunta el fd 1 a stderr (os.dup2) ANTES de los imports —
    así los prints propios Y los de SUBPROCESOS hijos (p. ej. workers que
    AgentSpanRuntime spawnea en /api/run-durable) caen en stderr y no pueden
    contaminar el protocolo.

Correr:  `python3 -m viewer.bridge`  (desde GraphAgents/).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

GA_ROOT = Path(__file__).resolve().parent.parent
if str(GA_ROOT) not in sys.path:
    sys.path.insert(0, str(GA_ROOT))

# NOTA de paridad: el plumbing stdio (dup2 + _write + _ID_SALVAGE_RE + el
# read-loop con (Exception, SystemExit)) está DUPLICADO a propósito en
# hubara_agency/scripts/system_map_bridge.py — los dos bridges viven en roots
# de Python distintos (GraphAgents es standalone, sin acople al monorepo). Si
# arreglás un bug del protocolo acá, portalo allá también.

# stdout real salvado y fd 1 re-apuntado a stderr antes de que nadie (import,
# capability, subproceso hijo) pueda escribirle.
_real_stdout = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
sys.stdout = sys.stderr

_ID_SALVAGE_RE = re.compile(r'"id"\s*:\s*(\d+)')

# Un request se atiende por thread (el server HTTP original era
# ThreadingHTTPServer): sin esto, un POST lento (/api/publish, /api/save,
# /api/replay) bloqueaba los polls de trace hasta su timeout. El protocolo ya
# soporta respuestas out-of-order (matching por id); solo la ESCRITURA de la
# línea de respuesta necesita ser atómica.
_write_lock = threading.Lock()


def _write(msg: dict) -> None:
    with _write_lock:
        _real_stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        _real_stdout.flush()


# --- cache de /api/graph -----------------------------------------------------
# build_graph re-lee y re-parsea TODO el catálogo por llamada, y la extensión
# pide el grafo en cada bootstrap/refresh/save de manifest. Mismo patrón de
# memo por fingerprint mtime que system_map_bridge.py. Las raíces cubren TODO
# lo que level_of/run_checks leen: manifests, tools, capabilities (graphs/) y
# el propio sdk (los checks importan módulos de ambos).
_GRAPH_ROOTS = ("manifests", "tools", "graphs", "sdk")
_GRAPH_SUFFIXES = (".py", ".yaml", ".yml", ".md")


def _graph_fingerprint() -> tuple:
    count, dirs, latest = 0, 0, 0.0
    for name in _GRAPH_ROOTS:
        for dirpath, dirnames, filenames in os.walk(GA_ROOT / name):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            dirs += len(dirnames)
            for f in filenames:
                if f.endswith(_GRAPH_SUFFIXES):
                    count += 1
                    try:
                        latest = max(latest, os.stat(os.path.join(dirpath, f)).st_mtime)
                    except OSError:
                        pass
    return (count, dirs, latest)


_graph_cache: tuple[tuple, tuple[int, dict]] | None = None
_graph_cache_lock = threading.Lock()


def _normalize_params(params: dict | None) -> dict:
    """`api_route` espera params estilo parse_qs: {clave: [strings]}. La
    extensión manda JSON (escalares, bools, listas); normalizamos a listas de
    STRINGS con semántica querystring — `True` debe llegar como "true" (el
    server compara contra ("1", "true")), y los None se descartan."""

    def _s(x) -> str:
        if isinstance(x, bool):
            return "true" if x else "false"
        return str(x)

    out: dict[str, list[str]] = {}
    for k, v in (params or {}).items():
        vals = v if isinstance(v, list) else [v]
        out[k] = [_s(x) for x in vals if x is not None]
    return out


def _dispatch(api_route, req: dict) -> None:
    req_id = req.get("id")
    method = req.get("method", "GET")
    path = req.get("path", "")
    try:
        if method == "GET" and path == "/api/graph":
            global _graph_cache
            with _graph_cache_lock:
                fp = _graph_fingerprint()
                if _graph_cache is not None and _graph_cache[0] == fp:
                    status, payload = _graph_cache[1]
                else:
                    status, payload = api_route(method, path, {}, None, GA_ROOT)
                    if status == 200:
                        _graph_cache = (fp, (status, payload))
        else:
            status, payload = api_route(
                method,
                path,
                _normalize_params(req.get("params")),
                req.get("body"),
                GA_ROOT,
            )
        _write({"id": req_id, "status": status, "payload": payload})
    except (Exception, SystemExit) as e:  # noqa: BLE001 — las tools pueden
        # levantar SystemExit (patrón AgentRuntime, ver server.py:232); en el
        # server multi-thread eso mataba UN request — acá mataría el bridge.
        _write({"id": req_id, "status": 500, "payload": {"error": f"{type(e).__name__}: {e}"}})


def main() -> None:
    # Import diferido: si algo imprime al importar sdk, cae en stderr (ya
    # re-apuntado arriba), nunca en el stdout de respuestas.
    from viewer.server import api_route

    _write({"ready": True, "root": str(GA_ROOT)})  # handshake para la extensión

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
        threading.Thread(target=_dispatch, args=(api_route, req), daemon=True).start()


if __name__ == "__main__":
    main()
