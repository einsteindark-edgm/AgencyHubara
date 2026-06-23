"""Canal 3 del plugin system — cast HTTP cross-plugin con propagación de identidad.

Un plugin consume el **contrato HTTP publicado** de otro (``depends_on`` +
``consumes`` + un router bajo ``/api/<tu-id>/``) haciendo un self-call loopback a
la MISMA app FastAPI (``http://127.0.0.1:8000`` por default) — NO importando el
módulo ajeno (P-3 lo prohíbe). Los casts canónicos: ``agents_admin→chats``
(``api/evals``) y ``chats→orders`` (``api/order_actions``).

Por qué este helper existe (incidente 2026-06-23): tras colgar ``require_auth``
(JWT/Cognito) en TODOS los routers (``main.py``), el 2º hop del cast volvió a
pasar por la auth — pero los casts hacían ``httpx`` **sin reenviar el header
``Authorization``**, así que el provider respondía ``401 {"detail":"Falta el
bearer token"}`` y el cast re-envolvía ``resp.text`` → ``detail`` doble-anidado.
La causa raíz: el hop interno cruza el trust boundary sin portar la identidad ya
validada en el edge. La cura es centralizar el cast acá, con UNA regla:

    **Todo cast loopback PORTA la identidad del request entrante.**

El gate ``tests/architecture/test_castkit_loopback.py`` prohíbe que un plugin
abra ``httpx`` a loopback ``:8000`` por fuera de este helper — así la regla no
se puede volver a saltar (regla de oro: el símbolo nuevo nace con su check).

Notas:

* El **traceparent** de OTel lo propaga la instrumentación de ``httpx``
  (OpenLIT, ver ``main.py``) automáticamente; acá solo añadimos ``Authorization``.
* **Semántica honesta de fallos (L-1, generalizada)**: un connect-error
  garantiza no-aplicación (→ 502); un timeout deja el resultado DESCONOCIDO
  (→ 504, "PUEDE haberse aplicado"), nunca afirmando que la operación falló.
* El ``timeout`` y el ``cast_label`` los fija cada cast (el timeout se dimensiona
  por el UPSTREAM del provider, no por el hop local — L-1).
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request

__all__ = ["forward"]


def _propagated_headers(request: Request) -> dict[str, str]:
    """Headers a portar al hop del provider — la identidad ya validada en el edge.

    Precedencia igual que ``_extract_token`` del backend: header ``Authorization``
    primero; si no está, el token pudo llegar por query ``?access_token=`` (el
    ``EventSource`` de SSE no manda headers) → se porta como ``Bearer`` al 2º hop.
    Sin sesión (dev local / tests con auth no-op) no se inventa header (el
    provider tampoco lo exige).
    """
    headers: dict[str, str] = {}
    authz = request.headers.get("Authorization")
    if not authz:
        query_token = request.query_params.get("access_token")
        if query_token:
            authz = f"Bearer {query_token}"
    if authz:
        headers["Authorization"] = authz
    return headers


def _unwrap_detail(resp: httpx.Response) -> Any:
    """Desanida el ``detail`` del error del provider (evita el doble-anidado).

    El provider FastAPI responde ``{"detail": <msg>}``; re-envolver ``resp.text``
    crudo produciría ``{"detail": "{\\"detail\\": ...}"}``. Devolvemos el ``detail``
    interno; si el body no es JSON (p.ej. 502 de un proxy intermedio), caemos al
    texto crudo sin romper.
    """
    try:
        parsed = resp.json()
    except (ValueError, httpx.DecodingError):
        return resp.text
    if isinstance(parsed, dict) and "detail" in parsed:
        return parsed["detail"]
    return parsed if isinstance(parsed, str) else resp.text


async def forward(
    request: Request,
    method: str,
    path: str,
    *,
    base_url: str,
    timeout: float,
    cast_label: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reenvía ``method path`` al contrato publicado del provider, portando la
    identidad del ``request`` entrante. Devuelve el dict del provider o levanta un
    ``HTTPException`` con status honesto y ``detail`` desanidado.

    Args:
        request: el request entrante del edge (de donde se porta ``Authorization``).
        method: verbo HTTP (``GET``/``POST``/``PATCH``/``DELETE``).
        path: path absoluto del contrato del provider (``/api/<provider>/...``).
        base_url: base del provider (loopback por default; override por env en el cast).
        timeout: segundos; dimensionado por el upstream del provider (L-1).
        cast_label: etiqueta ``origen→provider`` para los mensajes de error.
        params: query params opcionales.
        body: cuerpo JSON opcional.
    """
    url = f"{base_url.rstrip('/')}{path}"
    headers = _propagated_headers(request)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, url, params=params, json=body, headers=headers
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Nunca conectó → garantizado que la operación NO se aplicó.
        raise HTTPException(
            status_code=502,
            detail=(
                f"cast {cast_label}: provider no disponible "
                f"({exc.__class__.__name__}); la operación NO se aplicó. "
                f"¿El provider está habilitado y sirviendo? (depends_on lo exige al boot)"
            ),
        ) from exc
    except httpx.TimeoutException as exc:
        # Conectó pero no respondió a tiempo → resultado DESCONOCIDO (L-1):
        # la operación pudo completarse server-side. No afirmar fallo.
        raise HTTPException(
            status_code=504,
            detail=(
                f"cast {cast_label}: el provider no respondió a tiempo "
                f"({exc.__class__.__name__}). La operación PUEDE haberse aplicado — "
                f"refrescá el estado antes de reintentar."
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"cast {cast_label}: error de transporte ({exc.__class__.__name__}).",
        ) from exc
    if resp.status_code >= 400:
        # Passthrough del status del provider con el detail DESANIDADO.
        raise HTTPException(status_code=resp.status_code, detail=_unwrap_detail(resp))
    try:
        data = resp.json()
    except (ValueError, httpx.DecodingError) as exc:
        # 200 con body no-JSON (un proxy intermedio, u otro servicio detrás de
        # *_API_BASE) — no crashees con 500; 502 honesto.
        raise HTTPException(
            status_code=502,
            detail=f"cast {cast_label}: respuesta no-JSON del provider ({resp.status_code}).",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502, detail=f"cast {cast_label}: respuesta no-dict del provider"
        )
    return data
