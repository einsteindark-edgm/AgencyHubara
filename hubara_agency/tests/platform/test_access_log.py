"""Una línea por request de la API, sin datos personales y con la memoria del
proceso (memoria de la caja de app, 2026-11-05).

La API tuvo dos picos que casi tumban la caja (2,3 GB el 26-oct, 2,6 GB el
3-nov) y no se pudo saber qué pantalla los disparó: los logs de docker se
borran al recrear el contenedor, y la línea de uvicorn trae los tickets del
SSE y teléfonos en la ruta, así que no se puede mandar tal cual a CloudWatch.
Ahora cada request deja una línea JSON en un archivo (CloudWatch la guarda 14
días): la PLANTILLA de la ruta (`/sessions/{session_id}`, nunca la ruta real),
los nombres del query con solo los valores cortos de números o fechas, el
estado, cuánto tardó, la memoria del proceso al terminar y el máximo que llegó
a tener. El primer request tras el cual el máximo salta es el culpable.

Números de teléfono y tickets sintéticos.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.platform import config
from src.platform.access_log import install_access_log
from src.platform.rate_limit import SlidingWindowRateLimiter, install_rate_limit_middleware

PHONE = "573001234567"
TICKET = "tk-abcdefsecret"


def _app(lines: list[dict]) -> FastAPI:
    app = FastAPI()
    router = APIRouter(prefix="/api/dashboard")

    @router.get("/sessions/{session_id}")
    def session(session_id: str) -> dict:
        return {"ok": True}

    @router.get("/events")
    def events() -> StreamingResponse:
        return StreamingResponse(iter([b"data: 1\n\n", b"data: 2\n\n"]), media_type="text/event-stream")

    @router.get("/boom")
    def boom() -> dict:
        raise RuntimeError("se cayó")

    app.include_router(router)
    # Como en `src.main`: el rate limit es un middleware HTTP de Starlette.
    install_rate_limit_middleware(app, SlidingWindowRateLimiter(0, 60))
    install_access_log(app, lines.append)
    return app


def test_a_request_logs_its_route_template_not_its_path(tmp_path: Path) -> None:
    lines: list[dict] = []
    client = TestClient(_app(lines))

    client.get(f"/api/dashboard/sessions/wa_{PHONE}?days=56&ticket={TICKET}&start=2026-11-01")

    (line,) = lines
    assert line["method"] == "GET"
    assert line["route"] == "/api/dashboard/sessions/{session_id}"
    assert line["status"] == 200
    assert line["query"] == "days=56&ticket=…&start=2026-11-01"
    assert isinstance(line["ms"], int) and line["ms"] >= 0
    assert line["peak_mb"] > 0
    assert line["rss_mb"] is None or line["rss_mb"] > 0
    assert PHONE not in json.dumps(line) and TICKET not in json.dumps(line)


def test_a_path_that_matches_no_route_is_not_written(tmp_path: Path) -> None:
    lines: list[dict] = []
    client = TestClient(_app(lines))

    client.get(f"/wp-admin/{PHONE}/login.php")

    (line,) = lines
    assert line["route"] is None and line["status"] == 404
    assert PHONE not in json.dumps(line)


def test_a_stream_is_logged_once_when_it_ends() -> None:
    lines: list[dict] = []
    client = TestClient(_app(lines))

    body = client.get(f"/api/dashboard/events?ticket={TICKET}").text

    assert body.count("data:") == 2
    assert [(line["route"], line["status"]) for line in lines] == [("/api/dashboard/events", 200)]


def test_a_request_that_crashes_is_logged_as_500() -> None:
    lines: list[dict] = []
    client = TestClient(_app(lines), raise_server_exceptions=False)

    assert client.get("/api/dashboard/boom").status_code == 500

    assert [(line["route"], line["status"]) for line in lines] == [("/api/dashboard/boom", 500)]


def test_a_sink_that_fails_never_breaks_the_request() -> None:
    def broken(_line: dict) -> None:
        raise OSError("disco lleno")

    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    install_access_log(app, broken)

    assert TestClient(app).get("/ping").json() == {"ok": True}


def test_the_api_writes_the_line_to_the_configured_file(tmp_path: Path, monkeypatch) -> None:
    from src.main import app

    log = tmp_path / "access" / "api.log"
    monkeypatch.setattr(config, "ACCESS_LOG_FILE", str(log))

    TestClient(app).get("/")

    lines = [json.loads(raw) for raw in log.read_text(encoding="utf-8").splitlines()]
    assert [(line["method"], line["route"], line["status"]) for line in lines] == [("GET", "/", 200)]


def test_without_a_configured_file_the_api_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    from src.main import app

    monkeypatch.setattr(config, "ACCESS_LOG_FILE", "")
    monkeypatch.chdir(tmp_path)

    assert TestClient(app).get("/").status_code == 200
    assert list(tmp_path.rglob("*.log*")) == []
