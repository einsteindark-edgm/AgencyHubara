"""Ejecución de una tool: catálogo → GET al backend → CallToolResult.

Contrato con `McpAdsSource` del plugin:
- éxito → `structuredContent` = body exacto del backend (array → `{"items": [...]}`),
  más el mismo JSON en un `TextContent` (compatibilidad, spec MCP §tools);
- 404 del backend → `isError` con «404 not_found …» (el plugin lo trata como dominio);
- cualquier otro problema → `isError` sin «404»/«not found» (el plugin lo trata como infra).
"""

import json
import logging
import re
from typing import Any

from mcp_types import CallToolResult, TextContent

from hubara_gateway.backend import Backend, BackendUnavailable
from hubara_gateway.catalog import TOOLS, ArgumentError, resolve_request
from hubara_gateway.pii import mask_conversations_body
from hubara_gateway.schemas import output_validator

logger = logging.getLogger(__name__)

_MAX_DETAIL = 200
# Lo que `McpAdsSource` lee como «404 de dominio». Un error que NO es 404 no puede contenerlo.
_DOMAIN_404 = re.compile(r"\b404\b|not[_ ]found", re.IGNORECASE)


def _error(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _infra_error(text: str) -> CallToolResult:
    """Error que el plugin debe leer como infraestructura: nunca «404»/«not found»."""
    return _error(_DOMAIN_404.sub("[redacted]", text))


def _detail(body: Any) -> str:
    """El `detail` corto de un error de FastAPI; nada más del body (puede traer datos)."""
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail[:_MAX_DETAIL] if isinstance(detail, str) else ""


async def call_tool(
    name: str,
    args: dict[str, Any],
    backend: Backend,
    *,
    subject: str | None = None,
    pii_allowed: bool = False,
) -> CallToolResult:
    spec = TOOLS.get(name)
    if spec is None:
        return _infra_error(f"unknown tool {name!r}")
    try:
        path, query = resolve_request(name, {k: v for k, v in args.items() if isinstance(v, str)})
    except ArgumentError as exc:
        return _infra_error(f"invalid_arguments: {exc}")

    try:
        response = await backend.get(path, query, on_behalf_of=subject)
    except BackendUnavailable as exc:
        logger.warning("tool=%s backend_unavailable reason=%s", name, exc)
        return _infra_error(f"backend_unavailable: {exc}")

    logger.info("tool=%s status=%s", name, response.status)
    if response.status == 404:
        detail = _detail(response.body) or "not_found"
        return _error(f"404 not_found: {detail}")
    if response.status != 200:
        detail = _detail(response.body)
        return _infra_error(f"backend_error {response.status}" + (f": {detail}" if detail else ""))

    body = response.body
    if spec.wrap_items and isinstance(body, list):
        body = {"items": body}
    if not isinstance(body, dict):
        return _infra_error(f"backend_error 200: unexpected body type {type(body).__name__}")

    # La spec MCP exige que structuredContent conforme al outputSchema, y el cliente TS lo
    # valida: un body fuera de contrato se reporta acá, claro y sin datos, como infra.
    violation = next(iter(output_validator(name).iter_errors(body)), None)
    if violation is not None:
        where = "/".join(str(p) for p in violation.absolute_path) or "<root>"
        logger.error("tool=%s contract_violation at=%s validator=%s", name, where, violation.validator)
        return _infra_error(f"contract_violation: backend body does not match the {name} contract at {where}")

    meta = None
    if spec.has_pii:
        if not pii_allowed:
            body = mask_conversations_body(body)
        meta = {"hubara/pii_masked": not pii_allowed}

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
        structured_content=body,
        _meta=meta,
    )
