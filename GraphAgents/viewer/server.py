"""Backend VIVO del explorer — **stdlib `http.server`, cero deps nuevas**.

Sirve el visor estático (`index.html`) y una API JSON mínima que lee el registry
en vivo (cada request re-proyecta el sistema; editás un manifest y refrescás):

    GET  /api/graph          -> el grafo del sistema {nodes, edges} (sdk.graph)
    GET  /api/health         -> {ok: true}
    POST /api/run            -> corre un agente tool-only por el LocalRuntime
                               body: {"agent": "greeter", "input": {"name": "..."}}

Por qué stdlib y no FastAPI: el explorer es read-mostly + un endpoint de run; con
la stdlib corre en la imagen slim sin sincronizar deps pesadas, igual que el
`LocalRuntime`. Si crece (auth, websockets) se hace el swap — el core ruteable
(`api_route`) ya está aislado del transporte.

Correr:  `python -m viewer.server`  (desde GraphAgents/)  ·  PORT=8900 por defecto.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GA_ROOT = Path(__file__).resolve().parent.parent
VIEWER = Path(__file__).resolve().parent

# que `import sdk` resuelva tanto con `-m viewer.server` como con `python viewer/server.py`
if str(GA_ROOT) not in sys.path:
    sys.path.insert(0, str(GA_ROOT))


def run_agent(ga_root: Path, agent_id: str, input_dict: dict) -> dict:
    """Corre un agente tool-only por el LocalRuntime (mismo criterio que `cli run`).
    Los agentes que `consumes:` un port se rechazan: necesitan un vendor inyectado
    (corren por `tests/integration` con un Fixture, no desde la UI)."""
    from sdk.loader import build_runnable
    from sdk.manifest_model import iter_nodes, load_manifest
    from sdk.runtime import LocalRuntime

    manifests = ga_root / "manifests"
    cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
        manifests.glob(f"{agent_id}.taskgraph.yaml")
    )
    if not cand:
        return {"status": "failed", "error": f"no existe el agente '{agent_id}'"}
    m = load_manifest(cand[0])
    consumed = sorted({p for n in iter_nodes(m) for p in n.consumes})
    if consumed:
        return {
            "status": "failed",
            "error": (
                f"'{agent_id}' consume ports {consumed}: necesita vendors inyectados "
                "(corré por tests/integration con un Fixture, no desde la UI)."
            ),
        }
    try:
        ex = LocalRuntime().run(build_runnable(m, ga_root), input_dict or {})
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e)}
    return {"id": ex.id, "status": ex.status, "output": ex.output, "error": ex.error}


def api_route(method: str, path: str, params: dict, body: dict | None, ga_root: Path = GA_ROOT):
    """Core ruteable y testeable (sin socket). Devuelve `(status:int, payload:dict)`."""
    from sdk.graph import build_graph

    if method == "GET" and path == "/api/health":
        return 200, {"ok": True}
    if method == "GET" and path == "/api/graph":
        return 200, build_graph(ga_root)
    if method == "POST" and path == "/api/run":
        body = body or {}
        agent_id = body.get("agent")
        if not agent_id:
            return 400, {"error": "falta 'agent' en el body"}
        res = run_agent(ga_root, agent_id, body.get("input") or {})
        return (200 if res.get("status") == "completed" else 422), res
    return 404, {"error": f"no existe la ruta {method} {path}"}


_STATIC = {"/": "index.html", "/index.html": "index.html"}


class Handler(BaseHTTPRequestHandler):
    server_version = "GraphAgentsExplorer/0.1"

    def _send(self, status: int, payload, ctype: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            status, payload = api_route("GET", u.path, parse_qs(u.query), None)
            return self._send(status, payload)
        fname = _STATIC.get(u.path)
        if fname:
            f = VIEWER / fname
            if f.exists():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        self._send(404, {"error": f"no existe {u.path}"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._send(400, {"error": "body no es JSON válido"})
        status, payload = api_route("POST", u.path, parse_qs(u.query), body)
        self._send(status, payload)

    def log_message(self, *_args) -> None:  # silencio (no ensuciar stdout)
        pass


def serve(host: str = "0.0.0.0", port: int = 8900) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"GraphAgents explorer → http://localhost:{port}  (Ctrl-C para parar)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    import os

    serve(port=int(os.environ.get("PORT", "8900")))
