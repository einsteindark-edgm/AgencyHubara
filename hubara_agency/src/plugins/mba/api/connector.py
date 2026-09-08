"""Connector tools de Meta Business Agent (``PUBLIC_ROUTER``: las llama Meta, no el shell).

Una ruta por tool declarada en ``agents/*/agent.yaml``, bajo ``/api/mba/tools/<name>``,
protegida con la API key del connector (header ``X-API-Key`` = ``HUBARA_MBA_API_KEY``).
Fail-closed: sin la variable configurada responde 503.

Orden del pipeline por request (cada paso corta con su código):
bloqueo por keys inválidas (IP) → 429 · API key → 503/401 · rate limit por
(IP, tool) → 429 · tool/método → 404/405 ·
tope de body → 413 · JSON → 400 · validación contra el ``request_definition``
autorado → 422 · lógica → 200 · tool sin lógica todavía → 501.

IP del cliente: en prod la API solo es alcanzable vía Caddy (``expose``, sin
puerto público), así que el peer es siempre el proxy y la clave del limiter es
el primer hop de ``X-Forwarded-For`` (``client_ip`` del SDK). Dos buckets: el
chico por IP para el tráfico SIN key válida (frena el brute force y corre
antes de comparar la key) y el general por (IP, tool) para el tráfico
autenticado, así un tercero sin la key no toca el bucket que usa Meta.

Frontera de confianza: el teléfono que inyecta Meta (``customer_phone``) es la
única identidad del cliente: se convierte en ``session_key = wa_<dígitos>`` y
toda lectura y escritura queda acotada a esa sesión. Quien tenga la API key
puede operar sobre cualquier teléfono; la key es el secreto que guarda Meta y
es rotable.

Delegación: las tools de lectura corren por canal 1 (ports del SDK); las de
escritura por cast (canal 3) al contrato ``session-actions@v1`` de chats
(``api/chats_cast.py``, service token). Un fallo del cast vuelve como error
explícito en un 200 (el agente sabe manejarlo), nunca como 5xx hacia Meta.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.plugins.mba.domain.guard import MAX_BODY_BYTES, RateLimiter
from src.plugins.mba.domain.tool_calls import ToolCallError, ToolContract, contracts_from_config, parse_tool_call
from src.plugins.mba.service import list_agents, load_agent
from src.plugins.mba.tools import ToolDeps, default_deps, run_tool
from src.sdk.runtime import client_ip

PUBLIC_ROUTER = True
API_KEY_ENV = "HUBARA_MBA_API_KEY"

router = APIRouter()
_rate_limiter = RateLimiter()
#: keys inválidas por IP: 10 de ráfaga y una cada 10 s. Protege la comparación
#: de la key y el bucket general (que es el que usa Meta).
_bad_key_limiter = RateLimiter(capacity=10, refill_per_s=0.1)


@lru_cache(maxsize=1)
def declared_tools() -> dict[str, ToolContract]:
    """``{tool_name: contrato}`` de todos los agentes MBA autorados.

    Cacheado: los archivos del agente son estáticos por deploy y este endpoint
    es público (no re-parsear 10 archivos por request).
    """
    out: dict[str, ToolContract] = {}
    for agent in list_agents():
        cfg = load_agent(agent.id)
        if cfg is None or cfg.connector is None:
            continue
        for name, contract in contracts_from_config(cfg).items():
            out.setdefault(name, contract)
    return out


def get_tool_deps() -> ToolDeps:
    return default_deps()


def _check_api_key(presented: str | None, *, client: str, tool: str) -> None:
    expected = os.environ.get(API_KEY_ENV, "")
    if not expected:
        # el nombre de la variable va al log (operador), no al cliente anónimo
        logger.warning("[mba] connector sin {} configurada; 503 a {} para {}", API_KEY_ENV, client, tool)
        raise HTTPException(status_code=503, detail="connector no configurado")
    # compare_digest sobre bytes: con str lanza TypeError ante no-ASCII (y los
    # headers llegan decodificados latin-1) → sería un 500 en un endpoint público.
    if not presented or not hmac.compare_digest(
        presented.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        logger.warning("[mba] API key inválida desde {} para {}", client, tool)
        raise HTTPException(status_code=401, detail="API key inválida")


async def _read_body_capped(request: Request) -> bytes:
    """Lee el body por chunks y corta al superar el tope: un body chunked (sin
    Content-Length) de GBs no se materializa en memoria antes del 413."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"body supera {MAX_BODY_BYTES} bytes")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"body supera {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _raw_params(request: Request, method: str) -> dict[str, Any]:
    if method == "GET":
        return dict(request.query_params)
    body = await _read_body_capped(request)
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except ValueError:  # JSONDecodeError, UnicodeDecodeError y el límite de dígitos de int() son ValueError
        raise HTTPException(status_code=400, detail="body no es JSON válido") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="el body debe ser un objeto JSON")
    return parsed


@router.api_route("/tools/{tool_name}", methods=["GET", "POST"])
async def run_connector_tool(
    tool_name: str,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    deps: ToolDeps = Depends(get_tool_deps),
) -> Any:
    client = client_ip(request)
    if _bad_key_limiter.remaining(client) < 1.0:
        logger.warning("[mba] IP {} bloqueada por keys inválidas ({})", client, tool_name)
        raise HTTPException(status_code=429, detail="demasiadas llamadas; reintenta en unos segundos")
    try:
        _check_api_key(x_api_key, client=client, tool=tool_name)
    except HTTPException as exc:
        if exc.status_code == 401:
            _bad_key_limiter.allow(client)
        raise
    if not _rate_limiter.allow(f"{client}:{tool_name}"):
        logger.warning("[mba] rate limit desde {} para {}", client, tool_name)
        raise HTTPException(status_code=429, detail="demasiadas llamadas; reintenta en unos segundos")
    tools = declared_tools()
    if tool_name not in tools:
        raise HTTPException(status_code=404, detail=f"tool desconocida: {tool_name}")
    contract = tools[tool_name]
    if request.method != contract.method:
        raise HTTPException(status_code=405, detail=f"{tool_name} se llama con {contract.method}")
    raw = await _raw_params(request, contract.method)
    try:
        call = parse_tool_call(contract, raw)
    except ToolCallError as exc:
        logger.info("[mba] {} inválida desde {}: {}", tool_name, client, exc.errors)
        return JSONResponse(status_code=422, content=exc.payload)
    started = time.monotonic()
    result = await run_tool(call, deps, request)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if result is None:
        logger.info("[mba] {} session={} → 501 (sin lógica todavía)", tool_name, call.session_key)
        return JSONResponse(
            status_code=501,
            content={
                "tool": tool_name,
                "status": "not_implemented",
                "message": "Contrato registrado; esta tool todavía no tiene lógica.",
            },
        )
    logger.info(
        "[mba] {} session={} → {} en {}ms",
        tool_name, call.session_key, result.get("error") or "ok", elapsed_ms,
    )
    return JSONResponse(status_code=200, content=result)
